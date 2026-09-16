from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.ledger import Ledger
from app.v6 import ledger_v6
from app.v6.clock import FakeClock
from app.v6.ledger_v6 import AccountMark, Coverage, LedgerV6, SnapshotRecord
from app.v6.schemas.intent import ExecutionReport, PollRequest
from app.v6.schemas.snapshot import V6Snapshot
from app.v6.types import Bar

M5 = 300
M15 = 900
DAY = 86_400
DAY0 = 20_712 * DAY  # a UTC midnight
BASE_T = 1_789_560_000  # aligned to the M15 grid, 12:00 UTC
V6_TABLES = ("v6_bars", "v6_snapshots", "v6_executions",
             "v6_account_marks", "v6_control_log", "v6_runtime_lock")


def _bar(t: int, c: float = 2400.0, spr: int = 20) -> Bar:
    return Bar(t=t, o=c, h=c + 1.0, l=c - 1.0, c=c, tv=10, spr=spr)


def _bars(start: int, count: int, step: int = M15) -> tuple[Bar, ...]:
    return tuple(_bar(start + i * step, c=2400.0 + i) for i in range(count))


def _raw(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(db_path))


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "v6_test.db"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(epoch=float(BASE_T))


@pytest.fixture
def ledger(db_path: Path, clock: FakeClock) -> Iterator[LedgerV6]:
    led = LedgerV6(str(db_path), clock=clock)
    yield led
    led.close()


def _snapshot_json(snapshot_id: str = "snap-0001", bar_open: int = BASE_T) -> str:
    payload = {
        "schema_version": "v6.snapshot.1", "snapshot_id": snapshot_id, "symbol": "XAUUSD",
        "sent_at_epoch": bar_open + M15 + 2, "server_gmt_offset_s": 10_800,
        "bar_tf": "M15", "bar_open_epoch": bar_open,
        "account": {"login": "12345", "trade_mode": "DEMO", "server": "Broker-Demo",
                    "currency": "USD", "leverage": 500, "balance": 1000.0, "equity": 1010.5,
                    "margin": 0.0, "free_margin": 1010.5, "margin_level": 0.0},
        "symbol_spec": {"digits": 2, "point": 0.01, "tick_size": 0.01, "tick_value": 1.0,
                        "tick_value_loss": 1.0, "contract_size": 100.0, "volume_min": 0.01,
                        "volume_step": 0.01, "volume_max": 100.0, "stops_level": 0,
                        "freeze_level": 0, "margin_per_lot_buy": 480.0,
                        "margin_per_lot_sell": 480.0, "filling_modes": 1, "expiration_modes": 15},
        "quote": {"bid": 2400.0, "ask": 2400.2, "spread_points": 20, "time_msc": 1},
        "bars": {},
        "ticks": {"window_s": 60, "quote_count": 10, "max_gap_ms": 500,
                  "spread_p50_points": 20.0, "spread_p95_points": 25.0, "mid_rv": 0.1},
        "positions": [], "pending_orders": [],
        "day": {"day_start_equity": 1000.0, "realized_today": 0.0, "trades_today": 0},
        "calendar": [],
        "ea_state": {"ea_version": "6.0.0", "execute_enabled": False, "halted": False,
                     "local_breaker": "none", "outbox_pending": 0, "last_intent_id": ""},
    }
    return json.dumps(payload)


def _record(snapshot_id: str, bar_open: int, received_at: float) -> SnapshotRecord:
    raw = _snapshot_json(snapshot_id, bar_open)
    return SnapshotRecord.from_snapshot(V6Snapshot.model_validate_json(raw), raw, received_at)


