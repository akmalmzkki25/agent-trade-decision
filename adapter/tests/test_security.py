"""
Request-authentication and CSRF tests.

The adapter listens on localhost and is called by a MetaTrader 5 EA. Localhost
is not a security boundary on its own: any page the operator opens can POST to
127.0.0.1. These tests lock in the guards that keep drive-by requests out.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.settings import PLACEHOLDER_HMAC_KEY, Settings

from .fixtures_v5 import make_v5_request

client = TestClient(app)

WRITE_ENDPOINTS = [
    "/v1/decision",
    "/v2/plan",
    "/v3/plan",
    "/v4/plan",
    "/v5/burst",
    "/v1/events/trade-transaction",
    "/v1/events/basket-result",
]


@pytest.mark.parametrize("path", WRITE_ENDPOINTS)
def test_write_endpoints_reject_non_json_content_type(path):
    """
    A cross-origin form/text POST is a browser 'simple request' — no preflight.
    Demanding application/json is what makes those impossible.
    """
    r = client.post(path, content=b'{"hello": "world"}',
                    headers={"Content-Type": "text/plain"})
    assert r.status_code == 415, f"{path} accepted a text/plain body"


@pytest.mark.parametrize("path", WRITE_ENDPOINTS)
def test_write_endpoints_reject_cross_site_fetch(path):
    r = client.post(
        path,
        content=b"{}",
        headers={"Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"},
    )
    assert r.status_code == 403, f"{path} accepted a cross-site request"


@pytest.mark.parametrize("path", WRITE_ENDPOINTS)
def test_write_endpoints_reject_foreign_origin(path):
    r = client.post(
        path,
        content=b"{}",
        headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
    )
    assert r.status_code == 403, f"{path} accepted a foreign Origin"


# V6 routes answer 404 while V6 is disabled (as it is for the shared `app`), so
# their guards are checked on an isolated app with V6 enabled.
V6_WRITE_ENDPOINTS = [
    "/v6/bars/backfill",
    "/v6/snapshot",
    "/v6/intent/poll",
    "/v6/execution",
]


@pytest.fixture(scope="module")
def v6_client(tmp_path_factory):
    from app.main import create_app
    from app.v6.clock import FakeClock
    from app.v6.config import V6Settings

    tmp = tmp_path_factory.mktemp("v6-security")
    v6_app = create_app(
        v6_settings=V6Settings(_env_file=None, enabled=True, halt_file=str(tmp / "V6_HALT")),
        clock=FakeClock(),
        v6_db_path=str(tmp / "v6.db"),
    )
    with TestClient(v6_app) as v6:
        yield v6


@pytest.mark.parametrize("path", V6_WRITE_ENDPOINTS)
def test_v6_write_endpoints_are_hidden_while_v6_is_disabled(path):
    r = client.post(path, content=b"{}", headers={"Content-Type": "application/json"})
    assert r.status_code == 404, f"{path} is reachable with V6 disabled"


@pytest.mark.parametrize("path", V6_WRITE_ENDPOINTS)
def test_v6_write_endpoints_reject_non_json_content_type(v6_client, path):
    r = v6_client.post(path, content=b'{"hello": "world"}',
                       headers={"Content-Type": "text/plain"})
    assert r.status_code == 415, f"{path} accepted a text/plain body"


@pytest.mark.parametrize("path", V6_WRITE_ENDPOINTS)
def test_v6_write_endpoints_reject_cross_site_fetch(v6_client, path):
    r = v6_client.post(
        path,
        content=b"{}",
        headers={"Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"},
    )
    assert r.status_code == 403, f"{path} accepted a cross-site request"


@pytest.mark.parametrize("path", V6_WRITE_ENDPOINTS)
def test_v6_write_endpoints_reject_foreign_origin(v6_client, path):
    r = v6_client.post(
        path,
        content=b"{}",
        headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
    )
    assert r.status_code == 403, f"{path} accepted a foreign Origin"


@pytest.mark.parametrize("path", V6_WRITE_ENDPOINTS)
def test_v6_write_endpoints_reject_oversized_bodies(v6_client, path):
    from app.settings import settings

    r = v6_client.post(
        path,
        content=b"{}",
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(settings.max_request_bytes + 1),
        },
    )
    assert r.status_code == 413, f"{path} accepted an oversized body"


def test_same_origin_request_is_allowed_through_to_validation():
    """Same-origin requests pass the CSRF guard and reach schema validation."""
    r = client.post(
        "/v5/burst",
        content=b"{}",
        headers={"Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"},
    )
    assert r.status_code == 400   # rejected by the schema, not by the guard


def test_ea_style_request_without_browser_headers_succeeds():
    """MT5 WebRequest() sends no Origin/Sec-Fetch-Site; it must not be blocked."""
    r = client.post(
        "/v5/burst",
        content=make_v5_request().model_dump_json(),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200


def test_oversized_body_rejected_by_content_length():
    from app.settings import settings

    r = client.post(
        "/v5/burst",
        content=b"{}",
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(settings.max_request_bytes + 1),
        },
    )
    assert r.status_code == 413


def test_security_headers_present_on_dashboard():
    r = client.get("/dashboard")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]


def test_interactive_docs_disabled_by_default():
    """Docs map every trading endpoint; they stay off unless explicitly enabled."""
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


# --- Startup posture guards -------------------------------------------------


def test_non_loopback_bind_without_hmac_is_refused():
    with pytest.raises(ValidationError, match="not loopback"):
        Settings(adapter_host="0.0.0.0", hmac_required=False)


def test_non_loopback_bind_with_hmac_and_real_key_is_allowed():
    cfg = Settings(
        adapter_host="0.0.0.0", hmac_required=True, internal_hmac_key="a-real-strong-secret",
        allowed_hosts="192.168.1.10",
    )
    assert cfg.hmac_required is True
    assert cfg.extra_allowed_hosts == ("192.168.1.10",)


def test_hmac_enabled_with_placeholder_key_is_refused():
    with pytest.raises(ValidationError, match="placeholder"):
        Settings(hmac_required=True, internal_hmac_key=PLACEHOLDER_HMAC_KEY)


def test_hmac_enabled_with_empty_key_is_refused():
    with pytest.raises(ValidationError, match="placeholder"):
        Settings(hmac_required=True, internal_hmac_key="   ")


def test_loopback_bind_without_hmac_is_allowed_for_dev():
    cfg = Settings(adapter_host="127.0.0.1", hmac_required=False)
    assert cfg.hmac_required is False
