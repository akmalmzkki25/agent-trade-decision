"""
Publishing an approved operator entry (plan sections 3.3, 5 and 6).

The engine hands over the protocol's ENTER with the sized exit plan and the agent
that decided (`deliberation.publication`). Only for an armed execute session
whose arming checks still pass right now does `risk.intent_builder.build_intent`
make the intent and `IntentBook.publish` store it as PUBLISHED; the EA's next
signed poll carries it. A refusal keeps its code in `PublishOutcome.code` and
becomes either a shadow record (the session cannot publish at all) or a hold.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from ..cycle_codes import HoldReason
from ..deliberation.publication import PublishOutcome, PublishRequest
from ..risk import intent_builder as ib
from ..risk.policy import EXECUTE_MODE
from ..types import Refusal
from . import arming
from . import intent_book as book
from .desk import ExecutionDesk

NOT_ARMED: Final[str] = "SESSION_NOT_ARMED"
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
                                agent=request.agent, quote=quote)
        if isinstance(draft, Refusal):
            return refused(next(iter(draft.codes), ib.BAD_INPUT), draft.detail)
        occupancy = book.Occupancy.from_poll(observed.poll)
        booked = await asyncio.to_thread(deps.book.publish, draft, occupancy, now)
        if not booked.ok:
            return refused(booked.code, booked.detail)
        return PublishOutcome(intent_id=draft.row.intent_id, code=booked.code)
