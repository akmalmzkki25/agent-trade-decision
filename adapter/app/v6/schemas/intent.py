"""
Poll, intent and execution contracts between the V6 EA and the adapter.

The intent response is deliberately FLAT with every field always present: the
EA parses JSON with simple field lookups, so nested objects and nulls are
avoided. `has_intent=false` means every intent field is a zero value. The full
contract (signatures, ids, EA-side refusal rules) is docs/v6-wire-contract.md;
the HMAC helpers live in `app.v6.wire`.
"""

from __future__ import annotations

import base64
import math
import re
import secrets
from collections.abc import Mapping
from decimal import Decimal
from types import MappingProxyType
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ..risk import limits

INTENT_ID_PATTERN: Final[str] = r"^[a-z2-7]{12}$"
INTENT_ID_CHARS: Final[int] = 12
INTENT_ID_RANDOM_BYTES: Final[int] = 8        # 64 random bits; the id keeps the first 60
ORDER_COMMENT_PREFIX: Final[str] = "Q6:"      # MT5 order/position comment: "Q6:<intent_id>"
BASKET_ID_MARKER: Final[str] = "-V6B-"        # basket id: "<symbol>-V6B-<intent_id>"
POLL_SCHEMA: Final[str] = "v6.poll.1"
INTENT_SCHEMA: Final[str] = "v6.intent.2"
ACTION_SCHEMA: Final[str] = "v6.action.1"
EXECUTION_SCHEMA: Final[str] = "v6.execution.1"
# A pending order must outlive the validity of its intent by at least this much.
MIN_PENDING_LIFETIME_S: Final[int] = 60
REQUIRE_DEMO: Final[int] = 1

_INTENT_ID_RE: Final[re.Pattern[str]] = re.compile(INTENT_ID_PATTERN)
_COMMENT_RE: Final[re.Pattern[str]] = re.compile(r"^Q6:([a-z2-7]{12})$")
_BASKET_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._#+-]{3,20}-V6B-([a-z2-7]{12})$")

IntentId = Annotated[str, StringConstraints(pattern=INTENT_ID_PATTERN)]
OptionalIntentId = Annotated[str, StringConstraints(pattern=r"^([a-z2-7]{12})?$")]
OptionalActionId = OptionalIntentId
Source = Literal["rules", "operator"]
SOURCES: Final[tuple[Source, ...]] = ("rules", "operator")
Command = Literal["NONE", "FLATTEN", "CANCEL_PENDING", "CLOSE_POSITION",
                  "MODIFY_POSITION", "MODIFY_PENDING"]
# Commands that carry a management action instead of an intent (spec section 3.3).
ACTION_COMMANDS: Final[frozenset[str]] = frozenset(
    {"CLOSE_POSITION", "MODIFY_POSITION", "MODIFY_PENDING"})
OrderType = Literal["BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP", "BUY", "SELL", "NONE"]
LIMIT_ORDER_TYPES: Final[frozenset[str]] = frozenset({"BUY_LIMIT", "SELL_LIMIT"})
STOP_ORDER_TYPES: Final[frozenset[str]] = frozenset({"BUY_STOP", "SELL_STOP"})
PENDING_ORDER_TYPES: Final[frozenset[str]] = LIMIT_ORDER_TYPES | STOP_ORDER_TYPES
ORDER_TYPES_BY_SIDE: Final[Mapping[str, frozenset[str]]] = MappingProxyType({
    "buy": frozenset({"BUY_LIMIT", "BUY_STOP", "BUY"}),
    "sell": frozenset({"SELL_LIMIT", "SELL_STOP", "SELL"})})
_SIDE_SIGN: Final[Mapping[str, int]] = MappingProxyType({"buy": 1, "sell": -1})


def new_intent_id() -> str:
    """12 lowercase base32 characters (60 random bits), e.g. 'k7w2m4pq3xza'."""
    encoded = base64.b32encode(secrets.token_bytes(INTENT_ID_RANDOM_BYTES)).decode("ascii")
    return encoded[:INTENT_ID_CHARS].lower()


def _require_intent_id(intent_id: str) -> str:
    if not isinstance(intent_id, str) or not _INTENT_ID_RE.match(intent_id):
        raise ValueError("intent_id must be 12 lowercase base32 characters")
    return intent_id


def order_comment(intent_id: str) -> str:
    """The comment that ties an MT5 order and its position to the intent."""
    return ORDER_COMMENT_PREFIX + _require_intent_id(intent_id)


def intent_id_from_comment(comment: str) -> str | None:
    """The intent id of a V6 order comment; None for anything else."""
    match = _COMMENT_RE.match(comment) if isinstance(comment, str) else None
    return None if match is None else match.group(1)


