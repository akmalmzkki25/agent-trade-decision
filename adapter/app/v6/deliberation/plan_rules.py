"""
An agent plan v2 judged against the packet limits (spec section 2.2).

Checked in the order an agent can fix it: the shape, the entry (a LIMIT passive, a STOP
beyond the quote by d_min, both within the entry distance), the stop (floor, funding), the
target (1-5R), the ladder (ordering, TP1 at least 0.5R, SL+ steps d_min before their
trigger), the holding time and the pending expiry. A plan that passes becomes an ordinary
Candidate (setup "agent") and a TradePlan; the exit plan, the sizer and the intent builder
keep the last word.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from ..config import V6Settings
from ..cycle_codes import AGENT_SETUP
from ..cycle_types import MarketContext
from ..risk import limits
from ..schemas.operator_parts import AgentEntryPlan
from ..schemas.operator_plan import EntryPlanV2, ladder_problems
from ..types import Candidate, TradePlan
from .agent_entry import (
    AGENT_REASON_CODE, EntryLimits, EntryProblem, entry_limits, resolved_entry, snap,
    stop_problems, target_problems,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..schemas.operator import OperatorPacket

PROBLEM_SHAPE: Final[str] = "PLAN_SHAPE"
PROBLEM_STOP_NOT_BEYOND: Final[str] = "STOP_NOT_BEYOND"
PROBLEM_TP1_TOO_CLOSE: Final[str] = "TP1_TOO_CLOSE"
PROBLEM_STEP_TOO_CLOSE: Final[str] = "SL_STEP_TOO_CLOSE"
PROBLEM_TIME_LIMIT: Final[str] = "TIME_LIMIT_RANGE"
PROBLEM_PENDING_EXPIRY: Final[str] = "PENDING_EXPIRY_RANGE"
PROBLEM_TP3_TRIMMED: Final[str] = "TP3_TRIMMED_BELOW_TP2"
PROBLEM_LIMIT_NOT_PASSIVE: Final[str] = "LIMIT_NOT_PASSIVE"
PROBLEM_ENTRY_TOO_FAR: Final[str] = "ENTRY_TOO_FAR"
SECONDS_PER_MINUTE: Final[int] = 60
LOTS_EPSILON: Final[float] = 1e-9
PRICE_EPSILON: Final[float] = 1e-9


@dataclass(frozen=True)
class PlanBounds:
    """Everything a plan is judged against: the entry limits plus the plan windows."""

    entry: EntryLimits
    min_tp1_r: float
    time_limit_min: int
    time_limit_max: int
    pending_expiry_min: int
    pending_expiry_max: int
    volume_min: float
    lots_step: float
    max_lots: float


def plan_bounds(context: MarketContext, settings: V6Settings,
                remaining_loss_usd: float) -> PlanBounds:
    """The bounds for an agent plan on this bar."""
    low, high = settings.time_limit_bounds_s
    expiry_low, expiry_high = settings.pending_expiry_bounds_s
    return PlanBounds(
        entry=entry_limits(context, settings, remaining_loss_usd),
        min_tp1_r=limits.MIN_TP1_R, time_limit_min=low // SECONDS_PER_MINUTE,
        time_limit_max=high // SECONDS_PER_MINUTE,
        pending_expiry_min=expiry_low // SECONDS_PER_MINUTE,
        pending_expiry_max=expiry_high // SECONDS_PER_MINUTE,
        volume_min=context.spec.volume_min, lots_step=context.spec.volume_step,
        max_lots=min(settings.max_lots, limits.MAX_EXECUTE_LOTS))


def bounds_from_packet(packet: "OperatorPacket") -> PlanBounds:
    """The PlanBounds a served packet advertised."""
    from .agent_entry import limits_from_packet

    block = packet.limits
    return PlanBounds(
        entry=limits_from_packet(packet), min_tp1_r=block.min_tp1_r,
        time_limit_min=block.time_limit_min_minutes,
        time_limit_max=block.time_limit_max_minutes,
        pending_expiry_min=block.pending_expiry_min_minutes,
        pending_expiry_max=block.pending_expiry_max_minutes,
        volume_min=block.volume_min, lots_step=block.lots_step, max_lots=block.max_lots)


def _legacy(plan: EntryPlanV2) -> AgentEntryPlan:
    """The plan in the shape agent_entry's stop and target checks read."""
    return AgentEntryPlan(side=plan.side,
                          order_type="MARKET" if plan.entry is None else "LIMIT",
                          entry=plan.entry, stop=plan.sl, target=plan.tp3)


