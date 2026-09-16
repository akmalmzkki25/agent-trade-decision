"""
Resolution rules of the V6 panel (plan section 2, "Aturan resolusi").

Pure and deterministic: no I/O, no clock, no market data, no logging. Agents
only contribute enums, bounded multipliers and ids of candidates the detectors
offered, so the outcome names a candidate and never a direction, price or lot
size: the caller always takes the side from the offered candidate. Rules, in
evaluation order:

1. A failed hard gate holds (reason from `hold_reason_for_gates`, i.e.
   APP-V6-GATE unless the gate has a dedicated reason); no view is read.
2. A missing or inconsistent Price Action view or Chief decision holds
   (INVALID_VIEW). A missing risk desk (news, liquidity, structure) is replaced
   by the caller's deterministic view; without one the cycle holds as well.
3. Any enforced veto holds (VETO): calendar blackout, stale feed or any
   calendar code; news BLOCK; liquidity NO_TRADE; counter-structure when
   `structure_veto == "enforce"`. In "log" mode the counter-structure veto is
   recorded as VETO_STRUCTURE_LOGGED and not enforced.
4. ENTER needs the Chief's pick to be a PA TAKE that was not withdrawn in the
   rebuttal round (CHIEF_HOLD / NO_TAKE) with conviction >= `pa_min_conviction`
   (LOW_CONVICTION).
5. m = min(news, liquidity, structure multipliers) x REDUCED_TIER_FACTOR for
   the reduced tier; each multiplier is clamped to [0, 1], so m is too.
   m < MIN_COMBINED_MULTIPLIER holds (LOW_MULTIPLIER).
6. A liquidity LIMIT preference turns the Chief's MARKET into LIMIT.

Values that bypassed model validation (`model_construct`, `model_copy`) fail
closed: an unknown stance vetoes, a non-numeric or non-finite multiplier or
conviction counts as 0, an unknown tier is "reduced" and an unknown order
style is LIMIT.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Final, Literal, TypeVar, cast

from ..cycle_codes import (
    MIN_COMBINED_MULTIPLIER, REDUCED_TIER_FACTOR, VETO_CALENDAR, VETO_CODES, VETO_LIQUIDITY,
    VETO_NEWS, VETO_STRUCTURE, VETO_STRUCTURE_LOGGED, HoldReason, hold_reason_for_gates,
)
from ..cycle_types import CalendarAssessment, DeskViews, ProtocolDecision, ProtocolInput
from ..schemas.agents import (
    ChiefAction, ChiefDecision, ExitProfile, LiquidityView, NewsRiskView, OrderStyle,
    PriceActionView, RankedCandidate, RiskTier, StructureView,
)
from ..types import GateResult

__all__ = [
    "RebuttalStance", "REBUTTAL_MAINTAIN", "REBUTTAL_WITHDRAW", "REBUTTAL_STANCES",
    "STRUCTURE_VETO_MODES", "RULE_GATES", "RULE_VIEWS", "RULE_VETOES", "RULE_TAKE",
    "RULE_MULTIPLIER", "RULE_ORDER_STYLE", "RULE_CODES", "MARK_FALLBACK_PREFIX",
    "MARK_STRUCTURE_LOGGED", "MARK_REDUCED_TIER", "MARK_LIMIT_OVERRIDE", "Resolution",
    "resolve", "resolve_detailed",
]

# --- rebuttal round (R2) -------------------------------------------------------------
RebuttalStance = Literal["maintain", "withdraw"]
REBUTTAL_MAINTAIN: Final[str] = "maintain"
REBUTTAL_WITHDRAW: Final[str] = "withdraw"
REBUTTAL_STANCES: Final[frozenset[str]] = frozenset({REBUTTAL_MAINTAIN, REBUTTAL_WITHDRAW})

STRUCTURE_VETO_LOG: Final[str] = "log"
STRUCTURE_VETO_ENFORCE: Final[str] = "enforce"
STRUCTURE_VETO_MODES: Final[frozenset[str]] = frozenset(
    {STRUCTURE_VETO_LOG, STRUCTURE_VETO_ENFORCE})

# --- audit trail: every rule evaluated, in order, plus markers for modifiers ---------
RULE_GATES: Final[str] = "R1_GATES"
RULE_VIEWS: Final[str] = "R2_VIEWS"
RULE_VETOES: Final[str] = "R3_VETOES"
RULE_TAKE: Final[str] = "R4_TAKE"
RULE_MULTIPLIER: Final[str] = "R5_MULTIPLIER"
RULE_ORDER_STYLE: Final[str] = "R6_ORDER_STYLE"
RULE_CODES: Final[tuple[str, ...]] = (
    RULE_GATES, RULE_VIEWS, RULE_VETOES, RULE_TAKE, RULE_MULTIPLIER, RULE_ORDER_STYLE)
MARK_FALLBACK_PREFIX: Final[str] = "R2_FALLBACK:"          # + desk role
MARK_STRUCTURE_LOGGED: Final[str] = "R3_STRUCTURE_VETO_LOGGED"
MARK_REDUCED_TIER: Final[str] = "R5_REDUCED_TIER"
MARK_LIMIT_OVERRIDE: Final[str] = "R6_LIMIT_OVERRIDE"

_NEWS_OPEN: Final[frozenset[str]] = frozenset({"CLEAR", "CAUTION"})
_LIQUIDITY_OPEN: Final[frozenset[str]] = frozenset({"OK", "CAUTION"})
_MARKET_ALLOWED: Final[frozenset[str]] = frozenset({"MARKET", "EITHER"})
_MIN_UNIT: Final[float] = 0.0
_MAX_UNIT: Final[float] = 1.0
_STANDARD_TIER_FACTOR: Final[float] = 1.0
_MAX_DETAIL_CHARS: Final[int] = 200

_DeskT = TypeVar("_DeskT", NewsRiskView, LiquidityView, StructureView)


@dataclass(frozen=True)
class Resolution:
    """Protocol outcome plus its audit trail. Carries no side, price or lot size.

    `candidate_id` is the Chief's ENTER pick (also on a later HOLD, so the
    labeler can mark it "chosen"); it is None for gate, invalid-view and Chief
    holds. `size_multiplier` includes the tier factor and is 0 for holds
    decided before rule 3.
    """

    action: ChiefAction
    hold_reason: HoldReason | None
    candidate_id: str | None
    size_multiplier: float
    order_style: OrderStyle = "LIMIT"
    risk_tier: RiskTier = "reduced"
    exit_profile: ExitProfile = "STANDARD"
    applied_rules: tuple[str, ...] = ()
    enforced_vetoes: tuple[str, ...] = ()
    logged_vetoes: tuple[str, ...] = ()
    detail: str = ""

    def __post_init__(self) -> None:
        enforced, logged = set(self.enforced_vetoes), set(self.logged_vetoes)
        if not (enforced | logged) <= VETO_CODES or enforced & logged:
            raise ValueError("vetoes must be known codes, each either enforced or logged")
        if self.action == "ENTER" and (
                enforced or not self.size_multiplier >= MIN_COMBINED_MULTIPLIER):
            raise ValueError("ENTER needs no enforced veto and a sufficient multiplier")
        self.to_decision()  # shared ENTER/HOLD and [0, 1] invariants

    @property
    def vetoes(self) -> tuple[str, ...]:
        """Every veto observed: enforced first, then logged."""
        return self.enforced_vetoes + self.logged_vetoes

    def to_decision(self) -> ProtocolDecision:
        return ProtocolDecision(
            action=self.action, hold_reason=self.hold_reason, candidate_id=self.candidate_id,
            size_multiplier=self.size_multiplier, risk_tier=self.risk_tier,
            order_style=self.order_style, exit_profile=self.exit_profile,
            vetoes=self.vetoes, detail=self.detail)


@dataclass(frozen=True)
class _Panel:
    """The validated views the rules act on."""

    price_action: PriceActionView
    chief: ChiefDecision
    news: NewsRiskView
    liquidity: LiquidityView
    structure: StructureView


def resolve(inputs: ProtocolInput, *, rebuttals: Mapping[str, RebuttalStance] | None = None,
            fallback: DeskViews | None = None) -> ProtocolDecision:
    """Apply the resolution rules; see `resolve_detailed`."""
    return resolve_detailed(inputs, rebuttals=rebuttals, fallback=fallback).to_decision()


def resolve_detailed(inputs: ProtocolInput, *,
                     rebuttals: Mapping[str, RebuttalStance] | None = None,
                     fallback: DeskViews | None = None) -> Resolution:
    """Apply the resolution rules and keep the audit trail.

    Args:
        inputs: gates, calendar, offered ids, effective views and the Chief decision.
        rebuttals: PA's R2 answers, candidate_id -> "maintain" | "withdraw"; merged
            with `inputs.withdrawn_ids`.
        fallback: deterministic desk views used for a missing or wrongly typed
            news/liquidity/structure view (never for Price Action).

    Raises:
        ValueError: `pa_min_conviction` outside [0, 1], an unknown `structure_veto`
            mode or an unknown rebuttal stance.
    """
    _check_settings(inputs)
    withdrawn = frozenset(inputs.withdrawn_ids) | _withdrawals(rebuttals)
    gate_reason = hold_reason_for_gates(inputs.gates)
    if gate_reason is not None:
        return _early_hold(gate_reason, (RULE_GATES,), _failed_gates(inputs.gates))
    panel, markers, problem = _assemble_panel(inputs, fallback)
    trail = (RULE_GATES, RULE_VIEWS) + markers
    if panel is None:
        return _early_hold(HoldReason.INVALID_VIEW, trail, problem)
    return _deliberate(inputs, panel, withdrawn, trail)


# --- rules 3-6 ---------------------------------------------------------------------------
def _deliberate(inputs: ProtocolInput, panel: _Panel, withdrawn: frozenset[str],
                trail: tuple[str, ...]) -> Resolution:
    chief = panel.chief
    tier: RiskTier = "standard" if chief.risk_tier == "standard" else "reduced"
    style, overridden = _order_style(chief, panel.liquidity)
    multiplier = _multiplier(panel, tier)
    enforced, logged = _vetoes(inputs.calendar, panel, inputs.structure_veto)
    outcome = partial(
        Resolution, candidate_id=chief.candidate_id if chief.action == "ENTER" else None,
        size_multiplier=multiplier, order_style=style, risk_tier=tier,
        enforced_vetoes=enforced, logged_vetoes=logged)
    trail += (RULE_VETOES,) + ((MARK_STRUCTURE_LOGGED,) if logged else ())
    if enforced:
        return outcome(action="HOLD", hold_reason=HoldReason.VETO, applied_rules=trail,
                       detail="enforced veto: " + ",".join(enforced))
    trail += (RULE_TAKE,)
    reason, detail = _take_check(chief, panel.price_action, withdrawn,
                                 inputs.pa_min_conviction)
    if reason is not None:
        return outcome(action="HOLD", hold_reason=reason, applied_rules=trail, detail=detail)
    trail += (RULE_MULTIPLIER,) + ((MARK_REDUCED_TIER,) if tier == "reduced" else ())
    if multiplier < MIN_COMBINED_MULTIPLIER:
        return outcome(action="HOLD", hold_reason=HoldReason.LOW_MULTIPLIER,
                       applied_rules=trail,
                       detail=f"multiplier {multiplier:.3f} < {MIN_COMBINED_MULTIPLIER}")
    trail += (RULE_ORDER_STYLE,) + ((MARK_LIMIT_OVERRIDE,) if overridden else ())
    return outcome(action="ENTER", hold_reason=None, applied_rules=trail)


def _vetoes(calendar: object, panel: _Panel,
            mode: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(enforced, logged) veto codes in a fixed order."""
    counter = panel.structure.counter_structure_veto is not False
    checks = (
        (VETO_CALENDAR, _calendar_blocks(calendar)),
        (VETO_NEWS, panel.news.stance not in _NEWS_OPEN),
        (VETO_LIQUIDITY, panel.liquidity.stance not in _LIQUIDITY_OPEN),
        (VETO_STRUCTURE, counter and mode == STRUCTURE_VETO_ENFORCE),
    )
    enforced = tuple(code for code, hit in checks if hit)
    logged = (VETO_STRUCTURE_LOGGED,) if counter and mode == STRUCTURE_VETO_LOG else ()
    return enforced, logged


