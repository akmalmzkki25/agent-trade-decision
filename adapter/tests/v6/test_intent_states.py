"""The intent transition table: every legal move, every illegal one, report mapping."""

from __future__ import annotations

import itertools
from typing import Any, Final, get_args

import pytest

from app.v6.runtime import intent_states as states
from app.v6.runtime.intent_states import (
    ACTIVE_STATUSES, DELIVERABLE_STATUSES, INTENT_STATUSES, REPORT_TARGETS, TERMINAL_STATUSES,
    TRANSITIONS, IllegalIntentTransition, can_transition, is_active, is_status, is_terminal,
    require_transition, target_for_report,
)
from app.v6.schemas.intent import ExecutionStatus

LEGAL: Final[frozenset[tuple[str, str]]] = frozenset({
    ("PUBLISHED", "DELIVERED"), ("PUBLISHED", "EXPIRED"), ("PUBLISHED", "CANCELLED"),
    ("PUBLISHED", "REJECTED"),
    ("DELIVERED", "REPORTED"), ("DELIVERED", "FILLED"), ("DELIVERED", "EXPIRED"),
    ("DELIVERED", "CANCELLED"), ("DELIVERED", "REJECTED"),
    ("REPORTED", "FILLED"), ("REPORTED", "EXPIRED"), ("REPORTED", "CANCELLED"),
    ("REPORTED", "REJECTED"),
    ("FILLED", "CLOSED"),
})
ALL_PAIRS: Final[tuple[tuple[str, str], ...]] = tuple(
    itertools.product(INTENT_STATUSES, INTENT_STATUSES))


def test_status_sets_partition_the_statuses() -> None:
    assert INTENT_STATUSES == get_args(states.IntentStatus)
    assert set(TRANSITIONS) == set(INTENT_STATUSES)
    assert ACTIVE_STATUSES | TERMINAL_STATUSES == set(INTENT_STATUSES)
    assert not ACTIVE_STATUSES & TERMINAL_STATUSES
    assert DELIVERABLE_STATUSES < ACTIVE_STATUSES
    assert all(not TRANSITIONS[status] for status in TERMINAL_STATUSES)


def test_the_table_is_exactly_the_documented_one() -> None:
    table = {(source, target) for source, targets in TRANSITIONS.items() for target in targets}

    assert table == LEGAL


@pytest.mark.parametrize(("current", "target"), sorted(LEGAL))
def test_legal_moves(current: str, target: str) -> None:
    assert can_transition(current, target)
    require_transition(current, target)


@pytest.mark.parametrize(("current", "target"),
                         [pair for pair in ALL_PAIRS if pair not in LEGAL])
def test_illegal_moves(current: str, target: str) -> None:
    assert not can_transition(current, target)
    with pytest.raises(IllegalIntentTransition) as caught:
        require_transition(current, target)
    assert (caught.value.current, caught.value.target) == (current, target)
    assert isinstance(caught.value, ValueError)


@pytest.mark.parametrize(("current", "target"), [
    ("published", "DELIVERED"), ("PUBLISHED", "SENT"), (None, "DELIVERED"), ("FILLED", 3),
    (["PUBLISHED"], "DELIVERED"),
])
def test_unknown_statuses_are_never_legal(current: Any, target: Any) -> None:
    assert not can_transition(current, target)
    with pytest.raises(IllegalIntentTransition):
        require_transition(current, target)


def test_every_path_ends_in_a_terminal_status() -> None:
    def ends(status: str, seen: tuple[str, ...]) -> bool:
        if is_terminal(status):
            return True
        return all(ends(nxt, seen + (status,)) for nxt in TRANSITIONS[status]
                   if nxt not in seen) and bool(TRANSITIONS[status])

    assert ends("PUBLISHED", ())


@pytest.mark.parametrize("status", INTENT_STATUSES)
def test_predicates(status: str) -> None:
    assert is_status(status)
    assert is_active(status) is (status in ACTIVE_STATUSES)
    assert is_terminal(status) is (status in TERMINAL_STATUSES)


@pytest.mark.parametrize("value", [None, 1, "closed", ""])
def test_predicates_on_junk(value: Any) -> None:
    assert not is_status(value) and not is_active(value) and not is_terminal(value)


def test_every_execution_status_has_a_target() -> None:
    assert set(REPORT_TARGETS) == set(get_args(ExecutionStatus))
    assert target_for_report("placed") == "REPORTED"
    assert target_for_report("filled") == "FILLED"
    assert {target_for_report(s) for s in ("rejected_local", "failed", "dry_run")} == {"REJECTED"}
    assert target_for_report("expired") == "EXPIRED"
    assert target_for_report("cancelled") == "CANCELLED"
    for status, target in REPORT_TARGETS.items():
        assert can_transition("DELIVERED", target), status


@pytest.mark.parametrize("value", ["closed", "", None, 5])
def test_unknown_report_status(value: Any) -> None:
    with pytest.raises(ValueError, match="unknown execution status"):
        target_for_report(value)
