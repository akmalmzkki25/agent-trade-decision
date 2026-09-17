"""
Contracts of the operator backend (plan section 3.3; user decisions 2026-09-16).

Each cycle that reaches tier 1 is served to an operator agent (Claude Code, Codex
or Antigravity, in a chat session) as one `OperatorPacket`; the agent answers
with one `OperatorDecision` that fills the four desk views and the Chief. The Chief
either enters one of the detector suggestions or the agent's own entry plan (the
packet's `limits.agent_entry_id`, user decision 2026-09-17): side, order type,
entry, stop and an optional target, which code validates and sizes. Agents never
set lots. `parse_operator_decision` applies the same semantic checks as
`agents.validate_view`. Packets exist for DEMO accounts only.

`packet_hash` is the sha256 of the canonical JSON (sorted keys, no whitespace,
ASCII) of the packet without its `packet_hash` and `decision_template` keys; a
decision must echo it. Build served packets with `seal_packet`.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from typing import Final, Literal, TypeVar

from pydantic import Field, ValidationError, model_validator

from ..config import OperatorAgent
from ..cycle_codes import MAX_OFFERED_CANDIDATES
from ..types import TIMEFRAME_SECONDS
from .agents import (
    DESK_ROLES, MAX_RANKED, ChiefDecision, ItemId, LiquidityView, NewsRiskView, PriceActionView,
    StructureView, ViewValidationError, validate_view,
)
from .operator_parts import (
    ENUM_CHOICES, EQUITY_BANDS, LIMIT_VALUES, MAX_CODES, MAX_DECISION_BYTES, MAX_EVENTS,
    MAX_FEATURES, MAX_GATES, MAX_PACKET_H1_BARS, MAX_PACKET_M15_BARS, TOP_EQUITY_BAND,
    AgentEntryPlan, AllowedValues, BaselineViews, CandidateExit, CandidateSizing, CompactBar,
    Epoch, EquityBand, Frozen, Hash, PacketAccount, PacketBars, PacketCalendar,
    PacketCandidate, PacketEvent, PacketGate, PacketLevels, PacketLimits, PacketMarket,
    PacketSession, RebuttalStance, allowed_values, canonical_json, equity_band,
)

__all__ = [
    "PACKET_SCHEMA", "DECISION_SCHEMA", "HASH_EXCLUDED_KEYS", "MAX_DECISION_BYTES",
    "MAX_PACKET_M15_BARS", "MAX_PACKET_H1_BARS", "MAX_GATES", "MAX_EVENTS", "MAX_CODES",
    "MAX_FEATURES", "ENUM_CHOICES", "LIMIT_VALUES", "EQUITY_BANDS", "TOP_EQUITY_BAND",
    "DECISION_ERR_TOO_LARGE", "DECISION_ERR_NOT_JSON", "DECISION_ERR_SCHEMA",
    "DECISION_ERR_STALE", "DECISION_ERR_EXPIRED", "DECISION_ERR_AGENT", "DECISION_ERR_VIEW",
    "DECISION_ERR_REBUTTAL", "DECISION_ERR_ENTRY_PLAN", "DECISION_SCHEMAS", "AgentEntryPlan",
    "PacketLimits", "PacketLevels", "EquityBand", "RebuttalStance", "CompactBar",
    "PacketAccount", "PacketMarket", "PacketSession", "PacketBars", "PacketGate",
    "PacketEvent", "PacketCalendar", "CandidateExit", "CandidateSizing", "PacketCandidate",
    "BaselineViews", "AllowedValues", "OperatorPacketBody", "OperatorPacket", "OperatorViews",
    "OperatorDecision", "OperatorDecisionError", "ABSTAIN_VIEW", "UNKNOWN_NEWS_VIEW",
    "UNKNOWN_LIQUIDITY_VIEW", "UNKNOWN_STRUCTURE_VIEW", "HOLD_DECISION", "allowed_values",
    "canonical_json", "equity_band", "packet_hash", "decision_template", "seal_packet",
    "parse_operator_decision",
]

PACKET_SCHEMA: Final[str] = "v6.operator.packet.2"
DECISION_SCHEMA: Final[str] = "v6.operator.decision.2"
# Version 1 decisions (no entry_plan) are still accepted: they can only pick a suggestion.
DECISION_SCHEMAS: Final[tuple[str, ...]] = ("v6.operator.decision.1", DECISION_SCHEMA)
DecisionSchema = Literal["v6.operator.decision.1", "v6.operator.decision.2"]
HASH_EXCLUDED_KEYS: Final[frozenset[str]] = frozenset({"packet_hash", "decision_template"})
M15_S: Final[int] = TIMEFRAME_SECONDS["M15"]
MAX_ERROR_DETAIL_CHARS: Final[int] = 300
ViewT = TypeVar("ViewT")

# OperatorDecisionError.code values (the operator route answers 422 with the code).
DECISION_ERR_TOO_LARGE: Final[str] = "DECISION_TOO_LARGE"
DECISION_ERR_NOT_JSON: Final[str] = "DECISION_NOT_JSON"
DECISION_ERR_SCHEMA: Final[str] = "DECISION_SCHEMA"
DECISION_ERR_STALE: Final[str] = "DECISION_STALE_PACKET"
DECISION_ERR_EXPIRED: Final[str] = "DECISION_EXPIRED"
DECISION_ERR_AGENT: Final[str] = "DECISION_AGENT_NOT_ALLOWED"
DECISION_ERR_VIEW: Final[str] = "DECISION_VIEW"
DECISION_ERR_REBUTTAL: Final[str] = "DECISION_REBUTTAL"
DECISION_ERR_ENTRY_PLAN: Final[str] = "DECISION_ENTRY_PLAN"


def packet_hash(document: Mapping[str, object]) -> str:
    """sha256 hex of a packet's JSON document, ignoring HASH_EXCLUDED_KEYS."""
    body = {key: value for key, value in document.items() if key not in HASH_EXCLUDED_KEYS}
    return hashlib.sha256(canonical_json(body)).hexdigest()


