"""
Shared input and helpers for the deterministic setup detectors (plan §2, PA row).

Every detector reads a `SetupInput`: the bars that had CLOSED at `as_of_epoch` (the
close of the cycle's M15 bar), the live spread and the price grid. Anything used to
judge the trigger bar - levels, volatility unit, higher-timeframe structure - is cut
further, to what was known when the trigger bar OPENED, so a bar can never define the
level it breaks. Every lookback is a fixed time or bar window, so the result does not
depend on how much history the BarStore happens to hold.

Thresholds cite the evidence base in knowledge/ (kn/NN = knowledge/NN-*.md).
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Final, Literal, TypeGuard

from ..cycle_codes import SetupName, candidate_id_for
from ..cycle_types import MarketContext
from ..market.features import atr_wilder, bars_closed_by, same_slot_true_range
from ..market.levels import Structure, confirmed_pivots, prior_day_levels, swing_structure
from ..market.sessions import MAX_SUPPORTED_EPOCH, session_state
from ..types import TIMEFRAME_SECONDS, Bar, Candidate, Side

M5_S: Final[int] = TIMEFRAME_SECONDS["M5"]
M15_S: Final[int] = TIMEFRAME_SECONDS["M15"]
H1_S: Final[int] = TIMEFRAME_SECONDS["H1"]
D1_S: Final[int] = TIMEFRAME_SECONDS["D1"]
SIDES: Final[tuple[Side, ...]] = ("buy", "sell")
DIRECTION: Final[Mapping[Side, int]] = MappingProxyType({"buy": 1, "sell": -1})
MAX_DIGITS: Final[int] = 8
FEATURE_DECIMALS: Final[int] = 6

# kn/06 §1 and §4: every ATR in the setup rules is ATR(14).
ATR_PERIOD: Final[int] = 14
# Wilder memory after 100 bars is (13/14)^86 < 0.2%, so a fixed window makes the value
# independent of how much history is stored.
ATR_WINDOW_BARS: Final[int] = 100
# Plan §5: the EA backfills 60 D1 bars; (13/14)^46 ~ 3% residual seed weight.
D1_ATR_WINDOW_BARS: Final[int] = 60
# A D1 bar with less open market than this is a session stub, not a day: on a GMT+0
# server the Sunday reopen (21:00/22:00 UTC) builds a 2-3 h "Sunday" bar. Its range
# is neither a prior-day level (kn/06) nor a daily ATR sample.
MIN_D1_OPEN_HOURS: Final[int] = 12
HOURS_PER_DAY: Final[int] = 24
D1_OPEN_HOURS_CACHE: Final[int] = 4096
# kn/02 §1 / kn/15 §3: same-slot true range over the last ~10 sessions replaces
# ATR(14) on M15 (2.85x vs 1.08x calibration spread across the day).
SLOT_SESSIONS: Final[int] = 10
# Half the kn/02 sample still averages the daily noise; below it the unit falls back
# to ATR(14, M15) and the candidate says so (RC_VOL_ATR_FALLBACK).
MIN_SLOT_SESSIONS: Final[int] = 5
# Ten sessions span two weekends; three weeks leave room for a holiday (bar_store).
SLOT_LOOKBACK_S: Final[int] = 21 * D1_S
# kn/05 §11: Williams fractal, N = 2 closed bars on each side before a pivot counts.
H1_PIVOT_STRENGTH: Final[int] = 2
# kn/06 (intro): levels come from the higher timeframe; two days of H1 swings.
H1_LEVEL_LOOKBACK_S: Final[int] = 48 * H1_S
# Two highs and two lows are needed for a swing structure; five days of H1 hold them.
H1_STRUCTURE_LOOKBACK_S: Final[int] = 5 * D1_S
# kn/06 §7.2 and kn/04: a close is through a level only when |close - L| >= 3 x spread.
CLOSE_BEYOND_SPREADS: Final[float] = 3.0
# kn/06 §4 and §6: stops sit beyond the structural extreme by max(3 x spread, 0.15 x ATR).
STOP_BUFFER_SPREADS: Final[float] = 3.0
STOP_BUFFER_ATR: Final[float] = 0.15
# kn/06 §1 and §3: the stop is also at least 1.0 x ATR(14) beyond the entry zone;
# the wider of the two is used.
STOP_MIN_ATR: Final[float] = 1.0
# kn/06 §1: rest a limit at the 50% retracement of the trigger body instead of paying
# the whole move at market (Barber et al.: losses trace to aggressive orders).
BODY_RETRACE: Final[float] = 0.5

# --- reason codes shared by several detectors ---------------------------------------
# Values that match PriceActionReason are deliberate: the rules PA desk can pass them on.
RC_CONFIRMED_CLOSE: Final[str] = "CONFIRMED_CLOSE"
RC_LEVEL_CONFLUENCE: Final[str] = "LEVEL_CONFLUENCE"
RC_HTF_ALIGNED: Final[str] = "HTF_ALIGNED"
RC_HTF_OPPOSED: Final[str] = "HTF_OPPOSED"
RC_VOL_SLOT: Final[str] = "VOL_SLOT_TR"
RC_VOL_ATR_FALLBACK: Final[str] = "VOL_ATR_FALLBACK"

# --- candidate feature keys shared by several detectors -----------------------------
CF_RANGE_VOL: Final[str] = "range_vol"          # trigger range / volatility unit
CF_BODY_RATIO: Final[str] = "body_ratio"        # trigger body / range
CF_VOL_UNIT: Final[str] = "vol_unit_m15"        # slot true range (or ATR fallback), price
CF_ATR_M5: Final[str] = "atr_m5"                # ATR(14, M5), price
CF_BAR_RANGE: Final[str] = "bar_range"          # trigger range, price
CF_LEVEL_PRICE: Final[str] = "level_price"      # the level the setup is anchored on
CF_CLOSE_BEYOND: Final[str] = "close_beyond"    # signed close distance past the level
CF_STOP_BUFFER: Final[str] = "stop_buffer"      # buffer added beyond the extreme, price

LevelKind = Literal["pdh", "pdl", "h1_high", "h1_low"]
LEVEL_CODES: Final[Mapping[LevelKind, str]] = MappingProxyType({
    "pdh": "LEVEL_PDH", "pdl": "LEVEL_PDL",
    "h1_high": "LEVEL_H1_PIVOT_HIGH", "h1_low": "LEVEL_H1_PIVOT_LOW",
})
# Prior-day extremes outrank H1 swings when one close crosses both.
_LEVEL_RANK: Final[Mapping[LevelKind, int]] = MappingProxyType(
    {"pdh": 0, "pdl": 0, "h1_high": 1, "h1_low": 1})


@dataclass(frozen=True)
class Level:
    """A reference price and the epoch from which it was knowable."""

    kind: LevelKind
    price: float
    known_at: int

    @property
    def code(self) -> str:
        return LEVEL_CODES[self.kind]


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _check_rows(tf: str, rows: Sequence[Bar]) -> tuple[Bar, ...]:
    if tf not in TIMEFRAME_SECONDS:
        raise ValueError(f"unknown timeframe {tf!r}")
    ordered = tuple(rows)
    if any(a.t >= b.t for a, b in zip(ordered, ordered[1:])):
        raise ValueError(f"{tf}: bars must be oldest first with unique open times")
    return ordered


@dataclass(frozen=True)
class SetupInput:
    """What a detector may see at `as_of_epoch`; `bars` keeps CLOSED bars only."""

    as_of_epoch: int
    bars: Mapping[str, tuple[Bar, ...]]
    spread_price: float
    point: float
    digits: int

    def __post_init__(self) -> None:
        if not _is_int(self.as_of_epoch) or not M15_S < self.as_of_epoch <= MAX_SUPPORTED_EPOCH:
            raise ValueError(f"as_of_epoch must be an int epoch in ({M15_S}, "
                             f"{MAX_SUPPORTED_EPOCH}] seconds")
        if self.as_of_epoch % M15_S:
            raise ValueError("as_of_epoch must be an M15 bar close")
        if not (_is_finite(self.spread_price) and self.spread_price >= 0):
            raise ValueError("spread_price must be finite and >= 0")
        if not (_is_finite(self.point) and self.point > 0):
            raise ValueError("point must be finite and > 0")
        if not (_is_int(self.digits) and 0 <= self.digits <= MAX_DIGITS):
            raise ValueError(f"digits must be an int in [0, {MAX_DIGITS}]")
        closed = {
            tf: bars_closed_by(_check_rows(tf, rows), self.as_of_epoch, TIMEFRAME_SECONDS[tf])
            for tf, rows in self.bars.items()
        }
        object.__setattr__(self, "bars", MappingProxyType(closed))

    @classmethod
    def from_context(cls, ctx: MarketContext) -> "SetupInput":
        return cls(as_of_epoch=ctx.as_of_epoch, bars=ctx.bars, spread_price=ctx.spread_price,
                   point=ctx.spec.point, digits=ctx.spec.digits)

    @property
    def bar_open_epoch(self) -> int:
        """Open of the trigger bar: the M15 bar that closed at `as_of_epoch`."""
        return self.as_of_epoch - M15_S

    @property
    def close_buffer(self) -> float:
        """kn/06 §7.2: how far a close must be past a level to count as through it."""
        return CLOSE_BEYOND_SPREADS * max(self.spread_price, self.point)

    def series(self, tf: str) -> tuple[Bar, ...]:
        return self.bars.get(tf, ())

    def known_before_trigger(self, tf: str) -> tuple[Bar, ...]:
        """Bars of `tf` that had closed when the trigger bar opened."""
        return bars_closed_by(self.series(tf), self.bar_open_epoch, TIMEFRAME_SECONDS[tf])

    @property
    def trigger(self) -> Bar | None:
        """The M15 bar that closed at `as_of_epoch`; None when it is missing."""
        m15 = self.series("M15")
        return m15[-1] if m15 and m15[-1].t == self.bar_open_epoch else None

    @property
    def previous(self) -> Bar | None:
        """The M15 bar stored before the trigger (None without a trigger)."""
        m15 = self.series("M15")
        return m15[-2] if self.trigger is not None and len(m15) > 1 else None


SetupSource = SetupInput | MarketContext


def as_setup_input(source: SetupSource) -> SetupInput:
    if isinstance(source, SetupInput):
        return source
    if isinstance(source, MarketContext):
        return SetupInput.from_context(source)
    raise TypeError(f"expected SetupInput or MarketContext, got {type(source).__name__}")


# --- windows and volatility ---------------------------------------------------------

def since(bars: Sequence[Bar], start_epoch: int) -> tuple[Bar, ...]:
    """Bars (oldest first) that opened at or after `start_epoch`."""
    return tuple(bars[bisect_left(bars, start_epoch, key=lambda bar: bar.t):])


def between(bars: Sequence[Bar], start_epoch: int, end_epoch: int) -> tuple[Bar, ...]:
    """Bars (oldest first) with `start_epoch <= t < end_epoch`."""
    lo = bisect_left(bars, start_epoch, key=lambda bar: bar.t)
    hi = bisect_left(bars, end_epoch, key=lambda bar: bar.t)
    return tuple(bars[lo:hi])


def _usable(value: float | None) -> TypeGuard[float]:
    return value is not None and math.isfinite(value) and value > 0


def vol_unit_m15(inp: SetupInput) -> tuple[float, str] | None:
    """(unit, reason code): same-slot true range, else ATR(14, M15), before the trigger."""
    history = since(inp.known_before_trigger("M15"), inp.bar_open_epoch - SLOT_LOOKBACK_S)
    slot = same_slot_true_range(
        history, inp.bar_open_epoch, SLOT_SESSIONS, min_sessions=MIN_SLOT_SESSIONS)
    if _usable(slot):
        return slot, RC_VOL_SLOT
    atr = atr_wilder(history[-ATR_WINDOW_BARS:], ATR_PERIOD)
    if _usable(atr):
        return atr, RC_VOL_ATR_FALLBACK
    return None


def atr_m5(inp: SetupInput) -> float | None:
    """ATR(14, M5) over the last closed M5 bars (those inside the trigger bar included)."""
    value = atr_wilder(inp.series("M5")[-ATR_WINDOW_BARS:], ATR_PERIOD)
    return value if _usable(value) else None


@lru_cache(maxsize=D1_OPEN_HOURS_CACHE)
def _open_hours(day_open: int) -> int:
    """Hours of the D1 bar opening at `day_open` outside the weekend closure."""
    return sum(1 for hour in range(HOURS_PER_DAY)
               if not session_state(day_open + hour * H1_S + H1_S // 2).weekend)


def full_days(bars: Sequence[Bar]) -> tuple[Bar, ...]:
    """D1 bars that cover a trading day, weekend-session stubs dropped."""
    return tuple(bar for bar in bars if _open_hours(bar.t) >= MIN_D1_OPEN_HOURS)


def atr_d1(inp: SetupInput) -> float | None:
    """ATR(14, D1) over the full daily bars closed before the trigger bar."""
    daily = full_days(inp.known_before_trigger("D1"))
    value = atr_wilder(daily[-D1_ATR_WINDOW_BARS:], ATR_PERIOD)
    return value if _usable(value) else None


def stop_buffer(inp: SetupInput, atr5: float | None) -> float:
    """kn/06 §4 / §6: max(3 x spread, 0.15 x ATR(14, M5))."""
    by_spread = STOP_BUFFER_SPREADS * max(inp.spread_price, inp.point)
    return max(by_spread, STOP_BUFFER_ATR * atr5) if atr5 is not None else by_spread


def wider_stop(side: Side, *prices: float) -> float:
    """The stop furthest from the trade: the lowest for a buy, the highest for a sell."""
    return min(prices) if side == "buy" else max(prices)


def side_of(bar: Bar) -> Side | None:
    if bar.c > bar.o:
        return "buy"
    return "sell" if bar.c < bar.o else None


# --- levels and structure ------------------------------------------------------------

def _pivot_levels(inp: SetupInput) -> tuple[Level, ...]:
    start = inp.bar_open_epoch - H1_LEVEL_LOOKBACK_S
    # Extra bars on the left so a pivot at the window start still has its left side.
    h1 = since(inp.known_before_trigger("H1"), start - H1_PIVOT_STRENGTH * H1_S)
    return tuple(
        Level("h1_high" if pivot.kind == "high" else "h1_low", pivot.price, pivot.confirmed_at)
        for pivot in confirmed_pivots(h1, H1_PIVOT_STRENGTH, H1_S)
        if pivot.t >= start and pivot.confirmed_at <= inp.bar_open_epoch
    )


def reference_levels(inp: SetupInput) -> tuple[Level, ...]:
    """Prior-day high/low (last full day) and confirmed H1 pivots, known before the trigger."""
    daily = full_days(inp.known_before_trigger("D1"))
    extremes = prior_day_levels(daily)
    prior_day: tuple[Level, ...] = ()
    if extremes is not None:
        known_at = daily[-1].t + D1_S
        prior_day = (Level("pdh", extremes[0], known_at), Level("pdl", extremes[1], known_at))
    return prior_day + _pivot_levels(inp)


def htf_structure(inp: SetupInput) -> Structure:
    """H1 swing structure from pivots confirmed before the trigger opened."""
    h1 = since(inp.known_before_trigger("H1"), inp.bar_open_epoch - H1_STRUCTURE_LOOKBACK_S)
    return swing_structure(confirmed_pivots(h1, H1_PIVOT_STRENGTH, H1_S), inp.bar_open_epoch)


def bias_codes(structure: Structure, side: Side) -> tuple[str, ...]:
    """HTF_ALIGNED / HTF_OPPOSED against the H1 structure; nothing for range/unknown."""
    with_trade, against = ("up", "down") if side == "buy" else ("down", "up")
    if structure == with_trade:
        return (RC_HTF_ALIGNED,)
    return (RC_HTF_OPPOSED,) if structure == against else ()


def crossed_levels(levels: Iterable[Level], side: Side, prev_close: float, close: float,
                   buffer: float) -> tuple[Level, ...]:
    """Levels the close went through: from at/behind the level to `buffer` past it."""
    sign = DIRECTION[side]
    return tuple(
        level for level in levels
        if sign * (level.price - prev_close) >= 0 and sign * (close - level.price) >= buffer
    )


def pick_level(levels: Sequence[Level], reference: float) -> Level:
    """Highest-ranked level, then the one nearest `reference`; deterministic on ties."""
    if not levels:
        raise ValueError("pick_level needs at least one level")
    return min(levels, key=lambda lv: (_LEVEL_RANK[lv.kind], abs(lv.price - reference), lv.price))


def level_codes(chosen: Level, candidates: Sequence[Level]) -> tuple[str, ...]:
    """The chosen level's code, plus LEVEL_CONFLUENCE when several levels qualified."""
    prices = {level.price for level in candidates}
    return (chosen.code,) + ((RC_LEVEL_CONFLUENCE,) if len(prices) > 1 else ())


