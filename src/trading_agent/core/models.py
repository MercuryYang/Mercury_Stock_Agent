from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

Positive = Annotated[float, Field(gt=0)]
NonNegative = Annotated[float, Field(ge=0)]
Fraction = Annotated[float, Field(ge=0, le=1)]
Symbol = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,14}$")]


def utcnow() -> datetime:
    return datetime.now(UTC)


def stable_id(*parts) -> str:
    body = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(body.encode()).hexdigest()[:32]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Action(StrEnum):
    BUY = "BUY"
    ADD = "ADD"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    WAIT = "WAIT"
    REVIEW = "REVIEW"
    NO_ACTION = "NO_ACTION"


class Quality(StrEnum):
    VALID = "VALID"
    DATA_INVALID = "DATA_INVALID"
    DATA_STALE = "DATA_STALE"


class Regime(StrEnum):
    BULL_TREND = "BULL_TREND"
    BEAR_TREND = "BEAR_TREND"
    SIDEWAYS = "SIDEWAYS"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    RISK_OFF = "RISK_OFF"
    UNKNOWN = "UNKNOWN"


class Timed(Model):
    timestamp: AwareDatetime

    @field_validator("timestamp")
    @classmethod
    def to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class Bar(Timed):
    symbol: Symbol
    timeframe: Literal["1d", "1h", "15m"]
    open: Positive
    high: Positive
    low: Positive
    close: Positive
    volume: NonNegative
    source: str
    exchange_timezone: Literal["America/New_York"] = "America/New_York"
    is_delayed: bool | None = None
    session: Literal["REGULAR", "PRE", "POST", "CLOSED", "UNKNOWN"] = "REGULAR"
    adjustment: Literal["UNADJUSTED", "SPLIT_ADJUSTED", "TOTAL_RETURN", "UNKNOWN"] = "UNKNOWN"
    quality_status: Quality = Quality.VALID


class Quote(Timed):
    snapshot_id: str
    symbol: Symbol
    price: Positive
    bid: NonNegative | None = None
    ask: NonNegative | None = None
    source: str
    is_delayed: bool | None
    session: Literal["REGULAR", "PRE", "POST", "CLOSED", "UNKNOWN"]
    exchange_timezone: Literal["America/New_York"] = "America/New_York"
    adjustment: Literal["UNADJUSTED", "UNKNOWN"] = "UNADJUSTED"
    quality_status: Quality = Quality.VALID


class Position(Model):
    account_ref: str
    symbol: Symbol
    quantity: float
    average_cost: NonNegative | None = None
    tax_cost_basis: float | None = None
    manual_average_cost: NonNegative | None = None
    cost_basis_source: str = "UNKNOWN"
    market_value: float | None = None
    unrealized_pnl: float | None = None
    currency: str = "USD"
    source: str
    as_of: AwareDatetime
    sector: str | None = None
    add_count: int | None = None
    protective_stop: Positive | None = None


class Portfolio(Model):
    snapshot_id: str
    as_of: AwareDatetime
    source: str
    account_refs: list[str]
    positions: list[Position]
    cash: float | None
    equity: Positive | None
    peak_equity: Positive | None = None
    reconciliation: Literal["MATCHED", "RECONCILIATION_REQUIRED", "UNVERIFIED"]
    issues: list[str] = Field(default_factory=list)
    open_orders: list[dict] = Field(default_factory=list)
    transactions: list[dict] = Field(default_factory=list)
    order_history: list[dict] = Field(default_factory=list)
    order_coverage_complete: bool = False
    risk_context_id: str | None = None

    def position(self, symbol: str) -> Position | None:
        matches = [p for p in self.positions if p.symbol == symbol]
        return matches[0] if len(matches) == 1 else None


class QualityReport(Model):
    status: Quality
    reasons: list[str]


class Technical(Model):
    snapshot_id: str
    market_snapshot_id: str
    symbol: Symbol
    timeframe: str
    as_of: AwareDatetime
    version: str
    adjustment: str
    indicators: dict[str, float | None]
    trend: Literal["UP", "DOWN", "SIDEWAYS", "UNKNOWN"]
    levels: list[dict]
    quality: QualityReport


class MarketRegime(Model):
    snapshot_id: str
    state: Regime
    reasons: list[str]
    benchmark_snapshot_id: str | None
    version: str = "regime-v1"


class Signal(Model):
    signal_id: str
    strategy_id: str
    strategy_version: str
    symbol: Symbol
    direction: Literal["LONG", "NEUTRAL"] = "NEUTRAL"
    action: Action
    signal_strength: Fraction
    entry_zone: tuple[Positive, Positive] | None = None
    stop_loss_reference: Positive | None = None
    target_zone: tuple[Positive, Positive] | None = None
    risk_reward_ratio: Positive | None = None
    invalidation_condition: str
    reason_codes: list[str]
    market_snapshot_id: str
    technical_snapshot_id: str


class RiskAssessment(Model):
    assessment_id: str
    symbol: Symbol
    action: Action
    approved: bool
    quantity: Annotated[int, Field(gt=0)] | None = None
    entry_reference: Positive | None = None
    stop_reference: Positive | None = None
    risk_amount: NonNegative | None = None
    risk_reward_ratio: Positive | None = None
    reason_codes: list[str]
    warnings: list[str]
    metrics: dict[str, float | None]
    portfolio_snapshot_id: str
    market_snapshot_id: str
    signal_ids: list[str]
    config_version: str


class Evidence(Model):
    source_type: Literal["market", "portfolio", "technical", "strategy", "risk", "regime"]
    source_id: str
    statement: str = Field(max_length=500)


class Decision(Model):
    decision_id: str
    run_id: str
    created_at: AwareDatetime
    symbol: Symbol
    action: Action
    confidence_label: Literal["LOW", "MEDIUM", "HIGH"]
    market_view: str = Field(max_length=2000)
    evidence: list[Evidence]
    trigger_conditions: list[str]
    invalidation_conditions: list[str]
    risk_summary: list[str]
    quantity: Annotated[int, Field(gt=0)] | None = None
    entry_reference: Positive | None = None
    stop_reference: Positive | None = None
    risk_reward_ratio: Positive | None = None
    user_action_required: bool
    data_quality: Quality
    portfolio_snapshot_id: str
    market_snapshot_id: str
    strategy_versions: dict[str, str]
    generator: Literal["deterministic", "agents_sdk"]
    mode: Literal["demo", "live"]
    market_regime: Regime = Regime.UNKNOWN
    market_regime_snapshot_id: str | None = None
