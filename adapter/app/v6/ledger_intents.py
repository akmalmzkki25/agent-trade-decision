"""
v6_intents: every published intent and its lifecycle (`runtime.intent_states`).

`IntentStore` shares the connection and lock of `LedgerCycles`; reach it as
`ledger_cycles.intents`. Each write is one IMMEDIATE transaction, so a status
change is a compare-and-set on the stored status. The database enforces what
it can of V6.0 on its own: at most one active intent (one position, no
layering), a session for every intent, known statuses and sources, lots > 0.
Only execute mode publishes intents; shadow decisions stay in v6_cycles.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, fields, replace
from types import MappingProxyType
from typing import Final

from .config import OPERATOR_AGENTS
from .runtime.intent_states import (
    ACTIVE_STATUSES, INTENT_STATUSES, IntentStatus, is_status, is_terminal, require_transition,
)
from .schemas.intent import INTENT_ID_PATTERN, SOURCES, order_problems

MAX_TEXT_CHARS: Final[int] = 64
MAX_LIST_LIMIT: Final[int] = 1000
OPERATOR_SOURCE: Final[str] = "operator"
RULES_AGENT: Final[str] = ""

_ID_RE: Final[re.Pattern[str]] = re.compile(INTENT_ID_PATTERN)
_STATUS_LIST: Final[str] = ", ".join(f"'{status}'" for status in INTENT_STATUSES)
_ACTIVE_LIST: Final[str] = ", ".join(
    f"'{status}'" for status in INTENT_STATUSES if status in ACTIVE_STATUSES)

# Every statement is repeatable; LedgerCycles runs them on open.
INTENT_SCHEMA_DDL: Final[tuple[str, ...]] = (
    f"""CREATE TABLE IF NOT EXISTS v6_intents (
        intent_id TEXT NOT NULL PRIMARY KEY, cycle_id TEXT NOT NULL,
        session_id TEXT NOT NULL, agent TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL CHECK (source IN ('rules', 'operator')),
        status TEXT NOT NULL CHECK (status IN ({_STATUS_LIST})),
        side TEXT NOT NULL CHECK (side IN ('buy', 'sell')), order_type TEXT NOT NULL,
        entry REAL NOT NULL, sl REAL NOT NULL, tp REAL NOT NULL,
        lots REAL NOT NULL CHECK (lots > 0), risk_usd REAL NOT NULL,
        valid_until_epoch INTEGER NOT NULL, pending_expiry_epoch INTEGER NOT NULL,
        time_barrier_s INTEGER NOT NULL, created_at REAL NOT NULL,
        delivered_at REAL, reported_at REAL, closed_at REAL,
        report_status TEXT, report_reason TEXT, ticket INTEGER, fill_price REAL,
        outcome_pnl REAL, basket_id TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_v6_intents_created ON v6_intents(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_v6_intents_cycle ON v6_intents(cycle_id)",
    "CREATE INDEX IF NOT EXISTS idx_v6_intents_ticket ON v6_intents(ticket)"
    " WHERE ticket IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_v6_intents_basket ON v6_intents(basket_id)"
    " WHERE basket_id IS NOT NULL",
    # At most one active intent, enforced by the database itself.
    f"CREATE UNIQUE INDEX IF NOT EXISTS ux_v6_intents_one_active"
    f" ON v6_intents((status IN ({_ACTIVE_LIST}))) WHERE status IN ({_ACTIVE_LIST})",
)


# ALTER TABLE ... ADD COLUMN appends, so these follow basket_id in IntentRecord.
INTENT_COLUMN_MIGRATIONS: Final[tuple[str, ...]] = (
    "ALTER TABLE v6_intents ADD COLUMN tp1 REAL NOT NULL DEFAULT 0",
    "ALTER TABLE v6_intents ADD COLUMN tp2 REAL NOT NULL DEFAULT 0",
    "ALTER TABLE v6_intents ADD COLUMN sl_after_tp1 REAL NOT NULL DEFAULT 0",
    "ALTER TABLE v6_intents ADD COLUMN sl_after_tp2 REAL NOT NULL DEFAULT 0",
    "ALTER TABLE v6_intents ADD COLUMN plan_step INTEGER NOT NULL DEFAULT 0",
)


class IntentStoreError(ValueError):
    """An intent write the store refused (nothing was written)."""


class DuplicateIntent(IntentStoreError):
    pass


class ActiveIntentExists(IntentStoreError):
    def __init__(self, active_intent_id: str) -> None:
        super().__init__(f"intent {active_intent_id} is still active")
        self.active_intent_id = active_intent_id


class UnknownIntent(LookupError):
    pass


def _text(name: str, value: object, *, empty_ok: bool = False) -> list[str]:
    ok = isinstance(value, str) and len(value) <= MAX_TEXT_CHARS and (empty_ok or bool(value))
    return [] if ok else [f"{name} must be a string of at most {MAX_TEXT_CHARS} characters"]


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True, kw_only=True)
class NewIntent:
    """An intent at publish time. `agent` is an OPERATOR_AGENTS name for source "operator"
    and "" for source "rules". Raises ValueError when any invariant fails."""

    intent_id: str
    cycle_id: str
    session_id: str
    agent: str
    source: str
    side: str
    order_type: str
    entry: float
    sl: float
    tp: float
    lots: float
    risk_usd: float
    valid_until_epoch: int
    pending_expiry_epoch: int
    time_barrier_s: int
    created_at: float
    # The agent plan's ladder (0 = no level); a detector suggestion leaves them at 0.
    tp1: float = 0.0
    tp2: float = 0.0
    sl_after_tp1: float = 0.0
    sl_after_tp2: float = 0.0

    def __post_init__(self) -> None:
        problems = _new_intent_problems(self)
        if problems:
            raise ValueError("invalid intent: " + "; ".join(problems))


