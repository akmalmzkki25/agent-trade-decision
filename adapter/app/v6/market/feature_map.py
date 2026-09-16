"""
The `MarketContext.features` mapping for one cycle, built from CLOSED bars only.

Every key is one of `cycle_codes.FEATURE_KEYS`. A key is emitted only when it is
computable from the bars (and snapshot blocks) handed in; a missing key means
"unavailable", never zero. All windows are fixed bar counts, so the values do not
depend on how much history the BarStore happens to hold.

Choices worth knowing (all provisional until the process study measures them):
- friction_price is max(configured round-trip friction, live spread): kn/01 gives
  $0.40 (standard) / $0.22 (raw) per ounce, and a live spread above that constant
  is a cost the constant does not cover.
- rv_ratio is the trigger bar's true range over the same-slot true range (kn/02):
  a realised-range proxy that needs no tick data.
- spread_pctl_hour ranks the live spread against the stored M5 bar spreads of the
  same UTC hour. MT5 bar spreads can be per-bar minima, so the rank leans high.
- vr_m5 / ac1_m5 use the last four hours of M5 returns as the session window.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from ..cycle_codes import (
    F_AC1_M5, F_ADX_H1, F_ATR_H1, F_ATR_M5, F_ATR_M5_POINTS, F_ATR_M15, F_ATR_RATIO,
    F_DOM_SYNTHETIC, F_ER_M15, F_FRICTION_ATR, F_FRICTION_PRICE, F_QUOTE_GAP_MS, F_ROUND_DISTANCE,
    F_RV_RATIO, F_SLOT_TR_M15, F_SPREAD_PCTL_HOUR, F_SPREAD_POINTS, F_STRUCTURE_M15, F_TV_Z,
    F_VR_M5,
)
from ..schemas.snapshot import ProbeBlock, TickStatsBlock
from ..types import TIMEFRAME_SECONDS, Bar
from .features import (
    atr_wilder, kaufman_er, lag1_autocorr, log_returns, percentile_rank, same_slot_true_range,
    signed_variance_ratio, tick_volume_z, true_range,
)
from .levels import confirmed_pivots, round_level_distance, swing_structure

M15_S: Final[int] = TIMEFRAME_SECONDS["M15"]
SECONDS_PER_HOUR: Final[int] = 3600
HOURS_PER_DAY: Final[int] = 24
ATR_PERIOD: Final[int] = 14
# Wilder memory after 100 bars is below 0.2% (same window as app.v6.setups).
ATR_WINDOW_BARS: Final[int] = 100
ADX_PERIOD: Final[int] = 14
ADX_WINDOW_BARS: Final[int] = 100
# kn/02: same-slot true range over ten sessions; half of them still averages the noise.
SLOT_SESSIONS: Final[int] = 10
MIN_SLOT_SESSIONS: Final[int] = 5
# Kaufman ER over four hours of M15 (kn/09); provisional.
ER_WINDOW_BARS: Final[int] = 16
# kn/12 section 9: signed VR and lag-1 autocorrelation over the session window.
RETURN_WINDOW_BARS: Final[int] = 48
VR_HORIZON: Final[int] = 3
TV_WINDOW_BARS: Final[int] = 100
MIN_SPREAD_SAMPLES: Final[int] = 12
PIVOT_STRENGTH: Final[int] = 2          # the Structure desk uses the same strength
PERCENT: Final[float] = 100.0
DOM_SYNTHETIC_VALUE: Final[float] = 1.0
DOM_REAL_VALUE: Final[float] = 0.0
STRUCTURE_VALUES: Final[Mapping[str, float]] = MappingProxyType(
    {"up": 1.0, "down": -1.0, "range": 0.0})


@dataclass(frozen=True)
class FeatureInputs:
    """What the feature map may read; `bars` must already be closed at `as_of_epoch`."""

    as_of_epoch: int
    bars: Mapping[str, tuple[Bar, ...]]
    point: float
    spread_points: int
    spread_price: float
    friction_price: float
    mid: float
    ticks: TickStatsBlock
    probe: ProbeBlock | None = None

    @property
    def bar_open_epoch(self) -> int:
        return self.as_of_epoch - M15_S

    def series(self, tf: str) -> tuple[Bar, ...]:
        return self.bars.get(tf, ())


def effective_friction(configured: float, spread_price: float) -> float:
    """Round-trip friction per unit: the configured constant, never below the live spread."""
    return max(configured, spread_price)


def _finite(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return _finite(numerator / denominator)


# --- ADX (Wilder) ----------------------------------------------------------------

def _directional_moves(bars: Sequence[Bar]) -> tuple[list[float], list[float], list[float]]:
    plus, minus = [], []
    for prev, bar in zip(bars, bars[1:]):
        up, down = bar.h - prev.h, prev.l - bar.l
        plus.append(up if up > down and up > 0 else 0.0)
        minus.append(down if down > up and down > 0 else 0.0)
    return plus, minus, list(true_range(bars)[1:])


def _wilder_sums(values: Sequence[float], period: int) -> list[float]:
    total = math.fsum(values[:period])
    sums = [total]
    for value in values[period:]:
        total = total - total / period + value
        sums.append(total)
    return sums


def _dx(plus: float, minus: float, tr: float) -> float | None:
    if tr <= 0:
        return None
    plus_di, minus_di = PERCENT * plus / tr, PERCENT * minus / tr
    total = plus_di + minus_di
    return 0.0 if total == 0 else PERCENT * abs(plus_di - minus_di) / total


def adx_wilder(bars: Sequence[Bar], period: int = ADX_PERIOD) -> float | None:
    """Wilder ADX at the last bar; None with fewer than 2 x period + 1 bars or no range."""
    if period < 1:
        raise ValueError("period must be >= 1")
    if len(bars) < 2 * period + 1:
        return None
    plus, minus, ranges = _directional_moves(bars)
    rows = zip(_wilder_sums(plus, period), _wilder_sums(minus, period),
               _wilder_sums(ranges, period))
    dxs = [_dx(p, m, t) for p, m, t in rows]
    if any(value is None for value in dxs):
        return None
    known = [value for value in dxs if value is not None]
    adx = math.fsum(known[:period]) / period
    for value in known[period:]:
        adx = (adx * (period - 1) + value) / period
    return adx


# --- groups ------------------------------------------------------------------------

def _volatility(inp: FeatureInputs) -> dict[str, float | None]:
    m15 = inp.series("M15")
    atr_m5 = atr_wilder(inp.series("M5")[-ATR_WINDOW_BARS:], ATR_PERIOD)
    atr_m15 = atr_wilder(m15[-ATR_WINDOW_BARS:], ATR_PERIOD)
    slot = same_slot_true_range(m15, inp.bar_open_epoch, SLOT_SESSIONS,
                                min_sessions=MIN_SLOT_SESSIONS)
    trigger_tr = true_range(m15[-2:])[-1] if m15 and m15[-1].t == inp.bar_open_epoch else None
    return {
        F_ATR_M5: atr_m5,
        F_ATR_M5_POINTS: _ratio(atr_m5, inp.point),
        F_ATR_M15: atr_m15,
        F_ATR_H1: atr_wilder(inp.series("H1")[-ATR_WINDOW_BARS:], ATR_PERIOD),
        F_SLOT_TR_M15: slot,
        F_ATR_RATIO: _ratio(atr_m15, slot),
        F_RV_RATIO: _ratio(trigger_tr, slot),
    }


def _hour_spreads(inp: FeatureInputs) -> list[float]:
    hour = inp.as_of_epoch // SECONDS_PER_HOUR % HOURS_PER_DAY
    return [float(bar.spr) for bar in inp.series("M5")
            if bar.spr > 0 and bar.t // SECONDS_PER_HOUR % HOURS_PER_DAY == hour]


def _costs(inp: FeatureInputs, atr_m5: float | None) -> dict[str, float | None]:
    friction = effective_friction(inp.friction_price, inp.spread_price)
    sample = _hour_spreads(inp)
    pctl = (percentile_rank(float(inp.spread_points), sample)
            if len(sample) >= MIN_SPREAD_SAMPLES else None)
    m15 = inp.series("M15")
    has_trigger = bool(m15) and m15[-1].t == inp.bar_open_epoch
    return {
        F_FRICTION_PRICE: friction,
        F_FRICTION_ATR: _ratio(friction, atr_m5),
        F_SPREAD_POINTS: float(inp.spread_points),
        F_SPREAD_PCTL_HOUR: pctl,
        F_TV_Z: tick_volume_z(m15, TV_WINDOW_BARS) if has_trigger else None,
        F_QUOTE_GAP_MS: float(inp.ticks.max_gap_ms) if inp.ticks.window_s > 0 else None,
    }


def _returns_m5(inp: FeatureInputs) -> tuple[float, ...]:
    closes = [bar.c for bar in inp.series("M5")[-RETURN_WINDOW_BARS:]]
    return log_returns(closes) if len(closes) > 1 else ()


def _structure(inp: FeatureInputs) -> dict[str, float | None]:
    m15 = inp.series("M15")
    returns = _returns_m5(inp)
    pivots = confirmed_pivots(m15, PIVOT_STRENGTH, M15_S)
    swing = swing_structure(pivots, inp.as_of_epoch)
    return {
        F_ER_M15: kaufman_er([bar.c for bar in m15], ER_WINDOW_BARS),
        F_VR_M5: signed_variance_ratio(returns, VR_HORIZON) if returns else None,
        F_AC1_M5: lag1_autocorr(returns),
        F_ADX_H1: adx_wilder(inp.series("H1")[-ADX_WINDOW_BARS:], ADX_PERIOD),
        F_STRUCTURE_M15: STRUCTURE_VALUES.get(swing),
        F_ROUND_DISTANCE: round_level_distance(inp.mid)[1],
    }


def _dom(inp: FeatureInputs) -> dict[str, float | None]:
    if inp.probe is None:
        return {F_DOM_SYNTHETIC: None}
    return {F_DOM_SYNTHETIC: DOM_SYNTHETIC_VALUE if inp.probe.dom_synthetic else DOM_REAL_VALUE}


def build_feature_map(inp: FeatureInputs) -> Mapping[str, float]:
    """Every computable feature for the cycle; unavailable ones are left out."""
    volatility = _volatility(inp)
    groups = (volatility, _costs(inp, volatility[F_ATR_M5]), _structure(inp), _dom(inp))
    found = {key: _finite(value) for group in groups for key, value in group.items()}
    return MappingProxyType({key: value for key, value in found.items() if value is not None})
