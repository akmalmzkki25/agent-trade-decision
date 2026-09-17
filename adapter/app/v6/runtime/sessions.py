"""
Daily trading sessions (plan section 4b).

"Mulai trading skrg" opens one session per trading day; in execute mode the
session is armed when every arming check passes (`runtime.arming`). "Sudah cukup
hari ini" closes it: the session is disarmed, undelivered intents are cancelled,
a pending operator packet is withdrawn, CANCEL_PENDING is queued for the EA and
the day summary is returned. Open V6 positions are never flattened here: they run
to SL, TP or the time barrier (the EA still flattens before rollover). A session
left open is closed by `auto_close_if_rollover` once the rollover block starts.

The execution side effects (arming, disarming, cancelling intents) belong to an
optional `SessionDesk` (`runtime.desk.ExecutionDesk`); without one a session is
never armed (the Phase 2 behaviour). Every method runs on the event loop thread;
SQLite calls go through `asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Final, Protocol

from ..ledger_cycles import LedgerCycles
from ..ledger_cycles_schema import SessionRecord
from ..market.sessions import session_state
from .arming import ArmDecision
from .commands import (  # noqa: F401 - re-exported for the routes and tests
    CANCEL_PENDING, CANCEL_PENDING_TTL_S, FLATTEN, NO_COMMAND, CommandBoard, PendingCommand,
    poll_command,
)
from .day_summary import (  # noqa: F401 - re-exported for the routes and tests
    DaySummary, OpenExposure, last_trade_mode, read_day, read_intent_statuses,
    session_to_dict, trading_day_bounds, trading_day_for,
)
from .ea_state import EaState

logger = logging.getLogger(__name__)

ACTOR_RUNTIME: Final[str] = "runtime"
ACTION_SESSION_START: Final[str] = "session_start"
ACTION_SESSION_STOP: Final[str] = "session_stop"
STOP_REASON_ROLLOVER: Final[str] = "rollover_auto_close"
STOP_REASON_DAY_CHANGED: Final[str] = "trading_day_changed"
# v6_intents.report_reason of intents a session stop cancels (= intent_book.REASON_SESSION_STOP).
CANCEL_REASON_SESSION_STOP: Final[str] = "SESSION_STOP"
REFUSE_BREAKER: Final[str] = "APP-V6-SESSION-BREAKER"
REFUSE_NOT_DEMO: Final[str] = "APP-V6-SESSION-NOT-DEMO"
REFUSE_MARKET_CLOSED: Final[str] = "APP-V6-SESSION-MARKET-CLOSED"
DEMO_TRADE_MODE: Final[str] = "DEMO"
CSRF_NONCE_BYTES: Final[int] = 32


def _session_or_none(record: SessionRecord | None) -> dict[str, object] | None:
    return None if record is None else session_to_dict(record)


def _arm_or_none(decision: ArmDecision | None) -> dict[str, object] | None:
    if decision is None:
        return None
    return {"armed": decision.armed, "reason": decision.reason, "detail": decision.detail}


@dataclass(frozen=True)
class SessionStartOutcome:
    trading_day: str
    session: SessionRecord | None
    created: bool
    refusal: str | None = None
    detail: str = ""
    arm: ArmDecision | None = None          # execute mode: the arming verdict

    @property
    def refused(self) -> bool:
        return self.refusal is not None

    def to_dict(self) -> dict[str, object]:
        return {"trading_day": self.trading_day, "created": self.created,
                "refusal": self.refusal, "detail": self.detail,
                "session": _session_or_none(self.session),
                "armed": bool(self.session is not None and self.session.armed),
                "arm": _arm_or_none(self.arm)}


@dataclass(frozen=True)
class SessionStopOutcome:
    stopped: bool
    session: SessionRecord | None
    command: PendingCommand
    summary: DaySummary
    cancelled_intents: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {"stopped": self.stopped, "session": _session_or_none(self.session),
                "command": self.command.to_dict(), "summary": self.summary.to_dict(),
                "cancelled_intents": list(self.cancelled_intents)}


@dataclass(frozen=True)
class SessionStatus:
    trading_day: str
    session: SessionRecord | None
    summary: DaySummary
    command: PendingCommand | None

    def to_dict(self) -> dict[str, object]:
        return {"trading_day": self.trading_day, "session": _session_or_none(self.session),
                "summary": self.summary.to_dict(),
                "command": None if self.command is None else self.command.to_dict()}


# --- service -------------------------------------------------------------------
class ControlLog(Protocol):
    def log_control(self, actor: str, action: str, detail: Mapping[str, object]) -> None:
        ...


class SessionDesk(Protocol):
    """The execution side of a session (`runtime.desk.ExecutionDesk`)."""

    async def try_arm(self, session: SessionRecord,
                      now: float) -> tuple[SessionRecord, ArmDecision | None]: ...

    async def disarm(self, session_id: str | None, *, reason: str, cancel_reason: str,
                     now: float) -> tuple[str, ...]: ...


class SessionService:
    def __init__(self, *, ledger: LedgerCycles, control_log: ControlLog, ea_state: EaState,
                 commands: CommandBoard, desk: SessionDesk | None = None) -> None:
        self._ledger = ledger
        self._control_log = control_log
        self._ea_state = ea_state
        self._commands = commands
        self._desk = desk

    @property
    def commands(self) -> CommandBoard:
        return self._commands

    async def active(self) -> SessionRecord | None:
        return await asyncio.to_thread(self._ledger.active_session)

    async def start(self, *, backend: str, mode: str, actor: str,
                    now: float) -> SessionStartOutcome:
        """Open today's session (or return the open one) and, in execute mode, arm it."""
        day = trading_day_for(int(now))
        refusal = await self._start_refusal(int(now))
        if refusal is not None:
            logger.warning("v6 session start refused: %s (%s)", *refusal)
            return SessionStartOutcome(day, None, False, *refusal)
        current = await self.active()
        if current is not None and current.trading_day != day:
            await self._close(current, actor, STOP_REASON_DAY_CHANGED, now)
        started = await asyncio.to_thread(partial(
            self._ledger.start_session, trading_day=day, backend=backend, mode=mode,
            started_at=now, armed=False))
        if started.created:
            await self._audit(actor, ACTION_SESSION_START, {
                "session_id": started.session.session_id, "trading_day": day,
                "backend": backend, "mode": mode})
        session, arm = started.session, None
        if self._desk is not None:
            session, arm = await self._desk.try_arm(started.session, now)
        return SessionStartOutcome(day, session, started.created, arm=arm)

    async def _start_refusal(self, epoch: int) -> tuple[str, str] | None:
        state = session_state(epoch)
        if state.weekend or state.rollover_block:
            closed_by = "weekend" if state.weekend else "rollover"
            return REFUSE_MARKET_CLOSED, f"market closed ({closed_by})"
        breakers = await asyncio.to_thread(self._ledger.active_breakers)
        if breakers:
            tripped = ", ".join(f"{b.scope}:{b.period_key}" for b in breakers)
            return REFUSE_BREAKER, f"breaker tripped: {tripped}"
        trade_mode = last_trade_mode(self._ea_state.view())
        if trade_mode != DEMO_TRADE_MODE:
            return REFUSE_NOT_DEMO, f"last known trade_mode is {trade_mode or 'unknown'}"
        return None

    async def stop(self, *, actor: str, reason: str, now: float) -> SessionStopOutcome:
        """Disarm and close the session (if any), queue CANCEL_PENDING, summarise the day.

        Open positions are left running on purpose.
        """
        command = self._commands.request_cancel_pending(reason, now)
        current = await self.active()
        if current is None:
            cancelled = await self._stand_down(None, reason, now)
            closed = None
        else:
            closed, cancelled = await self._close(current, actor, reason, now)
        day = trading_day_for(int(now)) if current is None else current.trading_day
        summary = await self.day_summary(day)
        return SessionStopOutcome(stopped=closed is not None, session=closed,
                                  command=command, summary=summary,
                                  cancelled_intents=cancelled)

    async def _stand_down(self, session_id: str | None, reason: str,
                          now: float) -> tuple[str, ...]:
        if self._desk is None:
            return ()
        return await self._desk.disarm(session_id, reason=reason,
                                       cancel_reason=CANCEL_REASON_SESSION_STOP, now=now)

    async def _close(self, session: SessionRecord, actor: str, reason: str,
                     now: float) -> tuple[SessionRecord | None, tuple[str, ...]]:
        cancelled = await self._stand_down(session.session_id, reason, now)
        stopped = await asyncio.to_thread(partial(
            self._ledger.stop_session, session.session_id, stopped_at=now, reason=reason))
        if not stopped:
            return None, cancelled  # closed concurrently; nothing of ours to report
        await self._audit(actor, ACTION_SESSION_STOP, {
            "session_id": session.session_id, "trading_day": session.trading_day,
            "reason": reason})
        logger.info("v6 session %s stopped by %s (%s)", session.session_id, actor, reason)
        if session.armed:
            session = replace(session, disarmed_at=now, disarm_reason=reason)
        return replace(session, stopped_at=now, stop_reason=reason, armed=False), cancelled

    async def day_summary(self, trading_day: str) -> DaySummary:
        exposure = OpenExposure.from_view(self._ea_state.view())
        counts, day_sessions = await asyncio.to_thread(read_day, self._ledger, trading_day)
        statuses = await asyncio.to_thread(read_intent_statuses, self._ledger, trading_day)
        return DaySummary.build(trading_day, counts, day_sessions, exposure, statuses)

    async def status(self, now: float) -> SessionStatus:
        current = await self.active()
        today = trading_day_for(int(now))
        summary = await self.day_summary(today if current is None else current.trading_day)
        return SessionStatus(today, current, summary, self._commands.current(now))

    async def auto_close_if_rollover(self, now: float) -> SessionStopOutcome | None:
        """Watchdog hook: close a session that reached rollover or outlived its day."""
        current = await self.active()
        if current is None:
            return None
        epoch = int(now)
        if session_state(epoch).rollover_block:
            reason = STOP_REASON_ROLLOVER
        elif current.trading_day != trading_day_for(epoch):
            reason = STOP_REASON_DAY_CHANGED
        else:
            return None
        return await self.stop(actor=ACTOR_RUNTIME, reason=reason, now=now)

    async def _audit(self, actor: str, action: str, detail: Mapping[str, object]) -> None:
        try:
            await asyncio.to_thread(self._control_log.log_control, actor, action, detail)
        except (sqlite3.Error, ValueError):
            logger.exception("v6 control log write failed for %s", action)


# --- wiring --------------------------------------------------------------------
@dataclass(frozen=True)
class ControlPlane:
    """What the control and dashboard routes (and the poll route) share."""

    sessions: SessionService
    commands: CommandBoard
    ledger: LedgerCycles
    csrf_nonce: str = field(repr=False)
    desk: SessionDesk | None = None


def build_control_plane(*, ledger: LedgerCycles, control_log: ControlLog, ea_state: EaState,
                        commands: CommandBoard | None = None,
                        desk: SessionDesk | None = None) -> ControlPlane:
    """One per app; the CSRF nonce lives only in this process."""
    board = CommandBoard() if commands is None else commands
    service = SessionService(ledger=ledger, control_log=control_log, ea_state=ea_state,
                             commands=board, desk=desk)
    return ControlPlane(sessions=service, commands=board, ledger=ledger,
                        csrf_nonce=secrets.token_urlsafe(CSRF_NONCE_BYTES), desk=desk)
