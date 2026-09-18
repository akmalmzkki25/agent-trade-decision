"""
What every operator decision version shares: the envelopes, the parse, the packet
checks, the per-role view checks and the accepted decision (`operator_decision` holds
the version 1/2 rules, `decision_v3` the version 3 rules).

Error details never echo submitted text: they name known fields, pydantic error types
and rule codes only.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from types import MappingProxyType
from typing import Final, Literal, TypeVar, cast

from pydantic import BaseModel, Field, JsonValue, ValidationError

from ..config import OperatorAgent, V6Settings
from ..cycle_types import DeskViews, ViewRecord
from ..providers.base import (
    ERR_EMPTY, ERR_INVALID_OUTPUT, OPERATOR_PROVIDER_NAME, PROVIDER_STATUS_OK,
    PROVIDER_STATUS_PARTIAL,
)
from ..providers.offline import VIEW_ERROR_CODES
from ..risk.policy import check_operator_agent
from ..schemas.agents import (
    MAX_RANKED, VIEW_ERR_SCHEMA, AgentView, ChiefDecision, DeskRole, ItemId, LiquidityView,
    NewsRiskView, PriceActionView, StructureView, ViewValidationError, validate_view,
)
from ..schemas.operator import (
    DECISION_ERR_AGENT, DECISION_ERR_EXPIRED, DECISION_ERR_NOT_JSON, DECISION_ERR_SCHEMA,
    DECISION_ERR_STALE, DECISION_ERR_TOO_LARGE, DECISION_ERR_VIEW, DECISION_SCHEMA,
    DECISION_SCHEMA_V2, MAX_DECISION_BYTES, DecisionNote, DecisionSchema, OperatorPacket,
    RebuttalStance,
)
from ..schemas.operator_parts import AgentEntryPlan, DecisionAction, Frozen, Hash, PacketKind
from ..schemas.operator_plan import EntryPlanV2, M15Bias, ManageRequest
from .panel import CHIEF_ROLE

ModelT = TypeVar("ModelT", bound=BaseModel)
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


# --- the envelopes: typed identity, raw views ------------------------------------------
class RawViews(Frozen):
    """Each desk view as raw JSON. A missing or null risk desk is flagged, not refused."""

    price_action: JsonValue
    news_risk: JsonValue = None
    liquidity: JsonValue = None
    structure: JsonValue = None


class DecisionEnvelope(Frozen):
    """A version 1/2 OperatorDecision whose views are still unchecked JSON."""

    schema_version: DecisionSchema
    cycle_id: ItemId
    packet_hash: Hash
    agent: OperatorAgent
    views: RawViews
    chief: JsonValue
    rebuttal: dict[ItemId, RebuttalStance] = Field(default_factory=dict, max_length=MAX_RANKED)
    entry_plan: JsonValue = None
    lots: float | None = Field(default=None, gt=0)


class DecisionEnvelopeV3(Frozen):
    """A version 3 decision: the action, and the plan, manage request and bias still raw."""

    schema_version: Literal["v6.operator.decision.3"]
    packet_kind: PacketKind
    cycle_id: ItemId
    packet_hash: Hash
    agent: OperatorAgent
    action: DecisionAction
    views: RawViews | None = None
    entry_plan: JsonValue = None
    manage: JsonValue = None
    m15_bias: JsonValue = None
    note: DecisionNote = ""


Envelope = DecisionEnvelope | DecisionEnvelopeV3
KNOWN_FIELDS: Final[frozenset[str]] = frozenset().union(*(
    model.model_fields for model in (DecisionEnvelope, DecisionEnvelopeV3, RawViews,
                                      AgentEntryPlan, EntryPlanV2, ManageRequest, M15Bias)))


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
    """An accepted decision. A flagged desk has no view of its own.

    Version 1/2 carry the Chief, the rebuttal and an optional `entry_plan`/`lots`;
    version 3 carries `action`, `plan` (ENTER), `manage` (MANAGE), `bias` and `note`,
    and its Chief is derived from the action.
    """

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
    schema_version: str = DECISION_SCHEMA_V2
    action: str = ""
    plan: EntryPlanV2 | None = None
    manage: ManageRequest | None = None
    bias: M15Bias | None = None
    note: str = ""

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
        return {"cycle_id": self.cycle_id, "agent": self.agent, "schema": self.schema_version,
                "decision_action": self.action or self.chief.action,
                "action": self.chief.action, "candidate_id": self.chief.candidate_id,
                "flagged": list(self.flagged_roles),
                "agent_entry": self.entry_plan is not None or self.plan is not None,
                "lots": self.lots if self.plan is None else self.plan.lots,
                "plan_order_type": None if self.plan is None else self.plan.order_type,
                "manage_op": None if self.manage is None else self.manage.op,
                "withdrawn": sorted(self.withdrawn_ids), "latency_ms": self.latency_ms}


DecisionOutcome = ValidatedDecision | DecisionError


# --- parsing ---------------------------------------------------------------------------
def decision_error(code: str, detail: str, role: str = "") -> DecisionError:
    return DecisionError(code=code, detail=detail[:MAX_DETAIL_CHARS], role=role)


def safe_location(loc: Sequence[object]) -> str:
    """Error location without submitted text: known field names and indexes only."""
    parts = (str(part) if isinstance(part, int) or part in KNOWN_FIELDS else UNKNOWN_LOCATION
             for part in loc)
    return ".".join(parts)


def error_locations(exc: ValidationError) -> str:
    """'where(type), ...' for the first errors of a pydantic failure."""
    errors = exc.errors(include_url=False, include_context=False, include_input=False)
    return ", ".join(f"{safe_location(err['loc'])}({err['type']})"
                     for err in errors[:MAX_REPORTED_ERRORS])


def parse_model(model: type[ModelT], raw: bytes) -> ModelT | DecisionError:
    """Size cap (64 KB) and a strict parse of the whole document."""
    if not isinstance(raw, (bytes, bytearray)):
        raise TypeError("the decision body must be bytes")
    if len(raw) > MAX_DECISION_BYTES:
        return decision_error(DECISION_ERR_TOO_LARGE,
                              f"{len(raw)} bytes exceeds {MAX_DECISION_BYTES}")
    try:
        return model.model_validate_json(bytes(raw))
    except ValidationError as exc:
        kinds = {err["type"] for err in exc.errors(include_url=False, include_context=False,
                                                   include_input=False)}
        code = DECISION_ERR_NOT_JSON if kinds == {"json_invalid"} else DECISION_ERR_SCHEMA
        return decision_error(code, f"invalid at: {error_locations(exc)}")


def schema_of(raw: bytes) -> str | None:
    """The schema_version of a JSON object, or None (the strict parse then says why)."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_DECISION_BYTES:
        return None
    try:
        document = json.loads(bytes(raw))
    except (ValueError, UnicodeDecodeError):
        return None
    version = document.get("schema_version") if isinstance(document, dict) else None
    return version if isinstance(version, str) else None


