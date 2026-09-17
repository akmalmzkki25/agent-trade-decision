"""v6_intents through LedgerCycles.intents: invariants, lifecycle and lookups."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any, Final

import pytest

from app.v6.ledger_cycles import LedgerCycles
from app.v6.ledger_intents import (
    ActiveIntentExists, DuplicateIntent, IntentRecord, IntentStore, IntentUpdate, NewIntent,
    UnknownIntent,
)
from app.v6.runtime.intent_states import IllegalIntentTransition

T0: Final[float] = 1_789_565_408.0
FIRST: Final[str] = "k7w2m4pq3xza"
SECOND: Final[str] = "b5n6r7t2vw3y"


def new_intent(intent_id: str = FIRST, **changes: Any) -> NewIntent:
    fields: dict[str, Any] = dict(
        intent_id=intent_id, cycle_id="c-0123456789abcdef", session_id="a1b2c3d4e5f6",
        agent="claude_code", source="operator", side="buy", order_type="BUY_LIMIT",
        entry=4535.07, sl=4528.07, tp=4549.07, lots=0.01, risk_usd=7.4,
        valid_until_epoch=int(T0) + 120, pending_expiry_epoch=int(T0) + 1800,
        time_barrier_s=7200, created_at=T0)
    return NewIntent(**{**fields, **changes})


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[LedgerCycles]:
    led = LedgerCycles(tmp_path / "intents.db")
    yield led
    led.close()


@pytest.fixture
def store(ledger: LedgerCycles) -> IntentStore:
    return ledger.intents


# --- NewIntent -----------------------------------------------------------------------
@pytest.mark.parametrize(("changes", "message"), [
    ({"intent_id": "K7W2M4PQ3XZA"}, "intent_id"),
    ({"cycle_id": ""}, "cycle_id"),
    ({"session_id": "s" * 65}, "session_id"),
    ({"source": "claude_code"}, "unknown source"),
    ({"agent": "gpt"}, "agent does not match"),
    ({"agent": ""}, "agent does not match"),
    ({"source": "rules"}, "agent does not match"),
    ({"order_type": "SELL_LIMIT"}, "order_type"),
    ({"sl": 4540.0}, "own side"),
    ({"lots": 0.02}, "execution cap"),
    ({"lots": 0.0}, "finite and positive"),
    ({"risk_usd": -1.0}, "must not be negative"),
    ({"valid_until_epoch": int(T0)}, "after created_at"),
    ({"time_barrier_s": 14_401}, "time barrier"),
    ({"pending_expiry_epoch": int(T0) + 150}, "at least 60 s"),
    ({"entry": float("nan")}, "finite numbers"),
    ({"created_at": None}, "finite numbers"),
    ({"lots": True}, "finite numbers"),
])
def test_new_intent_invariants(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        new_intent(**changes)


def test_rules_intents_carry_no_agent() -> None:
    intent = new_intent(source="rules", agent="")

    assert (intent.source, intent.agent) == ("rules", "")
    with pytest.raises(FrozenInstanceError):
        intent.lots = 0.02  # type: ignore[misc]


# --- insert and lookups ------------------------------------------------------------------
def test_insert_publishes_and_reads_back(store: IntentStore) -> None:
    stored = store.insert(new_intent())

    assert stored.status == "PUBLISHED" and stored.active
    assert store.get(FIRST) == stored
    assert store.active_intent() == stored
    assert store.list_recent() == (stored,)
    assert stored.to_dict()["active"] is True
    assert (stored.delivered_at, stored.ticket, stored.basket_id) == (None, None, None)
    assert store.get("zzzzzzzzzzzz") is None


def test_one_active_intent_at_a_time(store: IntentStore) -> None:
    store.insert(new_intent())

    with pytest.raises(ActiveIntentExists) as caught:
        store.insert(new_intent(SECOND, created_at=T0 + 1))
    assert caught.value.active_intent_id == FIRST
    with pytest.raises(DuplicateIntent):
        store.insert(new_intent())
    store.transition(FIRST, "EXPIRED", at=T0 + 121)
    second = store.insert(new_intent(SECOND, created_at=T0 + 200,
                                     valid_until_epoch=int(T0) + 320))
    assert store.active_intent() == second
    assert [r.intent_id for r in store.list_recent(limit=1)] == [SECOND]


def test_the_database_itself_refuses_a_second_active_intent(store: IntentStore,
                                                            ledger: LedgerCycles) -> None:
    store.insert(new_intent())

    with sqlite3.connect(ledger.path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO v6_intents (intent_id, cycle_id, session_id, agent, source, status,"
            " side, order_type, entry, sl, tp, lots, risk_usd, valid_until_epoch,"
            " pending_expiry_epoch, time_barrier_s, created_at) VALUES (?, 'c', 's', '',"
            " 'rules', 'DELIVERED', 'buy', 'BUY', 1, 0.5, 2, 0.01, 1, 2, 0, 60, 1)", (SECOND,))


# --- lifecycle -----------------------------------------------------------------------------
def test_a_full_limit_order_lifecycle(store: IntentStore) -> None:
    store.insert(new_intent())
    delivered = store.transition(FIRST, "DELIVERED", at=T0 + 2)
    again = store.transition(FIRST, "DELIVERED", at=T0 + 4)
    reported = store.transition(FIRST, "REPORTED", at=T0 + 3, update=IntentUpdate(
        report_status="placed", report_reason="NONE", ticket=5012345702))
    filled = store.transition(FIRST, "FILLED", at=T0 + 600, update=IntentUpdate(
        report_status="filled", ticket=5012345999, fill_price=4535.07))
    closed = store.transition(FIRST, "CLOSED", at=T0 + 4000, update=IntentUpdate(
        outcome_pnl=-7.4, basket_id=f"XAUUSD-V6B-{FIRST}"))

    assert again == delivered and delivered.delivered_at == T0 + 2
    assert (reported.reported_at, reported.ticket) == (T0 + 3, 5012345702)
    assert (filled.reported_at, filled.ticket, filled.report_status) == (
        T0 + 3, 5012345999, "filled")
    assert (closed.status, closed.closed_at, closed.outcome_pnl) == ("CLOSED", T0 + 4000, -7.4)
    assert closed.fill_price == 4535.07 and not closed.active
    assert store.get(FIRST) == closed
    assert store.active_intent() is None
    assert store.by_ticket(5012345999) == closed and store.by_ticket(1) is None
    assert store.by_basket(f"XAUUSD-V6B-{FIRST}") == closed
    assert store.by_basket("XAUUSD-V6B-zzzzzzzzzzzz") is None


def test_a_rejection_before_delivery_closes_the_intent(store: IntentStore) -> None:
    store.insert(new_intent())

    rejected = store.transition(FIRST, "REJECTED", at=T0 + 1, update=IntentUpdate(
        report_reason="POLICY_OPERATOR_DEMO_ONLY"))

    assert (rejected.status, rejected.closed_at, rejected.reported_at) == (
        "REJECTED", T0 + 1, None)
    assert rejected.report_reason == "POLICY_OPERATOR_DEMO_ONLY"


def test_illegal_and_unknown_transitions_change_nothing(store: IntentStore) -> None:
    store.insert(new_intent())
    store.transition(FIRST, "CANCELLED", at=T0 + 5, update=IntentUpdate(
        report_reason="SESSION_STOP"))

    with pytest.raises(IllegalIntentTransition):
        store.transition(FIRST, "FILLED", at=T0 + 6)
    with pytest.raises(IllegalIntentTransition):
        store.transition(FIRST, "PUBLISHED", at=T0 + 6)
    with pytest.raises(UnknownIntent):
        store.transition(SECOND, "DELIVERED", at=T0 + 6)
    stored = store.get(FIRST)
    assert (stored.status, stored.closed_at, stored.report_reason) == (
        "CANCELLED", T0 + 5, "SESSION_STOP")


@pytest.mark.parametrize(("target", "at"), [
    ("SENT", T0), ("DELIVERED", float("nan")), ("DELIVERED", -1.0), ("DELIVERED", None),
])
def test_transition_arguments_are_checked(store: IntentStore, target: Any, at: Any) -> None:
    store.insert(new_intent())

    with pytest.raises(ValueError):
        store.transition(FIRST, target, at=at)


@pytest.mark.parametrize("changes", [
    {"ticket": 0}, {"ticket": True}, {"ticket": 1.5}, {"fill_price": float("inf")},
    {"outcome_pnl": "1"}, {"report_status": ""}, {"report_reason": "r" * 65},
    {"basket_id": 7},
])
def test_intent_update_is_validated(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="invalid intent update"):
        IntentUpdate(**changes)


def test_intent_update_values_skip_unset_fields() -> None:
    update = IntentUpdate(ticket=5, outcome_pnl=0.0)

    assert dict(update.values()) == {"ticket": 5, "outcome_pnl": 0.0}


def test_status_counts_and_limits(store: IntentStore) -> None:
    store.insert(new_intent())
    store.transition(FIRST, "EXPIRED", at=T0 + 121)
    store.insert(new_intent(SECOND, created_at=T0 + 900, valid_until_epoch=int(T0) + 1020,
                            pending_expiry_epoch=int(T0) + 2700))

    assert dict(store.status_counts(T0, T0 + 1000)) == {"EXPIRED": 1, "PUBLISHED": 1}
    assert dict(store.status_counts(T0 + 1, T0 + 900)) == {}
    with pytest.raises(ValueError):
        store.list_recent(limit=0)


def test_intents_survive_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "restart.db"
    first = LedgerCycles(path)
    try:
        stored = first.intents.insert(new_intent())
        first.intents.transition(FIRST, "DELIVERED", at=T0 + 1)
    finally:
        first.close()
    reopened = LedgerCycles(path)
    try:
        assert reopened.intents.get(FIRST) == replace(stored, status="DELIVERED",
                                                      delivered_at=T0 + 1)
        assert isinstance(reopened.intents.active_intent(), IntentRecord)
    finally:
        reopened.close()
