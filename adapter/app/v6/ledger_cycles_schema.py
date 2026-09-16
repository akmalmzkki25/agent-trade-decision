"""DDL and frozen row records for `app.v6.ledger_cycles`.

Every record's field order is the SELECT column order used by the ledger.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

# Every statement is repeatable; the ledger runs them all on open.
CYCLE_SCHEMA_DDL: Final[tuple[str, ...]] = (
    """CREATE TABLE IF NOT EXISTS v6_cycles (
        cycle_id TEXT NOT NULL PRIMARY KEY, snapshot_id TEXT NOT NULL,
        bar_open_epoch INTEGER NOT NULL, status TEXT NOT NULL, hold_reason TEXT,
        backend TEXT NOT NULL, provider TEXT NOT NULL, provider_status TEXT NOT NULL,
        session_id TEXT, total_ms INTEGER NOT NULL DEFAULT 0,
        summary_json TEXT NOT NULL, created_at REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_v6_cycles_bar_open ON v6_cycles(bar_open_epoch)",
    """CREATE TABLE IF NOT EXISTS v6_agent_views (
        id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_id TEXT NOT NULL, role TEXT NOT NULL,
        source TEXT NOT NULL, view_json TEXT, error_code TEXT NOT NULL DEFAULT '',
        latency_ms INTEGER NOT NULL DEFAULT 0, model TEXT NOT NULL DEFAULT '',
        tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
        cost_usd REAL NOT NULL DEFAULT 0, created_at REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_v6_agent_views_cycle ON v6_agent_views(cycle_id)",
    """CREATE TABLE IF NOT EXISTS v6_candidates (
        candidate_id TEXT NOT NULL PRIMARY KEY, cycle_id TEXT NOT NULL,
        setup TEXT NOT NULL, side TEXT NOT NULL, entry REAL NOT NULL,
        invalidation REAL NOT NULL, stop REAL NOT NULL, target REAL NOT NULL,
        bar_t INTEGER NOT NULL, features_json TEXT NOT NULL, verdict TEXT NOT NULL,
        label_status TEXT NOT NULL DEFAULT 'pending'
            CHECK (label_status IN ('pending', 'labeled', 'unfillable')),
        outcome TEXT, outcome_r REAL, labeled_at REAL, available_from INTEGER NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_v6_candidates_cycle ON v6_candidates(cycle_id)",
    "CREATE INDEX IF NOT EXISTS idx_v6_candidates_pending"
    " ON v6_candidates(label_status, available_from)",
    """CREATE TABLE IF NOT EXISTS v6_breaker_state (
        scope TEXT NOT NULL CHECK (scope IN ('daily', 'weekly', 'monthly')),
        period_key TEXT NOT NULL, tripped INTEGER NOT NULL CHECK (tripped IN (0, 1)),
        reason TEXT NOT NULL, tripped_at REAL NOT NULL, reset_at REAL, reset_by TEXT,
        PRIMARY KEY (scope, period_key))""",
    """CREATE TABLE IF NOT EXISTS v6_sessions (
        session_id TEXT NOT NULL PRIMARY KEY, trading_day TEXT NOT NULL,
        backend TEXT NOT NULL, mode TEXT NOT NULL, started_at REAL NOT NULL,
        stopped_at REAL, stop_reason TEXT, armed INTEGER NOT NULL DEFAULT 0)""",
    # At most one open session, enforced by the database itself.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_v6_sessions_one_active"
    " ON v6_sessions((stopped_at IS NULL)) WHERE stopped_at IS NULL",
    "CREATE INDEX IF NOT EXISTS idx_v6_sessions_day ON v6_sessions(trading_day)",
)


@dataclass(frozen=True)
class CycleRecord:
    cycle_id: str
    snapshot_id: str
    bar_open_epoch: int
    status: str
    hold_reason: str | None
    backend: str
    provider: str
    provider_status: str
    session_id: str | None
    total_ms: int
    summary_json: str
    created_at: float

    def summary(self) -> dict[str, object]:
        """A fresh dict parsed from `summary_json` (CycleResult.to_summary minus views)."""
        return json.loads(self.summary_json)


@dataclass(frozen=True)
class AgentViewRecord:
    cycle_id: str
    role: str
    source: str
    view_json: str | None
    error_code: str
    latency_ms: int
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    created_at: float

    def view(self) -> dict[str, object] | None:
        return None if self.view_json is None else json.loads(self.view_json)


@dataclass(frozen=True)
class CycleWithViews:
    cycle: CycleRecord
    views: tuple[AgentViewRecord, ...]


@dataclass(frozen=True)
class CycleSummary:
    total: int
    by_status: Mapping[str, int]
    by_hold_reason: Mapping[str, int]
    shadow_entries: int


@dataclass(frozen=True)
class CandidateRecord:
    candidate_id: str
    cycle_id: str
    setup: str
    side: str
    entry: float
    invalidation: float
    stop: float
    target: float
    bar_t: int
    features_json: str
    verdict: str
    label_status: str
    outcome: str | None
    outcome_r: float | None
    labeled_at: float | None
    available_from: int

    def features(self) -> dict[str, float]:
        return json.loads(self.features_json)


@dataclass(frozen=True)
class LabelStat:
    setup: str
    verdict: str
    label_status: str
    outcome: str | None
    count: int
    mean_r: float | None


@dataclass(frozen=True)
class BreakerRecord:
    scope: str
    period_key: str
    tripped: bool
    reason: str
    tripped_at: float
    reset_at: float | None
    reset_by: str | None

    @classmethod
    def from_row(cls, row: tuple) -> "BreakerRecord":
        scope, period_key, tripped, reason, tripped_at, reset_at, reset_by = row
        return cls(scope, period_key, bool(tripped), reason, tripped_at, reset_at, reset_by)


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    trading_day: str
    backend: str
    mode: str
    started_at: float
    stopped_at: float | None
    stop_reason: str | None
    armed: bool

    @property
    def is_active(self) -> bool:
        return self.stopped_at is None

    @classmethod
    def from_row(cls, row: tuple) -> "SessionRecord":
        *head, armed = row
        return cls(*head, armed=bool(armed))


@dataclass(frozen=True)
class SessionStart:
    session: SessionRecord
    created: bool
