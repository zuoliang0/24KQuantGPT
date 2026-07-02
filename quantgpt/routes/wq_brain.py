"""WQ BRAIN API routes — submit expressions to WorldQuant BRAIN for real simulation."""

import logging
import threading
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from sqlalchemy import desc, func, select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..db import get_db
from ..models import SubmittedAlpha, Task as TaskModel, User
from ..task_store import (
    active_task_count,
    check_rate_limit,
    persist_task_to_db,
    tasks,
    tasks_lock,
    MAX_ACTIVE_TASKS,
)
from ..wq_brain_client import SUBMIT_THRESHOLDS, WQBrainClient, configured_accounts, get_client, is_configured
from ..wq_brain_service import fitness_to_grade, run_list_alphas, run_single_simulation, safe_float

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/wq-brain", tags=["wq_brain"])


_safe_float = safe_float
_fitness_to_grade = fitness_to_grade

WQ_CANDIDATE_TASK_TYPES = {"wq_brain_submit", "wq_brain_batch"}
WQ_SUBMIT_TASK_TYPES = {"wq_brain_submit_by_ids", "wq_brain_batch_submit_by_id", "wq_brain_finalize"}
WQ_DEFAULT_TAG = "wq_round_YYYYMMDD_topic"


class WQBrainSubmitRequest(BaseModel):
    expression: str = Field(..., description="FASTEXPR factor expression")
    tag: str = Field(..., min_length=1, max_length=100, description="Submitter tag (e.g. 'agent-lowcorr-0506')")
    region: str = Field("USA", description="Market region")
    universe: str = Field("TOP3000", description="WQ Universe")
    delay: int = Field(1, ge=0, le=1, description="Signal delay")
    decay: int = Field(0, ge=0, le=20, description="Alpha decay")
    neutralization: str = Field("SUBINDUSTRY", description="Neutralization method")
    truncation: float = Field(0.08, ge=0, le=0.5, description="Weight truncation")
    auto_submit: bool = Field(False, description="Auto-submit if checks pass")
    account: str = Field("primary", description="WQ account: 'primary' or 'alt'")
    session_id: str | None = Field(None, description="Session ID")


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _status_label(status: str | None) -> str:
    labels = {
        "completed": "已模拟",
        "failed": "失败",
        "pending": "等待中",
        "running": "运行中",
        "authenticating": "认证中",
        "simulating": "模拟中",
        "cancelled": "已取消",
        "iteration_completed": "已完成",
    }
    return labels.get(status or "", status or "未知")


def _candidate_decision(fitness: float | None, sharpe: float | None, turnover: float | None, status: str | None) -> str:
    if status == "failed":
        return "记录失败原因"
    if fitness is not None and fitness >= 1.0:
        if turnover is not None and (turnover < 0.01 or turnover > 0.7):
            return "先复核换手"
        return "待确认提交"
    if fitness is not None and fitness >= 0.8 and sharpe is not None and sharpe >= 1.25:
        return "接近门槛"
    return "继续观察"


def _extract_single_task_metrics(task: TaskModel) -> dict:
    result = task.result if isinstance(task.result, dict) else {}
    is_metrics = result.get("is_metrics") if isinstance(result.get("is_metrics"), dict) else {}
    wq_brain = result.get("wq_brain") if isinstance(result.get("wq_brain"), dict) else {}
    backtest_summary = result.get("backtest_summary") if isinstance(result.get("backtest_summary"), dict) else {}
    params = task.params if isinstance(task.params, dict) else {}
    settings = result.get("settings") if isinstance(result.get("settings"), dict) else {}
    fitness = _safe_float(is_metrics.get("fitness") or wq_brain.get("wq_fitness") or backtest_summary.get("wq_fitness"))
    sharpe = _safe_float(is_metrics.get("sharpe") or wq_brain.get("wq_sharpe") or backtest_summary.get("long_short_sharpe"))
    returns = _safe_float(is_metrics.get("returns") or wq_brain.get("wq_returns"))
    turnover = _safe_float(is_metrics.get("turnover") or wq_brain.get("wq_turnover") or backtest_summary.get("turnover"))
    alpha_id = result.get("alpha_id")
    expression = task.expression or result.get("expression") or params.get("expression") or ""
    return {
        "task_id": task.id,
        "task_type": task.task_type,
        "status": task.status,
        "status_label": _status_label(task.status),
        "decision": _candidate_decision(fitness, sharpe, turnover, task.status),
        "expression": expression,
        "alpha_id": alpha_id,
        "tag": params.get("tag"),
        "region": settings.get("region") or params.get("region") or "USA",
        "universe": settings.get("universe") or params.get("universe") or "TOP3000",
        "delay": settings.get("delay") if settings.get("delay") is not None else params.get("delay"),
        "decay": settings.get("decay") if settings.get("decay") is not None else params.get("decay"),
        "neutralization": settings.get("neutralization") or params.get("neutralization") or "SUBINDUSTRY",
        "truncation": settings.get("truncation") if settings.get("truncation") is not None else params.get("truncation"),
        "fitness": fitness,
        "sharpe": sharpe,
        "returns": returns,
        "turnover": turnover,
        "submitted": result.get("submitted") is True,
        "error": task.error or result.get("error"),
        "created_at": _isoformat(task.created_at),
        "updated_at": _isoformat(task.updated_at),
    }


