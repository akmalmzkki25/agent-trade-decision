"""
The execution desk: arming, disarming and their side effects (plan sections 4, 4b, 6).

  arm_decision  `arming.decide_arm` on fresh facts: the halt file, the breakers
                evaluated on the newest EA poll (persisted trips only before the
                first poll) and the EA view
  try_arm       session start and resume ("Mulai trading skrg"): ARM when every
                check passes, DISARM (with its side effects) when one fails
  disarm        the session disarmed with its reason, undelivered intents cancelled
                (`IntentBook.cancel_all`), a pending operator packet withdrawn; the
                caller queues the EA command
  supervise     the watchdog tick: undelivered intents past their validity expire, a
                management action the EA never reported expires (`actions.ActionBoard`),
                and an armed session whose checks fail is disarmed (never re-armed here)

Shadow mode never arms. Open positions are never touched: they keep their
broker-side SL/TP and the EA's time barrier. SQLite work runs in worker threads.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Final

from ..clock import Clock
from ..config import V6Settings
from ..ledger_cycles import LedgerCycles
from ..ledger_cycles_schema import SessionRecord
from ..providers.operator_queue import OperatorQueue
from ..risk.policy import EXECUTE_MODE
from .actions import ActionBoard
from .arming import ARM, DISARM, ArmDecision, BreakerView, arm_action, decide_arm
from .breaker_feed import BreakerFeed, DayFacts
from .commands import CommandBoard
from .ea_state import EaState
from .intent_book import IntentBook

logger = logging.getLogger(__name__)

HALT_UNREADABLE: Final[str] = "halt file cannot be checked; treated as halted"
ACTION_EXPIRED: Final[str] = "EXPIRED"
DETAIL_ACTION_EXPIRED: Final[str] = "no EA report before the action went stale"


@dataclass(frozen=True)
class PersistedTrips:
    """The breaker view before any EA poll can be evaluated: persisted trips only."""

    tripped: bool


@dataclass(frozen=True)
class DeskDeps:
    settings: V6Settings
    clock: Clock
    ledger: LedgerCycles
    book: IntentBook
    commands: CommandBoard
    ea_state: EaState
    halt_path: Path
    breakers: BreakerFeed
    day: Callable[[], DayFacts | None]       # the newest snapshot's day facts
    queue: OperatorQueue | None = None
    actions: ActionBoard = field(default_factory=ActionBoard)   # the action for the EA


class ExecutionDesk:
    def __init__(self, deps: DeskDeps) -> None:
        self._deps = deps

    @property
    def deps(self) -> DeskDeps:
        return self._deps

    # --- facts -------------------------------------------------------------------------
    async def halted(self) -> bool:
        try:
            return await asyncio.to_thread(self._deps.halt_path.exists)
        except OSError:
            logger.error("v6 %s (%s)", HALT_UNREADABLE, self._deps.halt_path)
            return True

    async def breaker_view(self, now: float) -> BreakerView | None:
        """Breakers on the newest poll; None (fail closed) when they cannot be evaluated."""
        deps = self._deps
        observed = deps.ea_state.view().last_poll
        try:
            if observed is None:
                active = await asyncio.to_thread(deps.ledger.active_breakers)
                return PersistedTrips(tripped=bool(active))
            return await deps.breakers.for_poll(observed.poll, now, deps.day())
        except sqlite3.Error as exc:
            logger.error("v6 breakers could not be evaluated for arming: %s",
                         type(exc).__name__)
            return None

    async def arm_decision(self, session: SessionRecord | None, now: float) -> ArmDecision:
        halted = await self.halted()
        breakers = await self.breaker_view(now)
        return decide_arm(self._deps.settings, session, None, breakers,
                          self._deps.ea_state.view(), now=now, halted=halted)

    # --- arming ------------------------------------------------------------------------
    async def try_arm(self, session: SessionRecord,
                      now: float) -> tuple[SessionRecord, ArmDecision | None]:
        """Arm `session` when every check passes (execute mode only)."""
        deps = self._deps
        if deps.settings.mode != EXECUTE_MODE:
            return session, None
        decision = await self.arm_decision(session, now)
        action = arm_action(session, decision, may_arm=True)
        if action == ARM:
            await asyncio.to_thread(partial(deps.ledger.set_session_armed, session.session_id,
                                            True, at=now))
            logger.warning("v6 session %s ARMED: intents may be published", session.session_id)
        elif action == DISARM:
            await self._disarm_refused(session, decision, now)
        elif not decision.armed:
            logger.warning("v6 session %s stays disarmed: %s (%s)", session.session_id,
                           decision.reason, decision.detail)
        refreshed = await asyncio.to_thread(deps.ledger.active_session)
        if refreshed is None or refreshed.session_id != session.session_id:
            return session, decision
        return refreshed, decision

    async def disarm(self, session_id: str | None, *, reason: str, cancel_reason: str,
                     now: float) -> tuple[str, ...]:
        """Disarm the session, cancel undelivered intents, withdraw the pending packet.

        Returns the ids of the cancelled intents. The caller queues the EA command.
        """
        deps = self._deps
        if session_id is not None:
            await asyncio.to_thread(partial(deps.ledger.set_session_armed, session_id, False,
                                            at=now, reason=reason))
        cancelled = await asyncio.to_thread(deps.book.cancel_all, cancel_reason, now)
        if deps.queue is not None:
            deps.queue.withdraw(now)
        return tuple(record.intent_id for record in cancelled)

    async def _disarm_refused(self, session: SessionRecord, decision: ArmDecision,
                              now: float) -> None:
        await self.disarm(session.session_id, reason=decision.reason,
                          cancel_reason=decision.reason, now=now)
        self._deps.commands.request_cancel_pending(decision.reason, now)
        logger.warning("v6 session %s DISARMED: %s (%s); CANCEL_PENDING queued",
                       session.session_id, decision.reason, decision.detail)

    # --- watchdog ------------------------------------------------------------------------
    async def _expire_action(self, now: float) -> None:
        expired = self._deps.actions.pop_expired(now)
        if expired is None:
            return
        await asyncio.to_thread(self._deps.ledger.actions.mark, expired.action_id,
                                ACTION_EXPIRED, DETAIL_ACTION_EXPIRED, now)
        logger.warning("v6 action %s %s for ticket %s expired without an EA report",
                       expired.action_id, expired.command, expired.ticket)

    async def supervise(self, now: float, *, halted: bool,
                        breakers: BreakerView | None) -> ArmDecision | None:
        """Expire stale intents; disarm an armed session whose checks fail now."""
        deps = self._deps
        await asyncio.to_thread(deps.book.expire_due, now)
        await self._expire_action(now)
        session = await asyncio.to_thread(deps.ledger.active_session)
        if session is None or not session.armed:
            return None
        decision = decide_arm(deps.settings, session, None, breakers, deps.ea_state.view(),
                              now=now, halted=halted)
        if arm_action(session, decision, may_arm=False) == DISARM:
            await self._disarm_refused(session, decision, now)
        return decision
