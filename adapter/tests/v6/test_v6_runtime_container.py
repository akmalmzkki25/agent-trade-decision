"""V6 container wiring, runtime lock lifecycle and the in-memory EA state."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sqlite3
from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.v6 import container as container_module
from app.v6.clock import FakeClock, SystemClock
from app.v6.config import ADAPTER_DIR, V6Settings
from app.v6.container import (
    HEARTBEAT_INTERVAL_S,
    LOCK_STALE_AFTER_S,
    MAX_HOLDER_CHARS,
    V6Container,
    build_container,
    container_for_app,
    heartbeat_loop,
    heartbeat_once,
    require_active_container,
    start_runtime,
    stop_runtime,
    v6_lifespan,
)
from app.v6.runtime.ea_state import EaState, InboxItem, SnapshotMeta, cycle_id_for

from .fixtures_v6 import trend_bars
from .payloads_v6 import BAR_OPEN, RECEIVED_AT, as_poll, as_snapshot, poll_payload, snapshot_payload

OTHER_HOLDER = "other-host:1:abcdef"
WAIT_STEP_S = 0.01
WAIT_STEPS = 300


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(epoch=RECEIVED_AT)


def _settings(**overrides: Any) -> V6Settings:
    return V6Settings(_env_file=None, **({"enabled": True} | overrides))


@pytest.fixture
def container(tmp_path: Path, clock: FakeClock) -> Iterator[V6Container]:
    built = build_container(_settings(), tmp_path / "v6.db", clock)
    yield built
    built.close()


def _control_actions(container: V6Container) -> list[str]:
    with sqlite3.connect(container.ledger_v6.path) as conn:
        rows = conn.execute("SELECT action FROM v6_control_log ORDER BY id").fetchall()
    return [row[0] for row in rows]


def _meta(snapshot_id: str = "snap-0001") -> SnapshotMeta:
    snapshot = as_snapshot(snapshot_payload(snapshot_id, bars={}))
    return SnapshotMeta.from_snapshot(snapshot, cycle_id_for(snapshot_id), RECEIVED_AT)


def _item(snapshot_id: str) -> InboxItem:
    snapshot = as_snapshot(snapshot_payload(snapshot_id, bars={}))
    return InboxItem(cycle_id=cycle_id_for(snapshot_id), snapshot=snapshot,
                     received_at=RECEIVED_AT)


# --- EA state ----------------------------------------------------------------
def test_ea_state_starts_empty() -> None:
    view = EaState().view()
    assert (view.last_poll, view.last_snapshot, view.last_seen_at) == (None, None, None)
    assert (view.superseded, view.inbox_pending) == (0, 0)
    assert view.ea_age_s(RECEIVED_AT) is None


def test_ea_state_records_polls_snapshots_and_last_seen() -> None:
    state = EaState()
    poll = as_poll(poll_payload())
    observed = state.record_poll(poll, RECEIVED_AT)
    state.record_snapshot(_meta())
    state.touch(RECEIVED_AT - 50)  # an older touch never moves last-seen backwards
    view = state.view()
    assert view.last_poll == observed
    assert (view.last_poll.poll, view.last_poll.received_at) == (poll, RECEIVED_AT)
    assert view.last_snapshot.snapshot_id == "snap-0001"
    assert view.last_snapshot.bar_open_epoch == BAR_OPEN
    assert view.last_snapshot.trade_mode == "DEMO"
    assert view.ea_age_s(RECEIVED_AT + 3) == pytest.approx(3.0)
    state.touch(RECEIVED_AT + 10)
    assert state.view().last_seen_at == RECEIVED_AT + 10


def test_ea_state_views_are_frozen_copies() -> None:
    state = EaState()
    before = state.view()
    state.record_poll(as_poll(poll_payload()), RECEIVED_AT)
    assert before.last_poll is None
    with pytest.raises(FrozenInstanceError):
        before.superseded = 5  # type: ignore[misc]


@pytest.mark.anyio
async def test_inbox_keeps_only_the_latest_snapshot() -> None:
    state = EaState()
    assert state.offer_snapshot(_item("snap-0001")) is False
    assert state.offer_snapshot(_item("snap-0002")) is True
    assert state.offer_snapshot(_item("snap-0003")) is True
    assert (state.view().superseded, state.view().inbox_pending) == (2, 1)
    item = await state.next_snapshot()
    assert item.snapshot.snapshot_id == "snap-0003"
    assert state.view().inbox_pending == 0


@pytest.mark.anyio
async def test_a_waiting_consumer_receives_the_next_snapshot() -> None:
    state = EaState()
    waiter = asyncio.create_task(state.next_snapshot())
    await asyncio.sleep(0)
    state.offer_snapshot(_item("snap-0001"))
    item = await asyncio.wait_for(waiter, timeout=1.0)
    assert item.cycle_id == cycle_id_for("snap-0001")


def test_account_marks_are_due_once_per_minute() -> None:
    state = EaState()
    assert state.mark_due(60) is True
    state.mark_done(60)
    assert state.mark_due(60) is False
    assert state.mark_due(0) is False
    assert state.mark_due(120) is True


def test_cycle_id_is_a_short_hash_of_the_snapshot_id() -> None:
    digest = hashlib.sha256(b"snap-0001").hexdigest()
    assert cycle_id_for("snap-0001") == "c-" + digest[:16]


# --- building ----------------------------------------------------------------
def test_build_container_wires_components(container: V6Container, clock: FakeClock) -> None:
    assert container.clock is clock
    assert container.settings.enabled is True
    assert container.active is False
    assert container.heartbeat_interval_s == HEARTBEAT_INTERVAL_S
    assert 0 < len(container.holder) <= MAX_HOLDER_CHARS
    assert str(os.getpid()) in container.holder
    assert container.halt_path == ADAPTER_DIR / "V6_HALT"
    with pytest.raises(FrozenInstanceError):
        container.holder = "x"  # type: ignore[misc]


def test_each_container_gets_its_own_holder(tmp_path: Path, container: V6Container) -> None:
    other = build_container(_settings(halt_file=str(tmp_path / "HALT")), tmp_path / "b.db")
    try:
        assert other.holder != container.holder
        assert isinstance(other.clock, SystemClock)
        assert other.halt_path == tmp_path / "HALT"
    finally:
        other.close()


def test_build_container_rejects_a_bad_heartbeat_interval(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="heartbeat"):
        build_container(_settings(), tmp_path / "v6.db", heartbeat_interval_s=0)


def test_container_for_app_skips_a_disabled_v6(tmp_path: Path) -> None:
    db = tmp_path / "never.db"
    assert container_for_app(_settings(enabled=False), db, None) is None
    assert not db.exists()


def test_container_for_app_skips_mode_off(tmp_path: Path) -> None:
    db = tmp_path / "never.db"
    assert container_for_app(_settings(mode="off"), db, None) is None
    assert not db.exists()


def test_container_for_app_loads_settings_from_the_environment(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V6_ENABLED", "false")
    assert container_for_app(None, tmp_path / "never.db", None) is None
    monkeypatch.setenv("V6_ENABLED", "true")
    built = container_for_app(None, tmp_path / "v6.db", None)
    try:
        assert isinstance(built, V6Container)
    finally:
        built.close()


# --- runtime lifecycle -------------------------------------------------------
@pytest.mark.anyio
async def test_start_takes_the_lock_hydrates_and_activates(container: V6Container,
                                                           clock: FakeClock) -> None:
    container.ledger_v6.upsert_bars("M15", trend_bars(30))
    assert await start_runtime(container) is True
    try:
        assert container.active
        assert container.bar_store.cached_count("M15") == 30
        lock = container.ledger_v6.read_lock()
        assert (lock.holder, lock.pid, lock.heartbeat_at) == (
            container.holder, os.getpid(), clock.epoch)
    finally:
        await stop_runtime(container)
    assert not container.active
    assert container.ledger_v6.read_lock() is None
    assert _control_actions(container) == ["runtime_start", "runtime_stop"]


@pytest.mark.anyio
async def test_start_is_refused_while_another_holder_is_fresh(
        container: V6Container, clock: FakeClock, caplog: pytest.LogCaptureFixture) -> None:
    container.ledger_v6.acquire_lock(OTHER_HOLDER, 1, clock.epoch, LOCK_STALE_AFTER_S)
    with caplog.at_level(logging.ERROR, logger="app.v6.container"):
        assert await start_runtime(container) is False
    assert not container.active
    assert OTHER_HOLDER in caplog.text
    assert container.ledger_v6.read_lock().holder == OTHER_HOLDER


@pytest.mark.anyio
async def test_a_stale_holder_is_taken_over(container: V6Container, clock: FakeClock) -> None:
    stale_at = clock.epoch - LOCK_STALE_AFTER_S - 1
    container.ledger_v6.acquire_lock(OTHER_HOLDER, 1, stale_at, LOCK_STALE_AFTER_S)
    assert await start_runtime(container) is True
    assert container.ledger_v6.read_lock().holder == container.holder
    await stop_runtime(container)


def _raise_sqlite(*_: object, **__: object) -> Any:
    raise sqlite3.OperationalError("database is locked")


@pytest.mark.anyio
async def test_a_failed_hydration_releases_the_lock(container: V6Container,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(container.bar_store, "warm", _raise_sqlite)
    assert await start_runtime(container) is False
    assert not container.active
    assert container.ledger_v6.read_lock() is None


@pytest.mark.anyio
async def test_a_failed_lock_query_leaves_v6_disabled(container: V6Container,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(container.ledger_v6, "acquire_lock", _raise_sqlite)
    assert await start_runtime(container) is False
    assert not container.active


@pytest.mark.anyio
async def test_heartbeat_refreshes_the_lock_and_detects_its_loss(
        container: V6Container, clock: FakeClock) -> None:
    assert await start_runtime(container)
    clock.advance(HEARTBEAT_INTERVAL_S)
    assert await heartbeat_once(container) is True
    assert container.ledger_v6.read_lock().heartbeat_at == clock.epoch
    container.ledger_v6.release_lock(container.holder)
    assert await heartbeat_once(container) is False
    assert not container.active
    await stop_runtime(container)  # the lock is already gone; stopping must still succeed


@pytest.mark.anyio
async def test_a_transient_heartbeat_error_keeps_the_runtime(
        container: V6Container, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    assert await start_runtime(container)
    monkeypatch.setattr(container.ledger_v6, "heartbeat_lock", _raise_sqlite)
    with caplog.at_level(logging.WARNING, logger="app.v6.container"):
        assert await heartbeat_once(container) is True
    assert container.active
    assert "heartbeat" in caplog.text
    monkeypatch.undo()
    await stop_runtime(container)


@pytest.mark.anyio
async def test_heartbeat_loop_stops_once_the_lock_is_lost(container: V6Container,
                                                          clock: FakeClock) -> None:
    assert await start_runtime(container)
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)
        clock.advance(seconds)
        if len(waits) == 2:
            container.ledger_v6.release_lock(container.holder)

    await heartbeat_loop(container, sleep=fake_sleep)
    assert waits == [HEARTBEAT_INTERVAL_S, HEARTBEAT_INTERVAL_S]
    assert not container.active
    await stop_runtime(container)


@pytest.mark.anyio
async def test_the_background_heartbeat_runs_on_its_interval(tmp_path: Path,
                                                             clock: FakeClock) -> None:
    fast = build_container(_settings(), tmp_path / "fast.db", clock, heartbeat_interval_s=0.01)
    try:
        assert await start_runtime(fast)
        clock.advance(HEARTBEAT_INTERVAL_S)
        for _ in range(WAIT_STEPS):
            if fast.ledger_v6.read_lock().heartbeat_at == clock.epoch:
                break
            await asyncio.sleep(WAIT_STEP_S)
        assert fast.ledger_v6.read_lock().heartbeat_at == clock.epoch
        await stop_runtime(fast)
        assert fast.ledger_v6.read_lock() is None
    finally:
        fast.close()


@pytest.mark.anyio
async def test_a_failed_audit_write_does_not_block_the_runtime(
        container: V6Container, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(container.ledger_v6, "log_control", _raise_sqlite)
    with caplog.at_level(logging.ERROR, logger="app.v6.container"):
        assert await start_runtime(container) is True
        await stop_runtime(container)
    assert "control log" in caplog.text
    assert container.ledger_v6.read_lock() is None


@pytest.mark.anyio
async def test_a_failed_release_is_logged_not_raised(
        container: V6Container, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    assert await start_runtime(container)
    monkeypatch.setattr(container.ledger_v6, "release_lock", _raise_sqlite)
    with caplog.at_level(logging.ERROR, logger="app.v6.container"):
        await stop_runtime(container)
    assert not container.active
    assert "release" in caplog.text


# --- lifespan and dependency -------------------------------------------------
@pytest.mark.anyio
async def test_lifespan_starts_stops_and_closes(container: V6Container) -> None:
    app = SimpleNamespace(state=SimpleNamespace(v6_container=container))
    async with v6_lifespan(app):  # type: ignore[arg-type]
        assert container.active
    assert not container.active
    with pytest.raises(sqlite3.ProgrammingError):
        container.ledger_v6.read_lock()


@pytest.mark.anyio
async def test_lifespan_without_a_container_does_nothing() -> None:
    async with v6_lifespan(SimpleNamespace(state=SimpleNamespace())):  # type: ignore[arg-type]
        pass


@pytest.mark.anyio
async def test_lifespan_closes_a_container_that_could_not_start(
        container: V6Container, clock: FakeClock) -> None:
    container.ledger_v6.acquire_lock(OTHER_HOLDER, 1, clock.epoch, LOCK_STALE_AFTER_S)
    app = SimpleNamespace(state=SimpleNamespace(v6_container=container))
    async with v6_lifespan(app):  # type: ignore[arg-type]
        assert not container.active
    with pytest.raises(sqlite3.ProgrammingError):
        container.ledger_v6.read_lock()


def _request(state: SimpleNamespace) -> Any:
    return SimpleNamespace(app=SimpleNamespace(state=state))


@pytest.mark.anyio
async def test_dependency_requires_an_active_container(container: V6Container) -> None:
    for state in (SimpleNamespace(), SimpleNamespace(v6_container=container)):
        with pytest.raises(HTTPException) as exc_info:
            require_active_container(_request(state))
        assert (exc_info.value.status_code, exc_info.value.detail) == (404, "V6 disabled")
    assert await start_runtime(container)
    try:
        assert require_active_container(_request(SimpleNamespace(v6_container=container))) \
            is container
    finally:
        await stop_runtime(container)


def test_module_constants_keep_the_lock_ahead_of_the_heartbeat() -> None:
    assert container_module.LOCK_STALE_AFTER_S > 2 * container_module.HEARTBEAT_INTERVAL_S
