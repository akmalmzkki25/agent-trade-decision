"""LedgerCycles: breaker state and daily sessions persist across restarts."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.v6.ledger_cycles import LedgerCycles

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
    again = ledger.start_session(trading_day="2026-09-17", backend="openrouter",
                                 mode="shadow", started_at=20.0)

    assert first.created and not again.created
    assert first.session == again.session
    session = first.session
    assert session.is_active and not session.armed
    assert (session.trading_day, session.backend, session.started_at) == (DAY, "rules", 10.0)
    assert len(session.session_id) == 12
    assert ledger.active_session() == session


def test_session_stop_arm_and_list(ledger: LedgerCycles) -> None:
    session = ledger.start_session(trading_day=DAY, backend="rules", mode="shadow",
                                   started_at=10.0, armed=True).session
    assert session.armed
    assert ledger.set_session_armed(session.session_id, True)
    assert ledger.stop_session(session.session_id, stopped_at=30.0, reason="user_stop")
    assert not ledger.stop_session(session.session_id, stopped_at=31.0, reason="user_stop")
    assert not ledger.set_session_armed(session.session_id, True)
    assert ledger.active_session() is None

    later = ledger.start_session(trading_day=DAY, backend="rules", mode="shadow",
                                 started_at=40.0).session
    listed = ledger.sessions_for_day(DAY)

    assert [s.session_id for s in listed] == [session.session_id, later.session_id]
    assert (listed[0].stopped_at, listed[0].stop_reason, listed[0].armed) == \
        (30.0, "user_stop", False)
    assert ledger.sessions_for_day("2026-09-17") == ()


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