# --- candidate construction ----------------------------------------------------------

def _clean_features(features: Mapping[str, float | None]) -> Mapping[str, float]:
    # A missing or non-finite value means "unavailable" and is omitted, never zeroed.
    return MappingProxyType({
        key: round(float(value), FEATURE_DECIMALS)
        for key, value in features.items()
        if value is not None and math.isfinite(value)
    })


def build_candidate(
    inp: SetupInput,
    *,
    setup: SetupName,
    side: Side,
    entry: float,
    invalidation: float,
    features: Mapping[str, float | None],
    reason_codes: Iterable[str],
    variant: str = "",
) -> Candidate | None:
    """A frozen Candidate on the price grid, or None when the geometry is unusable."""
    entry_px = round(entry, inp.digits)
    stop_px = round(invalidation, inp.digits)
    if not all(math.isfinite(v) and v > 0 for v in (entry_px, stop_px)):
        return None
    if DIRECTION[side] * (entry_px - stop_px) <= 0:
        return None
    return Candidate(
        candidate_id=candidate_id_for(setup, side, inp.bar_open_epoch, variant),
        setup=setup,
        side=side,
        entry=entry_px,
        invalidation=stop_px,
        bar_t=inp.bar_open_epoch,
        features=_clean_features(features),
        reason_codes=tuple(dict.fromkeys(reason_codes)),
    )
