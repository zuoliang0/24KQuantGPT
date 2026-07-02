"""新增因子挖掘看板数据表"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "factor_mining_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_tag", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("source_summary", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "factor_mining_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("factor_mining_runs.id"), nullable=False),
        sa.Column("row_key", sa.String(200), nullable=False),
        sa.Column("source_id", sa.String(80), nullable=False),
        sa.Column("source_label", sa.String(160), nullable=False),
        sa.Column("row_index", sa.Integer, nullable=False, server_default="0"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("expression", sa.Text(), nullable=False),
        sa.Column("holding_period", sa.Integer, nullable=False),
        sa.Column("n_groups", sa.Integer, nullable=False, server_default="5"),
        sa.Column("cost_rate", sa.Float, nullable=False, server_default="0.003"),
        sa.Column("neutralize_industry", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("neutralize_cap", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("status", sa.String(20), server_default="success", nullable=False),
        sa.Column("score", sa.Float, nullable=True),
        sa.Column("grade", sa.String(2), nullable=True),
        sa.Column("latest_score", sa.Float, nullable=True),
        sa.Column("history_score", sa.Float, nullable=True),
        sa.Column("latest_ic_mean", sa.Float, nullable=True),
        sa.Column("latest_ic_ir", sa.Float, nullable=True),
        sa.Column("latest_ic_win_rate", sa.Float, nullable=True),
        sa.Column("latest_monotonicity", sa.Float, nullable=True),
        sa.Column("latest_sharpe", sa.Float, nullable=True),
        sa.Column("latest_top_group_sharpe", sa.Float, nullable=True),
        sa.Column("latest_long_short_sharpe", sa.Float, nullable=True),
        sa.Column("latest_turnover", sa.Float, nullable=True),
        sa.Column("latest_cagr", sa.Float, nullable=True),
        sa.Column("latest_max_drawdown", sa.Float, nullable=True),
        sa.Column("latest_strategy_max_drawdown", sa.Float, nullable=True),
        sa.Column("latest_total_return", sa.Float, nullable=True),
        sa.Column("latest_benchmark_total_return", sa.Float, nullable=True),
        sa.Column("latest_excess_total_return", sa.Float, nullable=True),
        sa.Column("latest_flipped", sa.Boolean, nullable=True),
        sa.Column("history_score_raw", sa.Float, nullable=True),
        sa.Column("history_ic_mean", sa.Float, nullable=True),
        sa.Column("history_ic_ir", sa.Float, nullable=True),
        sa.Column("history_ic_win_rate", sa.Float, nullable=True),
        sa.Column("history_monotonicity", sa.Float, nullable=True),
        sa.Column("history_sharpe", sa.Float, nullable=True),
        sa.Column("history_top_group_sharpe", sa.Float, nullable=True),
        sa.Column("history_long_short_sharpe", sa.Float, nullable=True),
        sa.Column("history_turnover", sa.Float, nullable=True),
        sa.Column("history_cagr", sa.Float, nullable=True),
        sa.Column("history_max_drawdown", sa.Float, nullable=True),
        sa.Column("history_strategy_max_drawdown", sa.Float, nullable=True),
        sa.Column("history_total_return", sa.Float, nullable=True),
        sa.Column("history_benchmark_total_return", sa.Float, nullable=True),
        sa.Column("history_excess_total_return", sa.Float, nullable=True),
        sa.Column("history_flipped", sa.Boolean, nullable=True),
        sa.Column("stability_score", sa.Float, nullable=True),
        sa.Column("market_fit", sa.Text(), nullable=True),
        sa.Column("failure_modes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_factor_mining_candidates_run_id", "factor_mining_candidates", ["run_id"])
    op.create_unique_constraint("uq_factor_mining_candidates_run_row_key", "factor_mining_candidates", ["run_id", "row_key"])

    op.create_table(
        "factor_mining_backtest_series",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("factor_mining_runs.id"), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), sa.ForeignKey("factor_mining_candidates.id"), nullable=False),
        sa.Column("row_key", sa.String(200), nullable=False),
        sa.Column("source_id", sa.String(80), nullable=False),
        sa.Column("source_label", sa.String(160), nullable=False),
        sa.Column("row_index", sa.Integer, nullable=False, server_default="0"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("expression", sa.Text(), nullable=False),
        sa.Column("holding_period", sa.Integer, nullable=False),
        sa.Column("status", sa.String(20), server_default="success", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("daily", sa.JSON(), nullable=True),
        sa.Column("monthly", sa.JSON(), nullable=True),
        sa.Column("yearly", sa.JSON(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_factor_mining_backtest_series_run_id", "factor_mining_backtest_series", ["run_id"])
    op.create_index("ix_factor_mining_backtest_series_candidate_id", "factor_mining_backtest_series", ["candidate_id"])
    op.create_unique_constraint("uq_factor_mining_backtest_series_run_row_key", "factor_mining_backtest_series", ["run_id", "row_key"])


def downgrade() -> None:
    op.drop_index("ix_factor_mining_backtest_series_candidate_id", table_name="factor_mining_backtest_series")
    op.drop_index("ix_factor_mining_backtest_series_run_id", table_name="factor_mining_backtest_series")
    op.drop_constraint("uq_factor_mining_backtest_series_run_row_key", "factor_mining_backtest_series", type_="unique")
    op.drop_table("factor_mining_backtest_series")
    op.drop_index("ix_factor_mining_candidates_run_id", table_name="factor_mining_candidates")
    op.drop_constraint("uq_factor_mining_candidates_run_row_key", "factor_mining_candidates", type_="unique")
    op.drop_table("factor_mining_candidates")
    op.drop_table("factor_mining_runs")
