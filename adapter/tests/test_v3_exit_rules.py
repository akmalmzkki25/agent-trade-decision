"""V3 exit_rules schema + planner default tests."""
from fastapi.testclient import TestClient

from app.layering.planner_v3 import build_plan_v3
from app.main import app
from app.models import V3ExitRules
from app.scenarios.aggressive_bias import AggressiveBias

from .fixtures_v3 import make_v3_request

client = TestClient(app)


def test_exit_rules_defaults_match_spec():
    r = V3ExitRules()
    assert r.stage1_trigger_pips == 30.0
    assert r.stage1_close_count == 3
    assert r.stage1_sl_offset_pips == -20.0
    assert r.stage1_cancel_pendings is True
    assert r.stage1_min_open_positions == 3
    assert r.stage2_trigger_pips == 50.0
    assert r.stage2_sl_offset_pips == 0.0
    assert r.stage3_trigger_pips == 60.0
    assert r.stage3_close_count == 1
    assert r.stage3_min_remaining_after == 1
    assert r.runner_cap_pips == 100.0


def test_planner_v3_emits_exit_rules_in_plan():
    req = make_v3_request(
        last_close=2410.00,
        ema20_m5=2400.00,
        ema50_m5=2400.00,
        ema50_m15=2400.00,
        di_balance=0.30,
        rsi_m1=0.30,
        last3_dir=1.0,
    )
    sc = AggressiveBias().evaluate(req)
    plan = build_plan_v3(scenario=sc, req=req, slot="A")
    assert plan.exit_rules.stage1_trigger_pips == 30.0
    assert plan.exit_rules.runner_cap_pips == 100.0


def test_v3_endpoint_response_includes_exit_rules():
    req = make_v3_request(
        m1_atr_pct=0.85,
        last3_dir=1.0,
        rsi_m1=0.10,
        last_close=2400.50,
        ema20_m5=2400.10,
        ema50_m5=2399.80,
        adx_m15=0.25,
        di_balance=0.25,
    )
    r = client.post(
        "/v3/plan",
        content=req.model_dump_json(),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    rules = body["plan"]["exit_rules"]
    assert rules["stage1_trigger_pips"] == 30.0
    assert rules["stage1_close_count"] == 3
    assert rules["stage1_sl_offset_pips"] == -20.0
    assert rules["stage1_min_open_positions"] == 3
    assert rules["stage2_trigger_pips"] == 50.0
    assert rules["stage2_sl_offset_pips"] == 0.0
    assert rules["stage3_trigger_pips"] == 60.0
    assert rules["runner_cap_pips"] == 100.0


def test_exit_rules_serializes_in_empty_v3_plan():
    """Even NONE/veto responses must include exit_rules for schema stability."""
    req = make_v3_request(halted=True)
    r = client.post(
        "/v3/plan",
        content=req.model_dump_json(),
        headers={"Content-Type": "application/json"},
    )
    body = r.json()
    assert "exit_rules" in body["plan"]
    assert body["plan"]["exit_rules"]["stage1_trigger_pips"] == 30.0


def test_rr_ratio_default_is_one():
    """Default RR ratio must be 1:1 — guards against accidental config drift."""
    assert V3ExitRules().rr_ratio == 1.0


def test_planner_v3_sets_tp_at_1_to_1_rr():
    """With RR 1.0, each layer's TP distance == its SL distance from entry."""
    req = make_v3_request(
        m1_atr_pct=0.85,
        last3_dir=1.0,
        rsi_m1=0.10,
        last_close=2400.50,
        ema20_m5=2400.10,
        ema50_m5=2399.80,
        adx_m15=0.25,
        di_balance=0.25,
    )
    sc = AggressiveBias().evaluate(req)
    plan = build_plan_v3(scenario=sc, req=req, slot="A")
    for layer in plan.layers:
        assert layer.tp is not None and layer.tp > 0
        sl_dist = abs(layer.price - layer.sl)
        # Note: market layer 'price' is anchor (ask), but SL distance still measured the same.
        tp_dist = abs(layer.tp - layer.price)
        # Allow small rounding tolerance.
        assert abs(tp_dist - sl_dist) < 0.05, f"Layer {layer.layer_id}: tp_dist={tp_dist} vs sl_dist={sl_dist}"


def test_planner_v3_tp_above_entry_for_buy():
    req = make_v3_request(
        m1_atr_pct=0.85, last3_dir=1.0, rsi_m1=0.10, last_close=2400.50,
        ema20_m5=2400.10, ema50_m5=2399.80, adx_m15=0.25, di_balance=0.25,
    )
    sc = AggressiveBias().evaluate(req)
    plan = build_plan_v3(scenario=sc, req=req, slot="A")
    assert plan.side_bias == "buy"
    for layer in plan.layers:
        assert layer.tp > layer.price, f"Layer {layer.layer_id} TP must be above entry for buy"
        assert layer.sl < layer.price, f"Layer {layer.layer_id} SL must be below entry for buy"


def test_planner_v3_rr_2_doubles_tp_distance():
    """Custom RR=2 → TP distance is 2× SL distance."""
    from app.models import V3ExitRules
    req = make_v3_request(
        m1_atr_pct=0.85, last3_dir=1.0, rsi_m1=0.10, last_close=2400.50,
        ema20_m5=2400.10, ema50_m5=2399.80, adx_m15=0.25, di_balance=0.25,
    )
    sc = AggressiveBias().evaluate(req)
    plan = build_plan_v3(scenario=sc, req=req, slot="A",
                        exit_rules=V3ExitRules(rr_ratio=2.0))
    for layer in plan.layers:
        sl_dist = abs(layer.price - layer.sl)
        tp_dist = abs(layer.tp - layer.price)
        assert abs(tp_dist - 2.0 * sl_dist) < 0.05


def test_planner_v3_rr_0_leaves_tp_null():
    """RR=0 disables auto-TP (legacy staged-exit-only mode)."""
    from app.models import V3ExitRules
    req = make_v3_request(
        m1_atr_pct=0.85, last3_dir=1.0, rsi_m1=0.10, last_close=2400.50,
        ema20_m5=2400.10, ema50_m5=2399.80, adx_m15=0.25, di_balance=0.25,
    )
    sc = AggressiveBias().evaluate(req)
    plan = build_plan_v3(scenario=sc, req=req, slot="A",
                        exit_rules=V3ExitRules(rr_ratio=0.0))
    for layer in plan.layers:
        assert layer.tp is None


def test_v3_endpoint_returns_tp_in_layers():
    req = make_v3_request(
        m1_atr_pct=0.85, last3_dir=1.0, rsi_m1=0.10, last_close=2400.50,
        ema20_m5=2400.10, ema50_m5=2399.80, adx_m15=0.25, di_balance=0.25,
    )
    r = client.post(
        "/v3/plan",
        content=req.model_dump_json(),
        headers={"Content-Type": "application/json"},
    )
    body = r.json()
    assert body["status"] == "ok"
    layers = body["plan"]["layers"]
    assert len(layers) > 0
    for layer in layers:
        assert layer["tp"] is not None and layer["tp"] > 0
