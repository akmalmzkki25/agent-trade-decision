"""
One operator decision checked role by role against the packet it answers.

`schemas.operator.parse_operator_decision` is all-or-nothing. Here every role
goes through `agents.validate_view` on its own, so the protocol's rule 2 holds
exactly: an invalid (or missing) Price Action view or Chief decision refuses the
submission, while an invalid or missing news, liquidity or structure view is
only flagged and the cycle uses that desk's rules view instead.

Nothing an agent writes can leave the enum space: views are re-parsed strictly
(unknown fields, NaN, oversized text and ids that were not offered are
refused), notes are bounded printable text that no rule reads, and lots never
come from a decision. An agent-designed entry (`entry_plan`) is checked here
against the packet's limits, so the agent learns at once why a plan does not fit
and can resubmit before the deadline; `risk/` still sizes it and may refuse it.
Error details never echo submitted text: they name known fields, pydantic error
types, VIEW_ERR_* codes and agent_entry.PROBLEM_* codes only.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
from types import MappingProxyType
from typing import Final, Literal, TypeVar, cast

from pydantic import Field, JsonValue, ValidationError

from ..config import OperatorAgent, V6Settings
from ..cycle_types import DeskViews, ViewRecord
from ..providers.base import (
    ERR_EMPTY, ERR_INVALID_OUTPUT, ERR_TIMEOUT, OPERATOR_PROVIDER_NAME, PROVIDER_STATUS_FAILED,
    PROVIDER_STATUS_OK, PROVIDER_STATUS_PARTIAL, ProviderResult,
)
from ..providers.offline import VIEW_ERROR_CODES
from ..risk.policy import check_operator_agent
from ..schemas.agents import (
    MAX_RANKED, VIEW_ERR_SCHEMA, AgentView, ChiefDecision, DeskRole, ItemId, LiquidityView,
    NewsRiskView, PriceActionView, StructureView, ViewValidationError, validate_view,
)
from ..schemas.operator import (
    DECISION_ERR_AGENT, DECISION_ERR_ENTRY_PLAN, DECISION_ERR_EXPIRED, DECISION_ERR_NOT_JSON,
    DECISION_ERR_REBUTTAL, DECISION_ERR_SCHEMA, DECISION_ERR_STALE, DECISION_ERR_TOO_LARGE,
    DECISION_ERR_VIEW, MAX_DECISION_BYTES, DecisionSchema, OperatorPacket, PendingAction,
    RebuttalStance, decision_extras_problem, entry_plan_problem,
)
from ..schemas.operator_parts import AgentEntryPlan, Frozen, Hash
from .agent_entry import limits_from_packet, plan_problems
from .panel import CHIEF_ROLE, Baseline, PanelResult

logger = logging.getLogger(__name__)

ViewT = TypeVar("ViewT")
RISK_DESKS: Final[tuple[DeskRole, ...]] = ("news_risk", "liquidity", "structure")
PRICE_ACTION_ROLE: Final[DeskRole] = "price_action"
# A desk view that was absent or null (not one of the agents.VIEW_ERR_* codes).
VIEW_MISSING: Final[str] = "VIEW_MISSING"
FLAG_PROVIDER_CODES: Final[Mapping[str, str]] = MappingProxyType(
    {**VIEW_ERROR_CODES, VIEW_MISSING: ERR_EMPTY})
MAX_DETAIL_CHARS: Final[int] = 300
MAX_REPORTED_ERRORS: Final[int] = 5
UNKNOWN_LOCATION: Final[str] = "?"


# --- the envelope: typed identity, raw views ------------------------------------------
class RawViews(Frozen):
    """Each desk view as raw JSON. A missing or null risk desk is flagged, not refused."""

    price_action: JsonValue
    news_risk: JsonValue = None
    liquidity: JsonValue = None
    structure: JsonValue = None


class DecisionEnvelope(Frozen):
    """An OperatorDecision whose views are still unchecked JSON."""

    schema_version: DecisionSchema
    cycle_id: ItemId
    packet_hash: Hash
    agent: OperatorAgent
    views: RawViews
    chief: JsonValue
    rebuttal: dict[ItemId, RebuttalStance] = Field(default_factory=dict, max_length=MAX_RANKED)
    entry_plan: JsonValue = None
    lots: float | None = Field(default=None, gt=0)
    pending_action: PendingAction | None = None


KNOWN_FIELDS: Final[frozenset[str]] = frozenset(DecisionEnvelope.model_fields) | frozenset(
    RawViews.model_fields)


@dataclass(frozen=True)
class DecisionError:
    """A refused submission: `code` is a DECISION_ERR_* value, `role` the failing role."""

    code: str
    detail: str = ""
    role: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"error": self.code, "detail": self.detail, "role": self.role}


@dataclass(frozen=True)
class DeskFlag:
    """A risk desk view that failed validation; the cycle uses that desk's rules view."""

    role: DeskRole
    code: str                                # agents.VIEW_ERR_* or VIEW_MISSING

    @property
    def provider_code(self) -> str:
        return FLAG_PROVIDER_CODES.get(self.code, ERR_INVALID_OUTPUT)