def basket_id_for(symbol: str, intent_id: str) -> str:
    """The basket id a V6 result carries: '<symbol>-V6B-<intent_id>' (V5 uses '-V5B-')."""
    basket_id = f"{symbol}{BASKET_ID_MARKER}{_require_intent_id(intent_id)}"
    if not _BASKET_RE.match(basket_id):
        raise ValueError("symbol is not a valid broker symbol name")
    return basket_id


def intent_id_from_basket(basket_id: str) -> str | None:
    match = _BASKET_RE.match(basket_id) if isinstance(basket_id, str) else None
    return None if match is None else match.group(1)


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


# Everything below `has_intent` except `sig`; all zero values when has_intent is false.
INTENT_FIELDS: Final[tuple[str, ...]] = (
    "intent_id", "source", "side", "order_type", "entry", "sl", "tp", "lots", "ref_price",
    "max_drift_points", "max_spread_points", "valid_until_epoch", "pending_expiry_epoch",
    "time_barrier_s", "magic", "tp1", "tp2", "sl_after_tp1", "sl_after_tp2",
)
# The management action of a command; all zero values for every other command.
ACTION_FIELDS: Final[tuple[str, ...]] = (
    "action_id", "action_ticket", "action_sl", "action_tp", "action_tp1", "action_tp2",
    "action_sl1", "action_sl2", "action_price", "action_expiry_epoch", "action_barrier_s",
    "action_issued_epoch",
)
ACTION_LEVEL_FIELDS: Final[tuple[str, ...]] = (
    "action_sl", "action_tp", "action_tp1", "action_tp2", "action_sl1", "action_sl2",
    "action_price", "action_expiry_epoch", "action_barrier_s",
)
# What each management command must carry beyond its identity.
ACTION_REQUIRED: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType({
    "CLOSE_POSITION": (),
    "MODIFY_POSITION": ("action_sl", "action_tp", "action_barrier_s"),
    "MODIFY_PENDING": ("action_sl", "action_tp", "action_barrier_s", "action_price",
                       "action_expiry_epoch"),
})
# What each command may never carry (the ladder levels stay optional).
ACTION_FORBIDDEN: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType({
    "CLOSE_POSITION": ACTION_LEVEL_FIELDS,
    "MODIFY_POSITION": ("action_price", "action_expiry_epoch"),
    "MODIFY_PENDING": (),
})


class PollResponse(_Strict):
    """The adapter's answer to a poll. `sig` is `wire.sign_intent`'s HMAC ("" unsigned)."""

    schema_version: Literal["v6.intent.2"] = "v6.intent.2"
    server_time_epoch: int = Field(ge=0)
    command: Command = "NONE"
    has_intent: bool = False
    intent_id: OptionalIntentId = ""
    source: Source | Literal[""] = ""
    require_demo: Literal[1] = 1
    side: Literal["buy", "sell", ""] = ""
    order_type: OrderType = "NONE"
    entry: float = Field(default=0.0, ge=0.0)
    sl: float = Field(default=0.0, ge=0.0)
    tp: float = Field(default=0.0, ge=0.0)
    lots: float = Field(default=0.0, ge=0.0, le=limits.MAX_EXECUTE_LOTS)
    ref_price: float = Field(default=0.0, ge=0.0)
    max_drift_points: int = Field(default=0, ge=0)
    max_spread_points: int = Field(default=0, ge=0)
    valid_until_epoch: int = Field(default=0, ge=0)
    pending_expiry_epoch: int = Field(default=0, ge=0)
    time_barrier_s: int = Field(default=0, ge=0, le=limits.MAX_TIME_BARRIER_S)
    magic: int = Field(default=0, ge=0)
    tp1: float = Field(default=0.0, ge=0.0)
    tp2: float = Field(default=0.0, ge=0.0)
    sl_after_tp1: float = Field(default=0.0, ge=0.0)
    sl_after_tp2: float = Field(default=0.0, ge=0.0)
    action_id: OptionalActionId = ""
    action_ticket: int = Field(default=0, ge=0)
    action_sl: float = Field(default=0.0, ge=0.0)
    action_tp: float = Field(default=0.0, ge=0.0)
    action_tp1: float = Field(default=0.0, ge=0.0)
    action_tp2: float = Field(default=0.0, ge=0.0)
    action_sl1: float = Field(default=0.0, ge=0.0)
    action_sl2: float = Field(default=0.0, ge=0.0)
    action_price: float = Field(default=0.0, ge=0.0)
    action_expiry_epoch: int = Field(default=0, ge=0)
    action_barrier_s: int = Field(default=0, ge=0, le=limits.MAX_TIME_BARRIER_S)
    action_issued_epoch: int = Field(default=0, ge=0)
    sig: Annotated[str, StringConstraints(pattern=r"^([0-9a-f]{64})?$")] = ""

    @model_validator(mode="after")
    def _consistent(self) -> "PollResponse":
        problems = (_intent_problems(self) if self.has_intent
                    else _leftover_fields(self, INTENT_FIELDS, "has_intent=false"))
        problems += _action_problems(self)
        if problems:
            raise ValueError("; ".join(problems))
        return self


