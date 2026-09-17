"""
The life of one published intent, as a pure transition table (plan section 4, restart).

    PUBLISHED --poll delivers--> DELIVERED --EA "placed"--> REPORTED --EA "filled"--> FILLED
        |                            |                          |                      |
        +-> EXPIRED / CANCELLED /    +-> FILLED (market order)  +-> EXPIRED / CANCELLED /
            REJECTED (not delivered)  +-> REJECTED / EXPIRED /       REJECTED       CLOSED
                                          CANCELLED

PUBLISHED  stored and offered on the next poll, until `valid_until_epoch`.
DELIVERED  a poll response carried it; the EA must now report what it did.
REPORTED   the EA placed the pending (limit) order at the broker.
FILLED     the position is open (market fill, or the limit order filled).
CLOSED     the position closed (SL, TP, time barrier, FLATTEN); the V6 basket result.
EXPIRED    never delivered in time, or the pending order expired at the broker.
CANCELLED  withdrawn by the adapter (session stop, halt, breaker) or by CANCEL_PENDING.
REJECTED   refused by the EA or the broker, or by the adapter before delivery.

The last four are terminal. A move to the current state is not a transition
(callers treat it as a no-op); everything else not listed is illegal.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

IntentStatus = Literal[
    "PUBLISHED", "DELIVERED", "REPORTED", "FILLED", "CLOSED", "EXPIRED", "CANCELLED", "REJECTED"]
INTENT_STATUSES: Final[tuple[IntentStatus, ...]] = (
    "PUBLISHED", "DELIVERED", "REPORTED", "FILLED", "CLOSED", "EXPIRED", "CANCELLED", "REJECTED")
ACTIVE_STATUSES: Final[frozenset[IntentStatus]] = frozenset(
    {"PUBLISHED", "DELIVERED", "REPORTED", "FILLED"})
TERMINAL_STATUSES: Final[frozenset[IntentStatus]] = frozenset(
    {"CLOSED", "EXPIRED", "CANCELLED", "REJECTED"})
# Statuses a poll may still offer to the EA (re-delivery is idempotent on the EA side).
DELIVERABLE_STATUSES: Final[frozenset[IntentStatus]] = frozenset({"PUBLISHED", "DELIVERED"})

TRANSITIONS: Final[Mapping[IntentStatus, frozenset[IntentStatus]]] = MappingProxyType({
    "PUBLISHED": frozenset({"DELIVERED", "EXPIRED", "CANCELLED", "REJECTED"}),
    "DELIVERED": frozenset({"REPORTED", "FILLED", "EXPIRED", "CANCELLED", "REJECTED"}),
    "REPORTED": frozenset({"FILLED", "EXPIRED", "CANCELLED", "REJECTED"}),
    "FILLED": frozenset({"CLOSED"}),
    "CLOSED": frozenset(),
    "EXPIRED": frozenset(),
    "CANCELLED": frozenset(),
    "REJECTED": frozenset(),
})

# `ExecutionReport.status` (schemas.intent.ExecutionStatus) -> the status it moves to.
REPORT_TARGETS: Final[Mapping[str, IntentStatus]] = MappingProxyType({
    "placed": "REPORTED",
    "filled": "FILLED",
    "rejected_local": "REJECTED",
    "failed": "REJECTED",
    "dry_run": "REJECTED",
    "expired": "EXPIRED",
    "cancelled": "CANCELLED",
})


class IllegalIntentTransition(ValueError):
    """A move the table does not allow; `current` and `target` name it."""

    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"intent cannot move from {str(current)[:16]} to {str(target)[:16]}")
        self.current = current
        self.target = target


def is_status(value: object) -> bool:
    return isinstance(value, str) and value in TRANSITIONS


def can_transition(current: object, target: object) -> bool:
    """True only for a listed move; unknown statuses and same-state moves are False."""
    if not (is_status(current) and is_status(target)):
        return False
    return target in TRANSITIONS[current]  # type: ignore[index]


def require_transition(current: object, target: object) -> None:
    """Raise IllegalIntentTransition unless `current -> target` is listed."""
    if not can_transition(current, target):
        raise IllegalIntentTransition(str(current), str(target))


def is_active(status: object) -> bool:
    return isinstance(status, str) and status in ACTIVE_STATUSES


def is_terminal(status: object) -> bool:
    return isinstance(status, str) and status in TERMINAL_STATUSES


def target_for_report(report_status: object) -> IntentStatus:
    """The status an EA execution report moves an intent to (ValueError if unknown)."""
    if not isinstance(report_status, str) or report_status not in REPORT_TARGETS:
        raise ValueError(f"unknown execution status {str(report_status)[:16]!r}")
    return REPORT_TARGETS[report_status]
