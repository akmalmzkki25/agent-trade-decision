"""
Managing the resting order or the open position of a packet (spec section 2.3).

`manage_problems` judges a ManageRequest against the packet it answers, with the prices
the agent saw; `build_action` turns an accepted CLOSE or MODIFY into the full
ManagementAction the EA receives. The EA checks the same rules again against its live
quote, so a packet that went stale can at worst be refused there.

A position MODIFY keeps the remaining ladder consistent: the stop, the targets not yet
reached and the take profit advance in the trade direction, and each SL+ step not yet
executed sits between the stop before it and its trigger minus the modify distance.
Executed steps are history: they can no longer change and no longer constrain the stop.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from ..schemas.intent import new_intent_id
from ..schemas.operator import OperatorPacket
from ..schemas.operator_plan import (
    OPS_BY_TARGET, PENDING_ONLY_FIELDS, EntryPlanV2, ManageRequest, PacketPendingOrder,
    PacketPosition,
)
from .plan_rules import bounds_from_packet, plan_problems_v2
from .publication import ManagementAction

MANAGE_SHAPE: Final[str] = "MANAGE_SHAPE"
MANAGE_TICKET: Final[str] = "MANAGE_TICKET"
SL_WIDER: Final[str] = "SL_WIDER"
TOO_CLOSE: Final[str] = "TOO_CLOSE"
REWARD_TOO_LARGE: Final[str] = "REWARD_TOO_LARGE"
STEP_DONE: Final[str] = "STEP_DONE"
LADDER_ORDER: Final[str] = "LADDER_ORDER"
SL_STEP_INVALID: Final[str] = "SL_STEP_INVALID"
TIME_LIMIT_RANGE: Final[str] = "TIME_LIMIT_RANGE"
MANAGE_SIZE: Final[str] = "MANAGE_SIZE"
LADDER_MISSING: Final[str] = "LADDER_MISSING"
MIN_EXTENSION_MIN: Final[int] = 5
SECONDS_PER_MINUTE: Final[int] = 60
PRICE_EPSILON: Final[float] = 1e-9
PLAN_TYPES: Final[dict[str, str]] = {"BUY_LIMIT": "LIMIT", "SELL_LIMIT": "LIMIT",
                                     "BUY_STOP": "STOP", "SELL_STOP": "STOP"}


def _problem(code: str, message: str) -> str:
    return f"{code}: {message}"


def _pick(new: float | int | None, old: float | int) -> float | int:
    return old if new is None else new


@dataclass(frozen=True)
class Levels:
    """A position's levels after the request (0 = no level)."""

    sl: float
    tp3: float
    tp1: float
    tp2: float
    s1: float
    s2: float


def _position_levels(request: ManageRequest, position: PacketPosition) -> Levels:
    plan = position.plan
    return Levels(sl=_pick(request.sl, position.sl), tp3=_pick(request.tp3, position.tp),
                  tp1=_pick(request.tp1, plan.tp1), tp2=_pick(request.tp2, plan.tp2),
                  s1=_pick(request.sl_after_tp1, plan.sl_after_tp1),
                  s2=_pick(request.sl_after_tp2, plan.sl_after_tp2))


def _ticket(packet: OperatorPacket) -> int:
    if packet.position is not None:
        return packet.position.ticket
    return 0 if packet.pending_order is None else packet.pending_order.ticket


def _shape_problems(request: ManageRequest, packet: OperatorPacket) -> list[str]:
    state = packet.state
    if state == "flat" or request.target != state:
        return [_problem(MANAGE_SHAPE, f"this packet manages the {state}")]
    changes = request.changes
    checks = (
        (request.ticket == _ticket(packet), MANAGE_TICKET,
         f"the packet's ticket is {_ticket(packet)}"),
        (request.op in OPS_BY_TARGET[state], MANAGE_SHAPE,
         f"{request.op} is not an op for a {state}"),
        (request.op != "MODIFY" or bool(changes), MANAGE_SHAPE,
         "MODIFY needs at least one field"),
        (request.op == "MODIFY" or not changes, MANAGE_SHAPE,
         f"{request.op} takes no fields"),
        (state == "pending" or not set(changes) & set(PENDING_ONLY_FIELDS), MANAGE_SHAPE,
         "entry and pending_expiry_min belong to a pending order"),
    )
    return [_problem(code, message) for ok, code, message in checks if not ok]


