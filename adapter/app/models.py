from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class MarketSnapshot(BaseModel):
    model_config = ConfigDict(strict=True)

    bid: float
    ask: float
    last_close: float
    spread_points: float
    digits: int
    stops_level_points: int = 0
    freeze_level_points: int = 0
    tick_size: float = 0.0
    tick_value: float = 0.0


class AccountSnapshot(BaseModel):
    model_config = ConfigDict(strict=True)

    login: str
    balance: float
    equity: float
    free_margin: float
    margin_level: float
    currency: str = "USD"
    leverage: int = 0


class PositionSnapshot(BaseModel):
    model_config = ConfigDict(strict=True)

    net_position: float
    avg_price: float = 0.0
    floating_pnl: float = 0.0
    open_positions_count: int
    pending_orders_count: int
    side: Literal["flat", "long", "short"]


class RiskState(BaseModel):
    model_config = ConfigDict(strict=True)

    max_risk_per_trade_pct: float
    max_symbol_exposure_lots: float
    daily_drawdown_pct: float
    consecutive_losses: int = 0
    cooldown_until_utc: Optional[str] = None
    trading_halted: bool
    blackout_reason: Optional[str] = None


class FeatureBundle(BaseModel):
    model_config = ConfigDict(strict=True)

    context_tf: dict[str, float]
    decision_tf: dict[str, float]
    execution_tf: dict[str, float]


class OpenClawContext(BaseModel):
    model_config = ConfigDict(strict=True)

    mode: Literal["normal", "reduce-risk", "halt"] = "normal"
    operator_notes: str = ""
    calendar_flags: list[str] = Field(default_factory=list)


class DecisionRequest(BaseModel):
    model_config = ConfigDict(strict=True)

    schema_version: Literal["trade-decision-request.v1"]
    request_id: str
    mode: Literal["live", "paper", "replay"]
    timestamp_utc: str
    symbol: str
    timeframe: str
    bar_index: int
    market: MarketSnapshot
    account: AccountSnapshot
    position: PositionSnapshot
    risk_state: RiskState
    features: FeatureBundle
    openclaw_context: OpenClawContext = Field(default_factory=OpenClawContext)


class TradeDecision(BaseModel):
    model_config = ConfigDict(strict=True)

    action: Literal["hold", "open", "close", "reduce", "reverse", "modify"]
    side: Literal["flat", "buy", "sell"]
    order_type: Literal["none", "market", "limit", "stop"]
    lots: float
    entry_price: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    max_deviation_points: int
    valid_until_utc: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale_short: str = Field(max_length=240)
    reason_codes: list[str]
    risk_note: str = ""


class ResponseMeta(BaseModel):
    model_config = ConfigDict(strict=True)

    model: str
    anthropic_request_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


class AdapterError(BaseModel):
    model_config = ConfigDict(strict=True)

    code: str
    message: str
    retryable: bool


class DecisionResponse(BaseModel):
    model_config = ConfigDict(strict=True)

    schema_version: Literal["trade-decision-response.v1"]
    request_id: str
    status: Literal["ok", "degraded", "error"]
    decision: TradeDecision
    meta: ResponseMeta
    error: Optional[AdapterError] = None


class TradeTransactionEvent(BaseModel):
    model_config = ConfigDict(strict=True)

    schema_version: Literal["trade-transaction-event.v1"]
    request_id: str
    symbol: str
    trans_type: str
    order: int = 0
    deal: int = 0
    position: int = 0
    retcode: int = 0
    comment: str = ""
    time_utc: str
