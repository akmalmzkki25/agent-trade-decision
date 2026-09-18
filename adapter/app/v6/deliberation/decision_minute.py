"""
Decision v3 against an m1 packet (spec sections 2.1 and 2.5).

An m1 packet carries no desk views of its own: its `baseline_views` are the views the
newest M15 cycle used (the accepted decision's, or that cycle's rules views). A decision
may still send its four views, which are then checked like an m15 decision's; without
them the inherited views apply, and an ENTER is the agent's Price Action TAKE of the
packet's entry id at the minimum conviction (the protocol asks exactly that). `m15_bias`
may be null.
"""

from __future__ import annotations

from typing import Final

from ..config import V6Settings
from ..schemas.agents import AgentView, PriceActionView, RankedCandidate
from ..schemas.operator import (
    ABSTAIN_VIEW, DECISION_ERR_BIAS, DECISION_SCHEMA, OperatorPacket, baseline_or_defaults,
)
from ..schemas.operator_plan import M15Bias
from .decision_parts import (
    CheckedDesks, DecisionEnvelopeV3, DecisionError, DecisionOutcome, ValidatedDecision,
    checked_desks, packet_problem, parse_json_value,
)
from .decision_v3 import action_parts, derived_chief, state_problem

MINUTE_PA_NOTE: Final[str] = "m1 ENTER"


def minute_price_action(envelope: DecisionEnvelopeV3, packet: OperatorPacket) -> PriceActionView:
    """Without views: an ENTER TAKEs the entry id at the minimum conviction, else abstain."""
    if envelope.action != "ENTER":
        return ABSTAIN_VIEW
    take = RankedCandidate(candidate_id=packet.limits.agent_entry_id, verdict="TAKE",
                           conviction=packet.allowed.pa_min_conviction, reason_codes=(),
                           note=MINUTE_PA_NOTE)
    return PriceActionView(abstain=False, ranked=(take,))


def _desks(envelope: DecisionEnvelopeV3, packet: OperatorPacket) -> CheckedDesks | DecisionError:
    """(Price Action, risk desk views, flags): the sent views, or the inherited ones."""
    if envelope.views is not None:
        return checked_desks(envelope.views, packet)
    inherited = baseline_or_defaults(packet)
    views: dict[str, AgentView | None] = {
        "news_risk": inherited.news_risk, "liquidity": inherited.liquidity,
        "structure": inherited.structure}
    return minute_price_action(envelope, packet), views, ()


def _bias(envelope: DecisionEnvelopeV3) -> M15Bias | DecisionError | None:
    if envelope.m15_bias is None:
        return None
    return parse_json_value(M15Bias, envelope.m15_bias, DECISION_ERR_BIAS, "m15_bias")


def validate_minute(packet: OperatorPacket, envelope: DecisionEnvelopeV3, settings: V6Settings,
                    *, now: float) -> DecisionOutcome:
    """Check a v3 decision for an m1 packet: the packet, the action for its state, the
    views (sent or inherited), the optional bias, then the plan or the manage request."""
    problem = (packet_problem(envelope, packet, settings, now)
               or state_problem(envelope, packet))
    if problem is not None:
        return problem
    desks = _desks(envelope, packet)
    if isinstance(desks, DecisionError):
        return desks
    pa_view, views, flags = desks
    bias = _bias(envelope)
    if isinstance(bias, DecisionError):
        return bias
    parts = action_parts(envelope, packet, pa_view)
    if isinstance(parts, DecisionError):
        return parts
    plan, manage = parts
    return ValidatedDecision(
        cycle_id=envelope.cycle_id, packet_hash=envelope.packet_hash, agent=envelope.agent,
        price_action=pa_view, chief=derived_chief(plan, packet, pa_view, envelope.note),
        flags=flags, schema_version=DECISION_SCHEMA, action=envelope.action, plan=plan,
        manage=manage, bias=bias, note=envelope.note, **views)
