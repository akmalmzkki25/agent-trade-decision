"""
Output contracts for the V6 desks and the Chief (plan section 2).

An agent never emits a price, a lot size or a direction of its own: it can only
rank candidate ids the detectors offered, pick enum values and shrink risk.
Every model is strict, forbids unknown fields and rejects NaN. `validate_view`
is the single entry point for untrusted output (LLM text, operator JSON): it
caps the raw size, parses through JSON and then applies the semantic rules that
a JSON schema cannot express (ids must have been offered, ENTER needs a
candidate, and so on).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Set
from types import MappingProxyType
from typing import Annotated, Final, Literal, Union

from pydantic import (
    AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, ValidationError,
)

AgentRole = Literal["price_action", "news_risk", "liquidity", "structure", "chief"]
DeskRole = Literal["price_action", "news_risk", "liquidity", "structure"]
AGENT_ROLES: Final[tuple[AgentRole, ...]] = (
    "price_action", "news_risk", "liquidity", "structure", "chief")
DESK_ROLES: Final[tuple[DeskRole, ...]] = ("price_action", "news_risk", "liquidity", "structure")

MAX_VIEW_JSON_BYTES: Final[int] = 8 * 1024
MAX_REASON_CODES: Final[int] = 5
MAX_RANKED: Final[int] = 3
MAX_EVENT_IDS: Final[int] = 10
MAX_NAMED_PATTERNS: Final[int] = 5
MAX_NOTE_CHARS: Final[int] = 200
MAX_RATIONALE_CHARS: Final[int] = 300
MAX_DISSENT_CHARS: Final[int] = 200
ID_PATTERN: Final[str] = r"^[A-Za-z0-9._:-]{1,64}$"

# --- enums ------------------------------------------------------------------
CandidateVerdict = Literal["TAKE", "SKIP"]
NewsStance = Literal["CLEAR", "CAUTION", "BLOCK"]
LiquidityStance = Literal["OK", "CAUTION", "NO_TRADE"]
OrderStyle = Literal["LIMIT", "MARKET"]
OrderStylePreference = Literal["LIMIT", "MARKET", "EITHER"]
ChiefAction = Literal["ENTER", "HOLD"]
RiskTier = Literal["reduced", "standard"]
ExitProfile = Literal["STANDARD"]
NewsRegime = Literal["QUIET", "EVENT_RISK", "RISK_OFF", "RISK_ON", "USD_DRIVEN", "UNCLEAR"]
StructureRegime = Literal["TREND_UP", "TREND_DOWN", "RANGE", "TRANSITION", "VOLATILE", "UNCLEAR"]
NamedPattern = Literal[
    "DOUBLE_TOP", "DOUBLE_BOTTOM", "HEAD_SHOULDERS", "INV_HEAD_SHOULDERS", "TRIANGLE",
    "FLAG", "WEDGE", "CHANNEL", "RECTANGLE"]

PriceActionReason = Literal[
    "LEVEL_CONFLUENCE", "HTF_ALIGNED", "HTF_OPPOSED", "STRONG_DISPLACEMENT",
    "WEAK_DISPLACEMENT", "CLEAN_RETEST", "EXTENDED_MOVE", "CONFIRMED_CLOSE", "CHOPPY_CONTEXT",
    "POOR_REWARD_ROOM", "SESSION_TIMING_GOOD", "SESSION_TIMING_POOR", "FRICTION_HIGH",
    "STOP_TOO_WIDE", "NO_EDGE"]
NewsReason = Literal[
    "NO_EVENTS", "EVENT_IMMINENT", "EVENT_RECENT", "HIGH_IMPACT_USD", "SURPRISE_LARGE",
    "VOL_ELEVATED", "SAFE_HAVEN_FLOW", "CALENDAR_STALE", "HEADLINE_RISK", "DATA_MISSING"]
LiquidityReason = Literal[
    "SPREAD_NORMAL", "SPREAD_WIDE", "FRICTION_HIGH", "QUOTES_THIN", "QUOTE_GAP",
    "SLIPPAGE_HIGH", "DOM_SYNTHETIC", "ACTIVITY_HIGH", "ACTIVITY_LOW", "ROLLOVER_NEAR",
    "DATA_MISSING"]
StructureReason = Literal[
    "HH_HL_SEQUENCE", "LH_LL_SEQUENCE", "RANGE_BOUND", "EFFICIENT_TREND", "INEFFICIENT_CHOP",
    "MEAN_REVERTING", "MOMENTUM", "ADX_STRONG", "ADX_WEAK", "ATR_EXPANDING",
    "ATR_CONTRACTING", "NEAR_ROUND_NUMBER", "COUNTER_STRUCTURE", "DATA_MISSING"]

# --- validation error codes (ViewValidationError.code) ---------------------
VIEW_ERR_TOO_LARGE: Final[str] = "VIEW_TOO_LARGE"
VIEW_ERR_NOT_JSON: Final[str] = "VIEW_NOT_JSON"
VIEW_ERR_SCHEMA: Final[str] = "VIEW_SCHEMA"
VIEW_ERR_UNKNOWN_ROLE: Final[str] = "VIEW_UNKNOWN_ROLE"
VIEW_ERR_UNKNOWN_CANDIDATE: Final[str] = "VIEW_UNKNOWN_CANDIDATE"
VIEW_ERR_UNKNOWN_EVENT: Final[str] = "VIEW_UNKNOWN_EVENT"
VIEW_ERR_SEMANTIC: Final[str] = "VIEW_SEMANTIC"
_MAX_ERROR_DETAIL_CHARS: Final[int] = 300


def _printable(value: str) -> str:
    """Model text is untrusted: control characters never reach logs or the UI."""
    return "".join(ch for ch in value if ch.isprintable())


ItemId = Annotated[str, StringConstraints(pattern=ID_PATTERN)]
Note = Annotated[str, StringConstraints(max_length=MAX_NOTE_CHARS), AfterValidator(_printable)]
Rationale = Annotated[
    str, StringConstraints(max_length=MAX_RATIONALE_CHARS), AfterValidator(_printable)]
Dissent = Annotated[
    str, StringConstraints(max_length=MAX_DISSENT_CHARS), AfterValidator(_printable)]
UnitFloat = Annotated[float, Field(ge=0.0, le=1.0)]


class _View(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, allow_inf_nan=False)


class RankedCandidate(_View):
    candidate_id: ItemId
    verdict: CandidateVerdict
    conviction: UnitFloat
    reason_codes: tuple[PriceActionReason, ...] = Field(max_length=MAX_REASON_CODES)
    note: Note


class PriceActionView(_View):
    """The only source of direction, and only by choosing offered candidates."""

    abstain: bool
    ranked: tuple[RankedCandidate, ...] = Field(max_length=MAX_RANKED)


class NewsRiskView(_View):
    stance: NewsStance
    size_multiplier: UnitFloat
    regime: NewsRegime
    event_ids: tuple[ItemId, ...] = Field(max_length=MAX_EVENT_IDS)
    reason_codes: tuple[NewsReason, ...] = Field(max_length=MAX_REASON_CODES)
    note: Note


class LiquidityView(_View):
    stance: LiquidityStance
    size_multiplier: UnitFloat
    order_style: OrderStylePreference
    reason_codes: tuple[LiquidityReason, ...] = Field(max_length=MAX_REASON_CODES)
    note: Note


class StructureView(_View):
    """`named_patterns` carry weight 0: logged for later measurement only."""

    regime: StructureRegime
    counter_structure_veto: bool
    size_multiplier: UnitFloat
    named_patterns: tuple[NamedPattern, ...] = Field(max_length=MAX_NAMED_PATTERNS)
    reason_codes: tuple[StructureReason, ...] = Field(max_length=MAX_REASON_CODES)
    note: Note


class ChiefDecision(_View):
    """May only choose among PA's TAKE candidates and only lower risk."""

    action: ChiefAction
    candidate_id: ItemId | None
    risk_tier: RiskTier
    order_style: OrderStyle
    exit_profile: ExitProfile
    confidence: UnitFloat
    rationale: Rationale
    dissent: Dissent