@dataclass(frozen=True)
class ValidatedDecision:
    """An accepted decision. A flagged desk has no view of its own."""

    cycle_id: str
    packet_hash: str
    agent: str
    price_action: PriceActionView
    chief: ChiefDecision
    news_risk: NewsRiskView | None = None
    liquidity: LiquidityView | None = None
    structure: StructureView | None = None
    rebuttal: Mapping[str, str] = field(default_factory=dict)
    flags: tuple[DeskFlag, ...] = ()
    latency_ms: int = 0
    entry_plan: AgentEntryPlan | None = None
    lots: float | None = None
    pending_action: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "rebuttal", MappingProxyType(dict(self.rebuttal)))
        object.__setattr__(self, "flags", tuple(self.flags))
        missing = {role for role in RISK_DESKS if getattr(self, role) is None}
        if missing != {flag.role for flag in self.flags} or len(self.flags) != len(missing):
            raise ValueError("a desk view is None exactly when that desk is flagged once")
        if self.latency_ms < 0:
            raise ValueError("latency_ms must be >= 0")

    @property
    def views(self) -> DeskViews:
        """The agent's own views (flagged desks are None)."""
        return DeskViews(price_action=self.price_action, news_risk=self.news_risk,
                         liquidity=self.liquidity, structure=self.structure)

    @property
    def withdrawn_ids(self) -> frozenset[str]:
        """For ProtocolInput.withdrawn_ids."""
        return frozenset(cid for cid, stance in self.rebuttal.items() if stance == "withdraw")

    @property
    def flagged_roles(self) -> tuple[str, ...]:
        return tuple(flag.role for flag in self.flags)

    @property
    def status(self) -> str:
        return PROVIDER_STATUS_PARTIAL if self.flags else PROVIDER_STATUS_OK

    def records(self) -> tuple[ViewRecord, ...]:
        """One v6_agent_views row per role: source `operator`, model = the agent."""
        record = partial(ViewRecord, source=OPERATOR_PROVIDER_NAME, model=self.agent,
                         latency_ms=self.latency_ms)
        codes = {flag.role: flag.provider_code for flag in self.flags}
        desks = tuple(record(role=role, view=getattr(self, role), error_code=codes.get(role, ""))
                      for role in (PRICE_ACTION_ROLE, *RISK_DESKS))
        return desks + (record(role=CHIEF_ROLE, view=self.chief),)

    def summary(self) -> dict[str, object]:
        """Log- and status-safe facts (no free text)."""
        return {"cycle_id": self.cycle_id, "agent": self.agent, "action": self.chief.action,
                "candidate_id": self.chief.candidate_id, "flagged": list(self.flagged_roles),
                "agent_entry": self.entry_plan is not None, "lots": self.lots,
                "pending_action": self.pending_action,
                "withdrawn": sorted(self.withdrawn_ids), "latency_ms": self.latency_ms}


DecisionOutcome = ValidatedDecision | DecisionError


# --- parsing ---------------------------------------------------------------------------
def _error(code: str, detail: str, role: str = "") -> DecisionError:
    return DecisionError(code=code, detail=detail[:MAX_DETAIL_CHARS], role=role)


def _safe_location(loc: Sequence[object]) -> str:
    """Error location without submitted text: known field names and indexes only."""
    parts = (str(part) if isinstance(part, int) or part in KNOWN_FIELDS else UNKNOWN_LOCATION
             for part in loc)
    return ".".join(parts)


def parse_envelope(raw: bytes) -> DecisionEnvelope | DecisionError:
    """Size cap (64 KB) and the strict envelope; the views stay raw for per-role checks."""
    if not isinstance(raw, (bytes, bytearray)):
        raise TypeError("the decision body must be bytes")
    if len(raw) > MAX_DECISION_BYTES:
        return _error(DECISION_ERR_TOO_LARGE, f"{len(raw)} bytes exceeds {MAX_DECISION_BYTES}")
    try:
        return DecisionEnvelope.model_validate_json(bytes(raw))
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        kinds = {err["type"] for err in errors}
        code = DECISION_ERR_NOT_JSON if kinds == {"json_invalid"} else DECISION_ERR_SCHEMA
        where = ", ".join(f"{_safe_location(err['loc'])}({err['type']})"
                          for err in errors[:MAX_REPORTED_ERRORS])
        return _error(code, f"invalid at: {where}")


# --- validation ------------------------------------------------------------------------
def _packet_problem(envelope: DecisionEnvelope, packet: OperatorPacket, settings: V6Settings,
                    now: float) -> DecisionError | None:
    answers = envelope.cycle_id == packet.cycle_id and hmac.compare_digest(
        envelope.packet_hash, packet.packet_hash)
    if not answers:
        return _error(DECISION_ERR_STALE, "the decision answers another packet")
    if not now <= packet.expires_at_epoch:
        return _error(DECISION_ERR_EXPIRED, "the packet has expired")
    enabled = check_operator_agent(envelope.agent, settings).allowed
    if not (enabled and envelope.agent in packet.allowed.agents):
        return _error(DECISION_ERR_AGENT, "the agent is not in V6_OPERATOR_AGENTS")
    return None


