from fastapi.testclient import TestClient

from app.main import app

from .fixtures import make_request
from .fixtures_v2 import make_plan_request

client = TestClient(app)


def test_root_redirects_to_dashboard():
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/dashboard"


def test_dashboard_html_renders():
    # Seed at least one decision so the dashboard has content.
    client.post(
        "/v1/decision",
        content=make_request().model_dump_json(),
        headers={"Content-Type": "application/json"},
    )
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "Trading Analytics" in body
    assert "Recent decisions" in body
    # Dark theme + Tailwind shipped via CDN.
    assert "bg-slate-950" in body
    # Navigation should highlight dashboard as active.
    assert "Dashboard" in body and "Chatbot" in body


def test_chatbot_html_renders():
    r = client.get("/chatbot")
    assert r.status_code == 200
    body = r.text
    assert "Coming Soon" in body
    assert "Conversational Trading Assistant" in body
    # Input must be disabled.
    assert "disabled" in body.lower()


def test_api_stats_json():
    r = client.get("/api/dashboard/stats")
    assert r.status_code == 200
    body = r.json()
    for key in (
        "decisions_total", "decisions_open", "decisions_hold",
        "hold_rate_pct", "avg_latency_ms",
        "plans_total", "plans_with_layers", "ledger_available",
    ):
        assert key in body


def test_api_decisions_returns_list():
    client.post(
        "/v1/decision",
        content=make_request().model_dump_json(),
        headers={"Content-Type": "application/json"},
    )
    r = client.get("/api/dashboard/decisions?limit=5")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_api_plans_returns_list():
    client.post(
        "/v2/plan",
        content=make_plan_request().model_dump_json(),
        headers={"Content-Type": "application/json"},
    )
    r = client.get("/api/dashboard/plans?limit=5")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_api_action_distribution_returns_list():
    client.post(
        "/v1/decision",
        content=make_request().model_dump_json(),
        headers={"Content-Type": "application/json"},
    )
    r = client.get("/api/dashboard/action-distribution")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    if body:
        assert "action" in body[0] and "count" in body[0]


def test_api_limit_is_clamped():
    r = client.get("/api/dashboard/decisions?limit=99999")
    assert r.status_code == 200  # clamped to 200, not 400


def test_api_trade_events_endpoint():
    r = client.get("/api/dashboard/trade-events")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_dashboard_renders_performance_section():
    """The scalper brief's metric set must be visible on the dashboard."""
    r = client.get("/dashboard")
    assert r.status_code == 200
    body = r.text
    assert "Strategy performance" in body
    assert "Profit factor" in body
    assert "Expected value" in body
    assert "Max floating DD" in body
    assert "p95 latency" in body
    assert "Avg slippage" in body


def test_dashboard_warns_when_sample_too_small():
    """Brief: don't trust a scalper's edge below 1000 trades."""
    r = client.get("/dashboard")
    body = r.text
    # With an empty/small ledger the page must not claim significance.
    assert ("too early to trust" in body) or ("No closed baskets yet" in body)