def _report(intent_id: str = "abcdefgh2345", status: str = "filled",
            sent_at: int = BASE_T) -> ExecutionReport:
    return ExecutionReport.model_validate_json(json.dumps({
        "schema_version": "v6.execution.1", "intent_id": intent_id, "status": status,
        "reason_code": "NONE", "ticket": 777, "retcode": 10009, "requested_price": 2400.0,
        "fill_price": 2400.3, "slippage_points": 30.0, "spread_points": 21,
        "latency_ms": 85, "sent_at_epoch": sent_at,
    }))


def _mark(now: float, equity: float = 1000.0) -> AccountMark:
    return AccountMark(observed_at=now, login="12345", trade_mode="DEMO", equity=equity,
                       balance=1000.0, floating_pnl_v6=equity - 1000.0, open_v6_positions=1)


# --- schema ------------------------------------------------------------------
def test_schema_is_created_idempotently_in_wal_mode(db_path: Path, ledger: LedgerV6) -> None:
    LedgerV6(str(db_path)).close()
    with _raw(db_path) as conn:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        names = {row[0] for row in tables}
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert set(V6_TABLES) <= names
    assert mode == "wal"
    assert ledger._conn.execute("PRAGMA busy_timeout").fetchone()[0] == ledger_v6.BUSY_TIMEOUT_MS


def test_shares_the_file_with_the_v5_ledger(db_path: Path) -> None:
    v5 = Ledger(str(db_path))
    v6 = LedgerV6(str(db_path))
    try:
        with _raw(db_path) as conn:
            names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        assert {"plans", "basket_results"} <= names
        assert set(V6_TABLES) <= names
    finally:
        v6.close()
        v5.conn.close()


def test_column_migrations_tolerate_only_duplicate_columns(ledger: LedgerV6) -> None:
    ledger._add_columns_if_missing("ALTER TABLE v6_bars ADD COLUMN spr INTEGER")
    with pytest.raises(sqlite3.OperationalError):
        ledger._add_columns_if_missing("ALTER TABLE v6_missing ADD COLUMN x INTEGER")


def test_a_file_that_is_not_a_database_is_refused(db_path: Path) -> None:
    db_path.write_bytes(b"this is not an sqlite database" * 100)
    with pytest.raises(sqlite3.DatabaseError):
        LedgerV6(str(db_path))


def test_close_is_idempotent_and_blocks_further_use(db_path: Path) -> None:
    led = LedgerV6(str(db_path))
    led.close()
    led.close()
    with pytest.raises(sqlite3.ProgrammingError):
        led.count_bars("M15", 0)


# --- bars --------------------------------------------------------------------
def test_upsert_bars_returns_count_and_loads_oldest_first(ledger: LedgerV6) -> None:
    bars = _bars(BASE_T, 5)
    assert ledger.upsert_bars("M15", tuple(reversed(bars))) == 5
    assert ledger.load_bars("M15", 0, 10) == bars
    assert ledger.upsert_bars("M15", ()) == 0


def test_upsert_bars_is_idempotent_and_updates_values(ledger: LedgerV6) -> None:
    bars = _bars(BASE_T, 3)
    ledger.upsert_bars("M15", bars)
    ledger.upsert_bars("M15", bars)
    revised = Bar(t=BASE_T, o=1.0, h=3.0, l=0.5, c=2.0, tv=99, spr=7)
    ledger.upsert_bars("M15", (revised,))
    assert ledger.count_bars("M15", 0) == 3
    assert ledger.load_bars("M15", 0, 10)[0] == revised


def test_a_failing_batch_writes_nothing_and_the_ledger_stays_usable(ledger: LedgerV6) -> None:
    broken = Bar(t=BASE_T + 2 * M15, o=None, h=1.0, l=1.0, c=1.0)  # type: ignore[arg-type]
    with pytest.raises(sqlite3.IntegrityError):
        ledger.upsert_bars("M15", (*_bars(BASE_T, 2), broken))
    assert ledger.count_bars("M15", 0) == 0
    assert ledger.upsert_bars("M15", _bars(BASE_T, 2)) == 2


