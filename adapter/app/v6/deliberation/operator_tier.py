"""
Tier 1 of the operator backend (plan section 3.3; user decisions 2026-09-16).

The sealed packet is offered on the decision channel
(`providers.operator_queue.OperatorQueue`) and the engine waits until bar close +
V6_OPERATOR_DEADLINE_S. An accepted decision becomes the panel (flagged risk
desks keep their rules views, Price Action never does); no decision in time is
a recorded Chief timeout, and the cycle holds with APP-V6-OPERATOR-TIMEOUT. When
no packet can be built (no session, not DEMO, nothing sized at the standard
tier, too late) nobody is asked: the refusal carries the cycle's hold reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ..clock import Clock
from ..config import V6Settings
from ..providers.operator_queue import CLOSE_EXPIRED, CLOSE_TIMEOUT, OperatorQueue
from ..schemas.operator import OperatorPacket
from .cycle_draft import elapsed_ms
from .operator_decision import ValidatedDecision, panel_from_decision, timeout_panel
from .operator_packet import PacketRefusal, PacketRequest, build_packet
from .panel import Baseline, PanelResult

DETAIL_TIMEOUT: Final[str] = "no operator decision before the deadline"
TIMEOUT_CLOSES: Final[frozenset[str]] = frozenset({CLOSE_TIMEOUT, CLOSE_EXPIRED})


@dataclass(frozen=True)
class OperatorRound:
    """One packet served, and the panel its decision (or its absence) produced."""

    packet: OperatorPacket
    panel: PanelResult
    decision: ValidatedDecision | None = None
    detail: str = ""

    @property
    def offered_ids(self) -> frozenset[str]:
        """The candidates the agent could pick (only those sized at the standard tier)."""
        return frozenset(self.packet.allowed.candidate_ids)

    @property
    def withdrawn_ids(self) -> frozenset[str]:
        return frozenset() if self.decision is None else self.decision.withdrawn_ids

    @property
    def agent(self) -> str | None:
        return None if self.decision is None else self.decision.agent


def _closed_detail(queue: OperatorQueue, cycle_id: str, now: float) -> str:
    """Why the cycle closed undecided: a withdrawal or a newer offer is not a timeout."""
    closed = queue.status(now).last_closed
    if closed is None or closed.cycle_id != cycle_id or closed.reason in TIMEOUT_CLOSES:
        return DETAIL_TIMEOUT
    return f"operator cycle closed without a decision ({closed.reason})"


async def operator_round(queue: OperatorQueue, request: PacketRequest, settings: V6Settings,
                         baseline: Baseline, clock: Clock) -> OperatorRound | PacketRefusal:
    """Build and offer the packet, then wait for the decision (never past the deadline)."""
    built = build_packet(request, settings)
    if isinstance(built, PacketRefusal):
        return built
    started = clock.now_epoch()
    queue.offer(built)
    deadline = float(built.expires_at_epoch if request.deadline_epoch is None
                     else min(request.deadline_epoch, built.expires_at_epoch))
    decision = await queue.await_decision(built.cycle_id, deadline)
    now = clock.now_epoch()
    if decision is None:
        panel = timeout_panel(baseline, latency_ms=elapsed_ms(started, now))
        return OperatorRound(packet=built, panel=panel,
                             detail=_closed_detail(queue, built.cycle_id, now))
    return OperatorRound(packet=built, panel=panel_from_decision(decision, baseline),
                         decision=decision)
