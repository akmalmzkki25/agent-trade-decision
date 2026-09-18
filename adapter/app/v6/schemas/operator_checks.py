"""
The all-or-nothing checks of a version 1 or 2 operator decision (`schemas.operator`).

`parse_operator_decision` applies the same semantic checks as `agents.validate_view`
and refuses the whole submission on the first problem; `deliberation.operator_decision`
checks role by role instead. Error details never echo free text from the submission.
"""

from __future__ import annotations

import hmac
from typing import Final

from pydantic import ValidationError

from .agents import DESK_ROLES, ChiefDecision, ViewValidationError, validate_view
from .operator import (
    DECISION_ERR_AGENT, DECISION_ERR_ENTRY_PLAN, DECISION_ERR_EXPIRED, DECISION_ERR_KIND,
    DECISION_ERR_LOTS, DECISION_ERR_NOT_JSON, DECISION_ERR_REBUTTAL, DECISION_ERR_SCHEMA, DECISION_ERR_STALE,
    DECISION_ERR_TOO_LARGE, DECISION_ERR_VIEW, MAX_DECISION_BYTES, OperatorDecision,
    OperatorPacket, OperatorPacketBody,
)

MAX_ERROR_DETAIL_CHARS: Final[int] = 300
LOTS_EPSILON: Final[float] = 1e-9


class OperatorDecisionError(ValueError):
    """An operator decision was refused; `code` is a DECISION_ERR_* value.

    The detail never echoes free text from the submission.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail[:MAX_ERROR_DETAIL_CHARS]}")
        self.code = code
        self.detail = detail[:MAX_ERROR_DETAIL_CHARS]


def _parse_decision(raw: bytes) -> OperatorDecision:
    if not isinstance(raw, (bytes, bytearray)):
        raise TypeError("the decision body must be bytes")
    if len(raw) > MAX_DECISION_BYTES:
        raise OperatorDecisionError(DECISION_ERR_TOO_LARGE,
                                    f"{len(raw)} bytes exceeds {MAX_DECISION_BYTES}")
    try:
        return OperatorDecision.model_validate_json(bytes(raw))
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        code = (DECISION_ERR_NOT_JSON if {err["type"] for err in errors} == {"json_invalid"}
                else DECISION_ERR_SCHEMA)
        locations = ", ".join(".".join(map(str, err["loc"])) for err in errors[:5])
        raise OperatorDecisionError(code, f"invalid at: {locations}") from None


def _check_views(decision: OperatorDecision, packet: OperatorPacket) -> None:
    offered = frozenset(packet.allowed.candidate_ids)
    events = frozenset(packet.allowed.event_ids)
    views = {role: getattr(decision.views, role) for role in DESK_ROLES}
    for role, view in {**views, "chief": decision.chief}.items():
        try:
            validate_view(role, view.model_dump(mode="json"), offered, events)
        except ViewValidationError as exc:
            raise OperatorDecisionError(DECISION_ERR_VIEW, f"{role}: {exc.code}") from None


def _check_rebuttal(decision: OperatorDecision) -> None:
    taken = {item.candidate_id for item in decision.views.price_action.ranked
             if item.verdict == "TAKE"}
    stray = sorted(set(decision.rebuttal) - taken)
    if stray:
        raise OperatorDecisionError(DECISION_ERR_REBUTTAL,
                                    f"rebuttal names candidates PA did not TAKE: {stray}")


def entry_plan_problem(chief: ChiefDecision, plan: object, agent_entry_id: str) -> str | None:
    """Why the entry plan does not match the Chief's pick, or None."""
    enters_agent = chief.action == "ENTER" and chief.candidate_id == agent_entry_id
    if enters_agent and plan is None:
        return "the Chief enters the agent entry but entry_plan is missing"
    if plan is not None and not enters_agent:
        return "entry_plan is only allowed when the Chief ENTERs the agent entry id"
    return None


def _lots_problem(lots: float | None, packet: OperatorPacketBody) -> str | None:
    if lots is None:
        return None
    limits = packet.limits
    steps = round(lots / limits.lots_step)
    on_step = abs(steps * limits.lots_step - lots) <= LOTS_EPSILON
    within = limits.volume_min - LOTS_EPSILON <= lots <= limits.max_lots + LOTS_EPSILON
    if not (on_step and within):
        return (f"lots must be a multiple of {limits.lots_step} between "
                f"{limits.volume_min} and {limits.max_lots}")
    return None


def decision_extras_problem(chief: ChiefDecision, lots: float | None,
                            packet: OperatorPacketBody) -> tuple[str, str] | None:
    """(code, detail) when a v2 decision's `lots` does not fit the packet, else None."""
    if lots is not None and chief.action != "ENTER":
        return DECISION_ERR_LOTS, "lots is only allowed with an ENTER"
    problem = _lots_problem(lots, packet)
    return None if problem is None else (DECISION_ERR_LOTS, problem)


def _check_entry_plan(decision: OperatorDecision, packet: OperatorPacket) -> None:
    problem = entry_plan_problem(decision.chief, decision.entry_plan,
                                 packet.limits.agent_entry_id)
    if problem is not None:
        raise OperatorDecisionError(DECISION_ERR_ENTRY_PLAN, problem)
    extras = decision_extras_problem(decision.chief, decision.lots, packet)
    if extras is not None:
        raise OperatorDecisionError(*extras)


def parse_operator_decision(raw: bytes, packet: OperatorPacket, *,
                            now: float) -> OperatorDecision:
    """Validate a v1/v2 submission against the packet it answers.

    Checks, in order: size (64 KB), strict schema, same cycle and packet hash, not
    expired at `now`, agent allowed, a flat packet (a management packet needs v3),
    every view and the Chief pass `validate_view` against the offered ids, rebuttal
    only for PA TAKE ids, the entry plan and lots. Raises OperatorDecisionError.
    """
    decision = _parse_decision(raw)
    if decision.cycle_id != packet.cycle_id or not hmac.compare_digest(
            decision.packet_hash, packet.packet_hash):
        raise OperatorDecisionError(DECISION_ERR_STALE, "the decision answers another packet")
    if not now <= packet.expires_at_epoch:
        raise OperatorDecisionError(DECISION_ERR_EXPIRED, "the packet has expired")
    if decision.agent not in packet.allowed.agents:
        raise OperatorDecisionError(DECISION_ERR_AGENT, "the agent is not in V6_OPERATOR_AGENTS")
    if packet.state != "flat":
        raise OperatorDecisionError(DECISION_ERR_KIND,
                                    f"a {packet.state} packet needs a v6.operator.decision.3")
    _check_views(decision, packet)
    _check_rebuttal(decision)
    _check_entry_plan(decision, packet)
    return decision
