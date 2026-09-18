"""The engine offers management packets, sends management actions and enters v3 plans."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.v6.cycle_codes import HoldReason
from app.v6.deliberation.publication import ManageOutcome, PublishOutcome
from app.v6.ledger_actions import ActionRow
from app.v6.ledger_intents import IntentRecord

from . import engine_fixtures_v6 as ef
from .cycle_fixtures_v6 import pa_payload
from .test_engine_operator import WAIT_STEP_S, WAIT_STEPS, FakePublisher, Rig, rig

INTENT_ID = "k7w2m4pq3xza"
POSITION = {"ticket": 91, "magic": 250570, "side": "buy", "volume": 0.01,
            "price_open": 4296.5, "sl": 4289.0, "tp": 4310.0, "profit": 3.5, "swap": 0.0,
            "open_epoch": ef.AS_OF - 1200, "comment": f"Q6:{INTENT_ID}",
            "mae_points": 80.0, "mfe_points": 390.0}
RESTING = {"ticket": 77, "magic": 250570, "order_type": "BUY_LIMIT", "price": 4296.5,
           "sl": 4289.0, "tp": 4310.0, "volume": 0.01, "expiration_epoch": ef.AS_OF + 900,
           "comment": f"Q6:{INTENT_ID}"}
RICH = {"login": "12345", "trade_mode": "DEMO", "server": "Broker-Demo", "currency": "USD",
        "leverage": 200, "balance": 96_896.0, "equity": 96_896.0, "margin": 0.0,
        "free_margin": 96_896.0, "margin_level": 0.0}
Edit = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class FakePlans:
    """A PlanReader over fixed rows."""

    record: IntentRecord | None = None
    action: ActionRow | None = None
    asked: list[tuple[str, object]] = field(default_factory=list)

    def intent(self, intent_id: str) -> IntentRecord | None:
        self.asked.append(("intent", intent_id))
        return self.record if self.record and self.record.intent_id == intent_id else None

    def by_ticket(self, ticket: int) -> IntentRecord | None:
        self.asked.append(("ticket", ticket))
        return self.record if self.record and self.record.ticket == ticket else None

    def last_action(self, session_id: str) -> ActionRow | None:
        self.asked.append(("action", session_id))
        return self.action


def published() -> FakePublisher:
    return FakePublisher(PublishOutcome(intent_id=INTENT_ID, code="BOOK_PUBLISHED"))


async def answer(setup: Rig, edit: Edit, request) -> tuple[Any, dict[str, Any]]:
    """Run one cycle; the agent edits the packet's template and submits it."""
    seen: dict[str, Any] = {}

    async def agent() -> None:
        for _ in range(WAIT_STEPS):
            if setup.queue.pending is not None:
                break
            await asyncio.sleep(WAIT_STEP_S)
        packet = setup.queue.pending.packet.model_dump(mode="json")
        seen.update(packet)
        template = {**json.loads(json.dumps(packet["decision_template"])),
                    "agent": "claude_code"}
        seen["submit"] = setup.queue.submit(json.dumps(edit(template, packet)).encode("utf-8"),
                                            setup.clock.now_epoch())

    outcome, _ = await asyncio.gather(setup.engine.run(request), agent())
    return outcome.result, seen


def position_request(**payload: Any):
    return ef.request(ef.engine_snapshot(positions=[POSITION], **payload))


