"""
V6 persistence on the shared adapter SQLite file.

V6 opens its own connection so a slow V6 write never waits on the V1-V5
`Ledger` lock, and so the async runtime can call these methods through
`asyncio.to_thread`. Every value reaches SQL as a bound parameter; the only
identifiers in statements are the literals in this module.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import astuple, dataclass
from os import PathLike
from threading import Lock
from typing import Final

from .clock import Clock, SystemClock
from .schemas.intent import ExecutionReport, PollRequest
from .schemas.snapshot import V6Snapshot
from .types import TIMEFRAME_SECONDS, Bar

logger = logging.getLogger(__name__)

BUSY_TIMEOUT_MS: Final[int] = 5000
MAX_LOAD_BARS: Final[int] = 5000
MAX_LABEL_CHARS: Final[int] = 64
MAX_CONTROL_DETAIL_CHARS: Final[int] = 4096
SECONDS_PER_MINUTE: Final[int] = 60
SECONDS_PER_DAY: Final[int] = 86_400
# Control-log details are kept indefinitely and shown to operators, so
# anything shaped like a credential is refused rather than written.
SECRET_KEY_FRAGMENTS: Final[tuple[str, ...]] = (
    "token", "secret", "password", "passwd", "api_key", "apikey", "authorization", "cookie")

# Later phases append their tables here; every statement must be repeatable.
SCHEMA_DDL: Final[tuple[str, ...]] = (
    """CREATE TABLE IF NOT EXISTS v6_bars (
        tf TEXT NOT NULL, t INTEGER NOT NULL, o REAL NOT NULL, h REAL NOT NULL,
        l REAL NOT NULL, c REAL NOT NULL, tv INTEGER NOT NULL DEFAULT 0,
        spr INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (tf, t)) WITHOUT ROWID""",
    # payload_json becomes NULL once pruned; the other columns stay as the summary.
    """CREATE TABLE IF NOT EXISTS v6_snapshots (
        snapshot_id TEXT NOT NULL PRIMARY KEY,
        received_at REAL NOT NULL, bar_open_epoch INTEGER NOT NULL,
        login TEXT, trade_mode TEXT, server TEXT,
        equity REAL, balance REAL, spread_points INTEGER, clock_skew_s REAL,
        payload_sha256 TEXT NOT NULL, payload_json TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_v6_snapshots_bar_open ON v6_snapshots(bar_open_epoch)",
    """CREATE TABLE IF NOT EXISTS v6_executions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        intent_id TEXT NOT NULL, status TEXT NOT NULL, reason_code TEXT NOT NULL,
        ticket INTEGER, retcode INTEGER, requested_price REAL, fill_price REAL,
        slippage_points REAL, spread_points INTEGER, latency_ms INTEGER,
        sent_at_epoch INTEGER NOT NULL, received_at REAL NOT NULL)""",
    # Leads with intent_id, so it also serves per-intent lookups. The EA outbox
    # re-sends a report verbatim; this index makes that retry idempotent.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_v6_executions_report "
    "ON v6_executions(intent_id, status, sent_at_epoch)",
    """CREATE TABLE IF NOT EXISTS v6_account_marks (
        minute_epoch INTEGER PRIMARY KEY,
        login TEXT, trade_mode TEXT, equity REAL, balance REAL,
        floating_pnl_v6 REAL, open_v6_positions INTEGER)""",
    """CREATE TABLE IF NOT EXISTS v6_control_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
        actor TEXT NOT NULL, action TEXT NOT NULL, detail_json TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_v6_control_log_ts ON v6_control_log(ts)",
    """CREATE TABLE IF NOT EXISTS v6_runtime_lock (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        holder TEXT NOT NULL, pid INTEGER NOT NULL, heartbeat_at REAL NOT NULL)""",
)
COLUMN_MIGRATIONS: Final[tuple[str, ...]] = ()

_UPSERT_BAR_SQL: Final[str] = (
    "INSERT INTO v6_bars (tf, t, o, h, l, c, tv, spr) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    " ON CONFLICT(tf, t) DO UPDATE SET o = excluded.o, h = excluded.h, l = excluded.l,"
    " c = excluded.c, tv = excluded.tv, spr = excluded.spr")
_LOAD_BARS_SQL: Final[str] = (
    "SELECT t, o, h, l, c, tv, spr FROM ("
    " SELECT t, o, h, l, c, tv, spr FROM v6_bars WHERE tf = ? AND t >= ?"
    " ORDER BY t DESC LIMIT ?) ORDER BY t ASC")
_COVERAGE_SQL: Final[str] = (
    "SELECT COUNT(*), MIN(t), MAX(t) FROM v6_bars WHERE tf = ? AND t >= ? AND t <= ?")
_FULL_DAYS_SQL: Final[str] = (
    "SELECT COUNT(*) FROM (SELECT t / ? AS day FROM v6_bars"
    " WHERE tf = ? AND t >= ? AND t <= ? GROUP BY day HAVING COUNT(*) >= ?)")
_INSERT_SNAPSHOT_SQL: Final[str] = (
    "INSERT INTO v6_snapshots (snapshot_id, received_at, bar_open_epoch, login, trade_mode,"
    " server, equity, balance, spread_points, clock_skew_s, payload_sha256, payload_json)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(snapshot_id) DO NOTHING")
_INSERT_EXECUTION_SQL: Final[str] = (
    "INSERT INTO v6_executions (intent_id, status, reason_code, ticket, retcode,"
    " requested_price, fill_price, slippage_points, spread_points, latency_ms,"
    " sent_at_epoch, received_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    " ON CONFLICT(intent_id, status, sent_at_epoch) DO NOTHING")
_INSERT_MARK_SQL: Final[str] = (
    "INSERT INTO v6_account_marks (minute_epoch, login, trade_mode, equity, balance,"
    " floating_pnl_v6, open_v6_positions) VALUES (?, ?, ?, ?, ?, ?, ?)"
    " ON CONFLICT(minute_epoch) DO NOTHING")
# One statement, so two processes racing for the lock cannot both win.
_ACQUIRE_LOCK_SQL: Final[str] = (
    "INSERT INTO v6_runtime_lock (id, holder, pid, heartbeat_at) VALUES (1, ?, ?, ?)"
    " ON CONFLICT(id) DO UPDATE SET holder = excluded.holder, pid = excluded.pid,"
    " heartbeat_at = excluded.heartbeat_at"
    " WHERE v6_runtime_lock.holder = excluded.holder OR v6_runtime_lock.heartbeat_at < ?")
_READ_LOCK_SQL: Final[str] = "SELECT holder, pid, heartbeat_at FROM v6_runtime_lock WHERE id = 1"


def require_timeframe(tf: str) -> str:
    if tf not in TIMEFRAME_SECONDS:
        raise ValueError(f"unknown timeframe {tf[:16]!r}")
    return tf


def _require_label(name: str, value: str) -> str:
    if not value or len(value) > MAX_LABEL_CHARS:
        raise ValueError(f"{name} must be 1-{MAX_LABEL_CHARS} characters")
    return value


def _find_secret_key(detail: Mapping[str, object]) -> str | None:
    for key, value in detail.items():
        if any(fragment in str(key).lower() for fragment in SECRET_KEY_FRAGMENTS):
            return str(key)
        nested = _find_secret_key(value) if isinstance(value, Mapping) else None
        if nested is not None:
            return nested
    return None


def _encode_detail(detail: Mapping[str, object]) -> str:
    secret_key = _find_secret_key(detail)
    if secret_key is not None:
        raise ValueError(f"control detail key {secret_key[:32]!r} looks like a credential")
    try:
        encoded = json.dumps(dict(detail), sort_keys=True, allow_nan=False)
    except TypeError as exc:
        raise ValueError(f"control detail is not JSON-serialisable: {exc}") from exc
    if len(encoded) > MAX_CONTROL_DETAIL_CHARS:
        raise ValueError(f"control detail exceeds {MAX_CONTROL_DETAIL_CHARS} characters")
    return encoded


@dataclass(frozen=True)
class SnapshotRecord:
    """One accepted snapshot; field order is the v6_snapshots column order."""

    snapshot_id: str
    received_at: float
    bar_open_epoch: int
    login: str
    trade_mode: str
    server: str
    equity: float
    balance: float
    spread_points: int
    clock_skew_s: float
    payload_sha256: str
    payload_json: str

    @classmethod
    def from_snapshot(cls, snapshot: V6Snapshot, payload: str | bytes,
                      received_at: float) -> "SnapshotRecord":
        """`payload` is the request body exactly as received; skew is receive minus send."""
        raw = payload.encode("utf-8") if isinstance(payload, str) else payload
        account = snapshot.account
        return cls(
            snapshot_id=snapshot.snapshot_id, received_at=received_at,
            bar_open_epoch=snapshot.bar_open_epoch, login=account.login,
            trade_mode=account.trade_mode, server=account.server,
            equity=account.equity, balance=account.balance,
            spread_points=snapshot.quote.spread_points,
            clock_skew_s=received_at - snapshot.sent_at_epoch,
            payload_sha256=hashlib.sha256(raw).hexdigest(), payload_json=raw.decode("utf-8"),
        )


@dataclass(frozen=True)
class AccountMark:
    observed_at: float
    login: str
    trade_mode: str
    equity: float
    balance: float
    floating_pnl_v6: float
    open_v6_positions: int

    @property
    def minute_epoch(self) -> int:
        return int(self.observed_at // SECONDS_PER_MINUTE) * SECONDS_PER_MINUTE

    @classmethod
    def from_poll(cls, poll: PollRequest, now: float) -> "AccountMark":
        return cls(
            observed_at=now, login=poll.login, trade_mode=poll.trade_mode,
            equity=poll.equity, balance=poll.balance,
            floating_pnl_v6=poll.floating_pnl_v6, open_v6_positions=poll.open_v6_positions,
        )


@dataclass(frozen=True)
class RuntimeLock:
    holder: str
    pid: int
    heartbeat_at: float


@dataclass(frozen=True)
class Coverage:
    """Bars in a window; `distinct_days` counts UTC days with enough bars."""

    count: int
    first_t: int | None
    last_t: int | None
    distinct_days: int


class LedgerV6:
    """Synchronous store; async callers wrap each call in `asyncio.to_thread`."""

    def __init__(self, path: str | PathLike[str], clock: Clock | None = None) -> None:
        self.path = str(path)
        self._clock: Clock = clock if clock is not None else SystemClock()
        # Reads take the lock too: the one connection is shared across threads
        # and interleaved cursors on it are not safe.
        self._lock = Lock()
        self._closed = False
        self._conn = sqlite3.connect(self.path, check_same_thread=False,
                                     isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000)
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            # PRAGMA values cannot be bound; this one is a module constant.
            self._conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_MS)}")
            self._init_schema()
        except sqlite3.Error:
            self._conn.close()
            raise

    def _init_schema(self) -> None:
        for ddl in SCHEMA_DDL:
            self._conn.execute(ddl)
        self._add_columns_if_missing(*COLUMN_MIGRATIONS)

    def _add_columns_if_missing(self, *ddl_statements: str) -> None:
        """Repeatable ADD COLUMN migrations. Only "duplicate column" is tolerated: a
        locked database, disk error or DDL typo must surface, not half-migrate."""
        for ddl in ddl_statements:
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError as exc:
                if "duplicate column" in str(exc).lower():
                    continue
                logger.error("ledger_v6 migration failed: %s (%s)", ddl, exc)
                raise

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """One IMMEDIATE transaction, so a writer never fails midway on a lock upgrade."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def _execute_write(self, sql: str, params: tuple[object, ...]) -> int:
        with self._write() as conn:
            return conn.execute(sql, params).rowcount

    def _fetchall(self, sql: str, params: tuple[object, ...]) -> list[tuple]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # --- bars --------------------------------------------------------------
    def upsert_bars(self, tf: str, bars: tuple[Bar, ...]) -> int:
        """Insert or overwrite bars by (tf, t) atomically; returns the rows written."""
        require_timeframe(tf)
        if not bars:
            return 0
        rows = [(tf, b.t, b.o, b.h, b.l, b.c, b.tv, b.spr) for b in bars]
        with self._write() as conn:
            conn.executemany(_UPSERT_BAR_SQL, rows)
        return len(rows)

    def load_bars(self, tf: str, since_epoch: int, limit: int) -> tuple[Bar, ...]:
        """Newest `limit` bars (max MAX_LOAD_BARS) opened at/after `since_epoch`, oldest first."""
        require_timeframe(tf)
        if limit < 1:
            raise ValueError("limit must be at least 1")
        rows = self._fetchall(_LOAD_BARS_SQL, (tf, since_epoch, min(limit, MAX_LOAD_BARS)))
        return tuple(Bar(*row) for row in rows)  # SELECT order is the Bar field order

    def count_bars(self, tf: str, since_epoch: int) -> int:
        require_timeframe(tf)
        sql = "SELECT COUNT(*) FROM v6_bars WHERE tf = ? AND t >= ?"
        rows = self._fetchall(sql, (tf, since_epoch))
        return int(rows[0][0])

    def bar_coverage(self, tf: str, since_epoch: int, until_epoch: int,
                     min_bars_per_day: int) -> Coverage:
        """Coverage of bars whose open time lies in [since_epoch, until_epoch]."""
        require_timeframe(tf)
        if min_bars_per_day < 1:
            raise ValueError("min_bars_per_day must be at least 1")
        window = (tf, since_epoch, until_epoch)
        with self._lock:
            count, first_t, last_t = self._conn.execute(_COVERAGE_SQL, window).fetchone()
            days = self._conn.execute(
                _FULL_DAYS_SQL, (SECONDS_PER_DAY, *window, min_bars_per_day)
            ).fetchone()[0]
        return Coverage(count=count, first_t=first_t, last_t=last_t, distinct_days=days)

    # --- snapshots ---------------------------------------------------------
    def snapshot_exists(self, snapshot_id: str) -> bool:
        sql = "SELECT 1 FROM v6_snapshots WHERE snapshot_id = ? LIMIT 1"
        return bool(self._fetchall(sql, (snapshot_id,)))

    def insert_snapshot(self, record: SnapshotRecord) -> bool:
        """False when the snapshot id is already stored (EA retry)."""
        return self._execute_write(_INSERT_SNAPSHOT_SQL, astuple(record)) == 1

    def prune_snapshots(self, older_than_epoch: int) -> int:
        """Keep only the summary of snapshots whose bar opened before `older_than_epoch`."""
        sql = ("UPDATE v6_snapshots SET payload_json = NULL"
               " WHERE bar_open_epoch < ? AND payload_json IS NOT NULL")
        return self._execute_write(sql, (older_than_epoch,))

    # --- executions, account marks, audit ----------------------------------
    def insert_execution(self, report: ExecutionReport, received_at: float) -> bool:
        """False when the EA re-sent a report that is already stored."""
        params = (
            report.intent_id, report.status, report.reason_code, report.ticket, report.retcode,
            report.requested_price, report.fill_price, report.slippage_points,
            report.spread_points, report.latency_ms, report.sent_at_epoch, received_at)
        return self._execute_write(_INSERT_EXECUTION_SQL, params) == 1

    def record_account_mark(self, mark: AccountMark) -> bool:
        """At most one row per minute; the first poll of a minute wins."""
        params = (
            mark.minute_epoch, mark.login, mark.trade_mode, mark.equity, mark.balance,
            mark.floating_pnl_v6, mark.open_v6_positions)
        return self._execute_write(_INSERT_MARK_SQL, params) == 1

    def log_control(self, actor: str, action: str, detail: Mapping[str, object]) -> None:
        """Append to the audit trail. Credential-like keys are refused, not redacted."""
        params = (
            self._clock.now_epoch(), _require_label("actor", actor),
            _require_label("action", action), _encode_detail(detail))
        sql = "INSERT INTO v6_control_log (ts, actor, action, detail_json) VALUES (?, ?, ?, ?)"
        self._execute_write(sql, params)

    # --- single-instance lock ----------------------------------------------
    def acquire_lock(self, holder: str, pid: int, now: float, stale_after_s: float) -> bool:
        """Take or refresh the lock; a different holder is displaced only once stale."""
        _require_label("holder", holder)
        if pid < 0 or stale_after_s <= 0:
            raise ValueError("pid must be >= 0 and stale_after_s must be positive")
        with self._write() as conn:
            previous = conn.execute(_READ_LOCK_SQL).fetchone()
            cur = conn.execute(_ACQUIRE_LOCK_SQL, (holder, pid, now, now - stale_after_s))
        acquired = cur.rowcount == 1
        if acquired and previous is not None and previous[0] != holder:
            logger.warning(
                "v6 runtime lock taken over from stale holder %s (heartbeat %.1f s old)",
                previous[0], now - previous[2],
            )
        return acquired

    def heartbeat_lock(self, holder: str, now: float) -> bool:
        """False means the lock was lost and the caller must stop acting as the runtime."""
        sql = "UPDATE v6_runtime_lock SET heartbeat_at = ? WHERE id = 1 AND holder = ?"
        return self._execute_write(sql, (now, holder)) == 1

    def release_lock(self, holder: str) -> bool:
        sql = "DELETE FROM v6_runtime_lock WHERE id = 1 AND holder = ?"
        return self._execute_write(sql, (holder,)) == 1

    def read_lock(self) -> RuntimeLock | None:
        rows = self._fetchall(_READ_LOCK_SQL, ())
        return RuntimeLock(*rows[0]) if rows else None  # SELECT order is the field order

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()
