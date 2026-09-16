"""
Phase 2 persistence: cycles, agent views, candidates, breakers and sessions.

Same SQLite file as `LedgerV6`, own connection (WAL, busy_timeout), one lock,
bound parameters only. Records returned here are frozen; JSON columns refuse
credential-shaped keys exactly like the V6 control log does.
"""

from __future__ import annotations

import json
import logging
import math
import re
import secrets
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from os import PathLike
from threading import Lock
from types import MappingProxyType
from typing import Final, Literal

from .cycle_types import CandidateAssessment, CycleResult, ViewRecord
from .ledger_cycles_schema import (
    CYCLE_SCHEMA_DDL, AgentViewRecord, BreakerRecord, CandidateRecord, CycleRecord, CycleSummary,
    CycleWithViews, LabelStat, SessionRecord, SessionStart,
)
from .ledger_v6 import BUSY_TIMEOUT_MS, _find_secret_key  # shared credential-key rule

logger = logging.getLogger(__name__)

BreakerScope = Literal["daily", "weekly", "monthly"]
LabelStatus = Literal["pending", "labeled", "unfillable"]
# "data_gap": the bars the outcome depends on are missing for good (learning.label_job).
LabelOutcome = Literal["tp", "sl", "time", "unfilled", "data_gap"]
BREAKER_SCOPES: Final[frozenset[str]] = frozenset({"daily", "weekly", "monthly"})
LABELED_OUTCOMES: Final[frozenset[str]] = frozenset({"tp", "sl", "time"})
UNFILLABLE_OUTCOMES: Final[frozenset[str]] = frozenset({"unfilled", "data_gap"})
MAX_SUMMARY_CHARS: Final[int] = 256_000
MAX_FEATURES_CHARS: Final[int] = 16_000
MAX_KEY_CHARS: Final[int] = 64
MAX_LIST_LIMIT: Final[int] = 1000
SESSION_ID_BYTES: Final[int] = 6
# Upper bound for "no upper bound" epoch filters (far beyond any real bar time).
EPOCH_UNBOUNDED: Final[int] = 2**62
TRADING_DAY_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Stored separately in v6_agent_views; also carries token counters whose key
# names the credential rule would (rightly) refuse.
_SUMMARY_EXCLUDED: Final[frozenset[str]] = frozenset({"view_records"})

_CYCLE_COLS: Final[str] = (
    "cycle_id, snapshot_id, bar_open_epoch, status, hold_reason, backend, provider,"
    " provider_status, session_id, total_ms, summary_json, created_at")
_VIEW_COLS: Final[str] = (
    "cycle_id, role, source, view_json, error_code, latency_ms, model, tokens_in, tokens_out,"
    " cost_usd, created_at")
_CANDIDATE_COLS: Final[str] = (
    "candidate_id, cycle_id, setup, side, entry, invalidation, stop, target, bar_t,"
    " features_json, verdict, label_status, outcome, outcome_r, labeled_at, available_from")
_BREAKER_COLS: Final[str] = (
    "scope, period_key, tripped, reason, tripped_at, reset_at, reset_by")
_SESSION_COLS: Final[str] = (
    "session_id, trading_day, backend, mode, started_at, stopped_at, stop_reason, armed")


def encode_json(value: Mapping[str, object], max_chars: int) -> str:
    """Canonical JSON; refuses credential-like keys, NaN and oversize documents."""
    secret_key = _find_secret_key(value)
    if secret_key is not None:
        raise ValueError(f"JSON key {secret_key[:32]!r} looks like a credential")
    try:
        encoded = json.dumps(dict(value), sort_keys=True, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"value is not JSON-serialisable: {exc}") from exc
    if len(encoded) > max_chars:
        raise ValueError(f"JSON document exceeds {max_chars} characters")
    return encoded


def _key(name: str, value: str) -> str:
    if not value or len(value) > MAX_KEY_CHARS:
        raise ValueError(f"{name} must be 1-{MAX_KEY_CHARS} characters")
    return value


def _limit(limit: int) -> int:
    if not 1 <= limit <= MAX_LIST_LIMIT:
        raise ValueError(f"limit must be within 1-{MAX_LIST_LIMIT}")
    return limit


def _cycle_row(result: CycleResult, created_at: float) -> tuple[object, ...]:
    summary = {k: v for k, v in result.to_summary().items() if k not in _SUMMARY_EXCLUDED}
    return (
        result.cycle_id, result.snapshot_id, result.bar_open_epoch, result.status,
        None if result.hold_reason is None else str(result.hold_reason), result.backend,
        result.provider, result.provider_status, result.session_id, result.timings.total_ms,
        encode_json(summary, MAX_SUMMARY_CHARS), created_at)


