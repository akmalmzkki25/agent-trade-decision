"""
Decision v3 building blocks (docs/superpowers/specs/2026-09-17-v6-m1-dynamic-management-design.md).

An agent plan carries the initial stop, a three-level target ladder and optional SL+ steps;
a manage request changes a resting order or an open position; the M15 bias is the agent's
own reading, echoed in later packets. The models check types and ranges only:
`deliberation.plan_rules` and `deliberation.management` judge the numbers against the
packet, so their refusals can name the broken rule without echoing submitted text.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Final, Literal

from pydantic import AfterValidator, Field, StringConstraints

from ..types import Side
from .agents import _printable as printable
from .operator_parts import Epoch, Frozen, Price

PlanOrderType = Literal["MARKET", "LIMIT", "STOP"]
ManageTarget = Literal["position", "pending"]
ManageOp = Literal["KEEP", "CLOSE", "CANCEL", "MODIFY"]
BiasDirection = Literal["up", "down", "range", "unclear"]
DecisionAction = Literal["HOLD", "ENTER", "MANAGE"]
PacketKind = Literal["m15"]
PacketState = Literal["flat", "pending", "position"]
ActionStatus = Literal["PUBLISHED", "APPLIED", "REJECTED", "FAILED", "EXPIRED"]

MAX_BIAS_LEVELS: Final[int] = 6
MAX_SCENARIO_CHARS: Final[int] = 240
MAX_REASON_CHARS: Final[int] = 200
MAX_THESIS_CHARS: Final[int] = 300
MAX_ACTION_DETAIL_CHARS: Final[int] = 120
MAX_PLAN_MINUTES: Final[int] = 240
MAX_EXPIRY_MINUTES: Final[int] = 60
MAX_PLAN_LOTS: Final[float] = 1.0
OPS_BY_TARGET: Final[Mapping[str, frozenset[str]]] = MappingProxyType({
    "position": frozenset({"KEEP", "CLOSE", "MODIFY"}),
    "pending": frozenset({"KEEP", "CANCEL", "MODIFY"}),
})
MODIFY_FIELDS: Final[tuple[str, ...]] = (
    "sl", "tp1", "tp2", "tp3", "sl_after_tp1", "sl_after_tp2", "time_limit_min",
    "entry", "pending_expiry_min")
PENDING_ONLY_FIELDS: Final[tuple[str, ...]] = ("entry", "pending_expiry_min")

Thesis = Annotated[str, StringConstraints(max_length=MAX_THESIS_CHARS),
                   AfterValidator(printable)]
Reason = Annotated[str, StringConstraints(max_length=MAX_REASON_CHARS),
                   AfterValidator(printable)]
Scenario = Annotated[str, StringConstraints(max_length=MAX_SCENARIO_CHARS),
                     AfterValidator(printable)]
ActionDetail = Annotated[str, StringConstraints(max_length=MAX_ACTION_DETAIL_CHARS),
                         AfterValidator(printable)]
PlanMinutes = Annotated[int, Field(ge=1, le=MAX_PLAN_MINUTES)]
ExpiryMinutes = Annotated[int, Field(ge=1, le=MAX_EXPIRY_MINUTES)]


class EntryPlanV2(Frozen):
    """An entry the agent designed: initial stop, TP ladder, SL+ steps, holding time, size."""

    side: Side
    order_type: PlanOrderType
    entry: Price | None = None
    sl: Price
    tp1: Price
    tp2: Price
    tp3: Price
    sl_after_tp1: Price | None = None
    sl_after_tp2: Price | None = None
    time_limit_min: PlanMinutes
    pending_expiry_min: ExpiryMinutes | None = None
    lots: float = Field(gt=0, le=MAX_PLAN_LOTS)
    thesis: Thesis = ""


class ManageRequest(Frozen):
    """What to do with the resting order or the open position of the packet."""

    target: ManageTarget
    ticket: int = Field(gt=0)
    op: ManageOp
    sl: Price | None = None
    tp1: Price | None = None
    tp2: Price | None = None
    tp3: Price | None = None
    sl_after_tp1: Price | None = None
    sl_after_tp2: Price | None = None
    time_limit_min: PlanMinutes | None = None
    entry: Price | None = None
    pending_expiry_min: ExpiryMinutes | None = None
    reason: Reason = ""

    @property
    def changes(self) -> dict[str, float | int]:
        """The fields a MODIFY sets (None means unchanged)."""
        return {name: getattr(self, name) for name in MODIFY_FIELDS
                if getattr(self, name) is not None}


class M15Bias(Frozen):
    """The agent's reading of the M15 bar, carried into the packets that follow."""

    direction: BiasDirection
    levels: tuple[Price, ...] = Field(default=(), max_length=MAX_BIAS_LEVELS)
    invalidation: Price | None = None
    scenario: Scenario = ""


class PacketPlan(Frozen):
    """The ladder of a resting order or an open position (0 = no level)."""

    tp1: float = Field(ge=0)
    tp2: float = Field(ge=0)
    sl_after_tp1: float = Field(ge=0)
    sl_after_tp2: float = Field(ge=0)
    step: int = Field(ge=0, le=2)
    time_limit_min: int = Field(ge=0, le=MAX_PLAN_MINUTES)


class PacketPosition(Frozen):
    """The open V6 position a management packet asks about."""

    ticket: int = Field(gt=0)
    intent_id: Annotated[str, StringConstraints(max_length=16)]
    side: Side
    lots: Price
    open_price: Price
    open_epoch: Epoch
    sl: float = Field(ge=0)
    tp: float = Field(ge=0)
    initial_sl: float = Field(ge=0)
    profit: float
    r_now: float
    mae_points: float = Field(ge=0)
    mfe_points: float = Field(ge=0)
    minutes_open: float = Field(ge=0)
    time_limit_epoch: Epoch
    plan: PacketPlan


class PacketAction(Frozen):
    """The newest management action of this session and what became of it."""

    action_id: Annotated[str, StringConstraints(pattern=r"^[a-z2-7]{12}$")]
    op: Annotated[str, StringConstraints(pattern=r"^[A-Z_]{1,24}$")]
    ticket: int = Field(ge=0)
    status: ActionStatus
    detail: ActionDetail
    at_epoch: Epoch


def _advancing(sign: int, levels: list[float]) -> bool:
    return all(sign * (later - earlier) > 0 for earlier, later in zip(levels, levels[1:]))


def ladder_problems(side: str, entry: float | None, sl: float, tp1: float, tp2: float,
                    tp3: float, sl_after_tp1: float | None,
                    sl_after_tp2: float | None) -> list[str]:
    """The ordering every plan keeps whatever the market does: sl, entry, tp1, tp2, tp3 in
    the trade's direction, and each SL+ step between the stop before it and its trigger."""
    sign = 1 if side == "buy" else -1
    levels = [sl] + ([] if entry is None else [entry]) + [tp1, tp2, tp3]
    problems = [] if _advancing(sign, levels) else [
        "LADDER_ORDER: levels must advance in the trade direction: sl, entry, tp1, tp2, tp3"]
    if sl_after_tp1 is not None and not _advancing(sign, [sl, sl_after_tp1, tp1]):
        problems.append("SL_STEP_INVALID: sl_after_tp1 must lie between sl and tp1")
    if sl_after_tp2 is not None:
        floor = sl if sl_after_tp1 is None else sl_after_tp1
        if not (sign * (sl_after_tp2 - floor) >= 0 and _advancing(sign, [sl, sl_after_tp2, tp2])):
            problems.append("SL_STEP_INVALID: sl_after_tp2 must lie between the stop before "
                            "it and tp2")
    return problems
