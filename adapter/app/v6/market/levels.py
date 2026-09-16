"""
Price structure on closed bars: confirmed pivots, swing structure and levels.

A pivot needs `strength` closed bars on each side, so it is only knowable once
the bar `strength` to its right has CLOSED. Every pivot carries that moment as
`confirmed_at`, and anything that reasons about structure "as of" a time must
filter on it; using a pivot earlier is look-ahead.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Literal, Sequence

from ..types import Bar

PivotKind = Literal["high", "low"]
Structure = Literal["up", "down", "range", "unknown"]

DEFAULT_ROUND_STEP: Final[float] = 50.0
_HALF: Final[float] = 0.5
_PIVOTS_PER_SIDE: Final[int] = 2


@dataclass(frozen=True)
class Pivot:
    """A swing extreme. `t` is the pivot bar's open; `confirmed_at` an epoch close."""

    kind: PivotKind
    index: int
    t: int
    price: float
    confirmed_at: int


def _is_pivot_high(bars: Sequence[Bar], i: int, strength: int) -> bool:
    # Strict on the left, inclusive on the right: a flat top yields one pivot,
    # at its first bar, instead of none.
    price = bars[i].h
    left = all(price > bars[j].h for j in range(i - strength, i))
    right = all(price >= bars[j].h for j in range(i + 1, i + strength + 1))
    return left and right


def _is_pivot_low(bars: Sequence[Bar], i: int, strength: int) -> bool:
    price = bars[i].l
    left = all(price < bars[j].l for j in range(i - strength, i))
    right = all(price <= bars[j].l for j in range(i + 1, i + strength + 1))
    return left and right


def confirmed_pivots(bars: Sequence[Bar], strength: int, tf_seconds: int) -> tuple[Pivot, ...]:
    """
    Fractal pivots of `strength` bars per side, oldest first (a high before a
    low on the same outside bar). Bars without a full left window are skipped,
    so the result for a prefix is exactly the full result filtered by
    `confirmed_at`.
    """
    if strength < 1:
        raise ValueError("strength must be >= 1")
    if tf_seconds <= 0:
        raise ValueError("tf_seconds must be positive")
    found: list[Pivot] = []
    for i in range(strength, len(bars) - strength):
        confirmed_at = bars[i + strength].t + tf_seconds
        if _is_pivot_high(bars, i, strength):
            found.append(Pivot("high", i, bars[i].t, bars[i].h, confirmed_at))
        if _is_pivot_low(bars, i, strength):
            found.append(Pivot("low", i, bars[i].t, bars[i].l, confirmed_at))
    return tuple(found)


def _last_two(pivots: Sequence[Pivot], kind: PivotKind) -> tuple[float, ...]:
    prices = [p.price for p in pivots if p.kind == kind]
    return tuple(prices[-_PIVOTS_PER_SIDE:])


def swing_structure(pivots: Sequence[Pivot], as_of_epoch: int) -> Structure:
    """
    "up" for a higher high and higher low, "down" for a lower high and lower
    low, "range" for any other pair, "unknown" with fewer than two of either.
    Only pivots confirmed by `as_of_epoch` are considered.
    """
    visible = (p for p in pivots if p.confirmed_at <= as_of_epoch)
    known = sorted(visible, key=lambda p: (p.t, p.kind))
    highs = _last_two(known, "high")
    lows = _last_two(known, "low")
    if len(highs) < _PIVOTS_PER_SIDE or len(lows) < _PIVOTS_PER_SIDE:
        return "unknown"
    (prev_high, last_high), (prev_low, last_low) = highs, lows
    if last_high > prev_high and last_low > prev_low:
        return "up"
    if last_high < prev_high and last_low < prev_low:
        return "down"
    return "range"


def prior_day_levels(bars_d1: Sequence[Bar]) -> tuple[float, float] | None:
    """(high, low) of the last closed D1 bar; the caller slices to closed bars."""
    if not bars_d1:
        return None
    last = bars_d1[-1]
    return last.h, last.l


def round_level_distance(price: float, step: float = DEFAULT_ROUND_STEP) -> tuple[float, float]:
    """
    Nearest multiple of `step` and `price - level` (positive above the level).
    A price exactly halfway rounds up, unlike Python's banker's `round`.
    """
    if not math.isfinite(price):
        raise ValueError("price must be finite")
    if not (math.isfinite(step) and step > 0):
        raise ValueError("step must be a positive finite number")
    level = math.floor(price / step + _HALF) * step
    return level, price - level
