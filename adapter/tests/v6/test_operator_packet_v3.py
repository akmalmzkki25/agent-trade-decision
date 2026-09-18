"""Operator packet v3: states, blocks, template and the packet builder."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.v6.deliberation.operator_packet import PacketRequest, build_packet
from app.v6.deliberation.packet_extras import pending_order_block, position_block
from app.v6.deliberation.plan_rules import bounds_from_packet
from app.v6.ledger_actions import ActionRow
from app.v6.ledger_intents import IntentRecord
from app.v6.schemas.operator_plan import M15Bias

from . import engine_fixtures_v6 as ef
from . import operator_fixtures_v6 as of


def test_a_flat_packet_holds_by_default() -> None:
    sealed = of.packet()
    template = sealed.decision_template
    assert (sealed.schema_version, sealed.packet_kind, sealed.state) == (
        "v6.operator.packet.3", "m15", "flat")
    assert (template.schema_version, template.action, template.manage) == (
        "v6.operator.decision.3", "HOLD", None)
    assert template.m15_bias.direction == "unclear"
    bounds = bounds_from_packet(sealed)
    assert (bounds.entry.modify_distance, bounds.time_limit_max, bounds.max_lots) == (
        0.37, 240, 0.03)


@pytest.mark.parametrize(("state", "ticket"), [("position", 91), ("pending", 77)])
def test_a_managed_packet_keeps_by_default(state: str, ticket: int) -> None:
    sealed = of.managed_packet(state)
    manage = sealed.decision_template.manage
    assert (sealed.decision_template.action, manage.target, manage.ticket, manage.op) == (
        "MANAGE", state, ticket, "KEEP")


def test_the_last_bias_is_echoed_in_the_template() -> None:
    bias = {"direction": "range", "levels": [4526.0, 4541.0], "invalidation": None,
            "scenario": "fade the box"}
    sealed = of.packet(last_bias=bias, last_bias_at_epoch=of.BAR_OPEN)
    assert sealed.decision_template.m15_bias.levels == (4526.0, 4541.0)


@pytest.mark.parametrize("changes", [
    {"state": "position"},
    {"state": "flat", "position": of.position_block()},
    {"state": "pending", "position": of.position_block(), "pending_order": of.pending_block()},
    {"state": "pending", "pending_order": of.pending_block()},  # candidates still offered
    {"packet_kind": "m1"},
    {"schema_version": "v6.operator.packet.2"},
])
def test_inconsistent_bodies_are_refused(changes: dict) -> None:
    with pytest.raises(ValidationError):
        of.packet(**changes)


def record(**changes) -> IntentRecord:
    fields = dict(intent_id="k7w2m4pq3xza", cycle_id="c-00000000000000aa",
                  session_id="sess-1", agent="claude_code", source="operator",
                  status="FILLED", side="buy", order_type="BUY_LIMIT", entry=4296.5,
                  sl=4289.0, tp=4310.0, lots=0.02, risk_usd=15.8, valid_until_epoch=1,
                  pending_expiry_epoch=2, time_barrier_s=9000, created_at=1.0,
                  tp1=4301.0, tp2=4305.0, sl_after_tp1=4297.0, sl_after_tp2=4301.0,
                  plan_step=1)
    return IntentRecord(**{**fields, **changes})


POSITION = {"ticket": 91, "magic": 250570, "side": "buy", "volume": 0.02,
            "price_open": 4296.5, "sl": 4297.0, "tp": 4310.0, "profit": 7.0, "swap": 0.0,
            "open_epoch": ef.AS_OF - 1200, "comment": "Q6:k7w2m4pq3xza",
            "mae_points": 150.0, "mfe_points": 500.0}
RESTING = {"ticket": 77, "magic": 250570, "order_type": "BUY_STOP", "price": 4303.5,
           "sl": 4296.5, "tp": 4317.0, "volume": 0.01, "expiration_epoch": ef.AS_OF + 900,
           "comment": "Q6:k7w2m4pq3xza"}


def context_with(**payload):
    return ef.market_context(ef.engine_snapshot(**payload))


def test_position_block() -> None:
    block = position_block(context_with(positions=[POSITION]), record())
    assert (block["ticket"], block["initial_sl"], block["plan"]["step"]) == (91, 4289.0, 1)
    assert block["minutes_open"] == pytest.approx(20.0)
    assert block["r_now"] == pytest.approx((ef.PRICE - 4296.5) / 7.5, abs=0.01)
    assert block["plan"]["time_limit_min"] == 150
    assert block["time_limit_epoch"] == POSITION["open_epoch"] + 9000


def test_position_block_without_a_record() -> None:
    block = position_block(context_with(positions=[POSITION]), None, 7200)
    assert block["initial_sl"] == 4297.0 and block["plan"]["tp1"] == 0.0
    assert (block["plan"]["step"], block["time_limit_epoch"]) == (
        0, POSITION["open_epoch"] + 7200)


def test_a_rewritten_comment_keeps_the_stored_intent_id() -> None:
    renamed = {**POSITION, "comment": "[sl 4297.00]"}
    block = position_block(context_with(positions=[renamed]), record())
    assert block["intent_id"] == "k7w2m4pq3xza"
    assert position_block(context_with(positions=[renamed]), None)["intent_id"] == ""


def test_pending_block_carries_the_plan() -> None:
    block = pending_order_block(context_with(pending_orders=[RESTING]),
                                record(order_type="BUY_STOP", plan_step=0))
    assert (block["order_type"], block["plan"]["tp1"], block["plan"]["step"]) == (
        "BUY_STOP", 4301.0, 0)
    assert block["distance_from_quote"] == pytest.approx(4303.5 - ef.ask_price())


@pytest.mark.parametrize(("order_type", "price", "expected"), [
    ("BUY_LIMIT", 4297.0, 4300.2 - 4297.0),
    ("SELL_LIMIT", 4304.0, 4304.0 - 4300.0),
    ("SELL_STOP", 4296.0, 4300.0 - 4296.0),
])
def test_distance_is_how_far_the_market_must_travel(order_type: str, price: float,
                                                    expected: float) -> None:
    sell = order_type.startswith("SELL")
    resting = {**RESTING, "order_type": order_type, "price": price,
               "sl": price + 7.0 if sell else price - 7.0,
               "tp": price - 14.0 if sell else price + 14.0}
    block = pending_order_block(context_with(pending_orders=[resting]), None)
    assert block["distance_from_quote"] == pytest.approx(expected)
    assert block["plan"]["tp1"] == 0.0


def build(context, **changes):
    request = PacketRequest(
        context=context, gates=(), offered=(), baseline=ef.rules_views(context),
        remaining_loss_usd=50.0, session_id="sess-1", armed=True, now=ef.RECEIVED,
        **changes)
    return build_packet(request, ef.settings(backend="operator", mode="execute",
                                             operator_token="t" * 40,
                                             ea_hmac_key="k" * 40))


def test_the_builder_serves_a_position_packet() -> None:
    sealed = build(context_with(positions=[POSITION]), state="position", record=record())
    assert sealed.state == "position" and sealed.position.ticket == 91
    assert sealed.candidates == () and sealed.limits.agent_entry_possible is False
    assert sealed.decision_template.manage.op == "KEEP"


def test_the_builder_serves_a_pending_packet_with_memory() -> None:
    action = ActionRow(action_id="abcdefghijkl", cycle_id="c-00000000000000aa",
                       session_id="sess-1", agent="claude_code", command="MODIFY_PENDING",
                       ticket=77, status="APPLIED", detail="ok\x07", updated_at=1234.9)
    bias = M15Bias(direction="up", levels=(4296.0,), scenario="higher lows")
    sealed = build(context_with(pending_orders=[RESTING]), state="pending",
                   record=record(order_type="BUY_STOP", plan_step=0), last_action=action,
                   last_bias=bias, last_bias_at=ef.AS_OF - 900)
    assert sealed.pending_order.ticket == 77 and sealed.position is None
    assert (sealed.last_action.op, sealed.last_action.detail, sealed.last_action.at_epoch) == (
        "MODIFY_PENDING", "ok", 1234)
    assert sealed.decision_template.m15_bias == bias
    assert sealed.last_bias_at_epoch == ef.AS_OF - 900


def test_the_builder_serves_a_flat_packet() -> None:
    sealed = build(ef.market_context())
    assert (sealed.state, sealed.pending_order, sealed.position) == ("flat", None, None)
    assert sealed.limits.time_limit_max_minutes == 240
    assert sealed.limits.buy_stop_min > sealed.market.ask
