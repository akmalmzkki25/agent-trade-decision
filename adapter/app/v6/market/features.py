"""
Pure features on CLOSED bars (oldest first). Nothing here reads a clock, a store
or a bar that is still forming; callers slice with `bars_closed_by` first.

Short input returns None (or an empty tuple for series) instead of raising.
Invalid parameters, and prices no validated snapshot can contain, raise
ValueError. Inputs are never mutated.

Why these features and not others: knowledge/02 (same-slot true range instead
of ATR(14) M15), knowledge/03 and 12 (signed variance ratio and lag-1
autocorrelation per session window), knowledge/09 (Kaufman ER).
"""

from __future__ import annotations

import math
import statistics
from itertools import accumulate
from typing import Final, Sequence

from ..types import Bar
from .levels import (
    Pivot,
    PivotKind,
    Structure,
    confirmed_pivots,
    prior_day_levels,
    round_level_distance,
    swing_structure,
)

__all__ = [
    "Pivot", "PivotKind", "Structure", "atr_wilder", "atr_wilder_series",
    "bars_closed_by", "body_ratio", "confirmed_pivots", "kaufman_er",
    "lag1_autocorr", "log_returns", "percentile_rank", "prior_day_levels",
    "range_to_atr", "round_level_distance", "same_slot_true_range",
    "signed_variance_ratio", "swing_structure", "tick_volume_z", "true_range",
]

SECONDS_PER_DAY: Final[int] = 86_400
DEFAULT_ATR_PERIOD: Final[int] = 14
DEFAULT_SLOT_SESSIONS: Final[int] = 10
DEFAULT_TV_WINDOW: Final[int] = 100
MIN_AUTOCORR_SAMPLES: Final[int] = 3
MIN_VR_HORIZON: Final[int] = 2
MIN_TV_WINDOW: Final[int] = 2
_TIE_WEIGHT: Final[float] = 0.5


def bars_closed_by(bars: Sequence[Bar], as_of_epoch: int, tf_seconds: int) -> tuple[Bar, ...]:
    """Bars whose CLOSE (open + tf_seconds) is at or before `as_of_epoch`."""
    if tf_seconds <= 0:
        raise ValueError("tf_seconds must be positive")
    return tuple(bar for bar in bars if bar.t + tf_seconds <= as_of_epoch)


# --- true range and ATR -----------------------------------------------------------

def _bar_true_range(bar: Bar, prev_close: float | None) -> float:
    if prev_close is None:
        return bar.h - bar.l
    return max(bar.h - bar.l, abs(bar.h - prev_close), abs(bar.l - prev_close))


def true_range(bars: Sequence[Bar]) -> tuple[float, ...]:
    """Per-bar true range; the first bar has no previous close, so it is h - l."""
    return tuple(
        _bar_true_range(bar, bars[i - 1].c if i else None) for i, bar in enumerate(bars)
    )


def _check_period(period: int) -> None:
    if period < 1:
        raise ValueError("period must be >= 1")


def atr_wilder_series(
    bars: Sequence[Bar], period: int = DEFAULT_ATR_PERIOD
) -> tuple[float | None, ...]:
    """
    Wilder ATR aligned with `bars`. As in TA-Lib, the seed is the mean of
    TR[1..period] (every TR with a real previous close), so the first value sits
    at index `period`; earlier entries are None.
    """
    _check_period(period)
    ranges = true_range(bars)
    out: list[float | None] = [None] * min(len(bars), period)
    if len(bars) <= period:
        return tuple(out)
    atr = statistics.fmean(ranges[1:period + 1])
    out.append(atr)
    for tr in ranges[period + 1:]:
        atr = (atr * (period - 1) + tr) / period
        out.append(atr)
    return tuple(out)


def atr_wilder(bars: Sequence[Bar], period: int = DEFAULT_ATR_PERIOD) -> float | None:
    """Wilder ATR at the last bar; None with fewer than period + 1 bars."""
    series = atr_wilder_series(bars, period)
    return series[-1] if series else None


def same_slot_true_range(
    bars_m15: Sequence[Bar],
    slot_open_epoch: int,
    sessions: int = DEFAULT_SLOT_SESSIONS,
    *,
    min_sessions: int | None = None,
) -> float | None:
    """
    Mean true range of the bars that opened at the same UTC time of day as
    `slot_open_epoch`, over the last `sessions` such bars strictly before it.

    Replaces ATR(14) on M15, whose 3.5 h lookback smears session boundaries and
    reads 0.57x at the NY open (knowledge/02 §1). Returns None when fewer than
    `min_sessions` (default: all `sessions`) matching bars exist. Matching is on
    UTC, so for up to `sessions` days after a DST change the sample mixes two
    local-session phases.
    """
    if sessions < 1:
        raise ValueError("sessions must be >= 1")
    required = sessions if min_sessions is None else min_sessions
    if not 1 <= required <= sessions:
        raise ValueError("min_sessions must be in [1, sessions]")
    time_of_day = slot_open_epoch % SECONDS_PER_DAY
    matched = [
        _bar_true_range(bar, bars_m15[i - 1].c if i else None)
        for i, bar in enumerate(bars_m15)
        if bar.t < slot_open_epoch and bar.t % SECONDS_PER_DAY == time_of_day
    ]
    recent = matched[-sessions:]
    if len(recent) < required:
        return None
    return statistics.fmean(recent)


