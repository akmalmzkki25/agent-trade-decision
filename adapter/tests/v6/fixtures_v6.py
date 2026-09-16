"""
Deterministic bar generators for V6 tests.

Every generator returns `tuple[Bar, ...]`, oldest first, with open times on the
timeframe grid so the output also passes `validate_bar_rows`. Randomness always
comes from a seeded `random.Random`, never the module-level generator, so a
failing property test can be replayed from its seed.
"""

from __future__ import annotations

import random
from dataclasses import replace
from typing import Any, Final, Literal, Sequence

from app.v6.types import TIMEFRAME_SECONDS, Bar

# Monday 2026-09-14 00:00 UTC: aligned to every grid, D1 included.
DEFAULT_START_EPOCH: Final[int] = 1_789_344_000
DEFAULT_PRICE: Final[float] = 4300.0
DEFAULT_WICK: Final[float] = 0.3
DEFAULT_TICK_VOLUME: Final[int] = 100
DEFAULT_SPREAD_POINTS: Final[int] = 20
PRICE_DIGITS: Final[int] = 2
TICK_VOLUME_SPREAD: Final[float] = 0.3
SECONDS_PER_DAY: Final[int] = 86_400

Direction = Literal["up", "down"]


def tf_step(tf: str) -> int:
    if tf not in TIMEFRAME_SECONDS:
        raise ValueError(f"unknown timeframe {tf!r}")
    return TIMEFRAME_SECONDS[tf]


def _check_start(start_t: int, tf: str) -> int:
    step = tf_step(tf)
    if start_t % step != 0:
        raise ValueError(f"start_t={start_t} is not aligned to the {tf} grid")
    return step


def _px(value: float) -> float:
    return round(value, PRICE_DIGITS)


def bars_from_ohlc(
    rows: Sequence[tuple[float, float, float, float]],
    *,
    start_t: int = DEFAULT_START_EPOCH,
    tf: str = "M15",
    tv: int = DEFAULT_TICK_VOLUME,
) -> tuple[Bar, ...]:
    """Hand-written (o, h, l, c) rows placed on consecutive grid slots."""
    step = _check_start(start_t, tf)
    return tuple(
        Bar(t=start_t + i * step, o=o, h=h, l=lo, c=c, tv=tv, spr=DEFAULT_SPREAD_POINTS)
        for i, (o, h, lo, c) in enumerate(rows)
    )


def bars_from_closes(
    closes: Sequence[float],
    *,
    start_t: int = DEFAULT_START_EPOCH,
    tf: str = "M15",
    wick: float = DEFAULT_WICK,
    tv: int = DEFAULT_TICK_VOLUME,
) -> tuple[Bar, ...]:
    """Each bar opens at the previous close; wicks extend `wick` beyond the body."""
    if wick < 0:
        raise ValueError("wick must be non-negative")
    rows = []
    previous = closes[0] if closes else 0.0
    for close in closes:
        o, c = _px(previous), _px(close)
        rows.append((o, _px(max(o, c) + wick), _px(min(o, c) - wick), c))
        previous = close
    return bars_from_ohlc(rows, start_t=start_t, tf=tf, tv=tv)


def trend_closes(
    n: int,
    *,
    direction: Direction = "up",
    step: float = 1.0,
    leg: int = 6,
    pullback: int = 2,
    start_price: float = DEFAULT_PRICE,
) -> tuple[float, ...]:
    """`leg` bars with the trend then `pullback` bars against it, repeated."""
    if leg <= pullback or pullback < 0:
        raise ValueError("a trend needs leg > pullback >= 0")
    sign = 1.0 if direction == "up" else -1.0
    closes = []
    price = start_price
    for i in range(n):
        with_trend = i % (leg + pullback) < leg
        price += sign * step if with_trend else -sign * step
        closes.append(price)
    return tuple(closes)


def trend_bars(
    n: int, *, tf: str = "M15", start_t: int = DEFAULT_START_EPOCH, **kwargs: Any
) -> tuple[Bar, ...]:
    """Higher highs and higher lows (or the mirror image for `direction="down"`)."""
    return bars_from_closes(trend_closes(n, **kwargs), start_t=start_t, tf=tf)


