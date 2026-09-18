"""Agent plan v2 against the packet limits."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from app.v6.deliberation.agent_entry import EntryLimits, modify_distance
from app.v6.deliberation.plan_rules import (
    PROBLEM_PENDING_EXPIRY, PROBLEM_STEP_TOO_CLOSE, PROBLEM_STOP_NOT_BEYOND,
    PROBLEM_TIME_LIMIT, PROBLEM_TP1_TOO_CLOSE, PROBLEM_TP3_TRIMMED, PlanBounds,
    ladder_after_exit, lots_problem, plan_candidate, plan_problems_v2, trade_plan,
)
from app.v6.schemas.operator_plan import EntryPlanV2
from app.v6.types import SymbolSpec

BID, ASK = 4366.61, 4366.89
LIMITS = EntryLimits(
    agent_entry_id="agent-1789650900", tick_size=0.01, digits=2, bid=BID, ask=ASK,
    max_entry_distance=18.44, stop_floor=6.0, max_stop_distance=7.16, min_reward_r=1.0,
    max_reward_r=5.0, default_reward_r=2.0, risk_budget_usd=9.12, modify_distance=0.38)
BOUNDS = PlanBounds(entry=LIMITS, min_tp1_r=0.5, time_limit_min=60, time_limit_max=240,
                    pending_expiry_min=15, pending_expiry_max=60, volume_min=0.01,
                    lots_step=0.01, max_lots=0.03)


def plan(**changes: Any) -> EntryPlanV2:
    document: dict[str, Any] = {
        "side": "buy", "order_type": "LIMIT", "entry": 4360.5, "sl": 4353.5,
        "tp1": 4366.0, "tp2": 4371.0, "tp3": 4374.5, "sl_after_tp1": 4361.0,
        "sl_after_tp2": 4366.0, "time_limit_min": 150, "pending_expiry_min": 30,
        "lots": 0.01}
    return EntryPlanV2.model_validate({**document, **changes})


def codes(found: tuple[Any, ...]) -> list[str]:
    return [item.code for item in found]


def test_a_valid_limit_plan() -> None:
    assert plan_problems_v2(plan(), BOUNDS) == ()


def test_stop_orders_must_sit_beyond_the_quote() -> None:
    assert LIMITS.buy_stop_min == round(ASK + 0.38, 2)
    assert LIMITS.sell_stop_max == round(BID - 0.38, 2)
    good = plan(order_type="STOP", entry=4370.0, sl=4363.5, tp1=4374.0, tp2=4378.0,
                tp3=4384.0, sl_after_tp1=4370.5, sl_after_tp2=4374.0)
    assert plan_problems_v2(good, BOUNDS) == ()
    near = plan(order_type="STOP", entry=4367.0, sl=4360.5, tp1=4371.0, tp2=4375.0,
                tp3=4381.0, sl_after_tp1=None, sl_after_tp2=None)
    assert PROBLEM_STOP_NOT_BEYOND in codes(plan_problems_v2(near, BOUNDS))


def test_an_entry_beyond_the_distance_limit_is_too_far() -> None:
    far = plan(entry=4345.0, sl=4338.0, tp1=4350.5, tp2=4355.5, tp3=4359.0,
               sl_after_tp1=4345.5, sl_after_tp2=4350.5)
    assert ASK - far.entry > LIMITS.max_entry_distance
    assert codes(plan_problems_v2(far, BOUNDS)) == ["ENTRY_TOO_FAR"]


def test_a_market_plan_is_judged_from_the_quote() -> None:
    market = plan(order_type="MARKET", entry=None, pending_expiry_min=None, sl=4359.9,
                  tp1=4370.5, tp2=4374.0, tp3=4380.0, sl_after_tp1=None, sl_after_tp2=None)
    assert plan_problems_v2(market, BOUNDS) == ()


@pytest.mark.parametrize(("changes", "code"), [
    ({"order_type": "MARKET"}, "PLAN_SHAPE"),
    ({"pending_expiry_min": None}, "PLAN_SHAPE"),
    ({"tp1": 4363.0}, PROBLEM_TP1_TOO_CLOSE),
    ({"sl_after_tp1": 4365.8}, PROBLEM_STEP_TOO_CLOSE),
    ({"sl_after_tp2": 4370.8}, PROBLEM_STEP_TOO_CLOSE),
    ({"time_limit_min": 45}, PROBLEM_TIME_LIMIT),
    ({"pending_expiry_min": 10}, PROBLEM_PENDING_EXPIRY),
    ({"tp2": 4365.0}, "LADDER_ORDER"),
    ({"sl": 4345.0}, "STOP_TOO_WIDE"),
    ({"sl": 4356.5}, "STOP_TOO_TIGHT"),
    ({"tp3": 4400.0}, "REWARD_TOO_LARGE"),
    ({"entry": 4367.0}, "LIMIT_NOT_PASSIVE"),
])
def test_plan_problems(changes: dict[str, Any], code: str) -> None:
    assert code in codes(plan_problems_v2(plan(**changes), BOUNDS))


@pytest.mark.parametrize(("lots", "ok"), [(0.01, True), (0.03, True), (0.04, False),
                                          (0.015, False), (0.005, False)])
def test_lots(lots: float, ok: bool) -> None:
    assert (lots_problem(lots, BOUNDS) is None) is ok


def test_candidate_and_trade_plan() -> None:
    chosen = plan()
    candidate = plan_candidate(chosen, BOUNDS, bar_t=1_789_650_000)
    assert (candidate.entry, candidate.invalidation) == (4360.5, 4353.5)
    assert candidate.features["reward_r"] == pytest.approx(2.0)
    assert candidate.features["stop_order"] == 0.0
    traded = trade_plan(chosen)
    assert (traded.order_type, traded.tp1, traded.sl_after_tp2) == ("LIMIT", 4366.0, 4366.0)
    assert (traded.time_limit_s, traded.pending_expiry_s) == (9000, 1800)
    assert trade_plan(chosen.model_copy(update={"sl_after_tp1": None})).sl_after_tp1 == 0.0


def test_a_trimmed_target_must_stay_beyond_tp2() -> None:
    assert ladder_after_exit(plan(), 4374.4) is None
    refusal = ladder_after_exit(plan(), 4370.9)
    assert refusal is not None and refusal.startswith(PROBLEM_TP3_TRIMMED)


def test_modify_distance() -> None:
    spec = SymbolSpec(digits=2, point=0.01, tick_size=0.01, tick_value=1.0,
                      tick_value_loss=1.0, contract_size=100.0, volume_min=0.01,
                      volume_step=0.01, volume_max=50.0, stops_level=1, freeze_level=0)
    assert modify_distance(spec, 0.28) == pytest.approx(0.39)


def test_limits_without_a_modify_distance_keep_their_old_shape() -> None:
    legacy = replace(LIMITS, modify_distance=0.0)
    assert legacy.buy_stop_min == round(ASK, 2)
    assert legacy.sell_stop_max == round(BID, 2)
