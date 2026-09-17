"""The engine on the operator backend: packets, decisions, timeouts and publishing."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import SecretStr

from app.v6.clock import FakeClock
from app.v6.cycle_codes import HoldReason
from app.v6.cycle_types import CycleResult
from app.v6.deliberation.engine import DeliberationEngine, EngineDeps, rules_provider_for
from app.v6.deliberation.publication import PublishOutcome, PublishRequest
from app.v6.providers.operator_queue import OperatorQueue

from . import engine_fixtures_v6 as ef
from .execute_fixtures_v6 import enter_decision

OPERATOR: dict[str, Any] = {
    "backend": "operator", "mode": "execute", "operator_token": SecretStr("operator-" + "o" * 40),
    "ea_hmac_key": SecretStr("ea-engine-key-" + "k" * 40)}
INTENT_ID = "k7w2m4pq3xza"
WAIT_STEPS = 500
WAIT_STEP_S = 0.01


@dataclass
class FakePublisher:
    outcome: PublishOutcome
    requests: list[PublishRequest] = field(default_factory=list)

    async def publish(self, request: PublishRequest) -> PublishOutcome:
        self.requests.append(request)
        return self.outcome


@dataclass(frozen=True)
class Rig:
    engine: DeliberationEngine
    queue: OperatorQueue
    clock: FakeClock


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def rig(*candidates: Any, publisher: FakePublisher | None = None,
        clock: FakeClock | None = None, **overrides: Any) -> Rig:
    config = ef.settings(**(OPERATOR | overrides))
    fake = clock or ef.clock_at()
    queue = OperatorQueue(settings=config, clock=fake)
    engine = DeliberationEngine(EngineDeps(
        settings=config, clock=fake, bars=ef.MemoryBars(ef.history()),
        breakers=ef.healthy_breakers(config), rules=rules_provider_for(config, fake),
        panel=None, detector=ef.detector_of(*candidates), operator=queue,
        publisher=publisher))
    return Rig(engine=engine, queue=queue, clock=fake)


def withdraw_all(decision: dict[str, Any]) -> dict[str, Any]:
    candidate_id = decision["chief"]["candidate_id"]
    return decision | {"rebuttal": {candidate_id: "withdraw"}}


async def answer(setup: Rig, decide: Callable[[dict[str, Any]], dict[str, Any]] | None,
                 ) -> None:
    for _ in range(WAIT_STEPS):
        if setup.queue.pending is not None:
            break
        await asyncio.sleep(WAIT_STEP_S)
    if decide is None:
        setup.queue.withdraw(setup.clock.now_epoch())
        return
    packet = setup.queue.pending.packet.model_dump(mode="json")
    result = setup.queue.submit(json.dumps(decide(enter_decision(packet))).encode("utf-8"),
                                setup.clock.now_epoch())
    assert result.accepted, result


async def run(setup: Rig, decide: Callable[[dict[str, Any]], dict[str, Any]] | None = dict,
              ) -> CycleResult:
    outcome, _ = await asyncio.gather(setup.engine.run(ef.request()), answer(setup, decide))
    return outcome.result


# --- publishing ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_an_operator_enter_is_published_with_its_intent_id() -> None:
    publisher = FakePublisher(PublishOutcome(intent_id=INTENT_ID, code="BOOK_PUBLISHED"))
    result = await run(rig(ef.candidate(), publisher=publisher))
    assert (result.status, result.hold_reason, result.intent_id) == ("ENTER", None, INTENT_ID)
    assert (result.provider, result.provider_status) == ("operator", "ok")
    (sent,) = publisher.requests
    assert (sent.agent, sent.candidate.candidate_id, sent.sizing.lots) == (
        "codex", ef.CANDIDATE_ID, 0.01)
    assert sent.decision.risk_tier == "standard"
    operator_views = [(r.role, r.model) for r in result.view_records if r.source == "operator"]
    assert operator_views == [(role, "codex") for role in (
        "price_action", "news_risk", "liquidity", "structure", "chief")]


@pytest.mark.anyio
async def test_a_session_that_cannot_publish_records_a_shadow_entry() -> None:
    publisher = FakePublisher(PublishOutcome(code="SESSION_NOT_ARMED",
                                             detail="not published: SESSION_NOT_ARMED"))
    result = await run(rig(ef.candidate(), publisher=publisher))
    assert (result.status, result.intent_id) == ("ENTER_SHADOW", None)
    assert result.hold_detail == "not published: SESSION_NOT_ARMED"
    assert result.shadow_intent is not None and result.shadow_intent.source == "operator"


@pytest.mark.anyio
@pytest.mark.parametrize(("reason", "status"), [
    (HoldReason.LATE, "LATE"), (HoldReason.HALTED, "HOLD"), (HoldReason.SIZE, "HOLD")])
async def test_a_publishing_refusal_holds(reason: HoldReason, status: str) -> None:
    publisher = FakePublisher(PublishOutcome(hold_reason=reason, code="X", detail="no"))
    result = await run(rig(ef.candidate(), publisher=publisher))
    assert (result.status, result.hold_reason, result.hold_detail) == (status, reason, "no")
    assert result.shadow_intent is None and result.sizing is not None


@pytest.mark.anyio
async def test_shadow_mode_never_asks_the_publisher() -> None:
    publisher = FakePublisher(PublishOutcome(intent_id=INTENT_ID))
    result = await run(rig(ef.candidate(), publisher=publisher, mode="shadow"))
    assert result.status == "ENTER_SHADOW" and publisher.requests == []


# --- the decision -----------------------------------------------------------------------------
@pytest.mark.anyio
async def test_a_withdrawn_candidate_holds() -> None:
    result = await run(rig(ef.candidate()), withdraw_all)
    assert (result.status, result.hold_reason) == ("HOLD", HoldReason.NO_TAKE)


@pytest.mark.anyio
async def test_a_withdrawn_packet_holds_as_an_operator_timeout() -> None:
    result = await run(rig(ef.candidate()), None)
    assert (result.status, result.hold_reason) == ("HOLD", HoldReason.OPERATOR_TIMEOUT)
    assert result.hold_detail == "operator cycle closed without a decision (cancelled)"
    chief = result.view_records[-1]
    assert (chief.role, chief.source, chief.error_code) == (
        "chief", "operator", "PROVIDER_TIMEOUT")


# --- no packet ------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_a_candidate_the_budget_cannot_size_holds_without_asking() -> None:
    setup = rig(ef.candidate(invalidation=ef.PRICE - 23.0))
    result = (await setup.engine.run(ef.request())).result
    assert (result.status, result.hold_reason) == ("HOLD", HoldReason.SIZE)
    assert result.hold_detail.startswith("PACKET_NO_SIZED_CANDIDATE: ")
    assert setup.queue.status().counts.offered == 0


@pytest.mark.anyio
async def test_a_packet_at_the_deadline_is_late() -> None:
    at_deadline = float(ef.AS_OF + 300)
    setup = rig(ef.candidate(), clock=FakeClock(epoch=at_deadline))
    snapshot = ef.engine_snapshot(sent_at_epoch=int(at_deadline))
    result = (await setup.engine.run(ef.request(snapshot, received_at=at_deadline))).result
    assert (result.status, result.hold_reason) == ("LATE", HoldReason.LATE)
    assert result.hold_detail.startswith("PACKET_DEADLINE_PASSED: ")
