from fastapi.testclient import TestClient

from app.main import app

from .fixtures import make_request

client = TestClient(app)


def test_healthz():
    r = client.get("/v1/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["decider"] == "dummy_trend_breakout"


def test_decision_open():
    req = make_request().model_dump_json()
    r = client.post("/v1/decision", content=req, headers={"Content-Type": "application/json"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["decision"]["action"] == "open"
    assert body["decision"]["side"] == "buy"


def test_decision_halted_returns_degraded_hold():
    req = make_request(halted=True).model_dump_json()
    r = client.post("/v1/decision", content=req, headers={"Content-Type": "application/json"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["decision"]["action"] == "hold"
    assert body["error"]["code"] == "APP-SAFE-409"


def test_decision_invalid_payload_400():
    r = client.post("/v1/decision", json={"foo": "bar"})
    assert r.status_code == 400


def test_trade_transaction_event():
    payload = {
        "schema_version": "trade-transaction-event.v1",
        "request_id": "TEST-XAUUSD-M15-2026-05-18T09:00:00Z",
        "symbol": "XAUUSD",
        "trans_type": "TRADE_TRANSACTION_DEAL_ADD",
        "order": 123,
        "deal": 456,
        "position": 789,
        "retcode": 10009,
        "comment": "",
        "time_utc": "2026-05-18T09:01:00+00:00",
    }
    r = client.post("/v1/events/trade-transaction", json=payload)
    assert r.status_code == 200
    assert r.json() == {"ok": True}
