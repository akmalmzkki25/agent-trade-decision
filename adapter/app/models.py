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
    # Optional confirm timeframe used by /v2/plan (M5 features).
    confirm_tf: dict[str, float] = Field(default_factory=dict)


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


# ============================================================================
# v2 — Bulk Layering plan models
# ============================================================================


OrderType = Literal[
    "buy_limit",
    "sell_limit",
    "buy_stop",
    "sell_stop",
    "buy_market",
    "sell_market",
]


ScenarioName = Literal[
    "RANGE_REVERT",
    "TREND_BREAKOUT",
    "CONTINUATION_PULLBACK",
    "NONE",
]


class LayerEntry(BaseModel):
    model_config = ConfigDict(strict=True)

    layer_id: int = Field(ge=1, le=10)
    order_type: OrderType
    price: float
    lots: float
    sl: float
    tp: Optional[float] = None
    expiration_utc: str
    magic: int


class NewsContext(BaseModel):
    model_config = ConfigDict(strict=True)

    blackout: bool = False
    severity: float = Field(ge=0.0, le=1.0, default=0.0)
    event: str = ""


class LayerPlan(BaseModel):
    model_config = ConfigDict(strict=True)

    scenario: ScenarioName
    side_bias: Literal["buy", "sell", "both", "none"]
    confidence: float = Field(ge=0.0, le=1.0)
    basket_tp_pct_equity: float
    scenario_invalidation_price: Optional[float] = None
    valid_until_utc: str
    layers: list[LayerEntry]
    reason_codes: list[str]
    rationale_short: str = Field(max_length=240)


class LayerPlanRequest(BaseModel):
    """Same shape as DecisionRequest. Kept as separate class so the v2 endpoint
    can evolve independently (extra optional features etc.)."""

    model_config = ConfigDict(strict=True)

    schema_version: Literal["layer-plan-request.v1"]
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
    # Bulk layering knobs (optional; defaults applied server-side).
    total_risk_pct_override: Optional[float] = None


class LayerPlanResponse(BaseModel):
    model_config = ConfigDict(strict=True)

    schema_version: Literal["layer-plan-response.v1"]
    request_id: str
    status: Literal["ok", "degraded", "veto"]
    plan: LayerPlan
    news_context: NewsContext = Field(default_factory=NewsContext)
    meta: ResponseMeta
    error: Optional[AdapterError] = None


# ============================================================================
# v3 — Aggressive Mixed-Layering models
# ============================================================================


V3ScenarioName = Literal[
    "RANGE_REVERT",
    "TREND_BREAKOUT",
    "CONTINUATION_PULLBACK",
    "MOMENTUM_M1",
    "AGGRESSIVE_BIAS",
    "NONE",
]


BasketSlot = Literal["A", "B"]


class ActiveBasket(BaseModel):
    model_config = ConfigDict(strict=True)

    slot: BasketSlot
    side: Literal["buy", "sell"]
    opened_at_utc: str


class FundamentalContext(BaseModel):
    model_config = ConfigDict(strict=True)

    dxy_slope: float = 0.0
    vix_zscore: float = 0.0
    gold_bias: float = Field(ge=-1.0, le=1.0, default=0.0)
    reason_codes: list[str] = Field(default_factory=list)
    news_blackout: bool = False


class V3Layer(BaseModel):
    model_config = ConfigDict(strict=True)

    layer_id: int = Field(ge=1, le=10)
    order_type: OrderType
    price: float
    lots: float
    sl: float
    tp: Optional[float] = None
    magic: int
    is_anchor: bool = False
    weight: float = Field(ge=0.0, le=1.0)


class V3ExitRules(BaseModel):
    """V3 staged exit rules. EA enforces; planner emits defaults below."""

    model_config = ConfigDict(strict=True)

    # Per-layer broker-side TP at TP = entry + (sl_distance × rr_ratio).
    # RR 1.0 = 1:1, RR 2.0 = TP is 2x further than SL, etc.
    # Set rr_ratio = 0 to disable per-layer TP (legacy staged-exit only mode).
    rr_ratio: float = 1.0
    # Stage 1: partial close + trail SL
    stage1_trigger_pips: float = 30.0
    stage1_close_count: int = 3                # close N layers (oldest first)
    stage1_sl_offset_pips: float = -20.0       # SL = entry + offset*pip_size (BUY); for SELL mirrored. Negative = below entry.
    stage1_cancel_pendings: bool = True
    stage1_min_open_positions: int = 3
    # Stage 2: trail SL to BEP
    stage2_trigger_pips: float = 50.0
    stage2_sl_offset_pips: float = 0.0         # entry (BEP)
    # Stage 3: close more, keep runners
    stage3_trigger_pips: float = 60.0
    stage3_close_count: int = 1                # close 1 more layer
    stage3_min_remaining_after: int = 1        # keep at least N runners
    # Final cap (per-position)
    runner_cap_pips: float = 100.0