def _view_row(cycle_id: str, record: ViewRecord, created_at: float) -> tuple[object, ...]:
    view_json = None if record.view is None else encode_json(
        record.view.model_dump(mode="json"), MAX_SUMMARY_CHARS)
    return (cycle_id, record.role, _key("source", record.source), view_json, record.error_code,
            record.latency_ms, record.model, record.tokens_in, record.tokens_out,
            record.cost_usd, created_at)


def _candidate_row(cycle_id: str, item: CandidateAssessment) -> tuple[object, ...]:
    c = item.candidate
    return (c.candidate_id, cycle_id, c.setup, c.side, c.entry, c.invalidation, item.stop,
            item.target, c.bar_t, encode_json(c.features, MAX_FEATURES_CHARS), item.verdict,
            "pending", None, None, None, item.available_from)


def _check_label(label_status: str, outcome: str, outcome_r: float | None) -> None:
    if label_status == "labeled":
        if outcome not in LABELED_OUTCOMES or outcome_r is None or not math.isfinite(outcome_r):
            raise ValueError("a labeled candidate needs outcome tp/sl/time and a finite outcome_r")
    elif label_status == "unfillable":
        if outcome not in UNFILLABLE_OUTCOMES or outcome_r is not None:
            raise ValueError("an unfillable candidate has outcome 'unfilled' or 'data_gap'"
                             " and no outcome_r")
    else:
        raise ValueError("label_status must be 'labeled' or 'unfillable'")


