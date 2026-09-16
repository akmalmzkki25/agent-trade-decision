"""
Hand-built bar scenarios for the setup detector tests.

All scenarios sit on Wednesday 2026-09-16 (UTC). In September London is on BST and
New York on EDT, so the London opening range is 07:00-07:30 UTC and the New York one
13:30-14:00 UTC. Quiet bars are flat (open == close) with a fixed half range, so
ATR(14) and the same-slot true range are exact round numbers.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping, Sequence
from typing import Final

from app.v6.setups import SetupInput
from app.v6.types import Bar

from .fixtures_v6 import DEFAULT_START_EPOCH

DAY: Final[int] = 86_400
HOUR: Final[int] = 3_600
M5: Final[int] = 300
M15: Final[int] = 900
WED: Final[int] = DEFAULT_START_EPOCH + 2 * DAY      # 2026-09-16 00:00 UTC
TUE: Final[int] = WED - DAY
SAT: Final[int] = WED + 3 * DAY
LONDON_OR_START: Final[int] = WED + 7 * HOUR
LONDON_OR_END: Final[int] = LONDON_OR_START + 2 * M15
NY_OR_START: Final[int] = WED + 13 * HOUR + 30 * 60
NY_OR_END: Final[int] = NY_OR_START + 2 * M15
NOON: Final[int] = WED + 12 * HOUR
SPREAD: Final[float] = 0.2
BUFFER: Final[float] = 0.6            # 3 x SPREAD
POINT: Final[float] = 0.01
DIGITS: Final[int] = 2
TV: Final[int] = 100


def bar(t: int, o: float, h: float, lo: float, c: float, tv: int = TV) -> Bar:
    return Bar(t=t, o=o, h=h, l=lo, c=c, tv=tv, spr=20)


def flat(start: int, end: int, *, price: float, half: float = 0.5, step: int = M15,
         tv: Iterable[int] | None = None) -> tuple[Bar, ...]:
    """Flat bars on [start, end) with a true range of exactly 2 x half."""
    times = range(start, end, step)
    volumes = [TV] * len(times) if tv is None else list(tv)
    return tuple(bar(t, price, price + half, price - half, price, v)
                 for t, v in zip(times, volumes))


def day_bar(t: int, high: float, low: float) -> Bar:
    mid = (high + low) / 2
    return bar(t, mid, high, low, mid)


def flat_days(end: int, days: int, *, high: float, low: float) -> tuple[Bar, ...]:
    return tuple(day_bar(end - (days - i) * DAY, high, low) for i in range(days))


def setup_input(as_of: int, spread: float = SPREAD, **bars: Sequence[Bar]) -> SetupInput:
    return SetupInput(as_of_epoch=as_of, bars=bars, spread_price=spread, point=POINT,
                      digits=DIGITS)


def as_of_for(trigger: Bar) -> int:
    return trigger.t + M15


# --- displacement ------------------------------------------------------------------

def displacement_up(trigger: Bar | None = None, *, history_start: int = WED,
                    prior_day: tuple[Bar, ...] = (day_bar(TUE, 4310.0, 4280.0),),
                    **extra: Sequence[Bar]) -> SetupInput:
    """Flat 4305 until noon, then a bar through the 4310 prior-day high."""
    trig = trigger or bar(NOON, 4305.0, 4313.5, 4304.5, 4313.0)
    m15 = flat(history_start, trig.t, price=4305.0) + (trig,)
    return setup_input(as_of_for(trig), M15=m15, D1=prior_day, **extra)


def displacement_down() -> SetupInput:
    """Flat 4305 until noon, then a bar through the 4300 prior-day low."""
    trig = bar(NOON, 4305.0, 4305.5, 4296.5, 4297.0)
    m15 = flat(WED, trig.t, price=4305.0) + (trig,)
    return setup_input(as_of_for(trig), M15=m15, D1=(day_bar(TUE, 4330.0, 4300.0),))


def h1_swing_high(start: int, price: float) -> tuple[Bar, ...]:
    """Six H1 bars with one strength-2 pivot high at index 2 (`price`)."""
    below = price - 5.0
    rows = ((below, below + 1, below - 1, below), (below, below + 2, below - 1, below + 1),
            (below + 1, price, below, below + 2), (below + 2, below + 3, below - 1, below),
            (below, below + 2, below - 2, below - 1), (below - 1, below + 1, below - 2, below))
    return tuple(bar(start + i * HOUR, *row) for i, row in enumerate(rows))


# --- opening range, ORB and retest ----------------------------------------------------

def london_range_bars() -> tuple[Bar, ...]:
    """Flat 4303 from midnight, then the London range: high 4306, low 4300."""
    return flat(WED, LONDON_OR_START, price=4303.0) + (
        bar(LONDON_OR_START, 4303.0, 4306.0, 4300.0, 4304.0),
        bar(LONDON_OR_START + M15, 4304.0, 4305.5, 4301.0, 4305.0),
    )


def after_range(*rows: tuple[float, float, float, float], start: int = LONDON_OR_END
                ) -> tuple[Bar, ...]:
    return tuple(bar(start + i * M15, *row) for i, row in enumerate(rows))


def orb_input(*rows: tuple[float, float, float, float], **extra: Sequence[Bar]) -> SetupInput:
    """London range, then `rows` from 07:30; the last row is the trigger."""
    m15 = london_range_bars() + after_range(*rows)
    return setup_input(as_of_for(m15[-1]), M15=m15, **extra)


ORB_QUIET: Final[tuple[tuple[float, float, float, float], ...]] = (
    (4305.0, 4305.8, 4304.5, 4305.5), (4305.5, 4305.9, 4305.0, 4305.6))
ORB_UP: Final[tuple[float, float, float, float]] = (4305.6, 4308.3, 4305.4, 4308.0)
ORB_DOWN: Final[tuple[float, float, float, float]] = (4305.6, 4305.7, 4298.8, 4299.0)

RETEST_BREAK: Final[tuple[float, float, float, float]] = (4305.0, 4307.5, 4304.8, 4307.2)
RETEST_PEAK: Final[tuple[float, float, float, float]] = (4307.2, 4308.0, 4307.0, 4307.5)
RETEST_TRIGGER: Final[tuple[float, float, float, float]] = (4307.0, 4307.9, 4306.2, 4307.4)

RETEST_DOWN: Final[tuple[tuple[float, float, float, float], ...]] = (
    (4301.0, 4301.2, 4298.5, 4298.8), (4298.8, 4299.0, 4298.0, 4298.5),
    (4298.9, 4299.8, 4298.4, 4298.6))


# --- engulfing ----------------------------------------------------------------------

def engulfing_input(previous: tuple[float, float, float, float],
                    trigger: tuple[float, float, float, float], *, price: float = 4304.0,
                    half: float = 0.5, pdh: float = 4330.0, pdl: float = 4300.0,
                    **extra: Sequence[Bar]) -> SetupInput:
    """Flat `price` until 11:45, then `previous` at 11:45 and `trigger` at noon."""
    m15 = flat(WED, NOON - M15, price=price, half=half) + (
        bar(NOON - M15, *previous), bar(NOON, *trigger))
    return setup_input(as_of_for(m15[-1]), M15=m15, D1=(day_bar(TUE, pdh, pdl),), **extra)


BULL_PREV: Final[tuple[float, float, float, float]] = (4304.0, 4304.2, 4300.6, 4301.0)
BULL_TRIGGER: Final[tuple[float, float, float, float]] = (4300.8, 4305.5, 4300.3, 4305.0)
BEAR_PREV: Final[tuple[float, float, float, float]] = (4306.0, 4309.4, 4305.8, 4309.0)
BEAR_TRIGGER: Final[tuple[float, float, float, float]] = (4309.2, 4309.7, 4304.5, 4305.0)


# --- multi-timeframe random walk ----------------------------------------------------

def _walk_bar(rng: random.Random, t: int, o: float, sigma: float, jump_p: float) -> Bar:
    move = rng.gauss(0.0, sigma)
    if rng.random() < jump_p:
        move += rng.choice((-1.0, 1.0)) * rng.uniform(3.0, 7.0)
    c = round(o + move, 2)
    h = round(max(o, c) + abs(rng.gauss(0.0, 0.15)), 2)
    lo = round(min(o, c) - abs(rng.gauss(0.0, 0.15)), 2)
    return bar(t, o, h, lo, c, max(1, int(rng.gauss(TV, 30))))


def m5_jump_walk(n: int, *, seed: int, start: int, sigma: float = 0.35,
                 jump_p: float = 0.015) -> tuple[Bar, ...]:
    """M5 random walk with occasional 3-7 dollar jumps (seeded, replayable)."""
    rng = random.Random(seed)
    bars, price = [], 4300.0
    for i in range(n):
        new = _walk_bar(rng, start + i * M5, price, sigma, jump_p)
        bars.append(new)
        price = new.c
    return tuple(bars)


def aggregate(bars: Sequence[Bar], step: int) -> tuple[Bar, ...]:
    """OHLC of `bars` grouped on the `step` grid (tick volumes summed)."""
    groups: dict[int, list[Bar]] = {}
    for item in bars:
        groups.setdefault(item.t // step * step, []).append(item)
    return tuple(
        bar(t, rows[0].o, max(r.h for r in rows), min(r.l for r in rows), rows[-1].c,
            sum(r.tv for r in rows))
        for t, rows in sorted(groups.items())
    )


def multi_timeframe(m5: Sequence[Bar]) -> Mapping[str, tuple[Bar, ...]]:
    return {"M5": tuple(m5), "M15": aggregate(m5, M15), "H1": aggregate(m5, HOUR),
            "D1": aggregate(m5, DAY)}
