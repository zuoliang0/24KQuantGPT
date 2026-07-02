from __future__ import annotations

import asyncio
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Sequence, TypedDict

import pandas as pd
from sqlalchemy import desc, select

from .backtest import api_context, run_factor_backtest
from .db import _get_session_factory, close_db, reset_db_state
from .expression_parser import parse_expression
from .factor_mining_backtest import (
    Candidate,
    RunContext,
    build_daily_rows,
    build_metrics,
    build_period_rows,
    load_benchmark_returns,
    load_market_data,
)
from .factor_mining_store import (
    complete_run,
    create_or_update_run,
    save_backtest_series,
    save_backtest_series_rows,
    save_mining_candidates,
    upsert_mining_candidates,
)
from .fundamental_data import ALL_FUNDAMENTAL_NAMES
from .iteration import compute_factor_score
from .market_data import BENCHMARK_CODES
from .models import FactorMiningCandidate, FactorMiningRun

ExecutionMode = Literal["async", "sync"]


class MiningWindow(TypedDict):
    label: str
    warmup_start: str
    validation_start: str
    validation_end: str


class WindowValidation(TypedDict):
    latest_window: MiningWindow
    history_window: MiningWindow


class WindowResult(TypedDict, total=False):
    status: str
    metrics: dict[str, Any]
    series_payload: dict[str, Any]
    error_message: str


_UNIVERSES = {"small_scale", "hs300", "csi500", "zz500", "csi1000", "csi2000"}
_VALIDATION_DUMMY = pd.DataFrame({
    "open": [1.0, 2.0, 3.0],
    "high": [1.1, 2.1, 3.1],
    "low": [0.9, 1.9, 2.9],
    "close": [1.0, 2.0, 3.0],
    "volume": [100.0, 200.0, 300.0],
    "amount": [100.0, 400.0, 900.0],
    "pct_change": [0.0, 100.0, 50.0],
    "trade_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
    **{name: [1.0, 1.1, 1.2] for name in ALL_FUNDAMENTAL_NAMES},
})


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isfinite(number):
        return round(number, 8)
    return 0.0


def _as_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} 不能是布尔值")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip():
        return int(value)
    raise ValueError(f"{field_name} 必须是整数")


def _as_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} 不能是布尔值")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} 必须是有效数字")
    return number


def _as_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y", "on"}:
            return True
        if text in {"false", "0", "no", "n", "off"}:
            return False
    raise ValueError(f"{field_name} 必须是布尔值")


def _validate_expression_text(expression: str) -> None:
    depth = 0
    for index, char in enumerate(expression):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise ValueError(f"表达式括号不平衡：位置 {index} 多余右括号")
    if depth > 0:
        raise ValueError(f"表达式括号不平衡：缺少 {depth} 个右括号")
    factor_func = parse_expression(expression)
    factor_func(_VALIDATION_DUMMY)


def _validate_window(raw_window: Mapping[str, Any], window_id: str) -> MiningWindow:
    label = str(raw_window.get("label") or window_id)
    warmup_start = str(raw_window.get("warmup_start") or "").strip()
    validation_start = str(raw_window.get("validation_start") or "").strip()
    validation_end = str(raw_window.get("validation_end") or "").strip()
    if not warmup_start or not validation_start or not validation_end:
        raise ValueError(f"{window_id} 必须包含 warmup_start、validation_start、validation_end")
    warmup_ts = pd.Timestamp(warmup_start)
    start_ts = pd.Timestamp(validation_start)
    end_ts = pd.Timestamp(validation_end)
    if warmup_ts > start_ts:
        raise ValueError(f"{window_id} 的 warmup_start 不能晚于 validation_start")
    if start_ts > end_ts:
        raise ValueError(f"{window_id} 的 validation_start 不能晚于 validation_end")
    return {
        "label": label,
        "warmup_start": warmup_start,
        "validation_start": validation_start,
        "validation_end": validation_end,
    }


