"""因子挖掘看板 API。"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..factor_mining_store import save_backtest_series_rows_async
from ..models import FactorMiningBacktestSeries, FactorMiningCandidate, FactorMiningRun
from ..factor_mining_backtest import (
    build_run_context,
    candidate_from_model,
    compute_payload,
)

router = APIRouter(prefix="/api/v1/factor-mining", tags=["factor-mining"])


class RefreshBacktestRequest(BaseModel):
    row_key: str


def _infer_universe_from_text(text: str | None) -> str | None:
    if not text:
        return None
    normalized = text.strip().lower()
    if not normalized:
        return None
    if "hs300" in normalized or "沪深300" in normalized:
        return "hs300"
    if "csi500" in normalized or "zz500" in normalized or "中证500" in normalized:
        return "csi500"
    if "csi1000" in normalized or "中证1000" in normalized:
        return "csi1000"
    if "csi2000" in normalized or "中证2000" in normalized:
        return "csi2000"
    if "small_scale" in normalized or "快速测试" in normalized:
        return "small_scale"
    return None


def _infer_benchmark_from_text(text: str | None, universe: str | None) -> str | None:
    if text:
        normalized = text.strip().lower()
        if "hs300" in normalized or "沪深300" in normalized:
            return "hs300"
        if "zz500" in normalized or "csi500" in normalized or "中证500" in normalized:
            return "zz500"
        if "csi1000" in normalized or "中证1000" in normalized:
            return "csi1000"
        if "sz50" in normalized or "上证50" in normalized:
            return "sz50"
    if universe == "csi500":
        return "zz500"
    if universe in ("hs300", "csi1000", "sz50"):
        return universe
    return None


def _run_universe(run: FactorMiningRun) -> str | None:
    if isinstance(run.params, dict):
        value = run.params.get("universe")
        if isinstance(value, str) and value.strip():
            return value.strip()
        legacy_summary = run.params.get("legacy_summary")
        if isinstance(legacy_summary, dict):
            for key in ("objective", "conclusion", "latest_window", "history_window"):
                inferred = _infer_universe_from_text(legacy_summary.get(key))
                if inferred is not None:
                    return inferred
    return (
        _infer_universe_from_text(run.source_tag)
        or _infer_universe_from_text(run.source_summary)
    )


def _run_benchmark(run: FactorMiningRun) -> str | None:
    universe = _run_universe(run)
    if isinstance(run.params, dict):
        value = run.params.get("benchmark")
        if isinstance(value, str) and value.strip():
            return value.strip()
        legacy_summary = run.params.get("legacy_summary")
        if isinstance(legacy_summary, dict):
            for key in ("objective", "conclusion"):
                inferred = _infer_benchmark_from_text(legacy_summary.get(key), universe)
                if inferred is not None:
                    return inferred
    return _infer_benchmark_from_text(run.source_tag, universe) or _infer_benchmark_from_text(run.source_summary, universe)


def _coerce_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_metric_payload(candidate: FactorMiningCandidate) -> dict[str, float | None]:
    overall_grade = candidate.grade or _grade_from_score(candidate.score)
    latest_grade = _grade_from_score(candidate.latest_score)
    history_grade = _grade_from_score(candidate.history_score)
    return {
        "score": _coerce_float(candidate.score),
        "grade": overall_grade,
        "latest_score": _coerce_float(candidate.latest_score),
        "history_score": _coerce_float(candidate.history_score),
        "latest": {
            "score": _coerce_float(candidate.latest_score),
            "grade": latest_grade,
            "ic_mean": _coerce_float(candidate.latest_ic_mean),
            "ic_ir": _coerce_float(candidate.latest_ic_ir),
            "ic_win_rate": _coerce_float(candidate.latest_ic_win_rate),
            "monotonicity": _coerce_float(candidate.latest_monotonicity),
            "sharpe": _coerce_float(candidate.latest_sharpe),
            "strategy_sharpe": _coerce_float(candidate.latest_sharpe),
            "top_group_sharpe": _coerce_float(candidate.latest_top_group_sharpe),
            "long_short_sharpe": _coerce_float(candidate.latest_long_short_sharpe),
            "turnover": _coerce_float(candidate.latest_turnover),
            "cagr": _coerce_float(candidate.latest_cagr),
            "max_drawdown": _coerce_float(candidate.latest_max_drawdown),
            "strategy_max_drawdown": _coerce_float(candidate.latest_strategy_max_drawdown),
            "total_return": _coerce_float(candidate.latest_total_return),
            "benchmark_total_return": _coerce_float(candidate.latest_benchmark_total_return),
            "excess_total_return": _coerce_float(candidate.latest_excess_total_return),
            "flipped": candidate.latest_flipped,
        },
        "history": {
            "score": _coerce_float(candidate.history_score),
            "grade": history_grade,
            "ic_mean": _coerce_float(candidate.history_ic_mean),
            "ic_ir": _coerce_float(candidate.history_ic_ir),
            "ic_win_rate": _coerce_float(candidate.history_ic_win_rate),
            "monotonicity": _coerce_float(candidate.history_monotonicity),
            "sharpe": _coerce_float(candidate.history_sharpe),
            "strategy_sharpe": _coerce_float(candidate.history_sharpe),
            "top_group_sharpe": _coerce_float(candidate.history_top_group_sharpe),
            "long_short_sharpe": _coerce_float(candidate.history_long_short_sharpe),
            "turnover": _coerce_float(candidate.history_turnover),
            "cagr": _coerce_float(candidate.history_cagr),
            "max_drawdown": _coerce_float(candidate.history_max_drawdown),
            "strategy_max_drawdown": _coerce_float(candidate.history_strategy_max_drawdown),
            "total_return": _coerce_float(candidate.history_total_return),
            "benchmark_total_return": _coerce_float(candidate.history_benchmark_total_return),
            "excess_total_return": _coerce_float(candidate.history_excess_total_return),
            "flipped": candidate.history_flipped,
        },
        "stability_score": _coerce_float(candidate.stability_score),
        "market_fit": candidate.market_fit,
        "failure_modes": candidate.failure_modes,
}


def _grade_from_score(score: float | None) -> str:
    numeric_score = _coerce_float(score)
    if numeric_score is None:
        return "D"
    if numeric_score >= 80:
        return "A"
    if numeric_score >= 60:
        return "B"
    if numeric_score >= 40:
        return "C"
    return "D"


def _coerce_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _serialize_candidate(candidate: FactorMiningCandidate) -> dict[str, Any]:
    metrics = _build_metric_payload(candidate)
    return {
        "id": str(candidate.id),
        "row_key": candidate.row_key,
        "source_id": candidate.source_id,
        "source_label": candidate.source_label,
        "row_index": candidate.row_index,
        "name": candidate.name,
        "expression": candidate.expression,
        "holding_period": candidate.holding_period,
        "n_groups": candidate.n_groups,
        "cost_rate": candidate.cost_rate,
        "neutralize_industry": candidate.neutralize_industry,
        "neutralize_cap": candidate.neutralize_cap,
        "status": candidate.status,
        "score": metrics["score"],
        "grade": metrics["grade"],
        "latest_score": metrics["latest_score"],
        "history_score": metrics["history_score"],
        "latest": metrics["latest"],
        "history": metrics["history"],
        "stability_score": metrics["stability_score"],
        "market_fit": metrics["market_fit"],
        "failure_modes": metrics["failure_modes"],
    }


def _parse_run_id(run_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(run_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=f"无效的 run_id：{run_id}") from error


@router.get("/runs")
async def list_runs(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    result = await db.execute(
        select(
            FactorMiningRun,
            func.count(FactorMiningCandidate.id).label("candidate_count"),
        )
        .outerjoin(FactorMiningCandidate, FactorMiningCandidate.run_id == FactorMiningRun.id)
        .group_by(FactorMiningRun.id)
        .order_by(desc(FactorMiningRun.generated_at))
    )
    runs = []
    for run, candidate_count in result.all():
        runs.append({
            "id": str(run.id),
            "source_tag": run.source_tag,
            "status": run.status,
            "universe": _run_universe(run),
            "benchmark": _run_benchmark(run),
            "candidate_count": int(candidate_count or 0),
            "error_message": run.error_message,
            "generated_at": _coerce_datetime(run.generated_at),
            "source_summary": run.source_summary,
            "created_at": _coerce_datetime(run.created_at),
            "updated_at": _coerce_datetime(run.updated_at),
            "params": run.params,
        })
    return {"runs": runs}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    run_uuid = _parse_run_id(run_id)
    run_result = await db.execute(select(FactorMiningRun).where(FactorMiningRun.id == run_uuid))
    run = run_result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="运行不存在")

    candidates_result = await db.execute(
        select(FactorMiningCandidate)
        .where(FactorMiningCandidate.run_id == run_uuid)
        .order_by(FactorMiningCandidate.source_id, FactorMiningCandidate.source_label, FactorMiningCandidate.score.desc())
    )
    candidates = [
        _serialize_candidate(item)
        for item in candidates_result.scalars().all()
    ]

    return {
        "run": {
            "id": str(run.id),
            "source_tag": run.source_tag,
            "status": run.status,
            "universe": _run_universe(run),
            "benchmark": _run_benchmark(run),
            "error_message": run.error_message,
            "generated_at": _coerce_datetime(run.generated_at),
            "source_summary": run.source_summary,
            "created_at": _coerce_datetime(run.created_at),
            "updated_at": _coerce_datetime(run.updated_at),
            "params": run.params,
        },
        "candidates": candidates,
    }


@router.get("/runs/{run_id}/backtest-series")
async def get_backtest_series(run_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    run_uuid = _parse_run_id(run_id)
    run_result = await db.execute(select(FactorMiningRun).where(FactorMiningRun.id == run_uuid))
    if run_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="运行不存在")

    result = await db.execute(
        select(FactorMiningBacktestSeries)
        .where(FactorMiningBacktestSeries.run_id == run_uuid)
        .order_by(FactorMiningBacktestSeries.row_key)
    )
    rows = list(result.scalars().all())
    items: dict[str, Any] = {}
    for item in rows:
        items[item.row_key] = {
            "row_key": item.row_key,
            "source_id": item.source_id,
            "source_label": item.source_label,
            "row_index": item.row_index,
            "name": item.name,
            "expression": item.expression,
            "holding_period": item.holding_period,
            "status": item.status,
            "error_message": item.error_message,
            "metrics": item.metrics,
            "daily": item.daily,
            "monthly": item.monthly,
            "yearly": item.yearly,
        }

    errors = []
    for item in rows:
        if item.status != "success":
            errors.append({
                "row_key": item.row_key,
                "name": item.name,
                "message": item.error_message or "回测未成功",
            })

    return {
        "run_id": str(run_uuid),
        "items": items,
        "errors": errors,
        "params": {
            "generated_at": None,
            "candidate_count": len(items),
            "success_count": len([item for item in items.values() if item.get("status") == "success"]),
            "error_count": len(errors),
        },
    }


@router.post("/runs/{run_id}/backtest-series/refresh")
async def refresh_backtest_series(
    run_id: str,
    request: RefreshBacktestRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    run_uuid = _parse_run_id(run_id)
    run = await db.get(FactorMiningRun, run_uuid)
    if run is None:
        raise HTTPException(status_code=404, detail="运行不存在")

    candidate_result = await db.execute(
        select(FactorMiningCandidate)
        .where(FactorMiningCandidate.run_id == run_uuid)
        .where(FactorMiningCandidate.row_key == request.row_key)
    )
    candidate = candidate_result.scalar_one_or_none()
    if candidate is None:
        raise HTTPException(status_code=404, detail=f"候选不存在：{request.row_key}")

    run_context = build_run_context(run)
    normalized_candidate = candidate_from_model(candidate)
    payload = await asyncio.to_thread(compute_payload, [normalized_candidate], run_context)
    item = payload["items"].get(request.row_key)
    if item is None:
        raise HTTPException(status_code=500, detail=f"未生成回测结果：{request.row_key}")

    result = await save_backtest_series_rows_async(
        run_id=str(run.id),
        items={request.row_key: item},
        errors=payload.get("errors"),
    )
    return {
        "run_id": str(run.id),
        "row_key": request.row_key,
        "item": item,
        "errors": payload.get("errors") or [],
        "result": result,
    }
