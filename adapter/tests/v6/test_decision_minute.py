"""Decisions on an m1 packet: views and bias may be null; the inherited views apply."""

from __future__ import annotations

import json
from typing import Any

from app.v6.deliberation.decision_parts import DecisionError, ValidatedDecision
from app.v6.deliberation.operator_decision import validate_decision
from app.v6.deliberation.trade_state import BiasMemory
from app.v6.schemas.operator import OperatorPacket
from app.v6.schemas.operator_plan import M15Bias

from . import engine_fixtures_v6 as ef
from .test_engine_operator import OPERATOR
from .test_minute_packet import MINUTE_CLOSE, sealed_minute_packet


def submit(packet: OperatorPacket, **changes: Any):
    template = packet.decision_template.model_dump(mode="json")
    document = {**template, "agent": "claude_code", **changes}
    return validate_decision(packet, json.dumps(document).encode("utf-8"),
                             ef.settings(**OPERATOR), now=float(MINUTE_CLOSE + 5))


def plan(packet: OperatorPacket) -> dict[str, Any]:
    limits = packet.limits
    entry = round(limits.buy_limit_max - 1.0, 2)
    sl = round(entry - limits.stop_floor - 0.5, 2)
    risk = entry - sl
    return {"side": "buy", "order_type": "LIMIT", "entry": entry, "sl": sl,
            "tp1": round(entry + 0.6 * risk, 2), "tp2": round(entry + 1.2 * risk, 2),
            "tp3": round(entry + 2.0 * risk, 2), "sl_after_tp1": None, "sl_after_tp2": None,
            "time_limit_min": 90, "pending_expiry_min": 20, "lots": 0.01, "thesis": "m1 test"}


def test_the_m1_template_is_a_valid_hold() -> None:
    decision = submit(sealed_minute_packet())
    assert isinstance(decision, ValidatedDecision), decision
    assert (decision.action, decision.bias, decision.price_action.abstain) == ("HOLD", None, True)


def test_an_m1_enter_takes_the_entry_at_the_minimum_conviction() -> None:
    packet = sealed_minute_packet()
    decision = submit(packet, action="ENTER", entry_plan=plan(packet))
    assert isinstance(decision, ValidatedDecision), decision
    ranked = decision.price_action.ranked[0]
    assert (ranked.candidate_id, ranked.verdict) == (packet.limits.agent_entry_id, "TAKE")
    assert ranked.conviction == packet.allowed.pa_min_conviction
    assert decision.chief.action == "ENTER" and decision.plan is not None


def test_an_m15_decision_for_an_m1_packet_is_refused() -> None:
    decision = submit(sealed_minute_packet(), packet_kind="m15")
    assert isinstance(decision, DecisionError) and decision.code == "DECISION_KIND"


def test_an_m1_decision_may_carry_a_bias() -> None:
    bias = {"direction": "up", "levels": [4300.0], "invalidation": 4290.0, "scenario": "x",
            "carried": False}
    decision = submit(sealed_minute_packet(), m15_bias=bias)
    assert isinstance(decision, ValidatedDecision) and decision.bias is not None


def test_a_carried_bias_keeps_the_remembered_one() -> None:
    memory = BiasMemory()
    original = M15Bias(direction="up", scenario="first")
    memory.remember(original, 100)
    memory.remember(M15Bias(direction="up", scenario="first", carried=True), 200)
    assert memory.latest() == (original, 100)
    fresh = BiasMemory()
    fresh.remember(M15Bias(direction="unclear", carried=True), 300)
    assert fresh.latest()[1] == 300
