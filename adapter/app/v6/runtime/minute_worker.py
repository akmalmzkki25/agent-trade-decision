"""
The minute worker (spec section 4.2): one m1 cycle per closed M1 bar, the M15 packet first.

It takes the newest minute from the EaState inbox and skips it, with a recorded reason,
when the runtime is not active here, V6_MINUTE_PACKETS is off, the minute closes an M15
bar (the M15 cycle covers it), no session is active and armed, the rollover block holds,
an m15 packet is open, the newest M15 cycle is missing or too old, or the account is
flat while an intent is still on its way to the EA. Otherwise the engine runs the m1
cycle. Every processed minute is one v6_minute_cycles row; rows older than three days are
pruned on the hour. A failed minute never stops the worker.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Final, Protocol

from ..clock import Clock
from ..config import V6Settings
from ..deliberation.cycle_draft import CycleRequest
from ..deliberation.minute_flow import MinuteBase, MinuteRun
from ..ledger_cycles import LedgerCycles
from ..ledger_minutes import MinuteRow
from ..market.sessions import session_state
from ..providers.operator_queue import OperatorQueue
from ..risk.gates import HALT_SOURCE_FILE, RuntimeGateState
from ..types import TIMEFRAME_SECONDS
from .ea_state import EaState, MinuteItem

logger = logging.getLogger(__name__)

M15_S: Final[int] = TIMEFRAME_SECONDS["M15"]
BASE_MAX_AGE_S: Final[int] = M15_S + 120        # one missed M15 cycle, plus its deadline
PRUNE_AFTER_S: Final[int] = 3 * 86_400
PRUNE_EVERY_S: Final[int] = 3_600
HALT_CHECK_FAILED: Final[str] = "HALT_CHECK_FAILED"
SKIP_INACTIVE: Final[str] = "INACTIVE"
SKIP_DISABLED: Final[str] = "DISABLED"
SKIP_M15_CLOSE: Final[str] = "M15_CLOSE"
SKIP_NOT_ARMED: Final[str] = "NOT_ARMED"
SKIP_ROLLOVER: Final[str] = "ROLLOVER"
SKIP_M15_PENDING: Final[str] = "M15_PENDING"
SKIP_NO_BASE: Final[str] = "NO_M15_CONTEXT"
SKIP_INTENT_ACTIVE: Final[str] = "INTENT_ACTIVE"
NOT_OFFERED: Final[str] = "NOT_OFFERED"


class MinuteEngine(Protocol):
    async def run_minute(self, request: CycleRequest, base: MinuteBase) -> MinuteRun: ...


class Sessions(Protocol):
    async def active(self) -> Any: ...


class ActiveIntent(Protocol):
    def active(self) -> object | None: ...


@dataclass(frozen=True)
class MinuteDeps:
    settings: V6Settings
    clock: Clock
    ea_state: EaState
    engine: MinuteEngine
    sessions: Sessions
    queue: OperatorQueue
    ledger: LedgerCycles
    book: ActiveIntent
    base: Callable[[], MinuteBase | None]
    runtime: Callable[[], RuntimeGateState | None]
    halt_path: Path
    is_active: Callable[[], bool]


@dataclass(frozen=True)
class MinuteWorkerStats:
    processed: int = 0
    offered: int = 0
    answered: int = 0
    skipped: int = 0
    errors: int = 0
    last: MinuteRow | None = None
    running: bool = False

    def to_dict(self) -> dict[str, object]:
        values = {key: value for key, value in asdict(self).items() if key != "last"}
        return {**values, "last": None if self.last is None else self.last.to_dict()}


def _state_of(item: MinuteItem) -> str:
    minute = item.minute
    return "position" if minute.positions else "pending" if minute.pending_orders else "flat"


def _action_of(decision: Any) -> str:
    if decision is None:
        return ""
    manage = decision.manage
    return f"MANAGE:{manage.op}" if manage is not None else str(decision.action)


class MinuteRuntime:
    def __init__(self, deps: MinuteDeps) -> None:
        self._deps = deps
        self._stats = MinuteWorkerStats()

    @property
    def stats(self) -> MinuteWorkerStats:
        return self._stats

    async def run_forever(self) -> None:
        """Consume the minute inbox until cancelled; a failed minute never stops it."""
        self._stats = replace(self._stats, running=True)
        try:
            while True:
                item = await self._deps.ea_state.next_minute()
                try:
                    await self.process(item)
                except Exception as exc:  # noqa: BLE001 - the worker must keep running
                    self._stats = replace(self._stats, errors=self._stats.errors + 1)
                    logger.error("v6 minute %s aborted: %s", item.cycle_id,
                                 type(exc).__name__, exc_info=True)
        finally:
            self._stats = replace(self._stats, running=False)

    async def process(self, item: MinuteItem) -> MinuteRow:
        reason, session, base = await self._gate(item)
        if reason or base is None or session is None:
            row = self._row(item, session, MinuteRun(state=_state_of(item), skipped=reason))
        else:
            row = await self._run(item, session, base)
        await self._record(row)
        await self._prune(item)
        self._note(row)
        return row

    async def _gate(self, item: MinuteItem) -> tuple[str, Any, MinuteBase | None]:
        deps, minute = self._deps, item.minute
        close = minute.bar_close_epoch
        if not deps.is_active():
            return SKIP_INACTIVE, None, None
        if not deps.settings.minute_packets:
            return SKIP_DISABLED, None, None
        if close % M15_S == 0:
            return SKIP_M15_CLOSE, None, None
        session = await deps.sessions.active()
        if session is None or not session.armed:
            return SKIP_NOT_ARMED, session, None
        if session_state(close).rollover_block:
            return SKIP_ROLLOVER, session, None
        if deps.queue.pending_kind() == "m15":
            return SKIP_M15_PENDING, session, None
        base = deps.base()
        if base is None or close - base.context.as_of_epoch > BASE_MAX_AGE_S:
            return SKIP_NO_BASE, session, None
        flat = not (minute.positions or minute.pending_orders)
        if flat and await asyncio.to_thread(deps.book.active) is not None:
            return SKIP_INTENT_ACTIVE, session, None
        return "", session, base

    async def _runtime(self) -> RuntimeGateState:
        carried = self._deps.runtime() or RuntimeGateState(warmed_up=False)
        try:
            halted = await asyncio.to_thread(self._deps.halt_path.exists)
        except OSError:
            return replace(carried, halt_sources=(HALT_CHECK_FAILED,))
        return replace(carried, halt_sources=(HALT_SOURCE_FILE,) if halted else ())

    async def _run(self, item: MinuteItem, session: Any, base: MinuteBase) -> MinuteRow:
        request = CycleRequest(
            cycle_id=item.cycle_id, snapshot=base.snapshot, received_at=item.received_at,
            runtime=await self._runtime(), session_id=session.session_id,
            session_armed=bool(session.armed), minute=item.minute)
        return self._row(item, session, await self._deps.engine.run_minute(request, base))

    def _row(self, item: MinuteItem, session: Any, run: MinuteRun) -> MinuteRow:
        common: dict[str, Any] = {
            "cycle_id": item.cycle_id, "bar_open_epoch": item.minute.bar_open_epoch,
            "state": run.state, "created_at": self._deps.clock.now_epoch(),
            "session_id": "" if session is None else str(session.session_id),
            "tier0_ms": run.tier0_ms}
        if run.outcome is None:
            return MinuteRow(outcome="SKIPPED", reason=run.skipped, **common)
        result = run.outcome.result
        closed = self._deps.queue.closed(item.cycle_id)
        decision = None if closed is None else closed.decision
        reason = "" if decision is not None else (NOT_OFFERED if closed is None
                                                  else closed.reason)
        return MinuteRow(
            outcome="ANSWERED" if decision is not None else "UNANSWERED", reason=reason,
            action=_action_of(decision), status=result.status,
            hold_reason=str(result.hold_reason or ""),
            agent="" if decision is None else decision.agent,
            latency_ms=0 if decision is None else decision.latency_ms,
            intent_id=result.intent_id or "", **common)

    async def _record(self, row: MinuteRow) -> None:
        try:
            await asyncio.to_thread(self._deps.ledger.minutes.record, row)
        except sqlite3.Error as exc:
            logger.error("v6 minute %s was not recorded: %s", row.cycle_id, type(exc).__name__)

    async def _prune(self, item: MinuteItem) -> None:
        bar_open = item.minute.bar_open_epoch
        if bar_open % PRUNE_EVERY_S != 0:
            return
        try:
            removed = await asyncio.to_thread(self._deps.ledger.minutes.prune,
                                              bar_open - PRUNE_AFTER_S)
        except sqlite3.Error as exc:
            logger.error("v6 minute rows were not pruned: %s", type(exc).__name__)
            return
        if removed:
            logger.info("v6 pruned %d minute rows", removed)

    def _note(self, row: MinuteRow) -> None:
        stats = self._stats
        offered = row.outcome != "SKIPPED"
        self._stats = replace(
            stats, processed=stats.processed + 1, offered=stats.offered + int(offered),
            answered=stats.answered + int(row.outcome == "ANSWERED"),
            skipped=stats.skipped + int(not offered), last=row)
        logger.info("v6 minute %s bar=%d state=%s outcome=%s reason=%s action=%s status=%s "
                    "tier0_ms=%d", row.cycle_id, row.bar_open_epoch, row.state, row.outcome,
                    row.reason or "-", row.action or "-", row.status or "-", row.tier0_ms)
