"""
Immutable records for one deliberation cycle (one closed M15 bar).

Flow: snapshot -> MarketContext -> gates / candidates -> DeliberationInput -> views ->
ProtocolInput -> ProtocolDecision -> exits / sizing -> CycleResult. An approved trade
becomes a ShadowIntent (the sized order); only the runtime publishes it (execute mode,
recorded as `CycleResult.intent_id`). `cycle_codes` is re-exported here.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Literal

from pydantic import BaseModel

from .cycle_codes import *  # noqa: F403 - re-export the shared code sets
from .cycle_codes import (
    ENTER_STATUSES, MAX_OFFERED_CANDIDATES, VETO_CODES, CandidateVerdictLabel, CycleStatus,
    HoldReason,
)
from .market.sessions import SessionState
from .providers.base import MS_PER_SECOND, ProviderResult
from .schemas.agents import (
    AgentRole, AgentView, ChiefDecision, ChiefAction, ExitProfile, LiquidityView, NewsRiskView,
    OrderStyle, PriceActionView, RiskTier, StructureView,
)
from .schemas.snapshot import (
    AccountBlock, DayBlock, EaStateBlock, PendingOrderBlock, PositionBlock, ProbeBlock,
    QuoteBlock, TickStatsBlock, V6Snapshot,
)
from .types import (
    TIMEFRAME_SECONDS, Bar, Candidate, ExitPlan, GateResult, Refusal, Side, SizingResult,
    SymbolSpec, Timeframe,
)

CalendarSource = Literal["mt5", "forexfactory", "static"]
Importance = Literal["NONE", "LOW", "MODERATE", "HIGH"]
ShadowOrderType = Literal["BUY_LIMIT", "SELL_LIMIT", "BUY", "SELL"]
StructureVetoMode = Literal["log", "enforce"]
# In-process providers (rules, scripted) read the typed input under PACKET_INPUT_KEY;
# an operator agent receives a `schemas.operator.OperatorPacket` instead.
PACKET_INPUT_KEY: Final[str] = "deliberation_input"
PUBLISHED_STATUS: Final[CycleStatus] = "ENTER"     # the intent went out to the EA
# A context closes an M15 bar, or the M1 bar of an m1 cycle (phase B).
CONTEXT_BAR_SECONDS: Final[frozenset[int]] = frozenset({
    TIMEFRAME_SECONDS["M15"], TIMEFRAME_SECONDS["M1"]})


@dataclass(frozen=True)
class CalendarEvent:
    """One scheduled release, reduced to ids, enums, times and numbers."""

    event_id: str            # "<source>:<id>", unique across sources, matches ID_PATTERN
    source: CalendarSource
    time_epoch: int
    currency: str
    importance: Importance
    code: str                # slug, e.g. "nonfarm-payrolls"
    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None


@dataclass(frozen=True)
class CalendarAssessment:
    """Code-computed news veto; applies even when every LLM is down."""

    as_of_epoch: int
    blackout: bool
    codes: tuple[str, ...]                 # CAL_* codes
    next_event_minutes: float | None
    last_event_minutes_ago: float | None
    stale: bool
    events: tuple[CalendarEvent, ...] = ()  # the events offered to the news desk

    @property
    def event_ids(self) -> frozenset[str]:
        return frozenset(event.event_id for event in self.events)


@dataclass(frozen=True)
class MarketContext:
    """Everything tier 0 knows at `as_of_epoch` (the M15 close, or the M1 close of an m1
    cycle). Only closed bars."""

    cycle_id: str
    snapshot_id: str
    symbol: str
    bar_open_epoch: int
    as_of_epoch: int
    sent_at_epoch: int
    received_at: float
    server_gmt_offset_s: int
    trade_mode: str
    server: str
    account: AccountBlock
    spec: SymbolSpec
    margin_per_lot_buy: float
    margin_per_lot_sell: float
    quote: QuoteBlock
    ticks: TickStatsBlock
    day: DayBlock
    ea_state: EaStateBlock
    probe: ProbeBlock | None
    positions: tuple[PositionBlock, ...]
    pending_orders: tuple[PendingOrderBlock, ...]
    bars: Mapping[Timeframe, tuple[Bar, ...]]
    session: SessionState
    calendar: CalendarAssessment
    features: Mapping[str, float]          # keys: F_* in cycle_codes

    def __post_init__(self) -> None:
        bars = {tf: tuple(rows) for tf, rows in self.bars.items()}
        object.__setattr__(self, "bars", MappingProxyType(bars))
        object.__setattr__(self, "features", MappingProxyType(dict(self.features)))
        if self.as_of_epoch - self.bar_open_epoch not in CONTEXT_BAR_SECONDS:
            raise ValueError("as_of_epoch must be the M15 bar close "
                             "(or the M1 bar close of an m1 cycle)")
        for tf, rows in self.bars.items():
            if rows and rows[-1].t + TIMEFRAME_SECONDS[tf] > self.as_of_epoch:
                raise ValueError(f"{tf}: bar at {rows[-1].t} is not closed at as_of_epoch")
        if any(not math.isfinite(value) for value in self.features.values()):
            raise ValueError("features must be finite; omit unavailable ones")

    @property
    def spread_price(self) -> float:
        return self.quote.ask - self.quote.bid

    @property
    def mid(self) -> float:
        return (self.quote.ask + self.quote.bid) / 2

    @classmethod
    def from_snapshot(cls, snapshot: V6Snapshot, *, cycle_id: str, received_at: float,
                      bars: Mapping[Timeframe, tuple[Bar, ...]], session: SessionState,
                      calendar: CalendarAssessment, features: Mapping[str, float],
                      probe: ProbeBlock | None = None) -> "MarketContext":
        """`bars`: merged BarStore view; `probe`: cached probe for snapshots without one."""
        spec = snapshot.symbol_spec
        return cls(
            cycle_id=cycle_id, snapshot_id=snapshot.snapshot_id, symbol=snapshot.symbol,
            bar_open_epoch=snapshot.bar_open_epoch,
            as_of_epoch=snapshot.bar_open_epoch + TIMEFRAME_SECONDS["M15"],
            sent_at_epoch=snapshot.sent_at_epoch, received_at=received_at,
            server_gmt_offset_s=snapshot.server_gmt_offset_s,
            trade_mode=snapshot.account.trade_mode, server=snapshot.account.server,
            account=snapshot.account, spec=spec.to_spec(),
            margin_per_lot_buy=spec.margin_per_lot_buy,
            margin_per_lot_sell=spec.margin_per_lot_sell, quote=snapshot.quote,
            ticks=snapshot.ticks, day=snapshot.day, ea_state=snapshot.ea_state,
            probe=snapshot.probe if snapshot.probe is not None else probe,
            positions=tuple(snapshot.positions), pending_orders=tuple(snapshot.pending_orders),
            bars=bars, session=session, calendar=calendar, features=features,
        )


@dataclass(frozen=True)
class DeskViews:
    """Effective views after fallback: a missing LLM desk is replaced by its rules view."""

    price_action: PriceActionView | None = None
    news_risk: NewsRiskView | None = None
    liquidity: LiquidityView | None = None
    structure: StructureView | None = None


@dataclass(frozen=True)
class ViewRecord:
    """One attempt to obtain a role's view; one v6_agent_views row."""

    role: AgentRole
    source: str               # "rules" or the provider name
    view: AgentView | None
    error_code: str = ""
    latency_ms: int = 0
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0

    def __post_init__(self) -> None:
        if (self.view is None) == (self.error_code == ""):
            raise ValueError("ViewRecord needs exactly one of view or error_code")

    @classmethod
    def from_result(cls, role: AgentRole, source: str, result: ProviderResult) -> "ViewRecord":
        return cls(
            role=role, source=source, view=result.view, error_code=result.error_code,
            latency_ms=result.latency_ms, model=result.model, tokens_in=result.tokens_in,
            tokens_out=result.tokens_out, cost_usd=result.cost_usd)


