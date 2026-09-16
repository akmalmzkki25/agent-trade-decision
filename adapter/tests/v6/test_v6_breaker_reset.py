"""POST /v6/control/breaker/reset and `v6_operator.py breaker-reset` (plan section 6, reset manual).

A trip survives restarts and period rollover, so without this route a single trip
(a legitimate 3% day, or a V5 loss on the same account) kept V6 in BREAKER for good.
"""

from __future__ import annotations

import io
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.routes.v6_control import NO_DAY_ANCHOR, NO_FRESH_POLL
from app.v6.clock import FakeClock
from app.v6.ledger_v6 import AccountMark
from app.v6.risk.breakers import (
    RESET_DONE, RESET_NOT_EVALUATED, RESET_NOT_TRIPPED, RESET_REFUSED_CONDITION,
)
from app.v6.runtime.breaker_feed import INPUTS_UNUSABLE

from .payloads_v6 import RECEIVED_AT, as_poll, poll_payload
from .test_v6_dashboard import (
    AUTH, JSON, LOOPBACK, TOKEN, ControlApp, build_control_app, control_settings, post,
)
from .test_v6_operator_cli import ClientTransport, cli

PATH = "/v6/control/breaker/reset"
TODAY, YESTERDAY = "2026-09-16", "2026-09-15"
YESTERDAY_RESET = {"scope": "daily", "period_key": YESTERDAY, "reason": "reviewed"}


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(epoch=RECEIVED_AT)


@pytest.fixture
def wired(tmp_path: Path, clock: FakeClock) -> ControlApp:
    return build_control_app(control_settings(tmp_path), tmp_path / "reset.db", clock)


@pytest.fixture
def client(wired: ControlApp) -> Iterator[TestClient]:
    with TestClient(wired.app, client=LOOPBACK) as test_client:
        yield test_client


def _poll(wired: ControlApp, equity: float = 2010.5, *, mark_equity: float | None = 2010.5,
          ) -> None:
    """The EA polls now; the poll route would also mark the day's first equity."""
    assert wired.container is not None
    now = wired.container.clock.now_epoch()
    poll = as_poll(poll_payload(equity=equity))
    wired.container.ea_state.record_poll(poll, now)
    if mark_equity is not None:
        mark = AccountMark.from_poll(as_poll(poll_payload(equity=mark_equity)), now - 60)
        assert wired.container.ledger_v6.record_account_mark(mark)


def _trip(wired: ControlApp, key: str = YESTERDAY) -> None:
    assert wired.ledger is not None
    wired.ledger.trip_breaker("daily", key, "EQUITY_DRAWDOWN", RECEIVED_AT - 86_400)


def _actions(wired: ControlApp) -> list[tuple[str, str, dict[str, Any]]]:
    assert wired.container is not None
    with sqlite3.connect(wired.container.ledger_v6.path) as conn:
        rows = conn.execute("SELECT actor, action, detail_json FROM v6_control_log"
                            " WHERE action = 'breaker_reset' ORDER BY id").fetchall()
    return [(actor, action, json.loads(detail)) for actor, action, detail in rows]


def test_a_past_trip_is_cleared_and_resume_is_allowed_again(client: TestClient,
                                                            wired: ControlApp) -> None:
    assert wired.ledger is not None
    _trip(wired)
    assert post(client, "/v6/control/resume", {}).status_code == 409
    _poll(wired)

    response = post(client, PATH, YESTERDAY_RESET)

    assert response.status_code == 200
    assert response.json() == {"reset": True, "code": RESET_DONE, "scope": "daily",
                               "period_key": YESTERDAY,
                               "detail": f"daily {YESTERDAY} reset by operator"}
    assert wired.ledger.active_breakers() == ()
    record = wired.ledger.get_breaker("daily", YESTERDAY)
    assert record is not None and record.reset_by == "operator"
    assert post(client, "/v6/control/resume", {}).status_code == 200
    assert _actions(wired) == [("operator", "breaker_reset", {
        "scope": "daily", "period_key": YESTERDAY, "reason": "reviewed", "result": RESET_DONE})]


@pytest.mark.parametrize(("prepare", "code", "detail"), [
    ("no_poll", RESET_NOT_EVALUATED, NO_FRESH_POLL),
    ("stale_poll", RESET_NOT_EVALUATED, NO_FRESH_POLL),
    ("no_day_anchor", RESET_NOT_EVALUATED, NO_DAY_ANCHOR),
    ("unconnected", RESET_NOT_EVALUATED, INPUTS_UNUSABLE),
    ("still_in_breach", RESET_REFUSED_CONDITION, "still in breach"),
    ("not_tripped", RESET_NOT_TRIPPED, "is not tripped"),
])
def test_resets_are_refused_without_a_fresh_clean_evaluation(
        client: TestClient, wired: ControlApp, clock: FakeClock, prepare: str, code: str,
        detail: str) -> None:
    assert wired.ledger is not None and wired.container is not None
    key = TODAY if prepare in ("still_in_breach", "not_tripped") else YESTERDAY
    if prepare != "not_tripped":
        _trip(wired, key)
    if prepare == "stale_poll":
        _poll(wired)
        clock.advance(wired.container.settings.ea_stale_s + 1)
    elif prepare in ("no_day_anchor", "unconnected"):
        _poll(wired, 0.0 if prepare == "unconnected" else 2010.5, mark_equity=None)
    elif prepare != "no_poll":
        _poll(wired, 1900.0 if prepare == "still_in_breach" else 2010.5)

    response = post(client, PATH, {"scope": "daily", "period_key": key})

    assert response.status_code == 409
    body = response.json()
    assert (body["reset"], body["code"]) == (False, code) and detail in body["detail"]
    assert len(wired.ledger.active_breakers()) == (0 if prepare == "not_tripped" else 1)
    assert _actions(wired)[-1][2]["result"] == code


