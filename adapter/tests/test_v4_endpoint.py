from fastapi.testclient import TestClient

from app.main import app

from .fixtures_v4 import make_v4_request

client = TestClient(app)


def test_v4_plan_ok_with_zone():
    req = make_v4_request(
        last_close=4170.00,
        swing_low_h1=4155.00,
        close_m15=4170.00,
        m5_direction=-1.0,
    )
    r = client.post(
        "/v4/plan",
        content=req.model_dump_json(),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["plan"]["scenario"] == "LIQUIDITY_ZONE_ENTRY"
    assert body["plan"]["side_bias"] == "buy"
    assert len(body["plan"]["layers"]) == 2
    assert body["plan"]["zone"]["width_pips"] == 30.0


def test_v4_plan_vetoed_when_active_basket():
    req = make_v4_request(
        last_close=4170.00,
        swing_low_h1=4155.00,
        has_active_basket=True,
    )
    r = client.post(
        "/v4/plan",
        content=req.model_dump_json(),
        headers={"Content-Type": "application/json"},
    )
    body = r.json()
    assert body["status"] == "veto"
    assert body["error"]["code"] == "APP-CONC-409"


def test_v4_plan_vetoed_when_halted():
    req = make_v4_request(halted=True)
    r = client.post(
        "/v4/plan",
        content=req.model_dump_json(),
        headers={"Content-Type": "application/json"},
    )
    body = r.json()
    assert body["status"] == "veto"
    assert body["error"]["code"] == "APP-SAFE-409"


def test_v4_plan_ok_empty_when_no_zone():
    # Price too far from BOTH swings → no zone.
    req = make_v4_request(
        last_close=4180.00,
        swing_low_h1=4100.00,
        swing_high_h1=4260.00,
    )
    r = client.post(
        "/v4/plan",
        content=req.model_dump_json(),
        headers={"Content-Type": "application/json"},
    )
    body = r.json()
    assert body["status"] == "ok"
    assert body["plan"]["scenario"] == "NONE"
    assert body["plan"]["layers"] == []


def test_v4_plan_invalid_payload_400():
    r = client.post("/v4/plan", json={"foo": "bar"})
    assert r.status_code == 400


def test_v4_plan_partial_tp_metadata_in_response():
    req = make_v4_request(
        last_close=4170.00,
        swing_low_h1=4155.00,
        close_m15=4170.00,
        m5_direction=-1.0,
    )
    r = client.post(
        "/v4/plan",
        content=req.model_dump_json(),
        headers={"Content-Type": "application/json"},
    )
    body = r.json()
    layer = body["plan"]["layers"][0]
    assert layer["partial_tp_pips"] == 30.0
    assert layer["partial_close_fraction"] == 0.50
    assert layer["runner_cap_pips"] == 100.0
    assert layer["move_sl_to_entry_after_partial"] is True
