"""The deliberation worker: request building, recording, logging and the inbox loop."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.v6.container import V6Container, build_container
from app.v6.cycle_codes import HoldReason
from app.v6.deliberation.cycle_draft import CycleDraft
from app.v6.deliberation.engine import DeliberationEngine, EngineDeps, rules_provider_for
from app.v6.risk.gates import HALT_SOURCE_FILE
from app.v6.runtime import service as service_module
from app.v6.runtime.ea_state import InboxItem
from app.v6.runtime.service import (
    HALT_CHECK_FAILED, WARMUP_UNKNOWN, CarryOver, DeliberationRuntime,
)

from . import engine_fixtures_v6 as ef

DAY = ef.DAY
WAIT_STEPS = 400
WAIT_STEP_S = 0.01


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def warm_history() -> dict[str, tuple[Any, ...]]:
    history = ef.history()
    return {**history, "M15": ef.flat_bars(ef.M15, ef.AS_OF - 12 * DAY, ef.AS_OF, 5.0)}


@pytest.fixture
def container(tmp_path: Path) -> Iterator[V6Container]:
    config = ef.settings(halt_file=str(tmp_path / "V6_HALT"))
    built = build_container(config, tmp_path / "v6.db", ef.clock_at(), run_tasks=False)
    built.switch.turn_on()
    yield built
    built.switch.turn_off()
    built.close()


def warm(container: V6Container) -> None:
    for tf, bars in warm_history().items():
        container.bar_store.ingest(tf, bars)


def start_session(container: V6Container) -> str:
    started = container.ledger_cycles.start_session(
        trading_day="2026-09-17", backend="rules", mode="shadow", started_at=ef.RECEIVED)
    return started.session.session_id


def runtime_with(container: V6Container, *candidates: Any,
                 active: bool = True) -> DeliberationRuntime:
    config, clock = container.settings, container.clock
    rules = rules_provider_for(config, clock)
    engine = DeliberationEngine(EngineDeps(
        settings=config, clock=clock, bars=container.bar_store,
        breakers=container.parts.breakers.for_context, rules=rules, panel=rules,
        detector=ef.detector_of(*candidates)))
    return DeliberationRuntime(
        ea_state=container.ea_state, bar_store=container.bar_store,
        ledger=container.ledger_cycles, engine=engine,
        sessions=container.parts.control.sessions, clock=clock,
        halt_path=container.halt_path, is_active=lambda: active)


def item(snapshot_id: str = ef.SNAPSHOT_ID, **changes: Any) -> InboxItem:
    request = ef.request(ef.engine_snapshot(snapshot_id, **changes))
    return InboxItem(cycle_id=request.cycle_id, snapshot=request.snapshot,
                     received_at=request.received_at)


# --- one cycle -----------------------------------------------------------------------
@pytest.mark.anyio
async def test_a_cycle_is_recorded_with_views_and_candidates(
        container: V6Container, caplog: pytest.LogCaptureFixture) -> None:
    warm(container)
    session_id = start_session(container)
    runtime = runtime_with(container, ef.candidate())
    with caplog.at_level(logging.INFO, logger="app.v6.runtime.service"):
        result = await runtime.process(item(probe={
            "book_depth": 0, "trade_ticks_count": 0, "real_volume_count": 0,
            "dom_synthetic": True, "gmt_offset_s": 10800, "dst_active": True,
            "calendar_events_seen": 3}))
    assert (result.status, result.session_id) == ("ENTER_SHADOW", session_id)
    ledger = container.ledger_cycles
    stored = ledger.get_cycle(result.cycle_id)
    assert (stored.status, stored.provider, stored.session_id) == (
        "ENTER_SHADOW", "rules", session_id)
    assert len(ledger.views_for_cycle(result.cycle_id)) == 5
    (candidate,) = ledger.candidates_for_cycle(result.cycle_id)
    assert (candidate.verdict, candidate.stop, candidate.target) == ("chosen", 4292.0, 4316.0)
    assert candidate.label_status == "pending"
    assert runtime.stats.recorded == 1 and runtime.stats.last_status == "ENTER_SHADOW"
    carry = runtime.carry
    assert carry.probe.calendar_events_seen == 3 and carry.point == 0.01
    assert len(carry.events) == 1 and carry.day.day_start_equity == 2000.0
    assert result.cycle_id in caplog.text and "status=ENTER_SHADOW" in caplog.text


@pytest.mark.anyio
async def test_the_container_worker_holds_until_warm(container: V6Container) -> None:
    result = await container.parts.worker.process(item())
    assert result.hold_reason == HoldReason.WARMUP
    warmup = next(gate for gate in result.gates if gate.code == "WARMUP")
    assert warmup.detail.startswith("M15 0/10 days")
    assert container.ledger_cycles.get_cycle(result.cycle_id) is not None


@pytest.mark.anyio
async def test_the_container_worker_uses_the_real_detectors(container: V6Container) -> None:
    warm(container)
    result = await container.parts.worker.process(item())
    assert (result.status, result.hold_reason) == ("HOLD", HoldReason.NO_CANDIDATE)


@pytest.mark.anyio
async def test_the_halt_file_halts_the_cycle(container: V6Container) -> None:
    warm(container)
    container.halt_path.write_text("halt", encoding="utf-8")
    runtime = runtime_with(container, ef.candidate())
    request = await runtime.request_for(item())
    assert request.runtime.halt_sources == (HALT_SOURCE_FILE,)
    result = await runtime.process(item())
    assert result.hold_reason == HoldReason.HALTED


@pytest.mark.anyio
async def test_unreadable_runtime_facts_fail_closed(container: V6Container,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_halt(_path: Path) -> bool:
        raise PermissionError("denied")

    def broken_store(*_args: Any) -> Any:
        raise sqlite3.OperationalError("locked")

    async def broken_session() -> Any:
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(service_module, "_halted", broken_halt)
    monkeypatch.setattr(container.bar_store, "coverage", broken_store)
    monkeypatch.setattr(container.parts.control.sessions, "active", broken_session)
    request = await container.parts.worker.request_for(item())
    assert request.runtime.halt_sources == (HALT_CHECK_FAILED,)
    assert (request.runtime.warmed_up, request.runtime.warmup_detail) == (
        False, WARMUP_UNKNOWN)
    assert request.session_id is None


@pytest.mark.anyio
async def test_an_inactive_runtime_skips_the_cycle(container: V6Container) -> None:
    runtime = runtime_with(container, active=False)
    assert await runtime.process(item()) is None
    assert runtime.stats.skipped_inactive == 1
    assert container.ledger_cycles.recent_cycles() == ()


@pytest.mark.anyio
async def test_record_failures_and_duplicates_are_counted(
        container: V6Container, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = runtime_with(container)
    await runtime.process(item())
    await runtime.process(item())
    assert (runtime.stats.recorded, runtime.stats.duplicates) == (1, 1)

    def broken(*_args: Any) -> bool:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(container.ledger_cycles, "record_cycle", broken)
    result = await runtime.process(item("snap-eng-0002"))
    assert result is not None and runtime.stats.record_failures == 1
    assert runtime.stats.processed == 3


@pytest.mark.anyio
async def test_a_cycle_cancelled_by_shutdown_is_recorded_as_aborted(
        container: V6Container, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = runtime_with(container)
    started = asyncio.Event()

    async def slow(_request: Any) -> Any:
        started.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(runtime._engine, "run", slow)
    task = asyncio.create_task(runtime.process(item()))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    (stored,) = container.ledger_cycles.recent_cycles()
    assert (stored.status, stored.hold_reason) == ("ABORTED", "APP-V6-ABORTED")
    assert stored.summary()["hold_detail"] == "runtime stopped during the cycle"
    assert runtime.stats.last_status == "ABORTED"


def test_carry_keeps_what_a_failed_cycle_could_not_provide() -> None:
    request = ef.request()
    outcome = CycleDraft(request=request, backend="rules", started_at=ef.RECEIVED).finish(
        "ERROR", HoldReason.ERROR, "tier0: X", ef.RECEIVED)
    previous = CarryOver(events=("kept",), point=0.001)  # type: ignore[arg-type]
    inbox = InboxItem(cycle_id=request.cycle_id, snapshot=request.snapshot,
                      received_at=ef.RECEIVED)
    carried = previous.after(outcome, inbox)
    assert carried.events == ("kept",) and carried.day is None and carried.probe is None
    assert carried.point == 0.01


# --- the loop ---------------------------------------------------------------------------
async def _wait_for(condition: Any) -> None:
    for _ in range(WAIT_STEPS):
        if condition():
            return
        await asyncio.sleep(WAIT_STEP_S)
    raise AssertionError("condition not met in time")


@pytest.mark.anyio
async def test_the_worker_consumes_the_inbox_and_survives_a_failure(
        container: V6Container, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    runtime = runtime_with(container)
    real_process = runtime.process
    calls: list[str] = []

    async def flaky(inbox: InboxItem) -> Any:
        calls.append(inbox.cycle_id)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return await real_process(inbox)

    monkeypatch.setattr(runtime, "process", flaky)
    task = asyncio.create_task(runtime.run_forever())
    try:
        await _wait_for(lambda: runtime.stats.running)
        container.ea_state.offer_snapshot(item("snap-eng-0001"))
        await _wait_for(lambda: runtime.stats.errors == 1)
        container.ea_state.offer_snapshot(item("snap-eng-0002"))
        await _wait_for(lambda: runtime.stats.processed == 1)
    finally:
        task.cancel()
        await asyncio.wait({task})
    assert runtime.stats.running is False
    assert "aborted: RuntimeError" in caplog.text
    assert len(container.ledger_cycles.recent_cycles()) == 1
