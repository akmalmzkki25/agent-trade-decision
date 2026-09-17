"""runtime.intent_book: publishing, serving and closing the one active V6 intent."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final
from unittest.mock import Mock

import pytest

from app.v6.ledger_cycles import LedgerCycles
from app.v6.ledger_intents import IntentStore, NewIntent
from app.v6.risk.intent_builder import IntentDraft
from app.v6.runtime.arming import ArmDecision
from app.v6.runtime import intent_book as ib
from app.v6.runtime.intent_book import IntentBook, Occupancy, walk_for
from app.v6.runtime.intent_states import IllegalIntentTransition
from app.v6.runtime.reconcile import Exposure
from app.v6.schemas.intent import ExecutionReport
from app.v6.schemas.snapshot import PendingOrderBlock, PositionBlock

from .cycle_fixtures_v6 import market_context
from .payloads_v6 import as_poll, poll_payload

T0: Final[float] = 1_789_565_408.0
FIRST: Final[str] = "k7w2m4pq3xza"
SECOND: Final[str] = "b5n6r7t2vw3y"
VALID_UNTIL: Final[int] = int(T0) + 120
PENDING_EXPIRY: Final[int] = int(T0) + 1800
FREE: Final[Occupancy] = Occupancy(open_positions=0, pending_orders=0)
MAGIC: Final[int] = 250570
BASKET: Final[str] = f"XAUUSD-V6B-{FIRST}"


def draft(intent_id: str = FIRST, cycle_id: str = "c-0000000000000001",
          **row_changes: Any) -> IntentDraft:
    row: dict[str, Any] = dict(
        intent_id=intent_id, cycle_id=cycle_id, session_id="a1b2c3d4e5f6",
        agent="claude_code", source="operator", side="buy", order_type="BUY_LIMIT",
        entry=4535.07, sl=4528.07, tp=4549.07, lots=0.01, risk_usd=7.4,
        valid_until_epoch=VALID_UNTIL, pending_expiry_epoch=PENDING_EXPIRY,
        time_barrier_s=7200, created_at=T0)
    return IntentDraft(row=NewIntent(**{**row, **row_changes}), candidate_id="cand-1",
                       ref_price=4535.35, max_drift_points=140, max_spread_points=35, magic=MAGIC)


def report(status: str, intent_id: str = FIRST, **changes: Any) -> ExecutionReport:
    accepted = status in {"placed", "filled"}
    fields: dict[str, Any] = dict(
        schema_version="v6.execution.1", intent_id=intent_id, status=status,
        reason_code="NONE" if accepted else "EXPIRED", ticket=5012345702 if accepted else 0,
        retcode=10009, requested_price=4535.07, fill_price=4535.05 if status == "filled" else 0.0,
        slippage_points=0.0, spread_points=17, latency_ms=40, sent_at_epoch=int(T0) + 3)
    return ExecutionReport(**{**fields, **changes})


def position(ticket: int = 5012345999) -> PositionBlock:
    return PositionBlock(ticket=ticket, magic=MAGIC, side="buy", volume=0.01,
                         price_open=4535.05, sl=4528.07, tp=4549.07, profit=0.5, swap=0.0,
                         open_epoch=int(T0) + 300, comment=f"Q6:{FIRST}", mae_points=0.0,
                         mfe_points=0.0)


class Rigged:
    """The real store with some methods replaced (failures, races, lost rows)."""

    def __init__(self, store: IntentStore, **replaced: Any) -> None:
        self.store = store
        self.replaced = dict(replaced)

    def __getattr__(self, name: str) -> Any:
        return self.replaced.get(name, getattr(self.store, name))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[IntentStore]:
    ledger = LedgerCycles(tmp_path / "book.db")
    yield ledger.intents
    ledger.close()


@pytest.fixture
def book(store: IntentStore) -> IntentBook:
    return IntentBook(store)


def published(book: IntentBook, intent: IntentDraft | None = None) -> IntentDraft:
    intent = intent or draft()
    assert book.publish(intent, FREE, T0).code == ib.BOOK_PUBLISHED
    return intent


# --- publish and serve --------------------------------------------------------------------
def test_a_published_intent_is_delivered_once_and_served_until_reported(
        book: IntentBook, store: IntentStore) -> None:
    intent = published(book)

    assert store.get(FIRST).status == "PUBLISHED" and book.active().intent_id == FIRST
    assert book.next_for_poll(VALID_UNTIL) is None and book.next_for_poll(float("nan")) is None
    assert book.next_for_poll(T0 + 1, arm=ArmDecision(False, "HALTED", "halt file")) is None
    assert book.next_for_poll(T0 + 2, arm=ArmDecision(armed=True, reason="ARMED")) is intent
    assert book.next_for_poll(T0 + 4) is intent
    assert (store.get(FIRST).status, store.get(FIRST).delivered_at) == ("DELIVERED", T0 + 2)
    book.apply_execution(report("placed"), T0 + 5)
    assert book.next_for_poll(T0 + 6) is None


@pytest.mark.parametrize(("occupancy", "now", "code"), [
    (None, T0, ib.BOOK_OCCUPANCY_UNKNOWN),
    (Occupancy(1, 0), T0, ib.BOOK_OCCUPIED),
    (Occupancy(0, 1), T0, ib.BOOK_OCCUPIED),
    (Occupancy(-1, 0), T0, ib.BOOK_OCCUPIED),
    (FREE, VALID_UNTIL, ib.BOOK_TOO_LATE),
    (FREE, float("nan"), ib.BOOK_TOO_LATE),
])
def test_publish_needs_a_free_broker_and_a_valid_draft(
        book: IntentBook, store: IntentStore, occupancy: Occupancy | None, now: float,
        code: str) -> None:
    outcome = book.publish(draft(), occupancy, now)

    assert (outcome.ok, outcome.code, outcome.record) == (False, code, None)
    assert store.get(FIRST) is None and book.next_for_poll(T0) is None


def test_one_active_intent_one_id_and_one_intent_per_cycle(book: IntentBook) -> None:
    published(book)

    assert book.publish(draft(SECOND, "c-2"), FREE, T0).code == ib.BOOK_ACTIVE_INTENT
    book.apply_execution(report("rejected_local", reason_code="SPREAD"), T0 + 3)
    assert book.publish(draft(cycle_id="c-2"), FREE, T0 + 4).code == ib.BOOK_DUPLICATE
    assert book.publish(draft(SECOND), FREE, T0 + 4).code == ib.BOOK_CYCLE_USED
    assert book.publish(draft(SECOND, "c-2"), FREE, T0 + 4).ok


def test_an_intent_without_its_own_draft_is_never_served(
        store: IntentStore, caplog: pytest.LogCaptureFixture) -> None:
    first = IntentBook(store)
    published(first)
    restarted = IntentBook(store)

    with caplog.at_level(logging.WARNING, logger=ib.__name__):
        assert restarted.next_for_poll(T0 + 1) is None
        assert restarted.next_for_poll(T0 + 2) is None
        store.transition(FIRST, "EXPIRED", at=T0 + 3)
        store.insert(draft(SECOND, "c-2").row)
        assert first.next_for_poll(T0 + 4) is None  # it holds FIRST's draft, not SECOND's
    assert (store.get(FIRST).status, store.get(SECOND).status) == ("EXPIRED", "PUBLISHED")
    assert sum("cannot be served" in r.getMessage() for r in caplog.records) == 2


@pytest.mark.parametrize("replaced", [
    {"active_intent": Mock(side_effect=sqlite3.OperationalError("database is locked"))},
    {"transition": Mock(side_effect=sqlite3.OperationalError("disk I/O error"))},
    {"transition": Mock(side_effect=IllegalIntentTransition("CANCELLED", "DELIVERED"))},
])
def test_a_failed_delivery_write_serves_nothing(store: IntentStore,
                                                replaced: dict[str, Any]) -> None:
    rigged = Rigged(store)
    book = IntentBook(rigged)  # type: ignore[arg-type]
    published(book)
    rigged.replaced.update(replaced)

    assert book.next_for_poll(T0 + 1) is None
    assert store.get(FIRST).status == "PUBLISHED"


@pytest.mark.anyio
async def test_concurrent_polls_deliver_once(book: IntentBook, store: IntentStore) -> None:
    intent = published(book)

    served = await asyncio.gather(*(asyncio.to_thread(book.next_for_poll, T0 + 1 + n / 10)
                                    for n in range(8)))

    assert all(item is intent for item in served)
    assert store.get(FIRST).status == "DELIVERED"
    assert store.get(FIRST).delivered_at in {T0 + 1 + n / 10 for n in range(8)}


# --- execution reports --------------------------------------------------------------------
def test_a_limit_order_is_reported_placed_then_filled(book: IntentBook,
                                                      store: IntentStore) -> None:
    published(book)
    book.next_for_poll(T0 + 1)

    placed = book.apply_execution(report("placed"), T0 + 3)
    resend = book.apply_execution(report("placed"), T0 + 4)
    filled = book.apply_execution(report("filled", ticket=5012345999), T0 + 600)
    late_placed = book.apply_execution(report("placed"), T0 + 601)

    assert (placed.ok, placed.code, placed.record.status) == (True, ib.BOOK_APPLIED, "REPORTED")
    assert (placed.record.ticket, placed.record.reported_at) == (5012345702, T0 + 3)
    assert (resend.ok, resend.code) == (False, ib.BOOK_UNCHANGED)
    assert (filled.record.status, filled.record.ticket, filled.record.fill_price) == (
        "FILLED", 5012345999, 4535.05)
    assert (late_placed.ok, late_placed.code) == (False, ib.BOOK_OUT_OF_ORDER)
    assert store.get(FIRST).report_status == "filled"


def test_a_report_proves_delivery_and_a_market_order_fills_directly(
        book: IntentBook, store: IntentStore) -> None:
    published(book, draft(order_type="BUY", pending_expiry_epoch=0))

    outcome = book.apply_execution(report("filled"), T0 + 2)

    assert outcome.record.status == "FILLED" and outcome.record.delivered_at == T0 + 2
    assert book.next_for_poll(T0 + 3) is None


@pytest.mark.parametrize(("status", "reason", "target"), [
    ("rejected_local", "DRIFT", "REJECTED"), ("failed", "BROKER_ERROR", "REJECTED"),
    ("dry_run", "EXECUTE_DISABLED", "REJECTED"), ("expired", "EXPIRED", "EXPIRED"),
    ("cancelled", "COMMAND", "CANCELLED"),
])
def test_refusals_end_the_intent_and_free_the_book(book: IntentBook, status: str, reason: str,
                                                   target: str) -> None:
    published(book)
    book.next_for_poll(T0 + 1)

    outcome = book.apply_execution(report(status, reason_code=reason), T0 + 3)

    assert (outcome.record.status, outcome.record.report_reason) == (target, reason)
    assert outcome.record.closed_at == T0 + 3 and book.next_for_poll(T0 + 4) is None
    assert book.publish(draft(SECOND, "c-2"), FREE, T0 + 5).ok


def test_reports_for_unknown_or_closed_intents_change_nothing(
        book: IntentBook, store: IntentStore, caplog: pytest.LogCaptureFixture) -> None:
    published(book)
    book.cancel_all(ib.REASON_SESSION_STOP, T0 + 1)

    with caplog.at_level(logging.INFO, logger=ib.__name__):
        unknown = book.apply_execution(report("filled", SECOND), T0 + 2)
        again = book.apply_execution(report("cancelled", reason_code="COMMAND"), T0 + 2)
        live = book.apply_execution(report("filled"), T0 + 2)
        stale = book.apply_execution(report("expired"), T0 + 2)
        basket = book.close_from_basket(basket_id=BASKET, net_pnl=2.0, now=T0 + 60)

    assert (unknown.code, again.code, live.code, stale.code, basket.code) == (
        ib.BOOK_UNKNOWN_INTENT, ib.BOOK_UNCHANGED, ib.BOOK_AFTER_TERMINAL,
        ib.BOOK_AFTER_TERMINAL, ib.BOOK_AFTER_TERMINAL)
    assert store.get(FIRST).status == "CANCELLED" and basket.record.outcome_pnl is None
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 2 and all("order the ledger closed" in e for e in errors)


# --- expiry, cancellation, recovery -------------------------------------------------------
def test_only_an_undelivered_intent_expires_on_its_ttl(book: IntentBook,
                                                       store: IntentStore) -> None:
    published(book)

    assert book.expire_due(VALID_UNTIL - 1) == () and book.expire_due(float("nan")) == ()
    (expired,) = book.expire_due(VALID_UNTIL)
    assert (expired.status, expired.report_reason) == ("EXPIRED", "TTL_EXPIRED")
    assert book.expire_due(VALID_UNTIL + 1) == ()
    published(book, draft(SECOND, "c-2", valid_until_epoch=VALID_UNTIL + 500))
    book.next_for_poll(VALID_UNTIL + 1)
    assert book.expire_due(VALID_UNTIL + 999) == ()


@pytest.mark.parametrize(("before", "withdrawn"), [
    (lambda book: None, True), (lambda book: book.next_for_poll(T0 + 1), False),
    (lambda book: book.apply_execution(report("placed"), T0 + 1), False),
    (lambda book: book.apply_execution(report("filled"), T0 + 1), False),
])
def test_cancel_all_withdraws_only_what_the_ea_never_received(book: IntentBook, before: Any,
                                                              withdrawn: bool) -> None:
    published(book)
    before(book)

    changed = book.cancel_all(ib.REASON_HALTED, T0 + 2)

    assert [(r.status, r.report_reason) for r in changed] == (
        [("CANCELLED", "HALTED")] if withdrawn else [])
    assert book.cancel_all(ib.REASON_BREAKER, T0 + 3) == ()
    with pytest.raises(ValueError):
        book.cancel_all("", T0 + 3)


def test_recover_cancels_only_an_undeliverable_published_intent(store: IntentStore) -> None:
    first = IntentBook(store)
    published(first)

    assert first.recover(T0 + 1) == ()
    (cancelled,) = IntentBook(store).recover(T0 + 2)
    assert (cancelled.status, cancelled.report_reason) == ("CANCELLED", "RESTART")
    assert IntentBook(store).recover(T0 + 3) == ()
    published(first, draft(SECOND, "c-2"))
    first.next_for_poll(T0 + 4)
    assert IntentBook(store).recover(T0 + 5) == ()


# --- basket results -----------------------------------------------------------------------
@pytest.mark.parametrize("steps", [
    (), ("DELIVERED",), ("DELIVERED", "REPORTED"), ("DELIVERED", "FILLED"),
])
def test_a_basket_result_closes_the_intent_with_its_pnl(book: IntentBook, store: IntentStore,
                                                        steps: tuple[str, ...]) -> None:
    published(book)
    for step in steps:
        store.transition(FIRST, step, at=T0 + 1)

    closed = book.close_from_basket(basket_id=BASKET, net_pnl=-7.4, now=T0 + 3600)
    again = book.close_from_basket(basket_id=BASKET, net_pnl=-7.4, now=T0 + 3601)

    assert (closed.ok, closed.record.status, closed.record.outcome_pnl) == (True, "CLOSED", -7.4)
    assert (closed.record.basket_id, closed.record.closed_at) == (BASKET, T0 + 3600)
    assert store.get(FIRST).delivered_at is not None
    assert (again.ok, again.code) == (False, ib.BOOK_ALREADY_CLOSED)


@pytest.mark.parametrize(("basket_id", "comment", "pnl", "code", "row"), [
    ("custom-basket", f"Q6:{FIRST}", 3, ib.BOOK_APPLIED, ("CLOSED", "custom-basket", 3.0)),
    ("b" * 65, f"Q6:{FIRST}", 3, ib.BOOK_APPLIED, ("CLOSED", None, 3.0)),
    ("", f"Q6:{FIRST}", -1.5, ib.BOOK_APPLIED, ("CLOSED", None, -1.5)),
    ("XAUUSD-V5B-7", "", 1.0, ib.BOOK_UNKNOWN_INTENT, ("PUBLISHED", None, None)),
    (f"XAUUSD-V6B-{SECOND}", "", 1.0, ib.BOOK_UNKNOWN_INTENT, ("PUBLISHED", None, None)),
    (BASKET, "", float("nan"), ib.BOOK_BAD_INPUT, ("PUBLISHED", None, None)),
    (BASKET, "", True, ib.BOOK_BAD_INPUT, ("PUBLISHED", None, None)),
])
def test_a_basket_result_names_its_intent_by_basket_id_or_comment(
        book: IntentBook, store: IntentStore, basket_id: str, comment: str, pnl: Any,
        code: str, row: tuple[Any, ...]) -> None:
    published(book)

    outcome = book.close_from_basket(basket_id=basket_id, net_pnl=pnl, now=T0 + 60,
                                     comment=comment)

    stored = store.get(FIRST)
    assert (outcome.ok, outcome.code) == (code == ib.BOOK_APPLIED, code)
    assert (stored.status, stored.basket_id, stored.outcome_pnl) == row


# --- reconciliation -----------------------------------------------------------------------
def test_reconcile_fills_then_closes_an_unreported_position(book: IntentBook,
                                                            store: IntentStore) -> None:
    published(book)
    book.next_for_poll(T0 + 1)

    filled = book.reconcile_with(Exposure(T0 + 900, positions=(position(),)), T0 + 900,
                                 magic=MAGIC)
    first_miss = book.reconcile_with(Exposure(T0 + 1800), T0 + 1800, magic=MAGIC)
    closed = book.reconcile_with(Exposure(T0 + 2700), T0 + 2700, magic=MAGIC)

    assert [r.status for r in filled.changed] == ["FILLED"]
    assert (filled.changed[0].ticket, filled.changed[0].fill_price) == (5012345999, 4535.05)
    assert first_miss == ib.ReconcileOutcome()
    assert [(r.status, r.report_reason) for r in closed.changed] == [
        ("CLOSED", "CLOSED_UNREPORTED")]
    assert store.active_intent() is None


def test_reconcile_expires_reports_orphans_and_sees_old_active_intents(
        book: IntentBook, store: IntentStore) -> None:
    published(book)
    rigged = IntentBook(Rigged(store, list_recent=lambda limit=50: ()))  # type: ignore[arg-type]
    orphan = position(ticket=7).model_copy(update={"comment": "Q6:zzzzzzzzzzzz"})

    outcome = rigged.reconcile_with(Exposure(VALID_UNTIL, positions=(orphan,)), VALID_UNTIL,
                                    magic=MAGIC)

    assert [(r.intent_id, r.status) for r in outcome.changed] == [(FIRST, "EXPIRED")]
    assert [(o.ticket, o.reason) for o in outcome.orphans] == [(7, "UNKNOWN_INTENT")]


def test_reconcile_skips_a_move_it_cannot_apply(store: IntentStore) -> None:
    published(IntentBook(store))
    rigged = IntentBook(Rigged(store, get=lambda intent_id: None))  # type: ignore[arg-type]

    outcome = rigged.reconcile_with(Exposure(T0, pending_orders=(PendingOrderBlock(
        ticket=9, magic=MAGIC, order_type="BUY_LIMIT", price=4535.07, sl=4528.07, tp=4549.07,
        volume=0.01, expiration_epoch=PENDING_EXPIRY, comment=f"Q6:{FIRST}"),)), T0, magic=MAGIC)

    assert [a.kind for a in outcome.skipped] == ["MARK_PLACED"] and outcome.changed == ()


def test_occupancy_comes_from_polls_and_snapshots() -> None:
    poll = as_poll({**poll_payload(), "open_v6_positions": 1, "pending_v6_orders": 0})

    assert Occupancy.from_poll(poll) == Occupancy(1, 0) and Occupancy.from_poll(poll).occupied
    assert Occupancy.from_context(market_context()) == FREE and not FREE.occupied


@pytest.mark.parametrize(("current", "target", "path"), [
    ("FILLED", "FILLED", ()), ("PUBLISHED", "DELIVERED", ("DELIVERED",)),
    ("PUBLISHED", "REPORTED", ("DELIVERED", "REPORTED")),
    ("PUBLISHED", "CLOSED", ("DELIVERED", "FILLED", "CLOSED")),
    ("REPORTED", "CLOSED", ("FILLED", "CLOSED")), ("FILLED", "REPORTED", None),
    ("CANCELLED", "FILLED", None), ("NOPE", "FILLED", None),
])
def test_walks_follow_the_transition_table(current: str, target: Any, path: Any) -> None:
    assert walk_for(current, target) == path
