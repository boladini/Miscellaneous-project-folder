"""Database model. Money/quantities are Numeric (Decimal). Timestamps are timezone-aware UTC.

Immutable records (approvals, strategy versions, ledger entries, fills) are never updated after
insert except for explicit lifecycle columns on orders/lots/experiments; derived holdings and NAV
reconcile to the ledger (see services/valuation.reconcile_portfolio).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

MONEY = Numeric(20, 6)
QTY = Numeric(20, 6)
PRICE = Numeric(20, 6)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


def ts():
    return mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="America/Chicago")
    created_at: Mapped[datetime] = ts()


class UserSettings(Base):
    __tablename__ = "user_settings"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = ts()
    created_at: Mapped[datetime] = ts()


class RiskPolicy(Base):
    __tablename__ = "risk_policies"
    __table_args__ = (UniqueConstraint("user_id", "version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    limits: Mapped[dict] = mapped_column(JSON)
    effective_at: Mapped[datetime] = ts()
    created_at: Mapped[datetime] = ts()


class Portfolio(Base):
    __tablename__ = "portfolios"
    __table_args__ = (UniqueConstraint("user_id", "family", "generation"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    family: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(120))
    generation: Mapped[int] = mapped_column(Integer, default=1)
    inception_at: Mapped[datetime] = ts()
    starting_cash: Mapped[Decimal] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|paused|archived
    paused_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    circuit_breaker: Mapped[bool] = mapped_column(Boolean, default=False)
    peak_nav: Mapped[Decimal] = mapped_column(MONEY)
    policy_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = ts()


class BenchmarkAccount(Base):
    __tablename__ = "benchmark_accounts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolios.id"), unique=True)
    symbol: Mapped[str] = mapped_column(String(16))
    starting_cash: Mapped[Decimal] = mapped_column(MONEY)
    inception_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cash: Mapped[Decimal] = mapped_column(MONEY)
    shares: Mapped[Decimal] = mapped_column(Numeric(24, 10), default=Decimal(0))
    entry_session: Mapped[date | None] = mapped_column(Date, nullable=True)
    entry_price: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    basis: Mapped[str] = mapped_column(String(24), default="price_only")
    created_at: Mapped[datetime] = ts()


class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = (UniqueConstraint("namespace", "symbol"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    namespace: Mapped[str] = mapped_column(String(8))  # demo|live
    symbol: Mapped[str] = mapped_column(String(16))
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(16), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    type: Mapped[str] = mapped_column(String(8), default="stock")  # stock|etf
    sector: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|halted|delisted
    active_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    active_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = ts()


class MarketBar(Base):
    __tablename__ = "market_bars"
    __table_args__ = (UniqueConstraint("instrument_id", "session"), Index("ix_bar_session", "session"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), index=True)
    session: Mapped[date] = mapped_column(Date)
    open: Mapped[Decimal] = mapped_column(PRICE)
    high: Mapped[Decimal] = mapped_column(PRICE)
    low: Mapped[Decimal] = mapped_column(PRICE)
    close: Mapped[Decimal] = mapped_column(PRICE)
    volume: Mapped[Decimal] = mapped_column(Numeric(24, 2))
    provider: Mapped[str] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # exchange close timestamp
    received_at: Mapped[datetime] = ts()
    adjustment_basis: Mapped[str] = mapped_column(String(24), default="raw")
    quality: Mapped[str] = mapped_column(String(24), default="ok")
    currency: Mapped[str] = mapped_column(String(3), default="USD")


class CorporateAction(Base):
    __tablename__ = "corporate_actions"
    __table_args__ = (UniqueConstraint("instrument_id", "type", "ex_date"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), index=True)
    type: Mapped[str] = mapped_column(String(24))  # split|dividend|delisting|symbol_change|merger
    ex_date: Mapped[date] = mapped_column(Date)
    pay_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    known_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(64))
    needs_manual_reconciliation: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = ts()


class MarketEvent(Base):
    __tablename__ = "market_events"
    __table_args__ = (UniqueConstraint("instrument_id", "type", "event_date"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), index=True)
    type: Mapped[str] = mapped_column(String(24))  # earnings
    event_date: Mapped[date] = mapped_column(Date)
    known_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = ts()


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("user_id", "schedule_key"), Index("ix_jobs_state_run", "state", "run_after"))
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    type: Mapped[str] = mapped_column(String(32))  # research|daily|monthly|market_refresh|backtest|notify
    schedule_key: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|succeeded|failed|dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = ts()
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    progress: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger: Mapped[str] = mapped_column(String(16), default="schedule")  # schedule|manual|catchup
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = ts()


class ResearchRun(Base):
    __tablename__ = "research_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="running")
    mode: Mapped[str] = mapped_column(String(8))  # demo|live
    llm_provider: Mapped[str] = mapped_column(String(32))
    llm_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(32))
    queries: Mapped[list] = mapped_column(JSON, default=list)
    coverage: Mapped[dict] = mapped_column(JSON, default=dict)
    briefing: Mapped[dict] = mapped_column(JSON, default=dict)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = ts()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = ts()


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("user_id", "url"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    authors: Mapped[str | None] = mapped_column(Text, nullable=True)
    publisher: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    retrieved_at: Mapped[datetime] = ts()
    source_type: Mapped[str] = mapped_column(String(32))  # peer_reviewed|preprint|filing|investor_letter|news|social|synthetic
    provider: Mapped[str] = mapped_column(String(32))
    retrieval_id: Mapped[str] = mapped_column(String(200))
    url_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    cited_by_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = ts()


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    research_run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id"), index=True)
    topic: Mapped[str] = mapped_column(String(64))
    stance: Mapped[str] = mapped_column(String(16))  # supporting|disconfirming|background
    claim: Mapped[str] = mapped_column(Text)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    limitations: Mapped[str] = mapped_column(Text)
    quality: Mapped[float] = mapped_column(Float)  # 0..1
    quality_label: Mapped[str] = mapped_column(String(16))  # high|medium|low
    claim_supported: Mapped[bool] = mapped_column(Boolean, default=False)
    is_new: Mapped[bool] = mapped_column(Boolean, default=True)
    extraction_method: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = ts()


class Strategy(Base):
    __tablename__ = "strategies"
    __table_args__ = (UniqueConstraint("user_id", "fingerprint"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    family: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    fingerprint: Mapped[str] = mapped_column(String(64))
    thesis_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = ts()


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (UniqueConstraint("strategy_id", "version"), UniqueConstraint("strategy_id", "rule_hash"))
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    rule_json: Mapped[dict] = mapped_column(JSON)
    rule_hash: Mapped[str] = mapped_column(String(64))
    thesis: Mapped[str] = mapped_column(Text)
    hypothesis: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    parent_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    diff: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    generated_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = ts()


class Critique(Base):
    __tablename__ = "critiques"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    strategy_version_id: Mapped[str] = mapped_column(ForeignKey("strategy_versions.id"), index=True)
    research_run_id: Mapped[str | None] = mapped_column(ForeignKey("research_runs.id"), nullable=True)
    items: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text)
    max_severity: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = ts()


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    strategy_version_id: Mapped[str] = mapped_column(ForeignKey("strategy_versions.id"), index=True)
    rule_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))  # completed|not_backtestable|failed
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    dataset_manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    code_version: Mapped[str] = mapped_column(String(64))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    series: Mapped[dict] = mapped_column(JSON, default=dict)
    limitations: Mapped[list] = mapped_column(JSON, default=list)
    trigger: Mapped[str] = mapped_column(String(16), default="research")
    artifact_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = ts()


class Proposal(Base):
    __tablename__ = "proposals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    strategy_version_id: Mapped[str] = mapped_column(ForeignKey("strategy_versions.id"), index=True)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolios.id"), index=True)
    research_run_id: Mapped[str | None] = mapped_column(ForeignKey("research_runs.id"), nullable=True)
    backtest_run_id: Mapped[str | None] = mapped_column(ForeignKey("backtest_runs.id"), nullable=True)
    critique_id: Mapped[str | None] = mapped_column(ForeignKey("critiques.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="new")  # new|modification|pause|retire|continue
    parent_experiment_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    legacy_treatment: Mapped[str | None] = mapped_column(String(32), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    component_scores: Mapped[dict] = mapped_column(JSON, default=dict)
    gates: Mapped[dict] = mapped_column(JSON, default=dict)
    rationale: Mapped[dict] = mapped_column(JSON, default=dict)
    uncertainty: Mapped[dict] = mapped_column(JSON, default=dict)
    card: Mapped[dict] = mapped_column(JSON, default=dict)
    budget: Mapped[Decimal] = mapped_column(MONEY)
    scope: Mapped[dict] = mapped_column(JSON)
    scope_hash: Mapped[str] = mapped_column(String(64))
    exploratory: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String(16), default="pending")  # pending|approved|rejected|expired|blocked|withdrawn
    state_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = ts()


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("proposals.id"), unique=True)
    decision: Mapped[str] = mapped_column(String(8))  # yes|no
    actor_id: Mapped[str] = mapped_column(String(32))
    decided_at: Mapped[datetime] = ts()
    scope_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = ts()


class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolios.id"), index=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("proposals.id"), unique=True)
    approved_version_id: Mapped[str] = mapped_column(ForeignKey("strategy_versions.id"))
    name: Mapped[str] = mapped_column(String(200))
    budget: Mapped[Decimal] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|paused|closing|closed|completed|legacy
    entries_allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    thesis_status: Mapped[str] = mapped_column(String(16), default="untested")
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    inception_at: Mapped[datetime] = ts()
    ends_by: Mapped[date | None] = mapped_column(Date, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    successor_of: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = ts()


class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"), index=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    session: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(8))  # entry|exit
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rules_evaluated: Mapped[dict] = mapped_column(JSON)
    result: Mapped[bool] = mapped_column(Boolean)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = ts()


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolios.id"), index=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"), index=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    lot_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    side: Mapped[str] = mapped_column(String(4))  # buy|sell
    quantity: Mapped[Decimal] = mapped_column(QTY)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|filled|expired|cancelled|blocked|failed
    reason: Mapped[str] = mapped_column(Text)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    signal_session: Mapped[date] = mapped_column(Date)
    eligible_session: Mapped[date] = mapped_column(Date)
    expires_after_session: Mapped[date] = mapped_column(Date)
    reserved_cash: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    reference_price: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    slippage_bps: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    fill_price: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    fill_session: Mapped[date | None] = mapped_column(Date, nullable=True)
    fill_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # simulated
    bar_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recorded_late: Mapped[bool] = mapped_column(Boolean, default=False)
    fees: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    slippage_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    gross_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    requested_at: Mapped[datetime] = ts()
    created_at: Mapped[datetime] = ts()


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolios.id"), index=True)
    experiment_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    instrument_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lot_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    event_type: Mapped[str] = mapped_column(String(24))  # initial_cash|buy|sell|dividend|split|fee|archive
    amount: Mapped[Decimal] = mapped_column(MONEY)  # cash delta (signed)
    quantity_delta: Mapped[Decimal] = mapped_column(QTY, default=Decimal(0))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    session: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = ts()


class PositionLot(Base):
    __tablename__ = "position_lots"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolios.id"), index=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"), index=True)
    strategy_version_id: Mapped[str] = mapped_column(String(32))
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    open_order_id: Mapped[str] = mapped_column(String(32), unique=True)
    quantity: Mapped[Decimal] = mapped_column(QTY)
    original_quantity: Mapped[Decimal] = mapped_column(QTY)
    cost_basis: Mapped[Decimal] = mapped_column(MONEY)  # remaining basis (incl. costs)
    entry_price: Mapped[Decimal] = mapped_column(PRICE)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    opened_session: Mapped[date] = mapped_column(Date)
    peak_close: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|closing|closed|unresolved
    realized_pnl: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    dividends: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_session: Mapped[date | None] = mapped_column(Date, nullable=True)
    exit_price: Mapped[Decimal | None] = mapped_column(PRICE, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = ts()


class ValuationSnapshot(Base):
    __tablename__ = "valuation_snapshots"
    __table_args__ = (UniqueConstraint("portfolio_id", "session"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolios.id"), index=True)
    session: Mapped[date] = mapped_column(Date)
    cash: Mapped[Decimal] = mapped_column(MONEY)
    reserved_cash: Mapped[Decimal] = mapped_column(MONEY)
    holdings_value: Mapped[Decimal] = mapped_column(MONEY)
    nav: Mapped[Decimal] = mapped_column(MONEY)
    benchmark_nav: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    peak_nav: Mapped[Decimal] = mapped_column(MONEY)
    drawdown: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    quality: Mapped[str] = mapped_column(String(24), default="ok")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = ts()


class ThesisReview(Base):
    __tablename__ = "thesis_reviews"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"), index=True)
    session: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(16), default="daily")  # daily|monthly|closing
    status: Mapped[str] = mapped_column(String(16))
    summary: Mapped[str] = mapped_column(Text)
    observations: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_links: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[str] = mapped_column(String(16), default="low")
    counterargument: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str] = mapped_column(String(32), default="deterministic")
    created_at: Mapped[datetime] = ts()
    __table_args__ = (UniqueConstraint("experiment_id", "session", "kind"),)


class Lesson(Base):
    __tablename__ = "lessons"
    __table_args__ = (UniqueConstraint("experiment_id", "period"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"), index=True)
    period: Mapped[str] = mapped_column(String(16))
    outcome: Mapped[str] = mapped_column(String(24))
    recommendation: Mapped[str] = mapped_column(String(16))  # continue|pause|retire|modify
    lesson: Mapped[str] = mapped_column(Text)
    competing_explanation: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(16))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_links: Mapped[list] = mapped_column(JSON, default=list)
    proposal_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider: Mapped[str] = mapped_column(String(32), default="deterministic")
    created_at: Mapped[datetime] = ts()


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("user_id", "dedupe_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    type: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(12), default="info")  # urgent|digest|info
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(200))
    data_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    delivery_state: Mapped[str] = mapped_column(String(16), default="in_app")  # in_app|email_pending|email_sent|email_failed
    delivery_attempts: Mapped[int] = mapped_column(Integer, default=0)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = ts()


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prior_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    new_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    occurred_at: Mapped[datetime] = ts()


class QAMessage(Base):
    __tablename__ = "qa_messages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("proposals.id"), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    provider: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = ts()


class LLMCall(Base):
    __tablename__ = "llm_calls"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    research_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(32))
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))  # ok|invalid|quarantined|error
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = ts()


class ProviderStatus(Base):
    __tablename__ = "provider_status"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # market|research|llm|notify
    state: Mapped[str] = mapped_column(String(16))  # ok|degraded|unavailable|unconfigured
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    calls_today: Mapped[int] = mapped_column(Integer, default=0)
    calls_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    quota_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = ts()
