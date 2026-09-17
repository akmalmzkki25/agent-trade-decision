"""schemas/intent.py: ids, comments, the flat PollResponse rules and ExecutionReport."""

from __future__ import annotations

import json
import re
from typing import Any, Final

import pytest
from pydantic import ValidationError

from app.v6.schemas import intent
from app.v6.schemas.intent import (
    ExecutionReport, PollResponse, basket_id_for, intent_id_from_basket,
    intent_id_from_comment, new_intent_id, order_comment, order_problems,
)

from .payloads_v6 import execution_payload

NOW: Final[int] = 1_789_565_408
INTENT_ID: Final[str] = "k7w2m4pq3xza"


def live(**changes: Any) -> dict[str, Any]:
    fields: dict[str, Any] = dict(
        server_time_epoch=NOW, has_intent=True, intent_id=INTENT_ID, source="operator",
        side="buy", order_type="BUY_LIMIT", entry=4535.07, sl=4528.07, tp=4549.07, lots=0.01,
        ref_price=4535.35, max_drift_points=200, max_spread_points=35,
        valid_until_epoch=NOW + 120, pending_expiry_epoch=NOW + 1800, time_barrier_s=7200,
        magic=250570)
    return {**fields, **changes}


def as_json(payload: dict[str, Any]) -> PollResponse:
    return PollResponse.model_validate_json(json.dumps(payload))


# --- ids -----------------------------------------------------------------------------
def test_new_intent_ids_are_random_base32() -> None:
    ids = {new_intent_id() for _ in range(200)}

    assert len(ids) == 200
    assert all(re.fullmatch(intent.INTENT_ID_PATTERN, value) for value in ids)


def test_order_comment_round_trip() -> None:
    comment = order_comment(INTENT_ID)

    assert comment == "Q6:k7w2m4pq3xza" and len(comment) <= 31
    assert intent_id_from_comment(comment) == INTENT_ID
    for other in ("Q5:k7w2m4pq3xza", "Q6:K7W2M4PQ3XZA", "Q6:k7w2m4pq3xz", "", None, 7):
        assert intent_id_from_comment(other) is None  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        order_comment("not-an-id")


def test_basket_id_round_trip() -> None:
    basket_id = basket_id_for("XAUUSD", INTENT_ID)

    assert basket_id == "XAUUSD-V6B-k7w2m4pq3xza"
    assert intent_id_from_basket(basket_id) == INTENT_ID
    assert intent_id_from_basket("XAUUSD-V5B-1789565408000") is None
    assert intent_id_from_basket(None) is None  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        basket_id_for("XAU USD", INTENT_ID)
    with pytest.raises(ValueError):
        basket_id_for("XAUUSD", "x")


# --- poll response -------------------------------------------------------------------
def test_idle_and_command_responses() -> None:
    idle = as_json({"server_time_epoch": NOW})
    cancel = as_json({"server_time_epoch": NOW, "command": "CANCEL_PENDING"})

    assert (idle.has_intent, idle.require_demo, idle.sig, idle.order_type) == (
        False, 1, "", "NONE")
    assert cancel.command == "CANCEL_PENDING"


@pytest.mark.parametrize("payload", [live(), live(order_type="BUY", pending_expiry_epoch=0),
                                     live(side="sell", order_type="SELL_LIMIT", sl=4542.07,
                                          tp=4521.07)])
def test_valid_intents(payload: dict[str, Any]) -> None:
    response = as_json(payload)

    assert response.has_intent and response.source == "operator"


@pytest.mark.parametrize("field", intent.INTENT_FIELDS)
def test_an_idle_response_must_leave_every_intent_field_empty(field: str) -> None:
    value = live()[field]

    with pytest.raises(ValidationError, match=field):
        as_json({"server_time_epoch": NOW, field: value})


@pytest.mark.parametrize(("changes", "message"), [
    ({"command": "FLATTEN"}, "never travels with a command"),
    ({"intent_id": ""}, "needs intent_id and source"),
    ({"source": ""}, "needs intent_id and source"),
    ({"side": "sell"}, "order_type does not match side"),
    ({"side": ""}, "order_type does not match side"),
    ({"order_type": "NONE"}, "order_type does not match side"),
    ({"sl": 4540.0}, "own side of entry"),
    ({"tp": 4530.0}, "own side of entry"),
    ({"ref_price": 0.0}, "ref_price must be positive"),
    ({"entry": 0.0}, "finite and positive"),
    ({"max_drift_points": 0}, "drift and spread"),
    ({"max_spread_points": 0}, "drift and spread"),
    ({"time_barrier_s": 0}, "time barrier"),
    ({"magic": 250569}, "magic"),
    ({"magic": 250580}, "magic"),
    ({"valid_until_epoch": NOW}, "already invalid"),
    ({"pending_expiry_epoch": NOW + 179}, "must expire at least 60 s"),
    ({"order_type": "BUY", "pending_expiry_epoch": NOW + 1800}, "no pending expiry"),
])
def test_invalid_intents(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        as_json(live(**changes))


@pytest.mark.parametrize(("changes", "field"), [
    ({"lots": 0.02}, "lots"), ({"require_demo": 0}, "require_demo"),
    ({"source": "claude_code"}, "source"), ({"source": "openai"}, "source"),
    ({"time_barrier_s": 14_401}, "time_barrier_s"), ({"sig": "ABC"}, "sig"),
    ({"entry": -1.0}, "entry"), ({"extra": 1}, "extra"),
])
def test_field_constraints(changes: dict[str, Any], field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        as_json(live(**changes))


def test_order_problems_handle_non_finite_values() -> None:
    problems = order_problems(side="buy", order_type="BUY_LIMIT", entry=float("nan"),
                              sl=1.0, tp=2.0, lots=float("inf"), valid_until_epoch=10,
                              pending_expiry_epoch=100, time_barrier_s=60)

    assert "prices and lots must be finite and positive" in problems
    assert "lots above the 0.01 execution cap" in problems


# --- execution report ------------------------------------------------------------------
@pytest.mark.parametrize("status", ["placed", "filled", "expired", "cancelled"])
def test_accepted_and_neutral_reports(status: str) -> None:
    payload = {**execution_payload(status), "reason_code": "NONE"}
    if status == "cancelled":
        payload["reason_code"] = "COMMAND"

    assert ExecutionReport.model_validate_json(json.dumps(payload)).status == status


@pytest.mark.parametrize(("status", "reason", "ticket"), [
    ("placed", "DRIFT", 7), ("filled", "NONE", 0), ("rejected_local", "NONE", 0),
    ("failed", "NONE", 0), ("dry_run", "NONE", 0), ("placed", "UNKNOWN", 7),
])
def test_inconsistent_reports_are_refused(status: str, reason: str, ticket: int) -> None:
    payload = {**execution_payload(status), "reason_code": reason, "ticket": ticket}

    with pytest.raises(ValidationError):
        ExecutionReport.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("reason", ["DRIFT", "BAD_SIGNATURE", "DEMO_REQUIRED", "OCCUPIED",
                                    "HALTED", "BAD_INTENT", "MARKET_CLOSED"])
def test_local_rejections_carry_a_reason(reason: str) -> None:
    payload = {**execution_payload("rejected_local"), "reason_code": reason, "ticket": 0}

    assert ExecutionReport.model_validate_json(json.dumps(payload)).reason_code == reason
