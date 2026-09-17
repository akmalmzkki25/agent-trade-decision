"""
Building blocks of the operator packet (`schemas.operator` re-exports all of them).

Everything here is strict, frozen and forbids unknown fields. Text that came
from outside (broker server name, gate details) is stripped of control
characters. The agent sees an equity band, never the balance or the login.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Final, Literal, get_args

from pydantic import (
    AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator,
)

from ..config import OperatorAgent
from ..cycle_codes import FEATURE_KEYS, MAX_OFFERED_CANDIDATES, SetupName
from ..market.sessions import MainWindowThird, Phase
from ..types import Side
from . import agents
from .agents import _printable as printable  # shared untrusted-text rule
from .agents import (
    MAX_EVENT_IDS, MAX_RANKED, ItemId, LiquidityView, NewsRiskView, PriceActionView,
    StructureView,
)

MAX_DECISION_BYTES: Final[int] = 64 * 1024
MAX_PACKET_M15_BARS: Final[int] = 12
MAX_PACKET_H1_BARS: Final[int] = 8
MAX_GATES: Final[int] = 32
MAX_EVENTS: Final[int] = 50
MAX_CODES: Final[int] = 10
MAX_FEATURES: Final[int] = 32
MAX_DETAIL_CHARS: Final[int] = 300
MAX_SERVER_CHARS: Final[int] = 80

EquityBand = Literal["lt_1k", "1k_2k", "2k_5k", "5k_10k", "10k_50k", "ge_50k"]
# (exclusive upper bound, band); equity at or above the last bound is TOP_EQUITY_BAND.
EQUITY_BANDS: Final[tuple[tuple[float, EquityBand], ...]] = (
    (1_000.0, "lt_1k"), (2_000.0, "1k_2k"), (5_000.0, "2k_5k"), (10_000.0, "5k_10k"),
    (50_000.0, "10k_50k"))
TOP_EQUITY_BAND: Final[EquityBand] = "ge_50k"
# Same spelling as deliberation.protocol.RebuttalStance (a test keeps them equal).
RebuttalStance = Literal["maintain", "withdraw"]

Hash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Epoch = Annotated[int, Field(ge=0)]
Price = Annotated[float, Field(gt=0)]
Code = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9_:.-]{1,48}$")]
FeatureName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,40}$")]
Detail = Annotated[str, StringConstraints(max_length=MAX_DETAIL_CHARS),
                   AfterValidator(printable)]
ServerName = Annotated[str, StringConstraints(min_length=1, max_length=MAX_SERVER_CHARS),
                       AfterValidator(printable)]
CompactBar = tuple[int, float, float, float, float]     # [open epoch, o, h, l, c]

ENUM_CHOICES: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType({
    "price_action.ranked.verdict": get_args(agents.CandidateVerdict),
    "price_action.ranked.reason_codes": get_args(agents.PriceActionReason),
    "news_risk.stance": get_args(agents.NewsStance),
    "news_risk.regime": get_args(agents.NewsRegime),
    "news_risk.reason_codes": get_args(agents.NewsReason),
    "liquidity.stance": get_args(agents.LiquidityStance),
    "liquidity.order_style": get_args(agents.OrderStylePreference),
    "liquidity.reason_codes": get_args(agents.LiquidityReason),
    "structure.regime": get_args(agents.StructureRegime),
    "structure.named_patterns": get_args(agents.NamedPattern),
    "structure.reason_codes": get_args(agents.StructureReason),
    "chief.action": get_args(agents.ChiefAction),
    "chief.risk_tier": get_args(agents.RiskTier),
    "chief.order_style": get_args(agents.OrderStyle),
    "chief.exit_profile": get_args(agents.ExitProfile),
    "rebuttal": get_args(RebuttalStance),
})
LIMIT_VALUES: Final[Mapping[str, int]] = MappingProxyType({
    "max_ranked": MAX_RANKED, "max_reason_codes": agents.MAX_REASON_CODES,
    "max_event_ids": MAX_EVENT_IDS, "max_named_patterns": agents.MAX_NAMED_PATTERNS,
    "max_note_chars": agents.MAX_NOTE_CHARS, "max_rationale_chars": agents.MAX_RATIONALE_CHARS,
    "max_dissent_chars": agents.MAX_DISSENT_CHARS, "max_view_bytes": agents.MAX_VIEW_JSON_BYTES,
    "max_decision_bytes": MAX_DECISION_BYTES,
})


def equity_band(equity: float) -> EquityBand:
    """The coarse band an agent sees instead of the account's equity."""
    for bound, band in EQUITY_BANDS:
        if equity < bound:
            return band
    return TOP_EQUITY_BAND


