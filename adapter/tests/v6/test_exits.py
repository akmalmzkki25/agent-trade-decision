"""Tests for app.v6.risk.exits: stop, target and time-barrier geometry (kn/07, kn/08)."""

from __future__ import annotations

import dataclasses
import math

import pytest

from app.v6.risk import limits
from app.v6.risk.exits import (
    CODE_BAD_GEOMETRY,
    CODE_BAD_INPUT,
    CODE_STOP_BELOW_FLOOR,
    CODE_STOPS_LEVEL,
    CODE_TP_BELOW_1R,
    LABEL_SL_ROUND_SHIFTED,
    LABEL_TP_ROUND_PULLED,
    LABEL_UNMEASURED,
    build_exit_plan,
)
from app.v6.types import Candidate, ExitPlan, Refusal, SymbolSpec

XAU = SymbolSpec(
    digits=2,
    point=0.01,
    tick_size=0.01,
    tick_value=1.0,
    tick_value_loss=1.0,
    contract_size=100.0,
    volume_min=0.01,
    volume_step=0.01,
    volume_max=100.0,
)
SPREAD = 0.30
FRICTION = 0.40
BARRIER_S = 2 * 3600
DEFAULTS = {
    "spread_price": SPREAD,
    "friction_price": FRICTION,
    "spec": XAU,
    "stop_floor_points": limits.MIN_STOP_FLOOR_POINTS,
    "tp_r_multiple": 2.0,
    "time_barrier_s": BARRIER_S,
}


def _candidate(side: str, entry: float, invalidation: float) -> Candidate:
    return Candidate(
        candidate_id="c-1",
        setup="displacement_m15",
        side=side,  # type: ignore[arg-type]
        entry=entry,
        invalidation=invalidation,
        bar_t=1_760_000_000,
    )


def _plan(side: str, entry: float, invalidation: float, **overrides):
    return build_exit_plan(_candidate(side, entry, invalidation), **{**DEFAULTS, **overrides})


def _assert_plan(result) -> ExitPlan:
    assert isinstance(result, ExitPlan), result
    return result


def _assert_refused(result, codes: tuple[str, ...]) -> Refusal:
    assert isinstance(result, Refusal), result
    assert result.codes == codes
    assert result.detail
    return result


class TestHappyPaths:
    def test_long_uses_invalidation_as_stop_and_two_r_target(self):
        plan = _assert_plan(_plan("buy", 4312.40, 4304.20))
        assert plan == ExitPlan(
            side="buy",
            entry=4312.40,
            sl=4304.20,
            tp=4328.80,
            stop_distance=8.20,
            reward_r=2.0,
            time_barrier_s=BARRIER_S,
            labels=(LABEL_UNMEASURED,),
        )

    def test_short_adds_spread_buffer_to_stop(self):
        plan = _assert_plan(_plan("sell", 4287.60, 4295.10))
        assert plan == ExitPlan(
            side="sell",
            entry=4287.60,
            sl=4295.40,
            tp=4272.00,
            stop_distance=7.80,
            reward_r=2.0,
            time_barrier_s=BARRIER_S,
            labels=(LABEL_UNMEASURED,),
        )

    def test_short_buffer_tracks_live_spread(self):
        plan = _assert_plan(_plan("sell", 4287.60, 4295.10, spread_price=0.25))
        assert plan.sl == 4295.35
        assert plan.stop_distance == 7.75

    def test_time_barrier_ceiling_is_accepted(self):
        plan = _assert_plan(_plan("buy", 4312.40, 4304.20, time_barrier_s=limits.MAX_TIME_BARRIER_S))
        assert plan.time_barrier_s == limits.MAX_TIME_BARRIER_S

    def test_other_target_multiple_is_honoured(self):
        plan = _assert_plan(_plan("buy", 4312.00, 4304.00, tp_r_multiple=1.5))
        assert plan.tp == 4324.00
        assert plan.reward_r == 1.5


