"""The watchdog: runtime status, breakers, session auto-close and the labeler cadence."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.v6.clock import FakeClock
from app.v6.container import V6Container, build_container
from app.v6.learning.label_job import ERR_STORAGE, LabelRun
from app.v6.ledger_v6 import AccountMark
from app.v6.market.broker_hours import DEFAULT_QUOTE_GAP
from app.v6.runtime import watchdog as watchdog_module
from app.v6.runtime.ea_state import SnapshotMeta
from app.v6.runtime.watchdog import (
    BREAKER_FAIL_CLOSED_AFTER, LABEL_INTERVAL_S, SNAPSHOT_STALE_AFTER_S, Watchdog, WatchdogState,
    next_streaks, snapshot_stale,
)

from . import engine_fixtures_v6 as ef
from .cycle_fixtures_v6 import enter_result
from .payloads_v6 import BAR_OPEN, as_poll, poll_payload

DAY_START = ef.AS_OF - ef.AS_OF % ef.DAY
ROLLOVER = DAY_START + 21 * 3600 + 60        # Thu 17:01 New York (EDT)
SATURDAY = DAY_START + 2 * ef.DAY + 12 * 3600
GAP = DEFAULT_QUOTE_GAP                       # the probe's 20:00-22:00 UTC quote gap


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def clock() -> FakeClock:
    return ef.clock_at()


@pytest.fixture
def container(tmp_path: Path, clock: FakeClock) -> Iterator[V6Container]:
    config = ef.settings(halt_file=str(tmp_path / "V6_HALT"))
    built = build_container(config, tmp_path / "v6.db", clock, run_tasks=False)
    built.switch.turn_on()
    yield built
    built.switch.turn_off()
    built.close()


def watchdog(container: V6Container, **kwargs: Any) -> Watchdog:
    return Watchdog(container.parts.watchdog.deps, **kwargs)


def poll(container: V6Container, equity: float = 2010.5) -> None:
    container.ea_state.record_poll(as_poll(poll_payload(equity=equity)),
                                   container.clock.now_epoch())


def mark(container: V6Container, at: float, equity: float) -> None:
    container.ledger_v6.record_account_mark(AccountMark(
        observed_at=at, login="12345", trade_mode="DEMO", equity=equity, balance=equity,
        floating_pnl_v6=0.0, open_v6_positions=0))


# --- status -------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_the_first_tick_waits_for_the_ea(container: V6Container) -> None:
    state = await watchdog(container).tick()
    assert (state.status, state.ea_stale, state.halted) == ("WAITING_EA", True, False)
    assert state.breaker.tripped is False
    assert (state.ticks, state.errors, state.last_label_at) == (1, 0, ef.RECEIVED)
    json.dumps(state.to_dict())


@pytest.mark.anyio
async def test_a_polling_ea_is_running_until_it_goes_quiet(container: V6Container,
                                                           clock: FakeClock) -> None:
    dog = watchdog(container)
    poll(container)
    assert (await dog.tick()).status == "RUNNING"
    clock.advance(container.settings.ea_stale_s + 1)
    state = await dog.tick()
    assert (state.status, state.ea_stale) == ("STALE", True)


@pytest.mark.anyio
async def test_a_missing_snapshot_marks_the_runtime_stale(container: V6Container,
                                                          clock: FakeClock) -> None:
    container.ea_state.record_snapshot(SnapshotMeta(
        snapshot_id="s", cycle_id="c", bar_open_epoch=ef.T_BAR,
        received_at=clock.epoch - SNAPSHOT_STALE_AFTER_S - 1, trade_mode="DEMO",
        clock_skew_s=1.0))
    poll(container)
    state = await watchdog(container).tick()
    assert (state.status, state.snapshot_stale) == ("STALE", True)


def test_snapshot_staleness_ignores_closed_markets(container: V6Container) -> None:
    view = container.ea_state.view()
    assert snapshot_stale(view, ef.RECEIVED, quote_gap=GAP) is False
    meta = SnapshotMeta(snapshot_id="s", cycle_id="c", bar_open_epoch=0, received_at=0.0,
                        trade_mode="DEMO", clock_skew_s=0.0)
    container.ea_state.record_snapshot(meta)
    assert snapshot_stale(container.ea_state.view(), SATURDAY, quote_gap=GAP) is False
    assert snapshot_stale(container.ea_state.view(), ROLLOVER, quote_gap=GAP) is False
    assert snapshot_stale(container.ea_state.view(), ef.RECEIVED, quote_gap=GAP) is True


@pytest.mark.anyio
async def test_the_halt_file_is_reported_and_logged(container: V6Container,
                                                    caplog: pytest.LogCaptureFixture) -> None:
    dog = watchdog(container)
    poll(container)
    await dog.tick()
    container.halt_path.write_text("halt", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="app.v6.runtime.watchdog"):
        state = await dog.tick()
    assert (state.status, state.halted) == ("HALTED", True)
    assert "RUNNING -> HALTED" in caplog.text


@pytest.mark.anyio
async def test_an_unreadable_halt_file_fails_closed(container: V6Container,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    dog = watchdog(container)

    async def broken() -> bool:
        raise PermissionError("denied")

    monkeypatch.setattr(dog, "_halted", broken)
    state = await dog.tick()
    assert state.halted is True and state.errors == 1


# --- breakers -------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_a_breaker_trip_queues_cancel_pending_once(
        container: V6Container, clock: FakeClock, caplog: pytest.LogCaptureFixture) -> None:
    dog = watchdog(container)
    mark(container, DAY_START + 60, 2000.0)
    poll(container, equity=1900.0)
    with caplog.at_level(logging.WARNING, logger="app.v6.runtime.watchdog"):
        state = await dog.tick()
    assert (state.status, state.breaker.tripped) == ("BREAKER", True)
    pending = container.parts.control.commands.current(clock.epoch)
    assert (pending.command, pending.reason) == ("CANCEL_PENDING", "breaker_trip")
    assert "breaker tripped" in caplog.text
    first_request = pending.requested_at
    clock.advance(5)
    await dog.tick()
    assert container.parts.control.commands.current(clock.epoch).requested_at == first_request


@pytest.mark.anyio
async def test_persisted_breakers_count_without_a_poll(container: V6Container) -> None:
    container.ledger_cycles.trip_breaker("daily", "2026-09-17", "EQUITY_DRAWDOWN",
                                         ef.RECEIVED)
    state = await watchdog(container).tick()
    assert state.breaker.tripped and state.breaker.labels == ("daily:2026-09-17",)
    assert "EQUITY_DRAWDOWN" in state.breaker.detail
    assert state.status == "BREAKER"


# --- sessions and labels ----------------------------------------------------------------------
@pytest.mark.anyio
async def test_a_session_is_closed_at_rollover(container: V6Container,
                                               clock: FakeClock) -> None:
    ledger = container.ledger_cycles
    ledger.start_session(trading_day="2026-09-17", backend="rules", mode="shadow",
                         started_at=clock.epoch)
    dog = watchdog(container)
    await dog.tick()
    assert ledger.active_session() is not None
    clock.epoch = float(ROLLOVER)
    await dog.tick()
    assert ledger.active_session() is None
    assert ledger.sessions_for_day("2026-09-17")[0].stop_reason == "rollover_auto_close"
    assert container.parts.control.commands.current(clock.epoch).command == "CANCEL_PENDING"


@pytest.mark.anyio
async def test_the_labeler_runs_on_its_own_cadence(container: V6Container, clock: FakeClock,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[float] = []
    real = watchdog_module.label_pending

    async def counting(*args: Any, **kwargs: Any) -> Any:
        calls.append(args[2])
        return await real(*args, **kwargs)

    monkeypatch.setattr(watchdog_module, "label_pending", counting)
    dog = watchdog(container)
    await dog.tick()
    clock.advance(5)
    await dog.tick()
    clock.advance(LABEL_INTERVAL_S)
    await dog.tick()
    assert calls == [ef.RECEIVED, ef.RECEIVED + 5 + LABEL_INTERVAL_S]


@pytest.mark.anyio
async def test_a_labeler_storage_error_is_counted_not_retried_at_once(
        container: V6Container, clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    dog = watchdog(container)
    calls: list[int] = []

    async def failing(*_args: Any, **_kwargs: Any) -> LabelRun:
        calls.append(1)
        return LabelRun(error=ERR_STORAGE)

    monkeypatch.setattr(watchdog_module, "label_pending", failing)
    state = await dog.tick()
    clock.advance(5)
    state = await dog.tick()
    assert calls == [1] and state.errors == 1


@pytest.mark.anyio
async def test_resolved_candidates_are_labeled(container: V6Container,
                                               clock: FakeClock) -> None:
    ledger = container.ledger_cycles
    assert ledger.record_cycle(enter_result("cyc-label"), BAR_OPEN + 901.0)
    end = BAR_OPEN + 4 * 3600
    for tf, step in (("M5", ef.M5), ("M1", 60)):
        container.bar_store.ingest(tf, ef.flat_bars(step, BAR_OPEN + ef.M15, end, 4.0))
    clock.epoch = float(end)
    state = await watchdog(container).tick()
    assert state.labels_written == 1
    (labelled,) = ledger.candidates_for_cycle("cyc-label")
    assert (labelled.label_status, labelled.outcome) == ("labeled", "time")


@pytest.mark.anyio
async def test_a_failing_step_does_not_stop_the_others(container: V6Container,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(_now: float) -> Any:
        raise RuntimeError("sessions down")

    monkeypatch.setattr(container.parts.control.sessions, "auto_close_if_rollover", broken)
    state = await watchdog(container).tick()
    assert state.errors == 1 and state.last_label_at == ef.RECEIVED


# --- loop -------------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_the_loop_sleeps_then_ticks_only_while_active(container: V6Container) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            container.switch.turn_off()
        if len(sleeps) == 3:
            raise asyncio.CancelledError

    dog = watchdog(container, interval_s=2.5, sleep=fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await dog.run_forever()
    assert sleeps == [2.5, 2.5, 2.5]
    assert dog.state.ticks == 1


def test_intervals_must_be_positive(container: V6Container) -> None:
    with pytest.raises(ValueError):
        watchdog(container, interval_s=0)
    with pytest.raises(ValueError):
        watchdog(container, label_interval_s=-1)


def test_the_initial_state_is_starting() -> None:
    state = WatchdogState()
    assert (state.status, state.breakers_tripped, state.checked_at) == ("STARTING", False, None)


# --- the broker's daily quote gap (finding: a false STALE every evening) ----------------------
EVENING = DAY_START + 20 * 3600                  # Thu 20:00 UTC: the broker stops quoting


def _last_snapshot_at(container: V6Container, received_at: float) -> None:
    container.ea_state.record_snapshot(SnapshotMeta(
        snapshot_id="s-eve", cycle_id="c-eve", bar_open_epoch=EVENING - ef.M15,
        received_at=received_at, trade_mode="DEMO", clock_skew_s=0.0))


@pytest.mark.anyio
async def test_the_daily_quote_gap_is_not_stale(container: V6Container,
                                                clock: FakeClock) -> None:
    _last_snapshot_at(container, EVENING + 5)
    clock.epoch = float(EVENING + 40 * 60)       # 20:40 UTC, before the 21:00 rollover block
    poll(container)
    state = await watchdog(container).tick()
    assert (state.status, state.snapshot_stale) == ("RUNNING", False)
    view = container.ea_state.view()
    assert snapshot_stale(view, clock.epoch, quote_gap=None) is True


@pytest.mark.parametrize(("minutes_after_reopen", "stale"), [(20, False), (31, False), (32, True)])
def test_the_stale_clock_restarts_when_quotes_return(container: V6Container,
                                                     minutes_after_reopen: int,
                                                     stale: bool) -> None:
    _last_snapshot_at(container, EVENING + 5)
    reopen = DAY_START + 23 * 3600               # the rollover block (EDT) ends at 23:00 UTC
    now = reopen + minutes_after_reopen * 60
    assert snapshot_stale(container.ea_state.view(), now, quote_gap=GAP) is stale


# --- failing steps (finding: no traceback, frozen breaker summary) ----------------------------
@pytest.mark.anyio
async def test_a_failing_breaker_step_logs_and_then_fails_closed(
        container: V6Container, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    dog = watchdog(container)
    poll(container)
    assert not (await dog.tick()).breaker.tripped

    async def locked(*_args: Any) -> Any:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(container.parts.breakers, "for_poll", locked)
    with caplog.at_level(logging.ERROR, logger="app.v6.runtime.watchdog"):
        states = [await dog.tick() for _ in range(BREAKER_FAIL_CLOSED_AFTER)]
    failures = [r for r in caplog.records if "step breakers failed" in r.getMessage()]
    assert "database is locked" in failures[0].getMessage()
    assert failures[0].exc_info and not failures[1].exc_info
    assert [s.breaker.tripped for s in states] == [False, False, True]
    assert (states[-1].status, states[-1].breaker.labels) == ("BREAKER", ("UNAVAILABLE",))
    assert "breaker evaluation unavailable (3 consecutive failures)" in states[-1].breaker.detail
    assert dog.streaks["breakers"] == BREAKER_FAIL_CLOSED_AFTER

    monkeypatch.undo()
    recovered = await dog.tick()
    assert (recovered.breaker.tripped, recovered.status, dog.streaks["breakers"]) == (
        False, "RUNNING", 0)


def test_streaks_count_only_the_steps_that_ran() -> None:
    first = next_streaks({}, ["halt", "labeler"], ["labeler"])
    second = next_streaks(first, ["halt"], ["halt"])
    assert dict(second) == {"halt": 1, "labeler": 1}
    assert dict(next_streaks(second, ["halt", "labeler"], [])) == {"halt": 0, "labeler": 0}


@pytest.mark.anyio
async def test_a_labeler_error_message_reaches_the_log(
        container: V6Container, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    async def failing(*_args: Any, **_kwargs: Any) -> LabelRun:
        return LabelRun(error=ERR_STORAGE)

    monkeypatch.setattr(watchdog_module, "label_pending", failing)
    with caplog.at_level(logging.ERROR, logger="app.v6.runtime.watchdog"):
        await watchdog(container).tick()
    assert "RuntimeError: LABELER_STORAGE" in caplog.text
