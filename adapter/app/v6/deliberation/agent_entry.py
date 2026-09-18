"""
Entries the operator agent designs itself (user decision 2026-09-17).

The agent may answer a packet with its own entry plan instead of a detector
suggestion: side, LIMIT or MARKET, entry, stop and an optional target. This
module publishes the bounds such a plan must respect (`entry_limits`, sent in the
packet), checks a plan against them (`plan_problems`) and turns an acceptable plan
into an ordinary Candidate (setup "agent") that then takes the detector path:
`risk.exits.build_exit_plan`, `risk.sizing.size_position` and the intent builder.

The agent never sets lots, and hard safety stays in code. Prices are snapped to
the tick grid; a LIMIT must rest on the passive side of the quote and within
MAX_AGENT_ENTRY_ATR_M15 x ATR(M15); the stop must sit on the losing side, at
least the exit plan's floor away and no further than the budget can fund at the
minimum lot (and MAX_AGENT_STOP_ATR_M15 x ATR(M15)); a target must give between
MIN_AGENT_REWARD_R and MAX_AGENT_REWARD_R. A plan outside is refused with codes the
agent can act on before the deadline; the exit plan and the sizer still have the
last word.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from typing import TYPE_CHECKING, Final

from ..config import V6Settings
from ..cycle_codes import AGENT_SETUP, F_ATR_M15, agent_entry_id
from ..cycle_types import MarketContext
from ..risk import limits
from ..risk.exits import stop_floor_price, stop_shift_allowance
from ..schemas.operator_parts import AgentEntryPlan
from ..types import Candidate, SymbolSpec
from .context_builder import cycle_friction

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..schemas.operator import OperatorPacket

PROBLEM_LIMIT_NOT_PASSIVE: Final[str] = "LIMIT_NOT_PASSIVE"
PROBLEM_ENTRY_TOO_FAR: Final[str] = "ENTRY_TOO_FAR"
PROBLEM_STOP_WRONG_SIDE: Final[str] = "STOP_WRONG_SIDE"
PROBLEM_STOP_TOO_TIGHT: Final[str] = "STOP_TOO_TIGHT"
PROBLEM_STOP_TOO_WIDE: Final[str] = "STOP_TOO_WIDE"
PROBLEM_TARGET_WRONG_SIDE: Final[str] = "TARGET_WRONG_SIDE"
PROBLEM_REWARD_TOO_SMALL: Final[str] = "REWARD_TOO_SMALL"
PROBLEM_REWARD_TOO_LARGE: Final[str] = "REWARD_TOO_LARGE"
PROBLEM_NOT_FUNDABLE: Final[str] = "BUDGET_CANNOT_FUND_MIN_LOT"
AGENT_REASON_CODE: Final[str] = "AGENT_ENTRY"
_DIRECTION: Final[dict[str, int]] = {"buy": 1, "sell": -1}
_HALF_DAILY_LOSS: Final[Decimal] = Decimal("0.5")
_CENT: Final[Decimal] = Decimal("0.01")
_PERCENT: Final[Decimal] = Decimal(100)


@dataclass(frozen=True)
class EntryLimits:
    """What an agent entry must respect on this bar (sent as the packet's `limits`)."""

    agent_entry_id: str
    tick_size: float
    digits: int
    bid: float
    ask: float
    max_entry_distance: float
    stop_floor: float
    max_stop_distance: float
    min_reward_r: float
    max_reward_r: float
    default_reward_r: float
    risk_budget_usd: float
    # d_min: how far a stop, a target or a STOP entry stays from the price.
    modify_distance: float = 0.0

    @property
    def possible(self) -> bool:
        return self.max_stop_distance >= self.stop_floor

    @property
    def buy_limit_max(self) -> float:
        return snap(self.ask - self.tick_size, self.tick_size, self.digits)

    @property
    def sell_limit_min(self) -> float:
        return snap(self.bid + self.tick_size, self.tick_size, self.digits)

    @property
    def buy_stop_min(self) -> float:
        """The lowest BUY STOP price: the ask plus d_min, rounded up to a tick."""
        return round(math.ceil((self.ask + self.modify_distance) / self.tick_size - 1e-9)
                     * self.tick_size, self.digits)

    @property
    def sell_stop_max(self) -> float:
        """The highest SELL STOP price: the bid minus d_min, rounded down to a tick."""
        return _floor_to_tick(self.bid - self.modify_distance, self.tick_size, self.digits)


@dataclass(frozen=True)
class EntryProblem:
    code: str
    message: str


def snap(price: float, tick_size: float, digits: int) -> float:
    """`price` on the tick grid (nearest tick)."""
    return round(round(price / tick_size) * tick_size, digits)


def _floor_to_tick(value: float, tick_size: float, digits: int) -> float:
    return round(math.floor(value / tick_size + 1e-9) * tick_size, digits)


def risk_budget_usd(context: MarketContext, settings: V6Settings,
                    remaining_loss_usd: float) -> float:
    """The unscaled budget, the same rule as `risk.sizing` (which stays the authority)."""
    account = context.account
    basis = min(Decimal(repr(account.equity)), Decimal(repr(account.balance)),
                Decimal(repr(settings.sizing_equity_basis_usd)))
    by_equity = basis * Decimal(repr(settings.risk_pct)) / _PERCENT
    by_loss = _HALF_DAILY_LOSS * Decimal(repr(max(remaining_loss_usd, 0.0)))
    return float(min(by_equity, by_loss).quantize(_CENT, rounding=ROUND_FLOOR))


def fundable_stop(budget_usd: float, friction: float, spec: SymbolSpec) -> float:
    """The widest stop (price units) the budget pays at the minimum lot, friction included."""
    usd_per_unit = spec.volume_min * spec.tick_value_loss / spec.tick_size
    if usd_per_unit <= 0:
        return 0.0
    return max(0.0, budget_usd / usd_per_unit - friction)


def modify_distance(spec: SymbolSpec, spread_price: float) -> float:
    """d_min: how far a stop, a target or a STOP entry must stay from the price
    (max(stops_level, freeze_level) x point + spread + MODIFY_BUFFER_PRICE)."""
    levels = max(spec.stops_level, spec.freeze_level) * spec.point
    return round(levels + spread_price + limits.MODIFY_BUFFER_PRICE, spec.digits + 2)


def _atr_m15(context: MarketContext) -> float | None:
    value = context.features.get(F_ATR_M15)
    return float(value) if value is not None and math.isfinite(value) and value > 0 else None


def entry_limits(context: MarketContext, settings: V6Settings,
                 remaining_loss_usd: float) -> EntryLimits:
    """The bounds for an agent entry on this bar."""
    spec, spread = context.spec, context.spread_price
    friction = cycle_friction(settings, context)
    floor = stop_floor_price(spec, settings.stop_floor_points, spread, friction)
    budget = risk_budget_usd(context, settings, remaining_loss_usd)
    atr = _atr_m15(context)
    widest = fundable_stop(budget, friction, spec)
    if atr is not None:
        widest = min(widest, limits.MAX_AGENT_STOP_ATR_M15 * atr)
    headroom = max(stop_shift_allowance(spread, "buy"), stop_shift_allowance(spread, "sell"))
    max_entry = (limits.MAX_AGENT_ENTRY_ATR_M15 * atr if atr is not None
                 else limits.AGENT_ENTRY_FLOOR_MULTIPLE * floor)
    tick, digits = spec.tick_size, spec.digits
    return EntryLimits(
        agent_entry_id=agent_entry_id(context.bar_open_epoch), tick_size=tick, digits=digits,
        bid=context.quote.bid, ask=context.quote.ask,
        max_entry_distance=_floor_to_tick(max_entry, tick, digits),
        stop_floor=round(math.ceil(floor / tick - 1e-9) * tick, digits),
        max_stop_distance=max(0.0, _floor_to_tick(widest - headroom, tick, digits)),
        min_reward_r=limits.MIN_AGENT_REWARD_R, max_reward_r=limits.MAX_AGENT_REWARD_R,
        default_reward_r=settings.tp_r_multiple, risk_budget_usd=budget,
        modify_distance=modify_distance(spec, spread))


# --- checking a plan -----------------------------------------------------------------
def resolved_entry(plan: AgentEntryPlan, bounds: EntryLimits) -> float:
    """The entry price: the plan's LIMIT price on the grid, or the quote a MARKET would take."""
    if plan.entry is not None:
        return snap(plan.entry, bounds.tick_size, bounds.digits)
    return bounds.ask if plan.side == "buy" else bounds.bid


def reward_r(plan: AgentEntryPlan, bounds: EntryLimits) -> float:
    """The R multiple the plan's target asks for (the default when there is no target)."""
    if plan.target is None:
        return bounds.default_reward_r
    entry = resolved_entry(plan, bounds)
    risk = abs(entry - snap(plan.stop, bounds.tick_size, bounds.digits))
    if risk <= 0:
        return 0.0
    direction = _DIRECTION[plan.side]
    return direction * (snap(plan.target, bounds.tick_size, bounds.digits) - entry) / risk


def _entry_problems(plan: AgentEntryPlan, bounds: EntryLimits) -> list[EntryProblem]:
    problems: list[EntryProblem] = []
    entry = resolved_entry(plan, bounds)
    if plan.order_type == "LIMIT":
        passive = (entry <= bounds.buy_limit_max if plan.side == "buy"
                   else entry >= bounds.sell_limit_min)
        if not passive:
            edge = bounds.buy_limit_max if plan.side == "buy" else bounds.sell_limit_min
            problems.append(EntryProblem(PROBLEM_LIMIT_NOT_PASSIVE,
                                         f"a {plan.side} LIMIT must rest beyond {edge}"))
        reference = bounds.ask if plan.side == "buy" else bounds.bid
        if abs(entry - reference) > bounds.max_entry_distance + 1e-9:
            problems.append(EntryProblem(PROBLEM_ENTRY_TOO_FAR,
                                         f"entry is more than {bounds.max_entry_distance} "
                                         f"from the quote {reference}"))
    return problems


def stop_problems(plan: AgentEntryPlan, bounds: EntryLimits) -> list[EntryProblem]:
    entry = resolved_entry(plan, bounds)
    stop = snap(plan.stop, bounds.tick_size, bounds.digits)
    distance = round(_DIRECTION[plan.side] * (entry - stop), bounds.digits)
    if distance <= 0:
        return [EntryProblem(PROBLEM_STOP_WRONG_SIDE,
                             f"a {plan.side} stop must be {'below' if plan.side == 'buy' else 'above'} "
                             f"the entry {entry}")]
    if not bounds.possible:
        return [EntryProblem(PROBLEM_NOT_FUNDABLE,
                             f"the {bounds.risk_budget_usd} USD budget cannot fund the minimum "
                             f"lot at the {bounds.stop_floor} stop floor: HOLD")]
    if distance + 1e-9 < bounds.stop_floor:
        return [EntryProblem(PROBLEM_STOP_TOO_TIGHT,
                             f"stop distance {distance} is under the floor {bounds.stop_floor}")]
    if distance > bounds.max_stop_distance + 1e-9:
        return [EntryProblem(PROBLEM_STOP_TOO_WIDE,
                             f"stop distance {distance} exceeds {bounds.max_stop_distance}")]
    return []


def target_problems(plan: AgentEntryPlan, bounds: EntryLimits) -> list[EntryProblem]:
    if plan.target is None:
        return []
    ratio = reward_r(plan, bounds)
    if ratio <= 0:
        return [EntryProblem(PROBLEM_TARGET_WRONG_SIDE,
                             f"a {plan.side} target must be beyond the entry")]
    if ratio + 1e-9 < bounds.min_reward_r:
        return [EntryProblem(PROBLEM_REWARD_TOO_SMALL,
                             f"target gives {ratio:.2f}R, below {bounds.min_reward_r}R")]
    if ratio > bounds.max_reward_r + 1e-9:
        return [EntryProblem(PROBLEM_REWARD_TOO_LARGE,
                             f"target gives {ratio:.2f}R, above {bounds.max_reward_r}R")]
    return []


def plan_problems(plan: AgentEntryPlan, bounds: EntryLimits) -> tuple[EntryProblem, ...]:
    """Every reason the plan cannot be sent as it stands (empty when it fits the limits)."""
    stop = stop_problems(plan, bounds)
    target = [] if stop else target_problems(plan, bounds)
    return tuple(_entry_problems(plan, bounds) + stop + target)


def agent_candidate(plan: AgentEntryPlan, bounds: EntryLimits, bar_t: int) -> Candidate:
    """The plan as a detector-shaped candidate (call only when `plan_problems` is empty)."""
    return Candidate(
        candidate_id=bounds.agent_entry_id, setup=AGENT_SETUP, side=plan.side,
        entry=resolved_entry(plan, bounds),
        invalidation=snap(plan.stop, bounds.tick_size, bounds.digits), bar_t=bar_t,
        features={"reward_r": round(reward_r(plan, bounds), 4),
                  "market": 1.0 if plan.order_type == "MARKET" else 0.0},
        reason_codes=(AGENT_REASON_CODE,))


def limits_from_packet(packet: "OperatorPacket") -> EntryLimits:
    """The EntryLimits a served packet advertised (to check a submission against them)."""
    block, market = packet.limits, packet.market
    return EntryLimits(
        agent_entry_id=block.agent_entry_id, tick_size=block.tick_size, digits=block.digits,
        bid=market.bid, ask=market.ask, max_entry_distance=block.max_entry_distance,
        stop_floor=block.stop_floor, max_stop_distance=block.max_stop_distance,
        min_reward_r=block.min_reward_r, max_reward_r=block.max_reward_r,
        default_reward_r=block.default_reward_r, risk_budget_usd=block.risk_budget_usd,
        modify_distance=block.modify_distance)