def test_timeframes_are_stored_separately(ledger: LedgerV6) -> None:
    ledger.upsert_bars("M15", _bars(BASE_T, 2))
    ledger.upsert_bars("M5", _bars(BASE_T, 4, step=M5))
    assert ledger.count_bars("M15", 0) == 2
    assert ledger.count_bars("M5", 0) == 4
    assert ledger.count_bars("M5", BASE_T + M5) == 3


def test_load_bars_returns_newest_limit_since_epoch(ledger: LedgerV6) -> None:
    bars = _bars(BASE_T, 10)
    ledger.upsert_bars("M15", bars)
    assert ledger.load_bars("M15", 0, 3) == bars[-3:]
    assert ledger.load_bars("M15", bars[8].t, 5) == bars[8:]


def test_load_bars_limit_is_capped(ledger: LedgerV6, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ledger_v6, "MAX_LOAD_BARS", 4)
    bars = _bars(BASE_T, 10)
    ledger.upsert_bars("M15", bars)
    assert ledger.load_bars("M15", 0, 1_000_000) == bars[-4:]
    with pytest.raises(ValueError):
        ledger.load_bars("M15", 0, 0)


@pytest.mark.parametrize("call", [
    lambda led: led.upsert_bars("M2", ()),
    lambda led: led.load_bars("m15", 0, 1),
    lambda led: led.count_bars("M15; DROP TABLE v6_bars", 0),
    lambda led: led.bar_coverage("W1", 0, 1, 1),
    lambda led: led.bar_coverage("M15", 0, 1, 0),
])
def test_invalid_bar_queries_are_rejected(ledger: LedgerV6, call) -> None:
    with pytest.raises(ValueError):
        call(ledger)


def test_bar_coverage_counts_only_days_with_enough_bars(ledger: LedgerV6) -> None:
    assert ledger.bar_coverage("M15", 0, DAY0 * 2, 1) == Coverage(0, None, None, 0)
    ledger.upsert_bars("M15", _bars(DAY0, 96))            # full day
    ledger.upsert_bars("M15", _bars(DAY0 + DAY, 8))       # stub day
    ledger.upsert_bars("M15", _bars(DAY0 + 2 * DAY, 60))  # outside the window
    cov = ledger.bar_coverage("M15", DAY0, DAY0 + 2 * DAY - M15, 48)
    assert cov == Coverage(count=104, first_t=DAY0, last_t=DAY0 + DAY + 7 * M15, distinct_days=1)


# --- snapshots ---------------------------------------------------------------
def test_snapshot_record_is_built_from_the_validated_snapshot() -> None:
    raw = _snapshot_json()
    snapshot = V6Snapshot.model_validate_json(raw)
    record = SnapshotRecord.from_snapshot(snapshot, raw.encode("utf-8"), BASE_T + M15 + 5.5)
    assert record.payload_sha256 == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert record.clock_skew_s == pytest.approx(3.5)
    assert (record.login, record.trade_mode, record.server) == ("12345", "DEMO", "Broker-Demo")
    assert (record.equity, record.balance, record.spread_points) == (1010.5, 1000.0, 20)
    assert record.bar_open_epoch == BASE_T


def test_duplicate_snapshots_are_refused(ledger: LedgerV6) -> None:
    record = _record("snap-0001", BASE_T, BASE_T + 905.0)
    assert not ledger.snapshot_exists("snap-0001")
    assert ledger.insert_snapshot(record) is True
    assert ledger.insert_snapshot(record) is False
    assert ledger.snapshot_exists("snap-0001")


