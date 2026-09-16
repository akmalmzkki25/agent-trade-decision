"""Known-value tests for the pure bar features in `app.v6.market.features`."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from app.v6.market import features as f
from app.v6.schemas.snapshot import validate_bar_rows
from app.v6.types import Bar

from .fixtures_v6 import (
    DEFAULT_START_EPOCH,
    SECONDS_PER_DAY,
    bars_from_closes,
    bars_from_ohlc,
    insert_gap,
    inject_displacement,
    random_walk_bars,
    range_bars,
    range_closes,
    to_rows,
    trend_bars,
    trend_closes,
)

M15 = 900
SLOTS_PER_DAY = SECONDS_PER_DAY // M15
NOON_SLOT = 48  # 12:00 UTC, the NY-open slot where ATR(14) reads 0.57x

# Hand-computed series: TR = 2, 2, 1, 3, 4, 2.5 (the last bar gaps up from 10 to 12).
HAND_ROWS = (
    (10.0, 11.0, 9.0, 10.0),
    (10.0, 12.0, 10.0, 11.0),
    (11.0, 11.5, 10.5, 11.0),
    (11.0, 14.0, 11.0, 13.0),
    (13.0, 13.0, 9.0, 10.0),
    (12.0, 12.5, 11.5, 12.0),
)


# --- fixtures are valid wire data ------------------------------------------------

@pytest.mark.parametrize(
    "bars",
    [
        trend_bars(64),
        trend_bars(64, direction="down"),
        range_bars(64),
        random_walk_bars(300, seed=7),
        inject_displacement(random_walk_bars(50, seed=3), 20, -12.0),
        insert_gap(random_walk_bars(50, seed=4), 10, missing_bars=8, price_jump=5.0),
    ],
)
def test_fixture_bars_pass_the_snapshot_validator(bars: tuple[Bar, ...]) -> None:
    assert validate_bar_rows(to_rows(bars), "M15")


def test_fixture_generators_are_deterministic_and_reject_bad_input() -> None:
    assert random_walk_bars(20, seed=1) == random_walk_bars(20, seed=1)
    assert random_walk_bars(20, seed=1) != random_walk_bars(20, seed=2)
    with pytest.raises(ValueError):
        random_walk_bars(5, seed=1, start_t=DEFAULT_START_EPOCH + 1)
    with pytest.raises(ValueError):
        range_closes(4, period=3)
    with pytest.raises(ValueError):
        trend_closes(4, leg=2, pullback=2)
    with pytest.raises(IndexError):
        inject_displacement(trend_bars(3), 5, 1.0)
    with pytest.raises(ValueError):
        insert_gap(trend_bars(3), 1, missing_bars=-1)


def test_displacement_and_gap_keep_the_series_continuous() -> None:
    base = random_walk_bars(30, seed=5)
    spiked = inject_displacement(base, 10, 15.0)
    assert spiked[10].body == pytest.approx(15.0)
    assert spiked[11].o == pytest.approx(spiked[10].c)
    assert spiked[10].tv == base[10].tv * 3
    gapped = insert_gap(base, 9, missing_bars=4, price_jump=-3.0)
    assert gapped[10].t - gapped[9].t == 5 * M15
    assert gapped[10].c == pytest.approx(base[10].c - 3.0)
    assert base == random_walk_bars(30, seed=5)  # inputs untouched


# --- closed-bar slicing ----------------------------------------------------------

def test_bars_closed_by_keeps_only_bars_closed_at_as_of() -> None:
    bars = trend_bars(10)
    as_of = bars[4].t + M15
    assert f.bars_closed_by(bars, as_of, M15) == bars[:5]
    assert f.bars_closed_by(bars, as_of - 1, M15) == bars[:4]
    assert f.bars_closed_by(bars, bars[0].t, M15) == ()
    with pytest.raises(ValueError):
        f.bars_closed_by(bars, as_of, 0)


# --- true range and ATR ----------------------------------------------------------

def test_true_range_known_values() -> None:
    bars = bars_from_ohlc(HAND_ROWS)
    assert f.true_range(bars) == pytest.approx((2.0, 2.0, 1.0, 3.0, 4.0, 2.5))
    assert f.true_range(()) == ()


def test_atr_wilder_known_values() -> None:
    bars = bars_from_ohlc(HAND_ROWS)
    # Seed = mean(TR[1..3]) = 2; then (2*2 + 4)/3 and (8/3*2 + 2.5)/3.
    assert f.atr_wilder_series(bars, period=3) == pytest.approx(
        (None, None, None, 2.0, 8.0 / 3.0, 23.5 / 9.0)
    )
    assert f.atr_wilder(bars, period=3) == pytest.approx(23.5 / 9.0)
    assert f.atr_wilder(bars[:4], period=3) == pytest.approx(2.0)


def test_atr_wilder_needs_period_plus_one_bars() -> None:
    bars = bars_from_ohlc(HAND_ROWS)
    assert f.atr_wilder(bars[:3], period=3) is None
    assert f.atr_wilder((), period=14) is None
    assert f.atr_wilder_series(bars[:2], period=3) == (None, None)
    with pytest.raises(ValueError):
        f.atr_wilder(bars, period=0)


# --- same-slot true range --------------------------------------------------------

def _slot_bars(days: int) -> tuple[Bar, ...]:
    """Flat M15 days; the 12:00 bar of day d has range d + 1, every other bar 1."""
    bars = []
    for day in range(days):
        for slot in range(SLOTS_PER_DAY):
            half = (day + 1.0 if slot == NOON_SLOT else 1.0) / 2.0
            t = DEFAULT_START_EPOCH + day * SECONDS_PER_DAY + slot * M15
            bars.append(Bar(t=t, o=100.0, h=100.0 + half, l=100.0 - half, c=100.0))
    return tuple(bars)


def _noon(day: int) -> int:
    return DEFAULT_START_EPOCH + day * SECONDS_PER_DAY + NOON_SLOT * M15


def test_same_slot_true_range_uses_only_matching_time_of_day() -> None:
    bars = _slot_bars(12)
    # Day 11 noon: the ten prior noons are days 1..10, ranges 2..11.
    assert f.same_slot_true_range(bars, _noon(11)) == pytest.approx(6.5)
    assert f.same_slot_true_range(bars, _noon(11), sessions=3) == pytest.approx(10.0)
    # 12:15 has range 1 every day, whatever happened at noon.
    assert f.same_slot_true_range(bars, _noon(11) + M15) == pytest.approx(1.0)


def test_same_slot_true_range_ignores_the_slot_itself_and_later_bars() -> None:
    bars = _slot_bars(12)
    widened = tuple(
        replace(b, h=b.h + 500.0) if b.t >= _noon(11) else b for b in bars
    )
    assert f.same_slot_true_range(widened, _noon(11)) == f.same_slot_true_range(bars, _noon(11))


def test_same_slot_true_range_returns_none_when_history_is_short() -> None:
    bars = _slot_bars(12)
    assert f.same_slot_true_range(bars, _noon(5)) is None
    assert f.same_slot_true_range(bars, _noon(5), min_sessions=5) == pytest.approx(3.0)
    assert f.same_slot_true_range(bars, _noon(11) + 7) is None  # off-grid slot
    assert f.same_slot_true_range((), _noon(1)) is None
    with pytest.raises(ValueError):
        f.same_slot_true_range(bars, _noon(11), sessions=0)
    with pytest.raises(ValueError):
        f.same_slot_true_range(bars, _noon(11), sessions=3, min_sessions=4)


# --- efficiency, returns, autocorrelation, variance ratio ------------------------

def test_kaufman_er_known_values() -> None:
    closes = (10.0, 11.0, 10.0, 12.0, 13.0)
    assert f.kaufman_er(closes, 4) == pytest.approx(0.6)
    assert f.kaufman_er(closes, 2) == pytest.approx(1.0)
    assert f.kaufman_er((5.0, 5.0, 5.0), 2) == 0.0
    assert f.kaufman_er(closes, 5) is None
    with pytest.raises(ValueError):
        f.kaufman_er(closes, 0)


def test_log_returns_known_values() -> None:
    assert f.log_returns((100.0, 110.0, 99.0)) == pytest.approx(
        (math.log(1.1), math.log(0.9))
    )
    assert f.log_returns((100.0,)) == ()
    with pytest.raises(ValueError):
        f.log_returns((100.0, 0.0))


def test_lag1_autocorr_known_values() -> None:
    assert f.lag1_autocorr((1.0, 2.0, 3.0, 4.0)) == pytest.approx(0.25)
    assert f.lag1_autocorr((1.0, -1.0, 1.0, -1.0)) == pytest.approx(-0.75)
    assert f.lag1_autocorr((1.0, 2.0)) is None
    assert f.lag1_autocorr((3.0, 3.0, 3.0)) is None


def test_signed_variance_ratio_known_values() -> None:
    # mu = 2.5, var1 = 5/3; 2-sums 3, 5, 7 -> 8 / m with m = 2*3*(1 - 2/4) = 3.
    assert f.signed_variance_ratio((1.0, 2.0, 3.0, 4.0), 2) == pytest.approx(1.6)
    assert f.signed_variance_ratio((1.0, -1.0, 1.0, -1.0), 2) == pytest.approx(0.0)
    assert f.signed_variance_ratio((1.0, 2.0), 2) is None
    assert f.signed_variance_ratio((2.0, 2.0, 2.0, 2.0), 2) is None
    with pytest.raises(ValueError):
        f.signed_variance_ratio((1.0, 2.0, 3.0), 1)


@pytest.mark.parametrize("q", [2, 3, 4])
def test_variance_ratio_is_signed_by_regime(q: int) -> None:
    # Long legs with shallow pullbacks: persistent returns, the momentum case.
    trending = f.log_returns(trend_closes(280, leg=10, pullback=4))
    alternating = f.log_returns(range_closes(240, period=2))
    assert f.signed_variance_ratio(trending, q) > 1.0
    assert f.signed_variance_ratio(alternating, q) < 1.0
    assert f.lag1_autocorr(trending) > 0.0
    assert f.lag1_autocorr(alternating) < 0.0


def test_variance_ratio_is_near_one_on_a_random_walk() -> None:
    returns = f.log_returns(tuple(b.c for b in random_walk_bars(4000, seed=11)))
    assert f.signed_variance_ratio(returns, 4) == pytest.approx(1.0, abs=0.15)
    assert f.lag1_autocorr(returns) == pytest.approx(0.0, abs=0.05)


# --- candle shape ----------------------------------------------------------------

def test_body_ratio_and_range_to_atr() -> None:
    bar = Bar(t=0, o=10.0, h=14.0, l=9.0, c=13.0)
    assert f.body_ratio(bar) == pytest.approx(0.6)
    assert f.body_ratio(Bar(t=0, o=5.0, h=5.0, l=5.0, c=5.0)) == 0.0
    assert f.range_to_atr(bar, 2.5) == pytest.approx(2.0)
    assert f.range_to_atr(bar, None) is None
    assert f.range_to_atr(bar, 0.0) is None


# --- tick volume and percentile --------------------------------------------------

def _with_volumes(volumes: tuple[int, ...]) -> tuple[Bar, ...]:
    bars = bars_from_closes(tuple(100.0 + i for i in range(len(volumes))))
    return tuple(replace(b, tv=v) for b, v in zip(bars, volumes))


def test_tick_volume_z_excludes_the_bar_itself() -> None:
    # Against (10, 20, 30): mean 20, sd 10 -> z = 2. Including 40 would give 1.
    assert f.tick_volume_z(_with_volumes((10, 20, 30, 40)), window=3) == pytest.approx(2.0)
    # Bars older than the window do not count.
    assert f.tick_volume_z(_with_volumes((9999, 10, 20, 30, 40)), window=3) == pytest.approx(2.0)


def test_tick_volume_z_short_or_flat_history() -> None:
    assert f.tick_volume_z(_with_volumes((10, 20, 30)), window=3) is None
    assert f.tick_volume_z(_with_volumes((50, 50, 50, 80)), window=3) is None
    with pytest.raises(ValueError):
        f.tick_volume_z(_with_volumes((10, 20, 30)), window=1)


def test_percentile_rank_known_values() -> None:
    sample = (1.0, 2.0, 3.0, 4.0)
    assert f.percentile_rank(3.0, sample) == pytest.approx(0.625)
    assert f.percentile_rank(0.0, sample) == 0.0
    assert f.percentile_rank(5.0, sample) == 1.0
    assert f.percentile_rank(1.0, ()) is None
    with pytest.raises(ValueError):
        f.percentile_rank(math.nan, sample)