def _calendar_blocks(calendar: object) -> bool:
    """The code-computed calendar veto; anything but a clean assessment blocks."""
    if not isinstance(calendar, CalendarAssessment):
        return True
    return bool(calendar.blackout or calendar.stale or calendar.codes)


def _take_check(chief: ChiefDecision, price_action: PriceActionView,
                withdrawn: frozenset[str],
                min_conviction: float) -> tuple[HoldReason | None, str]:
    if chief.action != "ENTER":
        return HoldReason.CHIEF_HOLD, "chief chose HOLD"
    ranked = next((item for item in price_action.ranked
                   if item.candidate_id == chief.candidate_id), None)
    if ranked is None or ranked.verdict != "TAKE":
        return HoldReason.NO_TAKE, "chief pick is not a price_action TAKE"
    if chief.candidate_id in withdrawn:
        return HoldReason.NO_TAKE, "price_action withdrew the pick in rebuttal"
    conviction = _finite(ranked.conviction)
    if conviction is None or conviction < min_conviction:
        return HoldReason.LOW_CONVICTION, f"pick conviction below {min_conviction:.2f}"
    return None, ""


def _multiplier(panel: _Panel, tier: RiskTier) -> float:
    lowest = min(_unit(panel.news.size_multiplier), _unit(panel.liquidity.size_multiplier),
                 _unit(panel.structure.size_multiplier))
    return lowest * (REDUCED_TIER_FACTOR if tier == "reduced" else _STANDARD_TIER_FACTOR)