def _normalize_candidate(raw_candidate: Mapping[str, Any], index: int) -> Candidate:
    name = str(raw_candidate.get("name") or "").strip()
    expression = str(raw_candidate.get("expression") or "").strip()
    if not name:
        raise ValueError(f"第 {index + 1} 个候选缺少 name")
    if not expression:
        raise ValueError(f"第 {index + 1} 个候选缺少 expression")
    _validate_expression_text(expression)
    row_key = str(raw_candidate.get("row_key") or f"candidate_{index + 1:03d}").strip()
    source_id = str(raw_candidate.get("source_id") or "mcp_factor_mining").strip()
    source_label = str(raw_candidate.get("source_label") or "MCP 因子挖掘").strip()
    holding_period = _as_int(raw_candidate.get("holding_period"), f"{row_key}.holding_period")
    n_groups = _as_int(raw_candidate.get("n_groups", 5), f"{row_key}.n_groups")
    cost_rate = _as_float(raw_candidate.get("cost_rate", 0.003), f"{row_key}.cost_rate")
    neutralize_industry = _as_bool(raw_candidate.get("neutralize_industry", True), f"{row_key}.neutralize_industry")
    neutralize_cap = _as_bool(raw_candidate.get("neutralize_cap", True), f"{row_key}.neutralize_cap")
    if holding_period <= 0:
        raise ValueError(f"{row_key}.holding_period 必须大于 0")
    if n_groups < 2:
        raise ValueError(f"{row_key}.n_groups 必须至少为 2")
    return {
        "row_key": row_key,
        "source_id": source_id,
        "source_label": source_label,
        "row_index": index + 1,
        "name": name,
        "expression": expression,
        "holding_period": holding_period,
        "n_groups": n_groups,
        "cost_rate": cost_rate,
        "neutralize_industry": neutralize_industry,
        "neutralize_cap": neutralize_cap,
    }