class TestStopRoundAvoidance:
    def test_long_stop_just_below_4300_moves_further_down(self):
        plan = _assert_plan(_plan("buy", 4308.00, 4299.50))
        assert plan.sl == 4298.70  # 4300 - 1.00 zone - 0.30 spread
        assert plan.stop_distance == 9.30
        assert plan.tp == 4326.60
        assert plan.labels == (LABEL_SL_ROUND_SHIFTED, LABEL_UNMEASURED)

    def test_long_stop_on_round_level_moves_down(self):
        plan = _assert_plan(_plan("buy", 4307.00, 4300.00))
        assert plan.sl == 4298.70
        assert plan.stop_distance == 8.30

    @pytest.mark.parametrize(
        ("invalidation", "expected_sl", "shifted"),
        [(4299.00, 4298.70, True), (4298.99, 4298.99, False), (4300.01, 4300.01, False)],
    )
    def test_long_zone_edges(self, invalidation, expected_sl, shifted):
        plan = _assert_plan(_plan("buy", 4308.00, invalidation))
        assert plan.sl == expected_sl
        assert (LABEL_SL_ROUND_SHIFTED in plan.labels) is shifted

    def test_short_stop_just_above_4250_moves_further_up(self):
        plan = _assert_plan(_plan("sell", 4242.00, 4250.40))
        assert plan.sl == 4251.30  # 4250 + 1.00 zone + 0.30 spread
        assert plan.stop_distance == 9.30
        assert plan.tp == 4223.40
        assert plan.labels == (LABEL_SL_ROUND_SHIFTED, LABEL_UNMEASURED)

    def test_short_spread_buffer_can_push_stop_into_the_zone(self):
        plan = _assert_plan(_plan("sell", 4242.00, 4249.80))
        assert plan.sl == 4251.30
        assert LABEL_SL_ROUND_SHIFTED in plan.labels

    @pytest.mark.parametrize(
        ("invalidation", "expected_sl", "shifted"),
        [(4250.70, 4251.30, True), (4250.71, 4251.01, False), (4249.50, 4249.80, False)],
    )
    def test_short_zone_edges(self, invalidation, expected_sl, shifted):
        plan = _assert_plan(_plan("sell", 4241.00, invalidation))
        assert plan.sl == expected_sl
        assert (LABEL_SL_ROUND_SHIFTED in plan.labels) is shifted

    def test_round_step_selects_which_levels_count(self):
        near_4250 = _assert_plan(_plan("sell", 4242.00, 4250.40, round_step=100.0))
        near_4300 = _assert_plan(_plan("buy", 4308.00, 4299.50, round_step=100.0))
        assert near_4250.sl == 4250.70
        assert LABEL_SL_ROUND_SHIFTED not in near_4250.labels
        assert near_4300.sl == 4298.70


class TestTargetRoundAvoidance:
    def test_long_target_crossing_4350_is_pulled_before_it(self):
        plan = _assert_plan(_plan("buy", 4336.00, 4328.00))
        assert plan.tp == 4349.90
        assert plan.reward_r == 1.7375
        assert plan.labels == (LABEL_TP_ROUND_PULLED, LABEL_UNMEASURED)

    def test_long_target_on_round_level_is_pulled(self):
        plan = _assert_plan(_plan("buy", 4334.00, 4326.00))
        assert plan.tp == 4349.90
        assert plan.reward_r == 1.9875

    def test_long_target_just_before_round_level_is_kept(self):
        plan = _assert_plan(_plan("buy", 4333.95, 4325.95))
        assert plan.tp == 4349.95
        assert plan.reward_r == 2.0
        assert LABEL_TP_ROUND_PULLED not in plan.labels

    def test_short_target_crossing_4200_is_pulled_above_it(self):
        plan = _assert_plan(_plan("sell", 4210.00, 4217.70))
        assert plan.sl == 4218.00
        assert plan.tp == 4200.10
        assert plan.reward_r == 1.2375
        assert plan.labels == (LABEL_TP_ROUND_PULLED, LABEL_UNMEASURED)

    def test_pull_below_one_r_is_refused(self):
        _assert_refused(_plan("buy", 4344.00, 4336.00), (CODE_TP_BELOW_1R,))

    def test_short_pull_below_one_r_is_refused(self):
        _assert_refused(_plan("sell", 4206.00, 4213.70), (CODE_TP_BELOW_1R,))

    def test_pull_to_exactly_one_r_is_accepted(self):
        plan = _assert_plan(_plan("buy", 4341.90, 4333.90))
        assert plan.tp == 4349.90
        assert plan.reward_r == 1.0

    def test_pull_behind_entry_is_refused(self):
        # Entry 5 cents under 4350: the pulled target lands on the wrong side of entry.
        _assert_refused(_plan("buy", 4349.95, 4341.95), (CODE_TP_BELOW_1R,))

    def test_stop_shift_and_target_pull_combine(self):
        plan = _assert_plan(_plan("sell", 4284.00, 4300.20))
        assert plan.sl == 4301.30
        assert plan.stop_distance == 17.30
        assert plan.tp == 4250.10
        assert plan.reward_r == 1.9595
        assert plan.labels == (LABEL_SL_ROUND_SHIFTED, LABEL_TP_ROUND_PULLED, LABEL_UNMEASURED)