def _extract_batch_task_candidates(task: TaskModel) -> list[dict]:
    result = task.result if isinstance(task.result, dict) else {}
    params = task.params if isinstance(task.params, dict) else {}
    expression = task.expression or result.get("expression") or params.get("expression") or ""
    sub_results = result.get("sub_results") if isinstance(result.get("sub_results"), dict) else {}
    if not sub_results:
        return [_extract_single_task_metrics(task)]

    candidates = []
    for key, item in sub_results.items():
        if not isinstance(item, dict):
            continue
        fitness = _safe_float(item.get("fitness"))
        sharpe = _safe_float(item.get("sharpe"))
        turnover = _safe_float(item.get("turnover"))
        status = "completed" if item.get("status") == "completed" else "failed"
        candidates.append({
            "task_id": task.id,
            "task_type": task.task_type,
            "combo_key": key,
            "status": status,
            "status_label": _status_label(status),
            "decision": _candidate_decision(fitness, sharpe, turnover, status),
            "expression": expression,
            "alpha_id": item.get("alpha_id"),
            "tag": params.get("tag"),
            "region": item.get("region") or params.get("region") or "USA",
            "universe": item.get("universe") or params.get("universe") or "TOP3000",
            "delay": item.get("delay") if item.get("delay") is not None else params.get("delay"),
            "decay": params.get("decay"),
            "neutralization": item.get("neutralization") or params.get("neutralization") or "SUBINDUSTRY",
            "truncation": params.get("truncation"),
            "fitness": fitness,
            "sharpe": sharpe,
            "returns": _safe_float(item.get("returns")),
            "turnover": turnover,
            "submitted": item.get("submitted") is True,
            "error": item.get("error"),
            "created_at": _isoformat(task.created_at),
            "updated_at": _isoformat(task.updated_at),
        })
    return candidates


def _submitted_alpha_row(alpha: SubmittedAlpha) -> dict:
    return {
        "alpha_id": alpha.alpha_id,
        "expression": alpha.expression,
        "tag": alpha.tag,
        "region": alpha.region,
        "universe": alpha.universe,
        "delay": alpha.delay,
        "decay": alpha.decay,
        "neutralization": alpha.neutralization,
        "truncation": alpha.truncation,
        "fitness": alpha.fitness,
        "sharpe": alpha.sharpe,
        "returns": alpha.returns,
        "turnover": alpha.turnover,
        "status": alpha.status,
        "status_label": _status_label(alpha.status),
        "submitted_at": _isoformat(alpha.submitted_at),
    }


def _candidate_sort_key(candidate: dict) -> tuple[int, float, float, float, str]:
    decision_rank = 0 if candidate.get("decision") == "待确认提交" else 1
    return (
        decision_rank,
        -float(candidate.get("fitness") or -999.0),
        -float(candidate.get("sharpe") or -999.0),
        float(candidate.get("turnover") or 999.0),
        str(candidate.get("created_at") or ""),
    )


