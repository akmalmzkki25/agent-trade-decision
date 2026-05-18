from fastapi.testclient import TestClient

from app.main import app

from .fixtures_v2 import make_plan_request

client = TestClient(app)


def test_plan_ok_with_layers():
    payload = make_plan_request().model_dump_json()
    r = client.post("/v2/plan", content=payload, headers={"Content-Type": "application/json"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["plan"]["scenario"] in ("RANGE_REVERT", "TREND_BREAKOUT", "CONTINUATION_PULLBACK")
    assert len(body["plan"]["layers"]) >= 3
    assert body["plan"]["basket_tp_pct_equity"] > 0


def test_plan_ok_no_scenario_returns_empty_layers():
    payload = make_plan_request(
        adx_h1=0.10,
        bb_pos_m5=0.0,
        rsi_m5=0.0,
        bb_pos_m1=0.0,
        rsi_m1=0.0,
        di_balance=0.0,
        adx_m15=0.10,
        bb_width_pct=0.80,
    ).model_dump_json()
    r = client.post("/v2/plan", content=payload, headers={"Content-Type": "application/json"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["plan"]["scenario"] == "NONE"
    assert body["plan"]["layers"] == []


def test_plan_veto_when_halted():
    req = make_plan_request(halted=True)
    r = client.post("/v2/plan", content=req.model_dump_json(), headers={"Content-Type": "application/json"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "veto"
    assert body["error"]["code"] == "APP-SAFE-409"


def test_plan_invalid_payload_400():
    r = client.post("/v2/plan", json={"foo": "bar"})
    assert r.status_code == 400
