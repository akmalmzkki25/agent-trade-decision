"""The engine on the operator backend: packets, decisions, timeouts and publishing."""

from __future__ import annotations

import asyncio
import copy
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
from app.v6.deliberation.publication import (
    ManageDispatch, ManageOutcome, PublishOutcome, PublishRequest,
)
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
    managed: ManageOutcome = ManageOutcome(True, "QUEUED", "queued")
    requests: list[PublishRequest] = field(default_factory=list)
    cancels: list[str] = field(default_factory=list)
    dispatches: list[ManageDispatch] = field(default_factory=list)

    async def publish(self, request: PublishRequest) -> PublishOutcome:
        self.requests.append(request)
        return self.outcome

    async def cancel_pending(self, reason: str) -> tuple[str, ...]:
        self.cancels.append(reason)
        return ()

    async def manage(self, dispatch: ManageDispatch) -> ManageOutcome:
        self.dispatches.append(dispatch)
        return self.managed


@dataclass(frozen=True)
class Rig:
    engine: DeliberationEngine
    queue: OperatorQueue
    clock: FakeClock


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def rig(*candidates: Any, publisher: FakePublisher | None = None,
        clock: FakeClock | None = None, plans: Any = None, **overrides: Any) -> Rig:
    config = ef.settings(**(OPERATOR | overrides))
    fake = clock or ef.clock_at()
    queue = OperatorQueue(settings=config, clock=fake)
    engine = DeliberationEngine(EngineDeps(
        settings=config, clock=fake, bars=ef.MemoryBars(ef.history()),
        breakers=ef.healthy_breakers(config), rules=rules_provider_for(config, fake),
        panel=None, detector=ef.detector_of(*candidates), operator=queue,
        publisher=publisher, plans=plans))
    return Rig(engine=engine, queue=queue, clock=fake)


def hold_instead(decision: dict[str, Any]) -> dict[str, Any]:
    """The same views, but the agent answers HOLD."""
    return decision | {"action": "HOLD", "entry_plan": None}


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
        "codex", f"agent-{ef.T_BAR}", 0.01)
    assert (sent.candidate.setup, sent.plan.tp1, sent.plan.time_limit_s) == ("agent", 4306.0, 7200)
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
async def test_a_hold_decision_holds() -> None:
    result = await run(rig(ef.candidate()), hold_instead)
    assert (result.status, result.hold_reason) == ("HOLD", HoldReason.CHIEF_HOLD)


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
async def test_an_unsizable_suggestion_is_left_out_but_the_agent_is_still_asked() -> None:
    setup = rig(ef.candidate(invalidation=ef.PRICE - 23.0))
    offered: list[dict[str, Any]] = []

    async def capture() -> None:
        for _ in range(WAIT_STEPS):
            if setup.queue.pending is not None:
                offered.append(setup.queue.pending.packet.model_dump(mode="json"))
                setup.queue.withdraw(setup.clock.now_epoch())
                return
            await asyncio.sleep(WAIT_STEP_S)

    outcome, _ = await asyncio.gather(setup.engine.run(ef.request()), capture())
    packet = offered[0]
    assert packet["candidates"] == []
    assert packet["allowed"]["candidate_ids"] == [packet["limits"]["agent_entry_id"]]
    assert outcome.result.hold_reason == HoldReason.OPERATOR_TIMEOUT


# --- agent-designed entries ---------------------------------------------------------------
def ladder(side: str, entry: float, distance: float, reward_r: float) -> dict[str, Any]:
    """A LIMIT plan: SL `distance` away, TP1 0.6R, TP2 0.9R, TP3 `reward_r`, no SL+ steps."""
    sign = 1 if side == "buy" else -1
    return {"side": side, "order_type": "LIMIT", "entry": entry,
            "sl": round(entry - sign * distance, 2),
            "tp1": round(entry + sign * 0.6 * distance, 2),
            "tp2": round(entry + sign * 0.9 * distance, 2),
            "tp3": round(entry + sign * reward_r * distance, 2),
            "time_limit_min": 120, "pending_expiry_min": 30, "lots": 0.01}


def agent_plan(packet: dict[str, Any]) -> dict[str, Any]:
    """A sell LIMIT 10 above the bid whose 1.5R TP3 stays in front of the $4300 level
    (a target beyond a $50 level would be pulled in front of it)."""
    distance = round(packet["limits"]["stop_floor"] + 0.5, 2)
    entry = round(packet["market"]["bid"] + 10.0, 2)
    return ladder("sell", entry, distance, 1.5) | {"thesis": "fade into resistance"}


