"""The minute worker: skip rules, one ledger row per minute, stats and pruning."""

from __future__ import annotations

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
from app.v6.providers.operator_queue import OperatorQueue
from app.v6.risk.gates import RuntimeGateState
from app.v6.runtime.ea_state import EaState, MinuteItem, minute_cycle_id_for
from app.v6.runtime.minute_worker import MinuteDeps, MinuteRuntime

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
           queue: OperatorQueue | None = None, **overrides: Any) -> MinuteRuntime:
    config = ef.settings(**(OPERATOR | overrides))
    clock = FakeClock(epoch=MINUTE_CLOSE + 1)
    return MinuteRuntime(MinuteDeps(
        settings=config, clock=clock, ea_state=EaState(),
        engine=FakeEngine(run or MinuteRun(state="flat", skipped="GATES:SPREAD")),
        sessions=sessions or FakeSessions(),
        queue=queue or OperatorQueue(settings=config, clock=clock),
        ledger=LedgerCycles(tmp_path / "minutes.db"), book=book or FakeBook(),
        base=lambda: minute_base, runtime=lambda: RuntimeGateState(warmed_up=True),
        halt_path=tmp_path / "V6_HALT", is_active=lambda: True))


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