def _shape_problems(plan: EntryPlanV2) -> list[EntryProblem]:
    market = plan.order_type == "MARKET"
    problems = []
    if market != (plan.entry is None):
        problems.append(EntryProblem(PROBLEM_SHAPE,
                                     "MARKET leaves entry null; LIMIT and STOP set it"))
    if market != (plan.pending_expiry_min is None):
        problems.append(EntryProblem(PROBLEM_SHAPE,
                                     "pending_expiry_min is set exactly for LIMIT and STOP"))
    return problems


def _entry_problems(plan: EntryPlanV2, bounds: EntryLimits) -> list[EntryProblem]:
    if plan.entry is None:
        return []
    entry = snap(plan.entry, bounds.tick_size, bounds.digits)
    buy = plan.side == "buy"
    reference = bounds.ask if buy else bounds.bid
    problems = []
    if plan.order_type == "LIMIT" and not (entry <= bounds.buy_limit_max if buy
                                           else entry >= bounds.sell_limit_min):
        edge = bounds.buy_limit_max if buy else bounds.sell_limit_min
        problems.append(EntryProblem(PROBLEM_LIMIT_NOT_PASSIVE,
                                     f"a {plan.side} LIMIT must rest beyond {edge}"))
    if plan.order_type == "STOP" and not (entry >= bounds.buy_stop_min if buy
                                          else entry <= bounds.sell_stop_max):
        edge = bounds.buy_stop_min if buy else bounds.sell_stop_max
        problems.append(EntryProblem(PROBLEM_STOP_NOT_BEYOND,
                                     f"a {plan.side} STOP must sit beyond {edge}"))
    if abs(entry - reference) > bounds.max_entry_distance + PRICE_EPSILON:
        problems.append(EntryProblem(PROBLEM_ENTRY_TOO_FAR,
                                     f"entry is more than {bounds.max_entry_distance} from "
                                     f"the quote {reference}"))
    return problems


def _step_problems(plan: EntryPlanV2, sign: int, distance: float) -> list[EntryProblem]:
    steps = ((plan.sl_after_tp1, plan.tp1, "sl_after_tp1"),
             (plan.sl_after_tp2, plan.tp2, "sl_after_tp2"))
    return [EntryProblem(PROBLEM_STEP_TOO_CLOSE,
                         f"{name} must stay {distance} before its trigger")
            for level, trigger, name in steps
            if level is not None and sign * (trigger - level) + PRICE_EPSILON < distance]


def _ladder_problems(plan: EntryPlanV2, bounds: PlanBounds) -> list[EntryProblem]:
    entry_limits_ = bounds.entry
    entry = resolved_entry(_legacy(plan), entry_limits_)
    found = ladder_problems(plan.side, entry, plan.sl, plan.tp1, plan.tp2, plan.tp3,
                            plan.sl_after_tp1, plan.sl_after_tp2)
    if found:
        return [EntryProblem(*text.split(": ", 1)) for text in found]
    sign = 1 if plan.side == "buy" else -1
    problems = _step_problems(plan, sign, entry_limits_.modify_distance)
    risk = sign * (entry - plan.sl)
    if sign * (plan.tp1 - entry) + PRICE_EPSILON < bounds.min_tp1_r * risk:
        problems.insert(0, EntryProblem(PROBLEM_TP1_TOO_CLOSE,
                                        f"tp1 must be at least {bounds.min_tp1_r}R from entry"))
    return problems


