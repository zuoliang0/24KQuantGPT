#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence, TypedDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TMP_ROOT = PROJECT_ROOT / "tmp" / "factor-mining"

import pandas as pd
from sqlalchemy import desc, select

from quantgpt.backtest import api_context, run_factor_backtest
from quantgpt.db import _get_session_factory
from quantgpt.factor_mining_store import complete_run, save_backtest_series
from quantgpt.fundamental_data import detect_fundamental_vars, enrich_market_data
from quantgpt.market_data import MarketDataFetcher, fetch_benchmark_returns, get_universe
from quantgpt.models import FactorMiningCandidate, FactorMiningRun


DEFAULT_WARMUP_START = "2025-09-01"
DEFAULT_VALIDATION_START = "2026-01-01"
DEFAULT_VALIDATION_END = "2026-06-29"
DEFAULT_UNIVERSE = "hs300"
DEFAULT_BENCHMARK = "hs300"
DEFAULT_TRADING_DAYS_PER_YEAR = 252


class Candidate(TypedDict):
    row_key: str
    source_id: str
    source_label: str
    row_index: int
    name: str
    expression: str
    holding_period: int
    n_groups: int
    cost_rate: float
    neutralize_industry: bool
    neutralize_cap: bool


class RunContext(TypedDict):
    run_id: str
    source_tag: str
    universe: str
    benchmark: str
    warmup_start: str
    validation_start: str
    validation_end: str
    trading_days_per_year: int


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="为已入库的因子批次生成回测序列并回写数据库")
    parser.add_argument("--run-id", type=str, default=None, help="指定运行批次 ID")
    parser.add_argument("--source-tag", type=str, default=None, help="按 source_tag 选择最新一批运行")
    parser.add_argument("--limit", type=int, default=None, help="只生成前 N 个候选，用于快速验证")
    parser.add_argument("--output", type=Path, default=None, help="导出 JSON 路径（可选）")
    parser.add_argument("--export-json", action="store_true", help="额外导出临时 JSON")
    parser.add_argument("--skip-db", action="store_true", help="只计算并导出，不回写数据库")
    return parser.parse_args(argv)


def _run_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _default_export_path(run_id: str) -> Path:
    return TMP_ROOT / "runs" / _run_timestamp() / f"{run_id}_backtest-series.json"


