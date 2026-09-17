"""
v6_intents against what the broker shows (plan section 4, restart; wire contract section 7).

`reconcile` is pure. Given the intents the ledger knows and the V6 orders and
positions of the newest snapshot (magic V6_MAGIC, comment "Q6:<intent_id>"), it
lists the lifecycle moves the ledger is missing and the orders or positions no
active intent explains. `runtime.intent_book.IntentBook.reconcile_with` applies
the moves; the runtime calls it at startup and on every snapshot.

Per active intent:
  its position found        MARK_FILLED (position ticket, open price), unless FILLED
  its pending order found   MARK_PLACED (order ticket) while PUBLISHED or DELIVERED
  neither, and
    PUBLISHED               EXPIRE once now >= valid_until (it was never delivered)
    DELIVERED / REPORTED    EXPIRE once the snapshot was taken grace_s after the deadline
                            (the pending expiry; a market order's valid_until)
    FILLED                  CLOSE_UNREPORTED once snapshots at least grace_s apart both
                            miss the position: the basket result with the P&L never came
  While the snapshot says the EA outbox still holds entries (reports or basket results
  queued during an outage; the EA sends its snapshot before servicing the outbox),
  DELIVERED, REPORTED and FILLED intents are not guessed EXPIRED or CLOSED, for at most
  OUTBOX_HOLD_MAX_S past the point where they would have been.
Orphans: an order or position under the V6 magic whose comment names no intent, a
second one for the same intent, an intent the ledger does not list, or one it has
already closed. Other magics (V1-V5, other V6 instances) are ignored.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, TypeVar

from ..cycle_types import MarketContext
from ..ledger_intents import IntentRecord, IntentUpdate
from ..schemas.intent import intent_id_from_comment
from ..schemas.snapshot import PendingOrderBlock, PositionBlock, V6Snapshot
from .intent_states import DELIVERABLE_STATUSES, IntentStatus

ActionKind = Literal["MARK_PLACED", "MARK_FILLED", "CLOSE_UNREPORTED", "EXPIRE"]
MARK_PLACED: Final[ActionKind] = "MARK_PLACED"
MARK_FILLED: Final[ActionKind] = "MARK_FILLED"
CLOSE_UNREPORTED: Final[ActionKind] = "CLOSE_UNREPORTED"
EXPIRE: Final[ActionKind] = "EXPIRE"
ACTION_TARGETS: Final[Mapping[str, IntentStatus]] = MappingProxyType({
    MARK_PLACED: "REPORTED", MARK_FILLED: "FILLED", CLOSE_UNREPORTED: "CLOSED",
    EXPIRE: "EXPIRED",
})
# v6_intents.report_reason written by a reconciled move.
REASON_ORDER_FOUND: Final[str] = "RECONCILED_ORDER"
REASON_POSITION_FOUND: Final[str] = "RECONCILED_POSITION"
REASON_TTL: Final[str] = "TTL_EXPIRED"
REASON_NOT_AT_BROKER: Final[str] = "NOT_AT_BROKER"
REASON_CLOSED_UNREPORTED: Final[str] = "CLOSED_UNREPORTED"

OrphanKind = Literal["position", "order"]
ORPHAN_NO_INTENT_ID: Final[str] = "NO_INTENT_ID"
ORPHAN_DUPLICATE: Final[str] = "DUPLICATE_FOR_INTENT"
ORPHAN_UNKNOWN_INTENT: Final[str] = "UNKNOWN_INTENT"
ORPHAN_CLOSED_INTENT: Final[str] = "CLOSED_INTENT"
# Deadline grace, and the least time between two snapshots that both miss a position.
RECONCILE_GRACE_S: Final[float] = 60.0
# How long a non-empty EA outbox may hold back an EXPIRE or CLOSE_UNREPORTED guess.
OUTBOX_HOLD_MAX_S: Final[float] = 3600.0
FILLED_STATUS: Final[IntentStatus] = "FILLED"
PUBLISHED_STATUS: Final[IntentStatus] = "PUBLISHED"

_NOTHING_MISSING: Final[Mapping[str, float]] = MappingProxyType({})
_Block = TypeVar("_Block", PositionBlock, PendingOrderBlock)


@dataclass(frozen=True)
class Exposure:
    """The V6 orders and positions one snapshot showed, when it arrived (adapter clock),
    and how many entries the EA outbox still held."""

    observed_at: float
    positions: tuple[PositionBlock, ...] = ()
    pending_orders: tuple[PendingOrderBlock, ...] = ()
    outbox_pending: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "positions", tuple(self.positions))
        object.__setattr__(self, "pending_orders", tuple(self.pending_orders))
        at = self.observed_at
        if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at):
            raise ValueError("observed_at must be a finite epoch")
        pending = self.outbox_pending
        if isinstance(pending, bool) or not isinstance(pending, int) or pending < 0:
            raise ValueError("outbox_pending must be a non-negative integer")

    @classmethod
    def from_context(cls, context: MarketContext) -> "Exposure":
        return cls(context.received_at, context.positions, context.pending_orders)

    @classmethod
    def from_snapshot(cls, snapshot: V6Snapshot, received_at: float) -> "Exposure":
        return cls(received_at, tuple(snapshot.positions), tuple(snapshot.pending_orders),
                   snapshot.ea_state.outbox_pending)


@dataclass(frozen=True)
class ReconcileAction:
    """One lifecycle move the ledger is missing."""

    kind: ActionKind
    intent_id: str
    reason: str
    ticket: int | None = None
    fill_price: float | None = None

    @property
    def target(self) -> IntentStatus:
        return ACTION_TARGETS[self.kind]

    def update(self) -> IntentUpdate:
        return IntentUpdate(report_reason=self.reason, ticket=self.ticket,
                            fill_price=self.fill_price)


@dataclass(frozen=True)
class OrphanOrder:
    """A V6 order or position that no active intent explains."""

    kind: OrphanKind
    ticket: int
    intent_id: str | None
    reason: str


@dataclass(frozen=True)
class ReconcileReport:
    """`missing_since`: FILLED intents whose position the snapshots miss, and since when."""

    actions: tuple[ReconcileAction, ...] = ()
    orphans: tuple[OrphanOrder, ...] = ()
    missing_since: Mapping[str, float] = _NOTHING_MISSING


@dataclass(frozen=True)
class _Seen:
    """What one intent's rule reads besides the intent itself."""

    exposure: Exposure
    now: float
    grace_s: float
    missing_since: Mapping[str, float]


