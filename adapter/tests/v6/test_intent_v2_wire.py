"""Intent v2: STOP orders, the TP ladder and signed management commands."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from app.v6 import wire
from app.v6.schemas.intent import ACTION_COMMANDS, ActionReport, PollResponse, order_problems

KEY = SecretStr("ea-hmac-key-" + "e" * 40)
NOW = 1_789_650_950


def intent(**changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = dict(
        server_time_epoch=NOW, command="NONE", has_intent=True, intent_id="k7w2m4pq3xza",
        source="operator", side="buy", order_type="BUY_STOP", entry=4370.0, sl=4363.5,
        tp=4384.0, lots=0.01, ref_price=4366.89, max_drift_points=130,
        max_spread_points=50, valid_until_epoch=NOW + 100, pending_expiry_epoch=NOW + 1800,
        time_barrier_s=9000, magic=250570, tp1=4374.0, tp2=4378.0, sl_after_tp1=4370.5,
        sl_after_tp2=4374.0)
    return {**document, **changes}


def action(**changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = dict(
        server_time_epoch=NOW, command="MODIFY_POSITION", action_id="m3a7q2z5k6pw",
        action_ticket=91, action_sl=4361.0, action_tp=4378.0, action_tp1=0.0,
        action_tp2=4371.0, action_sl1=0.0, action_sl2=4366.0, action_barrier_s=10800,
        action_issued_epoch=NOW)
    return {**document, **changes}


def test_a_stop_intent_with_a_ladder_is_valid() -> None:
    response = PollResponse(**intent())
    assert response.schema_version == "v6.intent.2"
    assert (response.tp1, response.sl_after_tp2) == (4374.0, 4374.0)


@pytest.mark.parametrize("changes", [
    {"tp1": 4385.0}, {"tp2": 4373.0}, {"sl_after_tp1": 4375.0},
    {"sl_after_tp2": 4369.0}, {"tp1": 0.0}, {"pending_expiry_epoch": 0},
    {"order_type": "SELL_STOP"},
])
def test_ladder_and_pending_rules(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PollResponse(**intent(**changes))


def test_a_ladder_is_optional() -> None:
    plain = intent(tp1=0.0, tp2=0.0, sl_after_tp1=0.0, sl_after_tp2=0.0,
                   order_type="BUY_LIMIT", entry=4360.5, sl=4353.5, tp=4374.5)
    assert PollResponse(**plain).tp1 == 0.0


def test_order_problems_accept_the_ladder_keywords() -> None:
    assert order_problems(side="sell", order_type="SELL_STOP", entry=4350.0, sl=4357.0,
                          tp=4336.0, lots=0.01, valid_until_epoch=100,
                          pending_expiry_epoch=1000, time_barrier_s=3600, tp1=4346.0,
                          tp2=4341.0, sl_after_tp1=4350.5, sl_after_tp2=0.0) == []


def test_management_commands() -> None:
    assert ACTION_COMMANDS == {"CLOSE_POSITION", "MODIFY_POSITION", "MODIFY_PENDING"}
    assert PollResponse(**action()).action_ticket == 91
    close = dict(server_time_epoch=NOW, command="CLOSE_POSITION",
                 action_id="m3a7q2z5k6pw", action_ticket=91, action_issued_epoch=NOW)
    assert PollResponse(**close).command == "CLOSE_POSITION"
    pending = action(command="MODIFY_PENDING", action_price=4359.0,
                     action_expiry_epoch=NOW + 900)
    assert PollResponse(**pending).action_price == 4359.0


@pytest.mark.parametrize("document", [
    action(action_id=""),
    action(action_ticket=0),
    action(action_sl=0.0),
    action(action_barrier_s=0),
    action(action_price=4359.0),
    action(command="MODIFY_PENDING", action_price=0.0, action_expiry_epoch=NOW + 900),
    dict(server_time_epoch=NOW, command="CLOSE_POSITION", action_id="m3a7q2z5k6pw",
         action_ticket=91, action_issued_epoch=NOW, action_sl=4361.0),
    dict(server_time_epoch=NOW, command="NONE", action_id="m3a7q2z5k6pw"),
    {**intent(), "command": "MODIFY_POSITION", "action_id": "m3a7q2z5k6pw",
     "action_ticket": 91, "action_issued_epoch": NOW},
])
def test_bad_management_commands(document: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PollResponse(**document)


def test_the_canonical_string_covers_the_new_fields() -> None:
    response = PollResponse(**intent())
    text = wire.intent_canonical(response, 0.01)
    assert text.startswith("v6.intent.2|")
    assert text.endswith("|437400|437800|437050|437400||0|0|0|0|0|0|0|0|0|0|0")
    assert len(text.split("|")) == len(wire.CANONICAL_FIELDS) == 36
    signed = wire.sign_intent(KEY, response, 0.01)
    assert wire.verify_intent(KEY, signed, 0.01)
    moved = signed.model_copy(update={"tp1": 4375.0})
    assert not wire.verify_intent(KEY, moved, 0.01)
    command = wire.sign_intent(KEY, PollResponse(**action()), 0.01)
    assert wire.intent_canonical(command, 0.01).endswith(
        "|m3a7q2z5k6pw|91|436100|437800|0|437100|0|436600|0|0|10800|1789650950")
    assert wire.verify_intent(KEY, command, 0.01)


def report(**changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = dict(
        schema_version="v6.action.1", kind="APPLIED", action_id="m3a7q2z5k6pw",
        command="MODIFY_POSITION", intent_id="k7w2m4pq3xza", ticket=91, reason_code="NONE",
        retcode=10009, step=0, old_sl=4353.5, new_sl=4361.0, price=4366.2,
        sent_at_epoch=NOW)
    return {**document, **changes}


def test_action_reports() -> None:
    assert ActionReport(**report()).kind == "APPLIED"
    step = report(kind="PLAN_STEP", action_id="", command="NONE", step=1)
    assert ActionReport(**step).step == 1
    rejected = report(kind="REJECTED", reason_code="SL_WIDER", retcode=0)
    assert ActionReport(**rejected).reason_code == "SL_WIDER"


@pytest.mark.parametrize("changes", [
    {"kind": "APPLIED", "reason_code": "STALE"},
    {"kind": "REJECTED", "reason_code": "NONE"},
    {"kind": "PLAN_STEP", "action_id": "", "command": "NONE", "step": 0},
    {"kind": "PLAN_STEP", "step": 1},
    {"kind": "APPLIED", "action_id": ""},
    {"command": "FLATTEN"},
])
def test_bad_action_reports(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ActionReport(**report(**changes))
