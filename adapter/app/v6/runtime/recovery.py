"""
Startup recovery (plan section 4, restart).

A process that starts has no intent drafts in memory. `IntentBook.recover`
cancels a PUBLISHED intent (it was never delivered, so nothing reached the EA),
and the newest stored snapshot is reconciled against the ledger: a V6 order or
position named `Q6:<intent_id>` moves its intent along, and one the broker no
longer shows expires once its deadline and grace have passed. Later snapshots
reconcile again. A storage or parsing failure is logged; the runtime still starts.

Execution does not survive a crash: when the previous runtime did not stop
cleanly (it left its lock behind), an armed session starts DISARMED
(`disarm_after_unclean_stop`) and the operator re-arms it with a session start.
That write failing keeps V6 off (the container refuses to start). A clean stop
keeps the session armed; the watchdog still disarms it when the EA does not
poll. Orders the EA placed before the crash are left alone: their reports and
the snapshots reconcile them.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from functools import partial
from typing import Final

from pydantic import ValidationError

from ..ledger_cycles import LedgerCycles
from ..schemas.snapshot import V6Snapshot
from .intent_book import IntentBook
from .realised_pnl import read_only_connection
from .reconcile import Exposure

logger = logging.getLogger(__name__)

# v6_sessions.disarm_reason of a session armed when the previous runtime crashed.
REASON_UNCLEAN_RESTART: Final[str] = "UNCLEAN_RESTART"

_NEWEST_SNAPSHOT_SQL: Final[str] = (
    "SELECT received_at, payload_json FROM v6_snapshots WHERE payload_json IS NOT NULL"
    " ORDER BY received_at DESC LIMIT 1")


@dataclass(frozen=True)
class RecoveryReport:
    cancelled: tuple[str, ...] = ()
    reconciled: tuple[str, ...] = ()
    orphans: int = 0
    snapshot_found: bool = False


def newest_exposure(db_path: str) -> Exposure | None:
    """Blocking: the V6 orders and positions of the newest stored snapshot, if readable."""
    with read_only_connection(db_path) as conn:
        row = conn.execute(_NEWEST_SNAPSHOT_SQL).fetchone()
    if row is None:
        return None
    received_at, payload = row
    try:
        snapshot = V6Snapshot.model_validate_json(payload)
    except ValidationError:
        logger.warning("v6 recovery: the newest stored snapshot is unreadable; skipped")
        return None
    return Exposure.from_snapshot(snapshot, float(received_at))


async def recover_intents(book: IntentBook, db_path: str, now: float, *,
                          magic: int) -> RecoveryReport:
    """Cancel undeliverable intents and reconcile with the newest snapshot; never raises
    on a storage failure (logged instead)."""
    try:
        cancelled = await asyncio.to_thread(book.recover, now)
        exposure = await asyncio.to_thread(newest_exposure, db_path)
        if exposure is None:
            return RecoveryReport(cancelled=tuple(r.intent_id for r in cancelled))
        outcome = await asyncio.to_thread(partial(book.reconcile_with, exposure, now,
                                                  magic=magic))
    except (sqlite3.Error, ValueError, LookupError) as exc:  # storage or a racing move
        logger.error("v6 intent recovery failed: %s", type(exc).__name__)
        return RecoveryReport()
    report = RecoveryReport(cancelled=tuple(r.intent_id for r in cancelled),
                            reconciled=tuple(r.intent_id for r in outcome.changed),
                            orphans=len(outcome.orphans), snapshot_found=True)
    logger.info("v6 intent recovery: cancelled=%d reconciled=%d orphans=%d",
                len(report.cancelled), len(report.reconciled), report.orphans)
    return report


def _disarm_armed(ledger: LedgerCycles, now: float) -> str | None:
    """Blocking: disarm the active session if it is armed; its id, or None."""
    session = ledger.active_session()
    if session is None or not session.armed:
        return None
    ledger.set_session_armed(session.session_id, False, at=now, reason=REASON_UNCLEAN_RESTART)
    return session.session_id


async def disarm_after_unclean_stop(ledger: LedgerCycles, now: float) -> str | None:
    """The previous runtime crashed: an armed session starts DISARMED.

    Returns the disarmed session id (None when nothing was armed). sqlite3.Error
    propagates: the caller must not start the runtime with the session still armed.
    """
    session_id = await asyncio.to_thread(_disarm_armed, ledger, now)
    if session_id is not None:
        logger.warning("v6 session %s DISARMED: the previous runtime did not stop cleanly; "
                       "a session start re-arms it", session_id)
    return session_id