def _sl_tp_problems(request: ManageRequest, packet: OperatorPacket, sign: int,
                    price: float) -> list[str]:
    position, limits = packet.position, packet.limits
    d = limits.modify_distance
    problems = []
    if request.sl is not None:
        if sign * (request.sl - position.sl) < -PRICE_EPSILON:
            problems.append(_problem(SL_WIDER, "the stop may only move toward safety"))
        if sign * (price - request.sl) + PRICE_EPSILON < d:
            problems.append(_problem(TOO_CLOSE, f"the stop must stay {d} from the price"))
    if request.tp3 is not None:
        risk = abs(position.open_price - position.initial_sl)
        if sign * (request.tp3 - price) + PRICE_EPSILON < d:
            problems.append(_problem(TOO_CLOSE, f"tp3 must stay {d} beyond the price"))
        if sign * (request.tp3 - position.open_price) > limits.max_reward_r * risk + PRICE_EPSILON:
            problems.append(_problem(REWARD_TOO_LARGE,
                                     f"tp3 is beyond {limits.max_reward_r}R of the initial risk"))
    return problems


def _advancing(sign: int, levels: list[float]) -> bool:
    return all(sign * (b - a) > 0 for a, b in zip(levels, levels[1:]))


def _ladder_problems(position: PacketPosition, new: Levels, sign: int, d: float) -> list[str]:
    """The remaining ladder after the request (executed steps are left out)."""
    step = position.plan.step
    if new.tp3 <= 0:
        return [_problem(LADDER_MISSING, "this position has no take profit: give tp3")]
    levels = ([new.sl] + ([new.tp1] if step < 1 and new.tp1 > 0 else [])
              + ([new.tp2] if step < 2 and new.tp2 > 0 else []) + [new.tp3])
    problems = [] if _advancing(sign, levels) else [
        _problem(LADDER_ORDER, "sl, tp1, tp2 and tp3 must advance in the trade direction")]
    first = step < 1 and new.s1 > 0
    if first and not (sign * (new.s1 - new.sl) > 0
                      and sign * (new.tp1 - new.s1) + PRICE_EPSILON >= d):
        problems.append(_problem(SL_STEP_INVALID,
                                 f"sl_after_tp1 must sit between sl and tp1 - {d}"))
    floor = new.s1 if first else new.sl
    if step < 2 and new.s2 > 0 and not (sign * (new.s2 - floor) >= 0
                                        and sign * (new.s2 - new.sl) > 0
                                        and sign * (new.tp2 - new.s2) + PRICE_EPSILON >= d):
        problems.append(_problem(SL_STEP_INVALID, "sl_after_tp2 must sit between the stop "
                                                  f"before it and tp2 - {d}"))
    return problems


def _step_done(request: ManageRequest, step: int) -> list[str]:
    first = request.tp1 is not None or request.sl_after_tp1 is not None
    second = request.tp2 is not None or request.sl_after_tp2 is not None
    if (first and step >= 1) or (second and step >= 2):
        return [_problem(STEP_DONE, "an executed step cannot change")]
    return []


def _position_problems(request: ManageRequest, packet: OperatorPacket) -> list[str]:
    position, limits = packet.position, packet.limits
    sign = 1 if position.side == "buy" else -1
    price = packet.market.bid if sign > 0 else packet.market.ask
    problems = _sl_tp_problems(request, packet, sign, price)
    problems += _step_done(request, position.plan.step) or _ladder_problems(
        position, _position_levels(request, position), sign, limits.modify_distance)
    if request.time_limit_min is not None:
        low = math.ceil(position.minutes_open) + MIN_EXTENSION_MIN
        high = limits.time_limit_max_minutes
        if not low <= request.time_limit_min <= high:
            problems.append(_problem(TIME_LIMIT_RANGE, f"time_limit_min must be {low}-{high}"))
    return problems


def _missing_levels(request: ManageRequest, order: PacketPendingOrder) -> list[str]:
    plan = order.plan
    has_ladder = plan is not None and plan.tp1 > 0 and plan.tp2 > 0
    if not has_ladder and (request.tp1 is None or request.tp2 is None):
        return [_problem(LADDER_MISSING, "this order has no ladder: give tp1 and tp2")]
    if request.tp3 is None and order.tp <= 0:
        return [_problem(LADDER_MISSING, "this order has no take profit: give tp3")]
    if request.sl is None and order.sl <= 0:
        return [_problem(LADDER_MISSING, "this order has no stop: give sl")]
    return []


def _pending_plan(request: ManageRequest, order: PacketPendingOrder,
                  packet: OperatorPacket) -> EntryPlanV2:
    """The order's plan after the request (checked like a new plan)."""
    plan = order.plan
    side = "buy" if order.order_type.startswith("BUY") else "sell"
    return EntryPlanV2(
        side=side, order_type=PLAN_TYPES[order.order_type],
        entry=_pick(request.entry, order.price), sl=_pick(request.sl, order.sl),
        tp1=_pick(request.tp1, plan.tp1 if plan else 0.0),
        tp2=_pick(request.tp2, plan.tp2 if plan else 0.0),
        tp3=_pick(request.tp3, order.tp),
        sl_after_tp1=_pick(request.sl_after_tp1, plan.sl_after_tp1 if plan else 0.0) or None,
        sl_after_tp2=_pick(request.sl_after_tp2, plan.sl_after_tp2 if plan else 0.0) or None,
        time_limit_min=_pick(request.time_limit_min,
                             plan.time_limit_min if plan and plan.time_limit_min
                             else packet.limits.time_limit_min_minutes),
        pending_expiry_min=_pick(request.pending_expiry_min,
                                 packet.limits.pending_expiry_min_minutes),
        lots=order.lots)