def _identity_problems(intent: NewIntent) -> list[str]:
    problems = _text("cycle_id", intent.cycle_id) + _text("session_id", intent.session_id)
    if not isinstance(intent.intent_id, str) or not _ID_RE.match(intent.intent_id):
        problems.append("intent_id must be 12 lowercase base32 characters")
    if intent.source not in SOURCES:
        problems.append("unknown source")
    expected = OPERATOR_AGENTS if intent.source == OPERATOR_SOURCE else (RULES_AGENT,)
    if intent.agent not in expected:
        problems.append("agent does not match the source")
    return problems


def _new_intent_problems(intent: NewIntent) -> list[str]:
    numbers = (intent.entry, intent.sl, intent.tp, intent.lots, intent.risk_usd,
               intent.created_at, intent.valid_until_epoch, intent.pending_expiry_epoch,
               intent.time_barrier_s, intent.tp1, intent.tp2, intent.sl_after_tp1,
               intent.sl_after_tp2)
    if not all(_number(value) for value in numbers):
        return ["prices, lots, risk and times must be finite numbers"]
    problems = _identity_problems(intent)
    if intent.risk_usd < 0 or intent.created_at < 0:
        problems.append("risk_usd and created_at must not be negative")
    if intent.valid_until_epoch <= intent.created_at:
        problems.append("valid_until_epoch must be after created_at")
    return problems + order_problems(
        side=intent.side, order_type=intent.order_type, entry=intent.entry, sl=intent.sl,
        tp=intent.tp, lots=intent.lots, valid_until_epoch=intent.valid_until_epoch,
        pending_expiry_epoch=intent.pending_expiry_epoch, time_barrier_s=intent.time_barrier_s,
        tp1=intent.tp1, tp2=intent.tp2, sl_after_tp1=intent.sl_after_tp1,
        sl_after_tp2=intent.sl_after_tp2)


@dataclass(frozen=True)
class IntentRecord:
    """One v6_intents row; the field order is the column order."""

    intent_id: str
    cycle_id: str
    session_id: str
    agent: str
    source: str
    status: str
    side: str
    order_type: str
    entry: float
    sl: float
    tp: float
    lots: float
    risk_usd: float
    valid_until_epoch: int
    pending_expiry_epoch: int
    time_barrier_s: int
    created_at: float
    delivered_at: float | None = None
    reported_at: float | None = None
    closed_at: float | None = None
    report_status: str | None = None
    report_reason: str | None = None
    ticket: int | None = None
    fill_price: float | None = None
    outcome_pnl: float | None = None
    basket_id: str | None = None
    # Appended by INTENT_COLUMN_MIGRATIONS, so they follow basket_id in column order.
    tp1: float = 0.0
    tp2: float = 0.0
    sl_after_tp1: float = 0.0
    sl_after_tp2: float = 0.0
    plan_step: int = 0

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "active": self.active}