def _order_style(chief: ChiefDecision, liquidity: LiquidityView) -> tuple[OrderStyle, bool]:
    """(style, overridden): MARKET only when the Chief asks and liquidity allows it."""
    if chief.order_style != "MARKET":
        return "LIMIT", False
    if liquidity.order_style in _MARKET_ALLOWED:
        return "MARKET", False
    return "LIMIT", True


# --- rules 1-2 and input checks -----------------------------------------------------------
def _assemble_panel(inputs: ProtocolInput, fallback: DeskViews | None
                    ) -> tuple[_Panel | None, tuple[str, ...], str]:
    """(panel or None, fallback markers, problem); problem is "" when the panel is usable."""
    views = inputs.views
    news, news_fb = _desk_view(views, fallback, "news_risk", NewsRiskView)
    liquidity, liquidity_fb = _desk_view(views, fallback, "liquidity", LiquidityView)
    structure, structure_fb = _desk_view(views, fallback, "structure", StructureView)
    used = (("news_risk", news_fb), ("liquidity", liquidity_fb), ("structure", structure_fb))
    markers = tuple(MARK_FALLBACK_PREFIX + role for role, flag in used if flag)
    price_action = getattr(views, "price_action", None)
    problem = (_price_action_problem(price_action, inputs.offered_ids)
               or _chief_problem(inputs.decision, inputs.offered_ids)
               or _missing_desk_problem(
                   (("news_risk", news), ("liquidity", liquidity), ("structure", structure))))
    if problem:
        return None, markers, problem
    # An empty problem string proves every type below (checked just above).
    panel = _Panel(cast(PriceActionView, price_action), cast(ChiefDecision, inputs.decision),
                   cast(NewsRiskView, news), cast(LiquidityView, liquidity),
                   cast(StructureView, structure))
    return panel, markers, ""


