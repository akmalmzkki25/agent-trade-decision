"""ScriptedProvider: canned outcomes, all routed through validate_view."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any, get_args

import pytest
from pydantic import BaseModel

from app.v6.clock import FakeClock
from app.v6.cycle_types import rules_packet
from app.v6.providers import AgentProvider, ask_safely, base
from app.v6.providers.offline import OfflineProvider
from app.v6.providers.scripted import (
    CONTROL_SMUGGLE, INJECTION_TEXT, INJECTION_VARIANTS, SCRIPTED_MODEL, ScriptedOutcome,
    ScriptedProvider, ScriptedProviderError, baseline_payload, injection_payload,
)
from app.v6.schemas import agents
from app.v6.schemas.agents import AGENT_ROLES, SCHEMA_NAMES, validate_view

from .cycle_fixtures_v6 import (
    chief_payload, liquidity_payload, news_payload, pa_payload, structure_payload,
)
from .test_offline_provider import BUY_ID, inputs

PACKET = rules_packet(inputs())
OFFERED = frozenset({BUY_ID})
VALID: dict[str, dict[str, Any]] = {
    "price_action": pa_payload(BUY_ID), "news_risk": news_payload(),
    "liquidity": liquidity_payload(), "structure": structure_payload(),
    "chief": chief_payload(candidate_id=BUY_ID),
}
USAGE = {"model": "m", "latency_ms": 900, "tokens_in": 120, "tokens_out": 30, "cost_usd": 0.002}
ENUM_VALUES = frozenset(value for alias in (
    agents.CandidateVerdict, agents.NewsStance, agents.LiquidityStance,
    agents.OrderStylePreference, agents.ChiefAction, agents.RiskTier, agents.ExitProfile,
    agents.NewsRegime, agents.StructureRegime, agents.NamedPattern, agents.PriceActionReason,
    agents.NewsReason, agents.LiquidityReason, agents.StructureReason,
) for value in get_args(alias))
TEXT_FIELDS = frozenset({"note", "rationale", "dissent"})
INVALID, UNKNOWN = base.ERR_INVALID_OUTPUT, base.ERR_UNKNOWN_ID


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def ask(provider: ScriptedProvider, role: Any = "chief", packet: Any = PACKET,
              deadline: float = 1e12) -> base.ProviderResult:
    schema = SCHEMA_NAMES.get(role, "x") if isinstance(role, str) else "x"
    return await provider.ask(role, packet, schema, deadline)


def _strings(value: object, key: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for name, item in value.items():
            yield from _strings(item, name)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item, key)
    elif isinstance(value, str):
        yield key, value


def assert_inert(view: BaseModel) -> None:
    """Every string is an enum value, an offered id, or printable free text."""
    for key, text in _strings(view.model_dump(mode="json")):
        if key in TEXT_FIELDS:
            assert text.isprintable()
        elif key == "candidate_id":
            assert text in OFFERED
        else:
            assert text in ENUM_VALUES, (key, text)


def test_provider_satisfies_the_protocol() -> None:
    assert isinstance(ScriptedProvider(), AgentProvider)
    assert ScriptedProvider(name="openrouter-fake").name == "openrouter-fake"


@pytest.mark.anyio
@pytest.mark.parametrize("role", AGENT_ROLES)
async def test_valid_json_becomes_a_view_with_usage(role: str) -> None:
    provider = ScriptedProvider({role: [ScriptedOutcome.json_payload(VALID[role], **USAGE)]})
    result = await ask(provider, role)
    assert result.ok
    assert result.view == validate_view(role, VALID[role], OFFERED)
    assert (result.model, result.latency_ms, result.tokens_in, result.tokens_out,
            result.cost_usd) == tuple(USAGE.values())


@pytest.mark.anyio
@pytest.mark.parametrize(("outcome", "code"), [
    (ScriptedOutcome.malformed(), INVALID),
    (ScriptedOutcome.oversized(), base.ERR_OUTPUT_TOO_LARGE),
    (ScriptedOutcome.raw(""), base.ERR_EMPTY),
    (ScriptedOutcome.raw(b" "), base.ERR_EMPTY),
    (ScriptedOutcome.json_payload({**VALID["chief"], "confidence": float("nan")}), INVALID),
    (ScriptedOutcome.json_payload({**VALID["chief"], "lots": 10}), INVALID),
    (ScriptedOutcome.json_payload(chief_payload(candidate_id="x-1")), UNKNOWN),
    (ScriptedOutcome.error(base.ERR_RATE_LIMITED), base.ERR_RATE_LIMITED),
    (ScriptedOutcome.timeout(), base.ERR_TIMEOUT),
    (ScriptedOutcome.timeout(0.01), base.ERR_TIMEOUT),
])
async def test_canned_failures(outcome: ScriptedOutcome, code: str) -> None:
    result = await ask(ScriptedProvider({"chief": [outcome]}))
    assert (result.error_code, result.model, result.view) == (code, SCRIPTED_MODEL, None)


@pytest.mark.anyio
async def test_a_delayed_payload_still_validates() -> None:
    outcome = ScriptedOutcome(kind="payload", payload=json.dumps(VALID["chief"]), delay_s=0.01)
    assert (await ask(ScriptedProvider({"chief": [outcome]}))).ok


@pytest.mark.anyio
async def test_a_hanging_call_is_cut_by_ask_safely() -> None:
    clock = FakeClock()
    provider = ScriptedProvider({"chief": [ScriptedOutcome.timeout(30.0)]})
    result = await ask_safely(provider, "chief", PACKET, "ChiefDecision",
                              clock.now_epoch() + 0.05, clock)
    assert result.error_code == base.ERR_TIMEOUT


@pytest.mark.anyio
async def test_exception_outcome_raises_and_ask_safely_contains_it(
        caplog: pytest.LogCaptureFixture) -> None:
    secret = "sk-or-v1-scripted-secret"
    provider = ScriptedProvider({"chief": [ScriptedOutcome.exception(secret)] * 2})
    with pytest.raises(ScriptedProviderError):
        await ask(provider)
    clock = FakeClock()
    with caplog.at_level(logging.ERROR):
        result = await ask_safely(provider, "chief", PACKET, "ChiefDecision",
                                  clock.now_epoch() + 5.0, clock)
    assert result.error_code == base.ERR_INTERNAL
    assert "ScriptedProviderError" in caplog.text and secret not in caplog.text


@pytest.mark.anyio
async def test_rules_outcome_and_exhausted_fallback_use_the_offline_provider() -> None:
    rules = OfflineProvider(structure_veto="enforce")
    provider = ScriptedProvider({"chief": [ScriptedOutcome.rules()]}, rules=rules,
                                exhausted=ScriptedOutcome.rules())
    expected = await rules.ask("chief", PACKET, "ChiefDecision", 1e12)
    first, second = await ask(provider), await ask(provider)
    assert first.view == second.view == expected.view
    assert first.model == "rules-v1"


@pytest.mark.anyio
async def test_queues_drain_in_order_and_record_calls() -> None:
    provider = ScriptedProvider({"chief": [ScriptedOutcome.error(base.ERR_AUTH),
                                           ScriptedOutcome.json_payload(VALID["chief"])]})
    provider.enqueue("chief", ScriptedOutcome.timeout())
    assert provider.remaining("chief") == 3 and provider.remaining("trader") == 0
    codes = [(await ask(provider, deadline=77.0)).error_code for _ in range(4)]
    assert codes == [base.ERR_AUTH, base.ERR_NONE, base.ERR_TIMEOUT, base.ERR_EMPTY]
    assert provider.remaining("chief") == 0
    assert [(c.role, c.schema_name, c.deadline_epoch) for c in provider.calls] == [
        ("chief", "ChiefDecision", 77.0)] * 4


@pytest.mark.anyio
@pytest.mark.parametrize(("role", "packet", "code"), [
    ("trader", PACKET, base.ERR_UNSUPPORTED_ROLE), (42, PACKET, base.ERR_UNSUPPORTED_ROLE),
    (["chief"], PACKET, base.ERR_UNSUPPORTED_ROLE), ("chief", {}, base.ERR_BAD_REQUEST),
    ("chief", None, base.ERR_BAD_REQUEST),
])
async def test_bad_requests_fail_cleanly(role: Any, packet: Any, code: str) -> None:
    provider = ScriptedProvider({"chief": [ScriptedOutcome.json_payload(VALID["chief"])]})
    assert (await ask(provider, role, packet)).error_code == code


def test_bad_scripts_are_refused() -> None:
    with pytest.raises(ValueError):
        ScriptedProvider({"trader": [ScriptedOutcome.timeout()]})
    with pytest.raises(TypeError):
        ScriptedProvider().enqueue("chief", "not an outcome")  # type: ignore[arg-type]


@pytest.mark.parametrize("build", [
    lambda: ScriptedOutcome.error("NOT_A_CODE"),
    lambda: ScriptedOutcome(kind="injection", variant="sql"),  # type: ignore[arg-type]
    lambda: ScriptedOutcome.timeout(float("nan")),
    lambda: ScriptedOutcome.timeout(-1.0),
    lambda: ScriptedOutcome.raw("{}", tokens_in=-1),
    lambda: ScriptedOutcome.raw("{}", cost_usd=float("inf")),
])
def test_bad_outcomes_are_refused(build: Any) -> None:
    with pytest.raises(ValueError):
        build()


# --- prompt-injection canary ------------------------------------------------------

def expected_code(role: str, variant: str) -> str:
    if variant == "control_chars":
        return base.ERR_NONE
    if variant == "unknown_id" and role in ("price_action", "chief", "news_risk"):
        return UNKNOWN
    return INVALID


@pytest.mark.anyio
@pytest.mark.parametrize("role", AGENT_ROLES)
@pytest.mark.parametrize("variant", INJECTION_VARIANTS)
async def test_prompt_injection_cannot_escape_the_enum_space(
        role: str, variant: str, caplog: pytest.LogCaptureFixture) -> None:
    outcome = ScriptedOutcome.injection(variant)  # type: ignore[arg-type]
    provider = ScriptedProvider({role: [outcome]})
    with caplog.at_level(logging.DEBUG):
        result = await ask(provider, role)
    assert result.error_code == expected_code(role, variant)
    assert INJECTION_TEXT[:20] not in caplog.text
    if result.view is not None:
        assert_inert(result.view)
        cleaned = "".join(ch for ch in CONTROL_SMUGGLE if ch.isprintable())
        assert cleaned in {text for key, text in _strings(result.view.model_dump(mode="json"))
                           if key in TEXT_FIELDS}


def test_instruction_text_is_not_json() -> None:
    assert injection_payload("chief", "instruction_text", inputs()) == INJECTION_TEXT


@pytest.mark.parametrize("role", AGENT_ROLES)
@pytest.mark.parametrize("with_candidate", [True, False])
def test_baseline_and_control_char_payloads_validate(role: str, with_candidate: bool) -> None:
    inp = inputs() if with_candidate else inputs(items=())
    for payload in (baseline_payload(role, inp),  # type: ignore[arg-type]
                    injection_payload(role, "control_chars", inp)):  # type: ignore[arg-type]
        view = validate_view(role, payload, inp.offered_candidate_ids)
        assert_inert(view)