@dataclass(frozen=True)
class CandidateAssessment:
    """A detected candidate plus everything the labeler needs.

    `stop` / `target` are the label geometry and always set: the exit plan's
    levels when it was accepted, otherwise the raw structural stop and the
    configured R multiple. `available_from` is when the triple barrier has
    certainly resolved (bar close + pending expiry + time barrier).
    """

    candidate: Candidate
    verdict: CandidateVerdictLabel
    stop: float
    target: float
    available_from: int
    exit_plan: ExitPlan | None = None
    refusal: Refusal | None = None     # exit or sizing refusal, if any

    def __post_init__(self) -> None:
        if not all(math.isfinite(v) and v > 0 for v in (self.stop, self.target)):
            raise ValueError("stop and target must be finite and positive")
        if self.available_from <= self.candidate.bar_t:
            raise ValueError("available_from must be after the candidate bar")


@dataclass(frozen=True)
class DeliberationInput:
    """What every role sees; desks get empty `views`, the Chief the effective ones."""

    context: MarketContext
    gates: tuple[GateResult, ...]
    offered: tuple[CandidateAssessment, ...]
    views: DeskViews = DeskViews()

    def __post_init__(self) -> None:
        ids = [item.candidate.candidate_id for item in self.offered]
        if len(ids) > MAX_OFFERED_CANDIDATES or len(set(ids)) != len(ids):
            raise ValueError(f"offer at most {MAX_OFFERED_CANDIDATES} distinct candidates")
        if any(item.exit_plan is None for item in self.offered):
            raise ValueError("only candidates with an accepted exit plan can be offered")

    @property
    def offered_candidate_ids(self) -> frozenset[str]:
        return frozenset(item.candidate.candidate_id for item in self.offered)

    @property
    def offered_event_ids(self) -> frozenset[str]:
        return self.context.calendar.event_ids


def rules_packet(inputs: DeliberationInput) -> Mapping[str, object]:
    """The packet an in-process provider receives (Phase 2)."""
    return MappingProxyType({PACKET_INPUT_KEY: inputs})


def input_from_packet(packet: Mapping[str, object]) -> DeliberationInput:
    value = packet.get(PACKET_INPUT_KEY)
    if not isinstance(value, DeliberationInput):
        raise TypeError("packet carries no DeliberationInput")
    return value