def _desk_view(views: object, fallback: DeskViews | None, role: str,
               model: type[_DeskT]) -> tuple[_DeskT | None, bool]:
    """(view, used_fallback): a missing or wrongly typed view takes the fallback's."""
    primary = getattr(views, role, None)
    if isinstance(primary, model):
        return primary, False
    backup = getattr(fallback, role, None)
    if isinstance(backup, model):
        return backup, True
    return None, False


def _missing_desk_problem(desks: Iterable[tuple[str, object]]) -> str:
    missing = [role for role, view in desks if view is None]
    return f"no {missing[0]} view and no deterministic fallback" if missing else ""


def _price_action_problem(view: object, offered: frozenset[str]) -> str:
    if not isinstance(view, PriceActionView):
        return "price_action view missing or invalid"
    if not all(isinstance(item, RankedCandidate) for item in view.ranked):
        return "price_action ranking is not validated"
    ids = [item.candidate_id for item in view.ranked]
    if bool(view.abstain) == bool(ids):
        return "price_action abstain flag contradicts its ranking"
    if len(set(ids)) != len(ids) or not set(ids) <= offered:
        return "price_action ranked a duplicate or unoffered candidate"
    return ""


def _chief_problem(decision: object, offered: frozenset[str]) -> str:
    if not isinstance(decision, ChiefDecision):
        return "chief decision missing or invalid"
    if decision.action == "ENTER":
        if decision.candidate_id in offered:
            return ""
        return "chief ENTER must pick an offered candidate"
    if decision.action == "HOLD" and decision.candidate_id is None:
        return ""
    return "chief decision has an unknown action or a HOLD naming a candidate"


def _check_settings(inputs: ProtocolInput) -> None:
    threshold = _finite(inputs.pa_min_conviction)
    if threshold is None or not _MIN_UNIT <= threshold <= _MAX_UNIT:
        raise ValueError("pa_min_conviction must be a finite number in [0, 1]")
    if inputs.structure_veto not in STRUCTURE_VETO_MODES:
        raise ValueError("structure_veto must be 'log' or 'enforce'")


def _withdrawals(rebuttals: Mapping[str, str] | None) -> frozenset[str]:
    if not rebuttals:
        return frozenset()
    if any(stance not in REBUTTAL_STANCES for stance in rebuttals.values()):
        raise ValueError("rebuttal stance must be 'maintain' or 'withdraw'")
    return frozenset(cid for cid, stance in rebuttals.items() if stance == REBUTTAL_WITHDRAW)


def _failed_gates(gates: Iterable[GateResult]) -> str:
    codes = ",".join(str(gate.code) for gate in gates if not gate.passed)
    return f"failed gates: {codes}"[:_MAX_DETAIL_CHARS]


def _early_hold(reason: HoldReason, trail: tuple[str, ...], detail: str) -> Resolution:
    return Resolution(action="HOLD", hold_reason=reason, candidate_id=None,
                      size_multiplier=_MIN_UNIT, applied_rules=trail,
                      detail=detail[:_MAX_DETAIL_CHARS])


def _finite(value: object) -> float | None:
    """A real, finite number as float; None for bools, strings, NaN and infinities."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _unit(value: object) -> float:
    number = _finite(value)
    return _MIN_UNIT if number is None else min(_MAX_UNIT, max(_MIN_UNIT, number))
