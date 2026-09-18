"""
Decision v3 against the packet it answers (spec section 2).

Checked role by role like v2: the Price Action view refuses, a risk desk view is only
flagged. The v3 action replaces the v2 Chief, which is derived here so that the protocol,
the exit plan, the sizer and the intent builder keep their authority. An ENTER therefore
needs the agent's own Price Action view to TAKE the packet's agent entry id with the
minimum conviction, exactly what the protocol will ask. Error details name fields,
pydantic error types and rule codes only; submitted text is never echoed.
"""

from __future__ import annotations

from typing import Final

from ..config import V6Settings
from ..schemas.agents import ChiefDecision, PriceActionView
from ..schemas.operator import (
    DECISION_ERR_BIAS, DECISION_ERR_ENTRY_PLAN, DECISION_ERR_KIND, DECISION_ERR_LOTS,
    DECISION_ERR_MANAGE, DECISION_ERR_VIEW, DECISION_SCHEMA, HOLD_DECISION, OperatorPacket,
)
from ..schemas.operator_plan import EntryPlanV2, M15Bias, ManageRequest
from .decision_parts import (
    PRICE_ACTION_ROLE, DecisionEnvelopeV3, DecisionError, DecisionOutcome, ValidatedDecision,
    checked_desks, decision_error, packet_problem, parse_json_value,
)
from .management import manage_problems
from .plan_rules import bounds_from_packet, lots_problem, plan_problems_v2

MAX_NOTE_CHARS: Final[int] = 300


def state_problem(envelope: DecisionEnvelopeV3,
                   packet: OperatorPacket) -> DecisionError | None:
    """A flat packet takes HOLD or ENTER; a pending or position packet takes MANAGE."""
    if envelope.packet_kind != packet.packet_kind:
        return decision_error(DECISION_ERR_KIND, "the decision answers another packet kind")
    if packet.state == "flat":
        if envelope.action == "MANAGE" or envelope.manage is not None:
            return decision_error(DECISION_ERR_MANAGE, "a flat packet takes HOLD or ENTER",
                                  "manage")
        if envelope.action == "HOLD" and envelope.entry_plan is not None:
            return decision_error(DECISION_ERR_ENTRY_PLAN, "a HOLD carries no entry_plan",
                                  "entry_plan")
        return None
    if envelope.entry_plan is not None or envelope.action == "ENTER":
        return decision_error(DECISION_ERR_ENTRY_PLAN,
                              f"a {packet.state} packet takes no entry", "entry_plan")
    if envelope.action != "MANAGE" or envelope.manage is None:
        return decision_error(DECISION_ERR_MANAGE,
                              f"a {packet.state} packet needs action MANAGE and manage",
                              "manage")
    return None


def _plan(envelope: DecisionEnvelopeV3, packet: OperatorPacket) -> EntryPlanV2 | DecisionError:
    plan = parse_json_value(EntryPlanV2, envelope.entry_plan, DECISION_ERR_ENTRY_PLAN,
                            "entry_plan")
    if isinstance(plan, DecisionError):
        return plan
    bounds = bounds_from_packet(packet)
    problems = plan_problems_v2(plan, bounds)
    if problems:
        detail = "; ".join(f"{item.code}: {item.message}" for item in problems)
        return decision_error(DECISION_ERR_ENTRY_PLAN, detail, "entry_plan")
    lots = lots_problem(plan.lots, bounds)
    return plan if lots is None else decision_error(DECISION_ERR_LOTS, lots, "entry_plan")


def _manage(envelope: DecisionEnvelopeV3,
            packet: OperatorPacket) -> ManageRequest | DecisionError:
    request = parse_json_value(ManageRequest, envelope.manage, DECISION_ERR_MANAGE, "manage")
    if isinstance(request, DecisionError):
        return request
    problems = manage_problems(request, packet)
    if problems:
        return decision_error(DECISION_ERR_MANAGE, "; ".join(problems), "manage")
    return request


def _conviction(price_action: PriceActionView, candidate_id: str) -> float | None:
    """The conviction of a TAKE for `candidate_id`, or None when PA does not take it."""
    return next((item.conviction for item in price_action.ranked
                 if item.candidate_id == candidate_id and item.verdict == "TAKE"), None)


def _take_problem(price_action: PriceActionView, packet: OperatorPacket) -> DecisionError | None:
    entry_id = packet.limits.agent_entry_id
    minimum = packet.allowed.pa_min_conviction
    conviction = _conviction(price_action, entry_id)
    if conviction is None or conviction < minimum:
        return decision_error(DECISION_ERR_VIEW,
                              f"an ENTER needs price_action to TAKE {entry_id} with conviction "
                              f">= {minimum}", PRICE_ACTION_ROLE)
    return None


def derived_chief(plan: EntryPlanV2 | None, packet: OperatorPacket, price_action: PriceActionView,
           note: str) -> ChiefDecision:
    """The Chief the protocol reads: ENTER the agent entry id, or HOLD."""
    rationale = note[:MAX_NOTE_CHARS]
    if plan is None:
        return HOLD_DECISION.model_copy(update={"rationale": rationale})
    entry_id = packet.limits.agent_entry_id
    return ChiefDecision(
        action="ENTER", candidate_id=entry_id, risk_tier="standard",
        order_style="MARKET" if plan.order_type == "MARKET" else "LIMIT",
        exit_profile="STANDARD", confidence=_conviction(price_action, entry_id) or 0.0,
        rationale=rationale, dissent="")


def action_parts(envelope: DecisionEnvelopeV3, packet: OperatorPacket,
                  price_action: PriceActionView
                  ) -> tuple[EntryPlanV2 | None, ManageRequest | None] | DecisionError:
    if envelope.action == "ENTER":
        plan = _plan(envelope, packet)
        if isinstance(plan, DecisionError):
            return plan
        return _take_problem(price_action, packet) or (plan, None)
    if envelope.action == "MANAGE":
        manage = _manage(envelope, packet)
        return manage if isinstance(manage, DecisionError) else (None, manage)
    return None, None


def validate_v3(packet: OperatorPacket, envelope: DecisionEnvelopeV3, settings: V6Settings,
                *, now: float) -> DecisionOutcome:
    """Check a v3 decision: the packet, the action for the packet's state, the views,
    the M15 bias, then the plan (ENTER) or the manage request (MANAGE)."""
    problem = (packet_problem(envelope, packet, settings, now)
               or state_problem(envelope, packet))
    if problem is not None:
        return problem
    if envelope.views is None:
        return decision_error(DECISION_ERR_VIEW, "an m15 packet needs the four views", "views")
    desks = checked_desks(envelope.views, packet)
    if isinstance(desks, DecisionError):
        return desks
    pa_view, views, flags = desks
    bias = parse_json_value(M15Bias, envelope.m15_bias, DECISION_ERR_BIAS, "m15_bias")
    if isinstance(bias, DecisionError):
        return bias
    parts = action_parts(envelope, packet, pa_view)
    if isinstance(parts, DecisionError):
        return parts
    plan, manage = parts
    return ValidatedDecision(
        cycle_id=envelope.cycle_id, packet_hash=envelope.packet_hash, agent=envelope.agent,
        price_action=pa_view, chief=derived_chief(plan, packet, pa_view, envelope.note), flags=flags,
        schema_version=DECISION_SCHEMA, action=envelope.action, plan=plan, manage=manage,
        bias=bias, note=envelope.note, **views)
