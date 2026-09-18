"""Managing a resting order or an open position."""

from __future__ import annotations

from typing import Any

import pytest

from app.v6.deliberation.management import build_action, manage_problems
from app.v6.schemas.operator_plan import ManageRequest

from . import operator_fixtures_v6 as of

NOW = float(of.CREATED + 10)
ACTION_ID = "m3a7q2z5k6pw"


def request(target: str = "position", ticket: int = 91, op: str = "MODIFY",
            **fields: Any) -> ManageRequest:
    return ManageRequest.model_validate({"target": target, "ticket": ticket, "op": op,
                                         **fields})


def codes(found: tuple[str, ...]) -> list[str]:
    return [text.split(":", 1)[0] for text in found]


def new_id() -> str:
    return ACTION_ID


POSITION = of.managed_packet("position")   # buy 4533.35, SL 4526.35, bid 4535.18
PENDING = of.managed_packet("pending")     # BUY_LIMIT 4531.35, SL 4524.35


def moved_market(bid: float) -> dict[str, Any]:
    return {**of.body()["market"], "bid": bid, "ask": round(bid + 0.17, 2)}


@pytest.mark.parametrize("fields", [
    {"sl": 4530.0}, {"sl": 4526.35}, {"tp3": 4545.0}, {"time_limit_min": 200},
    {"tp1": 4538.0, "sl_after_tp1": 4534.0}, {"sl_after_tp2": 4538.0},
])
def test_valid_position_changes(fields: dict[str, Any]) -> None:
    assert manage_problems(request(**fields), POSITION) == ()


@pytest.mark.parametrize(("fields", "code"), [
    ({"sl": 4525.0}, "SL_WIDER"),
    ({"sl": 4535.0}, "TOO_CLOSE"),
    ({"tp3": 4535.3}, "TOO_CLOSE"),
    ({"tp3": 4570.0}, "REWARD_TOO_LARGE"),
    ({"tp3": 4541.0}, "LADDER_ORDER"),
    ({"time_limit_min": 12}, "TIME_LIMIT_RANGE"),
    ({"tp1": 4544.0}, "LADDER_ORDER"),
    ({"sl_after_tp1": 4538.8}, "SL_STEP_INVALID"),
    ({"sl_after_tp2": 4534.0}, "SL_STEP_INVALID"),
    ({"entry": 4530.0}, "MANAGE_SHAPE"),
])
def test_refused_position_changes(fields: dict[str, Any], code: str) -> None:
    assert code in codes(manage_problems(request(**fields), POSITION))


def test_the_time_limit_stays_within_the_packet_window() -> None:
    short = of.managed_packet("position", limits=of.limits_block(
        agent_entry_possible=False, time_limit_max_minutes=180))
    assert "TIME_LIMIT_RANGE" in codes(manage_problems(request(time_limit_min=200), short))


def test_an_executed_step_cannot_change() -> None:
    stepped = of.managed_packet("position", position=of.position_block(
        plan=of.plan_block(step=1), sl=4534.0))
    assert "STEP_DONE" in codes(manage_problems(request(tp1=4538.0), stepped))
    assert manage_problems(request(tp2=4544.0), stepped) == ()


def test_executed_steps_do_not_hold_back_a_trailing_stop() -> None:
    beyond = of.managed_packet("position", market=moved_market(4546.0),
                               position=of.position_block(plan=of.plan_block(step=2),
                                                          sl=4539.0))
    assert manage_problems(request(sl=4541.0), beyond) == ()
    assert "STEP_DONE" in codes(manage_problems(request(sl_after_tp2=4540.0), beyond))


def test_a_trailing_stop_past_a_pending_step_needs_the_step_moved() -> None:
    ahead = of.managed_packet("position", market=moved_market(4538.5))
    assert "SL_STEP_INVALID" in codes(manage_problems(request(sl=4536.0), ahead))
    assert manage_problems(request(sl=4536.0, sl_after_tp1=4537.0), ahead) == ()


def test_a_second_step_without_a_first() -> None:
    single = of.managed_packet("position", position=of.position_block(
        plan=of.plan_block(sl_after_tp1=0.0)))
    assert manage_problems(request(sl_after_tp2=4538.0), single) == ()


def test_a_position_without_a_take_profit_needs_one() -> None:
    bare = of.managed_packet("position", position=of.position_block(tp=0.0))
    assert "LADDER_MISSING" in codes(manage_problems(request(sl=4530.0), bare))
    assert manage_problems(request(sl=4530.0, tp3=4549.0), bare) == ()


SELL = of.position_block(side="sell", open_price=4537.0, sl=4544.0, tp=4523.0,
                         initial_sl=4544.0,
                         plan=of.plan_block(tp1=4533.0, tp2=4529.0, sl_after_tp1=4537.5,
                                            sl_after_tp2=4533.0))


@pytest.mark.parametrize(("fields", "code"), [
    ({"sl": 4540.0}, None),
    ({"tp3": 4520.0}, None),
    ({"sl": 4545.0}, "SL_WIDER"),
    ({"sl": 4535.5}, "TOO_CLOSE"),
    ({"tp3": 4500.0}, "REWARD_TOO_LARGE"),
    ({"tp1": 4527.0}, "LADDER_ORDER"),
    ({"sl_after_tp1": 4533.2}, "SL_STEP_INVALID"),
])
def test_a_sell_position_mirrors_the_rules(fields: dict[str, Any], code: str | None) -> None:
    found = manage_problems(request(**fields), of.managed_packet("position", position=SELL))
    assert (found == ()) if code is None else (code in codes(found))


