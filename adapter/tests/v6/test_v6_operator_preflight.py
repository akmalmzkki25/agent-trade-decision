"""scripts/v6_operator.py preflight: one JSON verdict on whether the operator may trade."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.routes import v6_ea
from app.v6.clock import FakeClock

from .operator_cli_fixtures_v6 import (
    EA_KEY, TOKEN, RoutedTransport, cli, json_reply, operator_app, operator_status_document,
    packet, queue_of, replies, run_cli, session_document, status_document, warm_up,
)
from .payloads_v6 import RECEIVED_AT
from .test_v6_dashboard import (
    LOOPBACK, ControlApp, build_control_app, control_settings, record_demo_poll,
)

preflight = cli.preflight
NO_ENV_FILE = ["--env-file", "absent.env"]


@pytest.fixture
def ready_app(tmp_path: Path) -> Iterator[tuple[ControlApp, TestClient]]:
    built = operator_app(tmp_path)
    warm_up(built)
    with TestClient(built.app, client=LOOPBACK) as client:
        assert built.container is not None
        record_demo_poll(built.container)
        yield built, client


def fake(status: tuple[int, bytes] | Exception,
         session: tuple[int, bytes] | Exception = json_reply(200, session_document()),
         operator: tuple[int, bytes] | Exception = json_reply(200, operator_status_document()),
         ) -> RoutedTransport:
    return RoutedTransport(routes={("GET", "/v6/status"): replies(status),
                                   ("GET", "/v6/control/session"): replies(session),
                                   ("GET", "/v6/operator/status"): replies(operator)})


# --- against the real routes ---------------------------------------------------------
def test_a_ready_operator_setup_passes(ready_app: tuple[ControlApp, TestClient]) -> None:
    _, client = ready_app
    transport = RoutedTransport(client)
    result = run_cli([*NO_ENV_FILE, "preflight", "--agent", "codex"], transport)

    report = result.out_json()
    assert result.code == cli.EXIT_OK and result.err == ""
    assert report["ready"] is True and report["problems"] == [] and report["hints"] == {}
    assert (report["backend"], report["mode"], report["ea_signing"]) == (
        "operator", "execute", "required")
    assert (report["trade_mode"], report["runtime_status"], report["warm"]) == (
        "DEMO", "RUNNING", True)
    assert report["token_accepted"] is True and report["session"] is None
    assert (report["operator_api"], report["account_policy"], report["pending_cycle"]) == (
        True, "POLICY_OK", None)
    assert report["next"] == preflight.NEXT_START
    assert TOKEN not in result.text and EA_KEY not in result.text
    assert transport.paths() == ["/v6/status", "/v6/control/session", "/v6/operator/status"]
    assert "Authorization" not in transport.sent[0].headers
    assert transport.sent[1].headers["Authorization"] == f"Bearer {TOKEN}"


def test_the_default_shadow_rules_setup_is_not_ready(tmp_path: Path) -> None:
    built = build_control_app(control_settings(tmp_path), tmp_path / "shadow.db",
                              FakeClock(epoch=RECEIVED_AT))
    built.app.include_router(v6_ea.router)
    with TestClient(built.app, client=LOOPBACK) as client:
        result = run_cli([*NO_ENV_FILE, "preflight"], RoutedTransport(client))
    report = result.out_json()
    assert result.code == cli.EXIT_ERROR and report["ready"] is False
    assert report["problems"] == [preflight.P_BACKEND, preflight.P_MODE, preflight.P_EA_NOT_SEEN,
                                  preflight.P_TRADE_MODE_UNKNOWN, preflight.P_WARMUP]
    assert set(report["hints"]) == set(report["problems"])
    assert report["next"] == preflight.NEXT_FIX


def test_a_real_account_is_refused(tmp_path: Path) -> None:
    built = operator_app(tmp_path)
    warm_up(built)
    with TestClient(built.app, client=LOOPBACK) as client:
        assert built.container is not None
        record_demo_poll(built.container, trade_mode="REAL")
        result = run_cli([*NO_ENV_FILE, "preflight"], RoutedTransport(client))
    report = result.out_json()
    assert result.code == cli.EXIT_NOT_DEMO
    assert report["problems"] == [preflight.P_NOT_DEMO]
    assert report["next"] == preflight.NEXT_REFUSE and report["trade_mode"] == "REAL"
    assert report["account_policy"] == "POLICY_OPERATOR_DEMO_ONLY"


def test_the_adapter_account_policy_is_reported(tmp_path: Path) -> None:
    built = operator_app(tmp_path, demo_server_pattern="(?i)trial")
    warm_up(built)
    with TestClient(built.app, client=LOOPBACK) as client:
        assert built.container is not None
        record_demo_poll(built.container)
        result = run_cli([*NO_ENV_FILE, "preflight"], RoutedTransport(client))
    report = result.out_json()
    assert result.code == cli.EXIT_NOT_DEMO and report["problems"] == [preflight.P_NOT_DEMO]
    assert report["account_policy"] == "POLICY_SERVER_NOT_DEMO"


def test_a_pending_packet_says_wait_at_once(ready_app: tuple[ControlApp, TestClient]) -> None:
    built, client = ready_app
    assert built.ledger is not None
    built.ledger.start_session(trading_day="2026-09-16", backend="operator", mode="execute",
                               started_at=RECEIVED_AT, armed=True)
    offered = packet()
    queue_of(built).offer(offered)
    report = run_cli([*NO_ENV_FILE, "preflight"], RoutedTransport(client)).out_json()
    assert report["pending_cycle"] == offered.cycle_id
    assert report["next"] == preflight.NEXT_PENDING


def test_shadow_is_accepted_only_as_a_rehearsal(tmp_path: Path) -> None:
    built = operator_app(tmp_path, mode="shadow")
    warm_up(built)
    with TestClient(built.app, client=LOOPBACK) as client:
        assert built.container is not None
        record_demo_poll(built.container)
        strict = run_cli([*NO_ENV_FILE, "preflight"], RoutedTransport(client))
        rehearsal = run_cli([*NO_ENV_FILE, "preflight", "--allow-shadow"],
                            RoutedTransport(client))
    assert strict.code == 1 and strict.out_json()["problems"] == [preflight.P_MODE]
    assert rehearsal.code == 0 and rehearsal.out_json()["allow_shadow"] is True


def test_a_wrong_token_is_caught_without_echoing_it(
        ready_app: tuple[ControlApp, TestClient]) -> None:
    _, client = ready_app
    wrong = "wrong-" + "w" * 40
    result = run_cli([*NO_ENV_FILE, "preflight"], RoutedTransport(client),
                     environ={"V6_OPERATOR_TOKEN": wrong})
    report = result.out_json()
    assert result.code == 1 and report["problems"] == [preflight.P_TOKEN_REJECTED]
    assert report["token_accepted"] is False and wrong not in result.text


def test_without_a_token_nothing_is_sent_with_authorization(
        ready_app: tuple[ControlApp, TestClient]) -> None:
    _, client = ready_app
    transport = RoutedTransport(client)
    result = run_cli([*NO_ENV_FILE, "preflight"], transport, environ={})
    assert result.code == 1 and result.out_json()["problems"] == [preflight.P_TOKEN_MISSING]
    assert transport.paths() == ["/v6/status"]
    assert all("Authorization" not in sent.headers for sent in transport.sent)


def test_an_already_armed_session_says_wait(ready_app: tuple[ControlApp, TestClient]) -> None:
    built, client = ready_app
    assert built.ledger is not None
    started = built.ledger.start_session(trading_day="2026-09-16", backend="operator",
                                         mode="execute", started_at=RECEIVED_AT, armed=True)
    result = run_cli([*NO_ENV_FILE, "preflight"], RoutedTransport(client))
    report = result.out_json()
    assert result.code == 0 and report["next"] == preflight.NEXT_WAIT
    assert report["session"]["session_id"] == started.session.session_id
    assert report["session"]["armed"] is True and report["cycles_today"] == 0


# --- scripted adapters -----------------------------------------------------------------
def test_an_unreachable_adapter_still_prints_one_report() -> None:
    result = run_cli([*NO_ENV_FILE, "preflight"],
                     fake(cli.TransportError("ConnectionRefusedError")))
    report = result.out_json()
    assert result.code == cli.EXIT_UNREACHABLE and result.err == ""
    assert report["adapter_reachable"] is False and report["ready"] is False
    assert report["problems"] == [preflight.P_UNREACHABLE]
    assert report["transport_error"] == "ConnectionRefusedError"


@pytest.mark.parametrize(("status", "problems"), [
    (json_reply(404, {"detail": "V6 disabled"}), [preflight.P_DISABLED]),
    ((200, b"<html>"), [preflight.P_STATUS]),
    (json_reply(500, {"detail": "boom"}), [preflight.P_STATUS]),
])
def test_status_failures_are_problems(status: tuple[int, bytes], problems: list[str]) -> None:
    result = run_cli([*NO_ENV_FILE, "preflight"], fake(status))
    report = result.out_json()
    assert result.code == 1 and report["problems"] == problems
    assert report["adapter_reachable"] is True and report["session"]["armed"] is True


@pytest.mark.parametrize(("changes", "problems"), [
    ({"halt_file_present": True, "runtime": {"status": "HALTED", "breakers": []}},
     [preflight.P_HALTED]),
    ({"runtime": {"status": "BREAKER", "breakers": ["daily:2026-09-17"]}},
     [preflight.P_BREAKER]),
    ({"runtime": {"status": "STALE", "breakers": []}}, [preflight.P_EA_STALE]),
    ({"runtime": {"status": "SOMETHING_NEW", "breakers": []}}, [preflight.P_RUNTIME]),
    ({"runtime": None}, [preflight.P_RUNTIME]),
    ({"enabled": False, "runtime": {"status": "DISABLED", "breakers": []}},
     [preflight.P_DISABLED]),
    ({"operator_ready": False}, [preflight.P_TOKEN_ADAPTER]),
    ({"ea_signing": "available"}, [preflight.P_SIGNING]),
    ({"operator_agents": ["codex"]}, [preflight.P_AGENT]),
    ({"trade_mode": "CONTEST"}, [preflight.P_NOT_DEMO]),
])
def test_status_problems(changes: dict[str, Any], problems: list[str]) -> None:
    result = run_cli([*NO_ENV_FILE, "preflight", "--agent", "claude_code"],
                     fake(json_reply(200, status_document(**changes))))
    assert result.out_json()["problems"] == problems
    assert result.code == (cli.EXIT_NOT_DEMO if problems == [preflight.P_NOT_DEMO] else 1)


@pytest.mark.parametrize(("operator", "problems", "api"), [
    (json_reply(404, {"detail": "V6 operator backend disabled"}), [preflight.P_OPERATOR_API],
     False),
    (json_reply(200, operator_status_document(account_policy="POLICY_LOGIN_NOT_ALLOWED")),
     [preflight.P_ACCOUNT_POLICY], True),
    (json_reply(200, operator_status_document(account_policy="POLICY_UNKNOWN_TRADE_MODE")),
     [preflight.P_TRADE_MODE_UNKNOWN], True),
    (json_reply(200, operator_status_document(account_policy="POLICY_REAL_REFUSED")),
     [preflight.P_NOT_DEMO], True),
    (json_reply(401, {"detail": "invalid operator token"}), [], None),
    ((200, b"[]"), [], None),
    (cli.TransportError("TimeoutError"), [preflight.P_UNREACHABLE], None),
])
def test_operator_status_probe(operator: Any, problems: list[str], api: bool | None) -> None:
    result = run_cli([*NO_ENV_FILE, "preflight"],
                     fake(json_reply(200, status_document()), operator=operator))
    report = result.out_json()
    assert report["problems"] == problems and report["operator_api"] is api


@pytest.mark.parametrize(("session", "problems"), [
    (json_reply(503, {"detail": "V6_OPERATOR_TOKEN is not configured"}),
     [preflight.P_TOKEN_ADAPTER]),
    (json_reply(503, {"detail": "V6 storage unavailable"}), [preflight.P_SESSION_STATUS]),
    (json_reply(404, {"detail": "V6 disabled"}), [preflight.P_DISABLED]),
    ((200, b"[]"), [preflight.P_SESSION_STATUS]),
    (cli.TransportError("TimeoutError"), [preflight.P_UNREACHABLE]),
])
def test_session_probe_failures(session: Any, problems: list[str]) -> None:
    result = run_cli([*NO_ENV_FILE, "preflight"],
                     fake(json_reply(200, status_document()), session))
    report = result.out_json()
    assert report["problems"] == problems and report["token_accepted"] is None
    assert result.code == 1 and report["session"]["session_id"] == "a1b2c3d4e5f6"


def test_every_problem_has_a_hint() -> None:
    codes = {value for name, value in vars(preflight).items() if name.startswith("P_")}
    assert codes == set(preflight.HINTS)


def test_status_without_a_session_block_reports_none() -> None:
    session = json_reply(200, session_document(session=None))
    result = run_cli([*NO_ENV_FILE, "preflight"],
                     fake(json_reply(200, status_document()), session))
    assert result.code == 0 and result.out_json()["session"] is None
    assert result.out_json()["next"] == preflight.NEXT_START


def test_exit_code_rules() -> None:
    assert preflight.exit_code_for({"problems": []}) == 0
    assert preflight.exit_code_for({"problems": ["X"], "adapter_reachable": True}) == 1
    assert preflight.exit_code_for({"problems": ["X"], "adapter_reachable": False}) == 3
    assert preflight.exit_code_for({"problems": ["X", preflight.P_NOT_DEMO],
                                    "adapter_reachable": False}) == 5
