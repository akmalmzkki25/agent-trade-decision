"""
Pivots, swing structure and price levels, plus the no-look-ahead property:
nothing computed as of bar i may depend on a bar after i.
"""

from __future__ import annotations

import random
from dataclasses import FrozenInstanceError, replace

import pytest

from app.v6.market import features as f
from app.v6.market import levels as lv
from app.v6.types import Bar

from .fixtures_v6 import (
    DEFAULT_START_EPOCH,
    SECONDS_PER_DAY,
    bars_from_ohlc,
    random_walk_bars,
    range_bars,
    trend_bars,
)

M15 = 900
STRENGTH = 2

# One clean swing high at index 2 and one swing low at index 5 (strength 2).
SWING_ROWS = (
    (10.0, 11.0, 9.5, 10.5),
    (10.5, 12.0, 10.0, 11.5),
    (11.5, 15.0, 11.0, 14.0),
    (14.0, 14.5, 12.0, 12.5),
    (12.5, 13.0, 9.0, 9.5),
    (9.5, 10.0, 7.0, 8.0),
    (8.0, 9.5, 7.5, 9.0),
    (9.0, 11.0, 8.5, 10.5),
)


# --- confirmed pivots ------------------------------------------------------------

def test_confirmed_pivots_known_values_and_confirmation_lag() -> None:
    bars = bars_from_ohlc(SWING_ROWS)
    pivots = lv.confirmed_pivots(bars, STRENGTH, M15)
    assert [(p.kind, p.index, p.price) for p in pivots] == [("high", 2, 15.0), ("low", 5, 7.0)]
    high, low = pivots
    assert high.t == bars[2].t
    # Confirmed when the bar STRENGTH to the right has CLOSED, not when it opens.
    assert high.confirmed_at == bars[4].t + M15
    assert low.confirmed_at == bars[7].t + M15


def test_pivot_is_absent_until_its_right_side_has_closed() -> None:
    bars = bars_from_ohlc(SWING_ROWS)
    assert all(p.index != 5 for p in lv.confirmed_pivots(bars[:7], STRENGTH, M15))
    assert [p.index for p in lv.confirmed_pivots(bars[:5], STRENGTH, M15)] == [2]
    assert lv.confirmed_pivots(bars[:4], STRENGTH, M15) == ()


def test_equal_highs_yield_one_pivot_at_the_first_bar() -> None:
    rows = (
        (10.0, 11.0, 9.0, 10.0),
        (10.0, 12.0, 9.5, 11.0),
        (11.0, 13.0, 10.0, 12.0),
        (12.0, 13.0, 11.0, 11.5),
        (11.5, 12.0, 10.0, 10.5),
        (10.5, 11.0, 9.8, 10.0),
    )
    pivots = lv.confirmed_pivots(bars_from_ohlc(rows), STRENGTH, M15)
    highs = [p for p in pivots if p.kind == "high"]
    assert [(p.index, p.price) for p in highs] == [(2, 13.0)]


def test_outside_bar_can_be_both_pivot_kinds() -> None:
    quiet = ((10.0, 11.0, 9.0, 10.0),) * 2
    rows = quiet + ((10.0, 20.0, 1.0, 10.0),) + quiet
    pivots = lv.confirmed_pivots(bars_from_ohlc(rows), STRENGTH, M15)
    assert [(p.kind, p.index) for p in pivots] == [("high", 2), ("low", 2)]


def test_confirmed_pivots_rejects_bad_parameters_and_is_frozen() -> None:
    bars = bars_from_ohlc(SWING_ROWS)
    with pytest.raises(ValueError):
        lv.confirmed_pivots(bars, 0, M15)
    with pytest.raises(ValueError):
        lv.confirmed_pivots(bars, STRENGTH, 0)
    pivot = lv.confirmed_pivots(bars, STRENGTH, M15)[0]
    with pytest.raises(FrozenInstanceError):
        pivot.price = 1.0  # type: ignore[misc]


# --- swing structure -------------------------------------------------------------

def _structure(bars: tuple[Bar, ...]) -> str:
    pivots = lv.confirmed_pivots(bars, STRENGTH, M15)
    return lv.swing_structure(pivots, bars[-1].t + M15)


def test_swing_structure_by_regime() -> None:
    assert _structure(trend_bars(80)) == "up"
    assert _structure(trend_bars(80, direction="down")) == "down"
    assert _structure(range_bars(80)) == "range"
    assert _structure(trend_bars(4)) == "unknown"


def test_swing_structure_mixed_sequence_is_range() -> None:
    def pivot(kind: str, index: int, price: float) -> lv.Pivot:
        return lv.Pivot(
            kind=kind, index=index, t=index * M15, price=price,
            confirmed_at=(index + STRENGTH + 1) * M15,
        )

    higher_high_lower_low = (
        pivot("high", 1, 10.0), pivot("low", 2, 5.0), pivot("high", 3, 11.0), pivot("low", 4, 4.0),
    )
    assert lv.swing_structure(higher_high_lower_low, 100 * M15) == "range"