def _run_wq_brain_task(task_id: str, req: WQBrainSubmitRequest, user_id: str):
    task = tasks.get(task_id)
    if not task:
        return

    try:
        account = req.account if req.account in ("primary", "alt") else "primary"
        client = get_client(account)

        task["status"] = "authenticating"
        if not client.authenticate():
            task["status"] = "failed"
            task["error"] = f"WQ BRAIN 认证失败 (account={account})，请检查凭证配置"
            return

        task["status"] = "simulating"

        def on_progress(pct: int, message: str):
            task["progress"] = pct
            task["progress_message"] = message

        result = run_single_simulation(
            client, expression=req.expression,
            region=req.region, universe=req.universe,
            delay=req.delay, decay=req.decay,
            neutralization=req.neutralization, truncation=req.truncation,
            auto_submit=req.auto_submit and account == "primary",
            user_id=user_id, tag=req.tag,
            progress_callback=on_progress,
        )
        client.close()

        if not result.get("ok"):
            task["status"] = "failed"
            task["error"] = result.get("error", "WQ BRAIN simulation failed")
            return

        task["status"] = "completed"
        task["expression"] = req.expression
        task["result"] = result
        logger.info(f"[{task_id}] WQ BRAIN completed: alpha_id={result.get('alpha_id')} rating={result.get('interpretation', {}).get('rating')} submitted={result.get('submitted')}")

    except Exception as e:
        logger.error(f"[{task_id}] WQ BRAIN task error: {e}")
        task["status"] = "failed"
        task["error"] = f"WQ BRAIN 提交异常: {e}"
    finally:
        if "completed_at" not in task:
            task["completed_at"] = time.time()
        try:
            persist_task_to_db(task_id, user_id, task)
        except Exception as e:
            logger.error(f"[{task_id}] DB persist error: {e}")


@router.get("/status", summary="WQ BRAIN 配置状态")
async def wq_brain_status():
    accounts = configured_accounts()
    return {
        "configured": len(accounts) > 0,
        "accounts": accounts,
        "thresholds": SUBMIT_THRESHOLDS,
    }


@router.get("/research-board", summary="WQ 候选与提交状态")
async def wq_research_board(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    limit: int = 200,
):
    """返回 WQ 优先挖掘看板数据。"""
    bounded_limit = min(max(limit, 1), 500)
    task_result = await session.execute(
        sa_select(TaskModel)
        .where(TaskModel.user_id == user.id, TaskModel.task_type.in_(WQ_CANDIDATE_TASK_TYPES | WQ_SUBMIT_TASK_TYPES))
        .order_by(desc(TaskModel.created_at))
        .limit(bounded_limit)
    )
    task_rows = task_result.scalars().all()

    candidates: list[dict] = []
    submit_tasks: list[dict] = []
    for task in task_rows:
        if task.task_type in WQ_SUBMIT_TASK_TYPES:
            submit_tasks.append({
                "task_id": task.id,
                "task_type": task.task_type,
                "status": task.status,
                "status_label": _status_label(task.status),
                "params": task.params if isinstance(task.params, dict) else {},
                "result": task.result if isinstance(task.result, dict) else {},
                "error": task.error,
                "created_at": _isoformat(task.created_at),
                "updated_at": _isoformat(task.updated_at),
            })
            continue
        if task.task_type == "wq_brain_batch":
            candidates.extend(_extract_batch_task_candidates(task))
        else:
            candidates.append(_extract_single_task_metrics(task))

    submitted_result = await session.execute(
        sa_select(SubmittedAlpha)
        .where(SubmittedAlpha.user_id == user.id)
        .order_by(desc(SubmittedAlpha.submitted_at))
        .limit(bounded_limit)
    )
    submitted_alphas = [_submitted_alpha_row(row) for row in submitted_result.scalars().all()]
    submitted_ids = {row["alpha_id"] for row in submitted_alphas if row.get("alpha_id")}

    for candidate in candidates:
        alpha_id = candidate.get("alpha_id")
        if alpha_id and alpha_id in submitted_ids:
            candidate["decision"] = "已提交跟踪"

    candidates.sort(key=_candidate_sort_key)
    configured = configured_accounts()
    summary = {
        "candidate_count": len(candidates),
        "ready_to_submit": sum(1 for item in candidates if item.get("decision") == "待确认提交"),
        "near_ready": sum(1 for item in candidates if item.get("decision") == "接近门槛"),
        "submitted_count": len(submitted_alphas),
        "active_count": sum(1 for item in submitted_alphas if str(item.get("status") or "").lower() == "active"),
        "failed_count": sum(1 for item in candidates if item.get("status") == "failed"),
    }

    return {
        "configured": len(configured) > 0,
        "accounts": configured,
        "default_policy": {
            "region": "USA",
            "universe": "TOP3000",
            "delay": 1,
            "neutralization": "SUBINDUSTRY",
            "auto_submit": False,
            "tag_format": WQ_DEFAULT_TAG,
        },
        "thresholds": SUBMIT_THRESHOLDS,
        "summary": summary,
        "candidates": candidates,
        "submitted_alphas": submitted_alphas,
        "submit_tasks": submit_tasks,
    }


