"""SQLAlchemy ORM models for QuantGPT."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=True)  # bcrypt, NULL=未设置密码
    nickname = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    subscribe_weekly = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_login_at = Column(DateTime(timezone=True), nullable=True)

    sessions = relationship("Session", back_populates="user", lazy="selectin")
    tasks = relationship("Task", back_populates="user", lazy="selectin")
    reports = relationship("Report", back_populates="user", lazy="selectin")


class VerificationCode(Base):
    __tablename__ = "verification_codes"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    email = Column(String(255), nullable=False, index=True)
    code = Column(String(6), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    used = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_verification_codes_email_used", "email", "used"),
    )


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(200), nullable=True)
    market = Column(String(20), default="a_share", nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    user = relationship("User", back_populates="sessions")
    tasks = relationship("Task", back_populates="session", lazy="selectin")


class Task(Base):
    __tablename__ = "tasks"

    id = Column(String(12), primary_key=True)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    session_id = Column(Uuid, ForeignKey("sessions.id"), nullable=True, index=True)
    status = Column(String(30), nullable=False, default="pending")
    task_type = Column(String(50), nullable=True, default="backtest")
    parent_task_id = Column(String(12), ForeignKey("tasks.id"), nullable=True)
    params = Column(JSON, nullable=True)
    expression = Column(Text, nullable=True)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    user = relationship("User", back_populates="tasks")
    session = relationship("Session", back_populates="tasks")
    reports = relationship("Report", back_populates="task", lazy="selectin")


class Report(Base):
    __tablename__ = "reports"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    task_id = Column(String(12), ForeignKey("tasks.id"), nullable=False)
    filename = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    user = relationship("User", back_populates="reports")
    task = relationship("Task", back_populates="reports")


class SavedFactor(Base):
    __tablename__ = "saved_factors"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    task_id = Column(String(12), ForeignKey("tasks.id"), nullable=True)
    expression = Column(Text, nullable=False)
    name = Column(String(200), nullable=True)       # 用户自定义名称
    note = Column(Text, nullable=True)              # 备注
    tags = Column(JSON, nullable=True)              # 标签列表
    metrics = Column(JSON, nullable=True)           # 快照：report_metrics
    backtest_summary = Column(JSON, nullable=True)  # 快照：backtest_summary
    params = Column(JSON, nullable=True)            # 回测参数
    report_url = Column(String(500), nullable=True)
    market = Column(String(20), default="a_share", nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    user = relationship("User")


class Feedback(Base):
    __tablename__ = "feedbacks"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    description = Column(Text, nullable=False)
    screenshot_path = Column(String(500), nullable=True)
    task_id = Column(String(12), nullable=True)
    user_agent = Column(String(500), nullable=True)
    page_url = Column(String(500), nullable=True)
    webhook_sent = Column(Boolean, default=False, nullable=False)
    resolved = Column(Boolean, default=False, nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    user = relationship("User")


class SubmittedAlpha(Base):
    __tablename__ = "submitted_alphas"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    alpha_id = Column(String(50), nullable=False, index=True)
    expression = Column(Text, nullable=False)
    expression_normalized = Column(Text, nullable=True)
    region = Column(String(10), nullable=False, default="USA")
    universe = Column(String(20), nullable=False, default="TOP3000")
    delay = Column(Integer, nullable=False, default=1)
    decay = Column(Integer, nullable=False, default=0)
    neutralization = Column(String(30), nullable=False, default="SUBINDUSTRY")
    truncation = Column(Float, nullable=False, default=0.08)
    tag = Column(String(100), nullable=True)
    sharpe = Column(Float, nullable=True)
    fitness = Column(Float, nullable=True)
    returns = Column(Float, nullable=True)
    turnover = Column(Float, nullable=True)
    status = Column(String(20), nullable=False, default="submitted")
    submitted_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    user = relationship("User")

    __table_args__ = (
        Index("ix_submitted_alphas_user_expr", "user_id", "expression_normalized"),
    )


class DailySummary(Base):
    __tablename__ = "daily_summaries"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    date = Column(String(10), nullable=False)          # "2026-03-24"
    market = Column(String(20), default="a_share", nullable=False)
    title = Column(String(200), nullable=True)
    content = Column(Text, nullable=True)              # markdown
    metrics = Column(JSON, nullable=True)              # index changes, volume, etc.
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_daily_summaries_date_market", "date", "market", unique=True),
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    key_hash = Column(String(255), nullable=False, unique=True)
    prefix = Column(String(10), nullable=False)
    name = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    user = relationship("User")


class FactorMiningRun(Base):
    __tablename__ = "factor_mining_runs"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    source_tag = Column(String(120), nullable=False)
    status = Column(String(20), default="pending", nullable=False)
    params = Column(JSON, nullable=True)
    source_summary = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    generated_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class FactorMiningCandidate(Base):
    __tablename__ = "factor_mining_candidates"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id = Column(Uuid, ForeignKey("factor_mining_runs.id"), nullable=False, index=True)
    row_key = Column(String(200), nullable=False)
    source_id = Column(String(80), nullable=False)
    source_label = Column(String(160), nullable=False)
    row_index = Column(Integer, nullable=False, default=0)
    name = Column(String(200), nullable=False)
    expression = Column(Text, nullable=False)
    holding_period = Column(Integer, nullable=False)
    n_groups = Column(Integer, nullable=False, default=5)
    cost_rate = Column(Float, nullable=False, default=0.003)
    neutralize_industry = Column(Boolean, nullable=False, default=True)
    neutralize_cap = Column(Boolean, nullable=False, default=True)
    status = Column(String(20), default="success", nullable=False)
    score = Column(Float, nullable=True)
    grade = Column(String(2), nullable=True)
    latest_score = Column(Float, nullable=True)
    history_score = Column(Float, nullable=True)
    latest_ic_mean = Column(Float, nullable=True)
    latest_ic_ir = Column(Float, nullable=True)
    latest_ic_win_rate = Column(Float, nullable=True)
    latest_monotonicity = Column(Float, nullable=True)
    latest_sharpe = Column(Float, nullable=True)
    latest_top_group_sharpe = Column(Float, nullable=True)
    latest_long_short_sharpe = Column(Float, nullable=True)
    latest_turnover = Column(Float, nullable=True)
    latest_cagr = Column(Float, nullable=True)
    latest_max_drawdown = Column(Float, nullable=True)
    latest_strategy_max_drawdown = Column(Float, nullable=True)
    latest_total_return = Column(Float, nullable=True)
    latest_benchmark_total_return = Column(Float, nullable=True)
    latest_excess_total_return = Column(Float, nullable=True)
    latest_flipped = Column(Boolean, nullable=True)
    history_score_raw = Column(Float, nullable=True)
    history_ic_mean = Column(Float, nullable=True)
    history_ic_ir = Column(Float, nullable=True)
    history_ic_win_rate = Column(Float, nullable=True)
    history_monotonicity = Column(Float, nullable=True)
    history_sharpe = Column(Float, nullable=True)
    history_top_group_sharpe = Column(Float, nullable=True)
    history_long_short_sharpe = Column(Float, nullable=True)
    history_turnover = Column(Float, nullable=True)
    history_cagr = Column(Float, nullable=True)
    history_max_drawdown = Column(Float, nullable=True)
    history_strategy_max_drawdown = Column(Float, nullable=True)
    history_total_return = Column(Float, nullable=True)
    history_benchmark_total_return = Column(Float, nullable=True)
    history_excess_total_return = Column(Float, nullable=True)
    history_flipped = Column(Boolean, nullable=True)
    stability_score = Column(Float, nullable=True)
    market_fit = Column(Text, nullable=True)
    failure_modes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        Index("uq_factor_mining_candidates_run_row_key", "run_id", "row_key", unique=True),
    )

    run = relationship("FactorMiningRun")


class FactorMiningBacktestSeries(Base):
    __tablename__ = "factor_mining_backtest_series"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id = Column(Uuid, ForeignKey("factor_mining_runs.id"), nullable=False, index=True)
    candidate_id = Column(Uuid, ForeignKey("factor_mining_candidates.id"), nullable=False, index=True)
    row_key = Column(String(200), nullable=False)
    source_id = Column(String(80), nullable=False)
    source_label = Column(String(160), nullable=False)
    row_index = Column(Integer, nullable=False, default=0)
    name = Column(String(200), nullable=False)
    expression = Column(Text, nullable=False)
    holding_period = Column(Integer, nullable=False)
    status = Column(String(20), default="success", nullable=False)
    error_message = Column(Text, nullable=True)
    metrics = Column(JSON, nullable=True)
    daily = Column(JSON, nullable=True)
    monthly = Column(JSON, nullable=True)
    yearly = Column(JSON, nullable=True)
    generated_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        Index("uq_factor_mining_backtest_series_run_row_key", "run_id", "row_key", unique=True),
    )

    run = relationship("FactorMiningRun")
    candidate = relationship("FactorMiningCandidate")
