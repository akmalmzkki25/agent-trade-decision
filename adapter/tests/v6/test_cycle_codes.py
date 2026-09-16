"""Shared Phase 2 code sets: gate order, hold-reason mapping, ids and feature keys."""

from __future__ import annotations

import re

import pytest

from app.v6 import cycle_codes, cycle_types
from app.v6.cycle_codes import (
    CANDIDATE_VERDICTS, CYCLE_STATUSES, FEATURE_KEYS, GATE_BREAKER, GATE_CLOCK_SKEW, GATE_CODES,
    GATE_HALTED, GATE_HOLD_REASONS, GATE_LLM_BUDGET, GATE_NEWS, GATE_SNAPSHOT_AGE, GATE_SPREAD,
    GATE_WARMUP, SETUP_NAMES, VETO_CODES, HoldReason, candidate_id_for, hold_reason_for_gates,
)
from app.v6.schemas.agents import ID_PATTERN
from app.v6.types import GateResult

BAR_T = 1_789_560_000


def _gate(code: str, passed: bool) -> GateResult:
    return GateResult(code=code, passed=passed)


def test_code_sets_are_unique_and_non_empty() -> None:
    for codes in (CYCLE_STATUSES, GATE_CODES, SETUP_NAMES, CANDIDATE_VERDICTS):
        assert codes and len(set(codes)) == len(codes)
    assert set(GATE_HOLD_REASONS) <= set(GATE_CODES)
    assert len(VETO_CODES) == 5
    assert all(reason.value.startswith("APP-V6-") for reason in HoldReason)


def test_every_public_code_is_re_exported_by_cycle_types() -> None:
    assert all(hasattr(cycle_types, name) for name in cycle_codes.__all__)
    assert all(hasattr(cycle_codes, name) for name in cycle_codes.__all__)


def test_all_gates_passing_gives_no_hold_reason() -> None:
    assert hold_reason_for_gates([]) is None
    assert hold_reason_for_gates(_gate(code, True) for code in GATE_CODES) is None


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        (GATE_HALTED, HoldReason.HALTED),
        (GATE_WARMUP, HoldReason.WARMUP),
        (GATE_SNAPSHOT_AGE, HoldReason.STALE),
        (GATE_CLOCK_SKEW, HoldReason.STALE),
        (GATE_BREAKER, HoldReason.BREAKER),
        (GATE_LLM_BUDGET, HoldReason.BUDGET),
        (GATE_SPREAD, HoldReason.GATE),
        (GATE_NEWS, HoldReason.GATE),
        ("SOMETHING_NEW", HoldReason.GATE),
    ],
)
def test_failed_gate_maps_to_its_hold_reason(code: str, reason: HoldReason) -> None:
    assert hold_reason_for_gates([_gate(code, False)]) is reason


def test_first_failed_gate_wins() -> None:
    gates = (_gate(GATE_HALTED, True), _gate(GATE_SPREAD, False), _gate(GATE_BREAKER, False))

    assert hold_reason_for_gates(gates) is HoldReason.GATE
    assert hold_reason_for_gates(reversed(gates)) is HoldReason.BREAKER


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("displacement", "buy", BAR_T), f"displacement-buy-{BAR_T}"),
        (("orb", "sell", BAR_T, "ny"), f"orb-sell-{BAR_T}-ny"),
        (("engulfing", "buy", 0, "v2"), "engulfing-buy-0-v2"),
    ],
)
def test_candidate_ids_are_deterministic_and_valid(args: tuple, expected: str) -> None:
    candidate_id = candidate_id_for(*args)

    assert candidate_id == expected
    assert re.match(ID_PATTERN, candidate_id)
    assert candidate_id_for(*args) == candidate_id


@pytest.mark.parametrize(
    "args",
    [
        ("fvg", "buy", BAR_T),
        ("orb", "long", BAR_T),
        ("orb", "buy", -1),
        ("orb", "buy", 1.5),
        ("orb", "buy", True),
        ("orb", "buy", BAR_T, "NY"),
        ("orb", "buy", BAR_T, "a-b"),
        ("orb", "buy", BAR_T, "x" * 13),
        ("retest", "sell", 10**60),
    ],
)
def test_candidate_id_refuses_bad_parts(args: tuple) -> None:
    with pytest.raises(ValueError):
        candidate_id_for(*args)


def test_feature_keys_are_snake_case_and_listed() -> None:
    constants = {getattr(cycle_codes, name) for name in cycle_codes.__all__
                 if name.startswith("F_")}

    assert constants == FEATURE_KEYS
    assert all(re.fullmatch(r"[a-z0-9_]+", key) for key in FEATURE_KEYS)
