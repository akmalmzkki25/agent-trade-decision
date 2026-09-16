"""Performance metrics tests — the mandatory metric set from the scalper brief."""

import pytest
from fastapi.testclient import TestClient

from app import metrics
from app.main import app, ledger

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_basket_results():
    """Each test starts from an empty basket_results table."""
    ledger.conn.execute("DELETE FROM basket_results")
    yield
    ledger.conn.execute("DELETE FROM basket_results")


def _post_basket(
    *,
    basket_id: str,
    net_pnl: float,
    gross_profit: float,
    gross_loss: float,
    version: str = "v5",
    max_floating_dd: float = 0.0,
    latency_ms: int = 10,
    slippage: float = 1.0,
    spread: float = 8.0,
) -> None:
    payload = {
        "schema_version": "basket-result-event.v1",
        "basket_id": basket_id,
        "version": version,
        "symbol": "XAUUSD",
        "side": "buy",
        "opened_at_utc": "2026-09-16T09:00:00Z",
        "closed_at_utc": "2026-09-16T09:01:00Z",
        "close_reason": "TP",
        "bursts": 1,
        "positions": 3,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "net_pnl": net_pnl,
        "max_floating_dd": max_floating_dd,
        "avg_slippage_points": slippage,
        "avg_spread_points": spread,
        "decision_latency_ms": latency_ms,
        "equity_at_open": 10000.0,
        "equity_at_close": 10000.0 + net_pnl,
    }
    r = client.post("/v1/events/basket-result", json=payload)
    assert r.status_code == 200, r.text


def test_basket_result_endpoint_accepts_event():
    _post_basket(basket_id="bt-accept-1", net_pnl=5.0, gross_profit=6.0, gross_loss=-1.0)


def test_basket_result_rejects_invalid_payload():
    r = client.post("/v1/events/basket-result", json={"foo": "bar"})
    assert r.status_code == 400


def test_profit_factor_computed_from_gross_values():
    version = "v5"
    _post_basket(basket_id="pf-1", net_pnl=6.0, gross_profit=8.0, gross_loss=-2.0, version=version)
    _post_basket(basket_id="pf-2", net_pnl=4.0, gross_profit=6.0, gross_loss=-2.0, version=version)
    m = metrics.get_performance_metrics(version=version)
    # gross_profit 14 / |gross_loss| 4 = 3.5
    assert m["profit_factor"] == 3.5
    assert m["meets_profit_factor_target"] is True


def test_win_rate_and_expected_value():
    version = "v5"
    # 3 wins of +10, 1 loss of -20 → win_rate 75%, EV = 0.75*10 - 0.25*20 = 2.5
    for i in range(3):
        _post_basket(
            basket_id=f"ev-w{i}", net_pnl=10.0, gross_profit=10.0, gross_loss=0.0, version=version
        )
    _post_basket(
        basket_id="ev-l1", net_pnl=-20.0, gross_profit=0.0, gross_loss=-20.0, version=version
    )
    m = metrics.get_performance_metrics(version=version)
    assert m["wins"] == 3
    assert m["losses"] == 1
    assert m["win_rate_pct"] == 75.0
    assert m["avg_win"] == 10.0
    assert m["avg_loss"] == -20.0
    assert abs(m["expected_value"] - 2.5) < 1e-6


def test_negative_expected_value_is_surfaced():
    """A high win rate with terrible R:R must still show negative EV."""
    version = "v5"
    # 9 wins of +1, 1 loss of -20 → EV = 0.9*1 - 0.1*20 = -1.1
    for i in range(9):
        _post_basket(
            basket_id=f"nev-w{i}", net_pnl=1.0, gross_profit=1.0, gross_loss=0.0, version=version
        )
    _post_basket(
        basket_id="nev-l1", net_pnl=-20.0, gross_profit=0.0, gross_loss=-20.0, version=version
    )
    m = metrics.get_performance_metrics(version=version)
    assert m["win_rate_pct"] == 90.0
    assert m["expected_value"] < 0


def test_max_floating_drawdown_tracks_worst_value():
    version = "v5"
    _post_basket(
        basket_id="dd-1", net_pnl=2.0, gross_profit=2.0, gross_loss=0.0,
        version=version, max_floating_dd=-12.0,
    )
    _post_basket(
        basket_id="dd-2", net_pnl=3.0, gross_profit=3.0, gross_loss=0.0,
        version=version, max_floating_dd=-31.5,
    )
    m = metrics.get_performance_metrics(version=version)
    assert m["max_floating_drawdown"] == -31.5


def test_latency_percentile_and_target_flag():
    version = "v5"
    for i, latency in enumerate([5, 8, 10, 12, 15, 18, 22, 25, 30, 120]):
        _post_basket(
            basket_id=f"lat-{i}", net_pnl=1.0, gross_profit=1.0, gross_loss=0.0,
            version=version, latency_ms=latency,
        )
    m = metrics.get_performance_metrics(version=version)
    assert m["p95_latency_ms"] > 0
    assert m["latency_target_ms"] == 20
    assert m["avg_latency_ms"] > 0


