"""因子挖掘看板数据持久化助手。"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Sequence, TypeVar

from sqlalchemy import delete, select
from .db import _get_session_factory, close_db, reset_db_state
from .models import FactorMiningBacktestSeries, FactorMiningCandidate, FactorMiningRun

T = TypeVar("T")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"run_id 无效：{value}") from error


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1", "yes", "y", "on"):
            return True
        if text in ("false", "0", "no", "off"):
            return False
    return None


def _grade_from_score(score: float | None) -> str:
    if score is None:
        return "D"
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    return "D"


def _safe_json(value: Any) -> Any:
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        return value
    return json.dumps(value, ensure_ascii=False)


def _window_metric(payload: Mapping[str, Any] | None, key: str) -> float | None:
    if not isinstance(payload, Mapping):
        return None
    return _to_float(payload.get(key))


async def _run_with_fresh_db(operation: Callable[[], Awaitable[T]]) -> T:
    reset_db_state()
    try:
        return await operation()
    finally:
        await close_db()


def _run_sync_db(operation: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(_run_with_fresh_db(operation))


def _extract_candidate_row(candidate: Mapping[str, Any], row_key: str, row_index: int, source_id: str, source_label: str) -> FactorMiningCandidate:
    latest = candidate.get("latest")
    if not isinstance(latest, Mapping):
        latest = None
    history = candidate.get("history")
    if not isinstance(history, Mapping):
        history = None

    stability_score = _to_float(candidate.get("stability_score"))
    latest_score = _window_metric(latest, "score")
    history_score = _window_metric(history, "score")

    if stability_score is None:
        stability_score = latest_score
    if stability_score is None:
        stability_score = history_score

    grade = candidate.get("grade")
    if not grade:
        grade = _grade_from_score(stability_score)

    return FactorMiningCandidate(
        row_key=row_key,
        source_id=source_id,
        source_label=source_label,
        row_index=row_index,
        name=str(candidate.get("name", "")),
        expression=str(candidate.get("expression", "")),
        holding_period=_to_int(candidate.get("holding_period")) or 1,
        n_groups=_to_int(candidate.get("n_groups")) or 5,
        cost_rate=_to_float(candidate.get("cost_rate")) or 0.003,
        neutralize_industry=_to_bool(candidate.get("neutralize_industry")) if candidate.get("neutralize_industry") is not None else True,
        neutralize_cap=_to_bool(candidate.get("neutralize_cap")) if candidate.get("neutralize_cap") is not None else True,
        status=str(candidate.get("status") or "success"),
        score=stability_score,
        grade=str(grade),
        latest_score=latest_score,
        history_score=history_score,
        latest_ic_mean=_window_metric(latest, "ic_mean"),
        latest_ic_ir=_window_metric(latest, "ic_ir"),
        latest_ic_win_rate=_window_metric(latest, "ic_win_rate"),
        latest_monotonicity=_window_metric(latest, "monotonicity"),
        latest_sharpe=_window_metric(latest, "sharpe") or _window_metric(latest, "strategy_sharpe"),
        latest_top_group_sharpe=_window_metric(latest, "top_group_sharpe"),
        latest_long_short_sharpe=_window_metric(latest, "long_short_sharpe"),
        latest_turnover=_window_metric(latest, "turnover"),
        latest_cagr=_window_metric(latest, "cagr"),
        latest_max_drawdown=_window_metric(latest, "max_drawdown"),
        latest_strategy_max_drawdown=_window_metric(latest, "strategy_max_drawdown"),
        latest_total_return=_window_metric(latest, "total_return"),
        latest_benchmark_total_return=_window_metric(latest, "benchmark_total_return"),
        latest_excess_total_return=_window_metric(latest, "excess_total_return"),
        latest_flipped=_to_bool(latest.get("flipped")) if isinstance(latest, Mapping) else None,
        history_score_raw=history_score,
        history_ic_mean=_window_metric(history, "ic_mean"),
        history_ic_ir=_window_metric(history, "ic_ir"),
        history_ic_win_rate=_window_metric(history, "ic_win_rate"),
        history_monotonicity=_window_metric(history, "monotonicity"),
        history_sharpe=_window_metric(history, "sharpe") or _window_metric(history, "strategy_sharpe"),
        history_top_group_sharpe=_window_metric(history, "top_group_sharpe"),
        history_long_short_sharpe=_window_metric(history, "long_short_sharpe"),
        history_turnover=_window_metric(history, "turnover"),
        history_cagr=_window_metric(history, "cagr"),
        history_max_drawdown=_window_metric(history, "max_drawdown"),
        history_strategy_max_drawdown=_window_metric(history, "strategy_max_drawdown"),
        history_total_return=_window_metric(history, "total_return"),
        history_benchmark_total_return=_window_metric(history, "benchmark_total_return"),
        history_excess_total_return=_window_metric(history, "excess_total_return"),
        history_flipped=_to_bool(history.get("flipped")) if isinstance(history, Mapping) else None,
        stability_score=stability_score,
        market_fit=str(candidate.get("market_fit") or ""),
        failure_modes=str(candidate.get("failure_modes") or ""),
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )


def _normalize_candidates(candidates: Sequence[Mapping[str, Any]]) -> list[tuple[str, FactorMiningCandidate]]:
    normalized: list[tuple[str, FactorMiningCandidate]] = []
    for index, item in enumerate(candidates):
        if not isinstance(item, Mapping):
            raise ValueError(f"候选行不是对象：{index}")

        source_id = str(item.get("source_id") or item.get("source") or item.get("source_label") or f"source_{index}")
        source_label = str(item.get("source_label") or item.get("source") or source_id)
        row_index = _to_int(item.get("row_index"))
        if row_index is None:
            row_index = index
        row_key = str(item.get("row_key") or f"{source_id}:{row_index}")
        normalized.append((row_key, _extract_candidate_row(item, row_key=row_key, row_index=row_index, source_id=source_id, source_label=source_label)))
    return normalized


async def create_or_update_run_async(
    run_id: str | None,
    source_tag: str,
    params: dict[str, Any] | None,
    source_summary: str | None,
    status: str,
) -> str:
    run_uuid = _to_uuid(run_id) if run_id is not None else uuid.uuid4()
    async with _get_session_factory()() as session:
        run = await session.get(FactorMiningRun, run_uuid)
        if run is None:
            run = FactorMiningRun(
                id=run_uuid,
                source_tag=source_tag,
                status=status,
                params=params,
                source_summary=source_summary,
                generated_at=_utcnow(),
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            session.add(run)
        else:
            run.source_tag = source_tag
            run.status = status
            run.params = params
            run.source_summary = source_summary
            run.updated_at = _utcnow()
        await session.commit()
        return str(run.id)


def create_or_update_run(
    run_id: str | None,
    source_tag: str,
    params: dict[str, Any] | None,
    source_summary: str | None,
    status: str,
) -> str:
    return _run_sync_db(lambda: create_or_update_run_async(
        run_id=run_id,
        source_tag=source_tag,
        params=params,
        source_summary=source_summary,
        status=status,
    ))


async def complete_run_async(
    run_id: str,
    status: str,
    error_message: str | None,
) -> None:
    run_uuid = _to_uuid(run_id)
    async with _get_session_factory()() as session:
        run = await session.get(FactorMiningRun, run_uuid)
        if run is None:
            raise ValueError(f"运行不存在：{run_id}")
        run.status = status
        run.error_message = error_message
        run.updated_at = _utcnow()
        await session.commit()


def complete_run(run_id: str, status: str, error_message: str | None) -> None:
    _run_sync_db(lambda: complete_run_async(run_id=run_id, status=status, error_message=error_message))


async def save_mining_candidates_async(
    run_id: str,
    candidates: Sequence[Mapping[str, Any]],
    source_summary: str | None,
    status: str,
) -> list[str]:
    run_uuid = _to_uuid(run_id)
    normalized = _normalize_candidates(candidates)
    row_keys = [row_key for row_key, _ in normalized]

    async with _get_session_factory()() as session:
        run = await session.get(FactorMiningRun, run_uuid)
        if run is None:
            raise ValueError(f"运行不存在：{run_id}")
        if source_summary is not None:
            run.source_summary = source_summary
        await session.execute(delete(FactorMiningBacktestSeries).where(FactorMiningBacktestSeries.run_id == run_uuid))
        await session.execute(delete(FactorMiningCandidate).where(FactorMiningCandidate.run_id == run_uuid))
        for row_key, candidate in normalized:
            candidate.run_id = run_uuid
            candidate.id = None
            candidate.row_key = row_key
            session.add(candidate)
        run.status = status
        run.updated_at = _utcnow()
        await session.commit()
        return row_keys


def save_mining_candidates(
    run_id: str,
    candidates: Sequence[Mapping[str, Any]],
    source_summary: str | None,
    status: str,
) -> list[str]:
    return _run_sync_db(lambda: save_mining_candidates_async(
        run_id=run_id,
        candidates=candidates,
        source_summary=source_summary,
        status=status,
    ))


def _to_backtest_payload(payload: Mapping[str, Any], row_key: str) -> FactorMiningBacktestSeries:
    return FactorMiningBacktestSeries(
        row_key=row_key,
        source_id=str(payload.get("source_id") or ""),
        source_label=str(payload.get("source_label") or ""),
        row_index=_to_int(payload.get("row_index")) or 0,
        name=str(payload.get("name") or ""),
        expression=str(payload.get("expression") or ""),
        holding_period=_to_int(payload.get("holding_period")) or 1,
        status=str(payload.get("status") or "success"),
        error_message=str(payload.get("error_message") or "") or None,
        metrics=_safe_json(payload.get("metrics")),
        daily=_safe_json(payload.get("daily")),
        monthly=_safe_json(payload.get("monthly")),
        yearly=_safe_json(payload.get("yearly")),
        generated_at=_utcnow(),
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )


async def save_backtest_series_async(
    run_id: str,
    items: Mapping[str, Mapping[str, Any]],
    errors: Sequence[Mapping[str, Any]] | None,
) -> dict[str, int]:
    run_uuid = _to_uuid(run_id)
    normalized_errors = list(errors or [])

    async with _get_session_factory()() as session:
        run = await session.get(FactorMiningRun, run_uuid)
        if run is None:
            raise ValueError(f"运行不存在：{run_id}")

        candidate_rows = await session.execute(select(FactorMiningCandidate.id, FactorMiningCandidate.row_key).where(FactorMiningCandidate.run_id == run_uuid))
        candidate_map = {row_key: candidate_id for candidate_id, row_key in candidate_rows.all()}

        await session.execute(delete(FactorMiningBacktestSeries).where(FactorMiningBacktestSeries.run_id == run_uuid))

        success_count = 0
        fail_count = 0

        for row_key, payload in items.items():
            candidate_id = candidate_map.get(row_key)
            if candidate_id is None:
                normalized_errors.append({
                    "row_key": row_key,
                    "name": str(payload.get("name") or ""),
                    "message": f"回测已生成但候选不存在：{row_key}",
                })
                fail_count += 1
                continue

            series = _to_backtest_payload(payload, row_key=row_key)
            series.run_id = run_uuid
            series.candidate_id = candidate_id

            payload_status = str(payload.get("status") or "success")
            payload_error = payload.get("error_message")
            if payload_status == "success" and payload_error:
                payload_status = "failed"

            series.status = payload_status
            if payload_status == "failed" and not series.error_message and payload_error:
                series.error_message = str(payload_error)

            if series.status == "success":
                success_count += 1
            else:
                fail_count += 1

            session.add(series)

        if not items:
            success_count = 0
            fail_count = 0

        if normalized_errors:
            run.status = "partial_failed"
        else:
            run.status = "completed"
        run.error_message = "\n".join(item.get("message", "") for item in normalized_errors) if normalized_errors else None
        run.updated_at = _utcnow()
        await session.commit()

        return {
            "success_count": success_count,
            "fail_count": fail_count,
            "error_count": len(normalized_errors),
        }


async def save_backtest_series_rows_async(
    run_id: str,
    items: Mapping[str, Mapping[str, Any]],
    errors: Sequence[Mapping[str, Any]] | None,
) -> dict[str, int]:
    run_uuid = _to_uuid(run_id)
    normalized_errors = list(errors or [])
    target_row_keys = [str(row_key) for row_key in items.keys()]

    async with _get_session_factory()() as session:
        run = await session.get(FactorMiningRun, run_uuid)
        if run is None:
            raise ValueError(f"运行不存在：{run_id}")

        candidate_rows = await session.execute(
            select(FactorMiningCandidate.id, FactorMiningCandidate.row_key)
            .where(FactorMiningCandidate.run_id == run_uuid)
            .where(FactorMiningCandidate.row_key.in_(target_row_keys))
        )
        candidate_map = {row_key: candidate_id for candidate_id, row_key in candidate_rows.all()}

        if target_row_keys:
            await session.execute(
                delete(FactorMiningBacktestSeries)
                .where(FactorMiningBacktestSeries.run_id == run_uuid)
                .where(FactorMiningBacktestSeries.row_key.in_(target_row_keys))
            )

        success_count = 0
        fail_count = 0

        for row_key, payload in items.items():
            candidate_id = candidate_map.get(row_key)
            if candidate_id is None:
                normalized_errors.append({
                    "row_key": row_key,
                    "name": str(payload.get("name") or ""),
                    "message": f"回测已生成但候选不存在：{row_key}",
                })
                fail_count += 1
                continue

            series = _to_backtest_payload(payload, row_key=row_key)
            series.run_id = run_uuid
            series.candidate_id = candidate_id

            payload_status = str(payload.get("status") or "success")
            payload_error = payload.get("error_message")
            if payload_status == "success" and payload_error:
                payload_status = "failed"

            series.status = payload_status
            if payload_status == "failed" and not series.error_message and payload_error:
                series.error_message = str(payload_error)

            if series.status == "success":
                success_count += 1
            else:
                fail_count += 1

            session.add(series)

        await session.flush()

        failed_rows = await session.execute(
            select(
                FactorMiningBacktestSeries.row_key,
                FactorMiningBacktestSeries.name,
                FactorMiningBacktestSeries.error_message,
            )
            .where(FactorMiningBacktestSeries.run_id == run_uuid)
            .where(FactorMiningBacktestSeries.status != "success")
            .order_by(FactorMiningBacktestSeries.row_index.asc())
        )
        existing_failed = [
            {
                "row_key": str(row_key),
                "name": str(name or ""),
                "message": str(error_message or "回测未成功"),
            }
            for row_key, name, error_message in failed_rows.all()
        ]

        merged_errors: list[dict[str, str]] = []
        seen_error_keys: set[str] = set()
        for item in [*existing_failed, *normalized_errors]:
            error_key = f"{item.get('row_key', '')}:{item.get('message', '')}"
            if error_key in seen_error_keys:
                continue
            seen_error_keys.add(error_key)
            merged_errors.append({
                "row_key": str(item.get("row_key") or ""),
                "name": str(item.get("name") or ""),
                "message": str(item.get("message") or "回测未成功"),
            })

        if merged_errors:
            run.status = "partial_failed"
            run.error_message = "\n".join(item["message"] for item in merged_errors if item["message"])
        else:
            run.status = "completed"
            run.error_message = None
        run.updated_at = _utcnow()
        await session.commit()

        return {
            "success_count": success_count,
            "fail_count": fail_count,
            "error_count": len(merged_errors),
        }


def save_backtest_series(
    run_id: str,
    items: Mapping[str, Mapping[str, Any]],
    errors: Sequence[Mapping[str, Any]] | None,
) -> dict[str, int]:
    return _run_sync_db(lambda: save_backtest_series_async(run_id=run_id, items=items, errors=errors))


def save_backtest_series_rows(
    run_id: str,
    items: Mapping[str, Mapping[str, Any]],
    errors: Sequence[Mapping[str, Any]] | None,
) -> dict[str, int]:
    return _run_sync_db(lambda: save_backtest_series_rows_async(run_id=run_id, items=items, errors=errors))


def build_backtest_error_rows(items: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in items:
        message = str(item.get("message") if item.get("message") is not None else "").strip()
        if not message:
            continue
        rows.append({
            "row_key": str(item.get("row_key") or ""),
            "name": str(item.get("name") or ""),
            "message": message,
        })
    return rows
