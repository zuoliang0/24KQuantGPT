import asyncio
import uuid
from pathlib import Path
from typing import Any, Iterator

import pandas as pd
import pytest
from sqlalchemy import select

import quantgpt.factor_mining_service as service
from quantgpt.db import _get_session_factory, close_db, init_db, reset_db_state
from quantgpt.models import FactorMiningBacktestSeries, FactorMiningCandidate


def _latest_window() -> dict[str, str]:
    return {
        "label": "latest",
        "warmup_start": "2024-01-01",
        "validation_start": "2024-02-01",
        "validation_end": "2024-04-30",
    }


def _history_window() -> dict[str, str]:
    return {
        "label": "history",
        "warmup_start": "2024-01-01",
        "validation_start": "2024-01-15",
        "validation_end": "2024-03-29",
    }


def _candidate(row_key: str, name: str, expression: str, row_index: int) -> dict[str, Any]:
    return {
        "row_key": row_key,
        "name": name,
        "expression": expression,
        "holding_period": 5,
        "n_groups": 3,
        "cost_rate": 0.0,
        "neutralize_industry": False,
        "neutralize_cap": False,
        "source_id": "pytest",
        "source_label": "pytest",
        "row_index": row_index,
    }


def _market_data() -> tuple[pd.DataFrame, list[str]]:
    dates = pd.bdate_range("2024-01-01", "2024-04-30")
    stock_codes = [f"sh.60000{index}" for index in range(6)]
    rows: list[dict[str, Any]] = []
    for stock_index, stock_code in enumerate(stock_codes):
        price = 10.0 + stock_index
        for date_index, trade_date in enumerate(dates):
            drift = 0.0008 + stock_index * 0.00025
            wave = 0.002 * ((date_index + stock_index) % 5 - 2)
            close = price * (1.0 + drift + wave)
            open_price = close * (1.0 - 0.001)
            high = close * 1.01
            low = close * 0.99
            volume = 1_000_000.0 + stock_index * 50_000.0 + date_index * 1000.0
            rows.append({
                "trade_date": trade_date,
                "stock_code": stock_code,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "amount": close * volume,
                "pct_change": 0.0,
            })
            price = close
    return pd.DataFrame(rows), stock_codes


@pytest.fixture()
def factor_mining_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    db_path = tmp_path / "factor_mining.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    reset_db_state()
    asyncio.run(init_db())
    yield
    asyncio.run(close_db())
    reset_db_state()


@pytest.fixture()
def synthetic_market(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    market_df, stock_codes = _market_data()

    def _fake_load_market_data(
        candidates: list[service.Candidate],
        run_context: service.RunContext,
    ) -> tuple[pd.DataFrame, list[str]]:
        return market_df.copy(), list(stock_codes)

    def _fake_load_benchmark_returns(run_context: service.RunContext) -> pd.Series:
        dates = pd.bdate_range(run_context["validation_start"], run_context["validation_end"])
        return pd.Series(0.0004, index=dates)

    monkeypatch.setattr(service, "load_market_data", _fake_load_market_data)
    monkeypatch.setattr(service, "load_benchmark_returns", _fake_load_benchmark_returns)
    yield


def test_validate_factor_mining_batch_rejects_bad_input() -> None:
    candidates = [
        _candidate("dup", "价格强度", "rank(close)", 1),
        _candidate("dup", "量能强度", "rank(volume)", 2),
    ]
    with pytest.raises(ValueError, match="row_key 重复"):
        service.validate_factor_mining_batch_request(
            universe="small_scale",
            benchmark="hs300",
            latest_window=_latest_window(),
            history_window=_history_window(),
            candidates=candidates,
        )

    bad_candidates = [_candidate("bad", "坏表达式", "rank(close", 1)]
    with pytest.raises(ValueError, match="括号不平衡"):
        service.validate_factor_mining_batch_request(
            universe="small_scale",
            benchmark="hs300",
            latest_window=_latest_window(),
            history_window=_history_window(),
            candidates=bad_candidates,
        )


def test_sync_factor_mining_batch_writes_candidates_and_series(
    factor_mining_db: None,
    synthetic_market: None,
) -> None:
    result = service.run_factor_mining_batch_sync(
        source_tag="pytest_sync",
        source_summary="同步 smoke",
        universe="small_scale",
        benchmark="hs300",
        latest_window=_latest_window(),
        history_window=_history_window(),
        candidates=[
            _candidate("close_rank", "价格强度", "rank(close)", 1),
            _candidate("volume_rank", "量能强度", "rank(volume)", 2),
        ],
    )

    assert result["status"] == "completed"
    snapshot = service.get_factor_mining_run_snapshot(result["run_id"])
    assert snapshot["run"]["status"] == "completed"
    assert snapshot["candidate_count"] == 2
    first = snapshot["top_candidates"][0]
    assert first["latest"]["ic_ir"] is not None
    assert first["history"]["ic_ir"] is not None

    async def _read_rows() -> tuple[int, int, dict[str, Any]]:
        reset_db_state()
        try:
            async with _get_session_factory()() as session:
                run_uuid = uuid.UUID(result["run_id"])
                candidate_rows = await session.execute(
                    select(FactorMiningCandidate).where(FactorMiningCandidate.run_id == run_uuid)
                )
                series_rows = await session.execute(
                    select(FactorMiningBacktestSeries).where(FactorMiningBacktestSeries.run_id == run_uuid)
                )
                candidates = candidate_rows.scalars().all()
                series = series_rows.scalars().all()
                return len(candidates), len(series), dict(series[0].metrics)
        finally:
            await close_db()

    candidate_count, series_count, metrics = asyncio.run(_read_rows())
    assert candidate_count == 2
    assert series_count == 2
    assert metrics["ic_ir"] is not None
    assert metrics["ic_win_rate"] is not None
    assert metrics["turnover"] is not None


def test_async_submission_job_and_append_keep_existing_rows(
    factor_mining_db: None,
    synthetic_market: None,
) -> None:
    submitted, job = service.submit_factor_mining_batch_async(
        source_tag="pytest_async",
        source_summary="异步 smoke",
        universe="small_scale",
        benchmark="hs300",
        latest_window=_latest_window(),
        history_window=_history_window(),
        candidates=[_candidate("close_rank", "价格强度", "rank(close)", 1)],
    )
    assert submitted["status"] == "running"
    pending_snapshot = service.get_factor_mining_run_snapshot(submitted["run_id"])
    assert pending_snapshot["run"]["status"] == "running"

    completed = service.run_factor_mining_job(job)
    assert completed["status"] == "completed"

    appended = service.append_factor_mining_candidates_sync(
        run_id=submitted["run_id"],
        candidates=[_candidate("volume_rank", "量能强度", "rank(volume)", 2)],
    )
    assert appended["status"] == "completed"
    snapshot = service.get_factor_mining_run_snapshot(submitted["run_id"])
    assert snapshot["candidate_count"] == 2
    assert {item["row_key"] for item in snapshot["top_candidates"]} == {"close_rank", "volume_rank"}
