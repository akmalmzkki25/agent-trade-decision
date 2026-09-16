"""
Poll, intent and execution contracts between the V6 EA and the adapter.

The intent response is deliberately FLAT with every field always present: the
EA parses JSON with simple field lookups, so nested objects and nulls are
avoided. `has_intent=false` means every intent field is a zero value.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

INTENT_ID_PATTERN: Final[str] = r"^[a-z2-7]{12}$"

IntentId = Annotated[str, StringConstraints(pattern=INTENT_ID_PATTERN)]
OptionalIntentId = Annotated[str, StringConstraints(pattern=r"^([a-z2-7]{12})?$")]
Source = Literal["rules", "openrouter", "claude_code"]
Command = Literal["NONE", "FLATTEN", "CANCEL_PENDING"]
OrderType = Literal["BUY_LIMIT", "SELL_LIMIT", "BUY", "SELL", "NONE"]


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, allow_inf_nan=False)


class PollRequest(_Strict):
    """Heartbeat plus 'anything for me?'. Sent every ~2 s; must stay cheap."""

    schema_version: Literal["v6.poll.1"]
    login: Annotated[str, StringConstraints(pattern=r"^[0-9]{1,20}$")]
    trade_mode: Literal["DEMO", "CONTEST", "REAL"]
    server: Annotated[str, StringConstraints(max_length=80)]
    sent_at_epoch: int = Field(ge=0)
    balance: float = Field(ge=0)
    equity: float = Field(ge=0)
    free_margin: float
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    spread_points: int = Field(ge=0)
    open_v6_positions: int = Field(ge=0, le=20)
    pending_v6_orders: int = Field(ge=0, le=20)
    floating_pnl_v6: float
    last_intent_id: OptionalIntentId
    local_halt: bool


class PollResponse(_Strict):
    schema_version: Literal["v6.intent.1"] = "v6.intent.1"
    server_time_epoch: int
    command: Command = "NONE"
    has_intent: bool = False
    intent_id: OptionalIntentId = ""
    source: Source | Literal[""] = ""
    require_demo: int = Field(default=1, ge=0, le=1)
    side: Literal["buy", "sell", ""] = ""
    order_type: OrderType = "NONE"
    entry: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    lots: float = 0.0
    ref_price: float = 0.0
    max_drift_points: int = 0
    max_spread_points: int = 0
    valid_until_epoch: int = 0
    pending_expiry_epoch: int = 0
    time_barrier_s: int = 0
    magic: int = 0
    sig: Annotated[str, StringConstraints(pattern=r"^([0-9a-f]{64})?$")] = ""


ExecutionStatus = Literal["placed", "filled", "rejected_local", "expired", "failed", "dry_run"]
ExecutionReason = Literal[
    "NONE", "EXPIRED", "DRIFT", "SPREAD", "DEMO_REQUIRED", "LOT_CAP", "RISK_CAP",
    "BREAKER", "ORDER_CHECK", "DUPLICATE", "BAD_SIGNATURE", "NO_SL", "BROKER_ERROR",
    "EXECUTE_DISABLED",
]


class ExecutionReport(_Strict):
    schema_version: Literal["v6.execution.1"]
    intent_id: IntentId
    status: ExecutionStatus
    reason_code: ExecutionReason
    ticket: int = Field(ge=0)
    retcode: int = Field(ge=0)
    requested_price: float = Field(ge=0)
    fill_price: float = Field(ge=0)
    slippage_points: float
    spread_points: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    sent_at_epoch: int = Field(ge=0)