class V3Plan(BaseModel):
    model_config = ConfigDict(strict=True)

    scenario: V3ScenarioName
    side_bias: Literal["buy", "sell", "none"]
    confidence: float = Field(ge=0.0, le=1.0)
    basket_slot: Optional[BasketSlot] = None
    basket_tp_pct_equity: float                # legacy; EA prefers exit_rules
    scenario_invalidation_price: Optional[float] = None
    max_lifetime_seconds: int = 600
    valid_until_utc: str
    layers: list[V3Layer]
    reason_codes: list[str]
    rationale_short: str = Field(max_length=240)
    exit_rules: V3ExitRules = Field(default_factory=V3ExitRules)


class V3PlanRequest(BaseModel):
    model_config = ConfigDict(strict=True)

    schema_version: Literal["v3-plan-request.v1"]
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
    active_baskets: list[ActiveBasket] = Field(default_factory=list)
    # Optional fundamental features (DXY, VIX). Adapter handles missing.
    dxy_features: dict[str, float] = Field(default_factory=dict)
    vix_features: dict[str, float] = Field(default_factory=dict)
    total_risk_pct_override: Optional[float] = None


class V3PlanResponse(BaseModel):
    model_config = ConfigDict(strict=True)

    schema_version: Literal["v3-plan-response.v1"]
    request_id: str
    status: Literal["ok", "degraded", "veto"]
    plan: V3Plan
    news_context: NewsContext = Field(default_factory=NewsContext)
    fundamental_context: FundamentalContext = Field(default_factory=FundamentalContext)
    meta: ResponseMeta
    error: Optional[AdapterError] = None


# ============================================================================
# v4 — Liquidity-Zone Entry with Partial TP + BE
# ============================================================================


V4ScenarioName = Literal["LIQUIDITY_ZONE_ENTRY", "NONE"]


class LiquidityZone(BaseModel):
    model_config = ConfigDict(strict=True)

    side: Literal["buy", "sell"]
    top: float            # upper price of zone
    bottom: float         # lower price of zone (top > bottom always)
    sl_price: float       # SL price beyond zone (BUY: below bottom; SELL: above top)
    pip_size: float       # broker-specific pip size in price units (e.g. 0.10 for XAUUSD)
    width_pips: float     # zone width in pips (target ~30)
    sl_pips_from_zone: float  # distance from far edge to SL in pips (max ~50)


class V4Layer(BaseModel):
    model_config = ConfigDict(strict=True)

    layer_id: int = Field(ge=1, le=4)
    order_type: Literal["buy_limit", "sell_limit", "buy_stop", "sell_stop"]
    price: float
    lots: float
    sl: float
    magic: int
    # V4 exit metadata (EA enforces, not broker).
    partial_tp_pips: float = 30.0
    partial_close_fraction: float = 0.50    # fraction of lots to close at partial TP
    runner_cap_pips: float = 100.0
    move_sl_to_entry_after_partial: bool = True


class V4Plan(BaseModel):
    model_config = ConfigDict(strict=True)

    scenario: V4ScenarioName
    side_bias: Literal["buy", "sell", "none"]
    confidence: float = Field(ge=0.0, le=1.0)
    zone: Optional[LiquidityZone] = None
    max_lifetime_seconds: int = 1800       # 30 min default
    valid_until_utc: str
    layers: list[V4Layer]
    reason_codes: list[str]
    rationale_short: str = Field(max_length=240)


class V4PlanRequest(BaseModel):
    model_config = ConfigDict(strict=True)

    schema_version: Literal["v4-plan-request.v1"]
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
    # V4 only allows 1 basket — EA sends true if any active basket exists.
    has_active_basket: bool = False
    total_risk_pct_override: Optional[float] = None


class V4PlanResponse(BaseModel):
    model_config = ConfigDict(strict=True)

    schema_version: Literal["v4-plan-response.v1"]
    request_id: str
    status: Literal["ok", "degraded", "veto"]
    plan: V4Plan
    news_context: NewsContext = Field(default_factory=NewsContext)
    meta: ResponseMeta
    error: Optional[AdapterError] = None


# ============================================================================
# v5 — Scalping Burst Layering (tick-driven, basket-managed)
# ============================================================================


V5ScenarioName = Literal["SCALP_MICRO", "NONE"]


