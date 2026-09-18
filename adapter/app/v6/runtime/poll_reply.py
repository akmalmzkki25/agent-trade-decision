"""
The answer to one EA poll (wire contract section 6.3).

  command  FLATTEN (a new breaker trip) outranks CANCEL_PENDING (session stop, a
           disarm, a halt or a tripped breaker); a command repeats until done.
  action   only when no command is due: the waiting management action (CLOSE_POSITION,
           MODIFY_POSITION, MODIFY_PENDING), repeated until the EA reports it or it is
           too old (`actions.ActionBoard`); it outranks a new intent.
  intent   only when no command is due, in execute mode, for the active ARMED
           execute session whose arming checks pass on this very poll
           (`ExecutionDesk.arm_decision`), while that session's intent is PUBLISHED
           or DELIVERED and still valid. The first delivery is written before the
           answer (`IntentBook.next_for_poll`); a re-delivery repeats the same intent
           until the EA reports.

Every answer is signed with V6_EA_HMAC_KEY when one is configured
(`wire.sign_intent`), using the newest snapshot's point; the signature covers the
command as well. An answer that cannot be signed is logged and replaced by the
unsigned idle answer, which a signing EA ignores.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from functools import partial

from .. import wire
from ..deliberation.publication import ManagementAction
from ..ledger_cycles_schema import SessionRecord
from ..ledger_intents import IntentRecord
from ..risk.intent_builder import to_poll_response
from ..risk.policy import EXECUTE_MODE
from ..schemas.intent import PollRequest, PollResponse
from .actions import action_response
from .commands import NO_COMMAND, poll_command
from .desk import ExecutionDesk
from .intent_states import DELIVERABLE_STATUSES

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PollFacts:
    """What the route knows besides the poll: kill switches and the symbol point."""

    halted: bool
    breaker_tripped: bool
    point: float


class PollReplier:
    def __init__(self, desk: ExecutionDesk) -> None:
        self._desk = desk

    async def reply(self, poll: PollRequest, now: float, facts: PollFacts) -> PollResponse:
        """The signed answer to `poll` (recorded in the EA state before this call)."""
        deps = self._desk.deps
        command = poll_command(deps.commands, halted=facts.halted or facts.breaker_tripped,
                               pending_v6_orders=poll.pending_v6_orders,
                               open_v6_positions=poll.open_v6_positions, now=now)
        idle = PollResponse(server_time_epoch=int(now), command=command)
        response = idle
        action = deps.actions.for_poll(now) if command == NO_COMMAND else None
        if action is not None:
            response = self._with_action(idle, action, now)
        elif command == NO_COMMAND and deps.settings.mode == EXECUTE_MODE:
            response = await self._with_intent(idle, now, facts.point)
        try:
            return wire.sign_intent(deps.settings.ea_hmac_key, response, facts.point)
        except ValueError:
            # Unsigned: an EA that requires a signature ignores the answer, which is safe.
            logger.error("v6 poll answer could not be signed (point %s); answering idle",
                         facts.point)
            return idle

    @staticmethod
    def _with_action(idle: PollResponse, action: ManagementAction, now: float) -> PollResponse:
        try:
            return action_response(action, int(now))
        except ValueError as exc:  # checked when queued; never expected here
            logger.error("v6 action %s cannot be served: %s", action.action_id,
                         type(exc).__name__)
            return idle

    def _deliverable(self, now: float) -> tuple[SessionRecord, IntentRecord] | None:
        """Blocking: the armed execute session and its intent that may still be served."""
        deps = self._desk.deps
        session = deps.ledger.active_session()
        if session is None or not session.armed or session.mode != EXECUTE_MODE:
            return None
        record = deps.book.active()
        if (record is None or record.status not in DELIVERABLE_STATUSES
                or record.session_id != session.session_id
                or not now < record.valid_until_epoch):
            return None
        return session, record

    async def _with_intent(self, idle: PollResponse, now: float,
                           point: float) -> PollResponse:
        deps = self._desk.deps
        try:
            found = await asyncio.to_thread(self._deliverable, now)
        except sqlite3.Error as exc:
            logger.error("v6 poll: the intent ledger is unreadable (%s)", type(exc).__name__)
            return idle
        if found is None:
            return idle
        session, record = found
        arm = await self._desk.arm_decision(session, now)
        if not arm.armed:
            logger.info("v6 intent %s held back: %s", record.intent_id, arm.reason)
            return idle
        draft = await asyncio.to_thread(partial(deps.book.next_for_poll, now, arm=arm))
        if draft is None or draft.row.intent_id != record.intent_id:
            return idle
        try:
            return to_poll_response(draft, int(now), None, point)
        except ValueError as exc:
            logger.error("v6 intent %s cannot be served: %s", record.intent_id,
                         type(exc).__name__)
            return idle