@router.get("/user-info")
async def wq_brain_user_info(account: str = "primary"):
    if not is_configured(account):
        raise HTTPException(status_code=503, detail=f"WQ BRAIN 未配置 (account={account})")
    client = get_client(account)
    if not client.authenticate():
        raise HTTPException(status_code=502, detail="WQ BRAIN 认证失败")
    info = client.get_user_info()
    client.close()
    return info


@router.get("/platform-alphas")
async def list_platform_alphas(
    account: str = "primary",
    limit: int = 100,
    offset: int = 0,
    user: User = Depends(get_current_user),
):
    """List all alphas from WQ BRAIN platform (including simulated but not submitted)."""
    if not is_configured(account):
        raise HTTPException(status_code=503, detail=f"WQ BRAIN 未配置 (account={account})")
    client = get_client(account)
    if not client.authenticate():
        raise HTTPException(status_code=502, detail="WQ BRAIN 认证失败")
    result = run_list_alphas(client, limit=limit, offset=offset)
    client.close()
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "unknown"))
    return {"total": result["total"], "alphas": result["alphas"]}


@router.post("/submit", status_code=202, summary="提交因子到 WQ BRAIN 模拟")
async def wq_brain_submit(
    req: WQBrainSubmitRequest,
    request: Request,
    user: User = Depends(get_current_user),
):
    """提交因子表达式到 WorldQuant BRAIN 平台进行模拟。异步执行，返回 task_id。模拟通常需要 2-5 分钟，用 GET /api/v1/tasks/{task_id} 轮询结果。结果包含 Sharpe、Fitness、Turnover 等 IS 指标。"""
    if not is_configured(req.account):
        raise HTTPException(status_code=503, detail=f"WQ BRAIN 未配置 (account={req.account}) — 请设置对应的环境变量")

    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")

    if active_task_count() >= MAX_ACTIVE_TASKS:
        raise HTTPException(status_code=503, detail="当前任务已满，请稍后再试")

    task_id = uuid.uuid4().hex[:12]
    user_id = str(user.id)

    with tasks_lock:
        tasks[task_id] = {
            "task_id": task_id,
            "user_id": user_id,
            "session_id": req.session_id,
            "status": "pending",
            "task_type": "wq_brain_submit",
            "cancelled": False,
            "params": req.model_dump(exclude={"session_id"}),
            "created_at": time.time(),
        }

    thread = threading.Thread(
        target=_run_wq_brain_task, args=(task_id, req, user_id), daemon=True,
    )
    thread.start()

    return {"task_id": task_id, "status": "pending"}


@router.get("/submitted-alphas", summary="查询已提交因子列表")
async def list_submitted_alphas(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
):
    from sqlalchemy import func, select as sa_select

    from ..models import SubmittedAlpha

    count_q = await session.execute(
        sa_select(func.count()).where(SubmittedAlpha.user_id == user.id)
    )
    total = count_q.scalar() or 0

    q = await session.execute(
        sa_select(SubmittedAlpha)
        .where(SubmittedAlpha.user_id == user.id)
        .order_by(SubmittedAlpha.submitted_at.desc())
        .offset(offset)
        .limit(min(limit, 100))
    )
    alphas = q.scalars().all()

    return {
        "total": total,
        "alphas": [
            {
                "alpha_id": a.alpha_id,
                "expression": a.expression,
                "tag": a.tag,
                "region": a.region,
                "universe": a.universe,
                "delay": a.delay,
                "neutralization": a.neutralization,
                "sharpe": a.sharpe,
                "fitness": a.fitness,
                "returns": a.returns,
                "turnover": a.turnover,
                "status": a.status,
                "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
            }
            for a in alphas
        ],
    }


