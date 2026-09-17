"""scripts/v6_operator.py wait: long-poll until a packet, the timeout or the end of the session."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from .operator_cli_fixtures_v6 import (
    LONG_POLL_S, PACKET_NOW, TOKEN, ManualClock, RoutedTransport, cli, json_reply,
    operator_app, packet, packet_document, queue_of, replies, run_cli, status_document,
    warm_up,
)
from .operator_fixtures_v6 import BUY_ID, EXPIRES, SELL_ID
from .payloads_v6 import RECEIVED_AT
from .test_v6_dashboard import LOOPBACK, ControlApp, record_demo_poll

waiting = cli.waiting
NO_ENV_FILE = ["--env-file", "absent.env"]
SESSION = status_document()["session"]


def wait_body(pending: object) -> dict[str, Any]:
    """The shape of POST /v6/operator/wait (routes/v6_operator.py)."""
    return {"pending": pending, "session": SESSION, "armed": True, "mode": "execute",
            "server_time_epoch": int(PACKET_NOW)}


EMPTY = json_reply(200, wait_body(None))
WAIT = ("POST", "/v6/operator/wait")
STATUS = ("GET", "/v6/status")


@pytest.fixture
def adapter(tmp_path: Path) -> Iterator[tuple[ControlApp, TestClient]]:
    built = operator_app(tmp_path)
    warm_up(built)
    with TestClient(built.app, client=LOOPBACK) as client:
        assert built.container is not None and built.ledger is not None
        record_demo_poll(built.container)
        built.ledger.start_session(trading_day="2026-09-16", backend="operator",
                                   mode="execute", started_at=RECEIVED_AT, armed=True)
        yield built, client


def out_args(tmp_path: Path, *extra: str) -> list[str]:
    return [*NO_ENV_FILE, "wait", "--out", str(tmp_path / "packet.json"), *extra]


def scripted(status: Any = None, **routes: Any) -> RoutedTransport:
    handlers = {STATUS: replies(json_reply(200, status_document()) if status is None else status)}
    handlers.update({WAIT: routes["wait"]} if "wait" in routes else {})
    return RoutedTransport(routes=handlers)


# --- packets -------------------------------------------------------------------------
def test_a_packet_is_written_and_summarised(adapter: tuple[ControlApp, TestClient],
                                            tmp_path: Path) -> None:
    _, client = adapter
    clock = ManualClock()
    document = packet_document()
    (tmp_path / "packet.json").write_text('{"stale": true}', encoding="utf-8")
    wait = replies(EMPTY, EMPTY, json_reply(200, wait_body(document)), advance=LONG_POLL_S,
                   clock=clock)
    transport = RoutedTransport(client, {WAIT: wait})
    clock.now = PACKET_NOW - 3 * LONG_POLL_S

    result = run_cli(out_args(tmp_path, "--agent", "codex"), transport, clock=clock)

    assert result.code == cli.EXIT_OK, result.text
    assert json.loads((tmp_path / "packet.json").read_text(encoding="utf-8")) == document
    assert json.loads((tmp_path / "packet.prev.json").read_text(encoding="utf-8")) == {
        "stale": True}
    lines = result.out.splitlines()
    assert lines[0].startswith(f"V6 PACKET cycle {document['cycle_id']} | mode execute")
    assert f"({int(EXPIRES - PACKET_NOW)} s left)" in lines[1]
    assert any(line.startswith(f"  [1] {BUY_ID} BUY displacement") for line in lines)
    assert any(line.startswith(f"  [2] {SELL_ID} SELL orb") for line in lines)
    assert "lots 0.01 risk $7.40" in result.out and "body_ratio=0.8" in result.out
    assert "calendar: clear | next HIGH USD cpi-yy in 105 min" in result.out
    assert "gates: all 2 passed" in result.out and TOKEN not in result.text
    posts = [sent for sent in transport.sent if sent.path == WAIT[1]]
    assert len(posts) == 3
    assert all(json.loads(sent.body or b"") == {"timeout_s": 25.0, "agent": "codex"}
               for sent in posts)
    assert posts[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert posts[0].timeout == waiting.WAIT_POLL_S + waiting.HTTP_MARGIN_S
    assert transport.paths().count(STATUS[1]) == 3


@pytest.mark.parametrize("body", [packet_document(), {"packet": packet_document()}])
def test_a_bare_or_aliased_packet_is_accepted(tmp_path: Path, body: dict[str, Any]) -> None:
    transport = scripted(wait=replies(json_reply(200, body)))
    result = run_cli(out_args(tmp_path), transport)
    assert result.code == 0 and (tmp_path / "packet.json").exists()
    assert json.loads(transport.sent[-1].body or b"") == {"timeout_s": 25.0}


def test_the_real_route_serves_a_pending_packet(adapter: tuple[ControlApp, TestClient],
                                                tmp_path: Path) -> None:
    built, client = adapter
    offered = packet()
    queue_of(built).offer(offered)
    result = run_cli(out_args(tmp_path, "--agent", "claude_code"), RoutedTransport(client))
    assert result.code == cli.EXIT_OK, result.text
    written = json.loads((tmp_path / "packet.json").read_text(encoding="utf-8"))
    assert written == offered.model_dump(mode="json")
    status = client.get("/v6/operator/status",
                        headers={"Authorization": f"Bearer {TOKEN}"}).json()
    assert status["last_agent"]["agent"] == "claude_code"


def test_the_real_route_refuses_a_disabled_agent(tmp_path: Path) -> None:
    built = operator_app(tmp_path, operator_agents_csv="claude_code")
    with TestClient(built.app, client=LOOPBACK) as client:
        assert built.container is not None and built.ledger is not None
        record_demo_poll(built.container)
        built.ledger.start_session(trading_day="2026-09-16", backend="operator",
                                   mode="execute", started_at=RECEIVED_AT, armed=True)
        result = run_cli(out_args(tmp_path, "--agent", "codex"), RoutedTransport(client))
    report = result.err_json()
    assert result.code == cli.EXIT_ERROR and report["http_status"] == 403
    assert report["detail"].startswith("POLICY_AGENT_NOT_ALLOWED: ")


@pytest.mark.parametrize(("packet", "code", "detail"), [
    ({"schema_version": "v6.operator.packet.2"}, 1,
     "malformed packet: cycle_id,packet_hash,expires_at_epoch,candidates"),
    ([1, 2], 1, "the packet is not a JSON object"),
])
def test_malformed_packets_are_not_written(tmp_path: Path, packet: Any, code: int,
                                           detail: str) -> None:
    transport = scripted(wait=replies(json_reply(200, wait_body(packet))))
    result = run_cli(out_args(tmp_path), transport)
    assert (result.code, result.err_json()["detail"]) == (code, detail)
    assert not (tmp_path / "packet.json").exists()


def test_a_packet_for_a_non_demo_account_is_refused(tmp_path: Path) -> None:
    document = packet_document()
    document["account"]["trade_mode"] = "REAL"
    transport = scripted(wait=replies(json_reply(200, wait_body(document))))
    result = run_cli(out_args(tmp_path), transport)
    assert result.code == cli.EXIT_NOT_DEMO
    assert result.out_json() == {"outcome": "not_demo", "detail": "packet trade_mode is REAL",
                                 "next": waiting.NEXT_NOT_DEMO}
    assert not (tmp_path / "packet.json").exists()


def test_an_expired_packet_is_skipped(tmp_path: Path) -> None:
    clock = ManualClock(now=float(EXPIRES + 1))
    wait = replies(json_reply(200, wait_body(packet_document())), advance=LONG_POLL_S,
                   clock=clock)
    result = run_cli(out_args(tmp_path, "--timeout", "60"), scripted(wait=wait), clock=clock)
    assert result.code == cli.EXIT_TIMEOUT and not (tmp_path / "packet.json").exists()
    assert result.out_json()["skipped_expired"] == 3


# --- endings -------------------------------------------------------------------------
def test_the_timeout_ends_the_wait(adapter: tuple[ControlApp, TestClient],
                                   tmp_path: Path) -> None:
    _, client = adapter
    clock = ManualClock()
    transport = RoutedTransport(client, {WAIT: replies(EMPTY, advance=LONG_POLL_S, clock=clock)})
    result = run_cli(out_args(tmp_path, "--timeout", "100"), transport, clock=clock)
    report = result.out_json()
    assert result.code == cli.EXIT_TIMEOUT and report["outcome"] == "timeout"
    assert (report["waited_s"], report["runtime_status"], report["armed"]) == (100, "RUNNING", True)
    assert report["halted"] is False and report["next"] == waiting.NEXT_TIMEOUT
    assert report["skipped_expired"] == 0
    assert transport.paths().count(WAIT[1]) == 4 and not (tmp_path / "packet.json").exists()


def test_no_active_session_ends_the_wait_before_polling(
        adapter: tuple[ControlApp, TestClient], tmp_path: Path) -> None:
    built, client = adapter
    assert built.ledger is not None
    session = built.ledger.active_session()
    assert session is not None
    built.ledger.stop_session(session.session_id, stopped_at=RECEIVED_AT + 1, reason="done")
    transport = RoutedTransport(client, {WAIT: replies(EMPTY)})
    result = run_cli(out_args(tmp_path), transport)
    assert result.code == cli.EXIT_NO_SESSION
    assert result.out_json()["outcome"] == "no_session"
    assert WAIT[1] not in transport.paths()


@pytest.mark.parametrize("status", [
    status_document(trade_mode="REAL"), status_document(trade_mode="CONTEST")])
def test_a_non_demo_status_ends_the_wait(tmp_path: Path, status: dict[str, Any]) -> None:
    transport = scripted(json_reply(200, status), wait=replies(EMPTY))
    result = run_cli(out_args(tmp_path), transport)
    assert result.code == cli.EXIT_NOT_DEMO and WAIT[1] not in transport.paths()


@pytest.mark.parametrize(("reply", "code", "outcome"), [
    (json_reply(403, {"detail": "APP-V6-DEMO-403"}), 5, "not_demo"),
    (json_reply(403, {"detail": {"code": "POLICY_OPERATOR_DEMO_ONLY"}}), 5, "not_demo"),
    (json_reply(403, {"code": "APP-V6-DEMO-403", "policy": "POLICY_SERVER_NOT_DEMO",
                      "detail": "server 'Live-1' does not match"}), 5, "not_demo"),
    (json_reply(409, {"refusal": "APP-V6-NO-SESSION", "detail": "no session"}), 4, "no_session"),
    (json_reply(200, {"packet": None, "session_active": False}), 4, "no_session"),
    (json_reply(200, {**wait_body(packet_document()), "session": None}), 4, "no_session"),
    (json_reply(200, {**wait_body(None), "session": {**SESSION, "active": False}}), 4,
     "no_session"),
])
def test_refusals_from_the_wait_route(tmp_path: Path, reply: tuple[int, bytes], code: int,
                                      outcome: str) -> None:
    result = run_cli(out_args(tmp_path), scripted(wait=replies(reply)))
    assert result.code == code and result.out_json()["outcome"] == outcome


@pytest.mark.parametrize(("status", "detail"), [
    (json_reply(404, {"detail": "V6 disabled"}), "V6 is disabled on the adapter"),
    (json_reply(200, status_document(backend="rules")), "V6_BACKEND is not operator"),
    ((200, b"<html>"), "GET /v6/status failed"),
])
def test_status_problems_end_the_wait(tmp_path: Path, status: tuple[int, bytes],
                                      detail: str) -> None:
    result = run_cli(out_args(tmp_path), scripted(status, wait=replies(EMPTY)))
    assert result.code == cli.EXIT_ERROR and result.err_json()["detail"] == detail


@pytest.mark.parametrize(("reply", "detail"), [
    (json_reply(401, {"detail": "invalid operator token"}), "invalid operator token"),
    (json_reply(404, {"detail": "Not Found"}), "Not Found"),
    ((200, b"not json"), "non-JSON response"),
])
def test_other_wait_answers_are_errors(tmp_path: Path, reply: tuple[int, bytes],
                                       detail: str) -> None:
    result = run_cli(out_args(tmp_path), scripted(wait=replies(reply)))
    report = result.err_json()
    assert result.code == cli.EXIT_ERROR and report["error"] == "wait_failed"
    assert report["detail"] == detail


def test_the_session_can_be_active_without_a_session_block(tmp_path: Path) -> None:
    status = status_document()
    del status["session"]
    transport = scripted(json_reply(200, status), wait=replies(json_reply(200, packet_document())))
    assert run_cli(out_args(tmp_path), transport).code == 0


# --- resilience ----------------------------------------------------------------------
def test_short_outages_are_retried(tmp_path: Path) -> None:
    clock = ManualClock()
    wait = replies(cli.TransportError("ConnectionResetError"), json_reply(503, {"detail": "x"}),
                   json_reply(200, wait_body(packet_document())))
    result = run_cli(out_args(tmp_path), scripted(wait=wait), clock=clock)
    assert result.code == 0 and clock.slept[:2] == [waiting.RETRY_PAUSE_S] * 2


def test_a_lasting_outage_is_an_error(tmp_path: Path) -> None:
    transport = scripted(cli.TransportError("ConnectionRefusedError"))
    result = run_cli(out_args(tmp_path), transport)
    report = result.err_json()
    assert result.code == cli.EXIT_ERROR and report["error"] == "unreachable"
    assert report["detail"] == "ConnectionRefusedError"
    assert len(transport.sent) == waiting.MAX_TRANSIENT_FAILURES


def test_an_unknown_account_is_retried_not_refused(tmp_path: Path) -> None:
    unknown = json_reply(403, {"code": "APP-V6-DEMO-403", "policy": "POLICY_UNKNOWN_TRADE_MODE",
                               "detail": "no EA poll has reported the account yet"})
    transport = scripted(wait=replies(unknown, json_reply(200, wait_body(packet_document()))))
    assert run_cli(out_args(tmp_path), transport).code == cli.EXIT_OK

    stuck = run_cli(out_args(tmp_path), scripted(wait=replies(unknown)))
    report = stuck.err_json()
    assert stuck.code == cli.EXIT_ERROR and report["error"] == "unavailable"
    assert report["detail"].startswith("APP-V6-DEMO-403: POLICY_UNKNOWN_TRADE_MODE")


def test_a_status_5xx_is_transient(tmp_path: Path) -> None:
    status = replies(json_reply(503, {"detail": "V6 storage unavailable"}),
                     json_reply(200, status_document()))
    transport = RoutedTransport(routes={
        STATUS: status, WAIT: replies(json_reply(200, packet_document()))})
    assert run_cli(out_args(tmp_path), transport).code == 0


def test_instant_empty_answers_do_not_spin(tmp_path: Path) -> None:
    clock = ManualClock()
    transport = scripted(wait=replies((204, b"")))
    result = run_cli(out_args(tmp_path, "--timeout", "10"), transport, clock=clock)
    assert result.code == cli.EXIT_TIMEOUT
    assert transport.paths().count(WAIT[1]) == 5
    assert clock.slept == [waiting.IDLE_PAUSE_S] * 5


def test_a_packet_that_cannot_be_written_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "blocker").write_text("a file, not a folder", encoding="utf-8")
    argv = [*NO_ENV_FILE, "wait", "--out", str(tmp_path / "blocker" / "packet.json")]
    result = run_cli(argv, scripted(wait=replies(json_reply(200, packet_document()))))
    assert result.code == cli.EXIT_ERROR and result.err_json()["error"] in {"write_failed", "io"}


# --- arguments -----------------------------------------------------------------------
@pytest.mark.parametrize("extra", [["--timeout", "0"], ["--timeout", "3601"],
                                   ["--timeout", "soon"], ["--out", "packet.txt"]])
def test_bad_wait_arguments(tmp_path: Path, extra: list[str],
                            capsys: pytest.CaptureFixture) -> None:
    transport = scripted(wait=replies(EMPTY))
    assert run_cli([*NO_ENV_FILE, "wait", *extra], transport).code == cli.EXIT_USAGE
    assert transport.sent == [] and "usage" in capsys.readouterr().err


def test_wait_needs_the_token(tmp_path: Path) -> None:
    transport = scripted(wait=replies(EMPTY))
    result = run_cli(out_args(tmp_path), transport, environ={})
    assert result.code == cli.EXIT_USAGE and transport.sent == []
    assert "V6_OPERATOR_TOKEN" in result.err_json()["detail"]


def test_marker_matching() -> None:
    assert waiting.collect_codes({"detail": [{"code": "A"}, "B"], "refusal": "C",
                                  "other": "D"}) == ("C", "A", "B")
    assert waiting.describe(("X", "X", "Y\x1b")) == "X: Y?"
    assert waiting.collect_codes(["APP-V6-DEMO-403"]) == ()
    assert waiting.has_marker(("x: APP-V6-NO-SESSION",), waiting.NO_SESSION_MARKERS)
    assert not waiting.has_marker(("APP-V6-GATE",), waiting.DEMO_REFUSAL_MARKERS)


@pytest.mark.parametrize(("policy", "code"), [
    ("POLICY_REAL_REFUSED", cli.EXIT_NOT_DEMO), ("POLICY_LOGIN_NOT_ALLOWED", cli.EXIT_ERROR),
    ("POLICY_REAL_ACCOUNT_FLAG", cli.EXIT_ERROR)])
def test_only_a_demo_policy_refusal_exits_5(tmp_path: Path, policy: str, code: int) -> None:
    refused = json_reply(403, {"code": "APP-V6-DEMO-403", "policy": policy, "detail": "x"})
    result = run_cli(out_args(tmp_path), scripted(wait=replies(refused)))
    assert result.code == code
    assert waiting.demo_refused(("APP-V6-DEMO-403",), {"policy": policy}) is (
        code == cli.EXIT_NOT_DEMO)
