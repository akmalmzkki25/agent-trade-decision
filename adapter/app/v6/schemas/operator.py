"""
Contracts of the operator backend (plan section 3.3; user decisions 2026-09-16/17).

Each cycle that reaches tier 1 is served to an operator agent (Claude Code, Codex or
Antigravity, in a chat session) as one `OperatorPacket`. A packet has a state: `flat`
(the agent may HOLD or ENTER, its own plan included), `pending` or `position` (a V6
order rests or a V6 position is open: the agent manages it). The agent answers with a
decision v3 (`deliberation.decision_v3`); a v2 decision (`OperatorDecision`: four desk
views and a Chief) is still accepted for a flat packet. Packets exist for DEMO accounts
only.

`packet_hash` is the sha256 of the canonical JSON (sorted keys, no whitespace, ASCII)
of the packet without its `packet_hash` and `decision_template` keys; a decision must
echo it. Build served packets with `seal_packet`.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from typing import Annotated, Final, Literal, TypeVar

from pydantic import AfterValidator, Field, StringConstraints, model_validator

from ..config import OperatorAgent
from ..cycle_codes import MAX_OFFERED_CANDIDATES
from ..types import TIMEFRAME_SECONDS
from .agents import (
    MAX_RANKED, ChiefDecision, ItemId, LiquidityView, NewsRiskView, PriceActionView,
    StructureView,
)
from .agents import _printable as printable
from .operator_parts import (
    ENUM_CHOICES, EQUITY_BANDS, LIMIT_VALUES, MAX_CODES, MAX_DECISION_BYTES, MAX_EVENTS,
    MAX_FEATURES, MAX_GATES, MAX_PACKET_H1_BARS, MAX_PACKET_M15_BARS, TOP_EQUITY_BAND,
    AgentEntryPlan, AllowedValues, BaselineViews, CandidateExit, CandidateSizing, CompactBar,
    DecisionAction, Epoch, EquityBand, Frozen, Hash, PacketAccount, PacketBars, PacketCalendar,
    PacketCandidate, PacketEvent, PacketGate, PacketKind, PacketLevels, PacketLimits,
    PacketMarket, PacketSession, PacketState, RebuttalStance, allowed_values, canonical_json,
    equity_band,
)
from .operator_minute import MinuteState
from .operator_plan import (
    M15Bias, ManageRequest, PacketAction, PacketPendingOrder, PacketPosition,
)

__all__ = [
    "PACKET_SCHEMA", "DECISION_SCHEMA", "DECISION_SCHEMA_V2", "HASH_EXCLUDED_KEYS",
    "MAX_DECISION_BYTES", "MAX_PACKET_M15_BARS", "MAX_PACKET_H1_BARS", "MAX_GATES",
    "MAX_EVENTS", "MAX_CODES", "MAX_FEATURES", "ENUM_CHOICES", "LIMIT_VALUES", "EQUITY_BANDS",
    "TOP_EQUITY_BAND", "DECISION_ERR_TOO_LARGE", "DECISION_ERR_NOT_JSON",
    "DECISION_ERR_SCHEMA", "DECISION_ERR_STALE", "DECISION_ERR_EXPIRED", "DECISION_ERR_AGENT",
    "DECISION_ERR_VIEW", "DECISION_ERR_REBUTTAL", "DECISION_ERR_ENTRY_PLAN",
    "DECISION_ERR_LOTS", "DECISION_ERR_MANAGE", "DECISION_ERR_BIAS", "DECISION_ERR_KIND",
    "DECISION_SCHEMAS", "AgentEntryPlan", "PacketLimits", "PacketLevels",
    "PacketPendingOrder", "PacketPosition", "PacketAction", "M15Bias", "ManageRequest",
    "decision_extras_problem", "EquityBand", "RebuttalStance", "CompactBar",
    "PacketAccount", "PacketMarket", "PacketSession", "PacketBars", "PacketGate",
    "PacketEvent", "PacketCalendar", "CandidateExit", "CandidateSizing", "PacketCandidate",
    "BaselineViews", "AllowedValues", "OperatorPacketBody", "OperatorPacket", "OperatorViews",
    "OperatorDecision", "DecisionTemplate", "OperatorDecisionError", "ABSTAIN_VIEW",
    "UNKNOWN_NEWS_VIEW", "UNKNOWN_LIQUIDITY_VIEW", "UNKNOWN_STRUCTURE_VIEW", "HOLD_DECISION",
    "allowed_values", "canonical_json", "equity_band", "packet_hash", "decision_template",
    "seal_packet", "parse_operator_decision", "entry_plan_problem",
]

PACKET_SCHEMA: Final[str] = "v6.operator.packet.3"
DECISION_SCHEMA_V2: Final[str] = "v6.operator.decision.2"
DECISION_SCHEMA: Final[str] = "v6.operator.decision.3"
# Version 1 and 2 decisions are still accepted for a flat packet.
DECISION_SCHEMAS: Final[tuple[str, ...]] = (
    "v6.operator.decision.1", DECISION_SCHEMA_V2, DECISION_SCHEMA)
DecisionSchema = Literal["v6.operator.decision.1", "v6.operator.decision.2"]
HASH_EXCLUDED_KEYS: Final[frozenset[str]] = frozenset({"packet_hash", "decision_template"})
M15_S: Final[int] = TIMEFRAME_SECONDS["M15"]
M1_S: Final[int] = TIMEFRAME_SECONDS["M1"]
MAX_NOTE_CHARS: Final[int] = 300
# The agent's free note of a v3 decision: untrusted, cleaned of control characters.
DecisionNote = Annotated[str, StringConstraints(max_length=MAX_NOTE_CHARS),
                         AfterValidator(printable)]
ViewT = TypeVar("ViewT")

# Decision error codes (the operator route answers 422 with the code).
DECISION_ERR_TOO_LARGE: Final[str] = "DECISION_TOO_LARGE"
DECISION_ERR_NOT_JSON: Final[str] = "DECISION_NOT_JSON"
DECISION_ERR_SCHEMA: Final[str] = "DECISION_SCHEMA"
DECISION_ERR_STALE: Final[str] = "DECISION_STALE_PACKET"
DECISION_ERR_EXPIRED: Final[str] = "DECISION_EXPIRED"
DECISION_ERR_AGENT: Final[str] = "DECISION_AGENT_NOT_ALLOWED"
DECISION_ERR_VIEW: Final[str] = "DECISION_VIEW"
DECISION_ERR_REBUTTAL: Final[str] = "DECISION_REBUTTAL"
DECISION_ERR_ENTRY_PLAN: Final[str] = "DECISION_ENTRY_PLAN"
DECISION_ERR_LOTS: Final[str] = "DECISION_LOTS"
DECISION_ERR_MANAGE: Final[str] = "DECISION_MANAGE"
DECISION_ERR_BIAS: Final[str] = "DECISION_BIAS"
DECISION_ERR_KIND: Final[str] = "DECISION_KIND"


def packet_hash(document: Mapping[str, object]) -> str:
    """sha256 hex of a packet's JSON document, ignoring HASH_EXCLUDED_KEYS."""
    body = {key: value for key, value in document.items() if key not in HASH_EXCLUDED_KEYS}
    return hashlib.sha256(canonical_json(body)).hexdigest()


