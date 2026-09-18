"""
v6_actions and v6_plan_steps: management actions sent to the EA, and the SL+ steps it took.

`ActionStore` shares the connection and lock of `LedgerCycles`; reach it as
`ledger_cycles.actions`. An action is PUBLISHED when it is queued for the EA and moves
once to APPLIED, REJECTED, FAILED or EXPIRED, so a late report can never revive it. A plan
step is recorded once per (ticket, step).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

from .schemas.intent import ACTION_COMMANDS, INTENT_ID_PATTERN

ACTION_STATUSES: Final[tuple[str, ...]] = ("PUBLISHED", "APPLIED", "REJECTED", "FAILED",
                                           "EXPIRED")
FINAL_ACTION_STATUSES: Final[frozenset[str]] = frozenset(ACTION_STATUSES[1:])
MAX_TEXT_CHARS: Final[int] = 120
MAX_PAYLOAD_BYTES: Final[int] = 2048
MAX_LIST_LIMIT: Final[int] = 500
_ID_RE: Final[re.Pattern[str]] = re.compile(INTENT_ID_PATTERN)
_STATUS_LIST: Final[str] = ", ".join(f"'{status}'" for status in ACTION_STATUSES)

# Every statement is repeatable; LedgerCycles runs them on open.
ACTION_SCHEMA_DDL: Final[tuple[str, ...]] = (
    f"""CREATE TABLE IF NOT EXISTS v6_actions (
        action_id TEXT NOT NULL PRIMARY KEY, cycle_id TEXT NOT NULL,
        session_id TEXT NOT NULL, agent TEXT NOT NULL, command TEXT NOT NULL,
        ticket INTEGER NOT NULL CHECK (ticket > 0), intent_id TEXT NOT NULL DEFAULT '',
        payload TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ({_STATUS_LIST})),
        detail TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_v6_actions_session ON v6_actions(session_id, created_at)",
    """CREATE TABLE IF NOT EXISTS v6_plan_steps (
        ticket INTEGER NOT NULL, step INTEGER NOT NULL CHECK (step IN (1, 2)),
        intent_id TEXT NOT NULL, old_sl REAL NOT NULL, new_sl REAL NOT NULL,
        price REAL NOT NULL, at REAL NOT NULL, PRIMARY KEY (ticket, step))""",
)
_COLUMNS: Final[str] = ("action_id, cycle_id, session_id, agent, command, ticket, intent_id,"
                        " payload, status, detail, created_at, updated_at")
_COLUMN_COUNT: Final[int] = 12
_INSERT_SQL: Final[str] = (f"INSERT INTO v6_actions ({_COLUMNS})"
                           f" VALUES ({', '.join('?' * _COLUMN_COUNT)})")
_SELECT_SQL: Final[str] = f"SELECT {_COLUMNS} FROM v6_actions"
_BY_ID_SQL: Final[str] = f"{_SELECT_SQL} WHERE action_id = ?"
_LATEST_SQL: Final[str] = (f"{_SELECT_SQL} WHERE session_id = ?"
                           " ORDER BY created_at DESC, action_id LIMIT 1")
_RECENT_SQL: Final[str] = f"{_SELECT_SQL} ORDER BY created_at DESC, action_id LIMIT ?"
_MARK_SQL: Final[str] = ("UPDATE v6_actions SET status = ?, detail = ?, updated_at = ?"
                         " WHERE action_id = ? AND status = 'PUBLISHED'")
_STEP_SQL: Final[str] = ("INSERT OR IGNORE INTO v6_plan_steps"
                         " (ticket, step, intent_id, old_sl, new_sl, price, at)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?)")

WriteTx = Callable[[], AbstractContextManager]
Fetch = Callable[[str, tuple[object, ...]], list[tuple]]


def _finite(*values: object) -> bool:
    return all(isinstance(value, (int, float)) and not isinstance(value, bool)
               and math.isfinite(value) for value in values)


