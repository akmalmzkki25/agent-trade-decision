"""
Publishing an approved operator entry (plan sections 3.3, 5 and 6).

The engine hands over the protocol's ENTER with the sized exit plan, the agent's plan
(order type, TP ladder, SL+ steps, windows) and the agent that decided
(`deliberation.publication`). Only for an armed execute session whose arming checks
still pass right now does `risk.intent_builder.build_intent` make the intent and
`IntentBook.publish` store it as PUBLISHED; the EA's next signed poll carries it. A
refusal keeps its code in `PublishOutcome.code` and becomes either a shadow record
(the session cannot publish at all) or a hold.

`manage` queues a management decision under the same arming rule: CANCEL becomes the
EA's CANCEL_PENDING command, CLOSE and MODIFY a `ManagementAction` stored in v6_actions
(PUBLISHED) and served on the next polls (`actions.ActionBoard`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from ..cycle_codes import HoldReason
from ..deliberation.publication import (
    ManageDispatch, ManagementAction, ManageOutcome, PublishOutcome, PublishRequest,
)
from ..ledger_actions import ActionRow
from ..risk import intent_builder as ib
from ..risk.policy import EXECUTE_MODE
from ..types import Refusal
from . import arming
from . import intent_book as book
from .actions import action_response
from .desk import ExecutionDesk

NOT_ARMED: Final[str] = "SESSION_NOT_ARMED"
REASON_AGENT_CANCEL: Final[str] = "AGENT_CANCEL"
CODE_CANCEL_PENDING: Final[str] = "CANCEL_PENDING"
CODE_ACTION_INVALID: Final[str] = "ACTION_INVALID"
STATUS_SUPERSEDED: Final[str] = "EXPIRED"
MAX_DETAIL_CHARS: Final[int] = 240

# Why publishing was refused -> the hold the cycle records (POLICY_* codes are GATE).
HOLD_FOR_CODE: Final[Mapping[str, HoldReason]] = MappingProxyType({
    arming.DISARM_HALTED: HoldReason.HALTED, arming.DISARM_EA_HALT: HoldReason.HALTED,
    arming.DISARM_BREAKER: HoldReason.BREAKER, arming.DISARM_EA_NOT_SEEN: HoldReason.STALE,
    arming.DISARM_EA_STALE: HoldReason.STALE, arming.DISARM_NOT_DEMO: HoldReason.GATE,
    arming.DISARM_POLICY: HoldReason.GATE, arming.DISARM_SETTINGS: HoldReason.GATE,
    ib.NOT_EXECUTE_MODE: HoldReason.GATE, ib.QUOTE_ACCOUNT: HoldReason.GATE,
    ib.BAD_INPUT: HoldReason.ERROR, ib.NOT_ENTER: HoldReason.ERROR,
    ib.CANDIDATE_MISMATCH: HoldReason.ERROR, ib.QUOTE_UNUSABLE: HoldReason.STALE,
    ib.QUOTE_STALE: HoldReason.STALE, ib.BAD_GEOMETRY: HoldReason.EXIT,
    ib.OFF_GRID: HoldReason.EXIT, ib.LIMIT_NOT_PASSIVE: HoldReason.EXIT,
    ib.MARKET_STOP_BELOW_FLOOR: HoldReason.EXIT, ib.MARKET_REWARD_BELOW_1R: HoldReason.EXIT,
    ib.STOP_NOT_BEYOND: HoldReason.EXIT, ib.MARKET_MOVED: HoldReason.EXIT,
    ib.LOT_LIMIT: HoldReason.SIZE, ib.RISK_OVER_BUDGET: HoldReason.SIZE,
    ib.TOO_LATE: HoldReason.LATE, book.BOOK_OCCUPANCY_UNKNOWN: HoldReason.STALE,
    book.BOOK_OCCUPIED: HoldReason.GATE, book.BOOK_ACTIVE_INTENT: HoldReason.GATE,
    book.BOOK_TOO_LATE: HoldReason.LATE, book.BOOK_CYCLE_USED: HoldReason.ERROR,
    book.BOOK_DUPLICATE: HoldReason.ERROR,
})
# The session cannot publish at all: record the decision only.
SHADOW_CODES: Final[frozenset[str]] = frozenset({
    NOT_ARMED, ib.SESSION_INACTIVE, ib.SESSION_NOT_ARMED, arming.DISARM_NO_SESSION,
    arming.DISARM_SESSION_MODE, arming.DISARM_MODE,
})


def refused(code: str, detail: str) -> PublishOutcome:
    """The outcome of a refusal `code`: a shadow record or a hold."""
    text = f"not published: {code}" + (f" ({detail})" if detail else "")
    if code in SHADOW_CODES:
        return PublishOutcome(code=code, detail=text[:MAX_DETAIL_CHARS])
    reason = HOLD_FOR_CODE.get(code, HoldReason.GATE)
    return PublishOutcome(hold_reason=reason, code=code, detail=text[:MAX_DETAIL_CHARS])


class IntentPublisher:
    def __init__(self, desk: ExecutionDesk) -> None:
        self._desk = desk

    async def publish(self, request: PublishRequest) -> PublishOutcome:
        """Build and store the intent for an approved ENTER; never raises on a refusal.

        SQLite errors propagate (the engine records the cycle as ERROR).
        """
        deps = self._desk.deps
        now = deps.clock.now_epoch()
        session = await asyncio.to_thread(deps.ledger.active_session)
        if session is None or not session.armed or session.mode != EXECUTE_MODE:
            return refused(NOT_ARMED, "no armed execute session")
        arm = await self._desk.arm_decision(session, now)
        observed = deps.ea_state.view().last_poll
        if not arm.armed or observed is None:   # arming needs a poll: never None here
            return refused(arm.reason, arm.detail)
        quote = ib.ReferenceQuote.from_poll(observed.poll, observed.received_at)
        draft = ib.build_intent(request.decision, request.candidate, request.exit_plan,
                                request.sizing, request.context, deps.settings, session, now,
                                agent=request.agent, quote=quote, trade=request.plan)
        if isinstance(draft, Refusal):
            return refused(next(iter(draft.codes), ib.BAD_INPUT), draft.detail)
        occupancy = book.Occupancy.from_poll(observed.poll)
        booked = await asyncio.to_thread(deps.book.publish, draft, occupancy, now)
        if not booked.ok:
            return refused(booked.code, booked.detail)
        return PublishOutcome(intent_id=draft.row.intent_id, code=booked.code)

    async def cancel_pending(self, reason: str) -> tuple[str, ...]:
        """The agent's CANCEL: the EA deletes the V6 pending orders on its next poll and
        reports them; an intent it never received is cancelled here."""
        deps = self._desk.deps
        now = deps.clock.now_epoch()
        deps.commands.request_cancel_pending(reason, now)
        cancelled = await asyncio.to_thread(deps.book.cancel_all, reason, now)
        return tuple(record.intent_id for record in cancelled)

    async def manage(self, dispatch: ManageDispatch) -> ManageOutcome:
        """Queue a management action (or CANCEL_PENDING) for the armed session."""
        deps = self._desk.deps
        now = deps.clock.now_epoch()
        session = await asyncio.to_thread(deps.ledger.active_session)
        if session is None or not session.armed or session.mode != EXECUTE_MODE:
            return ManageOutcome(False, NOT_ARMED, "no armed execute session")
        arm = await self._desk.arm_decision(session, now)
        if not arm.armed:
            return ManageOutcome(False, arm.reason, "the session fails its arming checks")
        action = dispatch.action
        if dispatch.request.op == "CANCEL" or action is None:
            cancelled = await self.cancel_pending(REASON_AGENT_CANCEL)
            return ManageOutcome(True, CODE_CANCEL_PENDING,
                                 f"cancel queued ({len(cancelled)} undelivered intents "
                                 "cancelled)")
        try:
            action_response(action, int(now))
        except ValueError:
            return ManageOutcome(False, CODE_ACTION_INVALID,
                                 f"{action.command} breaks the wire rules of its command")
        await self._store(action, dispatch, session.session_id, now)
        return ManageOutcome(True, action.command,
                             f"{action.command} {action.action_id} queued for ticket "
                             f"{action.ticket}")

    async def _store(self, action: ManagementAction, dispatch: ManageDispatch,
                     session_id: str, now: float) -> None:
        """Record the action as PUBLISHED, then serve it (a waiting one is superseded)."""
        deps = self._desk.deps
        row = ActionRow(action_id=action.action_id, cycle_id=action.cycle_id,
                        session_id=session_id, agent=dispatch.agent, command=action.command,
                        ticket=action.ticket, intent_id=action.intent_id,
                        payload=action.payload(), created_at=now, updated_at=now)
        await asyncio.to_thread(deps.ledger.actions.insert, row)
        replaced = deps.actions.queue(action)
        if replaced is not None:
            await asyncio.to_thread(deps.ledger.actions.mark, replaced.action_id,
                                    STATUS_SUPERSEDED, f"superseded by {action.action_id}", now)
