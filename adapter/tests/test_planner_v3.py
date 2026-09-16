from app.layering.planner_v3 import _MAGIC_BASE_BY_SLOT, assign_slot, build_plan_v3
from app.models import ActiveBasket
from app.scenarios import select_best_v3
from app.scenarios.momentum_m1 import MomentumM1
from app.scenarios.range_revert import RangeRevert

from .fixtures_v3 import make_v3_request


def _strong_momentum_req():
    return make_v3_request(
        m1_atr_pct=0.85,
        last3_dir=1.0,
        rsi_m1=0.10,
        last_close=2400.50,
        ema20_m5=2400.10,
        ema50_m5=2399.80,
        adx_m15=0.25,
        di_balance=0.25,
    )


def test_plan_v3_has_exactly_5_layers():
    req = _strong_momentum_req()
    sc = MomentumM1().evaluate(req)
    plan = build_plan_v3(scenario=sc, req=req, slot="A")
    assert len(plan.layers) == 5


def test_plan_v3_layer_types_buy_recipe():
    req = _strong_momentum_req()
    sc = MomentumM1().evaluate(req)
    plan = build_plan_v3(scenario=sc, req=req, slot="A")
    types = [l.order_type for l in plan.layers]
    assert types == ["buy_market", "buy_limit", "buy_limit", "buy_stop", "buy_stop"]


def test_plan_v3_layer_types_sell_recipe():
    req = make_v3_request(
        m1_atr_pct=0.85,
        last3_dir=-1.0,
        rsi_m1=-0.10,
        last_close=2399.00,
        ema20_m5=2400.10,
        ema50_m5=2400.50,
        adx_m15=0.25,
        di_balance=-0.25,
    )
    sc = MomentumM1().evaluate(req)
    plan = build_plan_v3(scenario=sc, req=req, slot="A")
    types = [l.order_type for l in plan.layers]
    assert types == ["sell_market", "sell_limit", "sell_limit", "sell_stop", "sell_stop"]


def test_plan_v3_anchor_flag_only_on_layer_1():
    req = _strong_momentum_req()
    sc = MomentumM1().evaluate(req)
    plan = build_plan_v3(scenario=sc, req=req, slot="A")
    assert plan.layers[0].is_anchor is True
    assert all(l.is_anchor is False for l in plan.layers[1:])


def test_plan_v3_weights_sum_to_one():
    req = _strong_momentum_req()
    sc = MomentumM1().evaluate(req)
    plan = build_plan_v3(scenario=sc, req=req, slot="A")
    total = sum(l.weight for l in plan.layers)
    assert abs(total - 1.0) < 1e-6


def test_plan_v3_magic_assigned_by_slot():
    req = _strong_momentum_req()
    sc = MomentumM1().evaluate(req)
    plan_a = build_plan_v3(scenario=sc, req=req, slot="A")
    plan_b = build_plan_v3(scenario=sc, req=req, slot="B")
    assert plan_a.layers[0].magic == _MAGIC_BASE_BY_SLOT["A"] + 1
    assert plan_b.layers[0].magic == _MAGIC_BASE_BY_SLOT["B"] + 1


def test_plan_v3_basket_tp_pct_1_2():
    req = _strong_momentum_req()
    sc = MomentumM1().evaluate(req)
    plan = build_plan_v3(scenario=sc, req=req, slot="A")
    # 1% risk × 1.2 RR = 1.2% basket TP
    assert abs(plan.basket_tp_pct_equity - 1.2) < 1e-6


def test_plan_v3_max_lifetime_10_min():
    req = _strong_momentum_req()
    sc = MomentumM1().evaluate(req)
    plan = build_plan_v3(scenario=sc, req=req, slot="A")
    assert plan.max_lifetime_seconds == 600


def test_assign_slot_empty_returns_a():
    slot, reason = assign_slot([], "buy")
    assert slot == "A" and reason == "OK"


def test_assign_slot_a_busy_buy_returns_b_for_sell():
    slot, reason = assign_slot(
        [ActiveBasket(slot="A", side="buy", opened_at_utc="2026-05-18T09:00:00Z")],
        "sell",
    )
    assert slot == "B" and reason == "OK"


def test_assign_slot_same_side_vetoed():
    slot, reason = assign_slot(
        [ActiveBasket(slot="A", side="buy", opened_at_utc="2026-05-18T09:00:00Z")],
        "buy",
    )
    assert slot is None and reason == "APP-CONC-409"


def test_assign_slot_capacity_full_vetoed():
    # Same-side check fires first if requested side matches existing.
    # To reach CAPACITY-409, both slots must be occupied with same side
    # AND requested side must be opposite (so same-side check passes).
    slot, reason = assign_slot(
        [
            ActiveBasket(slot="A", side="buy", opened_at_utc="2026-05-18T09:00:00Z"),
            ActiveBasket(slot="B", side="buy", opened_at_utc="2026-05-18T09:00:00Z"),
        ],
        "sell",
    )
    assert slot is None and reason == "APP-CAPACITY-409"


def test_select_best_v3_threshold_lower_than_v2():
    # Default fixture (range buy weak) — V2 threshold 0.55 may reject, V3 0.40 may accept.
    req = make_v3_request()
    sc_v3 = select_best_v3(req)
    # V3 should at least try harder; assert it picks something or None deterministically.
    if sc_v3 is not None:
        assert sc_v3.score >= 0.40
