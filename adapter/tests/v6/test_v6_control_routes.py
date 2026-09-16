"""/v6/control/* routes on an isolated app (plan sections 4b, 6 and 9).

The app comes from `build_control_app` in test_v6_dashboard.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import security
from app.routes import v6_control
from app.routes.v6_control import CONTROL_STATE_KEY, install_control_plane
from app.v6.clock import FakeClock
from app.v6.runtime.sessions import REFUSE_NOT_DEMO, poll_command

from .payloads_v6 import RECEIVED_AT
from .test_v6_dashboard import (
    AUTH, JSON, LOOPBACK, TOKEN, ControlApp, build_control_app, control_settings, post,
    record_demo_poll,
)

REMOTE = ("192.0.2.10", 50123)
TOKEN_ROUTES = (("POST", "/v6/control/session"), ("GET", "/v6/control/session"),
                ("POST", "/v6/control/resume"))
ALL_ROUTES = TOKEN_ROUTES + (("POST", "/v6/control/halt"),)


def _actions(db_path: Path) -> list[tuple[str, str]]:
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT actor, action FROM v6_control_log"
                            " WHERE actor != 'runtime' ORDER BY id").fetchall()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(epoch=RECEIVED_AT)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "control.db"


@pytest.fixture
def wired(tmp_path: Path, db_path: Path, clock: FakeClock) -> ControlApp:
    return build_control_app(control_settings(tmp_path), db_path, clock)


@pytest.fixture
def client(wired: ControlApp) -> Iterator[TestClient]:
    with TestClient(wired.app, client=LOOPBACK) as test_client:
        yield test_client


def _request(client: TestClient, method: str, path: str, headers: dict[str, str]):
    if method == "GET":
        return client.get(path, headers=headers)
    return post(client, path, {}, headers)


# --- availability ------------------------------------------------------------------
@pytest.mark.parametrize("overrides", [{"enabled": False}, {"mode": "off"}])
@pytest.mark.parametrize(("method", "path"), ALL_ROUTES)
def test_control_routes_are_404_while_v6_is_off(tmp_path: Path, db_path: Path, clock: FakeClock,
                                                overrides: dict[str, Any], method: str,
                                                path: str) -> None:
    wired = build_control_app(control_settings(tmp_path, **overrides), db_path, clock)
    assert wired.plane is None
    with TestClient(wired.app, client=LOOPBACK) as off:
        response = _request(off, method, path, AUTH)
    assert response.status_code == 404
    assert response.json() == {"detail": "V6 disabled"}
    assert not db_path.exists()


def test_control_routes_are_404_without_a_control_plane(tmp_path: Path, db_path: Path,
                                                        clock: FakeClock) -> None:
    wired = build_control_app(control_settings(tmp_path), db_path, clock)
    install_control_plane(wired.app, None)
    with TestClient(wired.app, client=LOOPBACK) as test_client:
        assert test_client.get("/v6/control/session", headers=AUTH).status_code == 404
    assert getattr(wired.app.state, CONTROL_STATE_KEY) is None


@pytest.mark.parametrize("token", ["", "short-token", "change-me"])
@pytest.mark.parametrize(("method", "path"), TOKEN_ROUTES)
def test_token_routes_are_503_until_a_token_is_configured(
        tmp_path: Path, db_path: Path, clock: FakeClock, token: str, method: str,
        path: str) -> None:
    wired = build_control_app(control_settings(tmp_path, operator_token=token), db_path, clock)
    with TestClient(wired.app, client=LOOPBACK) as test_client:
        response = _request(test_client, method, path, AUTH)
        halt = post(test_client, "/v6/control/halt", {}, AUTH)
        assert wired.plane is not None  # the dashboard HALT needs no token at all
        dashboard = post(test_client, "/v6/control/halt", {"reason": "dashboard"},
                         {**JSON, "X-V6-CSRF": wired.plane.csrf_nonce})
    assert response.status_code == 503
    assert response.json()["detail"] == "V6_OPERATOR_TOKEN is not configured"
    assert halt.status_code == 503
    assert dashboard.status_code == 200 and dashboard.json()["actor"] == "dashboard"


# --- request guards ------------------------------------------------------------------
@pytest.mark.parametrize(("method", "path"), ALL_ROUTES)
def test_remote_clients_are_refused(wired: ControlApp, method: str, path: str) -> None:
    assert wired.plane is not None
    headers = {**AUTH, "X-V6-CSRF": wired.plane.csrf_nonce}
    with TestClient(wired.app, client=REMOTE) as remote:
        response = _request(remote, method, path, headers)
    assert response.status_code == 403
    assert response.json()["detail"] == "loopback clients only"


@pytest.mark.parametrize("authorization", [
    None, "Bearer wrong-token-" + "x" * 30, f"Basic {TOKEN}", "Bearer ", f"bearer{TOKEN}",
    f"Bearer {TOKEN}x",
])
@pytest.mark.parametrize(("method", "path"), TOKEN_ROUTES)
def test_a_missing_or_wrong_token_is_401(client: TestClient, authorization: str | None,
                                         method: str, path: str) -> None:
    headers = dict(JSON) if authorization is None else {**JSON, "Authorization": authorization}
    response = _request(client, method, path, headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_status_route_scheme_and_cross_site_rules(client: TestClient) -> None:
    mixed_case = {**JSON, "Authorization": f"bEaReR {TOKEN}"}
    assert client.get("/v6/control/session", headers=mixed_case).status_code == 200
    cross_site = {**AUTH, "Sec-Fetch-Site": "cross-site"}
    assert client.get("/v6/control/session", headers=cross_site).status_code == 403


@pytest.mark.parametrize("path", ["/v6/control/session", "/v6/control/halt",
                                  "/v6/control/resume"])
def test_write_guards_run_before_the_body_is_read(client: TestClient, path: str) -> None:
    assert post(client, path, b"{}", {**AUTH, "Content-Type": "text/plain"}).status_code == 415
    assert post(client, path, b"{}", {**AUTH, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert post(client, path, b"{}", {**AUTH, "Origin": "https://evil.example"}).status_code == 403
    oversize = str(security.OPERATOR_MAX_BODY_BYTES + 1)
    assert post(client, path, b"{}", {**AUTH, "Content-Length": oversize}).status_code == 413


def test_a_streamed_oversize_body_is_413(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        yield b'{"action": "stop", "reason": "'
        yield b"x" * security.OPERATOR_MAX_BODY_BYTES
        yield b'"}'

    response = client.post("/v6/control/session", content=chunks(), headers=AUTH)
    assert response.status_code == 413


@pytest.mark.parametrize("body", [
    b"not json", b"[]", {"action": "pause"}, {"action": "start", "extra": 1},
    {"action": "stop", "reason": "has spaces"}, {"action": "stop", "reason": "x" * 65},
    {}, {"action": 1},
])
def test_malformed_session_commands_are_400(client: TestClient, body: Any) -> None:
    response = post(client, "/v6/control/session", body)
    assert response.status_code == 400
    assert TOKEN not in response.text


@pytest.mark.parametrize(("peer", "allowed"), [
    (("127.0.0.1", 1), True), (("127.8.0.1", 1), True), (("::1", 1), True),
    (("LOCALHOST", 1), True), (None, False), (("testclient", 1), False),
    (("10.0.0.2", 1), False), (("::ffff:10.0.0.2", 1), False),
])
def test_only_loopback_peers_pass(peer: tuple[str, int] | None, allowed: bool) -> None:
    request = Request({"type": "http", "headers": [], "client": peer})
    try:
        security.require_loopback_client(request)
        refused = None
    except HTTPException as exc:
        refused = exc.status_code
    assert refused == (None if allowed else 403)


# --- sessions ------------------------------------------------------------------------
def test_session_start_is_refused_until_the_ea_reports_demo(client: TestClient,
                                                            wired: ControlApp) -> None:
    assert wired.container is not None
    refused = post(client, "/v6/control/session", {"action": "start"})
    assert refused.status_code == 409
    assert refused.json()["refusal"] == REFUSE_NOT_DEMO

    record_demo_poll(wired.container, "CONTEST")
    assert post(client, "/v6/control/session", {"action": "start"}).status_code == 409


def test_session_lifecycle_start_status_stop(client: TestClient, wired: ControlApp,
                                             db_path: Path) -> None:
    assert wired.container is not None and wired.plane is not None
    record_demo_poll(wired.container)

    started = post(client, "/v6/control/session", {"action": "start"})
    again = post(client, "/v6/control/session", {"action": "start"})
    status = client.get("/v6/control/session", headers=AUTH)
    stopped = post(client, "/v6/control/session", {"action": "stop", "reason": "cukup"})

    assert started.status_code == 200 and started.json()["created"] is True
    session = started.json()["session"]
    assert (session["backend"], session["mode"], session["armed"]) == ("rules", "shadow", False)
    assert again.status_code == 200 and again.json()["created"] is False
    body = status.json()
    assert status.status_code == 200 and body["session"]["session_id"] == session["session_id"]
    assert (body["mode"], body["backend"], body["halted"]) == ("shadow", "rules", False)
    assert body["summary"]["exposure"]["open_v6_positions"] == 0
    result = stopped.json()
    assert stopped.status_code == 200 and result["stopped"] is True
    assert result["session"]["stop_reason"] == "cukup"
    assert result["command"]["command"] == "CANCEL_PENDING"
    assert result["summary"]["sessions"][0]["active"] is False
    now = wired.container.clock.now_epoch()
    assert poll_command(wired.plane.commands, halted=False, pending_v6_orders=0,
                        now=now) == "CANCEL_PENDING"
    assert _actions(db_path) == [("operator", "session_start"), ("operator", "session_stop")]
    assert client.get("/v6/control/session", headers=AUTH).json()["session"] is None


def test_session_storage_failures_are_503(client: TestClient, wired: ControlApp,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    assert wired.plane is not None

    def broken() -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(wired.plane.ledger, "active_session", broken)
    response = post(client, "/v6/control/session", {"action": "stop"})
    assert response.status_code == 503
    assert response.json()["detail"] == "V6 storage unavailable"


# --- halt and resume -------------------------------------------------------------------
def test_operator_halt_writes_the_sentinel_once(client: TestClient, wired: ControlApp,
                                                db_path: Path) -> None:
    assert wired.container is not None
    halt_path = wired.container.halt_path
    first = post(client, "/v6/control/halt", {"reason": "news_spike"})
    second = post(client, "/v6/control/halt", b"")

    assert first.status_code == 200
    assert first.json() == {"halted": True, "created": True, "actor": "operator",
                            "halt_file": "V6_HALT"}
    assert second.json()["created"] is False
    marker = json.loads(halt_path.read_text(encoding="utf-8"))
    assert (marker["actor"], marker["reason"]) == ("operator", "news_spike")
    assert _actions(db_path) == [("operator", "halt"), ("operator", "halt")]
    assert client.get("/v6/control/session", headers=AUTH).json()["halted"] is True


def test_dashboard_halt_needs_the_csrf_nonce(client: TestClient, wired: ControlApp,
                                             db_path: Path) -> None:
    assert wired.plane is not None
    nonce = wired.plane.csrf_nonce
    missing = post(client, "/v6/control/halt", {}, dict(JSON))
    wrong = post(client, "/v6/control/halt", {}, {**JSON, "X-V6-CSRF": nonce + "x"})
    cross = post(client, "/v6/control/halt", {},
                 {**JSON, "X-V6-CSRF": nonce, "Sec-Fetch-Site": "cross-site"})
    plain = post(client, "/v6/control/halt", {}, {"Content-Type": "text/plain",
                                                  "X-V6-CSRF": nonce})
    ok = post(client, "/v6/control/halt", {"reason": "dashboard"},
              {**JSON, "X-V6-CSRF": nonce, "Sec-Fetch-Site": "same-origin",
               "Origin": "http://127.0.0.1:8765"})

    assert (missing.status_code, wrong.status_code) == (403, 403)
    assert missing.json()["detail"] == "invalid CSRF token"
    assert (cross.status_code, plain.status_code) == (403, 415)
    assert ok.status_code == 200 and ok.json()["actor"] == "dashboard"
    assert _actions(db_path) == [("dashboard", "halt")]


def test_the_csrf_nonce_never_stands_in_for_the_token(client: TestClient,
                                                     wired: ControlApp) -> None:
    assert wired.plane is not None
    nonce = {**JSON, "X-V6-CSRF": wired.plane.csrf_nonce}
    assert post(client, "/v6/control/halt", {}, {**nonce, "Authorization": "Bearer no"}
                ).status_code == 401
    assert post(client, "/v6/control/resume", {}, nonce).status_code == 401
    assert post(client, "/v6/control/session", {"action": "stop"}, nonce).status_code == 401


def test_halt_fails_loudly_when_the_sentinel_cannot_be_written(
        tmp_path: Path, db_path: Path, clock: FakeClock,
        caplog: pytest.LogCaptureFixture) -> None:
    settings = control_settings(tmp_path, halt_file=str(tmp_path / "missing" / "V6_HALT"))
    wired = build_control_app(settings, db_path, clock)
    with TestClient(wired.app, client=LOOPBACK) as test_client, \
            caplog.at_level(logging.ERROR, logger=v6_control.__name__):
        response = post(test_client, "/v6/control/halt", {})
    assert response.status_code == 503
    assert response.json()["detail"] == "halt file could not be written"
    assert "HALT FAILED" in caplog.text


def test_halt_survives_a_broken_audit_log(client: TestClient, wired: ControlApp,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    assert wired.container is not None

    def broken(*_: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(wired.container.ledger_v6, "log_control", broken)
    response = post(client, "/v6/control/halt", {})
    assert response.status_code == 200
    assert wired.container.halt_path.exists()


def test_resume_is_refused_while_a_breaker_is_tripped(client: TestClient, wired: ControlApp,
                                                      db_path: Path) -> None:
    assert wired.container is not None and wired.ledger is not None
    post(client, "/v6/control/halt", {})
    wired.ledger.trip_breaker("daily", "2026-09-16", "LOSS_3PCT", RECEIVED_AT)

    refused = post(client, "/v6/control/resume", {})
    assert refused.status_code == 409
    assert refused.json() == {"refusal": "APP-V6-RESUME-BREAKER", "halted": True,
                              "detail": "breaker tripped: daily:2026-09-16"}
    assert wired.container.halt_path.exists()

    wired.ledger.reset_breaker("daily", "2026-09-16", "operator", RECEIVED_AT + 1)
    resumed = post(client, "/v6/control/resume", {"reason": "checked"})
    repeat = post(client, "/v6/control/resume", b"")
    assert resumed.json() == {"halted": False, "removed": True}
    assert repeat.json() == {"halted": False, "removed": False}
    assert not wired.container.halt_path.exists()
    assert _actions(db_path)[-2:] == [("operator", "resume"), ("operator", "resume")]


def test_resume_reports_a_sentinel_that_cannot_be_removed(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def locked(_: Path) -> bool:
        raise PermissionError("file in use")

    monkeypatch.setattr(v6_control, "_remove_halt_file", locked)
    response = post(client, "/v6/control/resume", {})
    assert response.status_code == 503
    assert response.json()["detail"] == "halt file could not be removed"


def test_the_operator_token_never_reaches_logs_or_responses(
        client: TestClient, wired: ControlApp, caplog: pytest.LogCaptureFixture) -> None:
    assert wired.container is not None
    record_demo_poll(wired.container)
    with caplog.at_level(logging.DEBUG):
        bodies = [
            post(client, "/v6/control/session", {"action": "start"}).text,
            client.get("/v6/control/session", headers=AUTH).text,
            post(client, "/v6/control/halt", {}).text,
            post(client, "/v6/control/resume", {}).text,
            post(client, "/v6/control/session", {"action": "stop"}).text,
            post(client, "/v6/control/session", {"action": "stop"},
                 {**JSON, "Authorization": "Bearer " + TOKEN[:-1]}).text,
        ]
    assert all(TOKEN not in body and TOKEN[:-1] not in body for body in bodies)
    assert TOKEN[:-1] not in caplog.text
    assert "k" * 40 not in repr(wired.container.settings)
