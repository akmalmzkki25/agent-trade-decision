"""The engine with an injected panel: fallbacks, failures and vetoes."""

from __future__ import annotations

from typing import Any

import pytest

from app.v6.cycle_codes import HoldReason
from app.v6.deliberation.engine import DeliberationEngine, EngineDeps, rules_provider_for
from app.v6.providers.base import (
    ERR_TIMEOUT, PROVIDER_STATUS_FAILED, PROVIDER_STATUS_OK, PROVIDER_STATUS_PARTIAL,
)
from app.v6.providers.scripted import ScriptedOutcome, ScriptedProvider

from . import engine_fixtures_v6 as ef
from .cycle_fixtures_v6 import (
    chief_payload, liquidity_payload, news_payload, pa_payload, structure_payload,
)

PAYLOADS: dict[str, dict[str, Any]] = {
    "price_action": pa_payload(ef.CANDIDATE_ID, conviction=0.8),
    "news_risk": news_payload(),
    "liquidity": liquidity_payload(),
    "structure": structure_payload(),
    "chief": chief_payload("ENTER", ef.CANDIDATE_ID),
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def scripted(name: str = "operator", **overrides: ScriptedOutcome) -> ScriptedProvider:
    script = {role: [overrides.get(role, ScriptedOutcome.json_payload(payload))]
              for role, payload in PAYLOADS.items()}
    return ScriptedProvider(script, name=name)


def make_engine(panel: ScriptedProvider, *candidates: Any, **overrides: Any) -> DeliberationEngine:
    config = ef.settings(**overrides)
    clock = ef.clock_at()
    return DeliberationEngine(EngineDeps(
        settings=config, clock=clock, bars=ef.MemoryBars(ef.history()),
        breakers=ef.healthy_breakers(config), rules=rules_provider_for(config, clock),
        panel=panel, detector=ef.detector_of(*(candidates or (ef.candidate(),)))))


def json_of(role: str, **changes: Any) -> ScriptedOutcome:
    return ScriptedOutcome.json_payload({**PAYLOADS[role], **changes})


@pytest.mark.anyio
async def test_a_healthy_panel_decides_and_is_recorded_beside_the_baseline() -> None:
    panel = scripted()
    result = (await make_engine(panel).run(ef.request())).result
    assert result.status == "ENTER_SHADOW"
    assert (result.provider, result.provider_status) == ("operator", PROVIDER_STATUS_OK)
    assert result.shadow_intent.source == "operator"
    sources = [(r.role, r.source) for r in result.view_records]
    assert sources[:4] == [(role, "rules") for role in
                           ("price_action", "news_risk", "liquidity", "structure")]
    assert sources[4:] == [(role, "operator") for role in
                           ("price_action", "news_risk", "liquidity", "structure", "chief")]
    assert result.views.structure.regime == "TREND_UP", "the panel's view is the effective one"
    desk_calls = [call for call in panel.calls if call.role != "chief"]
    assert len(desk_calls) == 4 and all(call.deadline_epoch == ef.AS_OF + 120
                                        for call in panel.calls)


@pytest.mark.anyio
async def test_a_failed_risk_desk_falls_back_to_the_rules_view() -> None:
    panel = scripted(news_risk=ScriptedOutcome.error(ERR_TIMEOUT))
    result = (await make_engine(panel).run(ef.request())).result
    assert result.status == "ENTER_SHADOW"
    assert result.provider_status == PROVIDER_STATUS_PARTIAL
    assert result.views.news_risk.note.startswith("CLEAR"), "rules news view used"
    failed = [r for r in result.view_records if r.error_code]
    assert [(r.role, r.error_code) for r in failed] == [("news_risk", ERR_TIMEOUT)]


@pytest.mark.anyio
async def test_a_malformed_price_action_view_holds() -> None:
    panel = scripted(price_action=ScriptedOutcome.malformed())
    result = (await make_engine(panel).run(ef.request())).result
    assert (result.status, result.hold_reason) == ("HOLD", HoldReason.INVALID_VIEW)
    assert result.provider_status == PROVIDER_STATUS_FAILED
    assert result.views.price_action is None
    assert {item.verdict for item in result.candidates} == {"chosen"}


@pytest.mark.anyio
async def test_a_wrongly_typed_view_is_not_used() -> None:
    panel = scripted(chief=ScriptedOutcome.json_payload(PAYLOADS["price_action"]))
    result = (await make_engine(panel).run(ef.request())).result
    assert result.hold_reason == HoldReason.INVALID_VIEW
    assert result.decision is None


@pytest.mark.anyio
async def test_a_provider_exception_is_contained() -> None:
    panel = scripted(chief=ScriptedOutcome.exception())
    result = (await make_engine(panel).run(ef.request())).result
    assert result.hold_reason == HoldReason.INVALID_VIEW
    assert result.view_records[-1].error_code == "PROVIDER_INTERNAL"


@pytest.mark.anyio
async def test_an_injection_cannot_leave_the_enum_space() -> None:
    panel = scripted(chief=ScriptedOutcome.injection("unknown_id"),
                     price_action=ScriptedOutcome.injection("extra_field"))
    result = (await make_engine(panel).run(ef.request())).result
    assert result.status == "HOLD" and result.shadow_intent is None
    assert result.hold_reason == HoldReason.INVALID_VIEW


@pytest.mark.anyio
async def test_the_chief_may_hold() -> None:
    panel = scripted(chief=json_of("chief", action="HOLD", candidate_id=None))
    result = (await make_engine(panel).run(ef.request())).result
    assert result.hold_reason == HoldReason.CHIEF_HOLD
    assert {item.verdict for item in result.candidates} == {"take"}


@pytest.mark.anyio
async def test_a_news_block_is_a_veto() -> None:
    panel = scripted(news_risk=json_of("news_risk", stance="BLOCK", size_multiplier=0.0))
    result = (await make_engine(panel).run(ef.request())).result
    assert result.hold_reason == HoldReason.VETO
    assert result.protocol.vetoes == ("VETO_NEWS_BLOCK",)
    assert result.protocol.candidate_id == ef.CANDIDATE_ID


@pytest.mark.anyio
async def test_a_skip_is_not_taken() -> None:
    ranked = [{**PAYLOADS["price_action"]["ranked"][0], "verdict": "SKIP"}]
    panel = scripted(price_action=json_of("price_action", ranked=ranked))
    result = (await make_engine(panel).run(ef.request())).result
    assert result.hold_reason == HoldReason.NO_TAKE
    assert {item.verdict for item in result.candidates} == {"chosen"}


@pytest.mark.anyio
async def test_the_reduced_tier_halves_the_budget() -> None:
    panel = scripted(chief=json_of("chief", risk_tier="reduced", order_style="MARKET"))
    result = (await make_engine(panel).run(ef.request())).result
    # Half the budget cannot fund the minimum lot here, the full one can: the sizer
    # floors at the minimum lot instead of refusing (MIN_LOT_FLOOR).
    assert result.hold_reason is None
    assert result.shadow_intent is not None
    assert "MIN_LOT_FLOOR" in result.shadow_intent.labels
    assert result.protocol.size_multiplier == 0.5
    assert result.protocol.order_style == "LIMIT", "liquidity LIMIT overrides MARKET"


@pytest.mark.anyio
async def test_market_order_style_maps_to_a_market_intent() -> None:
    panel = scripted(chief=json_of("chief", order_style="MARKET"),
                     liquidity=json_of("liquidity", order_style="EITHER"))
    result = (await make_engine(panel).run(ef.request())).result
    assert result.shadow_intent.order_type == "BUY"


@pytest.mark.anyio
async def test_a_sell_candidate_uses_the_sell_margin_and_order_type() -> None:
    sell = ef.candidate(side="sell", invalidation=ef.PRICE + 7.0)
    payloads = {"price_action": json_of("price_action", ranked=[
        {**PAYLOADS["price_action"]["ranked"][0], "candidate_id": sell.candidate_id}]),
        "chief": json_of("chief", candidate_id=sell.candidate_id)}
    result = (await make_engine(scripted(**payloads), sell).run(ef.request())).result
    intent = result.shadow_intent
    assert (intent.side, intent.order_type) == ("sell", "SELL_LIMIT")
    assert intent.sl > intent.entry > intent.tp


@pytest.mark.anyio
async def test_an_unknown_intent_source_is_refused_by_the_demo_policy() -> None:
    result = (await make_engine(scripted(name="scripted")).run(ef.request())).result
    assert (result.status, result.hold_reason) == ("HOLD", HoldReason.GATE)
    assert result.hold_detail == "intent source refused: POLICY_UNKNOWN_SOURCE"
    assert result.exit_plan is not None and result.sizing is None


@pytest.mark.anyio
async def test_the_structure_veto_is_enforced_only_in_enforce_mode() -> None:
    veto = json_of("structure", regime="TREND_DOWN", counter_structure_veto=True,
                   reason_codes=["LH_LL_SEQUENCE"])
    logged = (await make_engine(scripted(structure=veto)).run(ef.request())).result
    assert logged.status == "ENTER_SHADOW"
    assert logged.protocol.vetoes == ("VETO_COUNTER_STRUCTURE_LOGGED",)
    enforced_engine = make_engine(scripted(structure=json_of(
        "structure", regime="TREND_DOWN", counter_structure_veto=True)),
        structure_veto="enforce")
    enforced = (await enforced_engine.run(ef.request())).result
    assert enforced.hold_reason == HoldReason.VETO