AgentView = Union[PriceActionView, NewsRiskView, LiquidityView, StructureView, ChiefDecision]

VIEW_MODELS: Final[Mapping[AgentRole, type[BaseModel]]] = MappingProxyType({
    "price_action": PriceActionView, "news_risk": NewsRiskView, "liquidity": LiquidityView,
    "structure": StructureView, "chief": ChiefDecision,
})
SCHEMA_NAMES: Final[Mapping[AgentRole, str]] = MappingProxyType(
    {role: model.__name__ for role, model in VIEW_MODELS.items()})


class ViewValidationError(ValueError):
    """Untrusted agent output was refused. `code` is one of the VIEW_ERR_* constants."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail[:_MAX_ERROR_DETAIL_CHARS]}")
        self.code = code
        self.detail = detail[:_MAX_ERROR_DETAIL_CHARS]


def schema_name_for(role: str) -> str:
    return SCHEMA_NAMES[_require_role(role)]


def json_schema_for(role: str) -> dict[str, object]:
    """JSON schema of the role's output model (for strict structured-output requests)."""
    return VIEW_MODELS[_require_role(role)].model_json_schema()


def _require_role(role: str) -> AgentRole:
    if role not in VIEW_MODELS:
        raise ViewValidationError(VIEW_ERR_UNKNOWN_ROLE, f"unknown role {str(role)[:32]!r}")
    return role  # type: ignore[return-value]