def test_statistical_significance_flag():
    version = "v5"
    _post_basket(basket_id="sig-1", net_pnl=1.0, gross_profit=1.0, gross_loss=0.0, version=version)
    m = metrics.get_performance_metrics(version=version)
    assert m["sample_size"] == 1
    assert m["is_significant"] is False
    assert m["min_significant_sample"] == 1000


def test_slippage_and_spread_averaged():
    version = "v5"
    _post_basket(
        basket_id="sl-1", net_pnl=1.0, gross_profit=1.0, gross_loss=0.0,
        version=version, slippage=2.0, spread=10.0,
    )
    _post_basket(
        basket_id="sl-2", net_pnl=1.0, gross_profit=1.0, gross_loss=0.0,
        version=version, slippage=4.0, spread=14.0,
    )
    m = metrics.get_performance_metrics(version=version)
    assert m["avg_slippage_points"] == 3.0
    assert m["avg_spread_points"] == 12.0


def test_equity_curve_is_cumulative_and_oldest_first():
    version = "v5"
    _post_basket(basket_id="eq-1", net_pnl=5.0, gross_profit=5.0, gross_loss=0.0, version=version)
    _post_basket(basket_id="eq-2", net_pnl=-2.0, gross_profit=0.0, gross_loss=-2.0, version=version)
    _post_basket(basket_id="eq-3", net_pnl=4.0, gross_profit=4.0, gross_loss=0.0, version=version)
    curve = metrics.get_equity_curve(version=version)
    assert len(curve) == 3
    cumulative = [point["cumulative_pnl"] for point in curve]
    assert cumulative == [5.0, 3.0, 7.0]


def test_metrics_endpoint_returns_full_metric_set():
    r = client.get("/api/dashboard/metrics")
    assert r.status_code == 200
    body = r.json()
    for key in (
        "profit_factor", "win_rate_pct", "expected_value", "avg_win", "avg_loss",
        "max_floating_drawdown", "avg_latency_ms", "p95_latency_ms",
        "avg_slippage_points", "avg_spread_points", "sample_size", "is_significant",
    ):
        assert key in body


def test_metrics_endpoint_version_filter():
    version = "v5"
    _post_basket(basket_id="f-1", net_pnl=7.0, gross_profit=7.0, gross_loss=0.0, version=version)
    r = client.get(f"/api/dashboard/metrics?version={version}")
    body = r.json()
    assert body["version"] == version
    assert body["sample_size"] == 1
    assert body["net_pnl"] == 7.0


def test_equity_curve_endpoint():
    r = client.get("/api/dashboard/equity-curve?limit=10")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_basket_results_endpoint():
    r = client.get("/api/dashboard/basket-results?limit=5")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_metrics_empty_when_no_data_for_version():
    m = metrics.get_performance_metrics(version="does-not-exist")
    assert m["sample_size"] == 0
    assert m["profit_factor"] == 0.0
    assert m["is_significant"] is False


# --- Profit factor edge cases (review CRITICAL) ----------------------------


def test_profit_factor_undefined_when_no_losses():
    """
    A run with profit and zero losses has an infinite profit factor — the best
    possible outcome. It must never be reported as 0.0, which is also the
    "no data" value.
    """
    _post_basket(basket_id="pfu-1", net_pnl=5.0, gross_profit=5.0, gross_loss=0.0)
    _post_basket(basket_id="pfu-2", net_pnl=3.0, gross_profit=3.0, gross_loss=0.0)
    m = metrics.get_performance_metrics(version="v5")
    assert m["profit_factor_is_undefined"] is True
    assert m["meets_profit_factor_target"] is True
    assert m["wins"] == 2 and m["losses"] == 0


def test_profit_factor_not_undefined_when_no_data():
    """Empty ledger must be distinguishable from a flawless run."""
    m = metrics.get_performance_metrics(version="v5")
    assert m["sample_size"] == 0
    assert m["profit_factor"] == 0.0
    assert m["profit_factor_is_undefined"] is False
    assert m["meets_profit_factor_target"] is False


def test_breakeven_baskets_excluded_from_win_rate():
    """Breakeven closes are neither wins nor losses; they must not skew the rate."""
    _post_basket(basket_id="be-w", net_pnl=10.0, gross_profit=10.0, gross_loss=0.0)
    _post_basket(basket_id="be-l", net_pnl=-10.0, gross_profit=0.0, gross_loss=-10.0)
    _post_basket(basket_id="be-0", net_pnl=0.0, gross_profit=0.0, gross_loss=0.0)
    m = metrics.get_performance_metrics(version="v5")
    assert m["sample_size"] == 3
    assert m["breakeven"] == 1
    assert m["win_rate_pct"] == 50.0      # 1 win / (1 win + 1 loss)


def test_p95_latency_uses_ceil_rank():
    """Nearest-rank percentile: p95 of 20 samples is the 19th value."""
    for i in range(20):
        _post_basket(
            basket_id=f"pct-{i}", net_pnl=1.0, gross_profit=1.0, gross_loss=0.0,
            latency_ms=i + 1,
        )
    m = metrics.get_performance_metrics(version="v5")
    assert m["p95_latency_ms"] == 19.0