def _time_problems(plan: EntryPlanV2, bounds: PlanBounds) -> list[EntryProblem]:
    problems = []
    if not bounds.time_limit_min <= plan.time_limit_min <= bounds.time_limit_max:
        problems.append(EntryProblem(PROBLEM_TIME_LIMIT,
                                     f"time_limit_min must be {bounds.time_limit_min}-"
                                     f"{bounds.time_limit_max}"))
    expiry = plan.pending_expiry_min
    if expiry is not None and not bounds.pending_expiry_min <= expiry <= bounds.pending_expiry_max:
        problems.append(EntryProblem(PROBLEM_PENDING_EXPIRY,
                                     f"pending_expiry_min must be {bounds.pending_expiry_min}-"
                                     f"{bounds.pending_expiry_max}"))
    return problems


def plan_problems_v2(plan: EntryPlanV2, bounds: PlanBounds) -> tuple[EntryProblem, ...]:
    """Every reason the plan cannot be sent as it stands (empty when it fits)."""
    shape = _shape_problems(plan)
    if shape:
        return tuple(shape)
    legacy = _legacy(plan)
    stop = stop_problems(legacy, bounds.entry)
    target = [] if stop else target_problems(legacy, bounds.entry)
    ladder = [] if stop or target else _ladder_problems(plan, bounds)
    return tuple(_entry_problems(plan, bounds.entry) + stop + target + ladder
                 + _time_problems(plan, bounds))


def lots_problem(lots: float, bounds: PlanBounds) -> str | None:
    """Why the requested size does not fit volume_min..max_lots on the step grid."""
    steps = round(lots / bounds.lots_step)
    on_step = abs(steps * bounds.lots_step - lots) <= LOTS_EPSILON
    within = bounds.volume_min - LOTS_EPSILON <= lots <= bounds.max_lots + LOTS_EPSILON
    if on_step and within:
        return None
    return (f"lots must be a multiple of {bounds.lots_step} between {bounds.volume_min} "
            f"and {bounds.max_lots}")


def _reward_r(plan: EntryPlanV2, bounds: EntryLimits) -> float:
    entry = resolved_entry(_legacy(plan), bounds)
    risk = abs(entry - plan.sl)
    return 0.0 if risk <= 0 else abs(plan.tp3 - entry) / risk


def plan_candidate(plan: EntryPlanV2, bounds: PlanBounds, bar_t: int) -> Candidate:
    """The plan as a detector-shaped candidate (only after `plan_problems_v2` is empty)."""
    entry_limits_ = bounds.entry
    return Candidate(
        candidate_id=entry_limits_.agent_entry_id, setup=AGENT_SETUP, side=plan.side,
        entry=resolved_entry(_legacy(plan), entry_limits_),
        invalidation=snap(plan.sl, entry_limits_.tick_size, entry_limits_.digits),
        bar_t=bar_t,
        features={"reward_r": round(_reward_r(plan, entry_limits_), 4),
                  "market": 1.0 if plan.order_type == "MARKET" else 0.0,
                  "stop_order": 1.0 if plan.order_type == "STOP" else 0.0},
        reason_codes=(AGENT_REASON_CODE,))


def trade_plan(plan: EntryPlanV2) -> TradePlan:
    """The ladder, holding time and pending expiry the intent carries to the EA."""
    expiry = plan.pending_expiry_min or 0
    return TradePlan(
        order_type=plan.order_type, tp1=plan.tp1, tp2=plan.tp2,
        sl_after_tp1=plan.sl_after_tp1 or 0.0, sl_after_tp2=plan.sl_after_tp2 or 0.0,
        time_limit_s=plan.time_limit_min * SECONDS_PER_MINUTE,
        pending_expiry_s=expiry * SECONDS_PER_MINUTE)


def ladder_after_exit(plan: EntryPlanV2, exit_tp: float) -> str | None:
    """The exit plan may pull TP3 in front of a round level; it must stay beyond TP2."""
    sign = Decimal(1 if plan.side == "buy" else -1)
    if sign * (Decimal(repr(exit_tp)) - Decimal(repr(plan.tp2))) > 0:
        return None
    return f"{PROBLEM_TP3_TRIMMED}: the exit plan pulled tp3 to {exit_tp}, not beyond tp2"
