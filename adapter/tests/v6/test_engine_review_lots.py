"""The engine's pending-order review and the operator's lot choice."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.v6.cycle_codes import HoldReason
from app.v6.cycle_types import CycleResult
from app.v6.deliberation.pending_review import REASON_AGENT_CANCEL
from app.v6.deliberation.publication import PublishOutcome

from . import engine_fixtures_v6 as ef
from .test_engine_operator import (
    INTENT_ID, WAIT_STEP_S, WAIT_STEPS, FakePublisher, Rig, agent_decision, agent_plan, rig,
    run_agent,
)

RESTING = {"ticket": 91, "magic": 250570, "order_type": "BUY_LIMIT", "price": 4296.5,
           "sl": 4289.0, "tp": 4310.0, "volume": 0.02, "expiration_epoch": ef.AS_OF + 900,
           "comment": "Q6:k7w2m4pq3xza"}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def resting_request(**payload: Any):
    snapshot = ef.engine_snapshot(pending_orders=[RESTING], **payload)
    return ef.request(snapshot)


async def review(setup: Rig, action: str | None, request=None) -> tuple[CycleResult, dict]:
    seen: dict[str, Any] = {}

    async def answer() -> None:
        for _ in range(WAIT_STEPS):
            if setup.queue.pending is not None:
                break
            await asyncio.sleep(WAIT_STEP_S)
        packet = setup.queue.pending.packet.model_dump(mode="json")
        seen.update(packet)
        decision = json.loads(json.dumps(packet["decision_template"]))
        decision.update(agent="claude_code", pending_action=action)
        result = setup.queue.submit(json.dumps(decision).encode("utf-8"),
                                    setup.clock.now_epoch())
        seen["submit"] = result

    outcome, _ = await asyncio.gather(setup.engine.run(request or resting_request()), answer())
    return outcome.result, seen


def published() -> FakePublisher:
    return FakePublisher(PublishOutcome(intent_id=INTENT_ID, code="BOOK_PUBLISHED"))


# --- review ---------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_a_resting_order_is_reviewed_and_kept() -> None:
    setup = rig(publisher=published())
    result, packet = await review(setup, "KEEP")
    assert packet["pending_order"]["price"] == 4296.5
    assert packet["pending_order"]["intent_id"] == "k7w2m4pq3xza"
    assert packet["pending_order"]["distance_from_quote"] == pytest.approx(3.7)
    assert packet["candidates"] == [] and packet["limits"]["agent_entry_possible"] is False
    assert packet["decision_template"]["pending_action"] == "KEEP"
    assert (result.status, result.hold_reason) == ("HOLD", HoldReason.PENDING_KEPT)


@pytest.mark.anyio
async def test_a_cancel_queues_the_ea_command() -> None:
    publisher = published()
    setup = rig(publisher=publisher)
    result, _ = await review(setup, "CANCEL")
    assert (result.status, result.hold_reason) == ("HOLD", HoldReason.PENDING_CANCELLED)
    assert publisher.cancels == [REASON_AGENT_CANCEL]
    assert "undelivered intents cancelled: 0" in result.hold_detail


@pytest.mark.anyio
async def test_a_shadow_cancel_is_only_recorded() -> None:
    publisher = published()
    setup = rig(publisher=publisher, mode="shadow")
    result, _ = await review(setup, "CANCEL")
    assert result.hold_reason == HoldReason.PENDING_CANCELLED and publisher.cancels == []


@pytest.mark.anyio
async def test_a_review_without_an_action_is_refused() -> None:
    setup = rig(publisher=published())
    setup_task = asyncio.create_task(review(setup, None))
    for _ in range(WAIT_STEPS):
        if setup.queue.pending is not None:
            break
        await asyncio.sleep(WAIT_STEP_S)
    await asyncio.sleep(WAIT_STEP_S * 5)
    status = setup.queue.status(setup.clock.now_epoch())
    setup.queue.withdraw(setup.clock.now_epoch())
    result, seen = await setup_task
    assert not seen["submit"].accepted and seen["submit"].error == "DECISION_REVIEW"
    assert status.pending is not None
    assert result.hold_reason == HoldReason.OPERATOR_TIMEOUT


@pytest.mark.anyio
async def test_a_halted_bar_with_a_resting_order_is_not_reviewed() -> None:
    setup = rig(publisher=published())
    request = ef.request(ef.engine_snapshot(pending_orders=[RESTING]), halt_sources=("file",))
    result = (await setup.engine.run(request)).result
    assert (result.status, result.hold_reason) == ("HOLD", HoldReason.HALTED)
    assert setup.queue.status().counts.offered == 0


@pytest.mark.anyio
async def test_an_open_position_is_not_reviewed() -> None:
    position = {"ticket": 7, "magic": 250570, "side": "buy", "volume": 0.01,
                "price_open": 4296.0, "sl": 4289.0, "tp": 4310.0, "profit": 1.0, "swap": 0.0,
                "open_epoch": ef.T_BAR, "comment": "Q6:k7w2m4pq3xza", "mae_points": 0.0,
                "mfe_points": 0.0}
    setup = rig(publisher=published())
    request = ef.request(ef.engine_snapshot(pending_orders=[RESTING], positions=[position]))
    result = (await setup.engine.run(request)).result
    assert result.hold_reason == HoldReason.GATE and setup.queue.status().counts.offered == 0


# --- lots -----------------------------------------------------------------------------------
def with_lots(lots: float | None):
    base = agent_decision()

    def decide(empty: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
        decision = base(empty, packet)
        decision["lots"] = lots
        return decision
    return decide


RICH = {"login": "12345", "trade_mode": "DEMO", "server": "Broker-Demo", "currency": "USD",
        "leverage": 200, "balance": 96_896.0, "equity": 96_896.0, "margin": 0.0,
        "free_margin": 96_896.0, "margin_level": 0.0}


async def run_rich(setup: Rig, decide) -> CycleResult:
    request = ef.request(ef.engine_snapshot(account=RICH))

    async def answer() -> None:
        for _ in range(WAIT_STEPS):
            if setup.queue.pending is not None:
                break
            await asyncio.sleep(WAIT_STEP_S)
        packet = setup.queue.pending.packet.model_dump(mode="json")
        body = json.dumps(decide({}, packet)).encode("utf-8")
        assert setup.queue.submit(body, setup.clock.now_epoch()).accepted

    outcome, _ = await asyncio.gather(setup.engine.run(request), answer())
    return outcome.result


@pytest.mark.anyio
@pytest.mark.parametrize(("basis", "lots", "expected"), [
    (5000.0, None, 0.01),     # no request: the minimum
    (5000.0, 0.02, 0.02),
    (5000.0, 0.03, 0.03),     # $25 pays 3 x ($6.50 + $0.60)
    (3000.0, 0.03, 0.02),     # $15 does not: reduced, never refused
])
async def test_the_requested_lots_cap_the_size(basis: float, lots: float | None,
                                               expected: float) -> None:
    publisher = published()
    setup = rig(publisher=publisher, sizing_equity_basis_usd=basis)
    await run_rich(setup, with_lots(lots))
    sizing = publisher.requests[0].sizing
    assert sizing.lots == expected
    assert sizing.risk_usd <= sizing.risk_budget_usd == basis * 0.005


def test_the_plan_helper_is_shared() -> None:
    assert callable(agent_plan)