def _leftover_fields(response: PollResponse, names: tuple[str, ...],
                     rule: str) -> list[str]:
    defaults = PollResponse.model_fields
    leftover = [name for name in names if getattr(response, name) != defaults[name].default]
    return [f"{rule} must leave {', '.join(leftover)} empty"] if leftover else []


def _action_problems(r: PollResponse) -> list[str]:
    """A management command carries its own levels; every other command carries none."""
    if r.command not in ACTION_COMMANDS:
        return _leftover_fields(r, ACTION_FIELDS, f"command {r.command}")
    required = ACTION_REQUIRED[r.command]
    unwanted = [name for name in ACTION_FORBIDDEN[r.command] if getattr(r, name) > 0]
    checks = (
        (not r.has_intent, "a management command never travels with an intent"),
        (bool(r.action_id) and r.action_ticket > 0 and r.action_issued_epoch > 0,
         "a management command needs action_id, action_ticket and action_issued_epoch"),
        (all(getattr(r, name) > 0 for name in required),
         f"{r.command} needs {', '.join(required) or 'no levels'}"),
        (not unwanted, f"{r.command} carries no {', '.join(unwanted)}"),
    )
    return [message for ok, message in checks if not ok]


def _finite_positive(*values: float) -> bool:
    return all(math.isfinite(value) and value > 0 for value in values)


def _within_lot_cap(lots: float) -> bool:
    if not math.isfinite(lots):
        return False
    return Decimal(repr(float(lots))) <= Decimal(repr(limits.MAX_EXECUTE_LOTS))


def _ladder_checks(sign: int, entry: float, sl: float, tp: float, tp1: float, tp2: float,
                   first: float, second: float) -> tuple[tuple[bool, str], ...]:
    """The SL+ ladder, when the intent carries one (spec section 2.2)."""
    if tp1 == 0 and tp2 == 0 and first == 0 and second == 0:
        return ()
    floor = first if first > 0 else sl
    return (
        (tp1 > 0 and tp2 > 0 and sign * (tp1 - entry) > 0 and sign * (tp2 - tp1) > 0
         and sign * (tp - tp2) > 0, "the TP ladder must advance: entry, tp1, tp2, tp"),
        (first == 0 or (sign * (first - sl) > 0 and sign * (tp1 - first) > 0),
         "sl_after_tp1 must sit between sl and tp1"),
        (second == 0 or (sign * (second - floor) >= 0 and sign * (second - sl) > 0
                         and sign * (tp2 - second) > 0),
         "sl_after_tp2 must sit between the stop before it and tp2"),
    )


def order_problems(*, side: str, order_type: str, entry: float, sl: float, tp: float,
                   lots: float, valid_until_epoch: int, pending_expiry_epoch: int,
                   time_barrier_s: int, tp1: float = 0.0, tp2: float = 0.0,
                   sl_after_tp1: float = 0.0, sl_after_tp2: float = 0.0) -> list[str]:
    """Geometry every live intent obeys (poll response and v6_intents row alike)."""
    sign = _SIDE_SIGN.get(side, 0)
    pending = order_type in PENDING_ORDER_TYPES
    checks = (
        (order_type in ORDER_TYPES_BY_SIDE.get(side, ()), "order_type does not match side"),
        (_finite_positive(entry, sl, tp, lots), "prices and lots must be finite and positive"),
        (sign * (entry - sl) > 0 and sign * (tp - entry) > 0,
         "sl and tp must lie on their own side of entry"),
        (_within_lot_cap(lots), f"lots above the {limits.MAX_EXECUTE_LOTS} execution cap"),
        (0 < time_barrier_s <= limits.MAX_TIME_BARRIER_S, "time barrier out of range"),
        (not pending or pending_expiry_epoch >= valid_until_epoch + MIN_PENDING_LIFETIME_S,
         f"a pending order must expire at least {MIN_PENDING_LIFETIME_S} s after valid_until"),
        (pending or pending_expiry_epoch == 0, "a market order has no pending expiry"),
        *_ladder_checks(sign, entry, sl, tp, tp1, tp2, sl_after_tp1, sl_after_tp2),
    )
    return [message for ok, message in checks if not ok]


