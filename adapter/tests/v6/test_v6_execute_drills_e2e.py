"""
Phase 5 drills end to end (plan section 12): the real app in execute mode.

  HALT       the session disarms, the undelivered intent is cancelled, every poll says
             CANCEL_PENDING; resume removes the sentinel and re-arms
  stop       a delivered intent is left to the EA's reports, so a fill that raced the
             stop still closes with its P&L
  breaker    equity 3.5% down: FLATTEN first, then CANCEL_PENDING while tripped; the
             session disarms and neither a new session start nor a resume is allowed
  non-DEMO   a REAL poll gets no intent; the watchdog disarms and cancels; the operator
             API refuses with 403
  failures   bookkeeping that fails never fails the EA's request (logged instead), and
             the V1 basket route refuses V6 results (they arrive signed on /v6)
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.deps import ledger as v1_ledger
from app.routes.events import V6_ROUTE_DETAIL
from app.v6.clock import FakeClock
from app.v6.ledger_intents import UnknownIntent
from app.v6.runtime.intent_states import IllegalIntentTransition
from app.v6.schemas.intent import basket_id_for

from . import engine_fixtures_v6 as ef
from .execute_fixtures_v6 import (
    ENTRY, Adapter, armed_session, basket_result, execute_settings, execution, fixed_candidate,
    published, running,
)

UNLINKED_ID = "abcdefgh2345"
LIVE = {"trade_mode": "REAL", "server": "Broker-Live"}


@pytest.fixture
def adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Adapter]:
    fixed_candidate(monkeypatch)
    clock = FakeClock(epoch=float(ef.AS_OF + 1))
    with running(tmp_path, execute_settings(tmp_path), clock) as live:
        yield live


def delivered(adapter: Adapter) -> str:
    _, intent_id = published(adapter)
    assert adapter.poll()["intent_id"] == intent_id
    return intent_id


def watchdog_tick(adapter: Adapter) -> Any:
    return adapter.run(adapter.container.parts.watchdog.tick)


def status(adapter: Adapter) -> dict[str, Any]:
    return adapter.client.get("/v6/status").json()


# --- kill switch -----------------------------------------------------------------------
def test_halt_disarms_and_cancels_and_resume_rearms(adapter: Adapter) -> None:
    _, intent_id = published(adapter)
    halted = adapter.operator_post("/v6/control/halt", {"reason": "drill"})
    assert (halted.status_code, halted.json()["halted"]) == (200, True)
    row = adapter.intent(intent_id)
    assert (row["status"], row["report_reason"]) == ("CANCELLED", "HALTED")
    body = status(adapter)
    assert (body["armed"], body["session"]["disarm_reason"], body["halt_file_present"]) == (
        False, "HALTED", True)

    replies = [adapter.poll(pending_v6_orders=1), adapter.poll(), adapter.poll()]
    assert [reply["command"] for reply in replies] == ["CANCEL_PENDING"] * 3
    assert not any(reply["has_intent"] for reply in replies)

    resumed = adapter.operator_post("/v6/control/resume", {"reason": "drill_done"})
    body = resumed.json()
    assert (resumed.status_code, body["halted"], body["removed"], body["armed"]) == (
        200, False, True, True)
    assert body["arm"]["reason"] == "ARMED"
    assert adapter.poll()["command"] == "NONE" and status(adapter)["armed"] is True


def test_a_fill_that_raced_the_session_stop_still_closes_its_intent(adapter: Adapter) -> None:
    intent_id = delivered(adapter)
    stopped = adapter.operator_post("/v6/control/session",
                                    {"action": "stop", "reason": "sudah_cukup"})
    assert stopped.status_code == 200, stopped.text
    assert adapter.intent(intent_id)["status"] == "DELIVERED"
    now = int(adapter.clock.now_epoch())
    filled = execution(intent_id, "filled", 777, now, fill_price=ENTRY)
    assert adapter.ea_post("/v6/execution", filled).json() == {"ok": True}
    closed = basket_result(intent_id, "2026-09-17T13:30:00Z", -7.5)
    assert adapter.ea_post("/v6/basket-result", closed).json() == {"ok": True}
    row = adapter.intent(intent_id)
    assert (row["status"], row["outcome_pnl"]) == ("CLOSED", -7.5)


# --- breaker -----------------------------------------------------------------------------
def test_a_breaker_trip_flattens_disarms_and_blocks_the_session(adapter: Adapter) -> None:
    _, intent_id = published(adapter)
    losing = adapter.poll(equity=1930.0, floating_pnl_v6=-70.0)
    assert losing["has_intent"] is False                  # the poll itself checks the breakers
    state = watchdog_tick(adapter)
    assert state.breakers_tripped
    row = adapter.intent(intent_id)
    assert (row["status"], row["report_reason"]) == ("CANCELLED", "BREAKER")
    body = status(adapter)
    assert (body["armed"], body["session"]["disarm_reason"]) == (False, "BREAKER")
    assert body["runtime"]["breakers"] == ["daily:2026-09-17"]

    commands = [adapter.poll(equity=1930.0, open_v6_positions=1)["command"],
                adapter.poll(equity=1930.0)["command"], adapter.poll(equity=1930.0)["command"]]
    assert commands == ["FLATTEN", "CANCEL_PENDING", "CANCEL_PENDING"]

    restart = adapter.operator_post("/v6/control/session", {"action": "start"})
    assert (restart.status_code, restart.json()["refusal"]) == (409, "APP-V6-SESSION-BREAKER")
    resumed = adapter.operator_post("/v6/control/resume", {})
    assert (resumed.status_code, resumed.json()["refusal"]) == (409, "APP-V6-RESUME-BREAKER")
    assert status(adapter)["armed"] is False


# --- demo only ---------------------------------------------------------------------------
def test_a_real_account_poll_never_gets_the_intent_and_disarms(adapter: Adapter) -> None:
    _, intent_id = published(adapter)
    assert adapter.poll(**LIVE)["has_intent"] is False
    watchdog_tick(adapter)
    row = adapter.intent(intent_id)
    assert (row["status"], row["report_reason"]) == ("CANCELLED", "NOT_DEMO")
    body = status(adapter)
    assert (body["armed"], body["session"]["disarm_reason"], body["trade_mode"]) == (
        False, "NOT_DEMO", "REAL")
    assert adapter.poll(**LIVE)["command"] == "CANCEL_PENDING"
    waited = adapter.operator_post("/v6/operator/wait", {"timeout_s": 0, "agent": "codex"})
    assert (waited.status_code, waited.json()["policy"]) == (403, "POLICY_OPERATOR_DEMO_ONLY")
    restart = adapter.operator_post("/v6/control/session", {"action": "start"})
    assert (restart.status_code, restart.json()["refusal"]) == (409, "APP-V6-SESSION-NOT-DEMO")


# --- failures that must not fail the EA --------------------------------------------------
def _locked(*_args: Any, **_kwargs: Any) -> Any:
    raise sqlite3.OperationalError("database is locked")


def test_halt_holds_even_when_the_disarm_fails(
        adapter: Adapter, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    armed_session(adapter)

    async def broken_disarm(*args: Any, **kwargs: Any) -> tuple[str, ...]:
        return _locked(*args, **kwargs)

    monkeypatch.setattr(adapter.container.parts.desk, "disarm", broken_disarm)
    with caplog.at_level(logging.ERROR, logger="app.routes.v6_control"):
        halted = adapter.operator_post("/v6/control/halt", {})
    assert (halted.status_code, halted.json()["created"]) == (200, True)
    assert "HALT: disarm failed (OperationalError)" in caplog.text
    assert adapter.poll()["command"] == "CANCEL_PENDING"


def test_a_failed_reconciliation_keeps_the_snapshot(
        adapter: Adapter, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    adapter.backfill()
    monkeypatch.setattr(adapter.container.parts.intent_book, "reconcile_with", _locked)
    with caplog.at_level(logging.ERROR, logger="app.routes.v6_ea"):
        cycle_id = adapter.snapshot("snap-reconcile-1")
    assert "intent reconciliation failed (OperationalError)" in caplog.text
    assert adapter.cycle(cycle_id)[:2] == ("HOLD", "APP-V6-NO-SESSION")


def test_a_report_that_cannot_be_applied_is_still_stored(
        adapter: Adapter, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    def racing(*_args: Any, **_kwargs: Any) -> Any:
        raise IllegalIntentTransition("REPORTED", "PUBLISHED")

    monkeypatch.setattr(adapter.container.parts.intent_book, "apply_execution", racing)
    report = execution(UNLINKED_ID, "filled", 777, int(adapter.clock.now_epoch()),
                       fill_price=ENTRY)
    with caplog.at_level(logging.ERROR, logger="app.routes.v6_ea"):
        assert adapter.ea_post("/v6/execution", report).json() == {"ok": True}
    assert "not applied (IllegalIntentTransition)" in caplog.text
    assert adapter.rows("SELECT intent_id, status FROM v6_executions") == [
        (UNLINKED_ID, "filled")]


def test_a_result_that_cannot_close_its_intent_is_still_stored(
        adapter: Adapter, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    def gone(**_kwargs: Any) -> Any:
        raise UnknownIntent(UNLINKED_ID)

    monkeypatch.setattr(adapter.container.parts.intent_book, "close_from_basket", gone)
    result = basket_result(UNLINKED_ID, "2026-09-17T13:30:00Z", -5.0)
    with caplog.at_level(logging.ERROR, logger="app.routes.v6_ea"):
        assert adapter.ea_post("/v6/basket-result", result).json() == {"ok": True}
    assert "not linked (UnknownIntent)" in caplog.text
    assert adapter.rows("SELECT basket_id, net_pnl FROM basket_results") == [
        (basket_id_for("XAUUSD", UNLINKED_ID), -5.0)]


def test_the_v1_basket_route_refuses_v6_results(adapter: Adapter) -> None:
    result = basket_result(UNLINKED_ID, "2026-09-17T13:30:00Z", 5.0)
    response = adapter.client.post("/v1/events/basket-result", json=result)
    assert (response.status_code, response.json()["detail"]) == (400, V6_ROUTE_DETAIL)
    stored = v1_ledger.conn.execute("SELECT COUNT(*) FROM basket_results WHERE basket_id = ?",
                                    (result["basket_id"],)).fetchone()
    assert stored == (0,)
    v5 = adapter.client.post("/v1/events/basket-result",
                             json=result | {"version": "v5", "basket_id": "XAUUSD-V5B-drill"})
    assert v5.status_code == 200
    v1_ledger.conn.execute("DELETE FROM basket_results WHERE basket_id = 'XAUUSD-V5B-drill'")


def test_the_v1_basket_route_refuses_a_v6_basket_id_under_any_version(
        adapter: Adapter) -> None:
    signed = basket_result(UNLINKED_ID, "2026-09-17T13:30:00Z", -5.0)
    forged = signed | {"version": "v5", "net_pnl": 0.0, "gross_loss": 0.0}
    before = adapter.client.post("/v1/events/basket-result", json=forged)
    assert (before.status_code, before.json()["detail"]) == (400, V6_ROUTE_DETAIL)
    assert adapter.ea_post("/v6/basket-result", signed).json() == {"ok": True}
    after = adapter.client.post("/v1/events/basket-result", json=forged)
    assert after.status_code == 400
    assert adapter.rows("SELECT version, net_pnl FROM basket_results WHERE basket_id = ?",
                        (signed["basket_id"],)) == [("v6", -5.0)]
