from fastapi.testclient import TestClient

from app.main import app

from .fixtures_v5 import make_v5_request

client = TestClient(app)


def test_v5_burst_ok():
    req = make_v5_request()
    r = client.post(
        "/v5/burst",
        content=req.model_dump_json(),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["burst"]["scenario"] == "SCALP_MICRO"
    assert len(body["burst"]["layers"]) == 3
    assert body["burst"]["layers"][0]["order_type"] in ("buy_market", "sell_market")


def test_v5_vetoed_when_halted():
    req = make_v5_request(halted=True)
    r = client.post(
        "/v5/burst",
        content=req.model_dump_json(),
        headers={"Content-Type": "application/json"},
    )
    body = r.json()
    assert body["status"] == "veto"
    assert body["error"]["code"] == "APP-SAFE-409"


def test_v5_vetoed_when_margin_low():
    req = make_v5_request(margin_level_pct=200.0)
    r = client.post("/v5/burst", content=req.model_dump_json(), headers={"Content-Type": "application/json"})
    body = r.json()
    assert body["status"] == "veto"
    assert body["error"]["code"] == "APP-MARGIN-409"


def test_v5_vetoed_when_rate_limit():
    req = make_v5_request(last_burst_ms_ago=100)
    r = client.post("/v5/burst", content=req.model_dump_json(), headers={"Content-Type": "application/json"})
    body = r.json()
    assert body["status"] == "veto"
    assert body["error"]["code"] == "APP-RATE-429"


def test_v5_vetoed_when_basket_full():
    req = make_v5_request(active_basket_bursts=3)
    r = client.post("/v5/burst", content=req.model_dump_json(), headers={"Content-Type": "application/json"})
    body = r.json()
    assert body["status"] == "veto"
    assert body["error"]["code"] == "APP-CAPACITY-409"


def test_v5_vetoed_when_burst_opposes_basket():
    """A sell burst must not be added to a basket that is already long."""
    # tick momentum down => the scenario wants to sell.
    req = make_v5_request(
        tick_momentum_signed=-1.0, bb_pos=-0.20, rsi_centered=-0.10,
        active_basket_bursts=1, active_basket_side="buy",
    )
    r = client.post("/v5/burst", content=req.model_dump_json(), headers={"Content-Type": "application/json"})
    body = r.json()
    assert body["status"] == "veto"
    assert body["error"]["code"] == "APP-SIDE-409"
    assert body["burst"]["layers"] == []


def test_v5_vetoed_when_basket_side_unknown():
    """An EA restarted mid-basket reports bursts with an empty side."""
    req = make_v5_request(active_basket_bursts=2, active_basket_side="")
    r = client.post("/v5/burst", content=req.model_dump_json(), headers={"Content-Type": "application/json"})
    body = r.json()
    assert body["status"] == "veto"
    assert body["error"]["code"] == "APP-SIDE-409"


def test_v5_allows_burst_matching_basket_side():
    req = make_v5_request(active_basket_bursts=1, active_basket_side="buy")
    r = client.post("/v5/burst", content=req.model_dump_json(), headers={"Content-Type": "application/json"})
    body = r.json()
    assert body["status"] == "ok"
    assert body["burst"]["side_bias"] == "buy"
    assert len(body["burst"]["layers"]) == 3


def test_v5_ok_empty_when_no_scalp():
    req = make_v5_request(tick_momentum_signed=0.0)
    r = client.post("/v5/burst", content=req.model_dump_json(), headers={"Content-Type": "application/json"})
    body = r.json()
    assert body["status"] == "ok"
    assert body["burst"]["scenario"] == "NONE"


def test_v5_invalid_400():
    r = client.post("/v5/burst", json={"foo": "bar"})
    assert r.status_code == 400


def test_v5_response_includes_exit_rules():
    req = make_v5_request()
    r = client.post("/v5/burst", content=req.model_dump_json(), headers={"Content-Type": "application/json"})
    body = r.json()
    rules = body["burst"]["exit_rules"]
    assert rules["basket_tp_usd"] == 5.0
    assert rules["basket_sl_usd"] == 30.0
    assert rules["max_bursts_per_basket"] == 3
