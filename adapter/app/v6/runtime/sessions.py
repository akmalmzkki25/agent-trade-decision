"""
Daily trading sessions and the pending EA command (plan section 4b).

"Mulai trading hari ini" opens one session per trading day; "Sudah cukup hari
ini" closes it, queues CANCEL_PENDING for the EA and returns the day summary.
Open V6 positions are never flattened here: they run to SL, TP or the time
barrier (the EA still flattens before rollover). A session left open is
closed by `auto_close_if_rollover` once the rollover block starts.

A trading day runs from 17:00 New York to 17:00 New York and is named after
the date it ends on, so the Sunday reopen belongs to Monday.

Phase 2 never arms a session: execution arrives in Phase 5. The command board
and every method here run on the event loop thread; SQLite calls go through
`asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import sqlite3
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from functools import partial
from types import MappingProxyType
from typing import Final, Protocol

from ..ledger_cycles import TRADING_DAY_PATTERN, LedgerCycles
from ..ledger_cycles_schema import CycleSummary, SessionRecord
from ..market.sessions import NEW_YORK, NY_DAILY_CLOSE, session_state
from ..schemas.intent import Command
from .ea_state import EaState, EaStateView

logger = logging.getLogger(__name__)

ACTOR_RUNTIME: Final[str] = "runtime"
ACTION_SESSION_START: Final[str] = "session_start"
ACTION_SESSION_STOP: Final[str] = "session_stop"
STOP_REASON_ROLLOVER: Final[str] = "rollover_auto_close"
STOP_REASON_DAY_CHANGED: Final[str] = "trading_day_changed"
REFUSE_BREAKER: Final[str] = "APP-V6-SESSION-BREAKER"
REFUSE_NOT_DEMO: Final[str] = "APP-V6-SESSION-NOT-DEMO"
REFUSE_MARKET_CLOSED: Final[str] = "APP-V6-SESSION-MARKET-CLOSED"
DEMO_TRADE_MODE: Final[str] = "DEMO"
CANCEL_PENDING: Final[Command] = "CANCEL_PENDING"
NO_COMMAND: Final[Command] = "NONE"
# The EA polls every ~2 s; a command nobody acknowledges is dropped after this.
CANCEL_PENDING_TTL_S: Final[float] = 600.0
CSRF_NONCE_BYTES: Final[int] = 32

_UNIX_EPOCH: Final[datetime] = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ONE_DAY: Final[timedelta] = timedelta(days=1)


# --- trading day ---------------------------------------------------------------
def trading_day_for(epoch: int) -> str:
    """ISO date of the trading day containing `epoch` (UTC seconds)."""
    if isinstance(epoch, bool) or not isinstance(epoch, int):
        raise TypeError("epoch must be int UTC seconds")
    ny = (_UNIX_EPOCH + timedelta(seconds=epoch)).astimezone(NEW_YORK)
    day = ny.date() + _ONE_DAY if ny.time() >= NY_DAILY_CLOSE else ny.date()
    return day.isoformat()


def _ny_close_epoch(day: date) -> int:
    local = datetime.combine(day, NY_DAILY_CLOSE, tzinfo=NEW_YORK)
    return int((local - _UNIX_EPOCH).total_seconds())


def trading_day_bounds(trading_day: str) -> tuple[int, int]:
    """[start, end) in UTC seconds: the previous and the same day's New York close."""
    if not TRADING_DAY_PATTERN.match(trading_day):
        raise ValueError("trading_day must be YYYY-MM-DD")
    day = date.fromisoformat(trading_day)
    return _ny_close_epoch(day - _ONE_DAY), _ny_close_epoch(day)