class OperatorPacketBody(Frozen):
    """Everything the agent decides on; `packet_hash` covers exactly these fields."""

    schema_version: Literal["v6.operator.packet.3"]
    packet_kind: PacketKind
    state: PacketState
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
    # The trade the agent manages (exactly one of them, matching `state`).
    pending_order: PacketPendingOrder | None = None
    position: PacketPosition | None = None
    # What the agent did last: its newest action and its newest M15 bias.
    last_action: PacketAction | None = None
    last_bias: M15Bias | None = None
    last_bias_at_epoch: Epoch | None = None
    # An m1 packet: what the closed M1 bars did and the distances to the managed trade.
    m1_state: MinuteState | None = None

    @model_validator(mode="after")
    def _check_body(self) -> "OperatorPacketBody":
        ids = tuple(item.candidate_id for item in self.candidates) + (
            self.limits.agent_entry_id,)
        events = tuple(event.event_id for event in self.calendar.events)
        bar_s = M15_S if self.packet_kind == "m15" else M1_S
        checks = (
            (self.bar_close_epoch == self.bar_open_epoch + bar_s,
             "bar_close is not the bar open plus the packet's bar"),
            ((self.packet_kind == "m1") == (self.m1_state is not None),
             "an m1 packet carries m1_state, an m15 packet does not"),
            (self.bar_close_epoch <= self.created_at_epoch < self.expires_at_epoch,
             "times must satisfy bar_close <= created_at < expires_at"),
            (ids == self.allowed.candidate_ids and len(set(ids)) == len(ids),
             "allowed.candidate_ids must list the distinct candidates, then the agent entry"),
            (events == self.allowed.event_ids, "allowed.event_ids must list the calendar events"),
            ((self.state == "position") == (self.position is not None),
             "state position needs exactly a position block"),
            ((self.state == "pending") == (self.pending_order is not None),
             "state pending needs exactly a pending_order block"),
            (self.state == "flat" or (not self.candidates
                                      and not self.limits.agent_entry_possible),
             "a management packet offers no entry"),
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
    """A version 1 or 2 answer to a flat packet: four views and a Chief.

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
    # The size the agent wants for an ENTER (limits.volume_min..max_lots; null = volume_min).
    lots: float | None = Field(default=None, gt=0)

    @property
    def withdrawn_ids(self) -> frozenset[str]:
        """For ProtocolInput.withdrawn_ids."""
        return frozenset(cid for cid, stance in self.rebuttal.items() if stance == "withdraw")


class DecisionTemplate(Frozen):
    """The ready-to-edit v3 decision of a served packet: HOLD, or KEEP when managing."""

    schema_version: Literal["v6.operator.decision.3"]
    packet_kind: PacketKind
    cycle_id: ItemId
    packet_hash: Hash
    agent: OperatorAgent
    action: DecisionAction
    views: OperatorViews | None
    entry_plan: None = None
    manage: ManageRequest | None = None
    m15_bias: M15Bias | None
    note: DecisionNote = ""


class OperatorPacket(OperatorPacketBody):
    """A served packet: the body, its hash and a ready-to-edit decision."""

    packet_hash: Hash
    decision_template: DecisionTemplate

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
UNCLEAR_BIAS: Final[M15Bias] = M15Bias(direction="unclear")


def _or_default(view: ViewT | None, default: ViewT) -> ViewT:
    return default if view is None else view


def baseline_or_defaults(body: OperatorPacketBody) -> OperatorViews:
    """The rules views of the packet, or cautious defaults where a rules desk failed."""
    base = body.baseline_views
    return OperatorViews(
        price_action=_or_default(base.price_action, ABSTAIN_VIEW),
        news_risk=_or_default(base.news_risk, UNKNOWN_NEWS_VIEW),
        liquidity=_or_default(base.liquidity, UNKNOWN_LIQUIDITY_VIEW),
        structure=_or_default(base.structure, UNKNOWN_STRUCTURE_VIEW))


def _keep(body: OperatorPacketBody) -> ManageRequest | None:
    """KEEP the managed trade, or None for a flat packet."""
    if body.position is not None:
        return ManageRequest(target="position", ticket=body.position.ticket, op="KEEP")
    if body.pending_order is not None:
        return ManageRequest(target="pending", ticket=body.pending_order.ticket, op="KEEP")
    return None


def decision_template(body: OperatorPacketBody, digest: str) -> DecisionTemplate:
    """HOLD (or KEEP when managing) for the first agent; an m15 template carries the rules
    views and the last bias, an m1 template neither (both may stay null, spec 2.1)."""
    manage = _keep(body)
    m15 = body.packet_kind == "m15"
    return DecisionTemplate(
        schema_version=DECISION_SCHEMA, packet_kind=body.packet_kind, cycle_id=body.cycle_id,
        packet_hash=digest, agent=body.allowed.agents[0],
        action="HOLD" if manage is None else "MANAGE",
        views=baseline_or_defaults(body) if m15 else None, manage=manage,
        m15_bias=(body.last_bias or UNCLEAR_BIAS) if m15 else None)


def seal_packet(body: OperatorPacketBody) -> OperatorPacket:
    """The packet to serve: `body` plus its hash and decision template, validated again."""
    document = body.model_dump(mode="json")
    digest = packet_hash(document)
    template = decision_template(body, digest).model_dump(mode="json")
    sealed = {**document, "packet_hash": digest, "decision_template": template}
    return OperatorPacket.model_validate_json(canonical_json(sealed))


# The v2 decision checks live in operator_checks; they are re-exported here, after every
# name they need is defined.
from .operator_checks import (  # noqa: E402
    OperatorDecisionError, decision_extras_problem, entry_plan_problem, parse_operator_decision,
)
