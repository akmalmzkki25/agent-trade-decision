"""runtime.recovery: the intent ledger after a restart, against the newest stored snapshot."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.v6.clock import FakeClock
from app.v6.container import LOCK_STALE_AFTER_S, V6Container, start_runtime, stop_runtime
from app.v6.ledger_v6 import SnapshotRecord
from app.v6.runtime.intent_book import IntentBook
from app.v6.runtime.recovery import (
    REASON_UNCLEAN_RESTART, RecoveryReport, newest_exposure, recover_intents,
)

from . import engine_fixtures_v6 as ef
from .desk_fixtures_v6 import INTENT_ID, desk_container, open_session, publish
from .payloads_v6 import as_snapshot

MAGIC = 250570
CRASHED_HOLDER = "crashed-host:1:abcdef"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def clock() -> FakeClock:
    return ef.clock_at()


@pytest.fixture
def container(tmp_path: Path, clock: FakeClock) -> Iterator[V6Container]:
    with desk_container(tmp_path, clock) as built:
        yield built


def position(intent_id: str = INTENT_ID) -> dict[str, Any]:
    return {"ticket": 777, "magic": MAGIC, "side": "buy", "volume": 0.01,
            "price_open": 4300.0, "sl": 4292.0, "tp": 4316.0, "profit": 1.0, "swap": 0.0,
            "open_epoch": ef.AS_OF + 60, "comment": f"Q6:{intent_id}", "mae_points": 0.0,
            "mfe_points": 0.0}


def store_snapshot(container: V6Container, snapshot_id: str, received_at: float,
                   **changes: Any) -> None:
    payload = ef.engine_snapshot_payload(snapshot_id, **changes)
    raw = json.dumps(payload)
    record = SnapshotRecord.from_snapshot(as_snapshot(payload), raw, received_at)
    assert container.ledger_v6.insert_snapshot(record)


def restarted_book(container: V6Container) -> IntentBook:
    return IntentBook(container.ledger_cycles.intents)


@pytest.mark.anyio
async def test_a_published_intent_without_its_draft_is_cancelled(
        container: V6Container) -> None:
    session = open_session(container, armed=True)
    publish(container, session.session_id)
    report = await recover_intents(restarted_book(container), container.ledger_v6.path,
                                   ef.RECEIVED, magic=MAGIC)
    assert report == RecoveryReport(cancelled=(INTENT_ID,))
    record = container.ledger_cycles.intents.get(INTENT_ID)
    assert (record.status, record.report_reason) == ("CANCELLED", "RESTART")


@pytest.mark.anyio
async def test_the_newest_snapshot_reconciles_a_filled_position(
        container: V6Container) -> None:
    session = open_session(container, armed=True)
    publish(container, session.session_id)
    book = container.parts.intent_book
    assert book.next_for_poll(ef.RECEIVED) is not None          # DELIVERED
    store_snapshot(container, "snap-old", ef.RECEIVED - 900)
    store_snapshot(container, "snap-new", ef.RECEIVED, positions=[position()])
    report = await recover_intents(restarted_book(container), container.ledger_v6.path,
                                   ef.RECEIVED + 5, magic=MAGIC)
    assert (report.cancelled, report.reconciled, report.snapshot_found) == (
        (), (INTENT_ID,), True)
    record = container.ledger_cycles.intents.get(INTENT_ID)
    assert (record.status, record.ticket, record.fill_price) == ("FILLED", 777, 4300.0)


@pytest.mark.anyio
async def test_orphans_are_counted(container: V6Container) -> None:
    store_snapshot(container, "snap-orphan", ef.RECEIVED, positions=[position("b5n6r7t2vw3y")])
    report = await recover_intents(restarted_book(container), container.ledger_v6.path,
                                   ef.RECEIVED, magic=MAGIC)
    assert (report.orphans, report.snapshot_found) == (1, True)


def test_an_unreadable_snapshot_is_skipped(container: V6Container,
                                           caplog: pytest.LogCaptureFixture) -> None:
    with sqlite3.connect(container.ledger_v6.path) as conn:
        conn.execute("INSERT INTO v6_snapshots (snapshot_id, received_at, bar_open_epoch,"
                     " payload_sha256, payload_json) VALUES ('bad', 1.0, 0, 'x', '{}')")
    with caplog.at_level(logging.WARNING, logger="app.v6.runtime.recovery"):
        assert newest_exposure(container.ledger_v6.path) is None
    assert "unreadable" in caplog.text


def test_no_snapshot_means_nothing_to_reconcile(container: V6Container) -> None:
    assert newest_exposure(container.ledger_v6.path) is None


@pytest.mark.anyio
async def test_a_storage_failure_is_logged_not_raised(
        container: V6Container, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="app.v6.runtime.recovery"):
        report = await recover_intents(restarted_book(container), str(tmp_path / "none.db"),
                                       ef.RECEIVED, magic=MAGIC)
    assert report == RecoveryReport()
    assert "recovery failed: OperationalError" in caplog.text


@pytest.mark.anyio
async def test_the_runtime_recovers_before_it_starts(tmp_path: Path, clock: FakeClock) -> None:
    with desk_container(tmp_path, clock) as first:
        session = open_session(first, armed=True)
        publish(first, session.session_id)
    with desk_container(tmp_path, clock) as second:
        second.switch.turn_off()
        assert await start_runtime(second)
        try:
            record = second.ledger_cycles.intents.get(INTENT_ID)
            assert (record.status, record.report_reason) == ("CANCELLED", "RESTART")
        finally:
            await stop_runtime(second)


# --- restarts and arming ----------------------------------------------------------------
def crashed_runtime(container: V6Container, clock: FakeClock) -> None:
    """The lock a runtime that died without stopping leaves behind (stale by now)."""
    stale_at = clock.epoch - LOCK_STALE_AFTER_S - 1
    assert container.ledger_v6.acquire_lock(CRASHED_HOLDER, 1, stale_at, LOCK_STALE_AFTER_S)


@pytest.mark.anyio
async def test_after_a_crash_the_armed_session_starts_disarmed(
        tmp_path: Path, clock: FakeClock, caplog: pytest.LogCaptureFixture) -> None:
    with desk_container(tmp_path, clock) as first:
        session = open_session(first, armed=True)
        crashed_runtime(first, clock)
    with desk_container(tmp_path, clock) as second:
        second.switch.turn_off()
        with caplog.at_level(logging.WARNING, logger="app.v6.runtime.recovery"):
            assert await start_runtime(second)
        try:
            record = second.ledger_cycles.active_session()
            assert (record.session_id, record.armed, record.disarmed_at,
                    record.disarm_reason) == (session.session_id, False, clock.epoch,
                                              REASON_UNCLEAN_RESTART)
            assert second.ledger_v6.read_lock().holder == second.holder
        finally:
            await stop_runtime(second)
    assert f"session {session.session_id} DISARMED" in caplog.text


@pytest.mark.anyio
async def test_after_a_clean_stop_the_session_stays_armed(tmp_path: Path,
                                                          clock: FakeClock) -> None:
    with desk_container(tmp_path, clock) as first:
        first.switch.turn_off()
        assert await start_runtime(first)
        session = open_session(first, armed=True)
        await stop_runtime(first)
    with desk_container(tmp_path, clock) as second:
        second.switch.turn_off()
        assert await start_runtime(second)
        try:
            record = second.ledger_cycles.active_session()
            assert (record.session_id, record.armed) == (session.session_id, True)
        finally:
            await stop_runtime(second)


@pytest.mark.anyio
async def test_a_crash_without_an_armed_session_changes_nothing(tmp_path: Path,
                                                               clock: FakeClock) -> None:
    with desk_container(tmp_path, clock) as first:
        session = open_session(first)
        crashed_runtime(first, clock)
    with desk_container(tmp_path, clock) as second:
        second.switch.turn_off()
        assert await start_runtime(second)
        try:
            record = second.ledger_cycles.active_session()
            assert (record.session_id, record.disarmed_at) == (session.session_id, None)
        finally:
            await stop_runtime(second)


@pytest.mark.anyio
async def test_a_session_that_cannot_be_disarmed_keeps_v6_off(
        tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    def locked(*_args: Any, **_kwargs: Any) -> bool:
        raise sqlite3.OperationalError("database is locked")

    with desk_container(tmp_path, clock) as first:
        open_session(first, armed=True)
        crashed_runtime(first, clock)
    with desk_container(tmp_path, clock) as second:
        second.switch.turn_off()
        monkeypatch.setattr(second.ledger_cycles, "set_session_armed", locked)
        assert await start_runtime(second) is False
        assert not second.active
        assert second.ledger_v6.read_lock() is None
        assert second.ledger_cycles.active_session().armed is True