# --- pending EA command --------------------------------------------------------
@dataclass(frozen=True)
class PendingCommand:
    command: Command
    reason: str
    requested_at: float
    expires_at: float
    delivered_at: float | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class CommandBoard:
    """At most one pending EA command; each change replaces the frozen value."""

    def __init__(self) -> None:
        self._pending: PendingCommand | None = None

    def request_cancel_pending(self, reason: str, now: float) -> PendingCommand:
        self._pending = PendingCommand(
            command=CANCEL_PENDING, reason=reason, requested_at=now,
            expires_at=now + CANCEL_PENDING_TTL_S)
        return self._pending

    def current(self, now: float) -> PendingCommand | None:
        if self._pending is not None and now >= self._pending.expires_at:
            self._pending = None
        return self._pending

    def command_for_poll(self, pending_v6_orders: int, now: float) -> Command:
        """Command for this poll. Cleared once a poll after delivery reports no pending order."""
        pending = self.current(now)
        if pending is None:
            return NO_COMMAND
        if pending.delivered_at is not None and pending_v6_orders == 0:
            self._pending = None
            return NO_COMMAND
        if pending.delivered_at is None:
            self._pending = replace(pending, delivered_at=now)
        return pending.command


def poll_command(board: CommandBoard, *, halted: bool, pending_v6_orders: int,
                 now: float) -> Command:
    """What `/v6/intent/poll` should answer. The board is consulted on every poll."""
    queued = board.command_for_poll(pending_v6_orders, now)
    return CANCEL_PENDING if halted else queued


# --- summaries -----------------------------------------------------------------
@dataclass(frozen=True)
class OpenExposure:
    """What the EA last said is still running; None everywhere until it has polled."""

    open_v6_positions: int | None
    pending_v6_orders: int | None
    floating_pnl_v6: float | None
    local_halt: bool | None
    observed_at: float | None

    @classmethod
    def from_view(cls, view: EaStateView) -> "OpenExposure":
        if view.last_poll is None:
            return cls(None, None, None, None, None)
        poll = view.last_poll.poll
        return cls(poll.open_v6_positions, poll.pending_v6_orders, poll.floating_pnl_v6,
                   poll.local_halt, view.last_poll.received_at)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def last_trade_mode(view: EaStateView) -> str | None:
    """Trade mode from the newest EA poll or snapshot; None if the EA was never seen."""
    seen: list[tuple[float, str]] = []
    if view.last_poll is not None:
        seen.append((view.last_poll.received_at, view.last_poll.poll.trade_mode))
    if view.last_snapshot is not None:
        seen.append((view.last_snapshot.received_at, view.last_snapshot.trade_mode))
    return max(seen, key=lambda item: item[0])[1] if seen else None


def session_to_dict(record: SessionRecord) -> dict[str, object]:
    return {**asdict(record), "active": record.is_active}


def read_day(ledger: LedgerCycles, trading_day: str,
             ) -> tuple[CycleSummary, tuple[SessionRecord, ...]]:
    """Blocking: cycle counts and sessions of one trading day."""
    start, end = trading_day_bounds(trading_day)
    return ledger.cycle_summary(start, end), ledger.sessions_for_day(trading_day)


@dataclass(frozen=True)
class DaySummary:
    trading_day: str
    day_start_epoch: int
    day_end_epoch: int
    cycles: int
    by_status: Mapping[str, int]
    hold_reasons: Mapping[str, int]
    shadow_intents: int
    sessions: tuple[SessionRecord, ...]
    exposure: OpenExposure

    @classmethod
    def build(cls, trading_day: str, counts: CycleSummary,
              sessions: tuple[SessionRecord, ...], exposure: OpenExposure) -> "DaySummary":
        start, end = trading_day_bounds(trading_day)
        return cls(
            trading_day=trading_day, day_start_epoch=start, day_end_epoch=end,
            cycles=counts.total, by_status=MappingProxyType(dict(counts.by_status)),
            hold_reasons=MappingProxyType(dict(counts.by_hold_reason)),
            shadow_intents=counts.shadow_entries, sessions=tuple(sessions), exposure=exposure)

    def to_dict(self) -> dict[str, object]:
        return {
            "trading_day": self.trading_day, "day_start_epoch": self.day_start_epoch,
            "day_end_epoch": self.day_end_epoch, "cycles": self.cycles,
            "by_status": dict(self.by_status), "hold_reasons": dict(self.hold_reasons),
            "shadow_intents": self.shadow_intents,
            "sessions": [session_to_dict(s) for s in self.sessions],
            "exposure": self.exposure.to_dict(),
        }