@dataclass(frozen=True)
class ProtocolInput:
    """Everything `deliberation.protocol.resolve` needs; no market data."""

    gates: tuple[GateResult, ...]
    calendar: CalendarAssessment
    offered_ids: frozenset[str]
    views: DeskViews                       # effective (after rules fallback)
    decision: ChiefDecision | None         # None: Chief missing or invalid
    pa_min_conviction: float
    structure_veto: StructureVetoMode
    withdrawn_ids: frozenset[str] = frozenset()   # PA withdrawals in R2 (operator rebuttal)


@dataclass(frozen=True)
class ProtocolDecision:
    """Outcome of the resolution rules. `size_multiplier` already includes the tier factor."""

    action: ChiefAction
    hold_reason: HoldReason | None
    candidate_id: str | None
    size_multiplier: float
    risk_tier: RiskTier = "reduced"
    order_style: OrderStyle = "LIMIT"
    exit_profile: ExitProfile = "STANDARD"
    vetoes: tuple[str, ...] = ()           # VETO_* codes observed, enforced or logged
    detail: str = ""

    def __post_init__(self) -> None:
        if not (math.isfinite(self.size_multiplier) and 0 <= self.size_multiplier <= 1):
            raise ValueError("size_multiplier must be within [0, 1]")
        if not set(self.vetoes) <= VETO_CODES:
            raise ValueError("unknown veto code")
        if self.action == "ENTER" and (self.hold_reason is not None or self.candidate_id is None):
            raise ValueError("ENTER needs a candidate_id and no hold_reason")
        if self.action == "HOLD" and self.hold_reason is None:
            raise ValueError("HOLD needs a hold_reason")


@dataclass(frozen=True)
class ShadowIntent:
    """The sized order of an ENTER decision; the runtime alone may publish it (execute)."""

    cycle_id: str
    candidate_id: str
    setup: str
    side: Side
    order_type: ShadowOrderType
    entry: float
    sl: float
    tp: float
    lots: float
    risk_usd: float
    risk_budget_usd: float
    size_multiplier: float
    risk_tier: RiskTier
    exit_profile: ExitProfile
    time_barrier_s: int
    valid_until_epoch: int
    source: str                        # "rules" or the provider name
    labels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0 <= self.size_multiplier <= 1:
            raise ValueError("size_multiplier must be within [0, 1]")
        sign = 1 if self.side == "buy" else -1
        if sign * (self.entry - self.sl) <= 0 or sign * (self.tp - self.entry) <= 0:
            raise ValueError("shadow intent SL/TP are on the wrong side of entry")
        if self.lots <= 0 or self.risk_usd > self.risk_budget_usd:
            raise ValueError("shadow intent violates the lots or risk invariant")


@dataclass(frozen=True)
class CycleTimings:
    started_at: float
    finished_at: float
    tier0_ms: int = 0
    deliberation_ms: int = 0

    @property
    def total_ms(self) -> int:
        return max(0, int((self.finished_at - self.started_at) * MS_PER_SECOND))


@dataclass(frozen=True)
class CycleResult:
    """The full, immutable record of one cycle; the ledger stores it as-is."""

    cycle_id: str
    snapshot_id: str
    bar_open_epoch: int
    status: CycleStatus
    hold_reason: HoldReason | None
    backend: str                       # configured V6_BACKEND
    provider: str                      # provider that produced the used views
    provider_status: str               # PROVIDER_STATUS_* in providers.base
    timings: CycleTimings
    hold_detail: str = ""
    session_id: str | None = None
    gates: tuple[GateResult, ...] = ()
    candidates: tuple[CandidateAssessment, ...] = ()   # every detected candidate
    views: DeskViews = DeskViews()
    view_records: tuple[ViewRecord, ...] = ()
    decision: ChiefDecision | None = None
    protocol: ProtocolDecision | None = None
    exit_plan: ExitPlan | None = None
    sizing: SizingResult | None = None
    refusal: Refusal | None = None
    shadow_intent: ShadowIntent | None = None
    intent_id: str | None = None       # the published intent (status ENTER only)

    def __post_init__(self) -> None:
        if self.status in ENTER_STATUSES:
            if self.shadow_intent is None or self.hold_reason is not None:
                raise ValueError(f"{self.status} needs a shadow_intent and no hold_reason")
        elif self.shadow_intent is not None or self.hold_reason is None:
            raise ValueError(f"{self.status} needs a hold_reason and no shadow_intent")
        if self.intent_id is not None and self.status != PUBLISHED_STATUS:
            raise ValueError("only an ENTER cycle names a published intent")
        if len({item.candidate.candidate_id for item in self.candidates}) != len(self.candidates):
            raise ValueError("candidate ids must be unique within a cycle")

    @property
    def failed_gates(self) -> tuple[GateResult, ...]:
        return tuple(gate for gate in self.gates if not gate.passed)

    def to_summary(self) -> dict[str, object]:
        """JSON-safe dict of the whole result (for v6_cycles.summary_json)."""
        return _jsonable(self)  # type: ignore[return-value]


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, StrEnum):
        return str(value)
    return value
