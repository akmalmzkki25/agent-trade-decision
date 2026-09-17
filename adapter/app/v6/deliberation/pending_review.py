"""
Reviewing a resting V6 order (user decision 2026-09-17).

While a V6 pending order rests, the OCCUPANCY gate keeps new entries out, yet the
market keeps moving: the plan behind the order may no longer hold. On such a bar
the operator gets a review packet (the order, fresh market data, no entry) and
answers `pending_action` KEEP or CANCEL. CANCEL queues the EA's CANCEL_PENDING
command, the path a session stop already uses. A review is only offered when
the system itself is healthy: a halt, a breaker, stale data or a failed account
policy already cancel or block on their own.
"""

from __future__ import annotations

from typing import Final

from ..cycle_codes import (
    GATE_ACCOUNT_POLICY, GATE_BREAKER, GATE_CLOCK_SKEW, GATE_HALTED, GATE_OCCUPANCY,
    GATE_SNAPSHOT_AGE, GATE_SPEC, GATE_WARMUP, HoldReason,
)
from ..cycle_types import MarketContext
from ..risk.gates import failed_codes
from ..types import GateResult

ACTION_CANCEL: Final[str] = "CANCEL"
REASON_AGENT_CANCEL: Final[str] = "AGENT_CANCEL"
# Gates that must pass before a review is offered.
REVIEW_BLOCKING_GATES: Final[frozenset[str]] = frozenset({
    GATE_HALTED, GATE_WARMUP, GATE_ACCOUNT_POLICY, GATE_SNAPSHOT_AGE, GATE_CLOCK_SKEW,
    GATE_SPEC, GATE_BREAKER})
DETAIL_KEPT: Final[str] = "review: the agent keeps the resting order"
DETAIL_CANCELLED: Final[str] = "review: the agent cancelled the resting order"


def wants_review(context: MarketContext, gates: tuple[GateResult, ...]) -> bool:
    """A V6 order rests (no V6 position) and only market or occupancy gates failed."""
    failed = frozenset(failed_codes(gates))
    return (GATE_OCCUPANCY in failed and not failed & REVIEW_BLOCKING_GATES
            and bool(context.pending_orders) and not context.positions)


def review_hold(pending_action: str | None) -> tuple[HoldReason, str, bool]:
    """(hold reason, detail, cancel?) for an accepted review decision."""
    if pending_action == ACTION_CANCEL:
        return HoldReason.PENDING_CANCELLED, DETAIL_CANCELLED, True
    return HoldReason.PENDING_KEPT, DETAIL_KEPT, False