def canonical_json(value: object) -> bytes:
    """Sorted keys, no whitespace, ASCII only; NaN and infinities are refused."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


class Frozen(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, allow_inf_nan=False)


class PacketAccount(Frozen):
    trade_mode: Literal["DEMO"]
    server: ServerName
    equity_band: EquityBand


class PacketMarket(Frozen):
    """Latest quote and the tier-0 features (keys from cycle_codes.FEATURE_KEYS only)."""

    bid: Price
    ask: Price
    spread_points: int = Field(ge=0)
    atr_m5: Price | None
    atr_m15: Price | None
    atr_h1: Price | None
    features: dict[FeatureName, float] = Field(max_length=len(FEATURE_KEYS))

    @model_validator(mode="after")
    def _check(self) -> "PacketMarket":
        if self.ask < self.bid or not set(self.features) <= FEATURE_KEYS:
            raise ValueError("ask below bid, or a feature key outside FEATURE_KEYS")
        return self


class PacketSession(Frozen):
    """Market-hours state at the close, plus whether the daily session is armed."""

    phase: Phase
    main_window_third: MainWindowThird
    entries_allowed: bool
    continuation_allowed: bool
    block_reasons: tuple[Code, ...] = Field(max_length=MAX_CODES)
    armed: bool


class PacketBars(Frozen):
    M15: tuple[CompactBar, ...] = Field(max_length=MAX_PACKET_M15_BARS)
    H1: tuple[CompactBar, ...] = Field(max_length=MAX_PACKET_H1_BARS)


class PacketGate(Frozen):
    code: Code
    passed: bool
    value: float | str | None
    limit: float | str | None
    detail: Detail


class PacketEvent(Frozen):
    event_id: ItemId
    time_epoch: Epoch
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    importance: Literal["NONE", "LOW", "MODERATE", "HIGH"]
    code: Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]{1,60}$")]
    actual: float | None
    forecast: float | None
    previous: float | None


class PacketCalendar(Frozen):
    as_of_epoch: Epoch
    blackout: bool
    stale: bool
    codes: tuple[Code, ...] = Field(max_length=MAX_CODES)
    next_event_minutes: float | None
    last_event_minutes_ago: float | None
    events: tuple[PacketEvent, ...] = Field(max_length=MAX_EVENTS)


class CandidateExit(Frozen):
    sl: Price
    tp: Price
    stop_distance: Price
    reward_r: Price
    time_barrier_s: int = Field(gt=0)


class CandidateSizing(Frozen):
    """Indicative size at the standard tier (multiplier 1); the live size is recomputed."""

    lots: Price
    risk_usd: float = Field(ge=0)
    loss_per_lot: Price


class PacketCandidate(Frozen):
    candidate_id: ItemId
    setup: SetupName
    side: Side
    entry: Price
    invalidation: Price
    reason_codes: tuple[Code, ...] = Field(max_length=MAX_CODES)
    features: dict[FeatureName, float] = Field(max_length=MAX_FEATURES)
    exit: CandidateExit
    sizing: CandidateSizing | None
    sizing_refusal: tuple[Code, ...] = Field(max_length=MAX_CODES)

    @model_validator(mode="after")
    def _check(self) -> "PacketCandidate":
        sign = 1 if self.side == "buy" else -1
        if sign * (self.entry - self.exit.sl) <= 0 or sign * (self.exit.tp - self.entry) <= 0:
            raise ValueError("exit sl/tp lie on the wrong side of entry")
        if (self.sizing is None) == (not self.sizing_refusal):
            raise ValueError("give sizing or the codes that refused it, not both or neither")
        return self


class BaselineViews(Frozen):
    """The rules desks' views of this cycle (None where a rules desk failed)."""

    price_action: PriceActionView | None
    news_risk: NewsRiskView | None
    liquidity: LiquidityView | None
    structure: StructureView | None


class AllowedValues(Frozen):
    """What a decision may contain: agents, ids, enum values and size limits."""

    agents: tuple[OperatorAgent, ...] = Field(min_length=1)
    candidate_ids: tuple[ItemId, ...] = Field(min_length=1, max_length=MAX_OFFERED_CANDIDATES)
    event_ids: tuple[ItemId, ...] = Field(max_length=MAX_EVENTS)
    pa_min_conviction: float = Field(ge=0.0, le=1.0)
    enums: dict[str, tuple[str, ...]]
    limits: dict[str, int]


def allowed_values(*, operator_agents: tuple[str, ...], candidate_ids: tuple[str, ...],
                   event_ids: tuple[str, ...], pa_min_conviction: float) -> AllowedValues:
    """AllowedValues with the shared enum and limit tables filled in."""
    return AllowedValues.model_validate_json(canonical_json({
        "agents": list(operator_agents), "candidate_ids": list(candidate_ids),
        "event_ids": list(event_ids), "pa_min_conviction": pa_min_conviction,
        "enums": {key: list(values) for key, values in ENUM_CHOICES.items()},
        "limits": dict(LIMIT_VALUES)}))