@dataclass(frozen=True)
class ActionRow:
    """One v6_actions row: what the adapter asked the EA to do with a V6 ticket."""

    action_id: str
    cycle_id: str
    session_id: str
    agent: str
    command: str
    ticket: int
    intent_id: str = ""
    payload: Mapping[str, object] = field(default_factory=dict)
    status: str = "PUBLISHED"
    detail: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        problems = self._problems()
        if problems:
            raise ValueError("invalid action: " + "; ".join(problems))

    def _problems(self) -> list[str]:
        checks = (
            (isinstance(self.action_id, str) and bool(_ID_RE.match(self.action_id)),
             "action_id must be 12 lowercase base32 characters"),
            (self.command in ACTION_COMMANDS, "unknown command"),
            (isinstance(self.ticket, int) and not isinstance(self.ticket, bool)
             and self.ticket > 0, "ticket must be a positive integer"),
            (self.status in ACTION_STATUSES, "unknown status"),
            (len(self.detail) <= MAX_TEXT_CHARS, "detail is too long"),
            (_finite(self.created_at, self.updated_at), "times must be finite"),
            (len(self.payload_json) <= MAX_PAYLOAD_BYTES, "payload is too large"),
        )
        return [message for ok, message in checks if not ok]

    @property
    def payload_json(self) -> str:
        return json.dumps(dict(self.payload), sort_keys=True, allow_nan=False)


def _row(values: tuple) -> ActionRow:
    (action_id, cycle_id, session_id, agent, command, ticket, intent_id, payload, status,
     detail, created_at, updated_at) = values
    return ActionRow(action_id=action_id, cycle_id=cycle_id, session_id=session_id,
                     agent=agent, command=command, ticket=int(ticket), intent_id=intent_id,
                     payload=json.loads(payload), status=status, detail=detail,
                     created_at=float(created_at), updated_at=float(updated_at))


class ActionStore:
    """Reads and writes v6_actions and v6_plan_steps. Blocking: call it in a thread."""

    def __init__(self, write: WriteTx, fetchall: Fetch) -> None:
        self._write = write
        self._fetchall = fetchall

    def insert(self, row: ActionRow) -> None:
        values = (row.action_id, row.cycle_id, row.session_id, row.agent, row.command,
                  row.ticket, row.intent_id, row.payload_json, row.status, row.detail,
                  row.created_at, row.updated_at)
        with self._write() as conn:
            conn.execute(_INSERT_SQL, values)

    def mark(self, action_id: str, status: str, detail: str, at: float) -> bool:
        """PUBLISHED -> a final status, once; False when the action is gone or final."""
        if status not in FINAL_ACTION_STATUSES:
            raise ValueError(f"{status} is not a final action status")
        with self._write() as conn:
            changed = conn.execute(_MARK_SQL, (status, detail[:MAX_TEXT_CHARS], at,
                                               action_id)).rowcount
        return changed == 1

    def get(self, action_id: str) -> ActionRow | None:
        rows = self._fetchall(_BY_ID_SQL, (action_id,))
        return _row(rows[0]) if rows else None

    def latest(self, session_id: str) -> ActionRow | None:
        """The newest action of a session (the packet shows it to the agent)."""
        rows = self._fetchall(_LATEST_SQL, (session_id,))
        return _row(rows[0]) if rows else None

    def recent(self, limit: int = 50) -> tuple[ActionRow, ...]:
        bounded = max(1, min(int(limit), MAX_LIST_LIMIT))
        return tuple(_row(values) for values in self._fetchall(_RECENT_SQL, (bounded,)))

    def record_step(self, intent_id: str, ticket: int, step: int, old_sl: float,
                    new_sl: float, price: float, at: float) -> bool:
        """An SL+ step the EA executed; False when that step was already recorded."""
        with self._write() as conn:
            changed = conn.execute(_STEP_SQL, (ticket, step, intent_id, old_sl, new_sl,
                                               price, at)).rowcount
        return changed == 1
