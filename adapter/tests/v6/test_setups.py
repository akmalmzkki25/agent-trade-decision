"""
Setup detectors: one positive case per side and the negative case of every rule.

Scenarios come from `setup_fixtures_v6` (flat history, one decisive bar), so every
expected price below can be checked by hand.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.v6.cycle_codes import candidate_id_for
from app.v6.setups import displacement, engulfing, orb, retest
from app.v6.types import Bar, Candidate

from .fixtures_v6 import trend_bars
from .setup_fixtures_v6 import (
    BEAR_PREV, BEAR_TRIGGER, BULL_PREV, BULL_TRIGGER, DAY, HOUR, LONDON_OR_END, LONDON_OR_START,
    M5, M15, NOON, NY_OR_END, NY_OR_START, ORB_DOWN, ORB_QUIET, ORB_UP, RETEST_BREAK,
    RETEST_DOWN, RETEST_PEAK, RETEST_TRIGGER, SAT, TUE, WED, after_range, as_of_for, bar,
    day_bar, displacement_down, displacement_up, engulfing_input, flat, flat_days,
    h1_swing_high, london_range_bars, orb_input, setup_input,
)


def _only(found: tuple[Candidate, ...]) -> Candidate:
    assert len(found) == 1, found
    return found[0]


# --- displacement ----------------------------------------------------------------------

def test_displacement_buy_through_prior_day_high() -> None:
    cand = _only(displacement.detect(displacement_up()))
    assert cand.candidate_id == candidate_id_for("displacement", "buy", NOON)
    assert (cand.setup, cand.side, cand.bar_t) == ("displacement", "buy", NOON)
    assert cand.entry == 4309.0                 # 50% of the 4305 -> 4313 body
    assert cand.invalidation == 4303.9          # low 4304.5 - 3 x spread
    assert cand.reason_codes == (
        "CONFIRMED_CLOSE", "STRONG_DISPLACEMENT", "LEVEL_PDH", "VOL_ATR_FALLBACK")
    assert dict(cand.features) == pytest.approx({
        "range_vol": 9.0, "body_ratio": 8 / 9, "vol_unit_m15": 1.0, "bar_range": 9.0,
        "level_price": 4310.0, "close_beyond": 3.0, "stop_buffer": 0.6})


def test_displacement_sell_through_prior_day_low() -> None:
    cand = _only(displacement.detect(displacement_down()))
    assert (cand.side, cand.entry, cand.invalidation) == ("sell", 4301.0, 4306.1)
    assert "LEVEL_PDL" in cand.reason_codes


def test_displacement_uses_same_slot_true_range_when_sessions_exist() -> None:
    cand = _only(displacement.detect(displacement_up(history_start=WED - 7 * DAY)))
    assert "VOL_SLOT_TR" in cand.reason_codes
    assert cand.features["vol_unit_m15"] == 1.0


def test_displacement_stop_takes_the_wider_of_extreme_and_atr() -> None:
    wide_m5 = flat(WED, NOON + M15, price=4305.0, half=3.0, step=M5)   # ATR(14, M5) = 6
    cand = _only(displacement.detect(displacement_up(M5=wide_m5)))
    assert cand.invalidation == 4303.0          # entry 4309 - 1 x ATR beats 4304.5 - 0.9
    assert cand.features["atr_m5"] == 6.0
    assert cand.features["stop_buffer"] == pytest.approx(0.9)


def test_displacement_records_activity_and_htf_bias() -> None:
    volumes = [90, 110] * 72
    m15 = flat(TUE, NOON, price=4305.0, tv=volumes) + (bar(NOON, 4305.0, 4313.5, 4304.5,
                                                           4313.0, tv=400),)
    h1_up = trend_bars(80, tf="H1", start_t=NOON - 80 * HOUR, start_price=4330.0)
    inp = setup_input(as_of_for(m15[-1]), M15=m15, H1=h1_up, D1=(day_bar(TUE, 4310, 4280),))
    cand = _only(displacement.detect(inp))
    assert {"ACTIVITY_HIGH", "HTF_ALIGNED"} <= set(cand.reason_codes)
    assert cand.features["tick_volume_z"] > 1.5
    h1_down = trend_bars(80, tf="H1", start_t=NOON - 80 * HOUR, direction="down",
                         start_price=4330.0)
    opposed = replace(inp, bars={**inp.bars, "H1": h1_down})
    assert "HTF_OPPOSED" in _only(displacement.detect(opposed)).reason_codes


def test_displacement_marks_confluence_and_prefers_prior_day_level() -> None:
    swing = h1_swing_high(WED, 4311.0)
    cand = _only(displacement.detect(displacement_up(H1=swing)))
    assert cand.reason_codes[2:4] == ("LEVEL_PDH", "LEVEL_CONFLUENCE")
    assert cand.features["level_price"] == 4310.0


@pytest.mark.parametrize(("label", "trigger"), [
    ("range too small", bar(NOON, 4305.0, 4306.9, 4305.0, 4306.8)),
    ("body too weak", bar(NOON, 4305.0, 4318.0, 4300.0, 4311.0)),
    ("close short of level", bar(NOON, 4305.0, 4310.0, 4304.5, 4309.5)),
    ("close under the buffer", bar(NOON, 4305.0, 4310.6, 4304.5, 4310.5)),
    ("doji", bar(NOON, 4305.0, 4313.0, 4297.0, 4305.0)),
])
def test_displacement_rejects(label: str, trigger: Bar) -> None:
    assert displacement.detect(displacement_up(trigger)) == (), label


def test_displacement_needs_the_level_crossed_by_this_close() -> None:
    above = flat(WED, NOON, price=4311.0) + (bar(NOON, 4311.0, 4320.5, 4310.5, 4320.0),)
    inp = setup_input(NOON + M15, M15=above, D1=(day_bar(TUE, 4310.0, 4280.0),))
    assert displacement.detect(inp) == ()


def test_displacement_ignores_the_forming_day_and_missing_data() -> None:
    today = (day_bar(TUE, 4400.0, 4200.0), day_bar(WED, 4310.0, 4280.0))
    assert displacement.detect(displacement_up(prior_day=today)) == ()
    assert displacement.detect(displacement_up(prior_day=())) == ()
    no_trigger = displacement_up()
    assert displacement.detect(replace(no_trigger, as_of_epoch=NOON + 2 * M15)) == ()
    lone = setup_input(NOON + M15, M15=(bar(NOON, 4305.0, 4313.5, 4304.5, 4313.0),))
    assert displacement.detect(lone) == ()
    short = setup_input(NOON + M15, M15=flat(NOON - 5 * M15, NOON, price=4305.0) + (
        bar(NOON, 4305.0, 4313.5, 4304.5, 4313.0),), D1=(day_bar(TUE, 4310.0, 4280.0),))
    assert displacement.detect(short) == ()     # no volatility unit yet


# --- opening-range break ----------------------------------------------------------------

def test_orb_buy_on_first_close_above_london_range() -> None:
    inp = orb_input(*ORB_QUIET, ORB_UP)
    cand = _only(orb.detect(inp))
    trigger_t = LONDON_OR_END + 2 * M15
    assert cand.candidate_id == candidate_id_for("orb", "buy", trigger_t, "lon")
    assert (cand.entry, cand.invalidation) == (4307.0, 4299.4)
    assert cand.reason_codes == ("CONFIRMED_CLOSE", "OR_LONDON")
    assert dict(cand.features) == pytest.approx({
        "or_high": 4306.0, "or_low": 4300.0, "or_width": 6.0, "level_price": 4306.0,
        "close_beyond": 2.0, "bars_since_range": 2.0, "stop_buffer": 0.6})


def test_orb_sell_below_london_range() -> None:
    cand = _only(orb.detect(orb_input(*ORB_QUIET, ORB_DOWN)))
    assert (cand.side, cand.entry, cand.invalidation) == ("sell", 4299.5, 4306.6)


def test_orb_new_york_range_uses_its_own_variant() -> None:
    m15 = flat(WED, NY_OR_START, price=4303.0) + (
        bar(NY_OR_START, 4303.0, 4306.0, 4300.0, 4304.0),
        bar(NY_OR_START + M15, 4304.0, 4305.5, 4301.0, 4305.0),
        bar(NY_OR_END, *ORB_UP))
    cand = _only(orb.detect(setup_input(NY_OR_END + M15, M15=m15)))
    assert cand.candidate_id.endswith("-ny") and "OR_NY" in cand.reason_codes


@pytest.mark.parametrize(("days_range", "code"), [(5.0, "OR_WIDE"), (30.0, "OR_NARROW")])
def test_orb_records_range_width_against_daily_atr(days_range: float, code: str) -> None:
    # 30 calendar days hold 22 full trading days; weekend bars are session stubs.
    daily = flat_days(WED, 30, high=4305.0 + days_range / 2, low=4305.0 - days_range / 2)
    cand = _only(orb.detect(orb_input(*ORB_QUIET, ORB_UP, D1=daily)))
    assert code in cand.reason_codes
    assert cand.features["or_width_atr_d1"] == pytest.approx(6.0 / days_range)


def test_orb_width_codes_thresholds() -> None:
    assert orb._width_codes(None) == ()
    assert orb._width_codes(0.3) == () and orb._width_codes(0.6) == ()
    assert orb._width_codes(0.29) == ("OR_NARROW",) and orb._width_codes(0.61) == ("OR_WIDE",)


def test_orb_rejects_late_repeated_weak_and_incomplete_breaks() -> None:
    late = orb_input(*ORB_QUIET, *ORB_QUIET, ORB_UP)                      # 4 bars after
    repeated = orb_input(ORB_QUIET[0], (4305.5, 4307.0, 4305.0, 4306.7), ORB_UP)
    weak = orb_input(*ORB_QUIET, (4305.6, 4306.8, 4305.4, 4306.5))
    for label, inp in (("late", late), ("repeated", repeated), ("weak", weak)):
        assert orb.detect(inp) == (), label
    incomplete = tuple(b for b in orb_input(*ORB_QUIET, ORB_UP).series("M15")
                       if b.t != LONDON_OR_START + M15)
    assert orb.detect(setup_input(as_of_for(incomplete[-1]), M15=incomplete)) == ()


def test_orb_needs_a_finished_range_a_weekday_and_a_trigger() -> None:
    inside = london_range_bars()
    assert orb.detect(setup_input(LONDON_OR_END, M15=inside)) == ()
    saturday = flat(SAT, SAT + 9 * HOUR, price=4303.0)
    assert orb.detect(setup_input(SAT + 9 * HOUR, M15=saturday)) == ()
    assert orb.opening_ranges(setup_input(SAT + 9 * HOUR, M15=saturday)) == ()
    assert orb.detect(setup_input(LONDON_OR_END + 4 * M15, M15=inside)) == ()
    flat_range = flat(WED, LONDON_OR_END, price=4303.0, half=0.0) + after_range(ORB_UP)
    assert orb.opening_ranges(setup_input(as_of_for(flat_range[-1]), M15=flat_range)) == ()


# --- retest continuation ----------------------------------------------------------------

def test_retest_buy_after_fast_extension() -> None:
    cand = _only(retest.detect(orb_input(RETEST_BREAK, RETEST_PEAK, RETEST_TRIGGER)))
    assert cand.candidate_id == candidate_id_for("retest", "buy", LONDON_OR_END + 2 * M15,
                                                 "lon")
    assert (cand.entry, cand.invalidation) == (4307.4, 4305.6)
    assert cand.reason_codes == ("CLEAN_RETEST", "CONFIRMED_CLOSE", "OR_LONDON",
                                 "FAST_EXTENSION")
    assert cand.features["extension"] == pytest.approx(8 / 6)
    assert cand.features["extension_bars"] == 1.0
    assert cand.features["bars_since_break"] == 2.0
    assert cand.features["retest_extreme"] == 4306.2


def test_retest_is_not_also_an_orb() -> None:
    assert orb.detect(orb_input(RETEST_BREAK, RETEST_PEAK, RETEST_TRIGGER)) == ()


def test_retest_slow_extension_and_window_edge() -> None:
    grind = ((4307.2, 4307.6, 4307.0, 4307.3), (4307.3, 4307.7, 4307.0, 4307.5),
             (4307.5, 4308.0, 4307.1, 4307.6))
    cand = _only(retest.detect(orb_input(RETEST_BREAK, *grind, RETEST_TRIGGER)))
    assert "SLOW_EXTENSION" in cand.reason_codes
    assert cand.features["extension_bars"] == 3.0
    filler = (RETEST_PEAK,) * 6
    last_in_window = orb_input(RETEST_BREAK, *filler, RETEST_TRIGGER)          # 7 bars after
    assert retest.detect(last_in_window) != ()
    expired = orb_input(RETEST_BREAK, *filler, RETEST_PEAK, RETEST_TRIGGER)    # 8 bars after
    assert retest.detect(expired) == ()


def test_retest_sell_mirror() -> None:
    cand = _only(retest.detect(orb_input(*RETEST_DOWN)))
    assert (cand.side, cand.entry, cand.invalidation) == ("sell", 4298.6, 4300.4)
    assert cand.features["extension"] == pytest.approx(8 / 6)


def test_retest_stop_uses_atr_beyond_the_edge_when_wider() -> None:
    wide_m5 = flat(WED, LONDON_OR_END + 3 * M15, price=4305.0, half=3.0, step=M5)
    cand = _only(retest.detect(orb_input(RETEST_BREAK, RETEST_PEAK, RETEST_TRIGGER,
                                         M5=wide_m5)))
    assert cand.invalidation == 4300.0          # edge 4306 - 1 x ATR(6)


@pytest.mark.parametrize(("label", "rows"), [
    ("extension too far", (RETEST_BREAK, (4307.2, 4309.0, 4307.0, 4307.5), RETEST_TRIGGER)),
    ("earlier retest", (RETEST_BREAK, (4307.2, 4308.0, 4306.5, 4307.5), RETEST_TRIGGER)),
    ("closes back", (RETEST_BREAK, RETEST_PEAK, (4306.3, 4307.9, 4306.2, 4306.4))),
    ("bearish close", (RETEST_BREAK, RETEST_PEAK, (4307.6, 4307.9, 4306.2, 4307.4))),
    ("no touch", (RETEST_BREAK, RETEST_PEAK, (4307.0, 4307.9, 4306.8, 4307.4))),
    ("no break", ((4305.0, 4306.5, 4304.8, 4306.3), (4306.3, 4306.8, 4306.0, 4306.5),
                  RETEST_TRIGGER)),
])
def test_retest_rejects(label: str, rows: tuple[tuple[float, float, float, float], ...]) -> None:
    assert retest.detect(orb_input(*rows)) == (), label


def test_retest_needs_a_trigger_and_a_range() -> None:
    assert retest.detect(setup_input(LONDON_OR_END + M15, M15=london_range_bars())) == ()
    assert retest.detect(setup_input(NOON + M15, M15=flat(NOON - M15, NOON + M15,
                                                          price=4305.0))) == ()


def test_retest_peak_is_the_first_extreme_in_the_trade_direction() -> None:
    leg = after_range(RETEST_BREAK, RETEST_PEAK, RETEST_PEAK)
    assert retest._peak(leg, "buy") == (1, 4308.0)
    assert retest._peak(leg, "sell") == (0, 4304.8)


# --- engulfing --------------------------------------------------------------------------

def test_engulfing_bull_at_prior_day_low_is_shadow_weight() -> None:
    cand = _only(engulfing.detect(engulfing_input(BULL_PREV, BULL_TRIGGER)))
    unit = (13 * 1.0 + 3.6) / 14
    assert cand.candidate_id == candidate_id_for("engulfing", "buy", NOON)
    assert (cand.entry, cand.invalidation) == (4302.9, 4299.7)
    assert cand.reason_codes == ("SHADOW_WEIGHT", "CONFIRMED_CLOSE", "LEVEL_PDL",
                                 "VOL_ATR_FALLBACK")
    assert cand.features["vol_unit_m15"] == pytest.approx(unit, abs=1e-6)
    assert cand.features["level_distance_vol"] == pytest.approx(0.3 / unit, abs=1e-6)
    assert cand.features["prev_body"] == pytest.approx(3.0)


def test_engulfing_is_not_a_displacement_here() -> None:
    assert displacement.detect(engulfing_input(BULL_PREV, BULL_TRIGGER)) == ()


def test_engulfing_bear_at_prior_day_high() -> None:
    inp = engulfing_input(BEAR_PREV, BEAR_TRIGGER, price=4306.0, pdh=4310.0, pdl=4280.0)
    cand = _only(engulfing.detect(inp))
    assert (cand.side, cand.entry, cand.invalidation) == ("sell", 4307.1, 4310.3)
    assert "LEVEL_PDH" in cand.reason_codes


@pytest.mark.parametrize(("label", "previous", "trigger", "kwargs"), [
    ("previous same colour", (4301.0, 4304.2, 4300.6, 4304.0), BULL_TRIGGER, {}),
    ("body not larger", BULL_PREV, (4302.0, 4305.5, 4300.3, 4305.0), {}),
    ("close under previous open", BULL_PREV, (4300.8, 4304.4, 4300.3, 4303.9), {}),
    ("level too far", BULL_PREV, (4301.0, 4305.5, 4301.0, 4305.0), {}),
    ("range too small", BULL_PREV, BULL_TRIGGER, {"half": 2.5}),
    ("doji trigger", BULL_PREV, (4302.0, 4305.5, 4300.3, 4302.0), {}),
])
def test_engulfing_rejects(label: str, previous: tuple[float, float, float, float],
                           trigger: tuple[float, float, float, float],
                           kwargs: dict[str, float]) -> None:
    assert engulfing.detect(engulfing_input(previous, trigger, **kwargs)) == (), label


def test_engulfing_requires_price_to_come_from_outside_the_level() -> None:
    inp = engulfing_input((4299.9, 4300.0, 4296.9, 4297.0), (4299.5, 4302.6, 4299.45, 4302.5),
                          price=4299.0)
    assert engulfing.detect(inp) == ()
    assert displacement.detect(inp) != ()      # the same bar still displaces through 4300


def test_engulfing_needs_two_bars_and_a_volatility_unit() -> None:
    lone = setup_input(NOON + M15, M15=(bar(NOON, *BULL_TRIGGER),))
    assert engulfing.detect(lone) == ()
    short = setup_input(NOON + M15, M15=(bar(NOON - M15, *BULL_PREV), bar(NOON, *BULL_TRIGGER)),
                        D1=(day_bar(TUE, 4330.0, 4300.0),))
    assert engulfing.detect(short) == ()
