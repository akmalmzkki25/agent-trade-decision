"""
One operator decision checked role by role against the packet it answers.

`schemas.operator.parse_operator_decision` is all-or-nothing. Here every role
goes through `agents.validate_view` on its own, so the protocol's rule 2 holds
exactly: an invalid (or missing) Price Action view or Chief decision refuses the
submission, while an invalid or missing news, liquidity or structure view is
only flagged and the cycle uses that desk's rules view instead.

Nothing an agent writes can leave the enum space: views are re-parsed strictly
(unknown fields, NaN, oversized text and ids that were not offered are
refused), notes are bounded printable text that no rule reads, and lots are
checked against the packet's limits. An agent-designed entry is checked here
against the packet's limits, so the agent learns at once why a plan does not fit
and can resubmit before the deadline; `risk/` still sizes it and may refuse it.

Version 3 decisions (`decision_v3`) are the only answer to a management packet;
versions 1 and 2 still answer a flat packet. The shared parts live in
`decision_parts` and are re-exported here. Error details never echo submitted
text: they name known fields, pydantic error types, VIEW_ERR_* codes and
agent_entry.PROBLEM_* codes only.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TypeVar, cast

from ..config import V6Settings
from ..cycle_types import DeskViews, ViewRecord
from ..providers.base import (
    ERR_TIMEOUT, OPERATOR_PROVIDER_NAME, PROVIDER_STATUS_FAILED, ProviderResult,
)
from ..schemas.agents import ChiefDecision, PriceActionView
from ..schemas.operator import (
    DECISION_ERR_ENTRY_PLAN, DECISION_ERR_KIND, DECISION_ERR_REBUTTAL, DECISION_ERR_VIEW,
    OperatorPacket, decision_extras_problem, entry_plan_problem,
)
from ..schemas.operator_parts import AgentEntryPlan
from .agent_entry import limits_from_packet, plan_problems
from .decision_parts import (  # noqa: F401 - re-exported for the operator API and tests
    KNOWN_FIELDS, RISK_DESKS, VIEW_MISSING, DecisionEnvelope, DecisionEnvelopeV3,
    DecisionError, DecisionOutcome, DeskFlag, Envelope, RawViews, ValidatedDecision,
    checked_desks, decision_error, error_locations, packet_problem, parse_envelope,
    parse_json_value, role_checker,
)
from .decision_minute import validate_minute
from .decision_v3 import validate_v3
from .panel import CHIEF_ROLE, Baseline, PanelResult

logger = logging.getLogger(__name__)

ViewT = TypeVar("ViewT")


# --- version 1 and 2 ---------------------------------------------------------------------
def _take_ids(view: PriceActionView) -> frozenset[str]:
    return frozenset(item.candidate_id for item in view.ranked if item.verdict == "TAKE")


def _checked_v2(envelope: DecisionEnvelope, packet: OperatorPacket) -> DecisionOutcome:
    desks = checked_desks(envelope.views, packet)
    if isinstance(desks, DecisionError):
        return desks
    pa_view, views, flags = desks
    chief = role_checker(packet)(CHIEF_ROLE, envelope.chief)
    if isinstance(chief, str):
        return decision_error(DECISION_ERR_VIEW, f"{CHIEF_ROLE}: {chief}", CHIEF_ROLE)
    chief_view = cast(ChiefDecision, chief)
    stray = set(envelope.rebuttal) - _take_ids(pa_view)
    if stray:
        return decision_error(DECISION_ERR_REBUTTAL,
                              f"rebuttal names {len(stray)} candidate(s) price_action did "
                              "not TAKE")
    plan = _checked_plan(envelope, packet, chief_view)
    if isinstance(plan, DecisionError):
        return plan
    extras = decision_extras_problem(chief_view, envelope.lots, packet)
    if extras is not None:
        return decision_error(extras[0], extras[1], "decision")
    return ValidatedDecision(
        cycle_id=envelope.cycle_id, packet_hash=envelope.packet_hash, agent=envelope.agent,
        price_action=pa_view, chief=chief_view, rebuttal=dict(envelope.rebuttal),
        entry_plan=plan, lots=envelope.lots, flags=flags,
        schema_version=envelope.schema_version, **views)


def _checked_plan(envelope: DecisionEnvelope, packet: OperatorPacket,
                  chief: ChiefDecision) -> AgentEntryPlan | DecisionError | None:
    """The agent's entry plan when the Chief enters it, checked against the packet limits."""
    raw = envelope.entry_plan
    mismatch = entry_plan_problem(chief, raw, packet.limits.agent_entry_id)
    if mismatch is not None:
        return decision_error(DECISION_ERR_ENTRY_PLAN, mismatch, "entry_plan")
    if raw is None:
        return None
    plan = parse_json_value(AgentEntryPlan, raw, DECISION_ERR_ENTRY_PLAN, "entry_plan")
    if isinstance(plan, DecisionError):
        return plan
    if plan.order_type != chief.order_style:
        return decision_error(DECISION_ERR_ENTRY_PLAN,
                              f"chief.order_style {chief.order_style} must equal "
                              f"entry_plan.order_type {plan.order_type}", "entry_plan")
    problems = plan_problems(plan, limits_from_packet(packet))
    if problems:
        detail = "; ".join(f"{item.code}: {item.message}" for item in problems)
        return decision_error(DECISION_ERR_ENTRY_PLAN, detail, "entry_plan")
    return plan


