"""
Execute mode end to end (plan sections 3.3, 4b, 5 and 6; user decisions 2026-09-16).

"Mulai trading skrg": backfill, a DEMO poll, and the session starts ARMED; a snapshot
with a sized candidate becomes an operator packet; codex decides ENTER; the signed poll
delivers the intent; the EA reports placed and filled; the V6 basket result closes it,
and the outcome and the realised P&L show. "Sudah cukup hari ini": CANCEL_PENDING once,
the undelivered intent is cancelled, and nothing is flattened.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.v6.clock import FakeClock
from app.v6.runtime.realised_pnl import OutcomeReader
from app.v6.schemas.intent import basket_id_for

from . import engine_fixtures_v6 as ef
from .execute_fixtures_v6 import (
    ENTRY, STOP, TARGET, Adapter, basket_result, execute_settings, execution, fixed_candidate,
    published, running,
)

R_MULTIPLE = 16.0 / 8.4
OPERATOR_ROLES = ["price_action", "news_risk", "liquidity", "structure", "chief"]


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(epoch=float(ef.AS_OF + 1))


@pytest.fixture
def adapter(tmp_path: Path, clock: FakeClock,
            monkeypatch: pytest.MonkeyPatch) -> Iterator[Adapter]:
    fixed_candidate(monkeypatch)
    with running(tmp_path, execute_settings(tmp_path), clock) as live:
        yield live


def _check_publication(adapter: Adapter, cycle_id: str, intent_id: str) -> None:
    row = adapter.intent(intent_id)
    assert (row["status"], row["agent"], row["source"], row["order_type"]) == (
        "PUBLISHED", "codex", "operator", "BUY_LIMIT")
    assert (row["entry"], row["sl"], row["tp"], row["lots"], row["risk_usd"]) == (
        ENTRY, STOP, TARGET, 0.01, 8.4)
    views = adapter.rows("SELECT role, model FROM v6_agent_views WHERE cycle_id = ?"
                         " AND source = 'operator' ORDER BY id", (cycle_id,))
    assert views == [(role, "codex") for role in OPERATOR_ROLES]
    (summary,) = adapter.rows("SELECT summary_json FROM v6_cycles WHERE cycle_id = ?",
                              (cycle_id,))[0]
    assert json.loads(summary)["intent_id"] == intent_id


def _check_delivery(adapter: Adapter, intent_id: str) -> None:
    delivered = adapter.poll()
    assert (delivered["has_intent"], delivered["intent_id"], delivered["command"]) == (
        True, intent_id, "NONE")
    assert (delivered["order_type"], delivered["entry"], delivered["sl"], delivered["tp"],
            delivered["lots"], delivered["ref_price"]) == (
        "BUY_LIMIT", ENTRY, STOP, TARGET, 0.01, 4300.2)
    assert (delivered["require_demo"], delivered["source"], delivered["magic"]) == (
        1, "operator", 250570)
    first = adapter.intent(intent_id)
    assert first["status"] == "DELIVERED"
    assert adapter.poll()["intent_id"] == intent_id   # re-delivered until the EA reports
    assert adapter.intent(intent_id)["delivered_at"] == first["delivered_at"]


def _check_fill(adapter: Adapter, intent_id: str) -> None:
    now = int(adapter.clock.now_epoch())
    placed = execution(intent_id, "placed", 777, now)
    assert adapter.ea_post("/v6/execution", placed).status_code == 200
    assert adapter.intent(intent_id)["status"] == "REPORTED"
    assert adapter.poll(pending_v6_orders=1, last_intent_id=intent_id)["has_intent"] is False
    filled = execution(intent_id, "filled", 777, now + 60, fill_price=ENTRY)
    for _ in range(2):                    # the outbox may resend a report
        assert adapter.ea_post("/v6/execution", filled).json() == {"ok": True}
    row = adapter.intent(intent_id)
    assert (row["status"], row["ticket"], row["fill_price"]) == ("FILLED", 777, ENTRY)
    assert adapter.rows("SELECT COUNT(*) FROM v6_executions") == [(2,)]


def _check_close(adapter: Adapter, intent_id: str) -> None:
    moment = datetime.fromtimestamp(adapter.clock.now_epoch(), timezone.utc)
    closed_at = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    basket_id = basket_id_for("XAUUSD", intent_id)
    for _ in range(2):                    # stored once, closed once
        response = adapter.ea_post("/v6/basket-result", basket_result(intent_id, closed_at,
                                                                      16.0))
        assert response.json() == {"ok": True}
    row = adapter.intent(intent_id)
    assert (row["status"], row["outcome_pnl"], row["basket_id"]) == ("CLOSED", 16.0, basket_id)
    assert adapter.rows("SELECT version, net_pnl FROM basket_results") == [("v6", 16.0)]
    outcome = OutcomeReader(adapter.db).for_basket(basket_id)
    assert outcome is not None and outcome.intent is not None
    assert (outcome.intent.intent_id, outcome.login, outcome.kind) == (intent_id, "12345", "win")
    assert float(outcome.r_multiple) == pytest.approx(R_MULTIPLE)


def test_a_demo_trade_from_the_decision_to_the_realised_pnl(adapter: Adapter) -> None:
    cycle_id, intent_id = published(adapter)
    _check_publication(adapter, cycle_id, intent_id)
    _check_delivery(adapter, intent_id)
    _check_fill(adapter, intent_id)
    _check_close(adapter, intent_id)

    overview = adapter.client.get("/v6/api/overview").json()
    daily = overview["outcomes"]["realised"]["periods"]["daily"]
    assert (daily["realized"], daily["trades"]) == (16.0, 1)
    assert overview["outcomes"]["stats"]["wins"] == 1
    assert (overview["intents"][0]["intent_id"], overview["intents"][0]["r_multiple"]) == (
        intent_id, round(R_MULTIPLE, 3))
    assert overview["session"]["armed"] is True
    assert overview["session"]["operator"]["last_agent"] == "codex"
    status = adapter.client.get("/v6/status").json()
    assert (status["armed"], status["active_intent"]) == (True, None)
    assert status["last_cycle"]["intent_id"] == intent_id
    assert status["operator"]["pending_cycle_id"] is None
    assert (status["mode"], status["ea_signing"], status["server"]) == (
        "execute", "required", "Broker-Demo")


def test_the_status_shows_the_active_intent_and_the_signing_mode(adapter: Adapter) -> None:
    _, intent_id = published(adapter)
    status = adapter.client.get("/v6/status").json()
    assert status["active_intent"]["intent_id"] == intent_id
    assert status["active_intent"]["status"] == "PUBLISHED"
    assert status["session"]["armed"] is True


def test_stopping_the_session_cancels_the_order_and_never_flattens(adapter: Adapter) -> None:
    _, intent_id = published(adapter)
    assert adapter.poll()["intent_id"] == intent_id           # DELIVERED

    stopped = adapter.operator_post("/v6/control/session", {"action": "stop",
                                                            "reason": "cukup"})
    body = stopped.json()
    assert stopped.status_code == 200 and body["stopped"] is True
    # The EA already has the intent: its CANCEL_PENDING report closes it, not the stop.
    assert (body["command"]["command"], body["cancelled_intents"]) == ("CANCEL_PENDING", [])
    assert (body["session"]["armed"], body["session"]["disarm_reason"]) == (False, "cukup")
    assert body["summary"]["intent_statuses"] == {"DELIVERED": 1}
    assert adapter.intent(intent_id)["status"] == "DELIVERED"

    replies = [adapter.poll(pending_v6_orders=1), adapter.poll(), adapter.poll()]
    assert [reply["command"] for reply in replies] == ["CANCEL_PENDING", "NONE", "NONE"]
    assert not any(reply["has_intent"] for reply in replies)
    cancelled = execution(intent_id, "cancelled", 777, int(adapter.clock.now_epoch())) | {
        "reason_code": "COMMAND"}
    assert adapter.ea_post("/v6/execution", cancelled).json() == {"ok": True}
    row = adapter.intent(intent_id)
    assert (row["status"], row["report_reason"]) == ("CANCELLED", "COMMAND")
    session = adapter.client.get("/v6/status").json()
    assert (session["session"], session["armed"], session["active_intent"]) == (None, False, None)