class TestStopFloor:
    @pytest.mark.parametrize(
        ("overrides", "refused_stop", "accepted_stop"),
        [
            pytest.param({}, 5.99, 6.00, id="default-600-points"),
            pytest.param({"stop_floor_points": 800}, 7.99, 8.00, id="configured-points"),
            pytest.param({"spread_price": 0.90}, 8.99, 9.00, id="ten-times-spread"),
            pytest.param({"friction_price": 0.95}, 9.49, 9.50, id="friction-over-ten-pct"),
        ],
    )
    def test_each_floor_component_binds(self, overrides, refused_stop, accepted_stop):
        entry = 4312.00
        refused = _plan("buy", entry, round(entry - refused_stop, 2), **overrides)
        accepted = _assert_plan(_plan("buy", entry, round(entry - accepted_stop, 2), **overrides))
        _assert_refused(refused, (CODE_STOP_BELOW_FLOOR,))
        assert accepted.stop_distance == accepted_stop
        assert accepted.sl == round(entry - accepted_stop, 2)

    def test_short_floor_counts_the_spread_buffer(self):
        accepted = _assert_plan(_plan("sell", 4287.60, 4293.40))
        refused = _plan("sell", 4287.60, 4293.20)
        assert accepted.stop_distance == 6.10
        _assert_refused(refused, (CODE_STOP_BELOW_FLOOR,))

    def test_stop_that_rounds_to_zero_is_refused_not_divided(self):
        # A degenerate spec makes the floor ~0; the zero stop must still be refused.
        spec = _spec(point=1e-15)
        result = _plan(
            "buy", 4312.4049, 4312.3999, spec=spec, spread_price=1e-15, friction_price=1e-15
        )
        _assert_refused(result, (CODE_STOP_BELOW_FLOOR,))

    def test_round_shift_can_lift_a_tight_stop_over_the_floor(self):
        # 5.50 structural distance is under the floor; the round-number shift adds 1.30.
        plan = _assert_plan(_plan("buy", 4305.50, 4300.00))
        assert plan.stop_distance == 6.80


class TestStopsLevel:
    def test_stop_inside_broker_stops_level_is_refused(self):
        spec = dataclasses.replace(XAU, stops_level=1000)
        _assert_refused(_plan("buy", 4312.00, 4304.00, spec=spec), (CODE_STOPS_LEVEL,))

    def test_stop_at_broker_stops_level_is_accepted(self):
        spec = dataclasses.replace(XAU, stops_level=800)
        plan = _assert_plan(_plan("buy", 4312.00, 4304.00, spec=spec))
        assert plan.stop_distance == 8.00

    def test_pulled_target_inside_stops_level_reports_both_codes(self):
        spec = dataclasses.replace(XAU, stops_level=700)
        result = _plan("buy", 4344.00, 4336.00, spec=spec)
        _assert_refused(result, (CODE_TP_BELOW_1R, CODE_STOPS_LEVEL))


class TestGeometry:
    @pytest.mark.parametrize(
        ("side", "entry", "invalidation"),
        [
            pytest.param("buy", 4312.00, 4312.00, id="long-stop-at-entry"),
            pytest.param("buy", 4312.00, 4315.00, id="long-stop-above-entry"),
            pytest.param("sell", 4287.60, 4287.60, id="short-stop-at-entry"),
            pytest.param("sell", 4287.60, 4280.00, id="short-stop-below-entry"),
            pytest.param("sell", 4287.60, 4287.50, id="short-spread-would-mask-wrong-side"),
        ],
    )
    def test_wrong_side_stop_is_refused(self, side, entry, invalidation):
        _assert_refused(_plan(side, entry, invalidation), (CODE_BAD_GEOMETRY,))


def _spec(**changes) -> SymbolSpec:
    return dataclasses.replace(XAU, **changes)