def agent_decision(plan: Callable[[dict[str, Any]], dict[str, Any]] = agent_plan
                   ) -> Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]:
    """A decide() for `run_agent`: Price Action takes the agent entry, the agent ENTERs."""
    def decide(_: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
        entry_id = packet["limits"]["agent_entry_id"]
        decision = copy.deepcopy(packet["decision_template"])
        decision.update(agent="claude_code", action="ENTER", entry_plan=plan(packet))
        decision["views"]["price_action"] = {"abstain": False, "ranked": [{
            "candidate_id": entry_id, "verdict": "TAKE", "conviction": 0.8,
            "reason_codes": ["LEVEL_CONFLUENCE"], "note": "own read"}]}
        return decision
    return decide


async def run_agent(setup: Rig, decide: Callable[[dict[str, Any], dict[str, Any]],
                                                 dict[str, Any]]) -> CycleResult:
    async def answer_with_packet() -> None:
        for _ in range(WAIT_STEPS):
            if setup.queue.pending is not None:
                break
            await asyncio.sleep(WAIT_STEP_S)
        packet = setup.queue.pending.packet.model_dump(mode="json")
        body = json.dumps(decide({}, packet)).encode("utf-8")
        result = setup.queue.submit(body, setup.clock.now_epoch())
        assert result.accepted, result

    outcome, _ = await asyncio.gather(setup.engine.run(ef.request()), answer_with_packet())
    return outcome.result


@pytest.mark.anyio
async def test_an_agent_entry_without_suggestions_is_published() -> None:
    publisher = FakePublisher(PublishOutcome(intent_id=INTENT_ID, code="BOOK_PUBLISHED"))
    setup = rig(publisher=publisher)
    result = await run_agent(setup, agent_decision())
    assert (result.status, result.intent_id) == ("ENTER", INTENT_ID)
    request = publisher.requests[0]
    assert (request.candidate.setup, request.candidate.side) == ("agent", "sell")
    assert request.candidate.candidate_id == f"agent-{ef.T_BAR}"
    assert request.sizing.lots == 0.01 and request.exit_plan.reward_r >= 1.0
    chosen = [item for item in result.candidates if item.candidate.setup == "agent"]
    assert [item.verdict for item in chosen] == ["chosen"]


@pytest.mark.anyio
async def test_an_agent_entry_the_exit_plan_refuses_holds() -> None:
    """A target just past a $50 level is pulled in front of it, under 1R: refused."""
    def near_round(packet: dict[str, Any]) -> dict[str, Any]:
        return ladder("buy", 4299.0, round(packet["limits"]["stop_floor"] + 0.5, 2), 1.1)

    setup = rig(publisher=FakePublisher(PublishOutcome(intent_id=INTENT_ID, code="BOOK_PUBLISHED")))
    result = await run_agent(setup, agent_decision(near_round))
    assert (result.status, result.hold_reason) == ("HOLD", HoldReason.EXIT)
    assert result.hold_detail.startswith("the agent entry was refused by the exit plan")


@pytest.mark.anyio
async def test_a_packet_at_the_deadline_is_late() -> None:
    at_deadline = float(ef.AS_OF + 180)
    setup = rig(ef.candidate(), clock=FakeClock(epoch=at_deadline))
    snapshot = ef.engine_snapshot(sent_at_epoch=int(at_deadline))
    result = (await setup.engine.run(ef.request(snapshot, received_at=at_deadline))).result
    assert (result.status, result.hold_reason) == ("LATE", HoldReason.LATE)
    assert result.hold_detail.startswith("PACKET_DEADLINE_PASSED: ")


@pytest.mark.anyio
async def test_the_operator_path_holds_without_a_session_and_asks_nobody() -> None:
    setup = rig()
    result = (await setup.engine.run(ef.request(session_id=None))).result
    assert (result.status, result.hold_reason) == ("HOLD", HoldReason.NO_SESSION)
    assert setup.queue.status().counts.offered == 0


@pytest.mark.anyio
async def test_the_operator_path_is_late_past_the_deadline() -> None:
    late = float(ef.AS_OF + 301)
    setup = rig(clock=FakeClock(epoch=late))
    snapshot = ef.engine_snapshot(sent_at_epoch=int(late))
    result = (await setup.engine.run(ef.request(snapshot, received_at=late))).result
    assert (result.status, result.hold_reason) == ("LATE", HoldReason.LATE)
    assert setup.queue.status().counts.offered == 0
