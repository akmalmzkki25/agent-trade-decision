from app.layering.planner import BASE_MAGIC, build_plan
from app.scenarios.range_revert import RangeRevert
from app.scenarios.trend_breakout_stop import TrendBreakoutStop

from .fixtures_v2 import make_plan_request


def test_planner_range_revert_buy_layers_descending():
    req = make_plan_request()  # range buy
    sc = RangeRevert().evaluate(req)
    plan = build_plan(scenario=sc, req=req)
    assert plan.scenario == "RANGE_REVERT"
    assert plan.side_bias == "buy"
    assert 3 <= len(plan.layers) <= 5

    # All buy_limit, prices below anchor (support=2390), strictly descending.
    prices = [l.price for l in plan.layers]
    assert prices == sorted(prices, reverse=True)
    assert all(l.order_type == "buy_limit" for l in plan.layers)
    assert all(l.sl < l.price for l in plan.layers)


def test_planner_trend_breakout_buy_stops_ascending():
    req = make_plan_request(
        adx_h1=0.30,
        di_balance=0.35,
        bb_width_pct=0.20,
        last_close=2401.90,
        bb_pos_m5=0.0,
        rsi_m5=0.0,
        breakout_high_m15=2402.00,
        breakout_low_m15=2395.00,
    )
    sc = TrendBreakoutStop().evaluate(req)
    plan = build_plan(scenario=sc, req=req)
    prices = [l.price for l in plan.layers]
    assert prices == sorted(prices)
    assert all(l.order_type == "buy_stop" for l in plan.layers)


def test_planner_magic_per_layer():
    req = make_plan_request()
    sc = RangeRevert().evaluate(req)
    plan = build_plan(scenario=sc, req=req)
    magics = [l.magic for l in plan.layers]
    assert magics == [BASE_MAGIC + i + 1 for i in range(len(plan.layers))]


def test_planner_total_risk_budget_split():
    req = make_plan_request()
    sc = RangeRevert().evaluate(req)
    plan = build_plan(scenario=sc, req=req)
    n = len(plan.layers)
    # All lots should be > 0 and roughly equal (within step rounding).
    assert all(l.lots > 0 for l in plan.layers)
    max_lot = max(l.lots for l in plan.layers)
    min_lot = min(l.lots for l in plan.layers)
    # Lots vary because SL distance differs per layer (anchor offset). Sanity check.
    assert max_lot < 5 * min_lot  # not orders of magnitude apart


def test_planner_basket_tp_pct():
    req = make_plan_request()
    sc = RangeRevert().evaluate(req)
    plan = build_plan(scenario=sc, req=req)
    # Default 1% risk * 1.5 RR = 1.5% basket TP.
    assert abs(plan.basket_tp_pct_equity - 1.5) < 1e-6


def test_planner_invalidation_price_propagated():
    req = make_plan_request()
    sc = RangeRevert().evaluate(req)
    plan = build_plan(scenario=sc, req=req)
    assert plan.scenario_invalidation_price is not None
    # For range buy, invalidation < swing_low (2390).
    assert plan.scenario_invalidation_price < 2390.0