def _index(blocks: Iterable[_Block], magic: int,
           kind: OrphanKind) -> tuple[Mapping[str, _Block], tuple[OrphanOrder, ...]]:
    """The first block per intent id under `magic`, and the blocks that cannot be one."""
    index: dict[str, _Block] = {}
    strays: list[OrphanOrder] = []
    for block in blocks:
        if block.magic != magic:
            continue
        intent_id = intent_id_from_comment(block.comment)
        if intent_id is None or intent_id in index:
            reason = ORPHAN_NO_INTENT_ID if intent_id is None else ORPHAN_DUPLICATE
            strays.append(OrphanOrder(kind, block.ticket, intent_id, reason))
        else:
            index[intent_id] = block
    return MappingProxyType(index), tuple(strays)


def _unexplained(index: Mapping[str, PositionBlock | PendingOrderBlock],
                 known: Mapping[str, IntentRecord], kind: OrphanKind) -> tuple[OrphanOrder, ...]:
    return tuple(
        OrphanOrder(kind, block.ticket, intent_id,
                    ORPHAN_UNKNOWN_INTENT if intent_id not in known else ORPHAN_CLOSED_INTENT)
        for intent_id, block in index.items()
        if intent_id not in known or not known[intent_id].active)


def _when(due: bool, kind: ActionKind, intent_id: str, reason: str) -> ReconcileAction | None:
    return ReconcileAction(kind, intent_id, reason) if due else None


def _guess_due(overdue_s: float, seen: _Seen) -> bool:
    """A guessed move is due `overdue_s` >= 0 past its point, unless the EA outbox may
    still hold the fact (for at most OUTBOX_HOLD_MAX_S)."""
    if overdue_s < 0:
        return False
    return seen.exposure.outbox_pending == 0 or overdue_s >= OUTBOX_HOLD_MAX_S


def _action(record: IntentRecord, position: PositionBlock | None,
            order: PendingOrderBlock | None, seen: _Seen) -> ReconcileAction | None:
    status, intent_id = record.status, record.intent_id
    if position is not None:
        if status == FILLED_STATUS:
            return None
        return ReconcileAction(MARK_FILLED, intent_id, REASON_POSITION_FOUND,
                               ticket=position.ticket or None, fill_price=position.price_open)
    if order is not None:
        if status not in DELIVERABLE_STATUSES:
            return None
        return ReconcileAction(MARK_PLACED, intent_id, REASON_ORDER_FOUND,
                               ticket=order.ticket or None)
    observed_at = seen.exposure.observed_at
    if status == PUBLISHED_STATUS:
        return _when(seen.now >= record.valid_until_epoch, EXPIRE, intent_id, REASON_TTL)
    if status == FILLED_STATUS:
        since = seen.missing_since[intent_id]
        return _when(_guess_due(observed_at - since - seen.grace_s, seen), CLOSE_UNREPORTED,
                     intent_id, REASON_CLOSED_UNREPORTED)
    deadline = record.pending_expiry_epoch or record.valid_until_epoch
    return _when(_guess_due(observed_at - deadline - seen.grace_s, seen), EXPIRE, intent_id,
                 REASON_NOT_AT_BROKER)


def reconcile(intents: Iterable[IntentRecord], exposure: Exposure, now: float, *, magic: int,
              grace_s: float = RECONCILE_GRACE_S,
              missing_since: Mapping[str, float] = _NOTHING_MISSING) -> ReconcileReport:
    """The moves the ledger is missing and the V6 orders it cannot explain.

    `intents`: recent v6_intents rows (every active one at least); `now`: the adapter
    clock; `missing_since`: the previous report's, so a FILLED intent is closed only
    after its position was missing from snapshots at least `grace_s` apart.
    """
    known = {record.intent_id: record for record in intents}
    positions, stray_positions = _index(exposure.positions, magic, "position")
    orders, stray_orders = _index(exposure.pending_orders, magic, "order")
    active = tuple(record for record in known.values() if record.active)
    missing = {
        record.intent_id: missing_since.get(record.intent_id, exposure.observed_at)
        for record in active
        if record.status == FILLED_STATUS and record.intent_id not in positions
        and record.intent_id not in orders
    }
    seen = _Seen(exposure=exposure, now=now, grace_s=grace_s, missing_since=missing)
    found = (_action(record, positions.get(record.intent_id), orders.get(record.intent_id),
                     seen) for record in active)
    actions = tuple(action for action in found if action is not None)
    closed = {action.intent_id for action in actions if action.kind == CLOSE_UNREPORTED}
    orphans = (stray_positions + stray_orders + _unexplained(positions, known, "position")
               + _unexplained(orders, known, "order"))
    return ReconcileReport(
        actions=actions, orphans=orphans,
        missing_since=MappingProxyType({k: v for k, v in missing.items() if k not in closed}))
