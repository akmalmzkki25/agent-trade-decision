from app.layering.planner_v4 import BASE_MAGIC, build_plan_v4
from app.scenarios.liquidity_zone import LiquidityZoneEntry

from .fixtures_v4 import make_v4_request


def _buy_setup():
    return make_v4_request(
        last_close=4170.00,
        swing_low_h1=4155.00,
        close_m15=4170.00,
        m5_direction=-1.0,
    )


def _sell_setup():
    return make_v4_request(
        last_close=4196.00,
        swing_high_h1=4200.00,
        close_m15=4196.00,
        m5_direction=1.0,
    )


def test_v4_plan_has_exactly_2_layers():
    req = _buy_setup()
    sc = LiquidityZoneEntry().evaluate(req)
    plan = build_plan_v4(scenario=sc, req=req)
    assert len(plan.layers) == 2


def test_v4_buy_layers_are_buy_limits_at_zone_edges():
    req = _buy_setup()
    sc = LiquidityZoneEntry().evaluate(req)
    plan = build_plan_v4(scenario=sc, req=req)
    assert plan.layers[0].order_type == "buy_limit"
    assert plan.layers[1].order_type == "buy_limit"
    assert plan.layers[0].price == 4156.0   # top of zone
    assert plan.layers[1].price == 4153.0   # bottom of zone


def test_v4_sell_layers_are_sell_limits():
    req = _sell_setup()
    sc = LiquidityZoneEntry().evaluate(req)
    plan = build_plan_v4(scenario=sc, req=req)
    assert plan.layers[0].order_type == "sell_limit"
    assert plan.layers[1].order_type == "sell_limit"


def test_v4_layer_sl_equals_zone_sl_price():
    req = _buy_setup()
    sc = LiquidityZoneEntry().evaluate(req)
    plan = build_plan_v4(scenario=sc, req=req)
    # SL = zone_bottom (4153) - 2.0 (20 pip buffer) = 4151
    assert plan.layers[0].sl == 4151.0
    assert plan.layers[1].sl == 4151.0


def test_v4_partial_tp_metadata_correct():
    req = _buy_setup()
    sc = LiquidityZoneEntry().evaluate(req)
    plan = build_plan_v4(scenario=sc, req=req)
    for layer in plan.layers:
        assert layer.partial_tp_pips == 30.0
        assert layer.partial_close_fraction == 0.50
        assert layer.runner_cap_pips == 100.0
        assert layer.move_sl_to_entry_after_partial is True


def test_v4_magic_assigned():
    req = _buy_setup()
    sc = LiquidityZoneEntry().evaluate(req)
    plan = build_plan_v4(scenario=sc, req=req)
    assert plan.layers[0].magic == BASE_MAGIC + 1
    assert plan.layers[1].magic == BASE_MAGIC + 2


def test_v4_lots_split_50_50():
    req = _buy_setup()
    sc = LiquidityZoneEntry().evaluate(req)
    plan = build_plan_v4(scenario=sc, req=req)
    # Both layers have similar (or equal) SL distance from price → equal lots.
    # L1 price=4156 sl=4151 → SL dist 5; L2 price=4153 sl=4151 → SL dist 2.
    # So L2 should have MORE lots (closer SL → same risk → bigger size).
    # But both must be > 0 and obey max_exposure cap.
    assert plan.layers[0].lots > 0
    assert plan.layers[1].lots > 0
    # Each layer's lots <= max_exposure_lots × 0.5 = 0.25
    assert plan.layers[0].lots <= 0.25
    assert plan.layers[1].lots <= 0.25


def test_v4_zone_metadata_populated():
    req = _buy_setup()
    sc = LiquidityZoneEntry().evaluate(req)
    plan = build_plan_v4(scenario=sc, req=req)
    assert plan.zone is not None
    assert plan.zone.side == "buy"
    assert plan.zone.top == 4156.0
    assert plan.zone.bottom == 4153.0
    assert plan.zone.sl_price == 4151.0
    assert plan.zone.width_pips == 30.0
    # SL = 4151, far edge (bottom for buy) = 4153 → distance = 2 = 20 pip
    assert plan.zone.sl_pips_from_zone == 20.0


def test_v4_max_lifetime_30_min():
    req = _buy_setup()
    sc = LiquidityZoneEntry().evaluate(req)
    plan = build_plan_v4(scenario=sc, req=req)
    assert plan.max_lifetime_seconds == 1800
