"""schemas/operator.py: sealed packets, the packet hash and the decision template."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.v6.config import V6Settings
from app.v6.deliberation.operator_decision import ValidatedDecision, validate_decision
from app.v6.deliberation.protocol import REBUTTAL_STANCES
from app.v6.schemas import operator as op
from app.v6.schemas.operator import OperatorPacket, canonical_json, equity_band, packet_hash

from . import operator_fixtures_v6 as of


# --- helpers -------------------------------------------------------------------------
@pytest.mark.parametrize(("equity", "band"), [
    (0.0, "lt_1k"), (999.99, "lt_1k"), (1000.0, "1k_2k"), (2010.5, "2k_5k"),
    (9999.0, "5k_10k"), (10_000.0, "10k_50k"), (50_000.0, "ge_50k"), (92_429.34, "ge_50k"),
])
def test_equity_band(equity: float, band: str) -> None:
    assert equity_band(equity) == band


def test_canonical_json_is_sorted_compact_ascii() -> None:
    assert canonical_json({"b": 1, "a": "é"}) == b'{"a":"\\u00e9","b":1}'
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_packet_hash_ignores_the_seal_fields() -> None:
    document = {"cycle_id": "c-1", "packet_hash": "x", "decision_template": {"y": 1}}

    expected = hashlib.sha256(b'{"cycle_id":"c-1"}').hexdigest()
    assert packet_hash(document) == expected == packet_hash({"cycle_id": "c-1"})


def test_rebuttal_stances_match_the_protocol() -> None:
    assert set(op.ENUM_CHOICES["rebuttal"]) == REBUTTAL_STANCES


def test_allowed_values_carry_the_enum_and_limit_tables() -> None:
    allowed = of.packet_body().allowed

    assert allowed.enums["chief.action"] == ("ENTER", "HOLD")
    assert allowed.limits["max_decision_bytes"] == op.MAX_DECISION_BYTES == 64 * 1024
    assert allowed.agents == ("claude_code", "codex")


# --- packets -------------------------------------------------------------------------
def test_a_sealed_packet_carries_its_hash_and_a_hold_template() -> None:
    sealed = of.packet()
    body_hash = packet_hash(of.packet_body().model_dump(mode="json"))

    assert sealed.packet_hash == body_hash
    template = sealed.decision_template
    assert (template.cycle_id, template.packet_hash, template.agent) == (
        of.CYCLE_ID, body_hash, "claude_code")
    assert (template.schema_version, template.action, template.manage) == (
        "v6.operator.decision.3", "HOLD", None)
    assert template.views.price_action == sealed.baseline_views.price_action
    assert OperatorPacket.model_validate_json(sealed.model_dump_json()) == sealed


def test_the_template_is_itself_an_acceptable_decision() -> None:
    sealed = of.packet()

    decision = validate_decision(sealed, sealed.decision_template.model_dump_json().encode(),
                                 V6Settings(_env_file=None), now=of.CREATED)
    assert isinstance(decision, ValidatedDecision), decision
    assert (decision.action, decision.chief.action) == ("HOLD", "HOLD")
    assert decision.price_action == sealed.decision_template.views.price_action


def test_missing_baselines_fall_back_to_cautious_template_views() -> None:
    empty = {"price_action": None, "news_risk": None, "liquidity": None, "structure": None}
    template = of.packet(baseline_views=empty).decision_template

    assert template.views.price_action == op.ABSTAIN_VIEW
    assert template.views.news_risk.stance == "CAUTION"
    assert template.views.liquidity.order_style == "LIMIT"
    assert template.views.structure.size_multiplier == 0.5


def test_any_change_breaks_the_seal() -> None:
    document = json.loads(of.packet().model_dump_json())
    document["market"]["bid"] = 4535.19

    with pytest.raises(ValidationError, match="packet_hash does not match"):
        OperatorPacket.model_validate_json(json.dumps(document))


def test_the_template_must_answer_this_packet() -> None:
    document = json.loads(of.packet().model_dump_json())
    wrong = copy.deepcopy(document)
    wrong["decision_template"]["cycle_id"] = "c-other"
    stranger = copy.deepcopy(document)
    stranger["allowed"]["agents"] = ["codex"]
    stranger["packet_hash"] = packet_hash(stranger)

    with pytest.raises(ValidationError, match="answers another packet"):
        OperatorPacket.model_validate_json(json.dumps(wrong))
    with pytest.raises(ValidationError, match="agent is not allowed"):
        OperatorPacket.model_validate_json(json.dumps(stranger))


def _without_events(document: dict[str, Any]) -> dict[str, Any]:
    document["calendar"]["events"] = []
    return document


@pytest.mark.parametrize(("mutate", "message"), [
    (lambda d: d.update(bar_close_epoch=d["bar_close_epoch"] + 1),
     "the bar open plus the packet's bar"),
    (lambda d: d.update(created_at_epoch=d["bar_close_epoch"] - 1), "bar_close <= created_at"),
    (lambda d: d.update(expires_at_epoch=d["created_at_epoch"]), "created_at < expires_at"),
    (lambda d: d.update(candidates=d["candidates"][::-1]), "allowed.candidate_ids"),
    (lambda d: d.update(candidates=[d["candidates"][0]] * 2), "allowed.candidate_ids"),
    (_without_events, "allowed.event_ids"),
    (lambda d: d["account"].update(trade_mode="REAL"), "trade_mode"),
    (lambda d: d["account"].update(trade_mode="CONTEST"), "trade_mode"),
    (lambda d: d.update(mode="off"), "mode"),
    (lambda d: d.update(candidates=[]), "candidates"),
    (lambda d: d["market"].update(ask=4535.0), "ask below bid"),
    (lambda d: d["market"]["features"].update(lots=1.0), "FEATURE_KEYS"),
    (lambda d: d["candidates"][0]["exit"].update(sl=4540.0), "wrong side"),
    (lambda d: d["candidates"][0].update(sizing=None), "sizing"),
    (lambda d: d["candidates"][0].update(sizing_refusal=["MIN_LOT_WALL"]), "sizing"),
    (lambda d: d["bars"].update(M15=[[1, 1.0, 1.0, 1.0, 1.0]] * 33), "M15"),
    (lambda d: d["allowed"].update(agents=["gpt"]), "agents"),
    (lambda d: d.update(extra=True), "extra"),
])
def test_invalid_packet_bodies(mutate: Any, message: str) -> None:
    document = of.body()
    mutate(document)

    with pytest.raises(ValidationError, match=message):
        op.OperatorPacketBody.model_validate_json(canonical_json(document))


def test_a_refused_sizing_is_allowed_with_its_codes() -> None:
    changed = of.candidate()
    changed.update(sizing=None, sizing_refusal=["MIN_LOT_WALL"])
    sealed = of.packet(candidates=[changed, of.candidate(of.SELL_ID, "sell")])

    assert sealed.candidates[0].sizing is None


def test_untrusted_text_is_cleaned() -> None:
    gates = [{"code": "SESSION", "passed": False, "value": "X", "limit": None,
              "detail": "bad\x1b[2Jdetail"}]
    sealed = of.packet(gates=gates, account={"trade_mode": "DEMO", "server": "Demo\x00Srv",
                                            "equity_band": "lt_1k"})

    assert sealed.gates[0].detail == "bad[2Jdetail"
    assert sealed.account.server == "DemoSrv"