@dataclass(frozen=True, kw_only=True)
class IntentUpdate:
    """Facts a transition records; None leaves the stored value as it is."""

    report_status: str | None = None    # schemas.intent.ExecutionStatus of the EA report
    report_reason: str | None = None    # ExecutionReason, or an adapter reason code
    ticket: int | None = None
    fill_price: float | None = None
    outcome_pnl: float | None = None
    basket_id: str | None = None

    def __post_init__(self) -> None:
        problems = [problem for name in ("report_status", "report_reason", "basket_id")
                    if getattr(self, name) is not None
                    for problem in _text(name, getattr(self, name))]
        ticket = self.ticket
        if ticket is not None and not (
                isinstance(ticket, int) and not isinstance(ticket, bool) and ticket > 0):
            problems.append("ticket must be a positive integer")
        for name in ("fill_price", "outcome_pnl"):
            if getattr(self, name) is not None and not _number(getattr(self, name)):
                problems.append(f"{name} must be finite")
        if problems:
            raise ValueError("invalid intent update: " + "; ".join(problems))

    def values(self) -> Mapping[str, object]:
        return MappingProxyType({f.name: getattr(self, f.name) for f in fields(self)
                                 if getattr(self, f.name) is not None})


_COLUMNS: Final[tuple[str, ...]] = tuple(f.name for f in fields(IntentRecord))
_COLS: Final[str] = ", ".join(_COLUMNS)
_NEW_COLS: Final[tuple[str, ...]] = tuple(f.name for f in fields(NewIntent))
_MUTABLE: Final[tuple[str, ...]] = (
    "status", "delivered_at", "reported_at", "closed_at", "report_status", "report_reason",
    "ticket", "fill_price", "outcome_pnl", "basket_id")
# Every statement is fixed text built once from the literals above; values are bound.
_UPDATE_SQL: Final[str] = (
    "UPDATE v6_intents SET " + ", ".join(f"{name} = ?" for name in _MUTABLE)
    + " WHERE intent_id = ? AND status = ?")
_SELECT_SQL: Final[str] = f"SELECT {_COLS} FROM v6_intents"
_INSERT_SQL: Final[str] = (
    f"INSERT INTO v6_intents ({', '.join(_NEW_COLS)}, status)"
    f" VALUES ({', '.join('?' * len(_NEW_COLS))}, 'PUBLISHED')")
_PLAN_SQL: Final[str] = (
    "UPDATE v6_intents SET tp1 = ?, tp2 = ?, sl_after_tp1 = ?, sl_after_tp2 = ?,"
    " time_barrier_s = ? WHERE intent_id = ?")
_STEP_SQL: Final[str] = (
    "UPDATE v6_intents SET plan_step = ? WHERE intent_id = ? AND plan_step < ?")
_EXISTS_SQL: Final[str] = "SELECT 1 FROM v6_intents WHERE intent_id = ?"
_ACTIVE_ID_SQL: Final[str] = f"SELECT intent_id FROM v6_intents WHERE status IN ({_ACTIVE_LIST})"
_BY_ID_SQL: Final[str] = f"{_SELECT_SQL} WHERE intent_id = ?"
_ACTIVE_SQL: Final[str] = f"{_SELECT_SQL} WHERE status IN ({_ACTIVE_LIST}) LIMIT 1"
_RECENT_SQL: Final[str] = f"{_SELECT_SQL} ORDER BY created_at DESC, intent_id LIMIT ?"
_BY_TICKET_SQL: Final[str] = f"{_SELECT_SQL} WHERE ticket = ? ORDER BY created_at DESC LIMIT 1"
_BY_BASKET_SQL: Final[str] = (
    f"{_SELECT_SQL} WHERE basket_id = ? ORDER BY created_at DESC LIMIT 1")
_COUNTS_SQL: Final[str] = (
    "SELECT status, COUNT(*) FROM v6_intents WHERE created_at >= ? AND created_at < ?"
    " GROUP BY status ORDER BY status")

WriteTx = Callable[[], AbstractContextManager[sqlite3.Connection]]
Fetch = Callable[[str, tuple[object, ...]], list[tuple]]


def _merged(current: IntentRecord, target: IntentStatus, at: float,
            update: IntentUpdate) -> IntentRecord:
    changes = dict(update.values())
    if target == "DELIVERED" and current.delivered_at is None:
        changes["delivered_at"] = at
    if update.report_status is not None and current.reported_at is None:
        changes["reported_at"] = at
    if is_terminal(target):
        changes["closed_at"] = at
    return replace(current, status=target, **changes)


