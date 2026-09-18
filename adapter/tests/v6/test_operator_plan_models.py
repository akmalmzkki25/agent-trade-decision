"""Decision v3 building blocks: types and ranges only; ordering lives in ladder_problems."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.v6.schemas.operator_plan import (
    EntryPlanV2, M15Bias, ManageRequest, PacketPlan, ladder_problems,
)


def plan(**changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "side": "buy", "order_type": "LIMIT", "entry": 4360.5, "sl": 4353.5,
        "tp1": 4366.0, "tp2": 4371.0, "tp3": 4378.0, "sl_after_tp1": 4361.0,
        "sl_after_tp2": 4366.0, "time_limit_min": 150, "pending_expiry_min": 30,
        "lots": 0.01, "thesis": "floor 4359-4360 held three times"}
    return {**document, **changes}


def test_a_plan_parses() -> None:
    parsed = EntryPlanV2.model_validate(plan())
    assert (parsed.order_type, parsed.tp3, parsed.lots) == ("LIMIT", 4378.0, 0.01)


@pytest.mark.parametrize("changes", [
    {"order_type": "BUY_LIMIT"}, {"lots": 0}, {"time_limit_min": 241},
    {"pending_expiry_min": 61}, {"sl": -1.0}, {"thesis": "x" * 301}, {"extra": 1},
    {"tp1": float("nan")},
])
def test_types_and_ranges_are_strict(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        EntryPlanV2.model_validate(plan(**changes))


@pytest.mark.parametrize(("side", "levels", "ok"), [
    ("buy", (4360.5, 4353.5, 4366.0, 4371.0, 4378.0, 4361.0, 4366.0), True),
    ("buy", (None, 4353.5, 4366.0, 4371.0, 4378.0, None, None), True),
    ("buy", (4360.5, 4353.5, 4371.0, 4366.0, 4378.0, None, None), False),
    ("buy", (4360.5, 4361.0, 4366.0, 4371.0, 4378.0, None, None), False),
    ("buy", (4360.5, 4353.5, 4366.0, 4371.0, 4378.0, 4367.0, None), False),
    ("buy", (4360.5, 4353.5, 4366.0, 4371.0, 4378.0, 4362.0, 4361.0), False),
    ("sell", (4358.5, 4365.5, 4353.0, 4349.0, 4345.0, 4357.0, 4353.0), True),
    ("sell", (4358.5, 4365.5, 4353.0, 4349.0, 4345.0, 4366.0, None), False),
])
def test_ladder_problems(side: str, levels: tuple[Any, ...], ok: bool) -> None:
    entry, sl, tp1, tp2, tp3, first, second = levels
    problems = ladder_problems(side, entry, sl, tp1, tp2, tp3, first, second)
    assert (problems == []) is ok
    assert all(":" in text for text in problems)


def test_manage_request_lists_its_changes() -> None:
    request = ManageRequest.model_validate(
        {"target": "position", "ticket": 91, "op": "MODIFY", "sl": 4361.0,
         "time_limit_min": 200, "reason": "higher low at 4361"})
    assert request.changes == {"sl": 4361.0, "time_limit_min": 200}
    keep = ManageRequest.model_validate({"target": "pending", "ticket": 7, "op": "KEEP"})
    assert keep.changes == {}


@pytest.mark.parametrize("document", [
    {"target": "position", "ticket": 0, "op": "KEEP"},
    {"target": "order", "ticket": 1, "op": "KEEP"},
    {"target": "position", "ticket": 1, "op": "HOLD"},
    {"target": "position", "ticket": 1, "op": "MODIFY", "time_limit_min": 0},
])
def test_manage_request_types(document: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ManageRequest.model_validate(document)


def bias(document: dict[str, Any]) -> M15Bias:
    """Bias blocks arrive as JSON, where a list becomes the model's tuple."""
    return M15Bias.model_validate_json(json.dumps(document))


def test_bias_and_plan_blocks() -> None:
    parsed = bias({"direction": "range", "levels": [4355.0, 4381.5],
                   "invalidation": None, "scenario": "fade the edges"})
    assert parsed.levels == (4355.0, 4381.5)
    with pytest.raises(ValidationError):
        bias({"direction": "up", "levels": [1.0] * 7})
    with pytest.raises(ValidationError):
        bias({"direction": "sideways"})
    assert PacketPlan(tp1=0.0, tp2=0.0, sl_after_tp1=0.0, sl_after_tp2=0.0, step=0,
                      time_limit_min=0).step == 0