def test_prune_snapshots_nulls_payload_but_keeps_summary(ledger: LedgerV6, db_path: Path) -> None:
    ledger.insert_snapshot(_record("snap-old-1", BASE_T - 8 * DAY, BASE_T))
    ledger.insert_snapshot(_record("snap-new-1", BASE_T, BASE_T))
    assert ledger.prune_snapshots(BASE_T - 7 * DAY) == 1
    assert ledger.prune_snapshots(BASE_T - 7 * DAY) == 0
    with _raw(db_path) as conn:
        rows = dict(conn.execute("SELECT snapshot_id, payload_json IS NULL FROM v6_snapshots"))
        sha = conn.execute("SELECT payload_sha256, equity FROM v6_snapshots "
                           "WHERE snapshot_id = 'snap-old-1'").fetchone()
    assert rows == {"snap-old-1": 1, "snap-new-1": 0}
    assert sha[0] and sha[1] == 1010.5


# --- executions --------------------------------------------------------------
def test_execution_reports_are_persisted_and_deduplicated(ledger: LedgerV6, db_path: Path) -> None:
    assert ledger.insert_execution(_report(status="placed"), BASE_T + 1.0) is True
    assert ledger.insert_execution(_report(status="filled"), BASE_T + 2.0) is True
    assert ledger.insert_execution(_report(status="filled"), BASE_T + 3.0) is False
    with _raw(db_path) as conn:
        rows = conn.execute(
            "SELECT intent_id, status, reason_code, ticket, retcode, requested_price, fill_price, "
            "slippage_points, spread_points, latency_ms, sent_at_epoch, received_at "
            "FROM v6_executions ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[1] == ("abcdefgh2345", "filled", "NONE", 777, 10009, 2400.0, 2400.3,
                       30.0, 21, 85, BASE_T, BASE_T + 2.0)


# --- account marks -----------------------------------------------------------
def test_account_marks_keep_one_row_per_minute(ledger: LedgerV6, db_path: Path) -> None:
    minute = float(BASE_T)
    assert ledger.record_account_mark(_mark(minute + 1.0, 1001.0)) is True
    assert ledger.record_account_mark(_mark(minute + 3.0, 1002.0)) is False
    assert ledger.record_account_mark(_mark(minute + 61.0, 1003.0)) is True
    with _raw(db_path) as conn:
        rows = conn.execute("SELECT minute_epoch, equity, floating_pnl_v6 FROM v6_account_marks "
                            "ORDER BY minute_epoch").fetchall()
    assert rows == [(BASE_T, 1001.0, 1.0), (BASE_T + 60, 1003.0, 3.0)]


def test_account_mark_from_poll() -> None:
    poll = PollRequest.model_validate_json(json.dumps({
        "schema_version": "v6.poll.1", "login": "12345", "trade_mode": "DEMO",
        "server": "Broker-Demo", "sent_at_epoch": BASE_T, "balance": 1000.0, "equity": 995.0,
        "free_margin": 900.0, "bid": 2400.0, "ask": 2400.2, "spread_points": 20,
        "open_v6_positions": 1, "pending_v6_orders": 0, "floating_pnl_v6": -5.0,
        "last_intent_id": "", "local_halt": False,
    }))
    mark = AccountMark.from_poll(poll, BASE_T + 59.9)
    assert mark.minute_epoch == BASE_T
    assert (mark.equity, mark.floating_pnl_v6, mark.open_v6_positions) == (995.0, -5.0, 1)


# --- control log -------------------------------------------------------------
def test_control_log_records_sorted_detail_with_clock_time(ledger: LedgerV6, db_path: Path) -> None:
    ledger.log_control("operator", "arm", {"mode": "execute", "b": [1, 2]})
    with _raw(db_path) as conn:
        row = conn.execute("SELECT ts, actor, action, detail_json FROM v6_control_log").fetchone()
    assert row == (float(BASE_T), "operator", "arm", '{"b": [1, 2], "mode": "execute"}')


