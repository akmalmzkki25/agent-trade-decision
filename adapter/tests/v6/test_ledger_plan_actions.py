"""v6_intents plan columns and the v6_actions ledger."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterator

import pytest

from app.v6.ledger_actions import ActionRow
from app.v6.ledger_cycles import LedgerCycles
from app.v6.ledger_intents import NewIntent

T0 = 1_789_650_900.0
INTENT_ID = "k7w2m4pq3xza"
ACTION_ID = "m3a7q2z5k6pw"


def new_intent(**changes: Any) -> NewIntent:
    fields: dict[str, Any] = dict(
        intent_id=INTENT_ID, cycle_id="c-0123456789abcdef", session_id="a1b2c3d4e5f6",
        agent="claude_code", source="operator", side="buy", order_type="BUY_LIMIT",
        entry=4360.5, sl=4353.5, tp=4374.5, lots=0.01, risk_usd=7.4,
        valid_until_epoch=int(T0) + 120, pending_expiry_epoch=int(T0) + 1800,
        time_barrier_s=9000, created_at=T0, tp1=4366.0, tp2=4371.0, sl_after_tp1=4361.0,
        sl_after_tp2=4366.0)
    return NewIntent(**{**fields, **changes})


def action_row(**changes: Any) -> ActionRow:
    fields: dict[str, Any] = dict(
        action_id=ACTION_ID, cycle_id="c-0123456789abcdef", session_id="a1b2c3d4e5f6",
        agent="claude_code", command="MODIFY_POSITION", ticket=91, intent_id=INTENT_ID,
        payload={"sl": 4361.0, "tp": 4378.0}, status="PUBLISHED", detail="",
        created_at=T0, updated_at=T0)
    return ActionRow(**{**fields, **changes})


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[LedgerCycles]:
    store = LedgerCycles(tmp_path / "v6.sqlite")
    yield store
    store.close()


def test_plan_columns_round_trip(ledger: LedgerCycles) -> None:
    record = ledger.intents.insert(new_intent())
    assert (record.tp1, record.tp2, record.sl_after_tp1, record.sl_after_tp2,
            record.plan_step) == (4366.0, 4371.0, 4361.0, 4366.0, 0)
    assert ledger.intents.update_plan(INTENT_ID, tp1=4367.0, tp2=4372.0,
                                      sl_after_tp1=4362.0, sl_after_tp2=0.0,
                                      time_barrier_s=10800)
    assert ledger.intents.set_plan_step(INTENT_ID, 1)
    assert not ledger.intents.set_plan_step(INTENT_ID, 1)
    stored = ledger.intents.get(INTENT_ID)
    assert stored is not None
    assert (stored.tp1, stored.sl_after_tp2, stored.time_barrier_s, stored.plan_step) == (
        4367.0, 0.0, 10800, 1)
    assert not ledger.intents.update_plan("zzzzzzzzzzzz", tp1=1.0, tp2=2.0,
                                          sl_after_tp1=0.0, sl_after_tp2=0.0,
                                          time_barrier_s=3600)


def test_a_broken_ladder_is_refused_at_insert() -> None:
    with pytest.raises(ValueError, match="ladder"):
        new_intent(tp1=4380.0)


def test_an_old_database_gains_the_columns(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE v6_intents (
        intent_id TEXT NOT NULL PRIMARY KEY, cycle_id TEXT NOT NULL,
        session_id TEXT NOT NULL, agent TEXT NOT NULL DEFAULT '', source TEXT NOT NULL,
        status TEXT NOT NULL, side TEXT NOT NULL, order_type TEXT NOT NULL,
        entry REAL NOT NULL, sl REAL NOT NULL, tp REAL NOT NULL, lots REAL NOT NULL,
        risk_usd REAL NOT NULL, valid_until_epoch INTEGER NOT NULL,
        pending_expiry_epoch INTEGER NOT NULL, time_barrier_s INTEGER NOT NULL,
        created_at REAL NOT NULL, delivered_at REAL, reported_at REAL, closed_at REAL,
        report_status TEXT, report_reason TEXT, ticket INTEGER, fill_price REAL,
        outcome_pnl REAL, basket_id TEXT)""")
    conn.commit()
    conn.close()
    store = LedgerCycles(path)
    try:
        assert store.intents.insert(new_intent()).tp1 == 4366.0
    finally:
        store.close()


def test_actions(ledger: LedgerCycles) -> None:
    ledger.actions.insert(action_row())
    assert ledger.actions.mark(ACTION_ID, "APPLIED", "retcode 10009", T0 + 3)
    assert not ledger.actions.mark(ACTION_ID, "FAILED", "late", T0 + 4)
    row = ledger.actions.get(ACTION_ID)
    assert row is not None
    assert (row.status, row.detail, row.payload["sl"]) == ("APPLIED", "retcode 10009", 4361.0)
    latest = ledger.actions.latest("a1b2c3d4e5f6")
    assert latest is not None and latest.action_id == ACTION_ID
    assert ledger.actions.latest("ffffffffffff") is None
    assert len(ledger.actions.recent(10)) == 1
    assert ledger.actions.record_step(INTENT_ID, 91, 1, 4353.5, 4361.0, 4366.1, T0 + 9)
    assert not ledger.actions.record_step(INTENT_ID, 91, 1, 4353.5, 4361.0, 4366.1, T0)


def test_an_unknown_action_cannot_be_marked(ledger: LedgerCycles) -> None:
    assert not ledger.actions.mark("zzzzzzzzzzzz", "APPLIED", "", T0)
    with pytest.raises(ValueError, match="final"):
        ledger.actions.mark(ACTION_ID, "PUBLISHED", "", T0)


@pytest.mark.parametrize("changes", [
    {"status": "DONE"}, {"command": "FLATTEN"}, {"ticket": 0}, {"action_id": "x"},
    {"detail": "x" * 200}, {"created_at": float("inf")},
])
def test_bad_action_rows(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        action_row(**changes)