@pytest.mark.parametrize("body", [
    {}, {"scope": "hourly", "period_key": TODAY}, {"scope": "daily", "period_key": "today"},
    {"scope": "daily", "period_key": TODAY + "\n"}, {"scope": "weekly", "period_key": "2026-38"},
    {"scope": "daily", "period_key": TODAY, "reason": "has spaces"},
    {"scope": "daily", "period_key": TODAY, "actor": "someone"},
])
def test_malformed_reset_commands_are_400(client: TestClient, body: dict[str, Any]) -> None:
    assert post(client, PATH, body).status_code == 400


def test_the_reset_needs_the_operator_token(tmp_path: Path, client: TestClient,
                                           wired: ControlApp, clock: FakeClock) -> None:
    assert wired.plane is not None
    csrf = {**JSON, "X-V6-CSRF": wired.plane.csrf_nonce}
    assert post(client, PATH, YESTERDAY_RESET, csrf).status_code == 401
    assert post(client, PATH, YESTERDAY_RESET, dict(JSON)).status_code == 401
    unset = build_control_app(control_settings(tmp_path, operator_token=""),
                              tmp_path / "unset.db", clock)
    with TestClient(unset.app, client=LOOPBACK) as other:
        assert post(other, PATH, YESTERDAY_RESET).status_code == 503


def test_unreadable_breaker_state_is_503(client: TestClient, wired: ControlApp,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    assert wired.container is not None
    _trip(wired)
    _poll(wired)

    async def broken(*_: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(wired.container.parts.breakers, "for_poll", broken)
    response = post(client, PATH, YESTERDAY_RESET)
    assert response.status_code == 503
    assert response.json()["detail"] == "V6 storage unavailable"


# --- CLI -------------------------------------------------------------------------------
def _cli(argv: list[str], transport: ClientTransport) -> tuple[int, dict[str, Any], str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(["--env-file", "absent.env", *argv],
                    environ={"V6_OPERATOR_TOKEN": TOKEN}, transport=transport,
                    stdout=out, stderr=err)
    stream = out if code == cli.EXIT_OK else err
    return code, json.loads(stream.getvalue()), out.getvalue() + err.getvalue()


def test_the_cli_resets_a_breaker_and_reports_refusals(wired: ControlApp) -> None:
    _trip(wired)
    argv = ["breaker-reset", "--scope", "daily", "--period-key", YESTERDAY]
    with TestClient(wired.app, client=LOOPBACK) as client:
        transport = ClientTransport(client)
        code, refused, text = _cli(argv, transport)
        assert code == cli.EXIT_HTTP_ERROR
        assert refused == {"error": 409, "code": RESET_NOT_EVALUATED, "detail": NO_FRESH_POLL}

        _poll(wired)
        code, done, _ = _cli([*argv, "--reason", "reviewed"], transport)

    assert code == cli.EXIT_OK and (done["reset"], done["code"]) == (True, RESET_DONE)
    method, url, headers = transport.sent[-1]
    assert (method, url) == ("POST", "http://127.0.0.1:8765" + PATH)
    assert headers["Authorization"] == AUTH["Authorization"] and TOKEN not in text


@pytest.mark.parametrize("argv", [
    ["breaker-reset"], ["breaker-reset", "--scope", "daily"],
    ["breaker-reset", "--scope", "hourly", "--period-key", TODAY],
    ["breaker-reset", "--scope", "daily", "--period-key", "16-09-2026"],
    ["breaker-reset", "--scope", "daily", "--period-key", TODAY + "\n"],
])
def test_bad_breaker_reset_arguments_are_usage_errors(argv: list[str],
                                                      capsys: pytest.CaptureFixture) -> None:
    assert cli.main(argv, environ={}) == cli.EXIT_USAGE
    assert "usage" in capsys.readouterr().err


def test_the_cli_builds_the_reset_call() -> None:
    args = cli.build_parser().parse_args(
        ["breaker-reset", "--scope", "weekly", "--period-key", "2026-W38"])
    call = cli.build_call(args)
    assert (call.method, call.path, call.needs_token) == ("POST", PATH, True)
    assert call.body == {"scope": "weekly", "period_key": "2026-W38",
                         "reason": "operator_reset"}
