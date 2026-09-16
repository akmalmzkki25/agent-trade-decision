"""OfflineProvider (the `rules` backend) and the rules Chief."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from app.v6.clock import FakeClock
from app.v6.cycle_types import (
    CAL_US_DATA_BAR, CalendarAssessment, CandidateAssessment, DeliberationInput, DeskViews,
    rules_packet,
)
from app.v6.desks import price_action_view
from app.v6.providers import AgentProvider, ask_safely, base, offline
from app.v6.providers.offline import (
    RULES_MODEL, OfflineProvider, rules_chief_decision, rules_desk_views, validated_result,
    view_error_code,
)
from app.v6.schemas.agents import (
    AGENT_ROLES, SCHEMA_NAMES, VIEW_ERR_NOT_JSON, VIEW_ERR_SCHEMA, VIEW_ERR_SEMANTIC,
    VIEW_ERR_TOO_LARGE, VIEW_ERR_UNKNOWN_CANDIDATE, VIEW_ERR_UNKNOWN_EVENT, NewsRiskView,
    PriceActionView, RankedCandidate, ViewValidationError,
)

from .cycle_fixtures_v6 import chief_payload, news_payload
from .test_desks import CALM, UP, cal, context, offer

GOOD = {**UP, **CALM, "rv_ratio": 1.0, "er_m15": 0.5, "vr_m5": 1.2}
BUY = offer(variant="b")
SELL = offer(side="sell", variant="s")
BUY_ID, SELL_ID = BUY.candidate.candidate_id, SELL.candidate.candidate_id


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def pa_view(*ranked: tuple[str, str, float]) -> PriceActionView:
    items = tuple(
        RankedCandidate.model_validate({"candidate_id": cid, "verdict": verdict,
                                        "conviction": conviction, "reason_codes": (), "note": ""})
        for cid, verdict, conviction in ranked)
    return PriceActionView(abstain=not items, ranked=items)


def inputs(features: dict[str, float] = GOOD, items: tuple[CandidateAssessment, ...] = (BUY,),
           *, views: DeskViews | None = None,
           calendar: CalendarAssessment | None = None) -> DeliberationInput:
    ctx = context(features, calendar=calendar)
    given = DeskViews(price_action=price_action_view(ctx, items)) if views is None else views
    return DeliberationInput(context=ctx, gates=(), offered=items, views=given)


class StepClock:
    """Advances 0.25 s per read; raises from read `fail_from` on."""

    def __init__(self, fail_from: int | None = None) -> None:
        self.reads = 0
        self.fail_from = fail_from

    def now_epoch(self) -> float:
        self.reads += 1
        if self.fail_from is not None and self.reads >= self.fail_from:
            raise RuntimeError("clock broke")
        return 1_789_560_901.0 + 0.25 * self.reads


async def ask(provider: OfflineProvider, role: Any, packet: Any, schema: str | None = None,
              deadline: float = 1e12) -> base.ProviderResult:
    name = schema if schema is not None else SCHEMA_NAMES[role]
    return await provider.ask(role, packet, name, deadline)


# --- rules chief ------------------------------------------------------------------

def test_chief_enters_the_best_take_when_every_desk_is_calm() -> None:
    decision = rules_chief_decision(inputs())
    assert (decision.action, decision.candidate_id, decision.risk_tier) == (
        "ENTER", BUY_ID, "standard")
    assert (decision.order_style, decision.confidence, decision.dissent) == ("LIMIT", 0.8, "")


@pytest.mark.parametrize(("overrides", "dissent"), [
    ({"rv_ratio": 2.5}, "news CAUTION"),
    ({"friction_atr_m5": 0.065}, "liquidity CAUTION"),
    ({"er_m15": 0.3, "vr_m5": 1.0}, "structure TRANSITION"),
])
def test_chief_reduces_risk_on_any_caution(overrides: dict[str, float], dissent: str) -> None:
    decision = rules_chief_decision(inputs({**GOOD, **overrides}))
    assert (decision.action, decision.risk_tier, decision.dissent) == ("ENTER", "reduced", dissent)


@pytest.mark.parametrize(("calendar", "overrides", "label"), [
    (cal(blackout=True), {}, "calendar veto"),
    (cal(stale=True), {}, "calendar veto"),
    (cal(codes=(CAL_US_DATA_BAR,)), {}, "calendar veto"),
    (None, {"spread_points": 40.0}, "liquidity NO_TRADE"),
])
def test_chief_holds_on_a_hard_veto(calendar: CalendarAssessment | None,
                                    overrides: dict[str, float], label: str) -> None:
    decision = rules_chief_decision(inputs({**GOOD, **overrides}, calendar=calendar))
    assert (decision.action, decision.candidate_id, decision.confidence) == ("HOLD", None, 1.0)
    assert decision.rationale.startswith("veto: ") and label in decision.rationale


def test_chief_holds_on_an_llm_news_block() -> None:
    block = NewsRiskView.model_validate(
        {**news_payload(), "stance": "BLOCK", "size_multiplier": 0.0}, strict=False)
    views = DeskViews(price_action=pa_view((BUY_ID, "TAKE", 0.9)), news_risk=block)
    decision = rules_chief_decision(inputs(views=views))
    assert (decision.action, decision.rationale) == ("HOLD", "veto: news BLOCK")


@pytest.mark.parametrize(("items", "ranked", "mode", "action", "pick"), [
    ((SELL,), ((SELL_ID, "TAKE", 0.9),), "log", "ENTER", SELL_ID),
    ((SELL,), ((SELL_ID, "TAKE", 0.9),), "enforce", "HOLD", None),
    ((BUY, SELL), ((SELL_ID, "TAKE", 0.9), (BUY_ID, "TAKE", 0.7)), "log", "ENTER", SELL_ID),
    ((BUY, SELL), ((SELL_ID, "TAKE", 0.9), (BUY_ID, "TAKE", 0.7)), "enforce", "ENTER", BUY_ID),
])
def test_chief_structure_veto_modes(items: tuple[CandidateAssessment, ...],
                                    ranked: tuple[tuple[str, str, float], ...], mode: str,
                                    action: str, pick: str | None) -> None:
    views = DeskViews(price_action=pa_view(*ranked))
    decision = rules_chief_decision(inputs(items=items, views=views),
                                    structure_veto=mode)  # type: ignore[arg-type]
    assert (decision.action, decision.candidate_id) == (action, pick)
    assert (decision.rationale == "veto: counter-structure") is (action == "HOLD")


@pytest.mark.parametrize(("ranked", "minimum", "action", "pick", "confidence"), [
    ((), 0.6, "HOLD", None, 1.0),
    (((BUY_ID, "SKIP", 0.9),), 0.6, "HOLD", None, 1.0),
    (((BUY_ID, "TAKE", 0.8),), 0.9, "HOLD", None, 0.2),
    (((BUY_ID, "TAKE", 0.7), (SELL_ID, "TAKE", 0.9)), 0.6, "ENTER", SELL_ID, 0.9),
    (((SELL_ID, "TAKE", 0.8), (BUY_ID, "TAKE", 0.8)), 0.6, "ENTER", SELL_ID, 0.8),
    ((("never-offered", "TAKE", 0.95),), 0.6, "HOLD", None, 1.0),
])
def test_chief_picks_the_highest_take_at_or_above_the_minimum(
        ranked: tuple[tuple[str, str, float], ...], minimum: float, action: str,
        pick: str | None, confidence: float) -> None:
    views = DeskViews(price_action=pa_view(*ranked))
    decision = rules_chief_decision(inputs({**GOOD, "structure_m15": 0.0}, (BUY, SELL),
                                           views=views), pa_min_conviction=minimum)
    assert (decision.action, decision.candidate_id, decision.confidence) == (
        action, pick, confidence)


def test_chief_holds_without_a_price_action_view() -> None:
    decision = rules_chief_decision(inputs(views=DeskViews()))
    assert (decision.action, decision.risk_tier) == ("HOLD", "reduced")
    assert decision.dissent == "price action view missing"


@pytest.mark.parametrize("kwargs", [
    {"pa_min_conviction": 1.5}, {"pa_min_conviction": -0.1}, {"pa_min_conviction": True},
    {"pa_min_conviction": "0.6"}, {"structure_veto": "strict"}, {"max_spread_points": 0},
])
def test_bad_chief_settings_are_refused(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        OfflineProvider(**kwargs)
    if "max_spread_points" not in kwargs:
        with pytest.raises(ValueError):
            rules_chief_decision(inputs(), **kwargs)


def test_rules_desk_views_fill_every_desk() -> None:
    views = rules_desk_views(inputs(views=DeskViews()))
    assert None not in (views.price_action, views.news_risk, views.liquidity, views.structure)
    assert OfflineProvider().desk_views(inputs(views=DeskViews())) == views


# --- provider --------------------------------------------------------------------

def test_provider_satisfies_the_protocol() -> None:
    provider = OfflineProvider()
    assert isinstance(provider, AgentProvider) and provider.name == base.RULES_PROVIDER_NAME
    with pytest.raises(ValueError):
        provider.view_for("trader", inputs())  # type: ignore[arg-type]


@pytest.mark.anyio
@pytest.mark.parametrize("role", AGENT_ROLES)
async def test_provider_answers_every_role(role: str) -> None:
    provider = OfflineProvider(clock=FakeClock())
    inp = inputs()
    result = await ask(provider, role, rules_packet(inp))
    assert result.ok and result.model == RULES_MODEL and result.cost_usd == 0.0
    assert result.view == provider.view_for(role, inp)  # type: ignore[arg-type]


@pytest.mark.anyio
@pytest.mark.parametrize(("role", "packet", "schema", "deadline", "code"), [
    ("trader", "ok", "PriceActionView", 1e12, base.ERR_UNSUPPORTED_ROLE),
    (42, "ok", "PriceActionView", 1e12, base.ERR_UNSUPPORTED_ROLE),
    (["chief"], "ok", "ChiefDecision", 1e12, base.ERR_UNSUPPORTED_ROLE),
    ("chief", "ok", "PriceActionView", 1e12, base.ERR_BAD_REQUEST),
    ("chief", {}, None, 1e12, base.ERR_BAD_REQUEST),
    ("chief", None, None, 1e12, base.ERR_BAD_REQUEST),
    ("chief", "ok", None, 0.0, base.ERR_DEADLINE_PASSED),
    ("chief", "ok", None, float("nan"), base.ERR_DEADLINE_PASSED),
])
async def test_provider_refuses_bad_requests(role: Any, packet: Any, schema: str | None,
                                             deadline: float, code: str) -> None:
    real_packet = rules_packet(inputs()) if packet == "ok" else packet
    result = await ask(OfflineProvider(clock=FakeClock()), role, real_packet, schema, deadline)
    assert result.error_code == code and result.model == RULES_MODEL


@pytest.mark.anyio
async def test_provider_contains_internal_errors_without_leaking_messages(
        monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def boom(*_: object, **__: object) -> None:
        raise RuntimeError("sk-or-v1-must-not-leak")

    monkeypatch.setattr(offline, "price_action_view", boom)
    with caplog.at_level(logging.ERROR):
        result = await ask(OfflineProvider(clock=StepClock()), "price_action",
                           rules_packet(inputs(views=DeskViews())))
    assert result.error_code == base.ERR_INTERNAL and result.latency_ms == 250
    assert "RuntimeError" in caplog.text and "sk-or-" not in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize(("fail_from", "code", "latency"), [
    (1, base.ERR_INTERNAL, 0), (2, base.ERR_NONE, 0), (None, base.ERR_NONE, 250)])
async def test_provider_survives_a_broken_clock(fail_from: int | None, code: str,
                                                latency: int) -> None:
    provider = OfflineProvider(clock=StepClock(fail_from))
    result = await ask(provider, "news_risk", rules_packet(inputs()))
    assert (result.error_code, result.latency_ms) == (code, latency)


@pytest.mark.anyio
async def test_provider_output_is_validated_like_llm_output(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(OfflineProvider, "view_for",
                        lambda self, role, inp: pa_view(("never-offered", "TAKE", 0.9)))
    result = await ask(OfflineProvider(), "price_action", rules_packet(inputs()))
    assert result.error_code == base.ERR_UNKNOWN_ID


@pytest.mark.anyio
async def test_provider_works_through_ask_safely() -> None:
    clock = FakeClock()
    result = await ask_safely(OfflineProvider(clock=clock), "chief", rules_packet(inputs()),
                              "ChiefDecision", clock.now_epoch() + 5.0, clock)
    assert result.ok and result.view.action == "ENTER"  # type: ignore[union-attr]


# --- validated_result ------------------------------------------------------------

@pytest.mark.parametrize(("role", "payload", "code"), [
    ("chief", "", base.ERR_EMPTY), ("chief", b"  \n", base.ERR_EMPTY),
    ("chief", {}, base.ERR_EMPTY), ("chief", "x" * 9000, base.ERR_OUTPUT_TOO_LARGE),
    ("chief", "not json", base.ERR_INVALID_OUTPUT),
    ("chief", chief_payload("HOLD"), base.ERR_INVALID_OUTPUT),
    ("chief", chief_payload(candidate_id="never-offered"), base.ERR_UNKNOWN_ID),
    ("news_risk", news_payload(("mt5:999",)), base.ERR_UNKNOWN_ID),
])
def test_validated_result_maps_refusals(role: str, payload: Any, code: str,
                                        caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        result = validated_result(role, payload, inputs(),  # type: ignore[arg-type]
                                  model="m", latency_ms=7, tokens_in=3, tokens_out=2,
                                  cost_usd=0.01)
    assert result.error_code == code
    assert (result.model, result.latency_ms, result.tokens_in, result.cost_usd) == (
        "m", 7, 3, 0.01)
    assert "never-offered" not in caplog.text


def test_validated_result_accepts_a_good_view() -> None:
    result = validated_result("chief", chief_payload(candidate_id=BUY_ID), inputs(), model="m",
                              tokens_out=5)
    assert result.ok and result.tokens_out == 5
    assert result.view.candidate_id == BUY_ID  # type: ignore[union-attr]


@pytest.mark.parametrize(("view_code", "code"), [
    (VIEW_ERR_TOO_LARGE, base.ERR_OUTPUT_TOO_LARGE),
    (VIEW_ERR_UNKNOWN_CANDIDATE, base.ERR_UNKNOWN_ID),
    (VIEW_ERR_UNKNOWN_EVENT, base.ERR_UNKNOWN_ID), (VIEW_ERR_NOT_JSON, base.ERR_INVALID_OUTPUT),
    (VIEW_ERR_SCHEMA, base.ERR_INVALID_OUTPUT), (VIEW_ERR_SEMANTIC, base.ERR_INVALID_OUTPUT),
])
def test_view_error_code(view_code: str, code: str) -> None:
    assert view_error_code(ViewValidationError(view_code, "detail")) == code