@pytest.mark.parametrize("actor, action, detail", [
    ("operator", "arm", {"operator_token": "x"}),
    ("operator", "arm", {"outer": {"Api_Key": "x"}}),
    ("operator", "arm", {"when": object()}),
    ("operator", "arm", {"n": float("nan")}),
    ("operator", "arm", {"blob": "x" * 10_000}),
    ("", "arm", {}),
    ("operator", "a" * 65, {}),
])
def test_control_log_rejects_unsafe_input(ledger: LedgerV6, actor: str, action: str,
                                          detail: dict) -> None:
    with pytest.raises(ValueError):
        ledger.log_control(actor, action, detail)


# --- runtime lock ------------------------------------------------------------
def test_lock_acquire_refresh_and_deny_when_fresh(ledger: LedgerV6) -> None:
    assert ledger.read_lock() is None
    assert ledger.acquire_lock("adapter-a", 11, 1000.0, 30.0) is True
    assert ledger.acquire_lock("adapter-a", 11, 1010.0, 30.0) is True
    assert ledger.acquire_lock("adapter-b", 22, 1035.0, 30.0) is False
    lock = ledger.read_lock()
    assert (lock.holder, lock.pid, lock.heartbeat_at) == ("adapter-a", 11, 1010.0)


def test_lock_is_taken_over_only_when_stale(ledger: LedgerV6,
                                            caplog: pytest.LogCaptureFixture) -> None:
    ledger.acquire_lock("adapter-a", 11, 1000.0, 30.0)
    assert ledger.acquire_lock("adapter-b", 22, 1030.0, 30.0) is False
    with caplog.at_level("WARNING", logger="app.v6.ledger_v6"):
        assert ledger.acquire_lock("adapter-b", 22, 1030.5, 30.0) is True
    assert "taken over from stale holder adapter-a (heartbeat 30.5 s old)" in caplog.text
    assert ledger.read_lock().holder == "adapter-b"
    assert ledger.heartbeat_lock("adapter-a", 1031.0) is False


def test_lock_heartbeat_and_release(ledger: LedgerV6) -> None:
    ledger.acquire_lock("adapter-a", 11, 1000.0, 30.0)
    assert ledger.heartbeat_lock("adapter-a", 1025.0) is True
    assert ledger.acquire_lock("adapter-b", 22, 1050.0, 30.0) is False
    assert ledger.release_lock("adapter-b") is False
    assert ledger.release_lock("adapter-a") is True
    assert ledger.read_lock() is None
    assert ledger.heartbeat_lock("adapter-a", 1051.0) is False
    assert ledger.acquire_lock("adapter-b", 22, 1052.0, 30.0) is True


def test_lock_is_exclusive_across_connections(db_path: Path) -> None:
    first, second = LedgerV6(str(db_path)), LedgerV6(str(db_path))
    try:
        assert first.acquire_lock("proc-1", 1, 500.0, 30.0) is True
        assert second.acquire_lock("proc-2", 2, 501.0, 30.0) is False
        assert second.read_lock().holder == "proc-1"
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("holder, pid, stale", [("", 1, 30.0), ("x" * 65, 1, 30.0),
                                                ("ok", -1, 30.0), ("ok", 1, 0.0)])
def test_lock_rejects_bad_arguments(ledger: LedgerV6, holder: str, pid: int, stale: float) -> None:
    with pytest.raises(ValueError):
        ledger.acquire_lock(holder, pid, 1000.0, stale)


# --- concurrency -------------------------------------------------------------
def test_concurrent_bar_writers_do_not_lose_rows(ledger: LedgerV6, db_path: Path) -> None:
    other = LedgerV6(str(db_path))
    errors: list[BaseException] = []
    per_thread = 150

    def writer(led: LedgerV6, index: int) -> None:
        try:
            for chunk in range(0, per_thread, 25):
                start = BASE_T + (index * per_thread + chunk) * M5
                led.upsert_bars("M5", _bars(start, 25, step=M5))
                led.count_bars("M5", 0)
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(ledger if i % 2 else other, i))
               for i in range(4)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    finally:
        other.close()
    assert errors == []
    assert ledger.count_bars("M5", 0) == 4 * per_thread
