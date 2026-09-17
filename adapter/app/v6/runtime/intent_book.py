"""
The one-active-intent book (plan sections 4-5; wire contract sections 6.3 and 7).

`IntentBook` runs the intent lifecycle over the v6_intents repository
(`ledger_intents.IntentStore`, reached as `ledger_cycles.intents`) and keeps the
draft of the intent it published, because its EA-only fields (reference price,
drift and spread limits, magic) are not stored in v6_intents. At most one intent
is active, so one draft slot suffices. An intent without its draft (after a
restart) is never served; `recover` cancels it while it is still PUBLISHED, which
means it was never delivered.

  publish            store a built draft as PUBLISHED: no other active intent, no V6
                     position or pending order at the broker, one intent per cycle
  next_for_poll      the draft to serve while `arming.decide_arm` says ARMED; its first
                     delivery writes DELIVERED first
  apply_execution    an EA report moves the intent along its legal path; resends change nothing
  expire_due         PUBLISHED past valid_until -> EXPIRED
  cancel_all         session stop, halt, breaker: PUBLISHED -> CANCELLED. The EA acts on a
                     DELIVERED intent inside the reply that carried it, so DELIVERED and
                     REPORTED wait for its report (after CANCEL_PENDING) or reconcile, and
                     FILLED keeps running
  close_from_basket  the V6 basket result closes the intent with its P&L
  reconcile_with     `runtime.reconcile` against the newest snapshot, applied
  recover            at startup, cancel a PUBLISHED intent whose draft is gone

A later fact proves the earlier steps (`walk_for`): a fill for a PUBLISHED intent
walks it through DELIVERED. Methods are synchronous and serialised by one lock;
async callers wrap them in `asyncio.to_thread`. SQLite errors propagate, except in
`next_for_poll`, which then serves nothing (write first, then answer).
"""

from __future__ import annotations

import logging
import math
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, fields
from threading import RLock
from types import MappingProxyType
from typing import Final, Protocol

from ..cycle_types import MarketContext
from ..ledger_intents import (
    MAX_TEXT_CHARS, ActiveIntentExists, DuplicateIntent, IntentRecord, IntentUpdate, NewIntent,
    UnknownIntent,
)
from ..risk.intent_builder import IntentDraft
from ..schemas.intent import (
    ACCEPTED_STATUSES, ExecutionReport, PollRequest, intent_id_from_basket,
    intent_id_from_comment,
)
from .arming import ArmDecision
from .intent_states import (
    DELIVERABLE_STATUSES, IllegalIntentTransition, IntentStatus, can_transition, is_terminal,
    target_for_report,
)
from .reconcile import (
    REASON_TTL, RECONCILE_GRACE_S, Exposure, OrphanOrder, ReconcileAction, reconcile,
)

logger = logging.getLogger(__name__)

BOOK_PUBLISHED: Final[str] = "BOOK_PUBLISHED"
BOOK_APPLIED: Final[str] = "BOOK_APPLIED"
BOOK_UNCHANGED: Final[str] = "BOOK_UNCHANGED"            # a resend: nothing new
BOOK_OCCUPANCY_UNKNOWN: Final[str] = "BOOK_OCCUPANCY_UNKNOWN"
BOOK_OCCUPIED: Final[str] = "BOOK_OCCUPIED"              # a V6 position or pending order is open
BOOK_TOO_LATE: Final[str] = "BOOK_TOO_LATE"
BOOK_CYCLE_USED: Final[str] = "BOOK_CYCLE_USED"
BOOK_ACTIVE_INTENT: Final[str] = "BOOK_ACTIVE_INTENT"
BOOK_DUPLICATE: Final[str] = "BOOK_DUPLICATE"
BOOK_UNKNOWN_INTENT: Final[str] = "BOOK_UNKNOWN_INTENT"
BOOK_OUT_OF_ORDER: Final[str] = "BOOK_OUT_OF_ORDER"      # the lifecycle is already past it
BOOK_AFTER_TERMINAL: Final[str] = "BOOK_AFTER_TERMINAL"  # a fact about a closed intent
BOOK_ALREADY_CLOSED: Final[str] = "BOOK_ALREADY_CLOSED"
BOOK_BAD_INPUT: Final[str] = "BOOK_BAD_INPUT"
# v6_intents.report_reason of the adapter's own moves.
REASON_SESSION_STOP: Final[str] = "SESSION_STOP"
REASON_HALTED: Final[str] = "HALTED"
REASON_BREAKER: Final[str] = "BREAKER"
REASON_RESTART: Final[str] = "RESTART"
# One intent per M15 cycle at most, so 50 rows reach back well over the time barrier.
RECENT_SCAN_LIMIT: Final[int] = 50
PUBLISHED: Final[IntentStatus] = "PUBLISHED"
DELIVERED: Final[IntentStatus] = "DELIVERED"
FILLED: Final[IntentStatus] = "FILLED"
CLOSED: Final[IntentStatus] = "CLOSED"
EXPIRED: Final[IntentStatus] = "EXPIRED"
CANCELLED: Final[IntentStatus] = "CANCELLED"

