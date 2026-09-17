"""LedgerCycles: breaker state and daily sessions persist across restarts."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.v6.ledger_cycles import LedgerCycles
from app.v6.ledger_cycles_schema import apply_schema

DAY = "2026-09-16"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "cycles.db"


@pytest.fixture
def ledger(db_path: Path) -> Iterator[LedgerCycles]:
    led = LedgerCycles(db_path)
    yield led
    led.close()


# --- breakers -----------------------------------------------------------------


def test_breaker_trip_keeps_the_first_reason(ledger: LedgerCycles) -> None:
    first = ledger.trip_breaker("daily", DAY, "LOSS_3PCT", 100.0)
    again = ledger.trip_breaker("daily", DAY, "LOSS_4PCT", 200.0)

    assert first == again
    assert (first.tripped, first.reason, first.tripped_at) == (True, "LOSS_3PCT", 100.0)
    with pytest.raises(FrozenInstanceError):
        first.tripped = False  # type: ignore[misc]


def test_breaker_survives_restart_until_manual_reset(db_path: Path) -> None:
    led = LedgerCycles(db_path)
    led.trip_breaker("weekly", "2026-W38", "LOSS_6PCT", 100.0)
    led.close()

    reopened = LedgerCycles(db_path)
    try:
        assert [b.scope for b in reopened.active_breakers()] == ["weekly"]
        assert reopened.reset_breaker("weekly", "2026-W38", "operator", 300.0)
        assert not reopened.reset_breaker("weekly", "2026-W38", "operator", 301.0)
        assert reopened.active_breakers() == ()
        record = reopened.get_breaker("weekly", "2026-W38")
        assert record is not None
        assert (record.tripped, record.reset_by, record.reset_at) == (False, "operator", 300.0)
    finally:
        reopened.close()


def test_breaker_can_trip_again_after_reset(ledger: LedgerCycles) -> None:
    ledger.trip_breaker("monthly", "2026-09", "LOSS_10PCT", 1.0)
    with pytest.raises(ValueError):
        ledger.reset_breaker("yearly", "2026-09", "operator", 2.0)  # type: ignore[arg-type]
    ledger.reset_breaker("monthly", "2026-09", "operator", 2.0)

    record = ledger.trip_breaker("monthly", "2026-09", "EQUITY_10PCT", 3.0)

    assert (record.tripped, record.reason, record.reset_at, record.reset_by) == \
        (True, "EQUITY_10PCT", None, None)


def test_breakers_of_older_periods_stay_active(ledger: LedgerCycles) -> None:
    ledger.trip_breaker("daily", "2026-09-15", "LOSS_3PCT", 1.0)
    ledger.trip_breaker("daily", DAY, "LOSS_3PCT", 2.0)

    assert [b.period_key for b in ledger.active_breakers()] == ["2026-09-15", DAY]
    assert ledger.get_breaker("daily", "2026-09-14") is None


@pytest.mark.parametrize(
    ("scope", "period", "reason"),
    [("hourly", DAY, "x"), ("daily", "", "x"), ("daily", DAY, ""), ("daily", "x" * 65, "x")],
)
def test_breaker_input_validation(ledger: LedgerCycles, scope: str, period: str,
                                  reason: str) -> None:
    with pytest.raises(ValueError):
        ledger.trip_breaker(scope, period, reason, 1.0)  # type: ignore[arg-type]


# --- sessions -----------------------------------------------------------------


def test_session_start_is_idempotent_and_single(ledger: LedgerCycles) -> None:
    first = ledger.start_session(trading_day=DAY, backend="rules", mode="shadow",
                                 started_at=10.0)
    again = ledger.start_session(trading_day="2026-09-17", backend="operator",
                                 mode="shadow", started_at=20.0)

    assert first.created and not again.created
    assert first.session == again.session
    session = first.session
    assert session.is_active and not session.armed
    assert (session.trading_day, session.backend, session.started_at) == (DAY, "rules", 10.0)
    assert len(session.session_id) == 12
    assert ledger.active_session() == session


def test_session_stop_arm_and_list(ledger: LedgerCycles) -> None:
    session = ledger.start_session(trading_day=DAY, backend="operator", mode="execute",
                                   started_at=10.0, armed=True).session
    assert (session.armed, session.armed_at, session.disarmed_at) == (True, 10.0, None)
    assert ledger.set_session_armed(session.session_id, True, at=20.0)
    assert ledger.active_session().armed_at == 10.0, "re-arming keeps the first arm time"
    assert ledger.stop_session(session.session_id, stopped_at=30.0, reason="user_stop")
    assert not ledger.stop_session(session.session_id, stopped_at=31.0, reason="user_stop")
    assert not ledger.set_session_armed(session.session_id, True, at=32.0)
    assert ledger.active_session() is None

    later = ledger.start_session(trading_day=DAY, backend="rules", mode="shadow",
                                 started_at=40.0).session
    listed = ledger.sessions_for_day(DAY)

    assert [s.session_id for s in listed] == [session.session_id, later.session_id]
    first = listed[0]
    assert (first.stopped_at, first.stop_reason, first.armed) == (30.0, "user_stop", False)
    assert (first.armed_at, first.disarmed_at, first.disarm_reason) == (10.0, 30.0, "user_stop")
    assert (later.armed_at, later.disarmed_at, later.disarm_reason) == (None, None, None)
    assert ledger.sessions_for_day("2026-09-17") == ()


def test_arm_and_disarm_record_their_times(ledger: LedgerCycles) -> None:
    session_id = ledger.start_session(trading_day=DAY, backend="operator", mode="execute",
                                      started_at=10.0).session.session_id
    assert ledger.set_session_armed(session_id, False, at=11.0, reason="noop")
    assert ledger.active_session().disarmed_at is None, "an unarmed session records nothing"
    assert ledger.set_session_armed(session_id, True, at=12.0)
    assert ledger.set_session_armed(session_id, False, at=13.0, reason="BREAKER")
    armed_again = ledger.set_session_armed(session_id, True, at=14.0)

    active = ledger.active_session()
    assert armed_again and active.armed
    assert (active.armed_at, active.disarmed_at, active.disarm_reason) == (14.0, 13.0, "BREAKER")
    with pytest.raises(ValueError):
        ledger.set_session_armed(session_id, False, at=15.0, reason="")
    assert ledger.stop_session(session_id, stopped_at=16.0, reason="rollover_auto_close")
    stopped = ledger.sessions_for_day(DAY)[0]
    assert (stopped.armed, stopped.disarmed_at, stopped.disarm_reason) == (
        False, 16.0, "rollover_auto_close")


def test_phase2_sessions_table_gains_the_arm_columns(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE v6_sessions (session_id TEXT NOT NULL PRIMARY KEY,"
                     " trading_day TEXT NOT NULL, backend TEXT NOT NULL, mode TEXT NOT NULL,"
                     " started_at REAL NOT NULL, stopped_at REAL, stop_reason TEXT,"
                     " armed INTEGER NOT NULL DEFAULT 0)")
        conn.execute("INSERT INTO v6_sessions VALUES ('old', ?, 'rules', 'shadow', 1.0,"
                     " NULL, NULL, 0)", (DAY,))
    led = LedgerCycles(db_path)
    try:
        session = led.active_session()
        assert session is not None and session.session_id == "old"
        assert (session.armed_at, session.disarmed_at, session.disarm_reason) == (None, None, None)
        assert led.set_session_armed("old", True, at=2.0)
    finally:
        led.close()
    LedgerCycles(db_path).close()  # a second open finds the columns already there


def test_a_failing_migration_surfaces(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            apply_schema(conn, (), ("ALTER TABLE missing_table ADD COLUMN x REAL",))


def test_active_session_survives_restart(db_path: Path) -> None:
    led = LedgerCycles(db_path)
    session = led.start_session(trading_day=DAY, backend="rules", mode="shadow",
                                started_at=1.0).session
    led.close()

    reopened = LedgerCycles(db_path)
    try:
        assert reopened.active_session() == session
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "changes",
    [{"trading_day": "16-09-2026"}, {"backend": ""}, {"mode": "x" * 65}],
)
def test_session_input_validation(ledger: LedgerCycles, changes: dict[str, str]) -> None:
    kwargs = {"trading_day": DAY, "backend": "rules", "mode": "shadow", **changes}

    with pytest.raises(ValueError):
        ledger.start_session(started_at=1.0, **kwargs)


def test_database_allows_only_one_open_session(db_path: Path, ledger: LedgerCycles) -> None:
    ledger.start_session(trading_day=DAY, backend="rules", mode="shadow", started_at=1.0)

    with sqlite3.connect(db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO v6_sessions (session_id, trading_day, backend, mode,"
                     " started_at, armed) VALUES ('x', ?, 'rules', 'shadow', 2.0, 0)", (DAY,))


def test_row_vanishing_after_a_write_is_an_error(ledger: LedgerCycles,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ledger, "get_breaker", lambda scope, period_key: None)
    monkeypatch.setattr(ledger, "active_session", lambda: None)

    with pytest.raises(sqlite3.DatabaseError, match="breaker"):
        ledger.trip_breaker("daily", DAY, "LOSS_3PCT", 1.0)
    with pytest.raises(sqlite3.DatabaseError, match="session"):
        ledger.start_session(trading_day=DAY, backend="rules", mode="shadow", started_at=1.0)