def keep(template: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    return template


def close(template: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    return {**template, "manage": {"target": "position", "ticket": 91, "op": "CLOSE"}}


def cancel(template: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    return {**template, "manage": {"target": "pending", "ticket": 77, "op": "CANCEL"}}


# --- management -------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_an_open_position_is_kept() -> None:
    setup = rig(publisher=published())
    result, packet = await answer(setup, keep, position_request())
    assert (packet["state"], packet["position"]["ticket"]) == ("position", 91)
    assert packet["candidates"] == [] and packet["limits"]["agent_entry_possible"] is False
    assert (result.status, result.hold_reason) == ("HOLD", HoldReason.MANAGE_KEPT)


@pytest.mark.anyio
async def test_a_close_is_sent_to_the_publisher() -> None:
    publisher = published()
    setup = rig(publisher=publisher)
    result, _ = await answer(setup, close, position_request())
    assert (result.status, result.hold_reason) == ("HOLD", HoldReason.MANAGE_SENT)
    dispatch = publisher.dispatches[0]
    assert (dispatch.request.op, dispatch.action.command, dispatch.action.ticket) == (
        "CLOSE", "CLOSE_POSITION", 91)
    assert (dispatch.agent, dispatch.session_id) == ("claude_code", "sess-1")


@pytest.mark.anyio
async def test_a_refused_action_holds_with_its_reason() -> None:
    publisher = FakePublisher(PublishOutcome(), ManageOutcome(False, "SESSION_NOT_ARMED", "no"))
    setup = rig(publisher=publisher)
    result, _ = await answer(setup, close, position_request())
    assert result.hold_reason == HoldReason.MANAGE_REFUSED
    assert "SESSION_NOT_ARMED" in result.hold_detail


@pytest.mark.anyio
async def test_shadow_mode_only_records_the_request() -> None:
    publisher = published()
    setup = rig(publisher=publisher, mode="shadow")
    result, _ = await answer(setup, close, position_request())
    assert result.hold_reason == HoldReason.MANAGE_KEPT and publisher.dispatches == []
    assert "recorded only" in result.hold_detail


@pytest.mark.anyio
async def test_a_pending_cancel_is_dispatched_without_an_action() -> None:
    publisher = published()
    setup = rig(publisher=publisher)
    result, packet = await answer(setup, cancel,
                                  ef.request(ef.engine_snapshot(pending_orders=[RESTING])))
    assert packet["state"] == "pending" and packet["pending_order"]["ticket"] == 77
    assert (publisher.dispatches[0].request.op, publisher.dispatches[0].action) == (
        "CANCEL", None)
    assert result.hold_reason == HoldReason.MANAGE_SENT


@pytest.mark.anyio
async def test_a_wide_spread_still_gets_a_management_packet() -> None:
    setup = rig(publisher=published())
    wide = {"bid": 4300.0, "ask": 4301.0, "spread_points": 100, "time_msc": 1}
    result, packet = await answer(setup, keep, position_request(quote=wide))
    assert packet["state"] == "position"
    assert result.hold_reason == HoldReason.MANAGE_KEPT
    assert "SPREAD" in {gate["code"] for gate in packet["gates"] if not gate["passed"]}


@pytest.mark.anyio
async def test_no_management_packet_while_halted() -> None:
    setup = rig(publisher=published())
    request = ef.request(ef.engine_snapshot(positions=[POSITION]), halt_sources=("file",))
    outcome = await setup.engine.run(request)
    assert outcome.result.hold_reason == HoldReason.HALTED
    assert setup.queue.pending is None


def stored(**changes: Any) -> IntentRecord:
    fields = dict(intent_id=INTENT_ID, cycle_id="c-00000000000000aa", session_id="sess-1",
                  agent="claude_code", source="operator", status="FILLED", side="buy",
                  order_type="BUY_LIMIT", entry=4296.5, sl=4289.0, tp=4310.0, lots=0.01,
                  risk_usd=7.9, valid_until_epoch=1, pending_expiry_epoch=2,
                  time_barrier_s=7200, created_at=1.0, ticket=91, tp1=4300.5, tp2=4304.0,
                  sl_after_tp1=4297.0, sl_after_tp2=4300.5)
    return IntentRecord(**{**fields, **changes})


@pytest.mark.anyio
async def test_the_packet_carries_the_stored_plan_and_the_last_action() -> None:
    action = ActionRow(action_id="m3a7q2z5k6pw", cycle_id="c-00000000000000aa",
                       session_id="sess-1", agent="claude_code", command="MODIFY_POSITION",
                       ticket=91, status="APPLIED", detail="NONE retcode 10009",
                       updated_at=ef.AS_OF - 300)
    plans = FakePlans(record=stored(), action=action)
    setup = rig(publisher=published(), plans=plans)
    _, packet = await answer(setup, keep, position_request())
    plan = packet["position"]["plan"]
    assert (plan["tp1"], plan["sl_after_tp2"], plan["time_limit_min"]) == (4300.5, 4300.5, 120)
    assert (packet["last_action"]["op"], packet["last_action"]["status"]) == (
        "MODIFY_POSITION", "APPLIED")
    assert ("intent", INTENT_ID) in plans.asked


@pytest.mark.anyio
async def test_a_rewritten_comment_finds_the_plan_by_ticket() -> None:
    plans = FakePlans(record=stored())
    setup = rig(publisher=published(), plans=plans)
    renamed = {**POSITION, "comment": "[tp 4310.00]"}
    _, packet = await answer(setup, keep, ef.request(ef.engine_snapshot(positions=[renamed])))
    assert packet["position"]["intent_id"] == INTENT_ID
    assert ("ticket", 91) in plans.asked


# --- v3 entries ----------------------------------------------------------------------------------
def stop_plan(limits: dict[str, Any], *, stop: float = 7.0,
              lots: float = 0.01) -> dict[str, Any]:
    entry = round(limits["buy_stop_min"] + 1.0, 2)
    reach = round(stop * 2, 2)
    return {"side": "buy", "order_type": "STOP", "entry": entry,
            "sl": round(entry - stop, 2), "tp1": round(entry + stop * 0.55, 2),
            "tp2": round(entry + stop * 1.1, 2), "tp3": round(entry + reach, 2),
            "sl_after_tp1": round(entry + 0.5, 2), "sl_after_tp2": round(entry + stop * 0.55, 2),
            "time_limit_min": 120, "pending_expiry_min": 30, "lots": lots}


def stop_entry(stop: float = 7.0, lots: float = 0.01) -> Edit:
    def edit(template: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
        limits = packet["limits"]
        plan = stop_plan(limits, stop=stop, lots=lots)
        views = {**template["views"],
                 "price_action": pa_payload(limits["agent_entry_id"], conviction=0.8)}
        return {**template, "action": "ENTER", "entry_plan": plan, "views": views,
                "m15_bias": {"direction": "up", "levels": [plan["entry"]],
                             "scenario": "breakout"}}
    return edit


@pytest.mark.anyio
async def test_a_stop_entry_is_published_with_its_plan_and_the_bias_is_remembered() -> None:
    publisher = published()
    setup = rig(publisher=publisher)
    result, seen = await answer(setup, stop_entry(), ef.request())
    assert seen["submit"].accepted, seen["submit"]
    assert result.status == "ENTER"
    request = publisher.requests[0]
    assert (request.plan.order_type, request.plan.time_limit_s) == ("STOP", 7200)
    assert request.plan.sl_after_tp2 > request.plan.sl_after_tp1 > 0
    assert request.candidate.setup == "agent"
    bias, at = setup.engine.deps.bias.latest()
    assert bias.direction == "up" and at == ef.AS_OF


@pytest.mark.anyio
async def test_the_next_packet_echoes_the_bias() -> None:
    setup = rig(publisher=published())
    await answer(setup, stop_entry(), ef.request())
    _, packet = await answer(setup, keep, ef.request(ef.engine_snapshot("snap-eng-0002")))
    assert packet["last_bias"]["direction"] == "up"
    assert packet["decision_template"]["m15_bias"]["direction"] == "up"
    assert packet["last_bias_at_epoch"] == ef.AS_OF


@pytest.mark.anyio
@pytest.mark.parametrize(("basis", "lots", "expected"), [
    (5000.0, 0.01, 0.01),
    (5000.0, 0.02, 0.02),
    (5000.0, 0.03, 0.03),     # $25 pays 3 x ($6.50 + $0.60)
    (3000.0, 0.03, 0.02),     # $15 does not: reduced, never refused
])
async def test_the_plan_lots_cap_the_size(basis: float, lots: float, expected: float) -> None:
    publisher = published()
    setup = rig(publisher=publisher, sizing_equity_basis_usd=basis)
    result, seen = await answer(setup, stop_entry(stop=6.5, lots=lots),
                                ef.request(ef.engine_snapshot(account=RICH)))
    assert seen["submit"].accepted, seen["submit"]
    sizing = publisher.requests[0].sizing
    assert (result.status, sizing.lots) == ("ENTER", expected)
    assert sizing.risk_usd <= sizing.risk_budget_usd == basis * 0.005
