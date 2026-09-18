"""
The deliberation worker: one task, one cycle at a time (plan section 4).

It takes the newest snapshot from the EaState inbox, gathers what only the
runtime knows (kill switches, warm-up, the active session, the calendar events
and probe carried from the previous cycle), runs the engine and records the
CycleResult with its views and candidates. SQLite work runs in worker threads.

The EA route has already stored the snapshot and its bars before queueing it,
so the BarStore holds every bar the cycle reads. Only the engine's publisher
(execute mode, armed session, operator decision) publishes an intent; the cycle
then records its id. The worker never dies on a failed cycle; it logs one line
per cycle with the cycle id and moves on.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Final

from ..clock import Clock
from ..cycle_codes import CycleStatus, HoldReason
from ..cycle_types import CalendarEvent, CycleResult, DeskViews, MarketContext
from ..deliberation.context_builder import as_of_for
from ..deliberation.cycle_draft import CycleDraft, CycleOutcome, CycleRequest
from ..deliberation.engine import DeliberationEngine
from ..deliberation.minute_flow import MinuteBase
from ..ledger_cycles import LedgerCycles
from ..learning.labeler import XAUUSD_POINT
from ..ledger_cycles_schema import SessionRecord
from ..market.bar_store import WARM_M5_DAYS, WARM_M15_DAYS, BarStore
from ..risk.gates import HALT_SOURCE_FILE, RuntimeGateState
from ..schemas.snapshot import ProbeBlock, V6Snapshot
from .breaker_feed import DayFacts
from .ea_state import EaState, InboxItem
from .sessions import SessionService

logger = logging.getLogger(__name__)

WARMUP_UNKNOWN: Final[str] = "bar coverage unavailable"
HALT_CHECK_FAILED: Final[str] = "HALT_CHECK_FAILED"
RECORD_OK: Final[str] = "recorded"
RECORD_DUPLICATE: Final[str] = "duplicate"
RECORD_FAILED: Final[str] = "failed"
STATUS_ABORTED: Final[CycleStatus] = "ABORTED"
ABORTED_DETAIL: Final[str] = "runtime stopped during the cycle"


@dataclass(frozen=True)
class CarryOver:
    """What one cycle hands to the next, to the watchdog and to the minute worker."""

    probe: ProbeBlock | None = None
    events: tuple[CalendarEvent, ...] = ()
    point: float = XAUUSD_POINT
    day: DayFacts | None = None
    snapshot: V6Snapshot | None = None      # the newest M15 cycle with a context
    context: MarketContext | None = None
    views: DeskViews | None = None
    runtime: RuntimeGateState | None = None

    def after(self, outcome: CycleOutcome, item: InboxItem,
              runtime: RuntimeGateState | None = None) -> "CarryOver":
        snapshot = item.snapshot
        context = outcome.context
        fresh = context is not None
        return CarryOver(
            probe=snapshot.probe if snapshot.probe is not None else self.probe,
            events=self.events if outcome.calendar is None else outcome.calendar.events,
            point=snapshot.symbol_spec.point,
            day=self.day if context is None else DayFacts.from_context(context),
            snapshot=snapshot if fresh else self.snapshot,
            context=context if fresh else self.context,
            views=outcome.result.views if fresh else self.views,
            runtime=self.runtime if runtime is None else runtime,
        )


@dataclass(frozen=True)
class WorkerStats:
    processed: int = 0
    recorded: int = 0
    duplicates: int = 0
    record_failures: int = 0
    errors: int = 0
    skipped_inactive: int = 0
    last_cycle_id: str | None = None
    last_status: str | None = None
    last_hold_reason: str | None = None
    last_finished_at: float | None = None
    running: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _halted(path: Path) -> bool:
    return path.exists()


class DeliberationRuntime:
    def __init__(self, *, ea_state: EaState, bar_store: BarStore, ledger: LedgerCycles,
                 engine: DeliberationEngine, sessions: SessionService, clock: Clock,
                 halt_path: Path, is_active: Callable[[], bool]) -> None:
        self._ea_state = ea_state
        self._bar_store = bar_store
        self._ledger = ledger
        self._engine = engine
        self._sessions = sessions
        self._clock = clock
        self._halt_path = halt_path
        self._is_active = is_active
        self._carry = CarryOver()
        self._stats = WorkerStats()

    @property
    def carry(self) -> CarryOver:
        return self._carry

    @property
    def stats(self) -> WorkerStats:
        return self._stats

    def minute_base(self) -> MinuteBase | None:
        """The newest M15 cycle for the minute worker (None before the first one)."""
        carry = self._carry
        if carry.snapshot is None or carry.context is None:
            return None
        return MinuteBase(snapshot=carry.snapshot, context=carry.context,
                          views=carry.views or DeskViews(), events=carry.events,
                          probe=carry.probe)

    async def run_forever(self) -> None:
        """Consume the inbox until cancelled; a failed cycle never stops the worker."""
        self._stats = replace(self._stats, running=True)
        try:
            while True:
                item = await self._ea_state.next_snapshot()
                try:
                    await self.process(item)
                except Exception as exc:  # noqa: BLE001 - the worker must keep running
                    self._stats = replace(self._stats, errors=self._stats.errors + 1)
                    logger.error("v6 cycle %s aborted: %s", item.cycle_id,
                                 type(exc).__name__, exc_info=True)
        finally:
            self._stats = replace(self._stats, running=False)

    async def process(self, item: InboxItem) -> CycleResult | None:
        """Run and record one cycle; None when this process no longer owns the runtime."""
        if not self._is_active():
            self._stats = replace(self._stats,
                                  skipped_inactive=self._stats.skipped_inactive + 1)
            logger.warning("v6 cycle %s skipped: runtime inactive", item.cycle_id)
            return None
        request = await self.request_for(item)
        try:
            outcome = await self._engine.run(request)
        except asyncio.CancelledError:
            await self._record_aborted(request)
            raise
        self._carry = self._carry.after(outcome, item, request.runtime)
        recorded = await self._record(outcome.result)
        self._note(outcome.result, recorded)
        return outcome.result

    async def _record_aborted(self, request: CycleRequest) -> None:
        """Shutdown interrupted the cycle: record it as ABORTED (plan section 4, restart)."""
        now = self._clock.now_epoch()
        draft = CycleDraft(request=request, backend=self._engine.settings.backend,
                           started_at=now)
        result = draft.finish(STATUS_ABORTED, HoldReason.ABORTED, ABORTED_DETAIL, now).result
        self._note(result, await self._record(result))

    # --- request -------------------------------------------------------------------
    async def request_for(self, item: InboxItem) -> CycleRequest:
        runtime = RuntimeGateState(
            warmed_up=False, halt_sources=await self._halt_sources(),
            warmup_detail=WARMUP_UNKNOWN)
        runtime = await self._warmup(runtime, as_of_for(item.snapshot))
        session = await self._session()
        return CycleRequest(
            cycle_id=item.cycle_id, snapshot=item.snapshot, received_at=item.received_at,
            runtime=runtime, session_id=None if session is None else session.session_id,
            carried_events=self._carry.events, probe=self._carry.probe,
            session_armed=session is not None and session.armed)

    async def _halt_sources(self) -> tuple[str, ...]:
        try:
            halted = await asyncio.to_thread(_halted, self._halt_path)
        except OSError:
            logger.error("v6 halt file %s cannot be checked; failing closed", self._halt_path)
            return (HALT_CHECK_FAILED,)
        return (HALT_SOURCE_FILE,) if halted else ()

    async def _warmup(self, runtime: RuntimeGateState, as_of: int) -> RuntimeGateState:
        store = self._bar_store
        try:
            m15 = await asyncio.to_thread(store.coverage, "M15", as_of)
            m5 = await asyncio.to_thread(store.coverage, "M5", as_of)
        except sqlite3.Error as exc:
            logger.error("v6 warm-up check failed (%s)", type(exc).__name__)
            return runtime
        warm = m15.distinct_days >= WARM_M15_DAYS and m5.distinct_days >= WARM_M5_DAYS
        detail = (f"M15 {m15.distinct_days}/{WARM_M15_DAYS} days, "
                  f"M5 {m5.distinct_days}/{WARM_M5_DAYS} days")
        return replace(runtime, warmed_up=warm, warmup_detail=detail)

    async def _session(self) -> SessionRecord | None:
        try:
            return await self._sessions.active()
        except sqlite3.Error as exc:
            logger.error("v6 active session unreadable (%s); tier 0 only", type(exc).__name__)
            return None

    # --- record ----------------------------------------------------------------------
    async def _record(self, result: CycleResult) -> str:
        try:
            stored = await asyncio.to_thread(self._ledger.record_cycle, result,
                                             self._clock.now_epoch())
        except (sqlite3.Error, ValueError) as exc:
            logger.error("v6 cycle %s was not recorded: %s", result.cycle_id,
                         type(exc).__name__)
            return RECORD_FAILED
        return RECORD_OK if stored else RECORD_DUPLICATE

    def _note(self, result: CycleResult, recorded: str) -> None:
        stats = self._stats
        self._stats = replace(
            stats, processed=stats.processed + 1,
            recorded=stats.recorded + int(recorded == RECORD_OK),
            duplicates=stats.duplicates + int(recorded == RECORD_DUPLICATE),
            record_failures=stats.record_failures + int(recorded == RECORD_FAILED),
            last_cycle_id=result.cycle_id, last_status=result.status,
            last_hold_reason=None if result.hold_reason is None else str(result.hold_reason),
            last_finished_at=result.timings.finished_at)
        failed = ",".join(gate.code for gate in result.failed_gates) or "-"
        logger.info(
            "v6 cycle %s bar=%d status=%s hold=%s provider=%s/%s candidates=%d "
            "failed_gates=%s intent=%s total_ms=%d recorded=%s", result.cycle_id,
            result.bar_open_epoch, result.status, result.hold_reason or "-", result.provider,
            result.provider_status, len(result.candidates), failed, result.intent_id or "-",
            result.timings.total_ms, recorded)