def test_swing_structure_ignores_pivots_confirmed_after_as_of() -> None:
    bars = trend_bars(80)
    pivots = lv.confirmed_pivots(bars, STRENGTH, M15)
    early = pivots[3].confirmed_at - 1
    visible = tuple(p for p in pivots if p.confirmed_at <= early)
    assert lv.swing_structure(pivots, early) == lv.swing_structure(visible, early)
    assert lv.swing_structure(pivots, bars[0].t) == "unknown"


# --- price levels ----------------------------------------------------------------

def test_prior_day_levels() -> None:
    days = bars_from_ohlc(
        ((4300.0, 4350.0, 4280.0, 4340.0), (4340.0, 4390.0, 4320.0, 4385.0)), tf="D1"
    )
    assert lv.prior_day_levels(days) == (4390.0, 4320.0)
    assert lv.prior_day_levels(()) is None


@pytest.mark.parametrize(
    ("price", "level", "distance"),
    [
        (4373.4, 4350.0, 23.4),
        (4380.0, 4400.0, -20.0),
        (4375.0, 4400.0, -25.0),  # halfway rounds up
        (4400.0, 4400.0, 0.0),
    ],
)
def test_round_level_distance(price: float, level: float, distance: float) -> None:
    nearest, signed = lv.round_level_distance(price)
    assert nearest == pytest.approx(level)
    assert signed == pytest.approx(distance)


def test_round_level_distance_custom_step_and_bad_input() -> None:
    assert lv.round_level_distance(4373.4, step=10.0) == pytest.approx((4370.0, 3.4))
    with pytest.raises(ValueError):
        lv.round_level_distance(4373.4, step=0.0)
    with pytest.raises(ValueError):
        lv.round_level_distance(float("inf"))


def test_levels_are_reexported_from_features() -> None:
    assert f.confirmed_pivots is lv.confirmed_pivots
    assert f.swing_structure is lv.swing_structure
    assert f.Pivot is lv.Pivot


# --- no look-ahead ---------------------------------------------------------------

WALK = random_walk_bars(6 * 96, seed=20260916)
CLOSES = tuple(b.c for b in WALK)
PREFIX_ENDS = sorted(random.Random(99).sample(range(len(WALK) - 1), 60))


def _scalar_features(bars: tuple[Bar, ...]) -> dict[str, object]:
    closes = tuple(b.c for b in bars)
    returns = f.log_returns(closes)
    atr = f.atr_wilder(bars)
    return {
        "atr": atr,
        "er": f.kaufman_er(closes, 10),
        "rho": f.lag1_autocorr(returns[-64:]),
        "vr": f.signed_variance_ratio(returns[-64:], 4),
        "tvz": f.tick_volume_z(bars, window=50),
        "body": f.body_ratio(bars[-1]),
        "r_atr": f.range_to_atr(bars[-1], atr),
        "pct": f.percentile_rank(bars[-1].range, tuple(b.range for b in bars[:-1])),
    }


@pytest.mark.parametrize("i", PREFIX_ENDS)
def test_features_as_of_bar_i_ignore_later_bars(i: int) -> None:
    prefix = WALK[: i + 1]
    as_of = WALK[i].t + M15
    assert f.bars_closed_by(WALK, as_of, M15) == prefix
    assert _scalar_features(f.bars_closed_by(WALK, as_of, M15)) == _scalar_features(prefix)
    assert f.true_range(WALK)[: i + 1] == f.true_range(prefix)
    assert f.atr_wilder_series(WALK)[i] == f.atr_wilder(prefix)
    assert f.log_returns(CLOSES)[:i] == f.log_returns(CLOSES[: i + 1])
    next_slot = WALK[i + 1].t
    assert f.same_slot_true_range(WALK, next_slot, sessions=3, min_sessions=1) == (
        f.same_slot_true_range(prefix, next_slot, sessions=3, min_sessions=1)
    )


@pytest.mark.parametrize("i", PREFIX_ENDS)
def test_pivots_as_of_bar_i_are_a_stable_subset(i: int) -> None:
    prefix = WALK[: i + 1]
    as_of = WALK[i].t + M15
    full = lv.confirmed_pivots(WALK, STRENGTH, M15)
    partial = lv.confirmed_pivots(prefix, STRENGTH, M15)
    assert set(partial) <= set(full)
    assert all(p.confirmed_at <= as_of for p in partial)
    assert partial == tuple(p for p in full if p.confirmed_at <= as_of)
    assert lv.swing_structure(full, as_of) == lv.swing_structure(partial, as_of)


def test_perturbing_future_bars_changes_nothing_before_them() -> None:
    i = 300
    future_shocked = WALK[: i + 1] + tuple(
        replace(b, h=b.h + 100.0, l=b.l - 100.0, tv=b.tv * 50) for b in WALK[i + 1:]
    )
    as_of = WALK[i].t + M15
    shocked_prefix = f.bars_closed_by(future_shocked, as_of, M15)
    assert _scalar_features(shocked_prefix) == _scalar_features(WALK[: i + 1])
    assert lv.swing_structure(lv.confirmed_pivots(future_shocked, STRENGTH, M15), as_of) == (
        lv.swing_structure(lv.confirmed_pivots(WALK, STRENGTH, M15), as_of)
    )
    assert WALK[0].t == DEFAULT_START_EPOCH and len(WALK) == 6 * SECONDS_PER_DAY // M15