def _parse_run_uuid(run_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(run_id)
    except ValueError as error:
        raise ValueError(f"run_id 无效：{run_id}") from error


def as_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip():
        return int(value)
    raise ValueError(f"无法转换为整数：{value!r}")


def as_float(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"不是有效数字：{value!r}")
    return number


def json_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isfinite(number):
        return round(number, 8)
    return 0.0


def _window_from_params(params: dict[str, Any] | None) -> tuple[str, str, str]:
    if not isinstance(params, dict):
        return DEFAULT_WARMUP_START, DEFAULT_VALIDATION_START, DEFAULT_VALIDATION_END
    windows = params.get("windows")
    if isinstance(windows, list):
        latest_window = None
        for item in windows:
            if isinstance(item, dict) and item.get("id") == "latest":
                latest_window = item
                break
        if latest_window is None and windows and isinstance(windows[0], dict):
            latest_window = windows[0]
        if isinstance(latest_window, dict):
            warmup_start = str(latest_window.get("warmup_start") or DEFAULT_WARMUP_START)
            validation_start = str(latest_window.get("validation_start") or DEFAULT_VALIDATION_START)
            validation_end = str(latest_window.get("validation_end") or DEFAULT_VALIDATION_END)
            return warmup_start, validation_start, validation_end
    warmup_start = str(params.get("warmup_start") or DEFAULT_WARMUP_START)
    validation_start = str(params.get("validation_start") or DEFAULT_VALIDATION_START)
    validation_end = str(params.get("validation_end") or DEFAULT_VALIDATION_END)
    return warmup_start, validation_start, validation_end


def _build_run_context(run: FactorMiningRun) -> RunContext:
    params = run.params if isinstance(run.params, dict) else None
    warmup_start, validation_start, validation_end = _window_from_params(params)
    universe = DEFAULT_UNIVERSE
    benchmark = DEFAULT_BENCHMARK
    trading_days_per_year = DEFAULT_TRADING_DAYS_PER_YEAR
    if params is not None:
        universe = str(params.get("universe") or DEFAULT_UNIVERSE)
        benchmark = str(params.get("benchmark") or DEFAULT_BENCHMARK)
        trading_value = params.get("trading_days_per_year")
        if trading_value is not None:
            trading_days_per_year = as_int(trading_value)
    return {
        "run_id": str(run.id),
        "source_tag": str(run.source_tag),
        "universe": universe,
        "benchmark": benchmark,
        "warmup_start": warmup_start,
        "validation_start": validation_start,
        "validation_end": validation_end,
        "trading_days_per_year": trading_days_per_year,
    }


def build_run_context(run: FactorMiningRun) -> RunContext:
    return _build_run_context(run)


def _row_to_candidate(row: FactorMiningCandidate) -> Candidate:
    return {
        "row_key": str(row.row_key),
        "source_id": str(row.source_id),
        "source_label": str(row.source_label),
        "row_index": int(row.row_index),
        "name": str(row.name),
        "expression": str(row.expression),
        "holding_period": int(row.holding_period),
        "n_groups": int(row.n_groups),
        "cost_rate": float(row.cost_rate),
        "neutralize_industry": bool(row.neutralize_industry),
        "neutralize_cap": bool(row.neutralize_cap),
    }


def candidate_from_model(row: FactorMiningCandidate) -> Candidate:
    return _row_to_candidate(row)


async def _load_run_candidates_async(run_id: str | None, source_tag: str | None, limit: int | None) -> tuple[RunContext, list[Candidate]]:
    async with _get_session_factory()() as session:
        run: FactorMiningRun | None = None
        if run_id is not None:
            run = await session.get(FactorMiningRun, _parse_run_uuid(run_id))
        elif source_tag is not None:
            result = await session.execute(
                select(FactorMiningRun)
                .where(FactorMiningRun.source_tag == source_tag)
                .order_by(desc(FactorMiningRun.generated_at), desc(FactorMiningRun.created_at))
                .limit(1)
            )
            run = result.scalar_one_or_none()
        else:
            raise ValueError("必须提供 --run-id 或 --source-tag")

        if run is None:
            raise ValueError("没有找到匹配的因子运行批次")

        query = (
            select(FactorMiningCandidate)
            .where(FactorMiningCandidate.run_id == run.id)
            .order_by(FactorMiningCandidate.row_index.asc(), FactorMiningCandidate.created_at.asc())
        )
        if limit is not None:
            query = query.limit(limit)
        result = await session.execute(query)
        candidates = [_row_to_candidate(item) for item in result.scalars().all()]
        if not candidates:
            raise ValueError(f"运行批次没有候选因子：{run.id}")
        return _build_run_context(run), candidates


def load_run_candidates(run_id: str | None, source_tag: str | None, limit: int | None) -> tuple[RunContext, list[Candidate]]:
    return asyncio.run(_load_run_candidates_async(run_id=run_id, source_tag=source_tag, limit=limit))


def load_market_data(candidates: Sequence[Candidate], run_context: RunContext) -> tuple[pd.DataFrame, list[str]]:
    stock_codes = get_universe(run_context["universe"], date=run_context["validation_end"])
    if len(stock_codes) < 200:
        raise RuntimeError(
            f"股票池缓存不足，日期 {run_context['validation_end']} 只读到 {len(stock_codes)} 只"
        )

    fetcher = MarketDataFetcher()
    market_df = fetcher.fetch_stocks(
        stock_codes,
        run_context["warmup_start"],
        run_context["validation_end"],
    )
    if market_df is None or len(market_df) == 0:
        raise RuntimeError(
            f"没有读到本地行情数据：{run_context['universe']} "
            f"{run_context['warmup_start']} 至 {run_context['validation_end']}"
        )

    all_fundamental_vars: set[str] = set()
    for candidate in candidates:
        all_fundamental_vars |= detect_fundamental_vars(candidate["expression"])
    if all_fundamental_vars:
        market_df = enrich_market_data(
            market_df,
            all_fundamental_vars,
            stock_codes,
            run_context["warmup_start"],
            run_context["validation_end"],
        )

    return market_df, stock_codes


def load_benchmark_returns(run_context: RunContext) -> pd.Series:
    benchmark_returns = fetch_benchmark_returns(
        run_context["benchmark"],
        run_context["validation_start"],
        run_context["validation_end"],
    )
    if benchmark_returns is None or len(benchmark_returns) < 2:
        raise RuntimeError(
            f"没有读到本地基准收益数据：{run_context['benchmark']} "
            f"{run_context['validation_start']} 至 {run_context['validation_end']}"
        )
    benchmark_returns.index = pd.to_datetime(benchmark_returns.index).normalize()
    return benchmark_returns.sort_index()


def cumulative_return(returns: pd.Series) -> pd.Series:
    return (1.0 + returns.fillna(0.0)).cumprod() - 1.0


def max_drawdown(returns: pd.Series) -> float:
    curve = (1.0 + returns.fillna(0.0)).cumprod()
    peak = curve.cummax()
    drawdown = curve / peak - 1.0
    if len(drawdown) == 0:
        return 0.0
    return json_float(drawdown.min())


def annualized_return(returns: pd.Series, trading_days_per_year: int) -> float:
    if len(returns) == 0:
        return 0.0
    total_value = float((1.0 + returns.fillna(0.0)).prod() - 1.0)
    annualized = (1.0 + total_value) ** (trading_days_per_year / len(returns)) - 1.0
    return json_float(annualized)


def sharpe_ratio(returns: pd.Series, trading_days_per_year: int) -> float:
    std = float(returns.std())
    if std <= 0 or not math.isfinite(std):
        return 0.0
    return json_float(float(returns.mean()) / std * math.sqrt(trading_days_per_year))


def period_returns(returns: pd.Series, period: str) -> pd.Series:
    grouped = returns.groupby(returns.index.to_period(period))
    values = grouped.apply(lambda item: float((1.0 + item.fillna(0.0)).prod() - 1.0))
    values.index = values.index.astype(str)
    return values


def build_daily_rows(strategy_returns: pd.Series, benchmark_returns: pd.Series) -> list[dict[str, Any]]:
    strategy_cumulative = cumulative_return(strategy_returns)
    benchmark_cumulative = cumulative_return(benchmark_returns)
    rows: list[dict[str, Any]] = []
    for trade_date in strategy_returns.index:
        strategy_cum = strategy_cumulative.loc[trade_date]
        benchmark_cum = benchmark_cumulative.loc[trade_date]
        rows.append({
            "date": trade_date.strftime("%Y-%m-%d"),
            "strategy_return": json_float(strategy_returns.loc[trade_date]),
            "benchmark_return": json_float(benchmark_returns.loc[trade_date]),
            "strategy_cumulative": json_float(strategy_cum),
            "benchmark_cumulative": json_float(benchmark_cum),
            "excess_cumulative": json_float(strategy_cum - benchmark_cum),
        })
    return rows


def build_period_rows(strategy_returns: pd.Series, benchmark_returns: pd.Series, period: str) -> list[dict[str, Any]]:
    strategy_period = period_returns(strategy_returns, period)
    benchmark_period = period_returns(benchmark_returns, period)
    common_periods = strategy_period.index.intersection(benchmark_period.index)
    rows: list[dict[str, Any]] = []
    for label in common_periods:
        strategy_value = strategy_period.loc[label]
        benchmark_value = benchmark_period.loc[label]
        rows.append({
            "period": label,
            "strategy_return": json_float(strategy_value),
            "benchmark_return": json_float(benchmark_value),
            "excess_return": json_float(strategy_value - benchmark_value),
        })
    return rows


def build_metrics(strategy_returns: pd.Series, benchmark_returns: pd.Series, trading_days_per_year: int) -> dict[str, float]:
    strategy_total = float((1.0 + strategy_returns.fillna(0.0)).prod() - 1.0)
    benchmark_total = float((1.0 + benchmark_returns.fillna(0.0)).prod() - 1.0)
    return {
        "total_return": json_float(strategy_total),
        "benchmark_total_return": json_float(benchmark_total),
        "excess_total_return": json_float(strategy_total - benchmark_total),
        "cagr": annualized_return(strategy_returns, trading_days_per_year),
        "benchmark_cagr": annualized_return(benchmark_returns, trading_days_per_year),
        "sharpe": sharpe_ratio(strategy_returns, trading_days_per_year),
        "max_drawdown": max_drawdown(strategy_returns),
        "win_rate": json_float((strategy_returns > 0).sum() / len(strategy_returns)),
    }


def build_series_payload(
    candidate: Candidate,
    market_df: pd.DataFrame,
    benchmark_returns: pd.Series,
    run_context: RunContext,
) -> dict[str, Any]:
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
        raise RuntimeError(f"策略和基准没有足够重叠日期：{candidate['row_key']}")

    strategy_aligned = aligned["strategy"]
    benchmark_aligned = aligned["benchmark"]
    return {
        "row_key": candidate["row_key"],
        "source_id": candidate["source_id"],
        "source_label": candidate["source_label"],
        "row_index": candidate["row_index"],
        "name": candidate["name"],
        "expression": candidate["expression"],
        "holding_period": candidate["holding_period"],
        "status": "success",
        "metrics": build_metrics(strategy_aligned, benchmark_aligned, run_context["trading_days_per_year"]),
        "daily": build_daily_rows(strategy_aligned, benchmark_aligned),
        "monthly": build_period_rows(strategy_aligned, benchmark_aligned, "M"),
        "yearly": build_period_rows(strategy_aligned, benchmark_aligned, "Y"),
    }


def compute_payload(candidates: Sequence[Candidate], run_context: RunContext) -> dict[str, Any]:
    market_df, stock_codes = load_market_data(candidates, run_context)
    benchmark_returns = load_benchmark_returns(run_context)
    items: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    computed: dict[str, dict[str, Any]] = {}

    for index, candidate in enumerate(candidates, start=1):
        print(f"[{index}/{len(candidates)}] {candidate['row_key']} {candidate['name']}", flush=True)
        compute_key = json.dumps(
            {
                "expression": candidate["expression"],
                "holding_period": candidate["holding_period"],
                "n_groups": candidate["n_groups"],
                "cost_rate": candidate["cost_rate"],
                "neutralize_industry": candidate["neutralize_industry"],
                "neutralize_cap": candidate["neutralize_cap"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        try:
            if compute_key not in computed:
                computed[compute_key] = build_series_payload(candidate, market_df, benchmark_returns, run_context)
            payload = dict(computed[compute_key])
            payload.update({
                "row_key": candidate["row_key"],
                "source_id": candidate["source_id"],
                "source_label": candidate["source_label"],
                "row_index": candidate["row_index"],
                "name": candidate["name"],
            })
            items[candidate["row_key"]] = payload
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            items[candidate["row_key"]] = {
                "row_key": candidate["row_key"],
                "source_id": candidate["source_id"],
                "source_label": candidate["source_label"],
                "row_index": candidate["row_index"],
                "name": candidate["name"],
                "expression": candidate["expression"],
                "holding_period": candidate["holding_period"],
                "status": "failed",
                "error_message": message,
                "metrics": None,
                "daily": [],
                "monthly": [],
                "yearly": [],
            }
            errors.append({"row_key": candidate["row_key"], "name": candidate["name"], "message": message})
            print(f"生成失败：{candidate['row_key']} {message}", file=sys.stderr, flush=True)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": {
            "run_id": run_context["run_id"],
            "source_tag": run_context["source_tag"],
            "universe": run_context["universe"],
            "benchmark": run_context["benchmark"],
            "warmup_start": run_context["warmup_start"],
            "validation_start": run_context["validation_start"],
            "validation_end": run_context["validation_end"],
            "trading_days_per_year": run_context["trading_days_per_year"],
            "stock_count": len(stock_codes),
            "candidate_count": len(candidates),
            "success_count": len([item for item in items.values() if item["status"] == "success"]),
            "error_count": len(errors),
        },
        "items": items,
        "errors": errors,
    }


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    run_context, candidates = load_run_candidates(run_id=args.run_id, source_tag=args.source_tag, limit=args.limit)
    output_path = args.output
    should_export = bool(args.export_json or output_path is not None)
    try:
        payload = compute_payload(candidates, run_context)
        if should_export:
            if output_path is None:
                output_path = _default_export_path(run_context["run_id"])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
                file.write("\n")
            print(f"已导出：{output_path}", flush=True)
        if not args.skip_db:
            result = save_backtest_series(
                run_id=run_context["run_id"],
                items=payload["items"],
                errors=payload["errors"],
            )
            print(
                f"run_id={run_context['run_id']} success={result['success_count']} "
                f"failed={result['fail_count']} errors={result['error_count']}",
                flush=True,
            )
    except Exception as error:
        if not args.skip_db:
            complete_run(run_id=run_context["run_id"], status="failed", error_message=str(error))
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
