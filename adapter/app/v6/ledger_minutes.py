"""
v6_minute_cycles (spec section 4.6): one row per closed M1 bar the minute worker processed.

`MinuteStore` shares the connection and lock of `LedgerCycles`; reach it as
`ledger_cycles.minutes`. A row says what became of the minute: SKIPPED (the reason says
why no packet was offered), ANSWERED (an agent's decision was accepted) or UNANSWERED
(timeout, or closed by a newer packet), with the adapter's own processing time. The
minute worker prunes rows older than three days.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from typing import Final

MINUTE_OUTCOMES: Final[tuple[str, ...]] = ("SKIPPED", "ANSWERED", "UNANSWERED")
MAX_TEXT_CHARS: Final[int] = 120
MAX_LIST_LIMIT: Final[int] = 500
P95: Final[float] = 0.95
ENTER_ACTION: Final[str] = "ENTER"
MANAGE_PREFIX: Final[str] = "MANAGE:"
_OUTCOME_LIST: Final[str] = ", ".join(f"'{outcome}'" for outcome in MINUTE_OUTCOMES)

# Every statement is repeatable; LedgerCycles runs them on open.
MINUTE_SCHEMA_DDL: Final[tuple[str, ...]] = (
    f"""CREATE TABLE IF NOT EXISTS v6_minute_cycles (
        cycle_id TEXT NOT NULL PRIMARY KEY, bar_open_epoch INTEGER NOT NULL,
        session_id TEXT NOT NULL DEFAULT '', state TEXT NOT NULL,
        outcome TEXT NOT NULL CHECK (outcome IN ({_OUTCOME_LIST})),
        reason TEXT NOT NULL DEFAULT '', action TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT '', hold_reason TEXT NOT NULL DEFAULT '',
        agent TEXT NOT NULL DEFAULT '', latency_ms INTEGER NOT NULL DEFAULT 0,
        tier0_ms INTEGER NOT NULL DEFAULT 0, intent_id TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_v6_minute_cycles_bar ON v6_minute_cycles(bar_open_epoch)",
)
_COLUMNS: Final[str] = ("cycle_id, bar_open_epoch, state, outcome, created_at, session_id,"
                        " reason, action, status, hold_reason, agent, latency_ms, tier0_ms,"
                        " intent_id")
_INSERT_SQL: Final[str] = (f"INSERT OR IGNORE INTO v6_minute_cycles ({_COLUMNS})"
                           f" VALUES ({', '.join('?' * 14)})")
_RECENT_SQL: Final[str] = (f"SELECT {_COLUMNS} FROM v6_minute_cycles"
                           " ORDER BY bar_open_epoch DESC LIMIT ?")
_SINCE_SQL: Final[str] = ("SELECT outcome, action, tier0_ms FROM v6_minute_cycles"
                          " WHERE bar_open_epoch >= ?")
_PRUNE_SQL: Final[str] = "DELETE FROM v6_minute_cycles WHERE bar_open_epoch < ?"

WriteTx = Callable[[], AbstractContextManager]
Fetch = Callable[[str, tuple[object, ...]], list[tuple]]


@dataclass(frozen=True)
class MinuteRow:
    """What the minute worker did with one closed M1 bar."""

    cycle_id: str
    bar_open_epoch: int
    state: str
    outcome: str
    created_at: float
    session_id: str = ""
    reason: str = ""
    action: str = ""
    status: str = ""
    hold_reason: str = ""
    agent: str = ""
    latency_ms: int = 0
    tier0_ms: int = 0
    intent_id: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in MINUTE_OUTCOMES:
            raise ValueError(f"unknown minute outcome {self.outcome[:20]!r}")
        if not math.isfinite(self.created_at) or min(self.latency_ms, self.tier0_ms) < 0:
            raise ValueError("a minute row needs a finite time and non-negative durations")
        for name in ("reason", "status", "hold_reason"):
            object.__setattr__(self, name, getattr(self, name)[:MAX_TEXT_CHARS])

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class MinuteStats:
    processed: int = 0
    offered: int = 0
    answered: int = 0
    entries: int = 0
    manages: int = 0
    tier0_p95_ms: int = 0

    @property
    def answered_pct(self) -> float | None:
        return None if self.offered == 0 else round(100.0 * self.answered / self.offered, 1)

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "answered_pct": self.answered_pct}


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(P95 * len(ordered)) - 1)]


class MinuteStore:
    """Reads and writes v6_minute_cycles. Blocking: call it in a thread."""

    def __init__(self, write: WriteTx, fetchall: Fetch) -> None:
        self._write = write
        self._fetchall = fetchall

    def record(self, row: MinuteRow) -> bool:
        """False when this minute was recorded already."""
        values = (row.cycle_id, row.bar_open_epoch, row.state, row.outcome, row.created_at,
                  row.session_id, row.reason, row.action, row.status, row.hold_reason,
                  row.agent, row.latency_ms, row.tier0_ms, row.intent_id)
        with self._write() as conn:
            return conn.execute(_INSERT_SQL, values).rowcount == 1

    def recent(self, limit: int = 20) -> tuple[MinuteRow, ...]:
        bounded = max(1, min(int(limit), MAX_LIST_LIMIT))
        return tuple(MinuteRow(*values) for values in self._fetchall(_RECENT_SQL, (bounded,)))

    def stats(self, since_epoch: int) -> MinuteStats:
        rows = self._fetchall(_SINCE_SQL, (int(since_epoch),))
        offered = [row for row in rows if row[0] != "SKIPPED"]
        return MinuteStats(
            processed=len(rows), offered=len(offered),
            answered=sum(1 for row in offered if row[0] == "ANSWERED"),
            entries=sum(1 for row in offered if row[1] == ENTER_ACTION),
            manages=sum(1 for row in offered if str(row[1]).startswith(MANAGE_PREFIX)),
            tier0_p95_ms=_p95([int(row[2]) for row in rows]))

    def prune(self, older_than_epoch: int) -> int:
        with self._write() as conn:
            return conn.execute(_PRUNE_SQL, (int(older_than_epoch),)).rowcount