def _pending_problems(request: ManageRequest, packet: OperatorPacket) -> list[str]:
    order = packet.pending_order
    missing = _missing_levels(request, order)
    if missing:
        return missing
    merged = _pending_plan(request, order, packet)
    problems = [_problem(item.code, item.message)
                for item in plan_problems_v2(merged, bounds_from_packet(packet))]
    wider = abs(merged.entry - merged.sl) > abs(order.price - order.sl) + PRICE_EPSILON
    if wider and order.lots > packet.limits.volume_min + PRICE_EPSILON:
        problems.append(_problem(MANAGE_SIZE, "a wider stop needs the minimum lot; "
                                              "cancel and enter again instead"))
    return problems


def manage_problems(request: ManageRequest, packet: OperatorPacket) -> tuple[str, ...]:
    """Every reason the request does not fit the packet (empty when it does).

    Each entry reads "CODE: message" and never echoes submitted text.
    """
    shape = _shape_problems(request, packet)
    if shape or request.op != "MODIFY":
        return tuple(shape)
    if packet.state == "position":
        return tuple(_position_problems(request, packet))
    return tuple(_pending_problems(request, packet))


def _snap(value: float, packet: OperatorPacket) -> float:
    tick, digits = packet.limits.tick_size, packet.limits.digits
    return round(round(value / tick) * tick, digits) if value > 0 else 0.0


def _position_barrier_s(request: ManageRequest, packet: OperatorPacket) -> int:
    """The holding time from the open; unchanged unless the request sets one."""
    if request.time_limit_min is not None:
        return request.time_limit_min * SECONDS_PER_MINUTE
    position = packet.position
    current = position.time_limit_epoch - position.open_epoch
    return current if current > 0 else packet.limits.time_barrier_s


def _position_action(request: ManageRequest, packet: OperatorPacket,
                     base: dict[str, object]) -> ManagementAction:
    position = packet.position
    if request.op == "CLOSE":
        return ManagementAction(command="CLOSE_POSITION", ticket=position.ticket,
                                intent_id=position.intent_id, **base)
    new = _position_levels(request, position)
    return ManagementAction(
        command="MODIFY_POSITION", ticket=position.ticket, intent_id=position.intent_id,
        sl=_snap(new.sl, packet), tp=_snap(new.tp3, packet), tp1=_snap(new.tp1, packet),
        tp2=_snap(new.tp2, packet), sl_after_tp1=_snap(new.s1, packet),
        sl_after_tp2=_snap(new.s2, packet), barrier_s=_position_barrier_s(request, packet),
        **base)


def _pending_action(request: ManageRequest, packet: OperatorPacket,
                    base: dict[str, object]) -> ManagementAction:
    order = packet.pending_order
    merged = _pending_plan(request, order, packet)
    expiry = (order.expiration_epoch if request.pending_expiry_min is None
              else packet.bar_close_epoch + request.pending_expiry_min * SECONDS_PER_MINUTE)
    return ManagementAction(
        command="MODIFY_PENDING", ticket=order.ticket, intent_id=order.intent_id,
        price=_snap(merged.entry, packet), sl=_snap(merged.sl, packet),
        tp=_snap(merged.tp3, packet), tp1=_snap(merged.tp1, packet),
        tp2=_snap(merged.tp2, packet), sl_after_tp1=_snap(merged.sl_after_tp1 or 0.0, packet),
        sl_after_tp2=_snap(merged.sl_after_tp2 or 0.0, packet), expiry_epoch=expiry,
        barrier_s=merged.time_limit_min * SECONDS_PER_MINUTE, **base)


def build_action(request: ManageRequest, packet: OperatorPacket, *, now: float,
                 new_id: Callable[[], str] = new_intent_id) -> ManagementAction | None:
    """The EA command for an accepted CLOSE or MODIFY; None for KEEP and CANCEL.

    Call it only after `manage_problems` returned nothing for the same packet.
    """
    if request.op in ("KEEP", "CANCEL"):
        return None
    base: dict[str, object] = dict(action_id=new_id(), cycle_id=packet.cycle_id,
                                   issued_at=int(now))
    if packet.state == "position":
        return _position_action(request, packet, base)
    if packet.state == "pending":
        return _pending_action(request, packet, base)
    raise ValueError("a flat packet has nothing to manage")
