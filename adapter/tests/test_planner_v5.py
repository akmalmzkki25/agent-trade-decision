from app.layering.planner_v5 import BASE_MAGIC, LAYERS_PER_BURST, build_burst_v5
from app.scenarios.scalp_micro import ScalpMicro

from .fixtures_v5 import make_v5_request


def _buy_setup():
    return make_v5_request(tick_momentum_signed=1.0, bb_pos=0.20, rsi_centered=0.10)


def _sell_setup():
    return make_v5_request(tick_momentum_signed=-1.0, bb_pos=-0.20, rsi_centered=-0.10)


def test_burst_has_3_market_layers():
    req = _buy_setup()
    sc = ScalpMicro().evaluate(req)
    burst = build_burst_v5(scenario=sc, req=req)
    assert len(burst.layers) == LAYERS_PER_BURST
    for layer in burst.layers:
        assert layer.order_type == "buy_market"


def test_burst_sell_recipe():
    req = _sell_setup()
    sc = ScalpMicro().evaluate(req)
    burst = build_burst_v5(scenario=sc, req=req)
    for layer in burst.layers:
        assert layer.order_type == "sell_market"


def test_burst_no_per_order_tp_sl():
    req = _buy_setup()
    sc = ScalpMicro().evaluate(req)
    burst = build_burst_v5(scenario=sc, req=req)
    for layer in burst.layers:
        assert layer.sl == 0.0
        assert layer.tp == 0.0


def test_burst_magic_within_v5_range():
    req = _buy_setup()
    sc = ScalpMicro().evaluate(req)
    burst = build_burst_v5(scenario=sc, req=req)
    for layer in burst.layers:
        assert BASE_MAGIC <= layer.magic <= BASE_MAGIC + 9


def test_burst_lots_capped_by_exposure():
    req = _buy_setup()
    sc = ScalpMicro().evaluate(req)
    burst = build_burst_v5(scenario=sc, req=req)
    # 3 layers × max_bursts (3) = 9 total. exposure cap 0.30 / 9 = 0.033 max per layer.
    for layer in burst.layers:
        assert layer.lots <= 0.04


def test_burst_carries_exit_rules():
    req = _buy_setup()
    sc = ScalpMicro().evaluate(req)
    burst = build_burst_v5(scenario=sc, req=req)
    assert burst.exit_rules.basket_tp_usd == 5.0
    assert burst.exit_rules.basket_sl_usd == 30.0
    assert burst.exit_rules.max_lifetime_seconds == 120
    assert burst.exit_rules.max_bursts_per_basket == 3


# --- Exposure cap must be a hard limit (review HIGH) -----------------------


def test_burst_vetoed_when_broker_minimum_exceeds_exposure_cap():
    """
    If the broker's minimum lot is larger than the per-layer share of the
    exposure cap, rounding back up to volume_min would trade above the
    configured risk limit. The planner must refuse instead.
    """
    req = _buy_setup()
    # 3 layers x 3 bursts = 9 positions; cap 0.05 lots => 0.0055 per layer,
    # well under the 0.01 broker minimum.
    req.risk_state.max_symbol_exposure_lots = 0.05
    sc = ScalpMicro().evaluate(req)
    burst = build_burst_v5(scenario=sc, req=req)
    assert burst.layers == []
    assert "EXPOSURE_CAP_BELOW_BROKER_MINIMUM" in burst.reason_codes


def test_worst_case_exposure_stays_within_cap():
    """Every layer of every allowed burst together must fit inside the cap."""
    req = _buy_setup()
    sc = ScalpMicro().evaluate(req)
    burst = build_burst_v5(scenario=sc, req=req)
    max_bursts = burst.exit_rules.max_bursts_per_basket
    worst_case = sum(layer.lots for layer in burst.layers) * max_bursts
    assert worst_case <= req.risk_state.max_symbol_exposure_lots + 1e-9


def test_burst_index_out_of_range_is_refused_by_planner():
    """The planner enforces its own bound, not just the endpoint's."""
    req = _buy_setup()
    req.active_basket_bursts = 99
    sc = ScalpMicro().evaluate(req)
    burst = build_burst_v5(scenario=sc, req=req)
    assert burst.layers == []
    assert "BURST_INDEX_OUT_OF_RANGE" in burst.reason_codes