BAD_OVERRIDES = [
    pytest.param({"spread_price": 0.0}, id="spread-zero"),
    pytest.param({"spread_price": -0.1}, id="spread-negative"),
    pytest.param({"spread_price": math.nan}, id="spread-nan"),
    pytest.param({"spread_price": "0.30"}, id="spread-string"),
    pytest.param({"friction_price": 0.0}, id="friction-zero"),
    pytest.param({"friction_price": math.inf}, id="friction-inf"),
    pytest.param({"stop_floor_points": limits.MIN_STOP_FLOOR_POINTS - 1}, id="floor-loosened"),
    pytest.param({"stop_floor_points": 600.0}, id="floor-float"),
    pytest.param({"stop_floor_points": True}, id="floor-bool"),
    pytest.param({"tp_r_multiple": 0.0}, id="tp-zero"),
    pytest.param({"tp_r_multiple": math.nan}, id="tp-nan"),
    pytest.param({"time_barrier_s": 0}, id="barrier-zero"),
    pytest.param({"time_barrier_s": -60}, id="barrier-negative"),
    pytest.param({"time_barrier_s": limits.MAX_TIME_BARRIER_S + 1}, id="barrier-over-ceiling"),
    pytest.param({"time_barrier_s": 7200.0}, id="barrier-float"),
    pytest.param({"time_barrier_s": True}, id="barrier-bool"),
    pytest.param({"round_step": 0.0}, id="round-step-zero"),
    pytest.param({"round_step": 5.0}, id="round-step-too-fine"),
    pytest.param({"round_step": math.inf}, id="round-step-inf"),
    pytest.param({"spec": _spec(point=0.0)}, id="point-zero"),
    pytest.param({"spec": _spec(point=math.nan)}, id="point-nan"),
    pytest.param({"spec": _spec(digits=-1)}, id="digits-negative"),
    pytest.param({"spec": _spec(digits=9)}, id="digits-too-many"),
    pytest.param({"spec": _spec(digits=2.0)}, id="digits-float"),
    pytest.param({"spec": _spec(stops_level=-1)}, id="stops-level-negative"),
    pytest.param({"spec": _spec(stops_level=1.5)}, id="stops-level-float"),
]


class TestBadInput:
    @pytest.mark.parametrize("overrides", BAD_OVERRIDES)
    def test_bad_parameters_are_refused(self, overrides):
        _assert_refused(_plan("buy", 4312.40, 4304.20, **overrides), (CODE_BAD_INPUT,))

    @pytest.mark.parametrize(
        ("side", "entry", "invalidation"),
        [
            pytest.param("long", 4312.40, 4304.20, id="unknown-side"),
            pytest.param("buy", math.nan, 4304.20, id="entry-nan"),
            pytest.param("buy", math.inf, 4304.20, id="entry-inf"),
            pytest.param("buy", 0.0, -8.0, id="entry-zero"),
            pytest.param("sell", -1.0, 7.0, id="entry-negative"),
            pytest.param("buy", True, 0.5, id="entry-bool"),
            pytest.param("buy", 4312.40, math.nan, id="invalidation-nan"),
            pytest.param("buy", 4312.40, 0.0, id="invalidation-zero"),
        ],
    )
    def test_bad_candidates_are_refused(self, side, entry, invalidation):
        _assert_refused(_plan(side, entry, invalidation), (CODE_BAD_INPUT,))


class TestRounding:
    def test_prices_are_rounded_to_symbol_digits(self):
        plan = _assert_plan(_plan("buy", 4312.4040, 4304.2049))
        assert (plan.entry, plan.sl, plan.tp) == (4312.40, 4304.20, 4328.80)
        assert plan.stop_distance == 8.20

    def test_three_digit_symbol_keeps_three_decimals(self):
        spec = _spec(digits=3, point=0.001, tick_size=0.001, tick_value=0.1, tick_value_loss=0.1)
        plan = _assert_plan(_plan("buy", 4312.4044, 4304.2046, spec=spec, stop_floor_points=6000))
        assert (plan.entry, plan.sl, plan.tp) == (4312.404, 4304.205, 4328.802)
        assert plan.stop_distance == 8.199
        assert plan.reward_r == 2.0

    def test_three_digit_symbol_floor_uses_its_own_point(self):
        spec = _spec(digits=3, point=0.001, tick_size=0.001, tick_value=0.1, tick_value_loss=0.1)
        result = _plan("buy", 4312.000, 4305.000, spec=spec, stop_floor_points=8000)
        _assert_refused(result, (CODE_STOP_BELOW_FLOOR,))


class TestImmutability:
    def test_inputs_are_untouched_and_results_are_new(self):
        candidate = _candidate("sell", 4284.00, 4300.20)
        before = dataclasses.replace(candidate)
        spec_before = dataclasses.replace(XAU)
        first = build_exit_plan(candidate, **DEFAULTS)
        second = build_exit_plan(candidate, **DEFAULTS)
        assert candidate == before
        assert XAU == spec_before
        assert first == second
        assert first is not second
        assert first is not candidate
        assert candidate.invalidation == 4300.20

    def test_plan_is_frozen(self):
        plan = _assert_plan(_plan("buy", 4312.40, 4304.20))
        with pytest.raises(dataclasses.FrozenInstanceError):
            plan.sl = 4300.00  # type: ignore[misc]