class LedgerCycles:
    """Synchronous store; async callers wrap each call in `asyncio.to_thread`."""

    def __init__(self, path: str | PathLike[str]) -> None:
        self.path = str(path)
        self._lock = Lock()
        self._closed = False
        self._conn = sqlite3.connect(self.path, check_same_thread=False,
                                     isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000)
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            # PRAGMA values cannot be bound; this one is a module constant.
            self._conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_MS)}")
            for ddl in CYCLE_SCHEMA_DDL:
                self._conn.execute(ddl)
        except sqlite3.Error:
            logger.error("ledger_cycles: schema initialisation failed for %s", self.path)
            self._conn.close()
            raise

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def _fetchall(self, sql: str, params: tuple[object, ...]) -> list[tuple]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _execute(self, sql: str, params: tuple[object, ...]) -> int:
        with self._write() as conn:
            return conn.execute(sql, params).rowcount

    # --- cycles (runtime) ----------------------------------------------------
    def record_cycle(self, result: CycleResult, created_at: float) -> bool:
        """Store the cycle, its view attempts and its candidates atomically.

        False when the cycle id already exists (nothing else is written then).
        """
        cycle = _cycle_row(result, created_at)
        views = [_view_row(result.cycle_id, rec, created_at) for rec in result.view_records]
        candidates = [_candidate_row(result.cycle_id, item) for item in result.candidates]
        with self._write() as conn:
            inserted = conn.execute(
                f"INSERT INTO v6_cycles ({_CYCLE_COLS}) VALUES ({_marks(12)})"
                " ON CONFLICT(cycle_id) DO NOTHING", cycle).rowcount == 1
            if not inserted:
                return False
            conn.executemany(f"INSERT INTO v6_agent_views ({_VIEW_COLS}) VALUES ({_marks(11)})",
                             views)
            conn.executemany(f"INSERT INTO v6_candidates ({_CANDIDATE_COLS}) VALUES ({_marks(16)})"
                             " ON CONFLICT(candidate_id) DO NOTHING", candidates)
        return True

    def get_cycle(self, cycle_id: str) -> CycleRecord | None:
        rows = self._fetchall(f"SELECT {_CYCLE_COLS} FROM v6_cycles WHERE cycle_id = ?",
                              (cycle_id,))
        return CycleRecord(*rows[0]) if rows else None

    def recent_cycles(self, limit: int = 50, before_bar_epoch: int | None = None,
                      ) -> tuple[CycleRecord, ...]:
        """Newest first by bar open time; `before_bar_epoch` pages backwards."""
        before = EPOCH_UNBOUNDED if before_bar_epoch is None else before_bar_epoch
        rows = self._fetchall(
            f"SELECT {_CYCLE_COLS} FROM v6_cycles WHERE bar_open_epoch < ?"
            " ORDER BY bar_open_epoch DESC, created_at DESC LIMIT ?", (before, _limit(limit)))
        return tuple(CycleRecord(*row) for row in rows)

    def views_for_cycle(self, cycle_id: str) -> tuple[AgentViewRecord, ...]:
        rows = self._fetchall(
            f"SELECT {_VIEW_COLS} FROM v6_agent_views WHERE cycle_id = ? ORDER BY id",
            (cycle_id,))
        return tuple(AgentViewRecord(*row) for row in rows)

    def recent_cycles_with_views(self, limit: int = 20) -> tuple[CycleWithViews, ...]:
        cycles = self.recent_cycles(limit)
        if not cycles:
            return ()
        ids = tuple(c.cycle_id for c in cycles)
        rows = self._fetchall(
            f"SELECT {_VIEW_COLS} FROM v6_agent_views WHERE cycle_id IN ({_marks(len(ids))})"
            " ORDER BY id", ids)
        grouped: dict[str, list[AgentViewRecord]] = {cid: [] for cid in ids}
        for row in rows:
            grouped[row[0]].append(AgentViewRecord(*row))
        return tuple(CycleWithViews(c, tuple(grouped[c.cycle_id])) for c in cycles)

    def cycle_summary(self, since_bar_epoch: int,
                      until_bar_epoch: int = EPOCH_UNBOUNDED) -> CycleSummary:
        """Counts for cycles whose bar opened in [since, until)."""
        rows = self._fetchall(
            "SELECT status, hold_reason, COUNT(*) FROM v6_cycles"
            " WHERE bar_open_epoch >= ? AND bar_open_epoch < ? GROUP BY status, hold_reason",
            (since_bar_epoch, until_bar_epoch))
        by_status: dict[str, int] = {}
        by_reason: dict[str, int] = {}
        for status, reason, count in rows:
            by_status[status] = by_status.get(status, 0) + count
            if reason is not None:
                by_reason[reason] = by_reason.get(reason, 0) + count
        return CycleSummary(
            total=sum(by_status.values()), by_status=MappingProxyType(by_status),
            by_hold_reason=MappingProxyType(by_reason),
            shadow_entries=by_status.get("ENTER_SHADOW", 0))

    def hold_reason_histogram(self, since_bar_epoch: int) -> Mapping[str, int]:
        return self.cycle_summary(since_bar_epoch).by_hold_reason

    # --- candidates (labeler, dashboard) -----------------------------------
    def candidates_for_cycle(self, cycle_id: str) -> tuple[CandidateRecord, ...]:
        rows = self._fetchall(
            f"SELECT {_CANDIDATE_COLS} FROM v6_candidates WHERE cycle_id = ?"
            " ORDER BY candidate_id", (cycle_id,))
        return tuple(CandidateRecord(*row) for row in rows)

    def pending_candidates(self, available_by_epoch: int,
                           limit: int = 500) -> tuple[CandidateRecord, ...]:
        """Unlabeled candidates whose barrier has resolved by `available_by_epoch`, oldest first."""
        rows = self._fetchall(
            f"SELECT {_CANDIDATE_COLS} FROM v6_candidates WHERE label_status = 'pending'"
            " AND available_from <= ? ORDER BY available_from, candidate_id LIMIT ?",
            (available_by_epoch, _limit(limit)))
        return tuple(CandidateRecord(*row) for row in rows)

    def write_label(self, candidate_id: str, *, label_status: LabelStatus, outcome: LabelOutcome,
                    outcome_r: float | None, labeled_at: float) -> bool:
        """Label a pending candidate once; False if unknown or already labeled."""
        _check_label(label_status, outcome, outcome_r)
        return self._execute(
            "UPDATE v6_candidates SET label_status = ?, outcome = ?, outcome_r = ?,"
            " labeled_at = ? WHERE candidate_id = ? AND label_status = 'pending'",
            (label_status, outcome, outcome_r, labeled_at, candidate_id)) == 1

    def candidate_label_stats(self, since_bar_t: int) -> tuple[LabelStat, ...]:
        rows = self._fetchall(
            "SELECT setup, verdict, label_status, outcome, COUNT(*), AVG(outcome_r)"
            " FROM v6_candidates WHERE bar_t >= ?"
            " GROUP BY setup, verdict, label_status, outcome"
            " ORDER BY setup, verdict, label_status, outcome", (since_bar_t,))
        return tuple(LabelStat(*row) for row in rows)

    # --- breakers ------------------------------------------------------------
    def trip_breaker(self, scope: BreakerScope, period_key: str, reason: str,
                     tripped_at: float) -> BreakerRecord:
        """Trip (or keep tripped) a breaker; the first trip's reason and time are kept."""
        _require_scope(scope)
        params = (scope, _key("period_key", period_key), _key("reason", reason), tripped_at)
        with self._write() as conn:
            changed = conn.execute(
                f"INSERT INTO v6_breaker_state ({_BREAKER_COLS}) VALUES (?, ?, 1, ?, ?, NULL, NULL)"
                " ON CONFLICT(scope, period_key) DO UPDATE SET tripped = 1,"
                " reason = excluded.reason, tripped_at = excluded.tripped_at,"
                " reset_at = NULL, reset_by = NULL WHERE v6_breaker_state.tripped = 0",
                params).rowcount == 1
        if changed:
            logger.warning("v6 breaker tripped: %s %s (%s)", scope, period_key, reason)
        record = self.get_breaker(scope, period_key)
        if record is None:
            raise sqlite3.DatabaseError("breaker row vanished after the write")
        return record

    def reset_breaker(self, scope: BreakerScope, period_key: str, reset_by: str,
                      reset_at: float) -> bool:
        """Manual reset only; False when the breaker was not tripped."""
        _require_scope(scope)
        return self._execute(
            "UPDATE v6_breaker_state SET tripped = 0, reset_at = ?, reset_by = ?"
            " WHERE scope = ? AND period_key = ? AND tripped = 1",
            (reset_at, _key("reset_by", reset_by), scope, period_key)) == 1

    def get_breaker(self, scope: BreakerScope, period_key: str) -> BreakerRecord | None:
        rows = self._fetchall(
            f"SELECT {_BREAKER_COLS} FROM v6_breaker_state WHERE scope = ? AND period_key = ?",
            (scope, period_key))
        return BreakerRecord.from_row(rows[0]) if rows else None

    def active_breakers(self) -> tuple[BreakerRecord, ...]:
        """Tripped breakers of any period: they survive restarts and period rollover."""
        rows = self._fetchall(
            f"SELECT {_BREAKER_COLS} FROM v6_breaker_state WHERE tripped = 1"
            " ORDER BY tripped_at", ())
        return tuple(BreakerRecord.from_row(row) for row in rows)

    # --- daily sessions ------------------------------------------------------
    def start_session(self, *, trading_day: str, backend: str, mode: str, started_at: float,
                      armed: bool = False) -> SessionStart:
        """Open a session, or return the one already active (idempotent start).

        `created` is False when another session was already open; that session
        is returned unchanged, even if its trading_day differs.
        """
        if not TRADING_DAY_PATTERN.match(trading_day):
            raise ValueError("trading_day must be YYYY-MM-DD")
        session_id = secrets.token_hex(SESSION_ID_BYTES)
        params = (session_id, trading_day, _key("backend", backend), _key("mode", mode),
                  started_at, int(armed))
        with self._write() as conn:
            created = conn.execute(
                f"INSERT INTO v6_sessions ({_SESSION_COLS}) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?)"
                " ON CONFLICT DO NOTHING", params).rowcount == 1
        active = self.active_session()
        if active is None:
            raise sqlite3.DatabaseError("no active session after start")
        if created:
            logger.info("v6 session started: %s (%s, %s)", session_id, backend, mode)
        return SessionStart(session=active, created=created)

    def stop_session(self, session_id: str, *, stopped_at: float, reason: str) -> bool:
        """Close an active session (also disarms it); False if not active."""
        return self._execute(
            "UPDATE v6_sessions SET stopped_at = ?, stop_reason = ?, armed = 0"
            " WHERE session_id = ? AND stopped_at IS NULL",
            (stopped_at, _key("reason", reason), session_id)) == 1

    def set_session_armed(self, session_id: str, armed: bool) -> bool:
        return self._execute(
            "UPDATE v6_sessions SET armed = ? WHERE session_id = ? AND stopped_at IS NULL",
            (int(armed), session_id)) == 1

    def active_session(self) -> SessionRecord | None:
        rows = self._fetchall(
            f"SELECT {_SESSION_COLS} FROM v6_sessions WHERE stopped_at IS NULL", ())
        return SessionRecord.from_row(rows[0]) if rows else None

    def sessions_for_day(self, trading_day: str) -> tuple[SessionRecord, ...]:
        rows = self._fetchall(
            f"SELECT {_SESSION_COLS} FROM v6_sessions WHERE trading_day = ?"
            " ORDER BY started_at", (trading_day,))
        return tuple(SessionRecord.from_row(row) for row in rows)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()


def _marks(count: int) -> str:
    return ", ".join("?" * count)


def _require_scope(scope: str) -> None:
    if scope not in BREAKER_SCOPES:
        raise ValueError(f"unknown breaker scope {str(scope)[:16]!r}")
