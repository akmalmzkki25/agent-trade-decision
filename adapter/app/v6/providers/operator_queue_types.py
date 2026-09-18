"""
Records of the operator decision queue (see `operator_queue`): refusal and close codes,
the pending and closed cycles, submission results and the status the routes show.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Final, Literal

from ..deliberation.operator_decision import ValidatedDecision
from ..schemas.operator import (
    DECISION_ERR_AGENT, DECISION_ERR_EXPIRED, DECISION_ERR_STALE, OperatorPacket,
)
from .base import MS_PER_SECOND

HISTORY_SIZE: Final[int] = 64
MAX_WAIT_S: Final[float] = 25.0                 # the /wait long-poll ceiling
VIA_WAIT: Final[str] = "wait"
VIA_SUBMIT: Final[str] = "submit"

SUBMIT_ACCEPTED: Final[str] = "ACCEPTED"
REFUSE_UNKNOWN_CYCLE: Final[str] = "UNKNOWN_CYCLE"
REFUSE_EXPIRED: Final[str] = "EXPIRED"
REFUSE_HASH_MISMATCH: Final[str] = "HASH_MISMATCH"
REFUSE_AGENT_NOT_ALLOWED: Final[str] = "AGENT_NOT_ALLOWED"
REFUSE_ALREADY_DECIDED: Final[str] = "ALREADY_DECIDED"
REFUSE_INVALID: Final[str] = "INVALID"
REFUSAL_CODES: Final[frozenset[str]] = frozenset({
    REFUSE_UNKNOWN_CYCLE, REFUSE_EXPIRED, REFUSE_HASH_MISMATCH, REFUSE_AGENT_NOT_ALLOWED,
    REFUSE_ALREADY_DECIDED, REFUSE_INVALID,
})
# validate_decision codes reported under the queue's own names; any other one is INVALID.
REFUSAL_FOR_ERROR: Final[Mapping[str, str]] = MappingProxyType({
    DECISION_ERR_STALE: REFUSE_HASH_MISMATCH, DECISION_ERR_EXPIRED: REFUSE_EXPIRED,
    DECISION_ERR_AGENT: REFUSE_AGENT_NOT_ALLOWED,
})

CloseReason = Literal["decided", "timeout", "expired", "superseded", "cancelled"]
CLOSE_DECIDED: Final[CloseReason] = "decided"
CLOSE_TIMEOUT: Final[CloseReason] = "timeout"            # the engine stopped waiting
CLOSE_EXPIRED: Final[CloseReason] = "expired"            # the packet's expires_at passed
CLOSE_SUPERSEDED: Final[CloseReason] = "superseded"      # a newer cycle was offered
CLOSE_CANCELLED: Final[CloseReason] = "cancelled"        # engine cancelled, or withdrawn
CLOSE_REASONS: Final[tuple[CloseReason, ...]] = (
    CLOSE_DECIDED, CLOSE_TIMEOUT, CLOSE_EXPIRED, CLOSE_SUPERSEDED, CLOSE_CANCELLED)


def _positive(value: float) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and value > 0


def _elapsed_ms(start: float, end: float) -> int:
    elapsed = end - start
    return max(0, int(elapsed * MS_PER_SECOND)) if math.isfinite(elapsed) else 0


@dataclass(frozen=True)
class PendingCycle:
    cycle_id: str
    packet: OperatorPacket
    offered_at: float

    @property
    def expires_at(self) -> int:
        return self.packet.expires_at_epoch

    def expired(self, now: float) -> bool:
        return not now <= self.expires_at

    def to_dict(self) -> dict[str, object]:
        packet = self.packet
        return {"cycle_id": self.cycle_id, "session_id": packet.session_id,
                "bar_open_epoch": packet.bar_open_epoch,
                "created_at_epoch": packet.created_at_epoch,
                "expires_at_epoch": packet.expires_at_epoch, "offered_at": self.offered_at,
                "candidates": list(packet.allowed.candidate_ids), "mode": packet.mode}


@dataclass(frozen=True)
class ClosedCycle:
    cycle_id: str
    reason: CloseReason
    closed_at: float
    decision: ValidatedDecision | None = None

    def to_dict(self) -> dict[str, object]:
        agent = None if self.decision is None else self.decision.agent
        return {"cycle_id": self.cycle_id, "reason": self.reason, "closed_at": self.closed_at,
                "agent": agent}


@dataclass(frozen=True)
class Accepted:
    cycle_id: str
    agent: str
    at: float
    flagged: tuple[str, ...] = ()
    latency_ms: int = 0

    @property
    def accepted(self) -> bool:
        return True

    @property
    def code(self) -> str:
        return SUBMIT_ACCEPTED

    def to_dict(self) -> dict[str, object]:
        return {"accepted": True, "code": self.code, "cycle_id": self.cycle_id,
                "agent": self.agent, "flagged": list(self.flagged),
                "latency_ms": self.latency_ms, "at": self.at}


@dataclass(frozen=True)
class Refused:
    """`error` is the DECISION_ERR_* behind the refusal; `detail` never echoes the body."""

    code: str
    detail: str
    at: float
    cycle_id: str = ""
    agent: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        if self.code not in REFUSAL_CODES:
            raise ValueError(f"unknown refusal code {self.code[:40]!r}")

    @property
    def accepted(self) -> bool:
        return False

    def to_dict(self) -> dict[str, object]:
        return {"accepted": False, "code": self.code, "error": self.error,
                "detail": self.detail, "cycle_id": self.cycle_id, "agent": self.agent,
                "at": self.at}


SubmitResult = Accepted | Refused


@dataclass(frozen=True)
class AgentSighting:
    agent: str
    at: float
    via: str


@dataclass(frozen=True)
class QueueCounts:
    offered: int = 0
    decided: int = 0
    timeout: int = 0
    expired: int = 0
    superseded: int = 0
    cancelled: int = 0
    refused: int = 0


@dataclass(frozen=True)
class QueueStatus:
    """What the dashboard and GET /v6/operator/status show (no free text, no secrets)."""

    pending: PendingCycle | None
    waiting: bool                         # the engine is awaiting the pending decision
    last_agent: AgentSighting | None
    last_submit: SubmitResult | None
    last_closed: ClosedCycle | None
    counts: QueueCounts

    def to_dict(self) -> dict[str, object]:
        def dumped(value: PendingCycle | ClosedCycle | SubmitResult | None) -> object:
            return None if value is None else value.to_dict()

        return {"pending": dumped(self.pending), "waiting": self.waiting,
                "last_agent": None if self.last_agent is None else asdict(self.last_agent),
                "last_submit": dumped(self.last_submit),
                "last_closed": dumped(self.last_closed), "counts": asdict(self.counts)}