def _session_or_none(record: SessionRecord | None) -> dict[str, object] | None:
    return None if record is None else session_to_dict(record)


@dataclass(frozen=True)
class SessionStartOutcome:
    trading_day: str
    session: SessionRecord | None
    created: bool
    refusal: str | None = None
    detail: str = ""

    @property
    def refused(self) -> bool:
        return self.refusal is not None

    def to_dict(self) -> dict[str, object]:
        return {"trading_day": self.trading_day, "created": self.created,
                "refusal": self.refusal, "detail": self.detail,
                "session": _session_or_none(self.session)}


@dataclass(frozen=True)
class SessionStopOutcome:
    stopped: bool
    session: SessionRecord | None
    command: PendingCommand
    summary: DaySummary

    def to_dict(self) -> dict[str, object]:
        return {"stopped": self.stopped, "session": _session_or_none(self.session),
                "command": self.command.to_dict(), "summary": self.summary.to_dict()}


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


class SessionService:
    def __init__(self, *, ledger: LedgerCycles, control_log: ControlLog, ea_state: EaState,
                 commands: CommandBoard) -> None:
        self._ledger = ledger
        self._control_log = control_log
        self._ea_state = ea_state
        self._commands = commands

    @property
    def commands(self) -> CommandBoard:
        return self._commands

    async def active(self) -> SessionRecord | None:
        return await asyncio.to_thread(self._ledger.active_session)

    async def start(self, *, backend: str, mode: str, actor: str,
                    now: float) -> SessionStartOutcome:
        """Open today's session, or return it when it is already open (never armed)."""
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
        return SessionStartOutcome(day, started.session, started.created)

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
        """Close the session (if any), queue CANCEL_PENDING, summarise the day.

        Open positions are left running on purpose.
        """
        command = self._commands.request_cancel_pending(reason, now)
        current = await self.active()
        closed = None if current is None else await self._close(current, actor, reason, now)
        day = trading_day_for(int(now)) if current is None else current.trading_day
        summary = await self.day_summary(day)
        return SessionStopOutcome(stopped=closed is not None, session=closed,
                                  command=command, summary=summary)

    async def _close(self, session: SessionRecord, actor: str, reason: str,
                     now: float) -> SessionRecord | None:
        stopped = await asyncio.to_thread(partial(
            self._ledger.stop_session, session.session_id, stopped_at=now, reason=reason))
        if not stopped:
            return None  # closed concurrently; nothing of ours to report
        await self._audit(actor, ACTION_SESSION_STOP, {
            "session_id": session.session_id, "trading_day": session.trading_day,
            "reason": reason})
        logger.info("v6 session %s stopped by %s (%s)", session.session_id, actor, reason)
        return replace(session, stopped_at=now, stop_reason=reason, armed=False)

    async def day_summary(self, trading_day: str) -> DaySummary:
        exposure = OpenExposure.from_view(self._ea_state.view())
        counts, day_sessions = await asyncio.to_thread(read_day, self._ledger, trading_day)
        return DaySummary.build(trading_day, counts, day_sessions, exposure)

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


def build_control_plane(*, ledger: LedgerCycles, control_log: ControlLog,
                        ea_state: EaState) -> ControlPlane:
    """One per app; the CSRF nonce lives only in this process."""
    commands = CommandBoard()
    service = SessionService(ledger=ledger, control_log=control_log, ea_state=ea_state,
                             commands=commands)
    return ControlPlane(sessions=service, commands=commands, ledger=ledger,
                        csrf_nonce=secrets.token_urlsafe(CSRF_NONCE_BYTES))
