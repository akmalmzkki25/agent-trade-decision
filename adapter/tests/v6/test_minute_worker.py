"""The minute worker: skip rules, one ledger row per minute, stats and pruning."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.v6.clock import FakeClock
from app.v6.cycle_codes import HoldReason
from app.v6.cycle_types import CycleResult, CycleTimings, DeskViews
from app.v6.deliberation.cycle_draft import CycleOutcome
from app.v6.deliberation.minute_flow import MinuteBase, MinuteRun
from app.v6.ledger_cycles import LedgerCycles
from app.v6.ledger_minutes import MinuteRow
from app.v6.providers.operator_queue import OperatorQueue
from app.v6.risk.gates import RuntimeGateState
from app.v6.runtime.ea_state import EaState, MinuteItem, minute_cycle_id_for
from app.v6.runtime.minute_worker import (
    HALT_CHECK_FAILED, PRUNE_AFTER_S, MinuteDeps, MinuteRuntime, _action_of,
)

from . import engine_fixtures_v6 as ef
from . import operator_fixtures_v6 as of
from .test_engine_operator import OPERATOR
from .test_minute_context import MINUTE_CLOSE, MINUTE_OPEN, minute


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class FakeEngine:
    run: MinuteRun
    calls: list[Any] = field(default_factory=list)

    async def run_minute(self, request: Any, base: MinuteBase) -> MinuteRun:
        self.calls.append(request)
        return self.run


@dataclass
class FakeSessions:
    record: Any = field(default_factory=lambda: SimpleNamespace(session_id="sess-1", armed=True))

    async def active(self) -> Any:
        return self.record


@dataclass
class FakeBook:
    intent: Any = None

    def active(self) -> Any:
        return self.intent


def base() -> MinuteBase:
    return MinuteBase(snapshot=ef.engine_snapshot(), context=ef.market_context(),
                      views=DeskViews())


def item(bar_open: int = MINUTE_OPEN) -> MinuteItem:
    if bar_open == MINUTE_OPEN:
        snapshot = minute()
    else:
        login = ef.engine_snapshot().account.login
        snapshot = minute(bar_open_epoch=bar_open, snapshot_id=f"Q6M-{login}-{bar_open}",
                          bar=[bar_open, 4300.0, 4300.4, 4299.8, 4300.2, 60, 20],
                          sent_at_epoch=bar_open + 60)
    return MinuteItem(cycle_id=minute_cycle_id_for(snapshot.snapshot_id), minute=snapshot,
                      received_at=float(snapshot.bar_close_epoch + 1))


def hold_run() -> MinuteRun:
    result = CycleResult(
        cycle_id="m-x", snapshot_id="Q6M-x", bar_open_epoch=MINUTE_OPEN, status="HOLD",
        hold_reason=HoldReason.OPERATOR_TIMEOUT, backend="operator", provider="operator",
        provider_status="timeout",
        timings=CycleTimings(started_at=MINUTE_CLOSE + 1.0, finished_at=MINUTE_CLOSE + 50.0))
    return MinuteRun(state="flat", tier0_ms=12, outcome=CycleOutcome(result=result))


def worker(tmp_path: Path, *, run: MinuteRun | None = None, sessions: FakeSessions | None = None,
           book: FakeBook | None = None, minute_base: MinuteBase | None = base(),
           queue: OperatorQueue | None = None, engine: Any = None, ledger: Any = None,
           halt_path: Any = None, active: bool = True, **overrides: Any) -> MinuteRuntime:
    config = ef.settings(**(OPERATOR | overrides))
    clock = FakeClock(epoch=MINUTE_CLOSE + 1)
    return MinuteRuntime(MinuteDeps(
        settings=config, clock=clock, ea_state=EaState(),
        engine=engine or FakeEngine(run or MinuteRun(state="flat", skipped="GATES:SPREAD")),
        sessions=sessions or FakeSessions(),
        queue=queue or OperatorQueue(settings=config, clock=clock),
        ledger=ledger or LedgerCycles(tmp_path / "minutes.db"), book=book or FakeBook(),
        base=lambda: minute_base, runtime=lambda: RuntimeGateState(warmed_up=True),
        halt_path=halt_path or tmp_path / "V6_HALT", is_active=lambda: active))


@pytest.mark.anyio
@pytest.mark.parametrize(("setup", "reason"), [
    ({"minute_packets": False}, "DISABLED"),
    ({"sessions": FakeSessions(SimpleNamespace(session_id="sess-1", armed=False))}, "NOT_ARMED"),
    ({"minute_base": None}, "NO_M15_CONTEXT"),
    ({"book": FakeBook(intent=object())}, "INTENT_ACTIVE"),
])
async def test_minutes_are_skipped_with_their_reason(tmp_path: Path, setup: dict[str, Any],
                                                     reason: str) -> None:
    row = await worker(tmp_path, **setup).process(item())
    assert (row.outcome, row.reason) == ("SKIPPED", reason)


@pytest.mark.anyio
async def test_the_minute_that_closes_an_m15_bar_is_left_to_the_m15_cycle(tmp_path: Path) -> None:
    row = await worker(tmp_path).process(item(ef.AS_OF - 60))
    assert row.reason == "M15_CLOSE"


@pytest.mark.anyio
async def test_an_open_m15_packet_pauses_the_minutes(tmp_path: Path) -> None:
    config = ef.settings(**OPERATOR)
    queue = OperatorQueue(settings=config, clock=FakeClock(epoch=of.BAR_CLOSE + 1))
    queue.offer(of.packet())
    row = await worker(tmp_path, queue=queue).process(item())
    assert row.reason == "M15_PENDING"


@pytest.mark.anyio
async def test_a_skipped_engine_run_is_recorded(tmp_path: Path) -> None:
    runtime = worker(tmp_path)
    row = await runtime.process(item())
    assert (row.outcome, row.reason, row.session_id) == ("SKIPPED", "GATES:SPREAD", "sess-1")
    assert runtime.stats.processed == 1 and runtime.stats.skipped == 1
    stored = runtime._deps.ledger.minutes.recent(5)
    assert [entry.cycle_id for entry in stored] == [row.cycle_id]


@pytest.mark.anyio
async def test_an_unanswered_packet_is_recorded_as_such(tmp_path: Path) -> None:
    runtime = worker(tmp_path, run=hold_run())
    row = await runtime.process(item())
    assert (row.outcome, row.status, row.hold_reason, row.tier0_ms) == (
        "UNANSWERED", "HOLD", str(HoldReason.OPERATOR_TIMEOUT), 12)
    assert runtime.stats.offered == 1 and runtime.stats.answered == 0


# --- more skips, failures and pruning ---------------------------------------------------------
HOUR = MINUTE_OPEN - MINUTE_OPEN % 3_600 + 3_600       # a minute that opens on the hour
ROLLOVER_MINUTE = MINUTE_OPEN - MINUTE_OPEN % 86_400 + 21 * 3_600 + 31 * 60  # 17:31 New York


@dataclass
class BrokenEngine:
    async def run_minute(self, request: Any, base: MinuteBase) -> MinuteRun:
        raise RuntimeError("engine failure")


class UnreadablePath:
    def exists(self) -> bool:
        raise OSError("drive gone")


def failing(*_args: Any) -> Any:
    raise sqlite3.OperationalError("database is locked")


@pytest.mark.anyio
async def test_an_inactive_runtime_skips_every_minute(tmp_path: Path) -> None:
    row = await worker(tmp_path, active=False).process(item())
    assert (row.outcome, row.reason, row.session_id) == ("SKIPPED", "INACTIVE", "")


@pytest.mark.anyio
async def test_the_rollover_block_pauses_the_minutes(tmp_path: Path) -> None:
    row = await worker(tmp_path).process(item(ROLLOVER_MINUTE))
    assert (row.reason, row.session_id) == ("ROLLOVER", "sess-1")


def test_the_recorded_action_of_a_decision() -> None:
    assert _action_of(None) == ""
    assert _action_of(SimpleNamespace(action="ENTER", manage=None)) == "ENTER"
    close = SimpleNamespace(action="MANAGE", manage=SimpleNamespace(op="CLOSE"))
    assert _action_of(close) == "MANAGE:CLOSE"


@pytest.mark.anyio
async def test_an_unreadable_halt_file_counts_as_a_halt(tmp_path: Path) -> None:
    engine = FakeEngine(MinuteRun(state="flat", skipped="GATES:HALTED"))
    await worker(tmp_path, engine=engine, halt_path=UnreadablePath()).process(item())
    assert engine.calls[0].runtime.halt_sources == (HALT_CHECK_FAILED,)


@pytest.mark.anyio
async def test_a_failed_minute_never_stops_the_worker(tmp_path: Path) -> None:
    runtime = worker(tmp_path, engine=BrokenEngine())
    runtime._deps.ea_state.offer_minute(item())
    task = asyncio.create_task(runtime.run_forever())
    for _ in range(200):
        if runtime.stats.errors:
            break
        await asyncio.sleep(0.01)
    assert (runtime.stats.errors, runtime.stats.running) == (1, True)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime.stats.running is False


@pytest.mark.anyio
async def test_ledger_failures_are_logged_and_the_minute_still_counts(
        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    ledger = SimpleNamespace(minutes=SimpleNamespace(record=failing, prune=failing))
    runtime = worker(tmp_path, ledger=ledger)
    row = await runtime.process(item(HOUR))
    assert row.outcome == "SKIPPED" and runtime.stats.processed == 1
    assert "was not recorded" in caplog.text and "were not pruned" in caplog.text


@pytest.mark.anyio
async def test_rows_older_than_three_days_are_pruned_on_the_hour(
        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.v6.runtime.minute_worker")
    runtime = worker(tmp_path)
    minutes = runtime._deps.ledger.minutes
    old = HOUR - PRUNE_AFTER_S - 60
    minutes.record(MinuteRow(cycle_id="m-" + "0" * 16, bar_open_epoch=old, state="flat",
                             outcome="SKIPPED", created_at=float(old + 61)))
    await runtime.process(item(HOUR - 3_480))               # not on the hour: kept
    assert len(minutes.recent(10)) == 2
    await runtime.process(item(HOUR))
    assert {row.bar_open_epoch for row in minutes.recent(10)} == {HOUR, HOUR - 3_480}
    assert "pruned 1 minute rows" in caplog.text
