"""
Which order an approved entry becomes, and what it may still lose.

The intent builder hands over everything one entry knows (`OrderInputs`) and this module
answers with the order type, its price, the drift the EA may still take and the loss at the
stop, or with the refusal that stops the cycle.

Without an agent plan the quote decides (kn/15 prefers limits): a passive price becomes a
LIMIT, otherwise a MARKET order is allowed for a MARKET or EITHER style within the drift
limit. With a plan the agent has already chosen: a LIMIT must still be passive, a STOP must
still sit beyond the quote, and a MARKET order must still fill near the planned price.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from types import MappingProxyType
from typing import Final

from ..config import V6Settings
from ..cycle_types import MarketContext, ProtocolDecision
from ..ledger_cycles_schema import SessionRecord
from ..market.feature_map import effective_friction
from ..schemas.intent import OrderType, PollRequest
from ..types import Candidate, ExitPlan, Refusal, SizingResult, TradePlan
from . import limits
from .exits import MIN_REWARD_R

SIDE_SIGN: Final[Mapping[str, int]] = MappingProxyType({"buy": 1, "sell": -1})
LIMIT_ORDERS: Final[Mapping[str, OrderType]] = MappingProxyType(
    {"buy": "BUY_LIMIT", "sell": "SELL_LIMIT"})
STOP_ORDERS: Final[Mapping[str, OrderType]] = MappingProxyType(
    {"buy": "BUY_STOP", "sell": "SELL_STOP"})
MARKET_ORDERS: Final[Mapping[str, OrderType]] = MappingProxyType({"buy": "BUY", "sell": "SELL"})
MARKET_STYLES: Final[frozenset[str]] = frozenset({"MARKET", "EITHER"})
DRIFT_STOP_FRACTION: Final[Decimal] = Decimal("0.2")
MIN_DRIFT_POINTS: Final[int] = 10          # V6_MAX_DRIFT_POINTS cannot go below it either

# Refusal codes this module raises (the publisher maps them to hold reasons).
LIMIT_NOT_PASSIVE: Final[str] = "LIMIT_NOT_PASSIVE"
STOP_NOT_BEYOND: Final[str] = "STOP_NOT_BEYOND"
MARKET_MOVED: Final[str] = "MARKET_MOVED"
MARKET_STOP_BELOW_FLOOR: Final[str] = "MARKET_STOP_BELOW_FLOOR"
MARKET_REWARD_BELOW_1R: Final[str] = "MARKET_REWARD_BELOW_1R"
RISK_OVER_BUDGET: Final[str] = "RISK_OVER_BUDGET"


@dataclass(frozen=True)
class ReferenceQuote:
    """The quote an order is judged against, with the account that reported it."""

    bid: float
    ask: float
    observed_at: float        # adapter clock when the quote arrived
    login: str
    trade_mode: str
    server: str

    @classmethod
    def from_context(cls, context: MarketContext) -> "ReferenceQuote":
        return cls(context.quote.bid, context.quote.ask, context.received_at,
                   context.account.login, context.trade_mode, context.server)

    @classmethod
    def from_poll(cls, poll: PollRequest, received_at: float) -> "ReferenceQuote":
        return cls(poll.bid, poll.ask, received_at, poll.login, poll.trade_mode, poll.server)

    def side_price(self, side: str) -> float:
        return self.ask if side == "buy" else self.bid


def dec(value: float) -> Decimal:
    return Decimal(repr(float(value)))


@dataclass(frozen=True)
class OrderInputs:
    """Everything one entry knows, as the intent builder assembled it."""

    resolution: ProtocolDecision
    candidate: Candidate
    plan: ExitPlan
    sizing: SizingResult
    context: MarketContext
    settings: V6Settings
    session: SessionRecord | None
    now: float
    agent: str
    quote: ReferenceQuote
    trade: TradePlan | None = None     # the agent plan behind the entry, when there is one

    @property
    def sign(self) -> int:
        return SIDE_SIGN[self.plan.side]

    @property
    def spread(self) -> float:
        return max(self.context.spread_price, self.quote.ask - self.quote.bid)

    @property
    def friction(self) -> Decimal:
        return dec(effective_friction(self.settings.friction_price, self.spread))


@dataclass(frozen=True)
class ChosenOrder:
    """The order to send: `pending` means it rests at the broker until it fills."""

    order_type: OrderType
    entry: float
    drift_points: int
    loss_usd: Decimal
    pending: bool


def stop_distance(i: OrderInputs, entry: Decimal) -> Decimal:
    return i.sign * (entry - dec(i.plan.sl))


def loss_usd(i: OrderInputs, distance: Decimal) -> Decimal:
    spec = i.context.spec
    return (dec(i.sizing.lots) * (distance + i.friction) / dec(spec.tick_size)
            * dec(spec.tick_value_loss))


def drift_points(i: OrderInputs) -> int:
    points = dec(i.plan.stop_distance) / dec(i.context.spec.point) * DRIFT_STOP_FRACTION
    return min(i.settings.max_drift_points,
               max(MIN_DRIFT_POINTS, int(points.to_integral_value(rounding=ROUND_FLOOR))))


def stop_floor(i: OrderInputs) -> Decimal:
    """The risk.exits floor: configured points, 10x spread, friction / 10%, stops level."""
    point = dec(i.context.spec.point)
    return max(i.settings.stop_floor_points * point,
               dec(limits.MIN_STOP_SPREAD_MULTIPLE) * dec(i.spread),
               i.friction / dec(limits.MAX_FRICTION_TO_STOP),
               i.context.spec.stops_level * point)


def _pending_gap(i: OrderInputs) -> Decimal:
    """EA check 13: a pending price sits a stops level plus one tick from the quote."""
    spec = i.context.spec
    return spec.stops_level * dec(spec.point) + dec(spec.tick_size)


def _limit_order(i: OrderInputs, entry: Decimal) -> ChosenOrder:
    return ChosenOrder(LIMIT_ORDERS[i.plan.side], i.plan.entry, drift_points(i),
                       loss_usd(i, stop_distance(i, entry)), pending=True)


def _market_order(i: OrderInputs, ref: Decimal, drift: int) -> ChosenOrder | Refusal:
    spec, point = i.context.spec, dec(i.context.spec.point)
    stop = stop_distance(i, ref)
    if stop < stop_floor(i):
        return Refusal((MARKET_STOP_BELOW_FLOOR,), f"stop {stop} from {ref} is under the floor")
    if i.sign * (dec(i.plan.tp) - ref) < dec(MIN_REWARD_R) * stop:
        return Refusal((MARKET_REWARD_BELOW_1R,), f"target from {ref} is under {MIN_REWARD_R}R")
    per_price = dec(i.sizing.lots) / dec(spec.tick_size) * dec(spec.tick_value_loss)
    slack = dec(i.sizing.risk_budget_usd) / per_price - i.friction - stop
    fitted = min(drift, int((slack / point).to_integral_value(rounding=ROUND_FLOOR)))
    if fitted < MIN_DRIFT_POINTS:
        return Refusal((RISK_OVER_BUDGET,), f"the budget leaves {slack} of drift at {ref}")
    return ChosenOrder(MARKET_ORDERS[i.plan.side], float(ref), fitted,
                       loss_usd(i, stop + fitted * point), pending=False)


def _suggested_order(i: OrderInputs) -> ChosenOrder | Refusal:
    """Without a plan the quote picks the order type (limits preferred, kn/15)."""
    ref, entry = dec(i.quote.side_price(i.plan.side)), dec(i.plan.entry)
    drift = drift_points(i)
    if i.sign * (ref - entry) >= _pending_gap(i):
        return _limit_order(i, entry)
    if i.resolution.order_style in MARKET_STYLES and abs(ref - entry) <= drift * dec(
            i.context.spec.point):
        return _market_order(i, ref, drift)
    return Refusal((LIMIT_NOT_PASSIVE,),
                   f"entry {i.plan.entry} is not passive to the quote and no market order fits")


def _planned_order(i: OrderInputs) -> ChosenOrder | Refusal:
    """With a plan the agent chose the order type; the quote only says whether it fits."""
    side, trade = i.plan.side, i.trade
    ref, entry = dec(i.quote.side_price(side)), dec(i.plan.entry)
    drift, gap = drift_points(i), _pending_gap(i)
    if trade is not None and trade.order_type == "LIMIT":
        if i.sign * (ref - entry) >= gap:
            return _limit_order(i, entry)
        return Refusal((LIMIT_NOT_PASSIVE,), f"the {side} LIMIT at {entry} is no longer passive")
    if trade is not None and trade.order_type == "STOP":
        if i.sign * (entry - ref) >= gap:
            return ChosenOrder(STOP_ORDERS[side], i.plan.entry, drift,
                               loss_usd(i, stop_distance(i, entry)), pending=True)
        return Refusal((STOP_NOT_BEYOND,), f"the {side} STOP at {entry} is not beyond the quote")
    if abs(ref - entry) <= drift * dec(i.context.spec.point):
        return _market_order(i, ref, drift)
    return Refusal((MARKET_MOVED,), f"the quote {ref} moved away from the planned {entry}")


def choose_order(i: OrderInputs) -> ChosenOrder | Refusal:
    """The order for this entry, or the refusal that stops it (inputs already checked)."""
    return _planned_order(i) if i.trade is not None else _suggested_order(i)