def parse_envelope(raw: bytes) -> Envelope | DecisionError:
    """The strict envelope of any version; the views stay raw for per-role checks."""
    if schema_of(raw) == DECISION_SCHEMA:
        return parse_model(DecisionEnvelopeV3, raw)
    return parse_model(DecisionEnvelope, raw)


def parse_json_value(model: type[ModelT], raw: object, code: str,
                     role: str) -> ModelT | DecisionError:
    """A nested raw JSON value parsed strictly through JSON (tuples accept arrays)."""
    try:
        return model.model_validate_json(json.dumps(raw, allow_nan=False))
    except (ValidationError, ValueError) as exc:
        where = error_locations(exc) if isinstance(exc, ValidationError) else "?"
        return decision_error(code, f"invalid at: {where}", role)


# --- checks ------------------------------------------------------------------------------
def packet_problem(envelope: Envelope, packet: OperatorPacket, settings: V6Settings,
                   now: float) -> DecisionError | None:
    """Same cycle and packet hash, not expired at `now`, agent enabled."""
    answers = envelope.cycle_id == packet.cycle_id and hmac.compare_digest(
        envelope.packet_hash, packet.packet_hash)
    if not answers:
        return decision_error(DECISION_ERR_STALE, "the decision answers another packet")
    if not now <= packet.expires_at_epoch:
        return decision_error(DECISION_ERR_EXPIRED, "the packet has expired")
    enabled = check_operator_agent(envelope.agent, settings).allowed
    if not (enabled and envelope.agent in packet.allowed.agents):
        return decision_error(DECISION_ERR_AGENT, "the agent is not in V6_OPERATOR_AGENTS")
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


def role_checker(packet: OperatorPacket):
    """`check(role, raw)` against the ids this packet offered."""
    return partial(_check_role, candidates=frozenset(packet.allowed.candidate_ids),
                   events=frozenset(packet.allowed.event_ids))


def _typed(result: AgentView | str, model: type[ViewT]) -> ViewT | None:
    return result if isinstance(result, model) else None


CheckedDesks = tuple[PriceActionView, dict[str, AgentView | None], tuple[DeskFlag, ...]]


def checked_desks(views: RawViews, packet: OperatorPacket) -> CheckedDesks | DecisionError:
    """(Price Action, the risk desk views by role, flags): an invalid Price Action view
    refuses the decision, an invalid risk desk view is only flagged."""
    check = role_checker(packet)
    price_action = check(PRICE_ACTION_ROLE, views.price_action)
    if isinstance(price_action, str):
        return decision_error(DECISION_ERR_VIEW, f"{PRICE_ACTION_ROLE}: {price_action}",
                              PRICE_ACTION_ROLE)
    desks = {role: check(role, getattr(views, role)) for role in RISK_DESKS}
    typed = {"news_risk": _typed(desks["news_risk"], NewsRiskView),
             "liquidity": _typed(desks["liquidity"], LiquidityView),
             "structure": _typed(desks["structure"], StructureView)}
    flags = tuple(DeskFlag(role=role, code=result) for role, result in desks.items()
                  if isinstance(result, str))
    # validate_view returns the model registered for the role (agents.VIEW_MODELS).
    return cast(PriceActionView, price_action), typed, flags
