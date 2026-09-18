"""deliberation/operator_packet.py: the operator packet built from tier 0."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import replace
from typing import Any

import pytest

from app.v6.config import OPERATOR_AGENTS, V6Settings
from app.v6.cycle_codes import FEATURE_KEYS, HoldReason
from app.v6.cycle_types import DeliberationInput, DeskViews
from app.v6.deliberation import operator_packet as opk
from app.v6.deliberation.candidates import assess_candidates
from app.v6.deliberation.context_builder import (
    ContextRequest, build_context, cycle_friction, load_bars,
)
from app.v6.deliberation.operator_packet import PacketRefusal, PacketRequest, build_packet
from app.v6.providers.offline import rules_desk_views
from app.v6.risk.gates import evaluate_gates
from app.v6.schemas.agents import NewsRiskView, PriceActionView
from app.v6.schemas.operator import (
    ABSTAIN_VIEW, OperatorPacket, packet_hash, parse_operator_decision,
)
from app.v6.schemas.snapshot import V6Snapshot
from app.v6.types import Candidate, GateResult

from . import engine_fixtures_v6 as ef
from .payloads_v6 import as_snapshot

TOKEN = "operator-" + "t" * 40
EA_KEY = "ea-key-" + "q" * 40
SESSION_ID = "a1b2c3d4e5f6"
LOGIN = "90909090"
WIDE = ef.candidate(invalidation=ef.PRICE - 12.0, variant="wide")


def operator_settings(**overrides: Any) -> V6Settings:
    return ef.settings(**({"backend": "operator", "operator_token": TOKEN} | overrides))


def snapshot(**account: Any) -> V6Snapshot:
    payload = ef.engine_snapshot_payload()
    payload["account"] = {**payload["account"], "login": LOGIN, **account}
    return as_snapshot(payload)


def tier0(*candidates: Candidate, settings: V6Settings | None = None,
          snap: V6Snapshot | None = None, **changes: Any) -> PacketRequest:
    config = settings or operator_settings()
    request = ef.request(snap or snapshot())
    bars = load_bars(ef.MemoryBars(ef.history()), request.snapshot)
    context = build_context(ContextRequest(cycle_id=request.cycle_id, snapshot=request.snapshot,
                                           received_at=request.received_at),
                            bars, config, ef.RECEIVED)
    breakers = ef.healthy_status(context, config)
    gates = evaluate_gates(context, context.calendar, request.runtime, config, breakers,
                           ef.RECEIVED)
    pool = assess_candidates(context, candidates or (ef.candidate(),), config,
                             friction_price=cycle_friction(config, context))
    views = rules_desk_views(DeliberationInput(context=context, gates=gates, offered=pool.offered),
                             max_spread_points=config.effective_max_spread_points)
    base = PacketRequest(context=context, gates=gates, offered=pool.assessments,
                         baseline=views, remaining_loss_usd=breakers.remaining_loss_usd,
                         session_id=SESSION_ID, armed=False, now=ef.RECEIVED)
    return replace(base, **changes)


def built(request: PacketRequest, settings: V6Settings | None = None) -> OperatorPacket:
    packet = build_packet(request, settings or operator_settings())
    assert isinstance(packet, OperatorPacket), packet
    return packet


def refusal(request: PacketRequest, code: str, settings: V6Settings | None = None
            ) -> PacketRefusal:
    result = build_packet(request, settings or operator_settings())
    assert isinstance(result, PacketRefusal), result
    assert result.code == code
    return result


# --- a packet ----------------------------------------------------------------------------------
def test_a_packet_carries_tier0_and_a_usable_template() -> None:
    request = tier0()
    packet = built(request)

    assert all(gate.passed for gate in packet.gates) and len(packet.gates) == 14
    assert (packet.cycle_id, packet.session_id, packet.mode) == (
        request.context.cycle_id, SESSION_ID, "shadow")
    assert (packet.bar_open_epoch, packet.bar_close_epoch) == (ef.T_BAR, ef.AS_OF)
    assert (packet.created_at_epoch, packet.expires_at_epoch) == (ef.AS_OF + 1, ef.AS_OF + 180)
    assert packet.account.model_dump() == {
        "trade_mode": "DEMO", "server": "Broker-Demo", "equity_band": "2k_5k"}
    assert (packet.market.bid, packet.market.ask, packet.market.spread_points) == (
        4300.0, 4300.2, 20)
    assert packet.market.atr_m5 == pytest.approx(8.0)
    assert set(packet.market.features) <= FEATURE_KEYS
    assert packet.session.armed is False and packet.session.entries_allowed is True
    assert (len(packet.bars.M15), len(packet.bars.H1)) == (32, 24)
    assert packet.session.quality in {"prime", "active", "thin"}
    assert packet.bars.M15[-1][0] == ef.T_BAR
    assert [event.code for event in packet.calendar.events] == ["cpi-yy"]
    (candidate,) = packet.candidates
    assert candidate.candidate_id == ef.CANDIDATE_ID and candidate.side == "buy"
    assert (candidate.entry, candidate.exit.sl, candidate.exit.tp) == (4300.0, 4292.0, 4316.0)
    assert candidate.sizing is not None and candidate.sizing.lots == 0.01
    assert candidate.sizing.risk_usd == pytest.approx(8.4) and candidate.sizing_refusal == ()
    assert packet.allowed.candidate_ids == (ef.CANDIDATE_ID, f"agent-{ef.T_BAR}")
    assert packet.allowed.agents == OPERATOR_AGENTS
    assert packet.baseline_views.price_action == request.baseline.price_action
    body = json.loads(packet.model_dump_json())
    assert packet.packet_hash == packet_hash(body)
    template = packet.decision_template.model_dump_json().encode()
    assert parse_operator_decision(template, packet, now=ef.RECEIVED).chief.action == "HOLD"


def test_the_account_is_shown_only_as_a_band() -> None:
    packet = built(tier0())
    body = packet.model_dump(mode="json", exclude={"packet_hash", "decision_template"})
    text = json.dumps(body)

    assert LOGIN not in text and "2010.5" not in text and "2000.0" not in text
    for key in ('"login"', '"balance"', '"equity"', '"free_margin"', '"margin"'):
        assert key not in text


def test_execute_mode_marks_the_armed_session() -> None:
    config = operator_settings(mode="execute", ea_hmac_key=EA_KEY)
    packet = built(tier0(settings=config, armed=True), config)

    assert (packet.mode, packet.session.armed) == ("execute", True)


# --- candidates and baselines --------------------------------------------------------------------
def test_only_sized_candidates_are_offered_and_baselines_follow() -> None:
    request = tier0(ef.candidate(), WIDE)
    ranked = {item.candidate_id for item in request.baseline.price_action.ranked}
    assert ranked == {ef.CANDIDATE_ID, WIDE.candidate_id}

    packet = built(request)
    assert packet.allowed.candidate_ids == (ef.CANDIDATE_ID, packet.limits.agent_entry_id)
    kept = packet.baseline_views.price_action
    assert kept is not None
    assert [item.candidate_id for item in kept.ranked] == [ef.CANDIDATE_ID]
    template = packet.decision_template.model_dump_json().encode()
    assert parse_operator_decision(template, packet, now=ef.RECEIVED).views.price_action == kept


def test_a_baseline_ranking_only_unoffered_ids_abstains() -> None:
    request = tier0()
    other = PriceActionView.model_validate(
        {"abstain": False, "ranked": [{"candidate_id": "orb-sell-1", "verdict": "TAKE",
                                       "conviction": 0.9, "reason_codes": [], "note": ""}]},
        strict=False)
    news = request.baseline.news_risk.model_copy(update={"event_ids": ("mt5:999",)})
    packet = built(replace(request, baseline=replace(request.baseline, price_action=other,
                                                       news_risk=news)))

    assert packet.baseline_views.price_action == ABSTAIN_VIEW
    assert packet.baseline_views.news_risk.event_ids == ()


def test_missing_baselines_and_features_are_null() -> None:
    request = tier0()
    context = replace(request.context, features={})
    packet = built(replace(request, context=context, baseline=DeskViews()))

    assert packet.baseline_views.model_dump() == dict.fromkeys(
        ("price_action", "news_risk", "liquidity", "structure"))
    assert (packet.market.atr_m5, packet.market.atr_m15, packet.market.atr_h1) == (
        None, None, None)
    assert packet.decision_template.views.price_action == ABSTAIN_VIEW
    assert isinstance(packet.decision_template.views.news_risk, NewsRiskView)


def test_at_most_three_candidates_are_offered() -> None:
    four = tuple(ef.candidate(variant=name) for name in "abcd")
    packet = built(tier0(*four))

    assert packet.allowed.candidate_ids == (
        *(c.candidate_id for c in four[:3]), packet.limits.agent_entry_id)


@pytest.mark.parametrize("offered", [(WIDE,), ()])
def test_without_a_sizable_suggestion_only_the_agent_entry_is_offered(
        offered: tuple[Candidate, ...], caplog: pytest.LogCaptureFixture) -> None:
    request = tier0(WIDE)
    if not offered:
        request = replace(request, offered=())

    with caplog.at_level(logging.INFO, logger=opk.__name__):
        packet = built(request)
    assert packet.candidates == ()
    assert packet.allowed.candidate_ids == (packet.limits.agent_entry_id,)
    assert packet.decision_template.chief.action == "HOLD"
    assert ("MIN_LOT_WALL" in caplog.text) == bool(offered)


def test_a_candidate_without_an_exit_plan_is_skipped() -> None:
    request = tier0()
    unplanned = replace(request.offered[0], exit_plan=None)

    packet = built(replace(request, offered=(unplanned,)))
    assert packet.candidates == ()


def test_the_packet_carries_levels_and_the_agent_entry_limits() -> None:
    packet = built(tier0())
    limits, levels = packet.limits, packet.levels

    assert limits.agent_entry_id == f"agent-{ef.T_BAR}" and limits.agent_entry_possible
    assert (limits.buy_limit_max, limits.sell_limit_min) == (4300.19, 4300.01)
    assert limits.stop_floor == 6.0 and limits.stop_floor <= limits.max_stop_distance
    assert (limits.min_reward_r, limits.max_reward_r, limits.default_reward_r) == (1.0, 5.0, 2.0)
    assert limits.pending_expiry_epoch == ef.AS_OF + 1800 and limits.time_barrier_s == 7200
    assert (levels.round_50_below, levels.round_50_above) == (4300.0, 4350.0)
    assert levels.prior_day_high is not None and len(packet.bars.D1) <= 5


# --- policy and timing -----------------------------------------------------------------------------
@pytest.mark.parametrize(("settings_changes", "account", "detail"), [
    ({"backend": "rules"}, {}, "V6_BACKEND=operator"),
    ({"mode": "off"}, {}, "V6_BACKEND=operator"),
    ({}, {"trade_mode": "CONTEST"}, "POLICY_OPERATOR_DEMO_ONLY"),
    ({}, {"trade_mode": "REAL"}, "POLICY_OPERATOR_DEMO_ONLY"),
    ({}, {"server": "MetaQuotes-Live"}, "POLICY_SERVER_NOT_DEMO"),
    ({"allowed_logins_csv": "1"}, {}, "POLICY_LOGIN_NOT_ALLOWED"),
])
def test_policy_refusals(settings_changes: dict[str, Any], account: dict[str, Any],
                         detail: str) -> None:
    config = operator_settings(**settings_changes)
    result = refusal(tier0(settings=operator_settings(), snap=snapshot(**account)),
                     opk.REFUSE_POLICY, config)

    assert result.detail.startswith(detail) or detail in result.detail
    assert result.hold_reason == HoldReason.GATE


def test_a_real_account_flag_that_bypassed_validation_is_refused() -> None:
    unsafe = operator_settings().model_copy(update={"allow_real_account": True})

    result = refusal(tier0(), opk.REFUSE_POLICY, unsafe)
    assert result.detail == "POLICY_REAL_ACCOUNT_FLAG"


def test_no_session_refuses() -> None:
    result = refusal(tier0(session_id=None), opk.REFUSE_NO_SESSION)
    assert result.hold_reason == HoldReason.NO_SESSION


@pytest.mark.parametrize(("changes", "window"), [
    ({"deadline_epoch": ef.AS_OF + 120.9}, (ef.AS_OF + 1, ef.AS_OF + 120)),
    ({"now": ef.AS_OF - 3.0}, (ef.AS_OF, ef.AS_OF + 180)),
])
def test_the_packet_window(changes: dict[str, Any], window: tuple[int, int]) -> None:
    packet = built(tier0(**changes))
    assert (packet.created_at_epoch, packet.expires_at_epoch) == window


@pytest.mark.parametrize("changes", [
    {"now": ef.AS_OF + 180.0}, {"deadline_epoch": ef.AS_OF + 1.5},
    {"now": math.nan}, {"deadline_epoch": math.inf},
])
def test_a_passed_deadline_refuses(changes: dict[str, Any]) -> None:
    result = refusal(tier0(**changes), opk.REFUSE_DEADLINE)
    assert result.hold_reason == HoldReason.LATE


# --- untrusted text and schema failures -----------------------------------------------------------
def test_untrusted_values_are_cleaned_and_bounded() -> None:
    noisy = ef.candidate()
    noisy = replace(noisy, reason_codes=("CONFIRMED_CLOSE", "lower-case", "CONFIRMED_CLOSE"),
                    features={"body_ratio": 0.8, "Bad-Key": 1.0, "nan_value": math.nan,
                              "flag": True, "huge": 10 ** 400})
    gates = (GateResult(code="SPREAD", passed=True, value=20, limit=35,
                        detail="x\x1b[2J" + "d" * 400),
             GateResult(code="SESSION", passed=True, value=True, limit=math.inf),
             GateResult(code="NEWS", passed=True, value="v‮" * 100, limit=None),
             GateResult(code="WARMUP", passed=True, value=("x",), limit=None))  # type: ignore[arg-type]
    request = tier0(noisy, gates=gates)
    context = replace(request.context, server="Broker\x1b-Demo")
    packet = built(replace(request, context=context))

    spread, session, news, warmup = packet.gates
    assert spread.value == 20.0 and len(spread.detail) == 300 and "\x1b" not in spread.detail
    assert (session.value, session.limit) == ("TRUE", None)
    assert news.value == "v" * 64
    assert warmup.value == "('x',)"
    assert packet.account.server == "Broker-Demo"
    (candidate,) = packet.candidates
    assert candidate.reason_codes == ("CONFIRMED_CLOSE",)
    assert candidate.features == {"body_ratio": 0.8}


def test_a_schema_failure_is_refused_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    gates = (GateResult(code="not a code", passed=True),)
    with caplog.at_level(logging.ERROR, logger=opk.__name__):
        result = refusal(tier0(gates=gates), opk.REFUSE_INVALID)

    assert result.hold_reason == HoldReason.ERROR
    assert "ValidationError" in result.detail and "failed its schema" in caplog.text


def test_packet_refusal_checks_its_code() -> None:
    with pytest.raises(ValueError, match="unknown packet refusal"):
        PacketRefusal("NOPE")
    assert len(PacketRefusal(opk.REFUSE_POLICY, "x" * 500).detail) == 300