def _intent_problems(r: PollResponse) -> list[str]:
    checks = (
        (r.command == "NONE", "an intent never travels with a command"),
        (bool(r.intent_id and r.source), "an intent needs intent_id and source"),
        (r.ref_price > 0, "ref_price must be positive"),
        (min(r.max_drift_points, r.max_spread_points) > 0,
         "drift and spread limits must be positive"),
        (limits.V6_MAGIC_FIRST <= r.magic <= limits.V6_MAGIC_LAST, "magic is outside the V6 range"),
        (r.valid_until_epoch > r.server_time_epoch, "the intent is already invalid"),
    )
    geometry = order_problems(
        side=r.side, order_type=r.order_type, entry=r.entry, sl=r.sl, tp=r.tp, lots=r.lots,
        valid_until_epoch=r.valid_until_epoch, pending_expiry_epoch=r.pending_expiry_epoch,
        time_barrier_s=r.time_barrier_s, tp1=r.tp1, tp2=r.tp2,
        sl_after_tp1=r.sl_after_tp1, sl_after_tp2=r.sl_after_tp2)
    return [message for ok, message in checks if not ok] + geometry


ExecutionStatus = Literal[
    "placed", "filled", "rejected_local", "expired", "cancelled", "failed", "dry_run"]
ExecutionReason = Literal[
    "NONE", "EXPIRED", "DRIFT", "SPREAD", "DEMO_REQUIRED", "LOT_CAP", "RISK_CAP",
    "BREAKER", "HALTED", "OCCUPIED", "ORDER_CHECK", "DUPLICATE", "BAD_SIGNATURE",
    "BAD_INTENT", "NO_SL", "MARKET_CLOSED", "BROKER_ERROR", "EXECUTE_DISABLED", "COMMAND",
]
ACCEPTED_STATUSES: Final[frozenset[str]] = frozenset({"placed", "filled"})
REFUSED_STATUSES: Final[frozenset[str]] = frozenset({"rejected_local", "failed", "dry_run"})


class ExecutionReport(_Strict):
    """What the EA did with one intent. `sent_at_epoch` is when this report was built."""

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

    @model_validator(mode="after")
    def _status_matches_reason(self) -> "ExecutionReport":
        if self.status in ACCEPTED_STATUSES and (self.reason_code != "NONE" or self.ticket <= 0):
            raise ValueError("placed and filled reports need reason NONE and a ticket")
        if self.status in REFUSED_STATUSES and self.reason_code == "NONE":
            raise ValueError(f"a {self.status} report needs a reason_code")
        return self

ActionKind = Literal["APPLIED", "REJECTED", "FAILED", "PLAN_STEP"]
ActionReason = Literal[
    "NONE", "UNKNOWN_TICKET", "STALE", "SL_WIDER", "TOO_CLOSE", "BARRIER", "DEMO_REQUIRED",
    "HALTED", "MARKET_CLOSED", "BROKER_ERROR", "BAD_ACTION",
]


class ActionReport(_Strict):
    """What the EA did with a management command, or an SL+ step it took (`PLAN_STEP`)."""

    schema_version: Literal["v6.action.1"]
    kind: ActionKind
    action_id: OptionalActionId
    command: Command
    intent_id: OptionalIntentId
    ticket: int = Field(ge=0)
    reason_code: ActionReason
    retcode: int = Field(ge=0)
    step: int = Field(ge=0, le=2)
    old_sl: float = Field(ge=0)
    new_sl: float = Field(ge=0)
    price: float = Field(ge=0)
    sent_at_epoch: int = Field(ge=0)

    @model_validator(mode="after")
    def _kind_matches(self) -> "ActionReport":
        step = self.kind == "PLAN_STEP"
        checks = (
            (step == (self.action_id == ""), "a PLAN_STEP has no action_id; an action has one"),
            (step == (self.command == "NONE"),
             "a PLAN_STEP has command NONE; an action names its command"),
            (not step or self.step in (1, 2), "a PLAN_STEP is step 1 or 2"),
            (step or self.command in ACTION_COMMANDS, "unknown management command"),
            ((self.kind == "APPLIED" or step) == (self.reason_code == "NONE"),
             "APPLIED and PLAN_STEP carry reason NONE; REJECTED and FAILED carry a reason"),
        )
        problems = [message for ok, message in checks if not ok]
        if problems:
            raise ValueError("; ".join(problems))
        return self
