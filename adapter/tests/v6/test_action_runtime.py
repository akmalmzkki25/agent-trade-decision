"""Management actions: queued, served on polls, settled by EA reports."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from app.v6 import wire
from app.v6.clock import FakeClock
from app.v6.container import V6Container
from app.v6.deliberation.publication import ManageDispatch, ManagementAction
from app.v6.ledger_actions import ActionRow
from app.v6.ledger_intents import NewIntent
from app.v6.runtime.action_desk import ActionDesk
from app.v6.runtime.actions import ActionBoard, action_response
from app.v6.runtime.poll_reply import PollFacts
from app.v6.runtime.publisher import IntentPublisher
from app.v6.schemas.intent import ActionReport
from app.v6.schemas.operator_plan import ManageRequest

from . import engine_fixtures_v6 as ef
from .desk_fixtures_v6 import EA_KEY, demo_poll, desk_container, open_session
from .execute_fixtures_v6 import armed_session, execute_settings, running
from .payloads_v6 import as_poll, poll_payload

T0 = ef.RECEIVED
ACTION = ManagementAction(action_id="m3a7q2z5k6pw", command="MODIFY_POSITION", ticket=91,
                          intent_id="k7w2m4pq3xza", cycle_id="c-00000000000000bb",
                          issued_at=int(T0), sl=4361.0, tp=4378.0, tp2=4371.0,
                          sl_after_tp2=4366.0, barrier_s=10800)
PENDING_ACTION = ManagementAction(action_id="p4b3r2t6w3yz", command="MODIFY_PENDING",
                                  ticket=77, intent_id="k7w2m4pq3xza",
                                  cycle_id="c-00000000000000bb", issued_at=int(T0),
                                  price=4359.5, sl=4352.5, tp=4373.5, tp1=4364.0,
                                  tp2=4368.0, expiry_epoch=int(T0) + 1200, barrier_s=9000)
POINT = 0.01


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def clock() -> FakeClock:
    return ef.clock_at()


@pytest.fixture
def container(tmp_path: Path, clock: FakeClock) -> Iterator[V6Container]:
    with desk_container(tmp_path, clock) as built:
        yield built


# --- the board and the poll answer ----------------------------------------------------------
def test_the_board_repeats_an_action_until_settled_or_stale() -> None:
    board = ActionBoard()
    assert board.queue(ACTION) is None
    assert board.for_poll(T0 + 1) == ACTION and board.for_poll(T0 + 2) == ACTION
    assert board.settle("m3a7q2z5k6pw") and board.for_poll(T0 + 3) is None
    assert not board.settle("m3a7q2z5k6pw")
    board.queue(ACTION)
    assert board.for_poll(T0 + 31) is None and board.pop_expired(T0 + 30) is None
    assert board.pop_expired(T0 + 31) == ACTION and board.pop_expired(T0 + 32) is None


def test_a_newer_action_replaces_a_waiting_one() -> None:
    board = ActionBoard()
    board.queue(ACTION)
    assert board.queue(PENDING_ACTION) == ACTION
    assert not board.settle(ACTION.action_id) and board.for_poll(T0) == PENDING_ACTION


def test_the_poll_answer_carries_the_action() -> None:
    response = action_response(ACTION, int(T0) + 1)
    assert (response.command, response.action_ticket, response.action_sl2) == (
        "MODIFY_POSITION", 91, 4366.0)
    assert (response.action_barrier_s, response.has_intent) == (10800, False)
    pending = action_response(PENDING_ACTION, int(T0) + 1)
    assert (pending.action_price, pending.action_expiry_epoch) == (4359.5, int(T0) + 1200)


def test_an_action_that_breaks_the_wire_rules_is_refused() -> None:
    with pytest.raises(ValidationError):
        action_response(replace(ACTION, command="CLOSE_POSITION"), int(T0))
    with pytest.raises(ValidationError):
        action_response(replace(ACTION, barrier_s=0), int(T0))


# --- the publisher ----------------------------------------------------------------------------
def dispatch(op: str = "MODIFY", action: ManagementAction | None = ACTION) -> ManageDispatch:
    request = ManageRequest(target="position", ticket=91, op=op,
                            sl=4361.0 if op == "MODIFY" else None)
    return ManageDispatch(request=request, action=action, cycle_id="c-00000000000000bb",
                          agent="claude_code", session_id="")


@pytest.mark.anyio
async def test_manage_needs_an_armed_session(container: V6Container) -> None:
    demo_poll(container)
    outcome = await IntentPublisher(container.parts.desk).manage(dispatch())
    assert not outcome.sent and outcome.code == "SESSION_NOT_ARMED"
    assert container.parts.actions.for_poll(T0) is None


@pytest.mark.anyio
async def test_manage_queues_and_records(container: V6Container) -> None:
    demo_poll(container)
    session = open_session(container, armed=True)
    outcome = await IntentPublisher(container.parts.desk).manage(dispatch())
    assert outcome.sent and outcome.code == "MODIFY_POSITION"
    assert container.parts.actions.for_poll(T0) == ACTION
    row = container.ledger_cycles.actions.get("m3a7q2z5k6pw")
    assert (row.status, row.session_id, row.agent, row.payload["tp"]) == (
        "PUBLISHED", session.session_id, "claude_code", 4378.0)


@pytest.mark.anyio
async def test_a_newer_action_supersedes_the_waiting_one(container: V6Container) -> None:
    demo_poll(container)
    open_session(container, armed=True)
    publisher = IntentPublisher(container.parts.desk)
    await publisher.manage(dispatch())
    newer = replace(ACTION, action_id="n5c7s3u7x4za", sl=4362.0)
    assert (await publisher.manage(dispatch(action=newer))).sent
    old = container.ledger_cycles.actions.get(ACTION.action_id)
    assert (old.status, old.detail) == ("EXPIRED", "superseded by n5c7s3u7x4za")
    assert container.parts.actions.for_poll(T0) == newer


@pytest.mark.anyio
async def test_an_invalid_action_is_not_queued(container: V6Container) -> None:
    demo_poll(container)
    open_session(container, armed=True)
    outcome = await IntentPublisher(container.parts.desk).manage(
        dispatch(action=replace(ACTION, barrier_s=0)))
    assert (outcome.sent, outcome.code) == (False, "ACTION_INVALID")
    assert container.parts.actions.for_poll(T0) is None
    assert container.ledger_cycles.actions.get(ACTION.action_id) is None


@pytest.mark.anyio
async def test_a_cancel_queues_cancel_pending(container: V6Container,
                                              clock: FakeClock) -> None:
    demo_poll(container)
    open_session(container, armed=True)
    outcome = await IntentPublisher(container.parts.desk).manage(dispatch("CANCEL", None))
    assert outcome.sent and outcome.code == "CANCEL_PENDING"
    pending = container.parts.control.commands.current(clock.now_epoch())
    assert (pending.command, pending.reason) == ("CANCEL_PENDING", "AGENT_CANCEL")


@pytest.mark.anyio
async def test_a_stale_action_expires_on_supervise(container: V6Container) -> None:
    container.parts.actions.queue(ACTION)
    seeded(container)
    await container.parts.desk.supervise(T0 + 31, halted=False, breakers=None)
    row = container.ledger_cycles.actions.get(ACTION.action_id)
    assert row.status == "EXPIRED" and container.parts.actions.for_poll(T0 + 31) is None


# --- the poll answer --------------------------------------------------------------------------
@pytest.mark.anyio
async def test_the_poll_serves_the_signed_action(container: V6Container) -> None:
    container.parts.actions.queue(ACTION)
    facts = PollFacts(halted=False, breaker_tripped=False, point=POINT)
    answer = await container.parts.poll_replier.reply(as_poll(poll_payload()), T0 + 1, facts)
    assert (answer.command, answer.action_id, answer.action_sl) == (
        "MODIFY_POSITION", ACTION.action_id, 4361.0)
    assert wire.verify_intent(SecretStr(EA_KEY), answer, POINT)


@pytest.mark.anyio
async def test_a_command_outranks_the_action(container: V6Container) -> None:
    container.parts.actions.queue(ACTION)
    container.parts.control.commands.request_cancel_pending("sudah_cukup", T0)
    facts = PollFacts(halted=False, breaker_tripped=False, point=POINT)
    poll = as_poll({**poll_payload(), "pending_v6_orders": 1})
    answer = await container.parts.poll_replier.reply(poll, T0 + 1, facts)
    assert (answer.command, answer.action_id) == ("CANCEL_PENDING", "")


# --- EA reports ---------------------------------------------------------------------------------
def report(**changes: Any) -> ActionReport:
    fields: dict[str, Any] = dict(
        schema_version="v6.action.1", kind="APPLIED", action_id="m3a7q2z5k6pw",
        command="MODIFY_POSITION", intent_id="k7w2m4pq3xza", ticket=91, reason_code="NONE",
        retcode=10009, step=0, old_sl=4353.5, new_sl=4361.0, price=4366.2,
        sent_at_epoch=int(T0))
    return ActionReport(**{**fields, **changes})


def seeded(container: V6Container, action: ManagementAction = ACTION) -> ActionDesk:
    ledger = container.ledger_cycles
    ledger.intents.insert(NewIntent(
        intent_id="k7w2m4pq3xza", cycle_id="c-00000000000000aa", session_id="sess-1",
        agent="claude_code", source="operator", side="buy", order_type="BUY_LIMIT",
        entry=4360.5, sl=4353.5, tp=4374.5, lots=0.01, risk_usd=7.4,
        valid_until_epoch=int(T0) + 60, pending_expiry_epoch=int(T0) + 1800,
        time_barrier_s=9000, created_at=T0, tp1=4366.0, tp2=4370.0, sl_after_tp1=4361.0,
        sl_after_tp2=4365.0))
    ledger.actions.insert(ActionRow(
        action_id=action.action_id, cycle_id=action.cycle_id, session_id="sess-1",
        agent="claude_code", command=action.command, ticket=action.ticket,
        intent_id=action.intent_id, payload=action.payload(), created_at=T0,
        updated_at=T0))
    return ActionDesk(ledger)


def test_an_applied_modify_updates_the_plan(container: V6Container) -> None:
    desk = seeded(container)
    assert desk.apply(report(), T0 + 2) == "APPLIED"
    intent = container.ledger_cycles.intents.get("k7w2m4pq3xza")
    assert (intent.tp2, intent.sl_after_tp2, intent.time_barrier_s) == (4371.0, 4366.0, 10800)
    assert (intent.entry, intent.sl) == (4360.5, 4353.5)       # a position keeps its risk
    assert desk.apply(report(), T0 + 3) == "DUPLICATE"


def test_an_applied_pending_modify_moves_the_order_levels(container: V6Container) -> None:
    desk = seeded(container, PENDING_ACTION)
    applied = report(action_id=PENDING_ACTION.action_id, command="MODIFY_PENDING", ticket=77)
    assert desk.apply(applied, T0 + 2) == "APPLIED"
    intent = container.ledger_cycles.intents.get("k7w2m4pq3xza")
    assert (intent.entry, intent.sl, intent.tp, intent.tp1) == (4359.5, 4352.5, 4373.5, 4364.0)


def test_a_rejected_action_keeps_the_plan(container: V6Container) -> None:
    desk = seeded(container)
    assert desk.apply(report(kind="REJECTED", reason_code="SL_WIDER", retcode=0),
                      T0 + 2) == "REJECTED"
    assert container.ledger_cycles.intents.get("k7w2m4pq3xza").tp2 == 4370.0
    row = container.ledger_cycles.actions.get(ACTION.action_id)
    assert (row.status, row.detail) == ("REJECTED", "SL_WIDER retcode 0")


def test_a_plan_step_is_recorded(container: V6Container) -> None:
    desk = seeded(container)
    step = report(kind="PLAN_STEP", action_id="", command="NONE", step=1, retcode=10009)
    assert desk.apply(step, T0 + 5) == "STEP"
    assert container.ledger_cycles.intents.get("k7w2m4pq3xza").plan_step == 1
    assert desk.apply(step, T0 + 6) == "DUPLICATE"


def test_a_plan_step_for_an_unknown_ticket_is_still_recorded(container: V6Container) -> None:
    desk = seeded(container)
    step = report(kind="PLAN_STEP", action_id="", command="NONE", step=2, intent_id="",
                  ticket=424242)
    assert desk.apply(step, T0 + 5) == "STEP"    # recorded, with no intent to raise
    assert container.ledger_cycles.intents.get("k7w2m4pq3xza").plan_step == 0


# --- the route ------------------------------------------------------------------------------------
def test_the_route_settles_and_applies_a_signed_report(tmp_path: Path) -> None:
    clock = ef.clock_at()
    with running(tmp_path, execute_settings(tmp_path), clock) as adapter:
        armed_session(adapter)
        parts = adapter.container.parts
        action = replace(ACTION, issued_at=int(clock.now_epoch()))
        parts.actions.queue(action)
        seeded(adapter.container, action)
        body = report(sent_at_epoch=int(clock.now_epoch())).model_dump(mode="json")
        refused = adapter.ea_post("/v6/action", body, sign=False)
        assert refused.status_code == 401
        assert parts.actions.for_poll(clock.now_epoch()) == action
        accepted = adapter.ea_post("/v6/action", body)
        assert (accepted.status_code, accepted.json()) == (200, {"ok": True})
        assert parts.actions.for_poll(clock.now_epoch()) is None
        row = adapter.rows("SELECT status FROM v6_actions WHERE action_id = ?",
                           (action.action_id,))
        assert row == [("APPLIED",)]
