"""AgentProvider contract: results are well-formed and `ask_safely` never raises."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from typing import Any

import anyio
import pytest

from app.v6.clock import FakeClock
from app.v6.providers import AgentProvider, ProviderResult, ask_safely
from app.v6.providers import base
from app.v6.schemas.agents import AgentRole

from .cycle_fixtures_v6 import chief_decision

DEADLINE_S = 5.0


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Provider:
    name = "scripted"

    def __init__(self, behaviour: str) -> None:
        self.behaviour = behaviour
        self.calls = 0

    async def ask(self, role: AgentRole, packet: Mapping[str, object], schema_name: str,
                  deadline_epoch: float) -> Any:
        self.calls += 1
        if self.behaviour == "ok":
            return ProviderResult.success(chief_decision(), model="m", latency_ms=12,
                                          tokens_in=100, tokens_out=20, cost_usd=0.0004)
        if self.behaviour == "raise":
            raise RuntimeError("canary-secret must not be logged")
        if self.behaviour == "slow":
            await anyio.sleep(10)
        return {"not": "a result"}


def test_success_and_failure_constructors() -> None:
    ok = ProviderResult.success(chief_decision(), model="m", latency_ms=3)
    failed = ProviderResult.failure(base.ERR_TIMEOUT, latency_ms=20_000)

    assert ok.ok and ok.error_code == base.ERR_NONE
    assert not failed.ok and failed.view is None


def test_result_is_frozen() -> None:
    result = ProviderResult.failure(base.ERR_EMPTY)

    with pytest.raises(FrozenInstanceError):
        result.error_code = ""  # type: ignore[misc]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"view": None},
        {"view": None, "error_code": "SOMETHING_ELSE"},
        {"view": "VIEW", "error_code": base.ERR_TIMEOUT},
        {"view": None, "error_code": base.ERR_TIMEOUT, "latency_ms": -1},
        {"view": None, "error_code": base.ERR_TIMEOUT, "tokens_in": -1},
        {"view": None, "error_code": base.ERR_TIMEOUT, "cost_usd": float("nan")},
        {"view": None, "error_code": base.ERR_TIMEOUT, "cost_usd": -0.01},
    ],
)
def test_malformed_results_are_refused(kwargs: dict[str, Any]) -> None:
    if kwargs.get("view") == "VIEW":
        kwargs = {**kwargs, "view": chief_decision()}
    with pytest.raises(ValueError):
        ProviderResult(**kwargs)


def test_status_and_error_sets_are_consistent() -> None:
    assert base.ERR_NONE not in base.ERROR_CODES
    assert base.PROVIDER_STATUSES == {"ok", "partial", "failed", "skipped"}
    assert (base.RULES_PROVIDER_NAME, base.OPERATOR_PROVIDER_NAME) == ("rules", "operator")
    assert base.ERR_UNAVAILABLE in base.ERROR_CODES


def test_scripted_provider_satisfies_the_protocol() -> None:
    assert isinstance(_Provider("ok"), AgentProvider)


@pytest.mark.anyio
async def test_ask_safely_passes_a_good_result_through() -> None:
    clock = FakeClock()
    provider = _Provider("ok")

    result = await ask_safely(provider, "chief", {}, "ChiefDecision",
                              clock.now_epoch() + DEADLINE_S, clock)

    assert result.ok and result.tokens_in == 100


@pytest.mark.anyio
async def test_ask_safely_contains_exceptions_without_logging_the_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock()
    with caplog.at_level(logging.ERROR):
        result = await ask_safely(_Provider("raise"), "chief", {}, "ChiefDecision",
                                  clock.now_epoch() + DEADLINE_S, clock)

    assert result.error_code == base.ERR_INTERNAL
    assert "RuntimeError" in caplog.text
    assert "canary-secret" not in caplog.text


@pytest.mark.anyio
async def test_ask_safely_rejects_a_non_result() -> None:
    clock = FakeClock()

    result = await ask_safely(_Provider("junk"), "chief", {}, "ChiefDecision",
                              clock.now_epoch() + DEADLINE_S, clock)

    assert result.error_code == base.ERR_INTERNAL


@pytest.mark.anyio
async def test_ask_safely_enforces_the_deadline() -> None:
    clock = FakeClock()

    result = await ask_safely(_Provider("slow"), "chief", {}, "ChiefDecision",
                              clock.now_epoch() + 0.05, clock)

    assert result.error_code == base.ERR_TIMEOUT


@pytest.mark.anyio
@pytest.mark.parametrize("offset", [0.0, -1.0, float("nan")])
async def test_ask_safely_skips_a_passed_deadline(offset: float) -> None:
    clock = FakeClock()
    provider = _Provider("ok")

    result = await ask_safely(provider, "chief", {}, "ChiefDecision",
                              clock.now_epoch() + offset, clock)

    assert result.error_code == base.ERR_DEADLINE_PASSED
    assert provider.calls == 0