# --- efficiency, returns, autocorrelation, variance ratio ------------------------

def kaufman_er(closes: Sequence[float], n: int) -> float | None:
    """
    |net change| / path length over the last `n` changes, in [0, 1]. A flat
    window has no path and no direction, and reads 0.0 (no efficiency).
    """
    if n < 1:
        raise ValueError("n must be >= 1")
    if len(closes) < n + 1:
        return None
    window = closes[-(n + 1):]
    path = sum(abs(b - a) for a, b in zip(window, window[1:]))
    if path == 0:
        return 0.0
    return abs(window[-1] - window[0]) / path


def log_returns(closes: Sequence[float]) -> tuple[float, ...]:
    """ln(c[i] / c[i-1]); one fewer element than `closes`."""
    if any(not (math.isfinite(c) and c > 0) for c in closes):
        raise ValueError("closes must be positive and finite")
    return tuple(math.log(b / a) for a, b in zip(closes, closes[1:]))


def _is_constant(values: Sequence[float]) -> bool:
    # Compared exactly: a float mean of equal values can leave 1e-17 residues
    # that would turn "undefined" into a huge, meaningless ratio.
    return max(values) == min(values)


def lag1_autocorr(returns: Sequence[float]) -> float | None:
    """
    Sample lag-1 autocorrelation (lag-1 autocovariance sum over the total sum of
    squares). None with fewer than 3 returns or a constant series.
    """
    if len(returns) < MIN_AUTOCORR_SAMPLES or _is_constant(returns):
        return None
    mean = statistics.fmean(returns)
    dev = [r - mean for r in returns]
    denom = sum(d * d for d in dev)
    return sum(a * b for a, b in zip(dev, dev[1:])) / denom


def signed_variance_ratio(returns: Sequence[float], q: int) -> float | None:
    """
    Lo-MacKinlay VR(q) with overlapping q-period sums and their bias
    corrections: > 1 trending, < 1 mean-reverting. Deliberately NOT |1 - VR|,
    which is what the published gold efficiency studies report and which hides
    the direction (knowledge/12 §9). None when len(returns) <= q or the series
    is constant.
    """
    if q < MIN_VR_HORIZON:
        raise ValueError("q must be >= 2")
    n = len(returns)
    if n <= q or _is_constant(returns):
        return None
    mu = statistics.fmean(returns)
    var_1 = sum((r - mu) ** 2 for r in returns) / (n - 1)
    cumulative = (0.0, *accumulate(returns))
    q_sums = (cumulative[k + q] - cumulative[k] for k in range(n - q + 1))
    m = q * (n - q + 1) * (1.0 - q / n)
    var_q = sum((s - q * mu) ** 2 for s in q_sums) / m
    return var_q / var_1


# --- candle shape ----------------------------------------------------------------

def body_ratio(bar: Bar) -> float:
    """|close - open| / range in [0, 1]; a zero-range bar reads 0.0."""
    return bar.body / bar.range if bar.range > 0 else 0.0


def range_to_atr(bar: Bar, atr: float | None) -> float | None:
    """Bar range in ATR units; None without a usable (positive, finite) ATR."""
    if atr is None or not (math.isfinite(atr) and atr > 0):
        return None
    return bar.range / atr


# --- tick volume and percentile --------------------------------------------------

def tick_volume_z(bars: Sequence[Bar], window: int = DEFAULT_TV_WINDOW) -> float | None:
    """
    z-score of the LAST bar's tick volume against the `window` bars before it.
    The bar itself is excluded so a spike cannot dilute its own baseline.
    None with too little history or a constant baseline.
    """
    if window < MIN_TV_WINDOW:
        raise ValueError("window must be >= 2")
    if len(bars) < window + 1:
        return None
    baseline = [bar.tv for bar in bars[-(window + 1):-1]]
    spread = statistics.stdev(baseline)
    if spread == 0:
        return None
    return (bars[-1].tv - statistics.fmean(baseline)) / spread


def percentile_rank(value: float, sample: Sequence[float]) -> float | None:
    """
    Share of `sample` below `value`, ties counted half ("mean" percentile), in
    [0, 1]. None for an empty sample.
    """
    if not math.isfinite(value):
        raise ValueError("value must be finite")
    if not sample:
        return None
    below = sum(1 for s in sample if s < value)
    equal = sum(1 for s in sample if s == value)
    return (below + _TIE_WEIGHT * equal) / len(sample)