def _limit(limit: int) -> int:
    if not 1 <= limit <= MAX_LIST_LIMIT:
        raise ValueError(f"limit must be within 1-{MAX_LIST_LIMIT}")
    return limit


class IntentStore:
    """Synchronous; async callers wrap each call in `asyncio.to_thread`."""

    def __init__(self, write: WriteTx, fetchall: Fetch) -> None:
        self._write = write
        self._fetchall = fetchall

    def _one(self, sql: str, params: tuple[object, ...]) -> IntentRecord | None:
        rows = self._fetchall(sql, params)
        return IntentRecord(*rows[0]) if rows else None

    def insert(self, intent: NewIntent) -> IntentRecord:
        """Store `intent` as PUBLISHED.

        Raises DuplicateIntent (id already stored) or ActiveIntentExists (another
        intent is still PUBLISHED, DELIVERED, REPORTED or FILLED).
        """
        values = tuple(getattr(intent, name) for name in _NEW_COLS)
        with self._write() as conn:
            if conn.execute(_EXISTS_SQL, (intent.intent_id,)).fetchone():
                raise DuplicateIntent(f"intent {intent.intent_id} already exists")
            active = conn.execute(_ACTIVE_ID_SQL).fetchone()
            if active is not None:
                raise ActiveIntentExists(active[0])
            conn.execute(_INSERT_SQL, values)
        return IntentRecord(**{**asdict(intent), "status": "PUBLISHED"})

    def transition(self, intent_id: str, target: IntentStatus, *, at: float,
                   update: IntentUpdate | None = None) -> IntentRecord:
        """Move one intent and record `update`; returns the stored result.

        A move to the current status is a no-op that returns the stored row
        unchanged. Raises UnknownIntent, or IllegalIntentTransition for a move
        the table does not list.
        """
        if not is_status(target) or not _number(at) or at < 0:
            raise ValueError("target must be an intent status and `at` a finite epoch")
        with self._write() as conn:
            row = conn.execute(_BY_ID_SQL, (intent_id,)).fetchone()
            if row is None:
                raise UnknownIntent(f"unknown intent {str(intent_id)[:16]!r}")
            current = IntentRecord(*row)
            if current.status == target:
                return current
            require_transition(current.status, target)
            merged = _merged(current, target, float(at), update or IntentUpdate())
            params = tuple(getattr(merged, name) for name in _MUTABLE)
            conn.execute(_UPDATE_SQL, (*params, intent_id, current.status))
        return merged

    def update_plan(self, intent_id: str, *, tp1: float, tp2: float, sl_after_tp1: float,
                    sl_after_tp2: float, time_barrier_s: int) -> bool:
        """Store the ladder a management action changed (the EA has applied it)."""
        with self._write() as conn:
            changed = conn.execute(_PLAN_SQL, (tp1, tp2, sl_after_tp1, sl_after_tp2,
                                               time_barrier_s, intent_id)).rowcount
        return changed == 1

    def set_plan_step(self, intent_id: str, step: int) -> bool:
        """Record an SL+ step the EA executed; a step never goes back."""
        with self._write() as conn:
            changed = conn.execute(_STEP_SQL, (step, intent_id, step)).rowcount
        return changed == 1

    def get(self, intent_id: str) -> IntentRecord | None:
        return self._one(_BY_ID_SQL, (intent_id,))

    def active_intent(self) -> IntentRecord | None:
        return self._one(_ACTIVE_SQL, ())

    def list_recent(self, limit: int = 50) -> tuple[IntentRecord, ...]:
        """Newest first by creation time."""
        rows = self._fetchall(_RECENT_SQL, (_limit(limit),))
        return tuple(IntentRecord(*row) for row in rows)

    def by_ticket(self, ticket: int) -> IntentRecord | None:
        """The newest intent whose order or position ticket is `ticket`."""
        return self._one(_BY_TICKET_SQL, (ticket,))

    def by_basket(self, basket_id: str) -> IntentRecord | None:
        return self._one(_BY_BASKET_SQL, (basket_id,))

    def status_counts(self, since: float, until: float) -> Mapping[str, int]:
        """Intents created in [since, until), per status."""
        rows = self._fetchall(_COUNTS_SQL, (since, until))
        return MappingProxyType({status: count for status, count in rows})
