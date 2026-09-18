"""
The analysis blocks of the operator packet: closed bars, reference levels, the
limits of an agent-designed entry, and the resting order or open position a
management packet asks about (user decisions 2026-09-17).

Everything is built from what the cycle already knows at the bar close: the bars
in the MarketContext (closed bars only), confirmed pivots (a pivot counts once
its confirmation bar has closed), `plan_rules.plan_bounds` and the intent the
adapter stored for the resting order or open position.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Final

from ..config import V6Settings
from ..cycle_types import MarketContext
from ..ledger_intents import IntentRecord
from ..market.levels import Pivot, confirmed_pivots, prior_day_levels
from ..schemas.intent import intent_id_from_comment
from ..schemas.operator_parts import (
    M15_PACKET_M1_BARS, MAX_PACKET_D1_BARS, MAX_PACKET_H1_BARS, MAX_PACKET_M5_BARS,
    MAX_PACKET_M15_BARS, MAX_PACKET_PIVOTS,
)
from ..setups.base import full_days
from ..types import TIMEFRAME_SECONDS, Bar
from .minute_packet import MINUTE_PACKET_M1_BARS, closed_minutes
from .plan_rules import plan_bounds

Document = dict[str, object]

PIVOT_STRENGTH: Final[int] = 2
SECONDS_PER_MINUTE: Final[int] = 60
R_DECIMALS: Final[int] = 3
ROUND_STEP_SMALL: Final[float] = 10.0
ROUND_STEP_LARGE: Final[float] = 50.0
BAR_LIMITS: Final[tuple[tuple[str, int], ...]] = (
    ("M1", M15_PACKET_M1_BARS), ("M5", MAX_PACKET_M5_BARS), ("M15", MAX_PACKET_M15_BARS),
    ("H1", MAX_PACKET_H1_BARS), ("D1", MAX_PACKET_D1_BARS))


def _finite(*values: float) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


def compact_bars(bars: Sequence[Bar], limit: int) -> list[list[int | float]]:
    rows = [[int(bar.t), float(bar.o), float(bar.h), float(bar.l), float(bar.c)]
            for bar in bars if _finite(bar.o, bar.h, bar.l, bar.c)]
    return rows[-limit:]


def bars_block(context: MarketContext, kind: str = "m15") -> Document:
    """An m15 packet shows every timeframe; an m1 packet only the last hour of M1 bars."""
    if kind == "m1":
        empty: Document = {tf: [] for tf, _ in BAR_LIMITS}
        return {**empty, "M1": compact_bars(closed_minutes(context), MINUTE_PACKET_M1_BARS)}
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


def _record_plan(record: IntentRecord | None, step: int) -> Document:
    """The ladder the adapter stored for the trade (zeros when it has none)."""
    if record is None:
        return {"tp1": 0.0, "tp2": 0.0, "sl_after_tp1": 0.0, "sl_after_tp2": 0.0,
                "step": step, "time_limit_min": 0}
    return {"tp1": record.tp1, "tp2": record.tp2, "sl_after_tp1": record.sl_after_tp1,
            "sl_after_tp2": record.sl_after_tp2, "step": max(step, record.plan_step),
            "time_limit_min": record.time_barrier_s // SECONDS_PER_MINUTE}


def _intent_of(comment: str, record: IntentRecord | None) -> str:
    """The intent id in the MT5 comment, else the stored intent's (a broker may rewrite
    comments)."""
    return intent_id_from_comment(comment) or ("" if record is None else record.intent_id)


def pending_order_block(context: MarketContext, record: IntentRecord | None) -> Document | None:
    """The first resting V6 order and its plan (None when nothing rests).

    `distance_from_quote` is how far the market still has to travel to fill the order.
    """
    if not context.pending_orders:
        return None
    order = context.pending_orders[0]
    quote = context.quote
    buying = order.order_type.startswith("BUY")
    reference = quote.ask if buying else quote.bid
    beyond = order.order_type.endswith("STOP")
    distance = (order.price - reference) if buying == beyond else (reference - order.price)
    return {"ticket": order.ticket, "intent_id": _intent_of(order.comment, record),
            "order_type": order.order_type, "price": order.price, "sl": order.sl,
            "tp": order.tp, "lots": order.volume, "expiration_epoch": order.expiration_epoch,
            "distance_from_quote": round(distance, context.spec.digits),
            "plan": _record_plan(record, 0)}


def position_block(context: MarketContext, record: IntentRecord | None,
                   default_barrier_s: int = 0) -> Document | None:
    """The first open V6 position with its initial risk and plan (None when flat).

    The SL+ step is the furthest one the EA (snapshot) or its step reports (the stored
    intent) know of; the time limit is the EA's, else the open time plus the stored
    holding time, else plus the default barrier.
    """
    if not context.positions:
        return None
    position = context.positions[0]
    sign = 1 if position.side == "buy" else -1
    mark = context.quote.bid if sign > 0 else context.quote.ask
    initial_sl = record.sl if record is not None else position.sl
    risk = abs(position.price_open - initial_sl)
    barrier = record.time_barrier_s if record is not None else default_barrier_s
    minutes = max(0, context.as_of_epoch - position.open_epoch) / SECONDS_PER_MINUTE
    return {"ticket": position.ticket,
            "intent_id": _intent_of(position.comment, record),
            "side": position.side, "lots": position.volume,
            "open_price": position.price_open, "open_epoch": position.open_epoch,
            "sl": position.sl, "tp": position.tp, "initial_sl": initial_sl,
            "profit": position.profit,
            "r_now": round(sign * (mark - position.price_open) / risk, R_DECIMALS)
            if risk > 0 else 0.0,
            "mae_points": position.mae_points, "mfe_points": position.mfe_points,
            "minutes_open": round(minutes, 2),
            "time_limit_epoch": position.time_limit_epoch or position.open_epoch + barrier,
            "plan": _record_plan(record, position.plan_step)}


def limits_block(context: MarketContext, settings: V6Settings,
                 remaining_loss_usd: float, *, state: str = "flat") -> Document:
    """The packet's `limits`: bounds for the agent's own entry on this bar (a management
    packet allows no new entry) and the windows of an agent plan."""
    bounds = plan_bounds(context, settings, remaining_loss_usd)
    entry = bounds.entry
    close = context.as_of_epoch
    return {
        "agent_entry_id": entry.agent_entry_id,
        "agent_entry_possible": entry.possible and state == "flat",
        "tick_size": entry.tick_size, "digits": entry.digits,
        "buy_limit_max": entry.buy_limit_max, "sell_limit_min": entry.sell_limit_min,
        "max_entry_distance": entry.max_entry_distance, "stop_floor": entry.stop_floor,
        "max_stop_distance": entry.max_stop_distance,
        "min_reward_r": entry.min_reward_r, "max_reward_r": entry.max_reward_r,
        "default_reward_r": entry.default_reward_r,
        "risk_budget_usd": entry.risk_budget_usd,
        "volume_min": bounds.volume_min, "lots_step": bounds.lots_step,
        "max_lots": bounds.max_lots,
        "pending_expiry_epoch": close + settings.pending_expiry_bars * TIMEFRAME_SECONDS["M15"],
        "time_barrier_s": settings.time_barrier_s,
        "buy_stop_min": entry.buy_stop_min, "sell_stop_max": entry.sell_stop_max,
        "modify_distance": entry.modify_distance, "min_tp1_r": bounds.min_tp1_r,
        "time_limit_min_minutes": bounds.time_limit_min,
        "time_limit_max_minutes": bounds.time_limit_max,
        "pending_expiry_min_minutes": bounds.pending_expiry_min,
        "pending_expiry_max_minutes": bounds.pending_expiry_max,
    }