_WALKS: Final[Mapping[tuple[str, str], tuple[IntentStatus, ...]]] = MappingProxyType({
    (PUBLISHED, "REPORTED"): (DELIVERED, "REPORTED"),
    (PUBLISHED, FILLED): (DELIVERED, FILLED),
    (PUBLISHED, CLOSED): (DELIVERED, FILLED, CLOSED),
    (DELIVERED, CLOSED): (FILLED, CLOSED),
    ("REPORTED", CLOSED): (FILLED, CLOSED),
})
_ROW_FIELDS: Final[tuple[str, ...]] = tuple(f.name for f in fields(NewIntent))


def walk_for(current: str, target: IntentStatus) -> tuple[IntentStatus, ...] | None:
    """The legal steps from `current` to `target`: () when there already, None if impossible."""
    if current == target:
        return ()
    if can_transition(current, target):
        return (target,)
    return _WALKS.get((current, target))


class IntentRepository(Protocol):
    """What the book needs of `ledger_intents.IntentStore`."""

    def insert(self, intent: NewIntent) -> IntentRecord: ...

    def transition(self, intent_id: str, target: IntentStatus, *, at: float,
                   update: IntentUpdate | None = None) -> IntentRecord: ...

    def get(self, intent_id: str) -> IntentRecord | None: ...

    def active_intent(self) -> IntentRecord | None: ...

    def list_recent(self, limit: int = RECENT_SCAN_LIMIT) -> tuple[IntentRecord, ...]: ...


@dataclass(frozen=True)
class Occupancy:
    """V6 positions and pending orders at the broker, as the EA last reported them."""

    open_positions: int
    pending_orders: int

    @classmethod
    def from_poll(cls, poll: PollRequest) -> "Occupancy":
        return cls(poll.open_v6_positions, poll.pending_v6_orders)

    @classmethod
    def from_context(cls, context: MarketContext) -> "Occupancy":
        return cls(len(context.positions), len(context.pending_orders))

    @property
    def occupied(self) -> bool:
        return self.open_positions != 0 or self.pending_orders != 0


@dataclass(frozen=True)
class BookOutcome:
    ok: bool
    code: str
    record: IntentRecord | None = None
    detail: str = ""


@dataclass(frozen=True)
class ReconcileOutcome:
    changed: tuple[IntentRecord, ...] = ()
    orphans: tuple[OrphanOrder, ...] = ()
    skipped: tuple[ReconcileAction, ...] = ()


def _matches(row: NewIntent, record: IntentRecord) -> bool:
    return all(getattr(row, name) == getattr(record, name) for name in _ROW_FIELDS)


def _publish_refusal(draft: IntentDraft, occupancy: Occupancy | None,
                     now: float) -> BookOutcome | None:
    if occupancy is None:
        return BookOutcome(False, BOOK_OCCUPANCY_UNKNOWN,
                           detail="the EA has not reported its V6 orders yet")
    if occupancy.occupied:
        return BookOutcome(False, BOOK_OCCUPIED, detail=(
            f"{occupancy.open_positions} V6 positions, {occupancy.pending_orders} pending orders"))
    if not now < draft.row.valid_until_epoch:
        return BookOutcome(False, BOOK_TOO_LATE, detail="the draft is no longer valid")
    return None


def _report_update(report: ExecutionReport) -> IntentUpdate:
    filled = report.status == "filled" and report.fill_price > 0
    return IntentUpdate(report_status=report.status, report_reason=report.reason_code,
                        ticket=report.ticket or None,
                        fill_price=report.fill_price if filled else None)


def _not_applied(record: IntentRecord, detail: str, *, live_order: bool) -> BookOutcome:
    if not is_terminal(record.status):
        logger.info("v6 intent %s: %s ignored", record.intent_id, detail)
        return BookOutcome(False, BOOK_OUT_OF_ORDER, record, detail)
    if live_order:
        logger.error("v6 intent %s is %s but %s: the broker may hold an order the ledger "
                     "closed", record.intent_id, record.status, detail)
    else:
        logger.info("v6 intent %s is %s; %s ignored", record.intent_id, record.status, detail)
    return BookOutcome(False, BOOK_AFTER_TERMINAL, record, detail)