class V5ExitRules(BaseModel):
    """
    Basket-level exit rules for V5 scalper. EA enforces; per-order TP/SL
    intentionally absent because per-position SL on a scalper gets eaten by
    spread. Basket TP/SL fires the moment collective floating PnL reaches
    USD or equity-percent thresholds (whichever hits first).
    """

    model_config = ConfigDict(strict=True)

    basket_tp_usd: float = Field(gt=0.0, default=5.0)          # absolute USD profit target
    basket_tp_pct_equity: float = Field(gt=0.0, default=0.05)  # 0.05% of $10k = $5
    basket_sl_usd: float = Field(gt=0.0, default=30.0)         # absolute USD loss limit
    basket_sl_pct_equity: float = Field(gt=0.0, default=0.30)  # percent equity loss limit
    max_lifetime_seconds: int = Field(gt=0, default=120)       # 2 min force close
    # Divides per-layer sizing in planner_v5, so it must never reach zero.
    max_bursts_per_basket: int = Field(gt=0, default=3)
    min_burst_interval_ms: int = Field(ge=0, default=250)      # rate-limit between bursts
    cooldown_seconds: int = Field(ge=0, default=30)            # after basket close


class V5MicroLayer(BaseModel):
    model_config = ConfigDict(strict=True)

    layer_id: int = Field(ge=1, le=20)
    order_type: Literal["buy_market", "sell_market"]
    lots: float
    magic: int
    # No per-layer sl/tp on purpose.
    sl: float = 0.0
    tp: float = 0.0


class V5Burst(BaseModel):
    model_config = ConfigDict(strict=True)

    scenario: V5ScenarioName
    side_bias: Literal["buy", "sell", "none"]
    confidence: float = Field(ge=0.0, le=1.0)
    valid_until_utc: str
    layers: list[V5MicroLayer]
    reason_codes: list[str]
    rationale_short: str = Field(max_length=240)
    exit_rules: V5ExitRules = Field(default_factory=V5ExitRules)


class V5BurstRequest(BaseModel):
    model_config = ConfigDict(strict=True)

    schema_version: Literal["v5-burst-request.v1"]
    request_id: str
    mode: Literal["live", "paper", "replay"]
    timestamp_utc: str
    symbol: str
    timeframe: str                          # "TICK" or "M1"
    bar_index: int
    market: MarketSnapshot
    account: AccountSnapshot
    position: PositionSnapshot
    risk_state: RiskState
    features: FeatureBundle
    openclaw_context: OpenClawContext = Field(default_factory=OpenClawContext)
    # V5-specific state from EA
    active_basket_bursts: int = 0           # how many bursts already in active basket
    # Direction of the basket already in progress: "buy", "sell", or "" when flat.
    # Later bursts must match it, otherwise the basket hedges itself.
    active_basket_side: Literal["buy", "sell", ""] = ""
    last_burst_ms_ago: int = 999999         # ms since last burst (rate-limit gate)
    margin_level_pct: float = 0.0           # current margin level


class V5BurstResponse(BaseModel):
    model_config = ConfigDict(strict=True)

    schema_version: Literal["v5-burst-response.v1"]
    request_id: str
    status: Literal["ok", "degraded", "veto"]
    burst: V5Burst
    news_context: NewsContext = Field(default_factory=NewsContext)
    meta: ResponseMeta
    error: Optional[AdapterError] = None


# ============================================================================
# Basket results — feeds the performance metrics required by the scalper brief
# (profit factor, win rate, expected value, max floating drawdown, slippage).
# ============================================================================


class BasketResultEvent(BaseModel):
    """Posted by an EA when a basket finishes, for performance analytics."""

    model_config = ConfigDict(strict=True)

    schema_version: Literal["basket-result-event.v1"]
    basket_id: str
    version: Literal["v2", "v3", "v4", "v5"]
    symbol: str
    side: Literal["buy", "sell", "none"]
    opened_at_utc: str
    closed_at_utc: str
    close_reason: str
    bursts: int = Field(ge=0, default=0)
    positions: int = Field(ge=0, default=0)
    # Sign conventions are enforced, not just documented: a flipped sign on the
    # EA side would silently corrupt profit_factor and expected_value.
    gross_profit: float = Field(ge=0.0, default=0.0)   # sum of winning PnL
    gross_loss: float = Field(le=0.0, default=0.0)     # sum of losing PnL, negative
    net_pnl: float = 0.0                               # may be either sign
    max_floating_dd: float = Field(le=0.0, default=0.0)  # worst floating PnL, <= 0
    avg_slippage_points: float = Field(ge=0.0, default=0.0)
    avg_spread_points: float = Field(ge=0.0, default=0.0)
    decision_latency_ms: int = Field(ge=0, default=0)
    equity_at_open: float = Field(ge=0.0, default=0.0)
    equity_at_close: float = Field(ge=0.0, default=0.0)