@pytest.mark.parametrize(("document", "code"), [
    ({"target": "pending", "ticket": 91, "op": "KEEP"}, "MANAGE_SHAPE"),
    ({"target": "position", "ticket": 92, "op": "KEEP"}, "MANAGE_TICKET"),
    ({"target": "position", "ticket": 91, "op": "CANCEL"}, "MANAGE_SHAPE"),
    ({"target": "position", "ticket": 91, "op": "MODIFY"}, "MANAGE_SHAPE"),
    ({"target": "position", "ticket": 91, "op": "CLOSE", "sl": 4530.0}, "MANAGE_SHAPE"),
])
def test_shape(document: dict[str, Any], code: str) -> None:
    found = manage_problems(ManageRequest.model_validate(document), POSITION)
    assert code in codes(found)


def test_a_flat_packet_has_nothing_to_manage() -> None:
    flat = of.packet()
    assert codes(manage_problems(request(op="KEEP"), flat)) == ["MANAGE_SHAPE"]
    with pytest.raises(ValueError):
        build_action(request(op="CLOSE"), flat, now=NOW, new_id=new_id)


def test_pending_changes() -> None:
    fine = request("pending", 77, entry=4530.35, sl=4523.85, tp1=4534.5, tp2=4540.0,
                   tp3=4546.0, sl_after_tp1=4531.0, sl_after_tp2=4534.5,
                   pending_expiry_min=20)
    assert manage_problems(fine, PENDING) == ()
    passive = request("pending", 77, entry=4536.0)
    assert "LIMIT_NOT_PASSIVE" in codes(manage_problems(passive, PENDING))
    assert manage_problems(request("pending", 77, op="CANCEL"), PENDING) == ()


def test_a_wider_stop_on_a_bigger_order_is_refused() -> None:
    bigger = of.managed_packet("pending", pending_order=of.pending_block(lots=0.02))
    wider = request("pending", 77, sl=4523.35)
    assert "MANAGE_SIZE" in codes(manage_problems(wider, bigger))
    assert "MANAGE_SIZE" not in codes(manage_problems(wider, PENDING))


@pytest.mark.parametrize(("changes", "fields", "message"), [
    ({"plan": None}, {"sl": 4525.0}, "ladder"),
    ({"tp": 0.0}, {"sl": 4525.0}, "take profit"),
    ({"sl": 0.0}, {"tp3": 4546.0}, "stop"),
])
def test_a_pending_order_missing_levels_needs_them(changes: dict[str, Any],
                                                   fields: dict[str, Any], message: str) -> None:
    bare = of.managed_packet("pending", pending_order=of.pending_block(**changes))
    found = manage_problems(request("pending", 77, **fields), bare)
    assert codes(found) == ["LADDER_MISSING"] and message in found[0]


def test_a_ladder_can_be_given_to_a_bare_order() -> None:
    bare = of.managed_packet("pending", pending_order=of.pending_block(plan=None))
    ladder = request("pending", 77, tp1=4535.5, tp2=4541.0)
    assert manage_problems(ladder, bare) == ()
    action = build_action(ladder, bare, now=NOW, new_id=new_id)
    assert (action.tp1, action.sl_after_tp1, action.barrier_s) == (4535.5, 0.0, 3600)


def test_actions() -> None:
    assert build_action(request(op="KEEP"), POSITION, now=NOW, new_id=new_id) is None
    close = build_action(request(op="CLOSE"), POSITION, now=NOW, new_id=new_id)
    assert (close.command, close.ticket, close.intent_id, close.sl) == (
        "CLOSE_POSITION", 91, "k7w2m4pq3xza", 0.0)
    modify = build_action(request(sl=4530.004, time_limit_min=200), POSITION, now=NOW,
                          new_id=new_id)
    assert (modify.command, modify.sl, modify.tp, modify.tp1, modify.barrier_s) == (
        "MODIFY_POSITION", 4530.0, 4549.0, 4539.0, 12000)
    assert modify.issued_at == int(NOW) and modify.cycle_id == POSITION.cycle_id
    assert modify.action_id == ACTION_ID
    pending = build_action(request("pending", 77, pending_expiry_min=20), PENDING, now=NOW,
                           new_id=new_id)
    assert (pending.command, pending.price, pending.sl, pending.tp) == (
        "MODIFY_PENDING", 4531.35, 4524.35, 4547.0)
    assert pending.expiry_epoch == of.BAR_CLOSE + 1200
    assert pending.payload()["price"] == 4531.35
    kept = build_action(request("pending", 77, sl=4525.0), PENDING, now=NOW, new_id=new_id)
    assert kept.expiry_epoch == PENDING.pending_order.expiration_epoch
    assert build_action(request("pending", 77, op="CANCEL"), PENDING, now=NOW,
                        new_id=new_id) is None


def test_a_position_modify_keeps_the_running_time_limit() -> None:
    modify = build_action(request(sl=4530.0), POSITION, now=NOW, new_id=new_id)
    assert modify.barrier_s == 9000     # time_limit_epoch - open_epoch
    unknown = of.managed_packet("position", position=of.position_block(
        time_limit_epoch=of.BAR_CLOSE - 600))
    fallback = build_action(request(sl=4530.0), unknown, now=NOW, new_id=new_id)
    assert fallback.barrier_s == unknown.limits.time_barrier_s
