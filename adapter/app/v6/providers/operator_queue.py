"""
The operator decision queue (plan section 3.3): one pending cycle at a time.

The engine offers a sealed packet and awaits the decision; operator agents
(Claude Code, Codex or Antigravity) long-poll `take_pending` through
POST /v6/operator/wait and answer through `submit` (POST /v6/operator/decision). Serving a packet
never consumes it: it stays available until it is decided or closed.

A pending cycle closes when it is decided, when a newer offer supersedes it,
when the engine stops waiting (timeout or cancellation), when its packet
expires, or when it is withdrawn (session stop, halt). Closed cycles are
remembered (bounded) so a late or repeated submission gets a precise refusal:
UNKNOWN_CYCLE, EXPIRED, HASH_MISMATCH, AGENT_NOT_ALLOWED, ALREADY_DECIDED or
INVALID (the decision itself failed validation). A refused submission leaves
the cycle pending, so the agent may correct it before the deadline.

Everything runs on the event loop thread; records are frozen and replaced on
change. Waits last real seconds: the remaining time is read from the clock.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import replace

import anyio

from ..clock import Clock
from ..config import OPERATOR_AGENTS, V6Settings
from ..deliberation.operator_decision import (
    DecisionError, ValidatedDecision, parse_envelope, validate_decision,
)
from ..schemas.operator import OperatorPacket
from .operator_queue_types import (  # re-exported: tests and routes import them from here
    CLOSE_CANCELLED, CLOSE_DECIDED, CLOSE_EXPIRED, CLOSE_REASONS, CLOSE_SUPERSEDED,
    CLOSE_TIMEOUT, HISTORY_SIZE, MAX_WAIT_S, REFUSAL_CODES, REFUSAL_FOR_ERROR,
    REFUSE_AGENT_NOT_ALLOWED, REFUSE_ALREADY_DECIDED, REFUSE_EXPIRED, REFUSE_HASH_MISMATCH,
    REFUSE_INVALID, REFUSE_UNKNOWN_CYCLE, SUBMIT_ACCEPTED, VIA_SUBMIT, VIA_WAIT, Accepted,
    AgentSighting, ClosedCycle, CloseReason, PendingCycle, QueueCounts, QueueStatus,
    Refused, SubmitResult, _elapsed_ms, _positive,
)

__all__ = [
    "CLOSE_CANCELLED", "CLOSE_DECIDED", "CLOSE_EXPIRED", "CLOSE_REASONS", "CLOSE_SUPERSEDED",
    "CLOSE_TIMEOUT", "HISTORY_SIZE", "MAX_WAIT_S", "REFUSAL_CODES", "REFUSAL_FOR_ERROR",
    "REFUSE_AGENT_NOT_ALLOWED", "REFUSE_ALREADY_DECIDED", "REFUSE_EXPIRED",
    "REFUSE_HASH_MISMATCH", "REFUSE_INVALID", "REFUSE_UNKNOWN_CYCLE", "SUBMIT_ACCEPTED",
    "VIA_SUBMIT", "VIA_WAIT", "Accepted", "AgentSighting", "ClosedCycle", "CloseReason",
    "OperatorQueue", "PendingCycle", "QueueCounts", "QueueStatus", "Refused", "SubmitResult",
]

logger = logging.getLogger(__name__)


class OperatorQueue:
    """One pending operator cycle at a time; see the module docstring."""

    def __init__(self, *, settings: V6Settings, clock: Clock,
                 history_size: int = HISTORY_SIZE) -> None:
        if isinstance(history_size, bool) or not isinstance(history_size, int) \
                or history_size < 1:
            raise ValueError("history_size must be a positive int")
        self._settings = settings
        self._clock = clock
        self._history_size = history_size
        self._pending: PendingCycle | None = None
        # Events bind to the running loop on first use (Python 3.10+).
        self._closed_signal = asyncio.Event()      # set when the pending cycle closes
        self._offer_signal = asyncio.Event()       # set, then replaced, on every offer
        self._closed: OrderedDict[str, ClosedCycle] = OrderedDict()
        self._last_agent: AgentSighting | None = None
        self._last_submit: SubmitResult | None = None
        self._counts = QueueCounts()
        self._waiters = 0

    @property
    def pending(self) -> PendingCycle | None:
        return self._pending

    def _now(self, now: float | None) -> float:
        return self._clock.now_epoch() if now is None else now

    # --- engine side ------------------------------------------------------------------
    def offer(self, packet: OperatorPacket) -> PendingCycle:
        """Serve `packet`; a still pending older cycle closes as superseded."""
        if not isinstance(packet, OperatorPacket):
            raise TypeError("offer() takes a sealed OperatorPacket")
        cycle_id = packet.cycle_id
        pending = self._pending
        if cycle_id in self._closed or (pending is not None and pending.cycle_id == cycle_id):
            raise ValueError("this cycle was already offered")
        now = self._clock.now_epoch()
        if pending is not None:
            self._close(pending, CLOSE_SUPERSEDED, now)
        self._pending = PendingCycle(cycle_id=cycle_id, packet=packet, offered_at=now)
        self._closed_signal = asyncio.Event()
        self._counts = replace(self._counts, offered=self._counts.offered + 1)
        self._offer_signal.set()
        self._offer_signal = asyncio.Event()
        logger.info("v6 operator cycle %s offered (candidates=%d, expires_at=%d)", cycle_id,
                    len(packet.candidates), packet.expires_at_epoch)
        return self._pending

    async def await_decision(self, cycle_id: str, deadline_epoch: float,
                             clock: Clock | None = None) -> ValidatedDecision | None:
        """The accepted decision, or None once the deadline (or the packet expiry) passed.

        A cycle that was superseded, withdrawn or never offered also gives None.
        Cancelling the caller closes the cycle as cancelled.
        """
        pending = self._pending
        if pending is None or pending.cycle_id != cycle_id:
            return self._decision_of(cycle_id)
        timer = self._clock if clock is None else clock
        wait_s = min(deadline_epoch, pending.expires_at) - timer.now_epoch()
        closed = self._closed_signal
        self._waiters += 1
        try:
            if _positive(wait_s):
                with anyio.move_on_after(wait_s):
                    await closed.wait()
        except asyncio.CancelledError:
            self._close_if_pending(cycle_id, CLOSE_CANCELLED)
            raise
        finally:
            self._waiters -= 1
        self._close_if_pending(cycle_id, CLOSE_TIMEOUT)
        return self._decision_of(cycle_id)

    def withdraw(self, now: float | None = None) -> ClosedCycle | None:
        """Close the pending cycle without a decision (session stop, halt)."""
        pending = self._pending
        return None if pending is None else self._close(pending, CLOSE_CANCELLED, self._now(now))

    # --- operator side ----------------------------------------------------------------
    def closed(self, cycle_id: str) -> ClosedCycle | None:
        """How a recent cycle closed (None once it left the bounded history)."""
        return self._closed.get(cycle_id)

    def pending_kind(self, now: float | None = None) -> str | None:
        """The packet kind of the pending cycle ("m15" or "m1"), None when nothing is open."""
        packet = self.current(now)
        return None if packet is None else packet.packet_kind

    def current(self, now: float | None = None) -> OperatorPacket | None:
        """The packet to serve, or None; an expired pending cycle is closed here."""
        pending = self._pending
        if pending is None:
            return None
        at = self._now(now)
        if pending.expired(at):
            self._close(pending, CLOSE_EXPIRED, at)
            return None
        return pending.packet

    async def take_pending(self, wait_s: float) -> OperatorPacket | None:
        """The pending packet (not consumed), waiting up to `wait_s` (<= MAX_WAIT_S) for one."""
        packet = self.current()
        if packet is not None or not _positive(wait_s):
            return packet
        offered = self._offer_signal
        with anyio.move_on_after(min(float(wait_s), MAX_WAIT_S)):
            await offered.wait()
        return self.current()

    def note_agent(self, agent: str, now: float) -> None:
        """An agent asked for work (POST /wait naming itself)."""
        if agent not in OPERATOR_AGENTS:
            raise ValueError("unknown operator agent")
        self._last_agent = AgentSighting(agent=agent, at=now, via=VIA_WAIT)

    def submit(self, raw: bytes, now: float) -> SubmitResult:
        """Validate and apply one submission (see the module docstring for the codes)."""
        envelope = parse_envelope(raw)
        if isinstance(envelope, DecisionError):
            return self._refuse(Refused(code=REFUSE_INVALID, detail=envelope.detail, at=now,
                                        error=envelope.code))
        agent = envelope.agent
        self._last_agent = AgentSighting(agent=agent, at=now, via=VIA_SUBMIT)
        pending = self._pending
        if pending is not None and pending.cycle_id == envelope.cycle_id and pending.expired(now):
            self._close(pending, CLOSE_EXPIRED, now)
            pending = None
        if pending is None or pending.cycle_id != envelope.cycle_id:
            return self._refuse(self._closed_refusal(envelope.cycle_id, agent, now))
        result = validate_decision(pending.packet, envelope, self._settings, now=now)
        if isinstance(result, DecisionError):
            return self._refuse(Refused(
                code=REFUSAL_FOR_ERROR.get(result.code, REFUSE_INVALID), detail=result.detail,
                at=now, cycle_id=pending.cycle_id, agent=agent, error=result.code))
        decision = replace(result, latency_ms=_elapsed_ms(pending.offered_at, now))
        self._close(pending, CLOSE_DECIDED, now, decision)
        accepted = Accepted(cycle_id=decision.cycle_id, agent=agent, at=now,
                            flagged=decision.flagged_roles, latency_ms=decision.latency_ms)
        self._last_submit = accepted
        logger.info("v6 operator decision accepted: %s", decision.summary())
        return accepted

    def status(self, now: float | None = None) -> QueueStatus:
        self.current(now)
        return QueueStatus(
            pending=self._pending, waiting=self._waiters > 0, last_agent=self._last_agent,
            last_submit=self._last_submit,
            last_closed=next(reversed(self._closed.values()), None), counts=self._counts)

    # --- bookkeeping ------------------------------------------------------------------
    def _decision_of(self, cycle_id: str) -> ValidatedDecision | None:
        closed = self._closed.get(cycle_id)
        return None if closed is None else closed.decision

    def _close_if_pending(self, cycle_id: str, reason: CloseReason) -> None:
        pending = self._pending
        if pending is not None and pending.cycle_id == cycle_id:
            self._close(pending, reason, self._clock.now_epoch())

    def _close(self, pending: PendingCycle, reason: CloseReason, now: float,
               decision: ValidatedDecision | None = None) -> ClosedCycle:
        closed = ClosedCycle(cycle_id=pending.cycle_id, reason=reason, closed_at=now,
                             decision=decision)
        self._closed[closed.cycle_id] = closed
        while len(self._closed) > self._history_size:
            self._closed.popitem(last=False)
        self._pending = None
        self._counts = replace(self._counts, **{reason: getattr(self._counts, reason) + 1})
        self._closed_signal.set()
        if reason != CLOSE_DECIDED:
            logger.warning("v6 operator cycle %s closed without a decision: %s",
                           closed.cycle_id, reason)
        return closed

    def _closed_refusal(self, cycle_id: str, agent: str, now: float) -> Refused:
        closed = self._closed.get(cycle_id)
        if closed is None:
            return Refused(code=REFUSE_UNKNOWN_CYCLE, detail="no pending cycle has this id",
                           at=now, agent=agent)
        if closed.decision is not None:
            return Refused(code=REFUSE_ALREADY_DECIDED,
                           detail=f"already decided by {closed.decision.agent}", at=now,
                           cycle_id=cycle_id, agent=agent)
        return Refused(code=REFUSE_EXPIRED, detail=f"closed without a decision ({closed.reason})",
                       at=now, cycle_id=cycle_id, agent=agent)

    def _refuse(self, refused: Refused) -> Refused:
        self._last_submit = refused
        self._counts = replace(self._counts, refused=self._counts.refused + 1)
        logger.warning("v6 operator decision refused: %s (%s) cycle=%s agent=%s", refused.code,
                       refused.error or "-", refused.cycle_id or "-", refused.agent or "-")
        return refused
