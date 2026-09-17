"""
"Mulai trading skrg" and "Sudah cukup hari ini" through the operator CLI, against the
whole adapter in execute mode (create_app: lifespan, engine, queue, intent book).

The CLI talks to the real routes through `RoutedTransport`; the EA side signs its
requests like `ea/QlipV6`. Packets come from the real engine, the intent from the real
builder and book, and the signed poll delivers it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.v6.clock import FakeClock

from . import engine_fixtures_v6 as ef
from .execute_fixtures_v6 import (
    TOKEN, Adapter, enter_decision, execute_settings, execution, fixed_candidate, running,
)
from .operator_cli_fixtures_v6 import ManualClock, Result, RoutedTransport, cli, run_cli

NO_ENV_FILE = ["--env-file", "absent.env"]
AGENT = "codex"


@pytest.fixture
def adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Adapter]:
    fixed_candidate(monkeypatch)
    clock = FakeClock(epoch=float(ef.AS_OF + 1))
    with running(tmp_path, execute_settings(tmp_path), clock) as live:
        yield live


def op(adapter: Adapter, *argv: str) -> Result:
    """One CLI command; its clock reads the adapter's."""
    return run_cli([*NO_ENV_FILE, *argv], RoutedTransport(adapter.client),
                   environ={"V6_OPERATOR_TOKEN": TOKEN},
                   clock=ManualClock(now=adapter.clock.now_epoch()))


def mulai(adapter: Adapter) -> dict[str, Any]:
    """Preflight and session start, as the skill runs them."""
    ready = op(adapter, "preflight", "--agent", AGENT)
    assert ready.code == cli.EXIT_OK, ready.text
    verdict = ready.out_json()
    assert (verdict["mode"], verdict["ea_signing"], verdict["trade_mode"]) == (
        "execute", "required", "DEMO")
    assert (verdict["account_policy"], verdict["next"]) == ("POLICY_OK", "run: session start")
    started = op(adapter, "session", "start")
    assert started.code == cli.EXIT_OK, started.text
    return started.out_json()


def decide(adapter: Adapter, packet_file: Path, decision_file: Path) -> dict[str, Any]:
    templated = op(adapter, "template", "--packet", str(packet_file),
                   "--out", str(decision_file))
    assert templated.code == cli.EXIT_OK, templated.text
    packet = json.loads(packet_file.read_text(encoding="utf-8"))
    decision = enter_decision(packet) | {"agent": None}
    decision_file.write_text(json.dumps(decision), encoding="utf-8")
    submitted = op(adapter, "submit", "--agent", AGENT, "--file", str(decision_file),
                   "--packet", str(packet_file))
    assert submitted.code == cli.EXIT_OK, submitted.text
    return submitted.out_json()


def test_the_cli_trades_a_demo_session_and_stops_it(adapter: Adapter, tmp_path: Path) -> None:
    packet_file, decision_file = tmp_path / "packet.json", tmp_path / "decision.json"
    adapter.backfill()
    adapter.poll()
    started = mulai(adapter)
    assert (started["armed"], started["arm_reason"], started["mode"]) == (
        True, "ARMED", "execute")

    cycle_id = adapter.snapshot("snap-cli-1")
    waited = op(adapter, "wait", "--agent", AGENT, "--timeout", "30", "--out", str(packet_file))
    assert waited.code == cli.EXIT_OK, waited.text
    assert waited.out.startswith(f"V6 PACKET cycle {cycle_id} | mode execute")
    assert "armed yes" in waited.out

    adapter.poll()                                    # the quote the builder judges by
    verdict = decide(adapter, packet_file, decision_file)
    assert (verdict["code"], verdict["chief_action"]) == ("ACCEPTED", "ENTER")
    assert adapter.cycle(cycle_id) == ("ENTER", None, "operator", "ok")
    delivered = adapter.poll()
    assert (delivered["has_intent"], delivered["order_type"]) == (True, "BUY_LIMIT")

    status = op(adapter, "session", "status").out_json()
    assert (status["armed"], status["intents"], status["intent_statuses"]) == (
        True, 1, {"DELIVERED": 1})

    stopped = op(adapter, "session", "stop", "--reason", "sudah_cukup")
    report = stopped.out_json()
    assert stopped.code == cli.EXIT_OK, stopped.text
    assert (report["stopped"], report["command"], report["armed"]) == (
        True, "CANCEL_PENDING", False)
    assert report["intent_statuses"] == {"DELIVERED": 1}   # the EA already has it
    assert adapter.poll(pending_v6_orders=1)["command"] == "CANCEL_PENDING"
    cancelled = execution(delivered["intent_id"], "cancelled", 777,
                          int(adapter.clock.now_epoch())) | {"reason_code": "COMMAND"}
    assert adapter.ea_post("/v6/execution", cancelled).json() == {"ok": True}
    row = adapter.intent(delivered["intent_id"])
    assert (row["status"], row["report_reason"]) == ("CANCELLED", "COMMAND")

    after = op(adapter, "wait", "--agent", AGENT, "--timeout", "5", "--out", str(packet_file))
    assert after.code == cli.EXIT_NO_SESSION
    outputs = (waited, stopped, after)
    assert all(TOKEN not in result.text for result in outputs)


def test_start_reports_why_an_execute_session_is_not_armed(adapter: Adapter) -> None:
    adapter.backfill()
    adapter.poll()
    adapter.clock.advance(adapter.container.settings.ea_stale_s + 1)
    started = op(adapter, "session", "start")
    body = started.out_json()
    assert started.code == cli.EXIT_OK, started.text
    assert (body["armed"], body["arm_reason"], body["refusal"]) == (False, "EA_STALE", None)
    assert body["arm_detail"].startswith("the newest EA poll is")
