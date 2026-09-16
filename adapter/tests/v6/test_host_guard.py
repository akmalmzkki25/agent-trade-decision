"""Host-header allowlist (DNS-rebinding defence) and secret-free settings errors."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.host_guard import LOOPBACK_HOSTS, HostGuardMiddleware, host_name, normalise_host
from app.main import allowed_hosts
from app.settings import Settings
from app.settings import settings as adapter_settings
from app.v6.config import V6Settings

FAKE_TOKEN = "tok-SUPERSECRET-0123456789abcdefghijkl"
TOKEN_TAIL = "0123456789abcdefghijkl"


def _guarded(*extra: str) -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(HostGuardMiddleware, allowed_hosts=(*LOOPBACK_HOSTS, *extra))
    return app


@pytest.mark.parametrize(("header", "name"), [
    ("127.0.0.1:8765", "127.0.0.1"), ("localhost", "localhost"), ("[::1]:8765", "[::1]"),
    ("[::1]", "[::1]"), ("[::1", ""), (" LOCALHOST:1 ", "LOCALHOST"), ("", ""),
])
def test_host_name_keeps_ipv6_brackets(header: str, name: str) -> None:
    assert host_name(header) == name


@pytest.mark.parametrize(("raw", "normal"), [
    ("LOCALHOST", "localhost"), ("::1", "[::1]"), ("[0:0::1]", "[::1]"),
    ("127.000.0.1", "127.000.0.1"), ("10.1.2.3", "10.1.2.3"), ("Box.LAN", "box.lan"),
])
def test_normalise_host(raw: str, normal: str) -> None:
    assert normalise_host(raw) == normal


@pytest.mark.parametrize(("host", "status"), [
    ("127.0.0.1:8765", 200), ("localhost:8765", 200), ("[::1]:8765", 200), ("[::1]", 200),
    ("LOCALHOST", 200), ("testserver", 400), ("attacker.example", 400),
    ("127.0.0.1.attacker.example", 400), ("[::1", 400), ("[::2]:8765", 400),
])
def test_only_loopback_names_reach_the_app(host: str, status: int) -> None:
    # TestClient cannot take an IPv6 base URL, so the Host header is set directly.
    with TestClient(_guarded(), base_url="http://127.0.0.1") as client:
        response = client.get("/ping", headers={"host": host})
    assert response.status_code == status
    if status == 400:
        assert response.text == "Invalid host header"


def test_extra_names_are_opt_in_and_a_missing_host_is_refused() -> None:
    with TestClient(_guarded("testserver")) as client:
        assert client.get("/ping").status_code == 200
        assert client.get("/ping", headers={"host": ""}).status_code == 400


@pytest.mark.parametrize("hosts", [(), ("*",), ("*.example",), ("",)])
def test_the_guard_refuses_wildcard_or_empty_allowlists(hosts: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="explicit"):
        HostGuardMiddleware(_guarded(), hosts)


def test_lifespan_scopes_pass_through() -> None:
    with TestClient(_guarded(), base_url="http://127.0.0.1") as client:
        assert client.get("/ping").json() == {"ok": True}


# --- production allowlist ------------------------------------------------------------
def test_production_hosts_never_include_the_test_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_settings, "allowed_hosts", "")
    monkeypatch.setattr(adapter_settings, "adapter_host", "127.0.0.1")
    assert allowed_hosts() == ["127.0.0.1", "localhost", "[::1]"]
    monkeypatch.setattr(adapter_settings, "adapter_host", "::1")
    assert allowed_hosts() == ["127.0.0.1", "localhost", "[::1]"]
    monkeypatch.setattr(adapter_settings, "adapter_host", "10.1.2.3")
    assert allowed_hosts()[-1] == "10.1.2.3"
    monkeypatch.setattr(adapter_settings, "adapter_host", "0.0.0.0")
    monkeypatch.setattr(adapter_settings, "allowed_hosts", "Box.LAN, 192.168.1.10,box.lan")
    assert allowed_hosts() == ["127.0.0.1", "localhost", "[::1]", "box.lan", "192.168.1.10"]


def test_a_wildcard_bind_needs_an_explicit_host_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
    secure = {"hmac_required": True, "internal_hmac_key": "a-real-strong-secret"}
    for bind in ("0.0.0.0", "::"):
        with pytest.raises(ValidationError, match="ALLOWED_HOSTS"):
            Settings(_env_file=None, adapter_host=bind, **secure)
    assert Settings(_env_file=None, adapter_host="0.0.0.0", allowed_hosts="box.lan",
                    **secure).extra_allowed_hosts == ("box.lan",)
    with pytest.raises(ValidationError, match="wildcards"):
        Settings(_env_file=None, allowed_hosts="*", **secure)


# --- validation errors never print the raw settings ----------------------------------
def test_v6_settings_errors_hide_the_operator_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V6_ENABLED", "true")
    monkeypatch.setenv("V6_BACKEND", "openrouter")
    monkeypatch.setenv("V6_OPERATOR_TOKEN", FAKE_TOKEN)
    with pytest.raises(ValidationError) as caught:
        V6Settings(_env_file=None)
    assert TOKEN_TAIL not in str(caught.value) and "input_value" not in str(caught.value)

    monkeypatch.setenv("V6_ENABLED", "false")
    monkeypatch.setenv("V6_RISK_PCT", "5")
    with pytest.raises(ValidationError, match="only tighten") as tightened:
        V6Settings(_env_file=None)
    assert TOKEN_TAIL not in str(tightened.value)


def test_adapter_settings_errors_hide_the_hmac_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_HMAC_KEY", FAKE_TOKEN)
    monkeypatch.setenv("ADAPTER_HOST", "10.1.2.3")
    monkeypatch.setenv("HMAC_REQUIRED", "false")
    with pytest.raises(ValidationError, match="not loopback") as caught:
        Settings(_env_file=None)
    assert TOKEN_TAIL not in str(caught.value) and "input_value" not in str(caught.value)
