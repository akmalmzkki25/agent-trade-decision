"""runtime.reconcile: v6_intents against the V6 orders and positions a snapshot shows."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any, Final

import pytest

from app.v6.ledger_intents import IntentRecord, IntentUpdate
from app.v6.runtime import reconcile as rc
from app.v6.runtime.reconcile import Exposure, OrphanOrder, ReconcileAction, reconcile
from app.v6.schemas.snapshot import PendingOrderBlock, PositionBlock

from .cycle_fixtures_v6 import market_context
from .payloads_v6 import as_snapshot, snapshot_payload

MAGIC: Final[int] = 250570
T0: Final[float] = 1_789_565_408.0
FIRST: Final[str] = "k7w2m4pq3xza"
OTHER: Final[str] = "b5n6r7t2vw3y"
VALID_UNTIL: Final[int] = int(T0) + 120
PENDING_EXPIRY: Final[int] = int(T0) + 1800


def record(status: str, intent_id: str = FIRST, **changes: Any) -> IntentRecord:
    fields: dict[str, Any] = dict(
        intent_id=intent_id, cycle_id="c-0123456789abcdef", session_id="a1b2c3d4e5f6",
        agent="claude_code", source="operator", status=status, side="buy",
        order_type="BUY_LIMIT", entry=4535.07, sl=4528.07, tp=4549.07, lots=0.01,
        risk_usd=7.4, valid_until_epoch=VALID_UNTIL, pending_expiry_epoch=PENDING_EXPIRY,
        time_barrier_s=7200, created_at=T0)
    return IntentRecord(**{**fields, **changes})


def position(comment: str = f"Q6:{FIRST}", ticket: int = 5012345999,
             magic: int = MAGIC) -> PositionBlock:
    return PositionBlock(ticket=ticket, magic=magic, side="buy", volume=0.01,
                         price_open=4535.05, sl=4528.07, tp=4549.07, profit=1.2, swap=0.0,
                         open_epoch=int(T0) + 300, comment=comment, mae_points=10.0,
                         mfe_points=40.0)


def order(comment: str = f"Q6:{FIRST}", ticket: int = 5012345702,
          magic: int = MAGIC) -> PendingOrderBlock:
    return PendingOrderBlock(ticket=ticket, magic=magic, order_type="BUY_LIMIT", price=4535.07,
                             sl=4528.07, tp=4549.07, volume=0.01,
                             expiration_epoch=PENDING_EXPIRY, comment=comment)


def run(intents: list[IntentRecord], *, observed_at: float = T0 + 10, now: float | None = None,
        positions: tuple[PositionBlock, ...] = (), orders: tuple[PendingOrderBlock, ...] = (),
        outbox: int = 0, **kwargs: Any) -> rc.ReconcileReport:
    exposure = Exposure(observed_at=observed_at, positions=positions, pending_orders=orders,
                        outbox_pending=outbox)
    return reconcile(intents, exposure, observed_at if now is None else now, magic=MAGIC,
                     **kwargs)


def kinds(report: rc.ReconcileReport) -> list[tuple[str, str, str]]:
    return [(action.kind, action.intent_id, action.reason) for action in report.actions]


# --- actions -----------------------------------------------------------------------------------
def test_nothing_to_do_while_everything_agrees() -> None:
    report = run([record("REPORTED")], orders=(order(),))

    assert report == rc.ReconcileReport()
    assert run([]) == rc.ReconcileReport()


@pytest.mark.parametrize(("now", "expected"), [
    (VALID_UNTIL - 1, []), (VALID_UNTIL, [(rc.EXPIRE, FIRST, rc.REASON_TTL)]),
])
def test_a_published_intent_expires_at_valid_until(now: float, expected: list[Any]) -> None:
    assert kinds(run([record("PUBLISHED")], now=now)) == expected


@pytest.mark.parametrize("status", ["PUBLISHED", "DELIVERED"])
def test_a_pending_order_proves_the_intent_was_placed(status: str) -> None:
    report = run([record(status)], orders=(order(),))

    (action,) = report.actions
    assert (action.kind, action.target, action.ticket, action.fill_price) == (
        rc.MARK_PLACED, "REPORTED", 5012345702, None)
    assert action.reason == rc.REASON_ORDER_FOUND


@pytest.mark.parametrize("status", ["PUBLISHED", "DELIVERED", "REPORTED"])
def test_a_position_proves_the_intent_was_filled(status: str) -> None:
    report = run([record(status)], positions=(position(),), orders=(order(ticket=1),))

    (action,) = report.actions
    assert (action.kind, action.target, action.ticket, action.fill_price) == (
        rc.MARK_FILLED, "FILLED", 5012345999, 4535.05)
    assert report.orphans == () and dict(report.missing_since) == {}


def test_a_ticket_of_zero_is_not_recorded() -> None:
    (action,) = run([record("DELIVERED")], positions=(position(ticket=0),)).actions

    assert action.ticket is None
    assert action.update() == IntentUpdate(report_reason=rc.REASON_POSITION_FOUND,
                                           fill_price=4535.05)


@pytest.mark.parametrize(("status", "changes", "deadline"), [
    ("DELIVERED", {}, PENDING_EXPIRY),
    ("REPORTED", {}, PENDING_EXPIRY),
    ("DELIVERED", {"order_type": "BUY", "pending_expiry_epoch": 0}, VALID_UNTIL),
])
def test_an_intent_the_broker_never_shows_expires_after_its_deadline_and_grace(
        status: str, changes: dict[str, Any], deadline: int) -> None:
    intent = record(status, **changes)
    early = run([intent], observed_at=deadline + rc.RECONCILE_GRACE_S - 1)
    late = run([intent], observed_at=deadline + rc.RECONCILE_GRACE_S)

    assert early.actions == ()
    assert kinds(late) == [(rc.EXPIRE, FIRST, rc.REASON_NOT_AT_BROKER)]


@pytest.mark.parametrize("status", ["DELIVERED", "REPORTED"])
def test_a_queued_ea_report_holds_the_expiry_back_for_a_bounded_time(status: str) -> None:
    due = PENDING_EXPIRY + rc.RECONCILE_GRACE_S
    held = run([record(status)], observed_at=due + 600, outbox=2)
    released = run([record(status)], observed_at=due + rc.OUTBOX_HOLD_MAX_S, outbox=2)

    assert held.actions == ()
    assert kinds(released) == [(rc.EXPIRE, FIRST, rc.REASON_NOT_AT_BROKER)]


def test_a_queued_basket_result_holds_back_the_unreported_close() -> None:
    filled = record("FILLED")
    missing = {FIRST: T0}
    held = run([filled], observed_at=T0 + 900, outbox=1, missing_since=missing)
    late = T0 + rc.RECONCILE_GRACE_S + rc.OUTBOX_HOLD_MAX_S
    released = run([filled], observed_at=late, outbox=1, missing_since=missing)

    assert held.actions == () and dict(held.missing_since) == missing
    assert kinds(released) == [(rc.CLOSE_UNREPORTED, FIRST, rc.REASON_CLOSED_UNREPORTED)]


def test_the_snapshot_time_not_the_clock_decides_a_broker_side_expiry() -> None:
    report = run([record("REPORTED")], observed_at=T0 + 10, now=PENDING_EXPIRY + 3600)

    assert report.actions == ()


def test_a_vanished_position_closes_only_after_two_snapshots_grace_apart() -> None:
    filled = record("FILLED", ticket=5012345999)

    first = run([filled], observed_at=T0 + 900)
    same = run([filled], observed_at=T0 + 900, missing_since=first.missing_since)
    later = run([filled], observed_at=T0 + 1800, missing_since=first.missing_since)

    assert first.actions == () and dict(first.missing_since) == {FIRST: T0 + 900}
    assert same.actions == () and same.missing_since == first.missing_since
    assert kinds(later) == [(rc.CLOSE_UNREPORTED, FIRST, rc.REASON_CLOSED_UNREPORTED)]
    assert later.actions[0].target == "CLOSED" and dict(later.missing_since) == {}


def test_a_position_that_reappears_clears_the_missing_mark() -> None:
    filled = record("FILLED")

    report = run([filled], positions=(position(),), missing_since={FIRST: T0})

    assert report.actions == () and dict(report.missing_since) == {}


def test_a_filled_intent_with_a_same_comment_order_is_not_missing() -> None:
    report = run([record("FILLED")], orders=(order(),), missing_since={FIRST: T0 - 999})

    assert report.actions == () and dict(report.missing_since) == {}


@pytest.mark.parametrize("status", ["CLOSED", "EXPIRED", "CANCELLED", "REJECTED"])
def test_terminal_intents_are_never_moved(status: str) -> None:
    report = run([record(status)], now=PENDING_EXPIRY * 2, observed_at=PENDING_EXPIRY * 2)

    assert report.actions == ()


# --- orphans -------------------------------------------------------------------------------------
def test_orders_and_positions_nobody_explains_are_flagged() -> None:
    report = run(
        [record("FILLED"), record("CANCELLED", OTHER)],
        positions=(position(), position(ticket=2), position("V5 burst", ticket=3),
                   position("Q6:zzzzzzzzzzzz", ticket=4), position(ticket=5, magic=250571)),
        orders=(order(f"Q6:{OTHER}", ticket=6), order("", ticket=7)))

    assert report.actions == ()
    assert set(report.orphans) == {
        OrphanOrder("position", 2, FIRST, rc.ORPHAN_DUPLICATE),
        OrphanOrder("position", 3, None, rc.ORPHAN_NO_INTENT_ID),
        OrphanOrder("position", 4, "zzzzzzzzzzzz", rc.ORPHAN_UNKNOWN_INTENT),
        OrphanOrder("order", 6, OTHER, rc.ORPHAN_CLOSED_INTENT),
        OrphanOrder("order", 7, None, rc.ORPHAN_NO_INTENT_ID),
    }


def test_a_position_of_an_unreported_close_is_flagged_when_it_reappears() -> None:
    report = run([record("CLOSED", report_reason=rc.REASON_CLOSED_UNREPORTED)],
                 positions=(position(),))

    assert report.orphans == (OrphanOrder("position", 5012345999, FIRST,
                                          rc.ORPHAN_CLOSED_INTENT),)


# --- values ---------------------------------------------------------------------------------------
def test_exposure_comes_from_a_context_or_a_snapshot() -> None:
    payload = snapshot_payload()
    payload["positions"] = [position().model_dump()]
    payload["pending_orders"] = [order().model_dump()]
    payload["ea_state"]["outbox_pending"] = 3
    snapshot = as_snapshot(payload)

    from_snapshot = Exposure.from_snapshot(snapshot, received_at=T0)
    from_context = Exposure.from_context(market_context())

    assert (from_snapshot.observed_at, from_snapshot.positions[0].ticket) == (T0, 5012345999)
    assert from_snapshot.pending_orders[0].ticket == 5012345702
    assert isinstance(from_snapshot.positions, tuple) and from_snapshot.outbox_pending == 3
    assert from_context.outbox_pending == 0
    assert (from_context.positions, from_context.pending_orders) == ((), ())
    listed = Exposure(T0, [position()], [order()])  # type: ignore[arg-type]
    assert (listed.positions, listed.pending_orders) == ((position(),), (order(),))


@pytest.mark.parametrize("observed_at", [float("nan"), float("inf"), True, "1"])
def test_exposure_needs_a_finite_observation_time(observed_at: Any) -> None:
    with pytest.raises(ValueError):
        Exposure(observed_at=observed_at)


@pytest.mark.parametrize("pending", [-1, True, 1.0, "1"])
def test_exposure_needs_a_whole_outbox_count(pending: Any) -> None:
    with pytest.raises(ValueError, match="outbox_pending"):
        Exposure(observed_at=T0, outbox_pending=pending)


def test_actions_and_reports_are_frozen() -> None:
    action = ReconcileAction(rc.EXPIRE, FIRST, rc.REASON_TTL)
    report = run([record("PUBLISHED")], now=VALID_UNTIL)

    assert action.update() == IntentUpdate(report_reason=rc.REASON_TTL)
    assert action.target == "EXPIRED"
    with pytest.raises(FrozenInstanceError):
        action.reason = "x"  # type: ignore[misc]
    with pytest.raises(TypeError):
        report.missing_since["x"] = 1.0  # type: ignore[index]