def _log_reconcile(changed: tuple[IntentRecord, ...], skipped: tuple[ReconcileAction, ...],
                   orphans: tuple[OrphanOrder, ...]) -> None:
    for record in changed:
        logger.warning("v6 intent %s reconciled to %s (%s)", record.intent_id, record.status,
                       record.report_reason)
    for action in skipped:
        logger.warning("v6 reconcile could not apply %s to %s", action.kind, action.intent_id)
    for orphan in orphans:
        logger.error("v6 %s %d has no active intent (%s, intent %s)", orphan.kind,
                     orphan.ticket, orphan.reason, orphan.intent_id or "-")


class IntentBook:
    def __init__(self, store: IntentRepository) -> None:
        self._store = store
        self._lock = RLock()
        self._draft: IntentDraft | None = None
        self._missing: Mapping[str, float] = MappingProxyType({})
        self._unserved_logged: str | None = None

    def active(self) -> IntentRecord | None:
        return self._store.active_intent()

    # --- publishing and serving ----------------------------------------------------------
    def publish(self, draft: IntentDraft, occupancy: Occupancy | None,
                now: float) -> BookOutcome:
        """Store `draft` as PUBLISHED; `occupancy` is the EA's newest report (None: unknown)."""
        refusal = _publish_refusal(draft, occupancy, now)
        if refusal is not None:
            return refusal
        row = draft.row
        with self._lock:
            if any(r.cycle_id == row.cycle_id for r in self._store.list_recent(RECENT_SCAN_LIMIT)):
                return BookOutcome(False, BOOK_CYCLE_USED,
                                   detail=f"cycle {row.cycle_id} already published an intent")
            try:
                record = self._store.insert(row)
            except DuplicateIntent as exc:
                return BookOutcome(False, BOOK_DUPLICATE, detail=str(exc))
            except ActiveIntentExists as exc:
                return BookOutcome(False, BOOK_ACTIVE_INTENT, detail=str(exc))
            self._draft = draft
        logger.info("v6 intent %s published: %s %s lots=%s valid_until=%d cycle=%s agent=%s",
                    row.intent_id, row.order_type, row.side, row.lots, row.valid_until_epoch,
                    row.cycle_id, row.agent)
        return BookOutcome(True, BOOK_PUBLISHED, record)

    def next_for_poll(self, now: float, *, arm: ArmDecision | None = None) -> IntentDraft | None:
        """The intent this poll should carry, or None; storage failures serve nothing.

        Serve only an armed session: pass the poll's `decide_arm` result as `arm` (an
        unarmed decision serves and writes nothing; None leaves that check to the caller).
        """
        if arm is not None and not arm.armed:
            return None
        with self._lock:
            try:
                return self._deliverable(now)
            except (sqlite3.Error, IllegalIntentTransition, UnknownIntent) as exc:
                logger.error("v6 intent delivery skipped: %s", type(exc).__name__)
                return None

    def _deliverable(self, now: float) -> IntentDraft | None:
        record = self._store.active_intent()
        if (record is None or record.status not in DELIVERABLE_STATUSES
                or not now < record.valid_until_epoch):
            return None
        draft = self._draft
        if draft is None or not _matches(draft.row, record):
            if self._unserved_logged != record.intent_id:
                self._unserved_logged = record.intent_id
                logger.warning("v6 intent %s cannot be served: its draft is not in this "
                               "process", record.intent_id)
            return None
        if record.status == PUBLISHED:
            self._store.transition(record.intent_id, DELIVERED, at=now)
        return draft

    # --- lifecycle -------------------------------------------------------------------------
    def _move(self, record: IntentRecord, target: IntentStatus, update: IntentUpdate,
              now: float) -> IntentRecord | None:
        """Walk `record` to `target` (None when no legal path leads there)."""
        path = walk_for(record.status, target)
        if not path:
            return None
        for step in path[:-1]:
            self._store.transition(record.intent_id, step, at=now)
        moved = self._store.transition(record.intent_id, path[-1], at=now, update=update)
        if moved.status not in DELIVERABLE_STATUSES and self._draft is not None \
                and self._draft.row.intent_id == moved.intent_id:
            self._draft = None
        return moved

    def apply_execution(self, report: ExecutionReport, now: float) -> BookOutcome:
        """Apply an EA report once; a resend or an out-of-order report changes nothing."""
        target = target_for_report(report.status)
        with self._lock:
            record = self._store.get(report.intent_id)
            if record is None:
                logger.warning("v6 %s report for unknown intent %s", report.status,
                               report.intent_id)
                return BookOutcome(False, BOOK_UNKNOWN_INTENT)
            if record.status == target:
                return BookOutcome(False, BOOK_UNCHANGED, record)
            moved = self._move(record, target, _report_update(report), now)
        if moved is None:
            return _not_applied(record, f"{report.status} report",
                                live_order=report.status in ACCEPTED_STATUSES)
        logger.info("v6 intent %s %s -> %s (%s/%s)", moved.intent_id, record.status,
                    moved.status, report.status, report.reason_code)
        return BookOutcome(True, BOOK_APPLIED, moved)

    def expire_due(self, now: float) -> tuple[IntentRecord, ...]:
        """A PUBLISHED intent past valid_until was never delivered: EXPIRED."""
        with self._lock:
            record = self._store.active_intent()
            if record is None or record.status != PUBLISHED \
                    or not now >= record.valid_until_epoch:
                return ()
            moved = self._move(record, EXPIRED, IntentUpdate(report_reason=REASON_TTL), now)
        logger.info("v6 intent %s expired undelivered", record.intent_id)
        return () if moved is None else (moved,)

    def cancel_all(self, reason: str, now: float) -> tuple[IntentRecord, ...]:
        """Session stop, halt or breaker (`REASON_*`): withdraw what the EA never received.

        A DELIVERED intent may already be filled with its report still in the EA outbox;
        cancelling it would orphan that live trade, so it is left to the EA's report.
        """
        update = IntentUpdate(report_reason=reason)
        with self._lock:
            record = self._store.active_intent()
            if record is None or record.status != PUBLISHED:
                return ()
            moved = self._move(record, CANCELLED, update, now)
        logger.warning("v6 intent %s cancelled while %s (%s)", record.intent_id, record.status,
                       reason)
        return () if moved is None else (moved,)

    def close_from_basket(self, *, basket_id: str, net_pnl: float, now: float,
                          comment: str = "") -> BookOutcome:
        """The V6 basket result ('<symbol>-V6B-<id>', or a 'Q6:<id>' comment) closes the intent."""
        intent_id = intent_id_from_basket(basket_id) or intent_id_from_comment(comment)
        if intent_id is None:
            return BookOutcome(False, BOOK_UNKNOWN_INTENT, detail="the basket names no V6 intent")
        if isinstance(net_pnl, bool) or not isinstance(net_pnl, (int, float)) \
                or not math.isfinite(net_pnl):
            return BookOutcome(False, BOOK_BAD_INPUT, detail="net_pnl must be a finite number")
        stored_basket = basket_id if 0 < len(basket_id) <= MAX_TEXT_CHARS else None
        update = IntentUpdate(outcome_pnl=float(net_pnl), basket_id=stored_basket)
        with self._lock:
            record = self._store.get(intent_id)
            if record is None:
                return BookOutcome(False, BOOK_UNKNOWN_INTENT, detail=f"no intent {intent_id}")
            if record.status == CLOSED:
                return BookOutcome(False, BOOK_ALREADY_CLOSED, record)
            moved = self._move(record, CLOSED, update, now)
        if moved is None:
            return _not_applied(record, "a basket result", live_order=True)
        logger.info("v6 intent %s closed: net_pnl=%.2f basket=%s", intent_id, net_pnl,
                    stored_basket or "-")
        return BookOutcome(True, BOOK_APPLIED, moved)

    # --- reconciliation ------------------------------------------------------------------------
    def reconcile_with(self, exposure: Exposure, now: float, *, magic: int,
                       grace_s: float = RECONCILE_GRACE_S) -> ReconcileOutcome:
        """Bring the ledger in line with the newest snapshot's V6 orders and positions."""
        with self._lock:
            recent = self._store.list_recent(RECENT_SCAN_LIMIT)
            active = self._store.active_intent()
            if active is not None and all(r.intent_id != active.intent_id for r in recent):
                recent = (*recent, active)
            report = reconcile(recent, exposure, now, magic=magic, grace_s=grace_s,
                               missing_since=self._missing)
            moves = tuple((action, self._apply(action, now)) for action in report.actions)
            self._missing = report.missing_since
        changed = tuple(moved for _, moved in moves if moved is not None)
        skipped = tuple(action for action, moved in moves if moved is None)
        _log_reconcile(changed, skipped, report.orphans)
        return ReconcileOutcome(changed=changed, orphans=report.orphans, skipped=skipped)

    def _apply(self, action: ReconcileAction, now: float) -> IntentRecord | None:
        record = self._store.get(action.intent_id)
        return None if record is None else self._move(record, action.target, action.update(), now)

    def recover(self, now: float) -> tuple[IntentRecord, ...]:
        """At startup: cancel a PUBLISHED intent this process has no draft for."""
        with self._lock:
            record = self._store.active_intent()
            draft = self._draft
            if record is None or record.status != PUBLISHED or (
                    draft is not None and _matches(draft.row, record)):
                return ()
            moved = self._move(record, CANCELLED, IntentUpdate(report_reason=REASON_RESTART),
                               now)
        logger.warning("v6 intent %s cancelled: published before a restart, never delivered",
                       record.intent_id)
        return () if moved is None else (moved,)