def _check_role(role: str, raw: object, candidates: frozenset[str],
                events: frozenset[str]) -> AgentView | str:
    """The validated view, or the code that refused it."""
    if raw is None:
        return VIEW_MISSING
    if not isinstance(raw, dict):
        return VIEW_ERR_SCHEMA
    try:
        return validate_view(role, raw, candidates, events)
    except ViewValidationError as exc:
        return exc.code


def _typed(result: AgentView | str, model: type[ViewT]) -> ViewT | None:
    return result if isinstance(result, model) else None


def _take_ids(view: PriceActionView) -> frozenset[str]:
    return frozenset(item.candidate_id for item in view.ranked if item.verdict == "TAKE")


def _checked_views(envelope: DecisionEnvelope, packet: OperatorPacket) -> DecisionOutcome:
    check = partial(_check_role, candidates=frozenset(packet.allowed.candidate_ids),
                    events=frozenset(packet.allowed.event_ids))
    price_action = check(PRICE_ACTION_ROLE, envelope.views.price_action)
    chief = check(CHIEF_ROLE, envelope.chief)
    for role, result in ((PRICE_ACTION_ROLE, price_action), (CHIEF_ROLE, chief)):
        if isinstance(result, str):
            return _error(DECISION_ERR_VIEW, f"{role}: {result}", role)
    # validate_view returns the model registered for the role (agents.VIEW_MODELS).
    pa_view, chief_view = cast(PriceActionView, price_action), cast(ChiefDecision, chief)
    stray = set(envelope.rebuttal) - _take_ids(pa_view)
    if stray:
        return _error(DECISION_ERR_REBUTTAL,
                      f"rebuttal names {len(stray)} candidate(s) price_action did not TAKE")
    plan = _checked_plan(envelope, packet, chief_view)
    if isinstance(plan, DecisionError):
        return plan
    extras = decision_extras_problem(chief_view, envelope.lots, envelope.pending_action, packet)
    if extras is not None:
        return _error(extras[0], extras[1], "decision")
    desks = {role: check(role, getattr(envelope.views, role)) for role in RISK_DESKS}
    return ValidatedDecision(
        cycle_id=envelope.cycle_id, packet_hash=envelope.packet_hash, agent=envelope.agent,
        price_action=pa_view, chief=chief_view,
        news_risk=_typed(desks["news_risk"], NewsRiskView),
        liquidity=_typed(desks["liquidity"], LiquidityView),
        structure=_typed(desks["structure"], StructureView),
        rebuttal=dict(envelope.rebuttal), entry_plan=plan, lots=envelope.lots,
        pending_action=envelope.pending_action,
        flags=tuple(DeskFlag(role=role, code=result) for role, result in desks.items()
                    if isinstance(result, str)))


def _checked_plan(envelope: DecisionEnvelope, packet: OperatorPacket,
                  chief: ChiefDecision) -> AgentEntryPlan | DecisionError | None:
    """The agent's entry plan when the Chief enters it, checked against the packet limits."""
    raw = envelope.entry_plan
    mismatch = entry_plan_problem(chief, raw, packet.limits.agent_entry_id)
    if mismatch is not None:
        return _error(DECISION_ERR_ENTRY_PLAN, mismatch, "entry_plan")
    if raw is None:
        return None
    try:
        plan = AgentEntryPlan.model_validate(raw)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        where = ", ".join(f"{_safe_location(err['loc'])}({err['type']})"
                          for err in errors[:MAX_REPORTED_ERRORS])
        return _error(DECISION_ERR_ENTRY_PLAN, f"invalid at: {where}", "entry_plan")
    if plan.order_type != chief.order_style:
        return _error(DECISION_ERR_ENTRY_PLAN,
                      f"chief.order_style {chief.order_style} must equal "
                      f"entry_plan.order_type {plan.order_type}", "entry_plan")
    problems = plan_problems(plan, limits_from_packet(packet))
    if problems:
        detail = "; ".join(f"{item.code}: {item.message}" for item in problems)
        return _error(DECISION_ERR_ENTRY_PLAN, detail, "entry_plan")
    return plan


def validate_decision(packet: OperatorPacket, decision: bytes | DecisionEnvelope,
                      settings: V6Settings, *, now: float) -> DecisionOutcome:
    """Check one submission against the packet it answers; never raises on bad input.

    Order: size and envelope, same cycle and packet hash, not expired at `now`,
    agent enabled (V6_OPERATOR_AGENTS and the packet), Price Action and Chief
    (a failure refuses), rebuttal only for PA TAKE ids, then the risk desks (a
    failure is flagged). Raises TypeError only when `decision` is not bytes.
    """
    envelope = decision if isinstance(decision, DecisionEnvelope) else parse_envelope(decision)
    if isinstance(envelope, DecisionError):
        return envelope
    problem = _packet_problem(envelope, packet, settings, now)
    if problem is not None:
        return problem
    outcome = _checked_views(envelope, packet)
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
