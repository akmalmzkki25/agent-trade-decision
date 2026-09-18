"""m1 cycles through the engine: served packets, entries, management, vetoes and skips."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any, Callable

import pytest

from app.v6.cycle_codes import HoldReason
from app.v6.deliberation.cycle_draft import CycleRequest
from app.v6.deliberation.minute_flow import MinuteBase, MinuteRun
from app.v6.deliberation.publication import PublishOutcome
from app.v6.risk.gates import RuntimeGateState
from app.v6.schemas.agents import LiquidityView

from . import engine_fixtures_v6 as ef
from .test_engine_management import POSITION
from .test_engine_operator import WAIT_STEP_S, WAIT_STEPS, FakePublisher, Rig, rig
from .test_minute_context import MINUTE_CLOSE, MINUTE_OPEN, minute

Decide = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
NO_TRADE = LiquidityView(stance="NO_TRADE", size_multiplier=0.0, order_style="LIMIT",
                         reason_codes=("FRICTION_HIGH",), note="")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _served(setup: Rig, task: asyncio.Future) -> bool:
    for _ in range(WAIT_STEPS):
        if setup.queue.pending is not None or task.done():
            break
        await asyncio.sleep(WAIT_STEP_S)
    return setup.queue.pending is not None


async def m15_base(setup: Rig) -> MinuteBase:
    """The newest M15 cycle, unanswered: what the minute worker would carry."""
    task = asyncio.ensure_future(setup.engine.run(ef.request()))
    if await _served(setup, task):
        setup.queue.withdraw(setup.clock.now_epoch())
    outcome = await task
    return MinuteBase(snapshot=ef.engine_snapshot(), context=outcome.context,
                      views=outcome.result.views)


def minute_request(**changes: Any) -> CycleRequest:
    snapshot = minute(**changes)
    return CycleRequest(cycle_id=f"m-{MINUTE_OPEN:016x}", snapshot=ef.engine_snapshot(),
                        received_at=float(MINUTE_CLOSE + 1),
                        runtime=RuntimeGateState(warmed_up=True), session_id="sess-1",
                        session_armed=True, minute=snapshot)


async def run(setup: Rig, base: MinuteBase, request: CycleRequest,
              decide: Decide | None) -> tuple[MinuteRun, dict[str, Any]]:
    seen: dict[str, Any] = {}
    setup.clock.advance(MINUTE_CLOSE + 1 - setup.clock.now_epoch())
    task = asyncio.ensure_future(setup.engine.run_minute(request, base))
    if await _served(setup, task):
        packet = setup.queue.pending.packet.model_dump(mode="json")
        seen.update(packet)
        if decide is None:
            setup.queue.withdraw(setup.clock.now_epoch())
        else:
            template = {**json.loads(json.dumps(packet["decision_template"])),
                        "agent": "claude_code"}
            body = json.dumps(decide(template, packet)).encode("utf-8")
            seen["submit"] = setup.queue.submit(body, setup.clock.now_epoch())
    return await task, seen


def hold(template: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    return template


def sell_limit(template: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    """A sell LIMIT 10 above the bid whose 1.5R TP3 stays in front of the $4300 level."""
    risk = round(packet["limits"]["stop_floor"] + 0.5, 2)
    entry = round(packet["market"]["bid"] + 10.0, 2)
    plan = {"side": "sell", "order_type": "LIMIT", "entry": entry, "sl": round(entry + risk, 2),
            "tp1": round(entry - 0.6 * risk, 2), "tp2": round(entry - 1.0 * risk, 2),
            "tp3": round(entry - 1.5 * risk, 2), "sl_after_tp1": None, "sl_after_tp2": None,
            "time_limit_min": 90, "pending_expiry_min": 20, "lots": 0.01,
            "thesis": "fade the M1 spike into resistance"}
    return {**template, "action": "ENTER", "entry_plan": plan}


def close(template: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    return {**template, "manage": {"target": "position", "ticket": 91, "op": "CLOSE"}}


def published() -> FakePublisher:
    return FakePublisher(PublishOutcome(intent_id="k7w2m4pq3xza", code="BOOK_PUBLISHED"))


@pytest.mark.anyio
async def test_a_flat_minute_serves_an_m1_packet() -> None:
    setup = rig(publisher=published())
    base = await m15_base(setup)
    result, packet = await run(setup, base, minute_request(), hold)
    assert (packet["packet_kind"], packet["state"], packet["bar_close_epoch"]) == (
        "m1", "flat", MINUTE_CLOSE)
    assert packet["decision_template"]["views"] is None and packet["m1_state"] is not None
    assert result.outcome is not None and result.outcome.result.status == "HOLD"
    assert result.outcome.result.cycle_id.startswith("m-")


@pytest.mark.anyio
async def test_an_m1_enter_is_published() -> None:
    publisher = published()
    setup = rig(publisher=publisher)
    base = await m15_base(setup)
    result, packet = await run(setup, base, minute_request(), sell_limit)
    assert packet["submit"].accepted, packet["submit"]
    assert result.outcome.result.status == "ENTER"
    request = publisher.requests[0]
    assert request.candidate.candidate_id == packet["limits"]["agent_entry_id"]
    assert request.context.as_of_epoch == MINUTE_CLOSE and request.plan is not None


@pytest.mark.anyio
async def test_an_m15_liquidity_veto_blocks_an_m1_enter() -> None:
    setup = rig(publisher=published())
    base = await m15_base(setup)
    vetoed = replace(base, views=replace(base.views, liquidity=NO_TRADE))
    result, packet = await run(setup, vetoed, minute_request(), sell_limit)
    assert packet["baseline_views"]["liquidity"]["stance"] == "NO_TRADE"
    assert result.outcome.result.hold_reason == HoldReason.VETO


@pytest.mark.anyio
async def test_a_managed_minute_can_close_the_position() -> None:
    publisher = published()
    setup = rig(publisher=publisher)
    base = await m15_base(setup)
    result, packet = await run(setup, base, minute_request(positions=[POSITION]), close)
    assert (packet["state"], packet["packet_kind"]) == ("position", "m1")
    assert result.outcome.result.hold_reason == HoldReason.MANAGE_SENT
    assert publisher.dispatches[0].action.command == "CLOSE_POSITION"


@pytest.mark.anyio
async def test_a_failed_gate_skips_a_flat_minute() -> None:
    setup = rig(publisher=published())
    base = await m15_base(setup)
    wide = {"bid": 4300.0, "ask": 4301.0, "spread_points": 100, "time_msc": MINUTE_CLOSE * 1000}
    result, packet = await run(setup, base, minute_request(quote=wide), hold)
    assert result.outcome is None and result.skipped.startswith("GATES:")
    assert "SPREAD" in result.skipped and packet == {}


@pytest.mark.anyio
async def test_an_unanswered_minute_holds() -> None:
    setup = rig(publisher=published())
    base = await m15_base(setup)
    result, _ = await run(setup, base, minute_request(), None)
    assert result.outcome.result.hold_reason == HoldReason.OPERATOR_TIMEOUT