def _validate_v2(packet: OperatorPacket, envelope: DecisionEnvelope, settings: V6Settings,
                 now: float) -> DecisionOutcome:
    problem = packet_problem(envelope, packet, settings, now)
    if problem is not None:
        return problem
    if packet.state != "flat":
        return decision_error(DECISION_ERR_KIND,
                              f"a {packet.state} packet needs a v6.operator.decision.3",
                              "decision")
    return _checked_v2(envelope, packet)


def validate_decision(packet: OperatorPacket, decision: bytes | Envelope,
                      settings: V6Settings, *, now: float) -> DecisionOutcome:
    """Check one submission against the packet it answers; never raises on bad input.

    Order: size and envelope, same cycle and packet hash, not expired at `now`,
    agent enabled (V6_OPERATOR_AGENTS and the packet), then the version's own rules:
    for 1/2 Price Action and Chief (a failure refuses), rebuttal only for PA TAKE ids,
    the entry plan and lots; for 3 see `decision_v3.validate_v3`. The risk desks are
    flagged, never refused. Raises TypeError only when `decision` is not bytes.
    """
    envelope = (decision if isinstance(decision, (DecisionEnvelope, DecisionEnvelopeV3))
                else parse_envelope(decision))
    if isinstance(envelope, DecisionError):
        return envelope
    if isinstance(envelope, DecisionEnvelopeV3):
        validate = validate_minute if packet.packet_kind == "m1" else validate_v3
        outcome = validate(packet, envelope, settings, now=now)
    else:
        outcome = _validate_v2(packet, envelope, settings, now)
    if isinstance(outcome, ValidatedDecision) and outcome.flags:
        logger.warning("v6 operator cycle %s: %s flagged %s; rules views used",
                       outcome.cycle_id, outcome.agent,
                       ",".join(f"{flag.role}={flag.code}" for flag in outcome.flags))
    return outcome


# --- tier 1 --------------------------------------------------------------------------------
def _either(primary: ViewT | None, fallback: ViewT | None) -> ViewT | None:
    return fallback if primary is None else primary


def panel_from_decision(decision: ValidatedDecision, baseline: Baseline) -> PanelResult:
    """Tier 1 from an accepted decision: flagged desks take their rules view, PA never does."""
    rules = baseline.views
    views = DeskViews(price_action=decision.price_action,
                      news_risk=_either(decision.news_risk, rules.news_risk),
                      liquidity=_either(decision.liquidity, rules.liquidity),
                      structure=_either(decision.structure, rules.structure))
    return PanelResult(views=views, decision=decision.chief, records=decision.records(),
                       provider=OPERATOR_PROVIDER_NAME, status=decision.status)


def timeout_panel(baseline: Baseline, *, latency_ms: int = 0) -> PanelResult:
    """No decision in time: a Chief timeout is recorded, PA stays empty, the cycle holds
    (the engine reports HoldReason.OPERATOR_TIMEOUT)."""
    failure = ProviderResult.failure(ERR_TIMEOUT, latency_ms=max(0, latency_ms))
    record = ViewRecord.from_result(CHIEF_ROLE, OPERATOR_PROVIDER_NAME, failure)
    return PanelResult(views=replace(baseline.views, price_action=None), decision=None,
                       records=(record,), provider=OPERATOR_PROVIDER_NAME,
                       status=PROVIDER_STATUS_FAILED)
