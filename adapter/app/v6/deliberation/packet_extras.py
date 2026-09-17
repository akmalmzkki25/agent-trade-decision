"""
The analysis blocks of the operator packet: closed bars, reference levels and
the limits of an agent-designed entry (user decision 2026-09-17).

Everything is built from what the cycle already knows at the bar close: the bars
in the MarketContext (closed bars only), confirmed pivots (a pivot counts once
its confirmation bar has closed) and `agent_entry.entry_limits`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Final

from ..config import V6Settings
from ..cycle_types import MarketContext
from ..market.levels import Pivot, confirmed_pivots, prior_day_levels
from ..schemas.operator_parts import (
    MAX_PACKET_D1_BARS, MAX_PACKET_H1_BARS, MAX_PACKET_M1_BARS, MAX_PACKET_M5_BARS,
    MAX_PACKET_M15_BARS, MAX_PACKET_PIVOTS,
)
from ..setups.base import full_days
from ..types import TIMEFRAME_SECONDS, Bar
from .agent_entry import entry_limits

Document = dict[str, object]

PIVOT_STRENGTH: Final[int] = 2
INTENT_COMMENT_PREFIX: Final[str] = "Q6:"
ROUND_STEP_SMALL: Final[float] = 10.0
ROUND_STEP_LARGE: Final[float] = 50.0
BAR_LIMITS: Final[tuple[tuple[str, int], ...]] = (
    ("M1", MAX_PACKET_M1_BARS), ("M5", MAX_PACKET_M5_BARS), ("M15", MAX_PACKET_M15_BARS),
    ("H1", MAX_PACKET_H1_BARS), ("D1", MAX_PACKET_D1_BARS))


def _finite(*values: float) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


def compact_bars(bars: Sequence[Bar], limit: int) -> list[list[int | float]]:
    rows = [[int(bar.t), float(bar.o), float(bar.h), float(bar.l), float(bar.c)]
            for bar in bars if _finite(bar.o, bar.h, bar.l, bar.c)]
    return rows[-limit:]


def bars_block(context: MarketContext) -> Document:
    return {tf: compact_bars(context.bars.get(tf, ()), limit) for tf, limit in BAR_LIMITS}


def _pivots(bars: Sequence[Bar], tf: str, as_of: int) -> list[Document]:
    step = TIMEFRAME_SECONDS[tf]
    known: list[Pivot] = [pivot for pivot in confirmed_pivots(tuple(bars), PIVOT_STRENGTH, step)
                          if pivot.confirmed_at <= as_of and _finite(pivot.price)
                          and pivot.price > 0]
    return [{"kind": pivot.kind, "price": pivot.price, "t": pivot.t}
            for pivot in known[-MAX_PACKET_PIVOTS:]]


def _round(price: float, step: float) -> tuple[float, float]:
    below = math.floor(price / step) * step
    above = math.ceil(price / step) * step
    if above == below:
        above = below + step
    return max(below, step), above


def levels_block(context: MarketContext) -> Document:
    """Prior full day's high/low, nearest $10/$50 levels and confirmed pivots."""
    daily = full_days(tuple(bar for bar in context.bars.get("D1", ())
                            if bar.t + TIMEFRAME_SECONDS["D1"] <= context.as_of_epoch))
    extremes = prior_day_levels(daily) if daily else None
    mid = (context.quote.bid + context.quote.ask) / 2
    small, large = _round(mid, ROUND_STEP_SMALL), _round(mid, ROUND_STEP_LARGE)
    return {
        "prior_day_high": None if extremes is None else extremes[0],
        "prior_day_low": None if extremes is None else extremes[1],
        "round_10_below": small[0], "round_10_above": small[1],
        "round_50_below": large[0], "round_50_above": large[1],
        "pivots_m15": _pivots(context.bars.get("M15", ()), "M15", context.as_of_epoch),
        "pivots_h1": _pivots(context.bars.get("H1", ()), "H1", context.as_of_epoch),
    }


def pending_order_block(context: MarketContext) -> Document | None:
    """The first resting V6 order, for a review packet (None when there is none)."""
    if not context.pending_orders:
        return None
    order = context.pending_orders[0]
    comment = order.comment
    intent_id = comment[len(INTENT_COMMENT_PREFIX):] if comment.startswith(
        INTENT_COMMENT_PREFIX) else ""
    buying = order.order_type.startswith("BUY")
    quote = context.quote
    distance = quote.ask - order.price if buying else order.price - quote.bid
    return {"ticket": order.ticket, "intent_id": intent_id[:16],
            "order_type": order.order_type, "price": order.price, "sl": order.sl,
            "tp": order.tp, "lots": order.volume, "expiration_epoch": order.expiration_epoch,
            "distance_from_quote": round(distance, context.spec.digits)}


def limits_block(context: MarketContext, settings: V6Settings,
                 remaining_loss_usd: float, *, review: bool = False) -> Document:
    """The packet's `limits`: bounds for the agent's own entry on this bar (a review
    packet allows no new entry)."""
    bounds = entry_limits(context, settings, remaining_loss_usd)
    spec = context.spec
    close = context.as_of_epoch
    return {
        "agent_entry_id": bounds.agent_entry_id,
        "agent_entry_possible": bounds.possible and not review,
        "tick_size": bounds.tick_size, "digits": bounds.digits,
        "buy_limit_max": bounds.buy_limit_max, "sell_limit_min": bounds.sell_limit_min,
        "max_entry_distance": bounds.max_entry_distance, "stop_floor": bounds.stop_floor,
        "max_stop_distance": bounds.max_stop_distance,
        "min_reward_r": bounds.min_reward_r, "max_reward_r": bounds.max_reward_r,
        "default_reward_r": bounds.default_reward_r,
        "risk_budget_usd": bounds.risk_budget_usd,
        "volume_min": spec.volume_min, "lots_step": spec.volume_step,
        "max_lots": settings.max_lots,
        "pending_expiry_epoch": close + settings.pending_expiry_bars * TIMEFRAME_SECONDS["M15"],
        "time_barrier_s": settings.time_barrier_s,
    }
