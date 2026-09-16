from fastapi.testclient import TestClient

from app.main import app
from app.models import ActiveBasket

from .fixtures_v3 import make_v3_request

client = TestClient(app)


def _strong_buy():
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


def _strong_sell():
    return make_v3_request(
        m1_atr_pct=0.85,
        last3_dir=-1.0,
        rsi_m1=-0.10,
        last_close=2399.00,
        ema20_m5=2400.10,
        ema50_m5=2400.50,
        adx_m15=0.25,
        di_balance=-0.25,
    )


def test_v3_plan_ok_assigns_slot_a():
    r = client.post(
        "/v3/plan",
        content=_strong_buy().model_dump_json(),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["plan"]["basket_slot"] == "A"
    assert len(body["plan"]["layers"]) == 5
    assert body["plan"]["layers"][0]["order_type"] == "buy_market"
    assert body["plan"]["layers"][0]["is_anchor"] is True


def test_v3_plan_with_slot_a_busy_buy_assigns_b_for_sell():
    req = _strong_sell()
    req.active_baskets = [
        ActiveBasket(slot="A", side="buy", opened_at_utc="2026-05-18T09:00:00Z")
    ]
    r = client.post(
        "/v3/plan",
        content=req.model_dump_json(),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["plan"]["basket_slot"] == "B"


def test_v3_plan_vetoes_same_side_concurrency():
    req = _strong_buy()
    req.active_baskets = [
        ActiveBasket(slot="A", side="buy", opened_at_utc="2026-05-18T09:00:00Z")
    ]
    r = client.post(
        "/v3/plan",
        content=req.model_dump_json(),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "veto"
    assert body["error"]["code"] == "APP-CONC-409"


def test_v3_plan_vetoes_when_both_slots_full():
    req = _strong_buy()
    req.active_baskets = [
        ActiveBasket(slot="A", side="sell", opened_at_utc="2026-05-18T09:00:00Z"),
        ActiveBasket(slot="B", side="sell", opened_at_utc="2026-05-18T09:00:00Z"),
    ]
    r = client.post(
        "/v3/plan",
        content=req.model_dump_json(),
        headers={"Content-Type": "application/json"},
    )
    body = r.json()
    assert body["status"] == "veto"
    # Same-side check fires first if buy vs the existing buys; here all existing are sell so
    # the request is buy → conc check passes (no same-side), then capacity check fires.
    assert body["error"]["code"] == "APP-CAPACITY-409"


def test_v3_plan_vetoes_when_halted():
    req = _strong_buy()
    req.risk_state.trading_halted = True
    r = client.post(
        "/v3/plan",
        content=req.model_dump_json(),
        headers={"Content-Type": "application/json"},
    )
    body = r.json()
    assert body["status"] == "veto"
    assert body["error"]["code"] == "APP-SAFE-409"


def test_v3_plan_invalid_payload_400():
    r = client.post("/v3/plan", json={"foo": "bar"})
    assert r.status_code == 400


def test_v3_plan_no_scenario_when_truly_flat():
    # All EMAs at price + no DI + no candle run + no RSI bias → AGGRESSIVE_BIAS abstains.
    req = make_v3_request(
        adx_h1=0.10,
        bb_pos_m5=0.0,
        rsi_m5=0.0,
        bb_pos_m1=0.0,
        rsi_m1=0.0,
        di_balance=0.0,
        adx_m15=0.10,
        bb_width_pct=0.80,
        m1_atr_pct=0.40,
        last3_dir=0.0,
        last_close=2400.00,
        ema20_m5=2400.00,    # exact equality → no vote
        ema50_m5=2400.00,
        ema50_m15=2400.00,
    )
    r = client.post(
        "/v3/plan",
        content=req.model_dump_json(),
        headers={"Content-Type": "application/json"},
    )
    body = r.json()
    assert body["status"] == "ok"
    assert body["plan"]["scenario"] == "NONE"
    assert body["plan"]["layers"] == []


def test_v3_plan_aggressive_bias_fires_on_any_directional_clue():
    # Quiet market but price below all EMAs → AGGRESSIVE_BIAS should fire SELL.
    req = make_v3_request(
        adx_h1=0.10,
        bb_pos_m5=0.0,
        rsi_m5=0.0,
        bb_pos_m1=0.0,
        rsi_m1=0.0,
        di_balance=0.0,
        adx_m15=0.10,
        bb_width_pct=0.80,
        m1_atr_pct=0.40,
        last3_dir=0.0,
        last_close=2400.00,
        ema20_m5=2420.00,
        ema50_m5=2420.00,
        ema50_m15=2420.00,
    )
    r = client.post(
        "/v3/plan",
        content=req.model_dump_json(),
        headers={"Content-Type": "application/json"},
    )
    body = r.json()
    assert body["status"] == "ok"
    assert body["plan"]["scenario"] == "AGGRESSIVE_BIAS"
    assert body["plan"]["side_bias"] == "sell"
    assert len(body["plan"]["layers"]) == 5
