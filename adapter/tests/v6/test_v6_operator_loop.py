"""The whole operator loop through the CLI against the real control and operator routes."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from .operator_cli_fixtures_v6 import (
    TOKEN, ManualClock, Result, RoutedTransport, cli, operator_app, packet, queue_of, run_cli,
    warm_up,
)
from .operator_fixtures_v6 import BUY_ID, EVENT_ID
from .test_v6_dashboard import LOOPBACK, ControlApp, record_demo_poll

NO_ENV_FILE = ["--env-file", "absent.env"]


def op(argv: list[str], transport: RoutedTransport, clock: ManualClock | None = None) -> Result:
    return run_cli([*NO_ENV_FILE, *argv], transport, clock=clock)


def start_and_wait(built: ControlApp, transport: RoutedTransport,
                   packet_file: Path) -> list[Result]:
    ready = op(["preflight", "--agent", "claude_code"], transport)
    assert ready.code == cli.EXIT_OK, ready.text
    started = op(["session", "start"], transport)
    assert started.code == cli.EXIT_OK, started.text
    session = started.out_json()
    assert (session["created"], session["mode"], session["refusal"]) == (True, "execute", None)

    offered = packet(session_id=session["session_id"])
    queue_of(built).offer(offered)
    waited = op(["wait", "--agent", "claude_code", "--out", str(packet_file)], transport)
    assert waited.code == cli.EXIT_OK, waited.text
    assert waited.out.startswith(f"V6 PACKET cycle {offered.cycle_id}")
    return [ready, started, waited]


def decide_and_submit(transport: RoutedTransport, packet_file: Path,
                      decision_file: Path) -> list[Result]:
    templated = op(["template", "--packet", str(packet_file), "--out", str(decision_file)],
                   transport)
    assert templated.code == cli.EXIT_OK, templated.text
    decision = json.loads(decision_file.read_text(encoding="utf-8"))
    decision["views"]["price_action"]["ranked"][0]["conviction"] = 0.7
    decision["views"]["news_risk"]["event_ids"] = [EVENT_ID]
    decision["chief"] = {**decision["chief"], "action": "ENTER", "candidate_id": BUY_ID,
                         "risk_tier": "standard", "confidence": 0.6,
                         "rationale": "PA TAKE 0.70, no veto"}
    decision_file.write_text(json.dumps(decision), encoding="utf-8")

    submitted = op(["submit", "--agent", "claude_code", "--file", str(decision_file),
                    "--packet", str(packet_file)], transport, clock=ManualClock())
    verdict = submitted.out_json()
    assert submitted.code == cli.EXIT_OK, submitted.text
    assert (verdict["code"], verdict["chief_action"], verdict["warnings"]) == (
        "ACCEPTED", "ENTER", [])
    return [templated, submitted]


def test_mulai_then_sudah_cukup(tmp_path: Path) -> None:
    built = operator_app(tmp_path)
    warm_up(built)
    packet_file, decision_file = tmp_path / "packet.json", tmp_path / "decision.json"
    with TestClient(built.app, client=LOOPBACK) as client:
        assert built.container is not None
        record_demo_poll(built.container)
        transport = RoutedTransport(client)
        results = start_and_wait(built, transport, packet_file)
        results += decide_and_submit(transport, packet_file, decision_file)

        status = op(["session", "status"], transport).out_json()
        assert status["active"] is True
        stopped = op(["session", "stop", "--reason", "sudah_cukup"], transport)
        report = stopped.out_json()
        assert stopped.code == cli.EXIT_OK
        assert (report["stopped"], report["command"], report["stop_reason"]) == (
            True, "CANCEL_PENDING", "sudah_cukup")
        assert "keep running" in report["positions"]

        after = op(["wait", "--out", str(packet_file)], transport)
        assert after.code == cli.EXIT_NO_SESSION
    assert all(TOKEN not in result.text for result in (*results, stopped, after))