@router.post("/{task_id}/submit-alpha")
async def submit_alpha_from_task(
    task_id: str,
    user: User = Depends(get_current_user),
):
    if not is_configured():
        raise HTTPException(status_code=503, detail="WQ BRAIN 未配置 — 无可用账号")

    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    user_id = str(user.id)
    if task.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="无权操作此任务")

    result = task.get("result", {})
    alpha_id = result.get("alpha_id")
    if not alpha_id:
        raise HTTPException(status_code=400, detail="任务无关联的 alpha_id")

    account = task.get("params", {}).get("account", "primary")
    if account != "primary":
        raise HTTPException(status_code=403, detail="Alpha 提交仅允许 primary 账号，禁止从 alt 账号提交")
    client = get_client(account)
    if not client.authenticate():
        raise HTTPException(status_code=502, detail=f"WQ BRAIN 认证失败 (account={account})")

    submit_result = client.submit_alpha(alpha_id)
    client.close()
    logger.info(f"[{task_id}] submit_alpha({alpha_id}) result: {submit_result}")

    if submit_result.get("ok"):
        task["result"]["submitted"] = True
        try:
            from ..alpha_tracker import record_submitted_alpha_sync
            params = task.get("params", {})
            is_metrics = result.get("is_metrics", {})
            record_submitted_alpha_sync(
                user_id=user_id, alpha_id=alpha_id, expression=result.get("expression", ""),
                region=params.get("region", "USA"), universe=params.get("universe", "TOP3000"),
                delay=params.get("delay", 1), decay=params.get("decay", 0),
                neutralization=params.get("neutralization", "SUBINDUSTRY"),
                truncation=params.get("truncation", 0.08),
                sharpe=_safe_float(is_metrics.get("sharpe")),
                fitness=_safe_float(is_metrics.get("fitness")),
                returns=_safe_float(is_metrics.get("returns")),
                turnover=_safe_float(is_metrics.get("turnover")),
                tag=params.get("tag"),
            )
        except Exception as e:
            logger.warning(f"Alpha tracking failed for manual submit: {e}")

    return {
        "alpha_id": alpha_id,
        "submitted": submit_result.get("ok", False),
        "detail": submit_result.get("detail", ""),
    }


@router.get("/alpha-status/{alpha_id}")
async def check_alpha_platform_status(
    alpha_id: str,
    account: str = "primary",
    user: User = Depends(get_current_user),
):
    """Check actual platform-side alpha status (whether it's really submitted)."""
    if not is_configured(account):
        raise HTTPException(status_code=503, detail=f"WQ BRAIN 未配置 (account={account})")
    client = get_client(account)
    if not client.authenticate():
        raise HTTPException(status_code=502, detail=f"WQ BRAIN 认证失败 (account={account})")
    result = client.check_alpha_status(alpha_id)
    client.close()
    return result


@router.post("/submit-by-id/{alpha_id}")
async def submit_alpha_by_id(
    alpha_id: str,
    account: str = "primary",
    user: User = Depends(get_current_user),
):
    """Submit alpha directly by alpha_id. Polls until platform confirms or SC fails."""
    if account != "primary":
        raise HTTPException(status_code=403, detail="Alpha 提交仅允许 primary 账号")
    if not is_configured(account):
        raise HTTPException(status_code=503, detail="WQ BRAIN 未配置")
    client = get_client(account)
    if not client.authenticate():
        raise HTTPException(status_code=502, detail=f"WQ BRAIN 认证失败 (account={account})")
    result = client.submit_alpha(alpha_id)
    client.close()
    logger.info(f"submit-by-id {alpha_id}: {result}")
    return result


@router.delete("/alpha/{alpha_id}")
async def delete_alpha(
    alpha_id: str,
    account: str = "primary",
    user: User = Depends(get_current_user),
):
    """Delete/retire an alpha from the WQ BRAIN platform."""
    if not is_configured(account):
        raise HTTPException(status_code=503, detail="WQ BRAIN 未配置")
    client = get_client(account)
    if not client.authenticate():
        raise HTTPException(status_code=502, detail="WQ BRAIN 认证失败")
    result = client.delete_alpha(alpha_id)
    client.close()
    logger.info(f"delete-alpha {alpha_id}: {result}")
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("detail", "删除失败"))
    return result


@router.post("/alpha/{alpha_id}/unhide")
async def unhide_alpha(
    alpha_id: str,
    account: str = "primary",
    user: User = Depends(get_current_user),
):
    """Restore a hidden alpha on WQ BRAIN platform."""
    if not is_configured(account):
        raise HTTPException(status_code=503, detail="WQ BRAIN 未配置")
    client = get_client(account)
    if not client.authenticate():
        raise HTTPException(status_code=502, detail="WQ BRAIN 认证失败")
    result = client.unhide_alpha(alpha_id)
    client.close()
    logger.info(f"unhide-alpha {alpha_id}: {result}")
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("detail", "恢复失败"))
    return result
