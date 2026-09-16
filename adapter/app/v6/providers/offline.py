"""
The deterministic `rules` backend (plan section 3.1): OfflineProvider.

It answers every role from the desks in `app.v6.desks` plus the rules Chief
below. It is always computed as the baseline an LLM has to beat, it is the
fallback for a failed desk, and it is the test fixture for the engine. Its
output goes through `validate_view` exactly like untrusted LLM output, so a bug
here surfaces as a failure result instead of an unchecked view.

Rules Chief: HOLD on any hard veto the protocol would enforce (calendar
blackout, stale feeds or any calendar code; news BLOCK; liquidity NO_TRADE;
counter-structure only when the structure veto is enforced); otherwise ENTER
the highest-conviction PA TAKE at or above `pa_min_conviction` (in enforce
mode, never one that trades against the confirmed swing). `risk_tier` is
reduced whenever a desk says CAUTION, a structure multiplier is below 1, or
the PA view is missing. Orders are always LIMIT. Missing news/liquidity/
structure views fall back to the rules view; a missing PA view is never
replaced (the protocol holds on it).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from functools import partial
from types import MappingProxyType
from typing import Final

from ..clock import Clock, SystemClock
from ..cycle_types import DeliberationInput, DeskViews, StructureVetoMode, input_from_packet
from ..desks import liquidity_view, news_risk_view, price_action_view, structure_view
from ..desks.liquidity import DEFAULT_MAX_SPREAD_POINTS, check_spread_ceiling
from ..schemas.agents import (
    MAX_DISSENT_CHARS, MAX_RATIONALE_CHARS, SCHEMA_NAMES, VIEW_ERR_TOO_LARGE,
    VIEW_ERR_UNKNOWN_CANDIDATE, VIEW_ERR_UNKNOWN_EVENT, VIEW_MODELS, AgentRole, AgentView,
    ChiefDecision, RankedCandidate, RiskTier, ViewValidationError, validate_view,
)
from ..types import Side
from .base import (
    ERR_BAD_REQUEST, ERR_DEADLINE_PASSED, ERR_EMPTY, ERR_INTERNAL, ERR_INVALID_OUTPUT,
    ERR_OUTPUT_TOO_LARGE, ERR_UNKNOWN_ID, ERR_UNSUPPORTED_ROLE, MS_PER_SECOND,
    RULES_PROVIDER_NAME, ProviderResult,
)

logger = logging.getLogger(__name__)

RULES_MODEL: Final[str] = "rules-v1"
DEFAULT_PA_MIN_CONVICTION: Final[float] = 0.6
STRUCTURE_VETO_MODES: Final[frozenset[str]] = frozenset({"log", "enforce"})
VETO_CONFIDENCE: Final[float] = 1.0
CONFIDENCE_DECIMALS: Final[int] = 2

VIEW_ERROR_CODES: Final[Mapping[str, str]] = MappingProxyType({
    VIEW_ERR_TOO_LARGE: ERR_OUTPUT_TOO_LARGE,
    VIEW_ERR_UNKNOWN_CANDIDATE: ERR_UNKNOWN_ID,
    VIEW_ERR_UNKNOWN_EVENT: ERR_UNKNOWN_ID,
})
_OPPOSED_SIDE: Final[Mapping[str, Side]] = MappingProxyType(
    {"HH_HL_SEQUENCE": "sell", "LH_LL_SEQUENCE": "buy"})


def view_error_code(exc: ViewValidationError) -> str:
    """Provider error code for a refused view (too large, unknown id, else invalid)."""
    return VIEW_ERROR_CODES.get(exc.code, ERR_INVALID_OUTPUT)


def _is_empty(payload: str | bytes | Mapping[str, object]) -> bool:
    if isinstance(payload, (str, bytes)):
        return not payload.strip()
    return not payload


def validated_result(role: AgentRole, payload: str | bytes | Mapping[str, object],
                     inputs: DeliberationInput, *, model: str, latency_ms: int = 0,
                     tokens_in: int = 0, tokens_out: int = 0,
                     cost_usd: float = 0.0) -> ProviderResult:
    """Validate untrusted output against the offered ids; never raises on bad output."""
    failure = partial(ProviderResult.failure, model=model, latency_ms=latency_ms,
                      tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost_usd)
    if _is_empty(payload):
        return failure(ERR_EMPTY)
    try:
        view = validate_view(role, payload, inputs.offered_candidate_ids,
                             inputs.offered_event_ids)
    except ViewValidationError as exc:
        logger.warning("v6 %s view from %s refused: %s", role, model[:64], exc.code)
        return failure(view_error_code(exc))
    return ProviderResult.success(view, model=model, latency_ms=latency_ms,
                                  tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost_usd)


def check_chief_settings(pa_min_conviction: float, structure_veto: str) -> None:
    """Raise ValueError for a conviction outside [0, 1] or an unknown veto mode."""
    is_number = isinstance(pa_min_conviction, (int, float)) and not isinstance(
        pa_min_conviction, bool)
    if not is_number or not 0.0 <= pa_min_conviction <= 1.0:
        raise ValueError("pa_min_conviction must be a number in [0, 1]")
    if structure_veto not in STRUCTURE_VETO_MODES:
        raise ValueError("structure_veto must be 'log' or 'enforce'")


# --- rules views --------------------------------------------------------------

def rules_desk_views(inputs: DeliberationInput, *,
                     max_spread_points: int = DEFAULT_MAX_SPREAD_POINTS) -> DeskViews:
    """All four rules desk views for one cycle."""
    context = inputs.context
    return DeskViews(
        price_action=price_action_view(context, inputs.offered),
        news_risk=news_risk_view(context),
        liquidity=liquidity_view(context, max_spread_points=max_spread_points),
        structure=structure_view(context, inputs.offered),
    )


def _effective_views(inputs: DeliberationInput, max_spread_points: int) -> DeskViews:
    given, context = inputs.views, inputs.context
    return DeskViews(
        price_action=given.price_action,
        news_risk=(news_risk_view(context) if given.news_risk is None else given.news_risk),
        liquidity=(liquidity_view(context, max_spread_points=max_spread_points)
                   if given.liquidity is None else given.liquidity),
        structure=(structure_view(context, inputs.offered)
                   if given.structure is None else given.structure),
    )


def _vetoes(inputs: DeliberationInput, views: DeskViews,
            structure_veto: StructureVetoMode) -> tuple[str, ...]:
    calendar = inputs.context.calendar
    checks = (
        (calendar.blackout or calendar.stale or bool(calendar.codes), "calendar veto"),
        (views.news_risk is not None and views.news_risk.stance == "BLOCK", "news BLOCK"),
        (views.liquidity is not None and views.liquidity.stance == "NO_TRADE",
         "liquidity NO_TRADE"),
        (structure_veto == "enforce" and views.structure is not None
         and views.structure.counter_structure_veto, "counter-structure"),
    )
    return tuple(label for vetoed, label in checks if vetoed)


def _cautions(views: DeskViews) -> tuple[str, ...]:
    structure = views.structure
    checks = (
        (views.price_action is None, "price action view missing"),
        (views.news_risk is not None and views.news_risk.stance == "CAUTION", "news CAUTION"),
        (views.liquidity is not None and views.liquidity.stance == "CAUTION",
         "liquidity CAUTION"),
        (structure is not None and structure.size_multiplier < 1.0,
         f"structure {structure.regime}" if structure is not None else ""),
    )
    return tuple(label for cautious, label in checks if cautious)


def _excluded_sides(views: DeskViews, structure_veto: StructureVetoMode) -> frozenset[str]:
    if structure_veto != "enforce" or views.structure is None:
        return frozenset()
    codes = views.structure.reason_codes
    return frozenset(side for code, side in _OPPOSED_SIDE.items() if code in codes)


def _takes(inputs: DeliberationInput, views: DeskViews,
           excluded: frozenset[str]) -> Sequence[RankedCandidate]:
    if views.price_action is None:
        return ()
    sides = {item.candidate.candidate_id: item.candidate.side for item in inputs.offered}
    return tuple(
        item for item in views.price_action.ranked
        if item.verdict == "TAKE" and item.candidate_id in sides
        and sides[item.candidate_id] not in excluded)


def _hold(tier: RiskTier, confidence: float, rationale: str, dissent: str) -> ChiefDecision:
    return ChiefDecision(
        action="HOLD", candidate_id=None, risk_tier=tier, order_style="LIMIT",
        exit_profile="STANDARD", confidence=round(confidence, CONFIDENCE_DECIMALS),
        rationale=rationale[:MAX_RATIONALE_CHARS], dissent=dissent[:MAX_DISSENT_CHARS])


def rules_chief_decision(inputs: DeliberationInput, *,
                         pa_min_conviction: float = DEFAULT_PA_MIN_CONVICTION,
                         structure_veto: StructureVetoMode = "log",
                         max_spread_points: int = DEFAULT_MAX_SPREAD_POINTS) -> ChiefDecision:
    """The rules Chief for one cycle (see the module docstring)."""
    check_chief_settings(pa_min_conviction, structure_veto)
    views = _effective_views(inputs, max_spread_points)
    cautions = _cautions(views)
    tier: RiskTier = "reduced" if cautions else "standard"
    dissent = "; ".join(cautions)
    vetoes = _vetoes(inputs, views, structure_veto)
    if vetoes:
        return _hold(tier, VETO_CONFIDENCE, "veto: " + ", ".join(vetoes), dissent)
    takes = _takes(inputs, views, _excluded_sides(views, structure_veto))
    best = max(takes, key=lambda item: item.conviction, default=None)
    if best is None or best.conviction < pa_min_conviction:
        top = 0.0 if best is None else best.conviction
        return _hold(tier, 1.0 - top,
                     f"no PA TAKE at or above conviction {pa_min_conviction:.2f}", dissent)
    return ChiefDecision(
        action="ENTER", candidate_id=best.candidate_id, risk_tier=tier, order_style="LIMIT",
        exit_profile="STANDARD", confidence=best.conviction,
        rationale=(f"ENTER {best.candidate_id}: PA TAKE {best.conviction:.2f}, "
                   f"no veto, tier {tier}")[:MAX_RATIONALE_CHARS],
        dissent=dissent[:MAX_DISSENT_CHARS])


# --- provider -------------------------------------------------------------------

class OfflineProvider:
    """AgentProvider for the `rules` backend. `ask` never raises."""

    name: str = RULES_PROVIDER_NAME

    def __init__(self, *, clock: Clock | None = None,
                 pa_min_conviction: float = DEFAULT_PA_MIN_CONVICTION,
                 structure_veto: StructureVetoMode = "log",
                 max_spread_points: int = DEFAULT_MAX_SPREAD_POINTS) -> None:
        check_chief_settings(pa_min_conviction, structure_veto)
        check_spread_ceiling(max_spread_points)
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._pa_min_conviction = float(pa_min_conviction)
        self._structure_veto: StructureVetoMode = structure_veto
        self._max_spread_points = max_spread_points

    def view_for(self, role: AgentRole, inputs: DeliberationInput) -> AgentView:
        """The rules view for `role` (synchronous; raises on bad input)."""
        context = inputs.context
        if role == "price_action":
            return price_action_view(context, inputs.offered)
        if role == "news_risk":
            return news_risk_view(context)
        if role == "liquidity":
            return liquidity_view(context, max_spread_points=self._max_spread_points)
        if role == "structure":
            return structure_view(context, inputs.offered)
        if role == "chief":
            return rules_chief_decision(
                inputs, pa_min_conviction=self._pa_min_conviction,
                structure_veto=self._structure_veto, max_spread_points=self._max_spread_points)
        raise ValueError("unsupported role")

    def desk_views(self, inputs: DeliberationInput) -> DeskViews:
        return rules_desk_views(inputs, max_spread_points=self._max_spread_points)

    async def ask(self, role: AgentRole, packet: Mapping[str, object], schema_name: str,
                  deadline_epoch: float) -> ProviderResult:
        started: float | None = None
        try:
            started = self._clock.now_epoch()
            return self._answer(role, packet, schema_name, deadline_epoch, started)
        except Exception as exc:  # noqa: BLE001 - the contract is "never raise"
            label = role[:32] if isinstance(role, str) else type(role).__name__
            logger.error("v6 rules provider raised %s on role %s", type(exc).__name__, label)
            return ProviderResult.failure(ERR_INTERNAL, model=RULES_MODEL,
                                          latency_ms=self._elapsed_ms(started))

    def _elapsed_ms(self, started: float | None) -> int:
        if started is None:
            return 0
        try:
            return max(0, int((self._clock.now_epoch() - started) * MS_PER_SECOND))
        except Exception:  # noqa: BLE001 - latency is informational only
            return 0

    def _answer(self, role: AgentRole, packet: Mapping[str, object], schema_name: str,
                deadline_epoch: float, started: float) -> ProviderResult:
        if not isinstance(role, str) or role not in VIEW_MODELS:
            return ProviderResult.failure(ERR_UNSUPPORTED_ROLE, model=RULES_MODEL)
        if not deadline_epoch > started:
            return ProviderResult.failure(ERR_DEADLINE_PASSED, model=RULES_MODEL)
        if schema_name != SCHEMA_NAMES[role] or not isinstance(packet, Mapping):
            return ProviderResult.failure(ERR_BAD_REQUEST, model=RULES_MODEL)
        try:
            inputs = input_from_packet(packet)
        except TypeError:
            return ProviderResult.failure(ERR_BAD_REQUEST, model=RULES_MODEL)
        view = self.view_for(role, inputs)
        return validated_result(role, view.model_dump(mode="json"), inputs, model=RULES_MODEL,
                                latency_ms=self._elapsed_ms(started))