class OperatorPacketBody(Frozen):
    """Everything the agent decides on; `packet_hash` covers exactly these fields."""

    schema_version: Literal["v6.operator.packet.2"]
    cycle_id: ItemId
    created_at_epoch: Epoch
    expires_at_epoch: Epoch
    bar_open_epoch: Epoch
    bar_close_epoch: Epoch
    mode: Literal["shadow", "execute"]
    session_id: ItemId
    account: PacketAccount
    market: PacketMarket
    session: PacketSession
    bars: PacketBars
    levels: PacketLevels
    limits: PacketLimits
    gates: tuple[PacketGate, ...] = Field(max_length=MAX_GATES)
    calendar: PacketCalendar
    candidates: tuple[PacketCandidate, ...] = Field(max_length=MAX_OFFERED_CANDIDATES)
    baseline_views: BaselineViews
    allowed: AllowedValues

    @model_validator(mode="after")
    def _check_body(self) -> "OperatorPacketBody":
        ids = tuple(item.candidate_id for item in self.candidates) + (
            self.limits.agent_entry_id,)
        events = tuple(event.event_id for event in self.calendar.events)
        checks = (
            (self.bar_close_epoch == self.bar_open_epoch + M15_S, "bar_close is not open + M15"),
            (self.bar_close_epoch <= self.created_at_epoch < self.expires_at_epoch,
             "times must satisfy bar_close <= created_at < expires_at"),
            (ids == self.allowed.candidate_ids and len(set(ids)) == len(ids),
             "allowed.candidate_ids must list the distinct candidates, then the agent entry"),
            (events == self.allowed.event_ids, "allowed.event_ids must list the calendar events"),
        )
        problems = [message for ok, message in checks if not ok]
        if problems:
            raise ValueError("; ".join(problems))
        return self


class OperatorViews(Frozen):
    """All four desks; copy a `baseline_views` entry to keep the rules view."""

    price_action: PriceActionView
    news_risk: NewsRiskView
    liquidity: LiquidityView
    structure: StructureView


class OperatorDecision(Frozen):
    """One agent's answer to one packet. `rebuttal` is PA's R2 stance per TAKE candidate.

    `entry_plan` is required exactly when the Chief ENTERs the packet's agent entry id.
    """

    schema_version: DecisionSchema
    cycle_id: ItemId
    packet_hash: Hash
    agent: OperatorAgent
    views: OperatorViews
    chief: ChiefDecision
    rebuttal: dict[ItemId, RebuttalStance] = Field(default_factory=dict, max_length=MAX_RANKED)
    entry_plan: AgentEntryPlan | None = None

    @property
    def withdrawn_ids(self) -> frozenset[str]:
        """For ProtocolInput.withdrawn_ids."""
        return frozenset(cid for cid, stance in self.rebuttal.items() if stance == "withdraw")


