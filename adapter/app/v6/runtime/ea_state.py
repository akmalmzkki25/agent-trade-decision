"""
Process-local view of what the V6 EA last told the adapter.

Everything stored here is an immutable value (frozen dataclasses around frozen
pydantic models), and readers only ever receive a freshly built `EaStateView`,
so nothing a route hands out can change underneath the caller. All mutation
happens on the event loop thread, which is why no lock is taken.

The snapshot inbox holds one item: the deliberation runtime only ever wants
the newest closed bar, so an unconsumed snapshot is replaced ("latest wins")
and counted as superseded rather than queued behind.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Final

from ..schemas.intent import PollRequest
from ..schemas.snapshot import V6Snapshot

INBOX_MAXSIZE: Final[int] = 1
CYCLE_ID_PREFIX: Final[str] = "c-"
CYCLE_ID_HEX_CHARS: Final[int] = 16


def cycle_id_for(snapshot_id: str) -> str:
    """Stable cycle id, so an EA retry of the same snapshot maps to the same cycle."""
    digest = hashlib.sha256(snapshot_id.encode("utf-8")).hexdigest()
    return CYCLE_ID_PREFIX + digest[:CYCLE_ID_HEX_CHARS]


@dataclass(frozen=True)
class PollObservation:
    poll: PollRequest
    received_at: float


@dataclass(frozen=True)
class SnapshotMeta:
    snapshot_id: str
    cycle_id: str
    bar_open_epoch: int
    received_at: float
    trade_mode: str
    clock_skew_s: float

    @classmethod
    def from_snapshot(cls, snapshot: V6Snapshot, cycle_id: str,
                      received_at: float) -> "SnapshotMeta":
        return cls(
            snapshot_id=snapshot.snapshot_id, cycle_id=cycle_id,
            bar_open_epoch=snapshot.bar_open_epoch, received_at=received_at,
            trade_mode=snapshot.account.trade_mode,
            clock_skew_s=received_at - snapshot.sent_at_epoch,
        )


@dataclass(frozen=True)
class InboxItem:
    cycle_id: str
    snapshot: V6Snapshot
    received_at: float


@dataclass(frozen=True)
class EaStateView:
    last_poll: PollObservation | None
    last_snapshot: SnapshotMeta | None
    last_seen_at: float | None
    superseded: int
    inbox_pending: int

    def ea_age_s(self, now: float) -> float | None:
        return None if self.last_seen_at is None else now - self.last_seen_at


class EaState:
    def __init__(self) -> None:
        # Since Python 3.10 a Queue binds to the running loop on first use,
        # so building it outside the loop (at app creation) is safe.
        self._inbox: asyncio.Queue[InboxItem] = asyncio.Queue(maxsize=INBOX_MAXSIZE)
        self._last_poll: PollObservation | None = None
        self._last_snapshot: SnapshotMeta | None = None
        self._last_seen_at: float | None = None
        self._last_mark_minute: int | None = None
        self._superseded = 0

    def touch(self, received_at: float) -> None:
        """Note EA activity; out-of-order requests never move last-seen backwards."""
        if self._last_seen_at is None or received_at > self._last_seen_at:
            self._last_seen_at = received_at

    def record_poll(self, poll: PollRequest, received_at: float) -> PollObservation:
        observation = PollObservation(poll=poll, received_at=received_at)
        self._last_poll = observation
        self.touch(received_at)
        return observation

    def record_snapshot(self, meta: SnapshotMeta) -> None:
        self._last_snapshot = meta
        self.touch(meta.received_at)

    def offer_snapshot(self, item: InboxItem) -> bool:
        """Queue `item`; True when it replaced a snapshot nobody had consumed yet."""
        try:
            self._inbox.get_nowait()
            replaced = True
        except asyncio.QueueEmpty:
            replaced = False
        # Single-threaded: the slot freed above cannot be taken in between.
        self._inbox.put_nowait(item)
        if replaced:
            self._superseded += 1
        return replaced

    async def next_snapshot(self) -> InboxItem:
        return await self._inbox.get()

    def mark_due(self, minute_epoch: int) -> bool:
        """True while no account mark has been written for this or a later minute."""
        return self._last_mark_minute is None or minute_epoch > self._last_mark_minute

    def mark_done(self, minute_epoch: int) -> None:
        if self.mark_due(minute_epoch):
            self._last_mark_minute = minute_epoch

    def view(self) -> EaStateView:
        return EaStateView(
            last_poll=self._last_poll,
            last_snapshot=self._last_snapshot,
            last_seen_at=self._last_seen_at,
            superseded=self._superseded,
            inbox_pending=self._inbox.qsize(),
        )
