"""
The blocks only an m1 packet carries (spec section 1): the last hour of closed M1 bars and
`m1_state` (ATR and range of the M1 bars, the 5- and 15-bar moves, the quote rate, and the
distance from the price to each level of the managed trade). Bars that are still forming at
the packet's minute close are never read.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

from ..cycle_types import MarketContext
from ..market.features import bars_closed_by
from ..schemas.operator_minute import MinuteMove, MinuteState
from ..types import TIMEFRAME_SECONDS, Bar

M1_S: Final[int] = TIMEFRAME_SECONDS["M1"]
MINUTE_PACKET_M1_BARS: Final[int] = 60
ATR_PERIOD: Final[int] = 14
SHORT_MOVE_BARS: Final[int] = 5
LONG_MOVE_BARS: Final[int] = 15
RATE_DIGITS: Final[int] = 2


def closed_minutes(context: MarketContext) -> tuple[Bar, ...]:
    """The M1 bars closed by the packet's minute close, oldest first (at most 60)."""
    bars = context.bars.get("M1", ())
    return bars_closed_by(bars, context.as_of_epoch, M1_S)[-MINUTE_PACKET_M1_BARS:]


def atr(bars: Sequence[Bar], period: int = ATR_PERIOD) -> float:
    """Average true range of the last `period` bars; 0 without period + 1 bars."""
    if len(bars) < period + 1:
        return 0.0
    pairs = zip(bars[-period - 1:-1], bars[-period:])
    return sum(max(bar.h, prev.c) - min(bar.l, prev.c) for prev, bar in pairs) / period


def _move(bars: Sequence[Bar], count: int, atr_value: float, digits: int) -> MinuteMove:
    if len(bars) <= count:
        return MinuteMove(bars=count, change=0.0, direction="flat", strength=0.0)
    change = round(bars[-1].c - bars[-1 - count].c, digits)
    direction = "up" if change > 0 else "down" if change < 0 else "flat"
    strength = abs(change) / atr_value if atr_value > 0 else 0.0
    return MinuteMove(bars=count, change=change, direction=direction,
                      strength=round(strength, RATE_DIGITS))


def _range(bars: Sequence[Bar], digits: int) -> float:
    recent = bars[-LONG_MOVE_BARS:]
    if not recent:
        return 0.0
    return round(max(bar.h for bar in recent) - min(bar.l for bar in recent), digits)


def _trade_levels(trade: Mapping[str, object]) -> tuple[bool, dict[str, float]]:
    """(buy?, levels) of a packet position or pending_order block; 0 means no level."""
    plan = trade.get("plan")
    ladder = plan if isinstance(plan, Mapping) else {}
    position = "open_price" in trade
    buy = (trade.get("side") == "buy" if position
           else str(trade.get("order_type")).startswith("BUY"))
    raw = {"entry": trade.get("open_price" if position else "price"), "sl": trade.get("sl"),
           "tp1": ladder.get("tp1"), "tp2": ladder.get("tp2"), "tp3": trade.get("tp")}
    return buy, {key: float(value) for key, value in raw.items()
                 if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0}


def _distances(context: MarketContext, trade: Mapping[str, object] | None) -> dict[str, float]:
    """Level minus the price that triggers it: a position exits on the bid (buy) or the ask
    (sell); a resting buy order fills on the ask, a sell order on the bid."""
    if trade is None:
        return {}
    buy, levels = _trade_levels(trade)
    quote, digits = context.quote, context.spec.digits
    if "open_price" in trade:
        price = quote.bid if buy else quote.ask
    else:
        price = quote.ask if buy else quote.bid
    return {key: round(level - price, digits) for key, level in levels.items()}


def minute_state(context: MarketContext, trade: Mapping[str, object] | None) -> MinuteState:
    """`m1_state` of the packet; `trade` is its position or pending_order block, if any."""
    bars = closed_minutes(context)
    digits = context.spec.digits
    atr_value = atr(bars)
    ticks = context.ticks
    rate = ticks.quote_count / ticks.window_s if ticks.window_s > 0 else 0.0
    return MinuteState(
        atr_m1=round(atr_value, digits), range_15=_range(bars, digits),
        last_5=_move(bars, SHORT_MOVE_BARS, atr_value, digits),
        last_15=_move(bars, LONG_MOVE_BARS, atr_value, digits),
        quotes_per_s=round(rate, RATE_DIGITS), max_gap_ms=int(ticks.max_gap_ms),
        distances=_distances(context, trade))