class OperatorPacket(OperatorPacketBody):
    """A served packet: the body, its hash and a ready-to-edit decision (HOLD)."""

    packet_hash: Hash
    decision_template: OperatorDecision

    @model_validator(mode="after")
    def _check_seal(self) -> "OperatorPacket":
        digest = packet_hash(self.model_dump(mode="json", exclude=set(HASH_EXCLUDED_KEYS)))
        template = self.decision_template
        checks = (
            (hmac.compare_digest(digest, self.packet_hash), "packet_hash does not match"),
            (template.cycle_id == self.cycle_id and template.packet_hash == self.packet_hash,
             "decision_template answers another packet"),
            (template.agent in self.allowed.agents, "decision_template agent is not allowed"),
        )
        problems = [message for ok, message in checks if not ok]
        if problems:
            raise ValueError("; ".join(problems))
        return self


ABSTAIN_VIEW: Final[PriceActionView] = PriceActionView(abstain=True, ranked=())
UNKNOWN_NEWS_VIEW: Final[NewsRiskView] = NewsRiskView(
    stance="CAUTION", size_multiplier=0.5, regime="UNCLEAR", event_ids=(),
    reason_codes=("DATA_MISSING",), note="")
UNKNOWN_LIQUIDITY_VIEW: Final[LiquidityView] = LiquidityView(
    stance="CAUTION", size_multiplier=0.5, order_style="LIMIT", reason_codes=("DATA_MISSING",),
    note="")
UNKNOWN_STRUCTURE_VIEW: Final[StructureView] = StructureView(
    regime="UNCLEAR", counter_structure_veto=False, size_multiplier=0.5, named_patterns=(),
    reason_codes=("DATA_MISSING",), note="")
HOLD_DECISION: Final[ChiefDecision] = ChiefDecision(
    action="HOLD", candidate_id=None, risk_tier="reduced", order_style="LIMIT",
    exit_profile="STANDARD", confidence=0.0, rationale="", dissent="")


def _or_default(view: ViewT | None, default: ViewT) -> ViewT:
    return default if view is None else view


def decision_template(body: OperatorPacketBody, digest: str) -> OperatorDecision:
    """The rules views (or cautious defaults) with a HOLD Chief, for the first allowed agent."""
    base = body.baseline_views
    views = OperatorViews(
        price_action=_or_default(base.price_action, ABSTAIN_VIEW),
        news_risk=_or_default(base.news_risk, UNKNOWN_NEWS_VIEW),
        liquidity=_or_default(base.liquidity, UNKNOWN_LIQUIDITY_VIEW),
        structure=_or_default(base.structure, UNKNOWN_STRUCTURE_VIEW))
    return OperatorDecision(schema_version=DECISION_SCHEMA, cycle_id=body.cycle_id,
                            packet_hash=digest, agent=body.allowed.agents[0], views=views,
                            chief=HOLD_DECISION)


def seal_packet(body: OperatorPacketBody) -> OperatorPacket:
    """The packet to serve: `body` plus its hash and decision template, validated again."""
    document = body.model_dump(mode="json")
    digest = packet_hash(document)
    template = decision_template(body, digest).model_dump(mode="json")
    sealed = {**document, "packet_hash": digest, "decision_template": template}
    return OperatorPacket.model_validate_json(canonical_json(sealed))


# --- decisions ---------------------------------------------------------------------------
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


def _check_entry_plan(decision: OperatorDecision, packet: OperatorPacket) -> None:
    problem = entry_plan_problem(decision.chief, decision.entry_plan,
                                 packet.limits.agent_entry_id)
    if problem is not None:
        raise OperatorDecisionError(DECISION_ERR_ENTRY_PLAN, problem)


def parse_operator_decision(raw: bytes, packet: OperatorPacket, *,
                            now: float) -> OperatorDecision:
    """Validate an agent's submission against the packet it answers.

    Checks, in order: size (64 KB), strict schema, same cycle and packet hash,
    not expired at `now`, agent allowed, every view and the Chief pass
    `validate_view` against the offered ids, rebuttal only for PA TAKE ids.
    Raises OperatorDecisionError.
    """
    decision = _parse_decision(raw)
    if decision.cycle_id != packet.cycle_id or not hmac.compare_digest(
            decision.packet_hash, packet.packet_hash):
        raise OperatorDecisionError(DECISION_ERR_STALE, "the decision answers another packet")
    if not now <= packet.expires_at_epoch:
        raise OperatorDecisionError(DECISION_ERR_EXPIRED, "the packet has expired")
    if decision.agent not in packet.allowed.agents:
        raise OperatorDecisionError(DECISION_ERR_AGENT, "the agent is not in V6_OPERATOR_AGENTS")
    _check_views(decision, packet)
    _check_rebuttal(decision)
    _check_entry_plan(decision, packet)
    return decision