def normalize_factor_mining_request(
    universe: str,
    benchmark: str,
    latest_window: Mapping[str, Any],
    history_window: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[WindowValidation, list[Candidate]]:
    normalized_universe = universe.strip().lower()
    normalized_benchmark = benchmark.strip().lower()
    if normalized_universe not in _UNIVERSES:
        raise ValueError(f"无效股票池：{universe}，可选：{sorted(_UNIVERSES)}")
    if normalized_benchmark not in BENCHMARK_CODES:
        raise ValueError(f"无效基准：{benchmark}，可选：{sorted(BENCHMARK_CODES)}")
    if not candidates:
        raise ValueError("候选因子不能为空")
    normalized_candidates = [
        _normalize_candidate(candidate, index)
        for index, candidate in enumerate(candidates)
    ]
    row_keys = [candidate["row_key"] for candidate in normalized_candidates]
    if len(row_keys) != len(set(row_keys)):
        raise ValueError(f"候选 row_key 重复：{row_keys}")
    return {
        "latest_window": _validate_window(latest_window, "latest_window"),
        "history_window": _validate_window(history_window, "history_window"),
    }, normalized_candidates


def _normalized_market_scope(universe: str, benchmark: str) -> tuple[str, str]:
    return universe.strip().lower(), benchmark.strip().lower()


def validate_factor_mining_batch_request(
    universe: str,
    benchmark: str,
    latest_window: Mapping[str, Any],
    history_window: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    windows, normalized_candidates = normalize_factor_mining_request(
        universe,
        benchmark,
        latest_window,
        history_window,
        candidates,
    )
    normalized_universe, normalized_benchmark = _normalized_market_scope(universe, benchmark)
    return {
        "status": "ok",
        "universe": normalized_universe,
        "benchmark": normalized_benchmark,
        "candidate_count": len(normalized_candidates),
        "row_keys": [candidate["row_key"] for candidate in normalized_candidates],
        "windows": windows,
    }


def _run_context(run_id: str, source_tag: str, universe: str, benchmark: str, window: MiningWindow) -> RunContext:
    return {
        "run_id": run_id,
        "source_tag": source_tag,
        "universe": universe,
        "benchmark": benchmark,
        "warmup_start": window["warmup_start"],
        "validation_start": window["validation_start"],
        "validation_end": window["validation_end"],
        "trading_days_per_year": 252,
    }


def _window_params(latest_window: MiningWindow, history_window: MiningWindow) -> list[dict[str, str]]:
    return [
        {
            "id": "latest",
            "label": latest_window["label"],
            "warmup_start": latest_window["warmup_start"],
            "validation_start": latest_window["validation_start"],
            "validation_end": latest_window["validation_end"],
        },
        {
            "id": "history",
            "label": history_window["label"],
            "warmup_start": history_window["warmup_start"],
            "validation_start": history_window["validation_start"],
            "validation_end": history_window["validation_end"],
        },
    ]


def _common_value(values: Sequence[Any]) -> Any | None:
    unique_values = set(values)
    if len(unique_values) == 1:
        return next(iter(unique_values))
    return None


def _common_candidate_params(candidates: Sequence[Candidate]) -> dict[str, Any | None]:
    n_groups = [candidate["n_groups"] for candidate in candidates]
    cost_rates = [candidate["cost_rate"] for candidate in candidates]
    neutralize_industries = [candidate["neutralize_industry"] for candidate in candidates]
    neutralize_caps = [candidate["neutralize_cap"] for candidate in candidates]
    return {
        "n_groups": _common_value(n_groups),
        "cost_rate": _common_value(cost_rates),
        "neutralize_industry": _common_value(neutralize_industries),
        "neutralize_cap": _common_value(neutralize_caps),
    }


def _run_params(
    universe: str,
    benchmark: str,
    latest_window: MiningWindow,
    history_window: MiningWindow,
    candidates: Sequence[Candidate],
) -> dict[str, Any]:
    common_params = _common_candidate_params(candidates)
    return {
        "universe": universe,
        "benchmark": benchmark,
        "n_groups": common_params["n_groups"],
        "cost_rate": common_params["cost_rate"],
        "neutralize_industry": common_params["neutralize_industry"],
        "neutralize_cap": common_params["neutralize_cap"],
        "windows": _window_params(latest_window, history_window),
        "candidate_count": len(candidates),
    }


def _pending_candidate_payload(candidate: Candidate) -> dict[str, Any]:
    return {
        **candidate,
        "status": "pending",
        "score": None,
        "grade": "D",
        "latest": None,
        "history": None,
        "stability_score": None,
        "market_fit": "等待回测",
        "failure_modes": "",
    }


def _score_grade(score: float | None) -> str:
    if score is None:
        return "D"
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    return "D"


def _aligned_strategy_returns(backtest_result: dict[str, Any], benchmark_returns: pd.Series, run_context: RunContext) -> tuple[pd.Series, pd.Series]:
    strategy_returns = backtest_result["strategy_returns"].copy()
    strategy_returns.index = pd.to_datetime(strategy_returns.index).normalize()
    strategy_returns = strategy_returns.sort_index()
    strategy_returns = strategy_returns[
        (strategy_returns.index >= pd.Timestamp(run_context["validation_start"]))
        & (strategy_returns.index <= pd.Timestamp(run_context["validation_end"]))
    ]
    aligned = pd.concat(
        [
            strategy_returns.rename("strategy"),
            benchmark_returns.reindex(strategy_returns.index).rename("benchmark"),
        ],
        axis=1,
    ).dropna()
    if len(aligned) < 2:
        raise RuntimeError(f"策略和基准没有足够重叠日期：{run_context['benchmark']} {run_context['validation_start']} 至 {run_context['validation_end']}")
    return aligned["strategy"], aligned["benchmark"]


def _window_metric_payload(backtest_result: dict[str, Any], return_metrics: dict[str, float], score: float, grade: str) -> dict[str, Any]:
    return {
        "score": score,
        "grade": grade,
        "ic_mean": _json_float(backtest_result.get("ic_mean")),
        "rank_ic_mean": _json_float(backtest_result.get("rank_ic_mean")),
        "ic_ir": _json_float(backtest_result.get("ic_ir")),
        "ic_win_rate": _json_float(backtest_result.get("ic_win_rate")),
        "monotonicity": _json_float(backtest_result.get("monotonicity_score")),
        "sharpe": return_metrics.get("sharpe"),
        "strategy_sharpe": return_metrics.get("sharpe"),
        "top_group_sharpe": _json_float(backtest_result.get("top_group_sharpe")),
        "long_short_sharpe": _json_float(backtest_result.get("long_short_sharpe")),
        "turnover": _json_float(backtest_result.get("turnover")),
        "cagr": return_metrics.get("cagr"),
        "max_drawdown": return_metrics.get("max_drawdown"),
        "strategy_max_drawdown": return_metrics.get("max_drawdown"),
        "total_return": return_metrics.get("total_return"),
        "benchmark_total_return": return_metrics.get("benchmark_total_return"),
        "excess_total_return": return_metrics.get("excess_total_return"),
        "flipped": bool(backtest_result.get("flipped", False)),
    }


def _series_metric_payload(return_metrics: dict[str, float], window_metrics: dict[str, Any], backtest_result: dict[str, Any]) -> dict[str, Any]:
    return {
        **return_metrics,
        "score": window_metrics["score"],
        "grade": window_metrics["grade"],
        "ic_mean": window_metrics["ic_mean"],
        "rank_ic_mean": window_metrics["rank_ic_mean"],
        "ic_ir": window_metrics["ic_ir"],
        "ic_win_rate": window_metrics["ic_win_rate"],
        "monotonicity": window_metrics["monotonicity"],
        "top_group_sharpe": window_metrics["top_group_sharpe"],
        "long_short_sharpe": window_metrics["long_short_sharpe"],
        "turnover": window_metrics["turnover"],
        "flipped": window_metrics["flipped"],
        "wq_fitness": _json_float(backtest_result.get("wq_fitness")),
    }


def _evaluate_candidate_window(
    candidate: Candidate,
    market_df: pd.DataFrame,
    benchmark_returns: pd.Series,
    run_context: RunContext,
) -> WindowResult:
    try:
        with api_context():
            backtest_result = run_factor_backtest(
                market_df,
                expression=candidate["expression"],
                n_groups=candidate["n_groups"],
                holding_period=candidate["holding_period"],
                cost_rate=candidate["cost_rate"],
                neutralize_industry=candidate["neutralize_industry"],
                neutralize_cap=candidate["neutralize_cap"],
                trading_days_per_year=run_context["trading_days_per_year"],
                rebalance_anchor=run_context["validation_start"],
            )
        strategy_returns, benchmark_aligned = _aligned_strategy_returns(backtest_result, benchmark_returns, run_context)
        return_metrics = build_metrics(strategy_returns, benchmark_aligned, run_context["trading_days_per_year"])
        scoring = compute_factor_score(
            {
                "long_short_sharpe": backtest_result.get("long_short_sharpe", 0.0),
                "monotonicity_score": backtest_result.get("monotonicity_score", 0.0),
                "spread": backtest_result.get("spread", 0.0),
                "ic_mean": backtest_result.get("ic_mean", 0.0),
                "rank_ic_mean": backtest_result.get("rank_ic_mean", 0.0),
                "ic_ir": backtest_result.get("ic_ir", 0.0),
                "ic_win_rate": backtest_result.get("ic_win_rate", 0.0),
                "turnover": backtest_result.get("turnover", 0.0),
            },
            return_metrics,
            data_days=len(strategy_returns),
        )
        score = float(scoring["score"])
        grade = str(scoring["grade"])
        window_metrics = _window_metric_payload(backtest_result, return_metrics, score, grade)
        series_payload = {
            "row_key": candidate["row_key"],
            "source_id": candidate["source_id"],
            "source_label": candidate["source_label"],
            "row_index": candidate["row_index"],
            "name": candidate["name"],
            "expression": candidate["expression"],
            "holding_period": candidate["holding_period"],
            "status": "success",
            "metrics": _series_metric_payload(return_metrics, window_metrics, backtest_result),
            "daily": build_daily_rows(strategy_returns, benchmark_aligned),
            "monthly": build_period_rows(strategy_returns, benchmark_aligned, "M"),
            "yearly": build_period_rows(strategy_returns, benchmark_aligned, "Y"),
        }
        return {
            "status": "success",
            "metrics": window_metrics,
            "series_payload": series_payload,
        }
    except Exception as error:
        return {
            "status": "failed",
            "error_message": f"{type(error).__name__}: {error}",
        }


def _evaluate_window(candidates: Sequence[Candidate], run_context: RunContext) -> dict[str, WindowResult]:
    try:
        market_df, _stock_codes = load_market_data(candidates, run_context)
        benchmark_returns = load_benchmark_returns(run_context)
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        return {
            candidate["row_key"]: {"status": "failed", "error_message": message}
            for candidate in candidates
        }
    return {
        candidate["row_key"]: _evaluate_candidate_window(candidate, market_df, benchmark_returns, run_context)
        for candidate in candidates
    }


def _combined_score(latest_result: WindowResult, history_result: WindowResult) -> float | None:
    scores: list[float] = []
    if latest_result.get("status") == "success":
        latest_metrics = latest_result.get("metrics")
        if isinstance(latest_metrics, dict):
            scores.append(float(latest_metrics["score"]))
    if history_result.get("status") == "success":
        history_metrics = history_result.get("metrics")
        if isinstance(history_metrics, dict):
            scores.append(float(history_metrics["score"]))
    if not scores:
        return None
    return round(sum(scores) / len(scores), 1)


def _failure_message(latest_result: WindowResult, history_result: WindowResult) -> str:
    messages: list[str] = []
    if latest_result.get("status") != "success":
        messages.append(f"latest: {latest_result.get('error_message') or '回测失败'}")
    if history_result.get("status") != "success":
        messages.append(f"history: {history_result.get('error_message') or '回测失败'}")
    return "\n".join(messages)


def _candidate_payload(candidate: Candidate, latest_result: WindowResult, history_result: WindowResult) -> dict[str, Any]:
    combined_score = _combined_score(latest_result, history_result)
    latest_metrics = latest_result.get("metrics") if latest_result.get("status") == "success" else None
    history_metrics = history_result.get("metrics") if history_result.get("status") == "success" else None
    latest_failed = latest_result.get("status") != "success"
    candidate_status = "failed" if latest_failed else "success"
    return {
        **candidate,
        "status": candidate_status,
        "score": combined_score,
        "grade": _score_grade(combined_score),
        "latest": latest_metrics,
        "history": history_metrics,
        "stability_score": combined_score,
        "market_fit": "latest/history 双窗口因子挖掘",
        "failure_modes": _failure_message(latest_result, history_result),
    }


def _failed_series_payload(candidate: Candidate, latest_result: WindowResult) -> dict[str, Any]:
    return {
        "row_key": candidate["row_key"],
        "source_id": candidate["source_id"],
        "source_label": candidate["source_label"],
        "row_index": candidate["row_index"],
        "name": candidate["name"],
        "expression": candidate["expression"],
        "holding_period": candidate["holding_period"],
        "status": "failed",
        "error_message": latest_result.get("error_message") or "latest 窗口回测失败",
        "metrics": None,
        "daily": [],
        "monthly": [],
        "yearly": [],
    }


def _build_evaluation_payload(
    run_id: str,
    source_tag: str,
    universe: str,
    benchmark: str,
    latest_window: MiningWindow,
    history_window: MiningWindow,
    candidates: Sequence[Candidate],
) -> dict[str, Any]:
    latest_context = _run_context(run_id, source_tag, universe, benchmark, latest_window)
    history_context = _run_context(run_id, source_tag, universe, benchmark, history_window)
    latest_results = _evaluate_window(candidates, latest_context)
    history_results = _evaluate_window(candidates, history_context)
    candidate_payloads: list[dict[str, Any]] = []
    series_items: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []

    for candidate in candidates:
        row_key = candidate["row_key"]
        latest_result = latest_results[row_key]
        history_result = history_results[row_key]
        candidate_payloads.append(_candidate_payload(candidate, latest_result, history_result))
        if latest_result.get("status") == "success":
            series_payload = latest_result.get("series_payload")
            if isinstance(series_payload, dict):
                series_items[row_key] = series_payload
        else:
            failed_payload = _failed_series_payload(candidate, latest_result)
            series_items[row_key] = failed_payload
            errors.append({
                "row_key": row_key,
                "name": candidate["name"],
                "message": str(failed_payload["error_message"]),
            })
        if latest_result.get("status") == "success" and history_result.get("status") != "success":
            errors.append({
                "row_key": row_key,
                "name": candidate["name"],
                "message": str(history_result.get("error_message") or "history 窗口回测失败"),
            })

    return {
        "generated_at": _utcnow(),
        "candidates": candidate_payloads,
        "items": series_items,
        "errors": errors,
        "params": {
            "run_id": run_id,
            "source_tag": source_tag,
            "universe": universe,
            "benchmark": benchmark,
            "latest_window": latest_window,
            "history_window": history_window,
            "candidate_count": len(candidates),
            "success_count": len([item for item in series_items.values() if item.get("status") == "success"]),
            "error_count": len(errors),
        },
    }


def _dashboard_paths(run_id: str) -> dict[str, str]:
    return {
        "run_api": f"/api/v1/factor-mining/runs/{run_id}",
        "backtest_series_api": f"/api/v1/factor-mining/runs/{run_id}/backtest-series",
        "dashboard_hash": "#factor-mining",
    }


def _final_status(errors: Sequence[Mapping[str, Any]], items: Mapping[str, Mapping[str, Any]]) -> str:
    if not items:
        return "failed"
    success_count = len([item for item in items.values() if item.get("status") == "success"])
    if success_count == 0:
        return "failed"
    if errors:
        return "partial_failed"
    return "completed"


def _error_text(errors: Sequence[Mapping[str, Any]]) -> str | None:
    if not errors:
        return None
    return "\n".join(str(item.get("message") or "") for item in errors if item.get("message"))


def _parse_run_uuid(run_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(run_id)
    except (TypeError, ValueError) as error:
        raise ValueError(f"run_id 无效：{run_id}") from error


def start_factor_mining_batch(
    source_tag: str,
    source_summary: str,
    universe: str,
    benchmark: str,
    latest_window: Mapping[str, Any],
    history_window: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    windows, normalized_candidates = normalize_factor_mining_request(
        universe,
        benchmark,
        latest_window,
        history_window,
        candidates,
    )
    normalized_universe, normalized_benchmark = _normalized_market_scope(universe, benchmark)
    params = _run_params(
        normalized_universe,
        normalized_benchmark,
        windows["latest_window"],
        windows["history_window"],
        normalized_candidates,
    )
    run_id = create_or_update_run(
        run_id=None,
        source_tag=source_tag,
        params=params,
        source_summary=source_summary,
        status="running",
    )
    save_mining_candidates(
        run_id=run_id,
        candidates=[_pending_candidate_payload(candidate) for candidate in normalized_candidates],
        source_summary=source_summary,
        status="running",
    )
    return {
        "run_id": run_id,
        "candidates": normalized_candidates,
        "windows": windows,
        "universe": normalized_universe,
        "benchmark": normalized_benchmark,
        "paths": _dashboard_paths(run_id),
    }


def execute_factor_mining_batch(
    run_id: str,
    source_tag: str,
    source_summary: str,
    universe: str,
    benchmark: str,
    latest_window: MiningWindow,
    history_window: MiningWindow,
    candidates: Sequence[Candidate],
    persist_mode: Literal["replace", "upsert"],
) -> dict[str, Any]:
    try:
        payload = _build_evaluation_payload(
            run_id,
            source_tag,
            universe,
            benchmark,
            latest_window,
            history_window,
            candidates,
        )
        if persist_mode == "replace":
            save_mining_candidates(
                run_id=run_id,
                candidates=payload["candidates"],
                source_summary=source_summary,
                status="running",
            )
            series_result = save_backtest_series(
                run_id=run_id,
                items=payload["items"],
                errors=payload["errors"],
            )
        else:
            upsert_mining_candidates(
                run_id=run_id,
                candidates=payload["candidates"],
                source_summary=source_summary,
                status="running",
            )
            series_result = save_backtest_series_rows(
                run_id=run_id,
                items=payload["items"],
                errors=payload["errors"],
            )
        final_status = _final_status(payload["errors"], payload["items"])
        if persist_mode == "upsert":
            final_status = str(series_result.get("status") or final_status)
        else:
            complete_run(run_id, final_status, _error_text(payload["errors"]))
        return {
            "run_id": run_id,
            "status": final_status,
            "result": series_result,
            "top_candidates": _top_candidates(payload["candidates"], 10),
            "paths": _dashboard_paths(run_id),
            "errors": payload["errors"],
        }
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        complete_run(run_id, "failed", message)
        return {
            "run_id": run_id,
            "status": "failed",
            "error": message,
            "paths": _dashboard_paths(run_id),
            "errors": [{"row_key": "", "name": "", "message": message}],
        }


def run_factor_mining_batch_sync(
    source_tag: str,
    source_summary: str,
    universe: str,
    benchmark: str,
    latest_window: Mapping[str, Any],
    history_window: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    started = start_factor_mining_batch(
        source_tag,
        source_summary,
        universe,
        benchmark,
        latest_window,
        history_window,
        candidates,
    )
    return execute_factor_mining_batch(
        started["run_id"],
        source_tag,
        source_summary,
        started["universe"],
        started["benchmark"],
        started["windows"]["latest_window"],
        started["windows"]["history_window"],
        started["candidates"],
        "replace",
    )


def _task_result(run_id: str, status: str, message: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "status": status,
        "message": message,
        "paths": _dashboard_paths(run_id),
    }


def submit_factor_mining_batch_async(
    source_tag: str,
    source_summary: str,
    universe: str,
    benchmark: str,
    latest_window: Mapping[str, Any],
    history_window: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = start_factor_mining_batch(
        source_tag,
        source_summary,
        universe,
        benchmark,
        latest_window,
        history_window,
        candidates,
    )
    job = {
        "run_id": started["run_id"],
        "source_tag": source_tag,
        "source_summary": source_summary,
        "universe": started["universe"],
        "benchmark": started["benchmark"],
        "latest_window": started["windows"]["latest_window"],
        "history_window": started["windows"]["history_window"],
        "candidates": started["candidates"],
        "persist_mode": "replace",
    }
    return _task_result(started["run_id"], "running", "因子挖掘批次已提交"), job


async def _run_sync_db(operation) -> Any:
    reset_db_state()
    try:
        return await operation()
    finally:
        await close_db()


def _run_db(operation) -> Any:
    return asyncio.run(_run_sync_db(operation))


async def _load_run_async(run_id: str) -> FactorMiningRun:
    run_uuid = _parse_run_uuid(run_id)
    async with _get_session_factory()() as session:
        run = await session.get(FactorMiningRun, run_uuid)
        if run is None:
            raise ValueError(f"运行不存在：{run_id}")
        return run


def _window_from_run(run: FactorMiningRun) -> WindowValidation:
    params = run.params if isinstance(run.params, dict) else {}
    windows = params.get("windows")
    if not isinstance(windows, list) or len(windows) < 2:
        raise ValueError(f"运行缺少 latest/history 窗口参数：{run.id}")
    latest_raw = next((item for item in windows if isinstance(item, dict) and item.get("id") == "latest"), windows[0])
    history_raw = next((item for item in windows if isinstance(item, dict) and item.get("id") == "history"), windows[1])
    if not isinstance(latest_raw, dict) or not isinstance(history_raw, dict):
        raise ValueError(f"运行窗口参数格式错误：{run.id}")
    return {
        "latest_window": _validate_window(latest_raw, "latest_window"),
        "history_window": _validate_window(history_raw, "history_window"),
    }


def _universe_benchmark_from_run(run: FactorMiningRun) -> tuple[str, str]:
    params = run.params if isinstance(run.params, dict) else {}
    universe = str(params.get("universe") or "hs300")
    benchmark = str(params.get("benchmark") or "hs300")
    return _normalized_market_scope(universe, benchmark)


def _load_run_for_append(run_id: str) -> tuple[FactorMiningRun, str, str, WindowValidation]:
    async def _operation() -> tuple[FactorMiningRun, str, str, WindowValidation]:
        run = await _load_run_async(run_id)
        universe, benchmark = _universe_benchmark_from_run(run)
        return run, universe, benchmark, _window_from_run(run)
    return _run_db(_operation)


def append_factor_mining_candidates_sync(run_id: str, candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    run, universe, benchmark, windows = _load_run_for_append(run_id)
    _validated_windows, normalized_candidates = normalize_factor_mining_request(
        universe,
        benchmark,
        windows["latest_window"],
        windows["history_window"],
        candidates,
    )
    upsert_mining_candidates(
        run_id=run_id,
        candidates=[_pending_candidate_payload(candidate) for candidate in normalized_candidates],
        source_summary=run.source_summary,
        status="running",
    )
    return execute_factor_mining_batch(
        run_id,
        str(run.source_tag),
        str(run.source_summary or ""),
        universe,
        benchmark,
        windows["latest_window"],
        windows["history_window"],
        normalized_candidates,
        "upsert",
    )


def submit_append_factor_mining_candidates_async(run_id: str, candidates: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    run, universe, benchmark, windows = _load_run_for_append(run_id)
    _validated_windows, normalized_candidates = normalize_factor_mining_request(
        universe,
        benchmark,
        windows["latest_window"],
        windows["history_window"],
        candidates,
    )
    upsert_mining_candidates(
        run_id=run_id,
        candidates=[_pending_candidate_payload(candidate) for candidate in normalized_candidates],
        source_summary=run.source_summary,
        status="running",
    )
    job = {
        "run_id": run_id,
        "source_tag": str(run.source_tag),
        "source_summary": str(run.source_summary or ""),
        "universe": universe,
        "benchmark": benchmark,
        "latest_window": windows["latest_window"],
        "history_window": windows["history_window"],
        "candidates": normalized_candidates,
        "persist_mode": "upsert",
    }
    return _task_result(run_id, "running", "候选追加任务已提交"), job


async def _load_candidate_async(run_id: str, row_key: str) -> tuple[FactorMiningRun, Candidate]:
    run_uuid = _parse_run_uuid(run_id)
    async with _get_session_factory()() as session:
        run = await session.get(FactorMiningRun, run_uuid)
        if run is None:
            raise ValueError(f"运行不存在：{run_id}")
        result = await session.execute(
            select(FactorMiningCandidate)
            .where(FactorMiningCandidate.run_id == run.id)
            .where(FactorMiningCandidate.row_key == row_key)
        )
        candidate = result.scalar_one_or_none()
        if candidate is None:
            raise ValueError(f"候选不存在：{run_id} {row_key}")
        return run, {
            "row_key": str(candidate.row_key),
            "source_id": str(candidate.source_id),
            "source_label": str(candidate.source_label),
            "row_index": int(candidate.row_index),
            "name": str(candidate.name),
            "expression": str(candidate.expression),
            "holding_period": int(candidate.holding_period),
            "n_groups": int(candidate.n_groups),
            "cost_rate": float(candidate.cost_rate),
            "neutralize_industry": bool(candidate.neutralize_industry),
            "neutralize_cap": bool(candidate.neutralize_cap),
        }


def refresh_factor_mining_candidate_sync(run_id: str, row_key: str) -> dict[str, Any]:
    async def _operation() -> tuple[FactorMiningRun, Candidate]:
        return await _load_candidate_async(run_id, row_key)
    run, candidate = _run_db(_operation)
    universe, benchmark = _universe_benchmark_from_run(run)
    windows = _window_from_run(run)
    return execute_factor_mining_batch(
        run_id,
        str(run.source_tag),
        str(run.source_summary or ""),
        universe,
        benchmark,
        windows["latest_window"],
        windows["history_window"],
        [candidate],
        "upsert",
    )


def submit_refresh_factor_mining_candidate_async(run_id: str, row_key: str) -> tuple[dict[str, Any], dict[str, Any]]:
    async def _operation() -> tuple[FactorMiningRun, Candidate]:
        return await _load_candidate_async(run_id, row_key)
    run, candidate = _run_db(_operation)
    universe, benchmark = _universe_benchmark_from_run(run)
    windows = _window_from_run(run)
    upsert_mining_candidates(
        run_id=run_id,
        candidates=[_pending_candidate_payload(candidate)],
        source_summary=run.source_summary,
        status="running",
    )
    job = {
        "run_id": run_id,
        "source_tag": str(run.source_tag),
        "source_summary": str(run.source_summary or ""),
        "universe": universe,
        "benchmark": benchmark,
        "latest_window": windows["latest_window"],
        "history_window": windows["history_window"],
        "candidates": [candidate],
        "persist_mode": "upsert",
    }
    return _task_result(run_id, "running", "候选刷新任务已提交"), job


def _top_candidates(candidates: Sequence[Mapping[str, Any]], limit: int) -> list[dict[str, Any]]:
    rows = sorted(
        candidates,
        key=lambda item: float(item.get("score") or -1.0),
        reverse=True,
    )
    return [
        {
            "row_key": item.get("row_key"),
            "name": item.get("name"),
            "expression": item.get("expression"),
            "score": item.get("score"),
            "grade": item.get("grade"),
            "status": item.get("status"),
            "latest": item.get("latest"),
            "history": item.get("history"),
        }
        for item in rows[:limit]
    ]


async def _run_snapshot_async(run_id: str) -> dict[str, Any]:
    run_uuid = _parse_run_uuid(run_id)
    async with _get_session_factory()() as session:
        run = await session.get(FactorMiningRun, run_uuid)
        if run is None:
            raise ValueError(f"运行不存在：{run_id}")
        result = await session.execute(
            select(FactorMiningCandidate)
            .where(FactorMiningCandidate.run_id == run.id)
            .order_by(FactorMiningCandidate.score.desc().nullslast(), FactorMiningCandidate.row_index.asc())
        )
        candidates = [
            {
                "row_key": row.row_key,
                "name": row.name,
                "expression": row.expression,
                "score": row.score,
                "grade": row.grade,
                "status": row.status,
                "latest": {
                    "score": row.latest_score,
                    "ic_mean": row.latest_ic_mean,
                    "ic_ir": row.latest_ic_ir,
                    "ic_win_rate": row.latest_ic_win_rate,
                    "monotonicity": row.latest_monotonicity,
                    "sharpe": row.latest_sharpe,
                    "long_short_sharpe": row.latest_long_short_sharpe,
                    "turnover": row.latest_turnover,
                    "cagr": row.latest_cagr,
                    "max_drawdown": row.latest_max_drawdown,
                    "total_return": row.latest_total_return,
                    "excess_total_return": row.latest_excess_total_return,
                },
                "history": {
                    "score": row.history_score,
                    "ic_mean": row.history_ic_mean,
                    "ic_ir": row.history_ic_ir,
                    "ic_win_rate": row.history_ic_win_rate,
                    "monotonicity": row.history_monotonicity,
                    "sharpe": row.history_sharpe,
                    "long_short_sharpe": row.history_long_short_sharpe,
                    "turnover": row.history_turnover,
                    "cagr": row.history_cagr,
                    "max_drawdown": row.history_max_drawdown,
                    "total_return": row.history_total_return,
                    "excess_total_return": row.history_excess_total_return,
                },
                "failure_modes": row.failure_modes,
            }
            for row in result.scalars().all()
        ]
        return {
            "run": {
                "id": str(run.id),
                "source_tag": run.source_tag,
                "status": run.status,
                "params": run.params,
                "source_summary": run.source_summary,
                "error_message": run.error_message,
                "generated_at": run.generated_at.isoformat() if run.generated_at else None,
                "updated_at": run.updated_at.isoformat() if run.updated_at else None,
            },
            "candidate_count": len(candidates),
            "top_candidates": _top_candidates(candidates, 10),
            "paths": _dashboard_paths(str(run.id)),
        }


def get_factor_mining_run_snapshot(run_id: str) -> dict[str, Any]:
    return _run_db(lambda: _run_snapshot_async(run_id))


async def _list_runs_async(limit: int) -> dict[str, Any]:
    async with _get_session_factory()() as session:
        result = await session.execute(
            select(FactorMiningRun)
            .order_by(desc(FactorMiningRun.generated_at))
            .limit(limit)
        )
        runs = [
            {
                "id": str(run.id),
                "source_tag": run.source_tag,
                "status": run.status,
                "params": run.params,
                "source_summary": run.source_summary,
                "error_message": run.error_message,
                "generated_at": run.generated_at.isoformat() if run.generated_at else None,
                "updated_at": run.updated_at.isoformat() if run.updated_at else None,
                "paths": _dashboard_paths(str(run.id)),
            }
            for run in result.scalars().all()
        ]
        return {"runs": runs}


def list_factor_mining_run_snapshots(limit: int) -> dict[str, Any]:
    safe_limit = max(1, min(limit, 100))
    return _run_db(lambda: _list_runs_async(safe_limit))


def run_factor_mining_job(job: Mapping[str, Any]) -> dict[str, Any]:
    return execute_factor_mining_batch(
        str(job["run_id"]),
        str(job["source_tag"]),
        str(job["source_summary"]),
        str(job["universe"]),
        str(job["benchmark"]),
        job["latest_window"],
        job["history_window"],
        job["candidates"],
        job["persist_mode"],
    )