def range_closes(
    n: int,
    *,
    mid: float = DEFAULT_PRICE,
    amplitude: float = 5.0,
    period: int = 8,
) -> tuple[float, ...]:
    """Triangle wave between mid +/- amplitude; `period=2` alternates every bar."""
    if period < 2 or period % 2:
        raise ValueError("period must be an even number >= 2")
    half = period // 2
    closes = []
    for i in range(n):
        k = i % period
        offset = k if k <= half else period - k
        closes.append(mid - amplitude + 2.0 * amplitude * offset / half)
    return tuple(closes)


def range_bars(
    n: int, *, tf: str = "M15", start_t: int = DEFAULT_START_EPOCH, **kwargs: Any
) -> tuple[Bar, ...]:
    """Equal highs and equal lows: no directional structure."""
    return bars_from_closes(range_closes(n, **kwargs), start_t=start_t, tf=tf)


def _random_bar(rng: random.Random, t: int, o: float, sigma: float, wick: float) -> Bar:
    c = _px(o + rng.gauss(0.0, sigma))
    h = _px(max(o, c) + abs(rng.gauss(0.0, wick)))
    lo = _px(min(o, c) - abs(rng.gauss(0.0, wick)))
    tv = max(1, int(rng.gauss(DEFAULT_TICK_VOLUME, DEFAULT_TICK_VOLUME * TICK_VOLUME_SPREAD)))
    return Bar(t=t, o=o, h=h, l=lo, c=c, tv=tv, spr=DEFAULT_SPREAD_POINTS)


def random_walk_bars(
    n: int,
    *,
    seed: int,
    sigma: float = 1.0,
    wick: float = DEFAULT_WICK,
    start_price: float = DEFAULT_PRICE,
    start_t: int = DEFAULT_START_EPOCH,
    tf: str = "M15",
) -> tuple[Bar, ...]:
    """Gaussian random walk; each bar opens at the previous close."""
    step = _check_start(start_t, tf)
    rng = random.Random(seed)
    bars = []
    price = _px(start_price)
    for i in range(n):
        bar = _random_bar(rng, start_t + i * step, price, sigma, wick)
        bars.append(bar)
        price = bar.c
    return tuple(bars)


def _shift(bar: Bar, dt: int, dp: float) -> Bar:
    return replace(
        bar, t=bar.t + dt,
        o=_px(bar.o + dp), h=_px(bar.h + dp), l=_px(bar.l + dp), c=_px(bar.c + dp),
    )


def inject_displacement(
    bars: Sequence[Bar],
    index: int,
    size: float,
    *,
    wick: float = DEFAULT_WICK,
    tv_multiplier: int = 3,
) -> tuple[Bar, ...]:
    """
    Replace bar `index` with a wide-body bar of signed `size` that opens at the
    previous close; later bars move with it so the series stays continuous.
    """
    if not 0 <= index < len(bars):
        raise IndexError(f"index {index} outside 0..{len(bars) - 1}")
    original = bars[index]
    o = bars[index - 1].c if index > 0 else original.o
    c = _px(o + size)
    spike = replace(
        original, o=o, c=c, h=_px(max(o, c) + wick), l=_px(min(o, c) - wick),
        tv=original.tv * tv_multiplier,
    )
    delta = c - original.c
    tail = tuple(_shift(bar, 0, delta) for bar in bars[index + 1:])
    return tuple(bars[:index]) + (spike,) + tail


def insert_gap(
    bars: Sequence[Bar],
    after_index: int,
    *,
    missing_bars: int = 0,
    price_jump: float = 0.0,
    tf: str = "M15",
) -> tuple[Bar, ...]:
    """Bars after `after_index` move `missing_bars` slots later and `price_jump` away."""
    if missing_bars < 0:
        raise ValueError("missing_bars must be non-negative")
    if not 0 <= after_index < len(bars):
        raise IndexError(f"after_index {after_index} outside 0..{len(bars) - 1}")
    dt = missing_bars * tf_step(tf)
    tail = tuple(_shift(bar, dt, price_jump) for bar in bars[after_index + 1:])
    return tuple(bars[:after_index + 1]) + tail


def to_rows(bars: Sequence[Bar]) -> list[tuple[int, float, float, float, float, int, int]]:
    """Wire-format rows, for feeding fixtures through the snapshot validators."""
    return [(b.t, b.o, b.h, b.l, b.c, b.tv, b.spr) for b in bars]