def _raw_bytes(payload: str | bytes | Mapping[str, object]) -> bytes:
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    try:
        return json.dumps(dict(payload), allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ViewValidationError(VIEW_ERR_NOT_JSON, f"payload is not JSON-safe: {exc}") from exc


def _parse(model: type[BaseModel], raw: bytes) -> BaseModel:
    if len(raw) > MAX_VIEW_JSON_BYTES:
        raise ViewValidationError(
            VIEW_ERR_TOO_LARGE, f"{len(raw)} bytes exceeds {MAX_VIEW_JSON_BYTES}")
    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        kinds = {err["type"] for err in exc.errors()}
        code = VIEW_ERR_NOT_JSON if kinds == {"json_invalid"} else VIEW_ERR_SCHEMA
        locs = ", ".join(".".join(map(str, err["loc"])) for err in exc.errors()[:5])
        raise ViewValidationError(code, f"{model.__name__} invalid at: {locs}") from exc


def _require_offered(ids: tuple[str, ...], offered: Set[str], code: str, what: str) -> None:
    unknown = [item for item in ids if item not in offered]
    if unknown:
        raise ViewValidationError(code, f"{what} not offered: {unknown[:3]}")
    if len(set(ids)) != len(ids):
        raise ViewValidationError(VIEW_ERR_SEMANTIC, f"duplicate {what}")


def _check_price_action(view: PriceActionView, candidates: Set[str]) -> None:
    if view.abstain and view.ranked:
        raise ViewValidationError(VIEW_ERR_SEMANTIC, "abstain=true must rank nothing")
    if not view.abstain and not view.ranked:
        raise ViewValidationError(VIEW_ERR_SEMANTIC, "abstain=false must rank a candidate")
    ids = tuple(item.candidate_id for item in view.ranked)
    _require_offered(ids, candidates, VIEW_ERR_UNKNOWN_CANDIDATE, "candidate_id")


def _check_chief(view: ChiefDecision, candidates: Set[str]) -> None:
    if view.action == "ENTER" and view.candidate_id is None:
        raise ViewValidationError(VIEW_ERR_SEMANTIC, "ENTER requires candidate_id")
    if view.action == "HOLD" and view.candidate_id is not None:
        raise ViewValidationError(VIEW_ERR_SEMANTIC, "HOLD must not name a candidate_id")
    if view.candidate_id is not None:
        _require_offered((view.candidate_id,), candidates, VIEW_ERR_UNKNOWN_CANDIDATE,
                         "candidate_id")


def _check_semantics(view: BaseModel, candidates: Set[str], events: Set[str]) -> None:
    # Size multipliers are bounded to [0, 1] by the field type itself.
    if isinstance(view, PriceActionView):
        _check_price_action(view, candidates)
    elif isinstance(view, ChiefDecision):
        _check_chief(view, candidates)
    elif isinstance(view, NewsRiskView):
        _require_offered(view.event_ids, events, VIEW_ERR_UNKNOWN_EVENT, "event_id")


def validate_view(
    role: str,
    payload: str | bytes | Mapping[str, object],
    offered_candidate_ids: Set[str],
    offered_event_ids: Set[str] = frozenset(),
) -> AgentView:
    """Parse and check one agent's output; return the frozen view or raise.

    `payload` may be raw JSON text/bytes or a mapping (re-encoded as JSON, so both
    take the same strict path and the same 8 KB cap).

    Raises:
        ViewValidationError: with `code` in VIEW_ERR_*; never echoes the payload.
    """
    model = VIEW_MODELS[_require_role(role)]
    view = _parse(model, _raw_bytes(payload))
    _check_semantics(view, offered_candidate_ids, offered_event_ids)
    return view  # type: ignore[return-value]
