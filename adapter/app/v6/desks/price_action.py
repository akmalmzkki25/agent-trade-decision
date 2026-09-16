"""
Deterministic Price Action desk (plan section 2): ranks the offered candidates.

It is the only desk with a directional voice, and only by choosing among
candidates the detectors in `app.v6.setups` produced; it reads their feature
keys and reason codes. Each candidate gets eight signals in {-1, 0, +1}
(0 = neutral or unknown):

    quality  setup trigger strength against the kn/06 thresholds
    htf      the detector's H1 HTF_ALIGNED / HTF_OPPOSED code, else the M15
             swing-structure feature, against the candidate side
    level    the detector found a LEVEL_CONFLUENCE (several levels crossed)
    timing   early (+1) / late or outside (-1) third of the main window;
             continuation setups after London are -1 (kn/03, kn/06 §3)
    cost     friction / ATR(M5) at or above the hard ceiling (-1)
    chop     Kaufman ER(M15) at or below the chop threshold (-1)
    reward   target pulled below FULL_REWARD_R by a round level (-1)
    reach    the target lies beyond ATR(M15) x sqrt(barrier bars) (-1)

Conviction = clamp(BASE_CONVICTION + sum(weight x signal), 0, 1), rounded to
two decimals: improving any one signal never lowers it. A candidate is TAKE
only when quality is +1, none of htf/cost/reward/reach is -1, a continuation
setup sits in the early third (kn/06 §3), it is not a SHADOW_WEIGHT candidate
(measured only), and conviction reaches TAKE_MIN_CONVICTION; otherwise SKIP.
With nothing offered the desk abstains. Every threshold is provisional
(UNMEASURED) until the labeler has enough samples.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, fields
from types import MappingProxyType
from typing import Final

from ..cycle_types import (
    F_ATR_M15, F_ER_M15, F_FRICTION_ATR, F_STRUCTURE_M15, SETUP_NAMES, CandidateAssessment,
    MarketContext,
)
from ..risk.limits import MAX_FRICTION_TO_ATR_M5
from ..schemas.agents import (
    MAX_NOTE_CHARS, MAX_RANKED, MAX_REASON_CODES, PriceActionReason, PriceActionView,
    RankedCandidate,
)
from ..types import TIMEFRAME_SECONDS, Candidate, ExitPlan, Side
from .structure import ER_CHOP_MAX, finite_feature

# --- vocabulary of app.v6.setups (feature keys and reason codes) -----------------
CF_BODY_RATIO: Final[str] = "body_ratio"            # trigger |close - open| / range
CF_RANGE_VOL: Final[str] = "range_vol"              # trigger range / M15 volatility unit
CF_TV_Z: Final[str] = "tick_volume_z"               # trigger tick-volume z, own feed
CF_EXTENSION: Final[str] = "extension"              # retest: leg extension, range widths
CF_OR_WIDTH_ATR: Final[str] = "or_width_atr_d1"     # orb: range width / ATR(14, D1)
RC_LEVEL_CONFLUENCE: Final[str] = "LEVEL_CONFLUENCE"
RC_HTF_ALIGNED: Final[str] = "HTF_ALIGNED"
RC_HTF_OPPOSED: Final[str] = "HTF_OPPOSED"
RC_SHADOW_WEIGHT: Final[str] = "SHADOW_WEIGHT"      # detected for measurement only
RC_FAST_EXTENSION: Final[str] = "FAST_EXTENSION"
RC_SLOW_EXTENSION: Final[str] = "SLOW_EXTENSION"
RC_OR_WIDE: Final[str] = "OR_WIDE"
RC_OR_NARROW: Final[str] = "OR_NARROW"

# kn/06 §1: displacement trigger (range in the detector's M15 volatility unit).
DISPLACEMENT_MIN_BODY_RATIO: Final[float] = 0.70
DISPLACEMENT_MIN_RANGE_VOL: Final[float] = 2.0
DISPLACEMENT_MIN_TV_Z: Final[float] = 1.5
# kn/06 §4: engulfing size filter.
ENGULFING_MIN_RANGE_VOL: Final[float] = 1.3
# kn/06 §2: wide (> 0.6 ATR) opening ranges continue 77.5%; narrow (< 0.3) double-break.
ORB_WIDE_ATR: Final[float] = 0.6
ORB_NARROW_ATR: Final[float] = 0.3
# kn/06 §3: retest continuation only between ~1.0x and ~1.35x, fast, early in the session.
RETEST_MIN_EXTENSION: Final[float] = 1.0
RETEST_MAX_EXTENSION: Final[float] = 1.35
CONTINUATION_SETUPS: Final[frozenset[str]] = frozenset({"retest"})

FULL_REWARD_R: Final[float] = 1.5
REACH_ATR_MULTIPLE: Final[float] = 1.0
M15_SECONDS: Final[int] = TIMEFRAME_SECONDS["M15"]

BASE_CONVICTION: Final[float] = 0.45
TAKE_MIN_CONVICTION: Final[float] = 0.60
CONVICTION_DECIMALS: Final[int] = 2
SIGNAL_WEIGHTS: Final[Mapping[str, float]] = MappingProxyType({
    "quality": 0.15, "htf": 0.10, "level": 0.05, "timing": 0.05,
    "cost": 0.10, "chop": 0.05, "reward": 0.05, "reach": 0.05,
})

# Disqualifiers first, so the reason for a SKIP survives the 5-code cap.
CODE_PRIORITY: Final[tuple[PriceActionReason, ...]] = (
    "HTF_OPPOSED", "FRICTION_HIGH", "STOP_TOO_WIDE", "POOR_REWARD_ROOM", "EXTENDED_MOVE",
    "WEAK_DISPLACEMENT", "NO_EDGE", "SESSION_TIMING_POOR", "CHOPPY_CONTEXT",
    "STRONG_DISPLACEMENT", "CLEAN_RETEST", "CONFIRMED_CLOSE", "HTF_ALIGNED",
    "LEVEL_CONFLUENCE", "SESSION_TIMING_GOOD",
)
_SIGNAL_VALUES: Final[frozenset[int]] = frozenset({-1, 0, 1})

Graded = tuple[int, tuple[PriceActionReason, ...]]
_UNKNOWN: Final[Graded] = (0, ("NO_EDGE",))
_NEUTRAL: Final[Graded] = (0, ())
_STRONG: Final[Graded] = (1, ("STRONG_DISPLACEMENT",))
_WEAK: Final[Graded] = (-1, ("WEAK_DISPLACEMENT",))


@dataclass(frozen=True)
class PaSignals:
    """Per-candidate evidence, each in {-1, 0, +1}."""

    quality: int = 0
    htf: int = 0
    level: int = 0
    timing: int = 0
    cost: int = 0
    chop: int = 0
    reward: int = 0
    reach: int = 0

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, bool) or value not in _SIGNAL_VALUES:
                raise ValueError(f"signal {item.name} must be -1, 0 or +1")

    @property
    def disqualified(self) -> bool:
        return self.quality < 1 or min(self.htf, self.cost, self.reward, self.reach) < 0


def conviction_from(signals: PaSignals) -> float:
    """Clamped weighted sum; monotone non-decreasing in every signal."""
    total = BASE_CONVICTION + sum(
        SIGNAL_WEIGHTS[item.name] * getattr(signals, item.name) for item in fields(signals))
    return round(min(1.0, max(0.0, total)), CONVICTION_DECIMALS)


# --- quality per setup ------------------------------------------------------------

def _displacement_quality(candidate: Candidate) -> Graded:
    body = finite_feature(candidate.features, CF_BODY_RATIO)
    size = finite_feature(candidate.features, CF_RANGE_VOL)
    if body is None or size is None:
        return _UNKNOWN
    tv_z = finite_feature(candidate.features, CF_TV_Z)
    strong = (body >= DISPLACEMENT_MIN_BODY_RATIO and size >= DISPLACEMENT_MIN_RANGE_VOL
              and (tv_z is None or tv_z >= DISPLACEMENT_MIN_TV_Z))
    return _STRONG if strong else _WEAK


def _engulfing_quality(candidate: Candidate) -> Graded:
    size = finite_feature(candidate.features, CF_RANGE_VOL)
    if size is None:
        return _UNKNOWN
    return _STRONG if size >= ENGULFING_MIN_RANGE_VOL else _WEAK


def _orb_quality(candidate: Candidate) -> Graded:
    width = finite_feature(candidate.features, CF_OR_WIDTH_ATR)
    wide = RC_OR_WIDE in candidate.reason_codes if width is None else width > ORB_WIDE_ATR
    narrow = RC_OR_NARROW in candidate.reason_codes if width is None else width < ORB_NARROW_ATR
    if wide:
        return 1, ("STRONG_DISPLACEMENT", "CONFIRMED_CLOSE")
    return _WEAK if narrow else _UNKNOWN


def _retest_quality(candidate: Candidate) -> Graded:
    extension = finite_feature(candidate.features, CF_EXTENSION)
    if extension is None:
        return _UNKNOWN
    if extension > RETEST_MAX_EXTENSION:
        return -1, ("EXTENDED_MOVE",)
    if extension < RETEST_MIN_EXTENSION or RC_SLOW_EXTENSION in candidate.reason_codes:
        return _WEAK
    return 1, ("CLEAN_RETEST",)


QUALITY_GRADERS: Final[Mapping[str, Callable[[Candidate], Graded]]] = MappingProxyType({
    "displacement": _displacement_quality, "orb": _orb_quality,
    "retest": _retest_quality, "engulfing": _engulfing_quality,
})


# --- context signals --------------------------------------------------------------

def _htf_signal(context: MarketContext, candidate: Candidate) -> Graded:
    if RC_HTF_OPPOSED in candidate.reason_codes:
        return -1, ("HTF_OPPOSED",)
    if RC_HTF_ALIGNED in candidate.reason_codes:
        return 1, ("HTF_ALIGNED",)
    structure = finite_feature(context.features, F_STRUCTURE_M15)
    if structure is None or structure == 0:
        return _NEUTRAL
    if (structure > 0) == (candidate.side == "buy"):
        return 1, ("HTF_ALIGNED",)
    return -1, ("HTF_OPPOSED",)


def _level_signal(candidate: Candidate) -> Graded:
    confluence = RC_LEVEL_CONFLUENCE in candidate.reason_codes
    return (1, ("LEVEL_CONFLUENCE",)) if confluence else _NEUTRAL


def _timing_signal(context: MarketContext, setup: str) -> Graded:
    session = context.session
    if setup in CONTINUATION_SETUPS and not session.continuation_allowed:
        return -1, ("SESSION_TIMING_POOR",)
    if session.main_window_third == "early":
        return 1, ("SESSION_TIMING_GOOD",)
    if session.main_window_third in ("late", "outside"):
        return -1, ("SESSION_TIMING_POOR",)
    return _NEUTRAL


def _cost_signal(context: MarketContext) -> Graded:
    ratio = finite_feature(context.features, F_FRICTION_ATR)
    if ratio is not None and ratio >= MAX_FRICTION_TO_ATR_M5:
        return -1, ("FRICTION_HIGH",)
    return _NEUTRAL


def _chop_signal(context: MarketContext) -> Graded:
    er = finite_feature(context.features, F_ER_M15)
    return (-1, ("CHOPPY_CONTEXT",)) if er is not None and er <= ER_CHOP_MAX else _NEUTRAL


def _reward_signal(plan: ExitPlan) -> Graded:
    return (-1, ("POOR_REWARD_ROOM",)) if plan.reward_r < FULL_REWARD_R else _NEUTRAL


def _reach_signal(context: MarketContext, plan: ExitPlan) -> Graded:
    atr = finite_feature(context.features, F_ATR_M15)
    if atr is None or atr <= 0:
        return _NEUTRAL
    reach = REACH_ATR_MULTIPLE * atr * math.sqrt(plan.time_barrier_s / M15_SECONDS)
    target = plan.reward_r * plan.stop_distance
    return (-1, ("STOP_TOO_WIDE",)) if target > reach else _NEUTRAL


# --- ranking ------------------------------------------------------------------------

def ordered_codes(codes: Iterable[PriceActionReason]) -> tuple[PriceActionReason, ...]:
    """Unique codes in CODE_PRIORITY order, capped at MAX_REASON_CODES."""
    unique = set(codes)
    return tuple(code for code in CODE_PRIORITY if code in unique)[:MAX_REASON_CODES]


def grade_candidate(context: MarketContext, assessment: CandidateAssessment
                    ) -> tuple[PaSignals, tuple[PriceActionReason, ...]]:
    """The signals and reason codes for one offered candidate."""
    candidate, plan = assessment.candidate, assessment.exit_plan
    if plan is None:
        raise ValueError("only candidates with an exit plan can be ranked")
    grader = QUALITY_GRADERS.get(candidate.setup)
    graded: dict[str, Graded] = {
        "quality": grader(candidate) if grader is not None else _UNKNOWN,
        "htf": _htf_signal(context, candidate),
        "level": _level_signal(candidate),
        "timing": _timing_signal(context, candidate.setup),
        "cost": _cost_signal(context),
        "chop": _chop_signal(context),
        "reward": _reward_signal(plan),
        "reach": _reach_signal(context, plan),
    }
    signals = PaSignals(**{name: value for name, (value, _) in graded.items()})
    return signals, ordered_codes(code for _, found in graded.values() for code in found)


def is_takeable(candidate: Candidate, signals: PaSignals, conviction: float) -> bool:
    """The TAKE rule from the module docstring."""
    if signals.disqualified or RC_SHADOW_WEIGHT in candidate.reason_codes:
        return False
    if candidate.setup in CONTINUATION_SETUPS and signals.timing < 1:
        return False
    return conviction >= TAKE_MIN_CONVICTION


def score_candidate(context: MarketContext, assessment: CandidateAssessment) -> RankedCandidate:
    signals, codes = grade_candidate(context, assessment)
    conviction = conviction_from(signals)
    candidate = assessment.candidate
    take = is_takeable(candidate, signals, conviction)
    shadow = RC_SHADOW_WEIGHT in candidate.reason_codes
    note = (f"{candidate.setup} {candidate.side}: quality {signals.quality:+d}, "
            f"htf {signals.htf:+d}, conviction {conviction:.2f}"
            + (", shadow weight" if shadow else ""))
    return RankedCandidate(
        candidate_id=candidate.candidate_id, verdict="TAKE" if take else "SKIP",
        conviction=conviction,
        reason_codes=ordered_codes((*codes, "NO_EDGE") if shadow else codes),
        note=note[:MAX_NOTE_CHARS])


def _setup_rank(setup: str) -> int:
    return SETUP_NAMES.index(setup) if setup in SETUP_NAMES else len(SETUP_NAMES)


def price_action_view(context: MarketContext,
                      offered: Sequence[CandidateAssessment]) -> PriceActionView:
    """Rank up to MAX_RANKED offered candidates: TAKE first, then by conviction.

    Ties keep the kn/15 setup priority (displacement, orb, retest, engulfing),
    then the offer order.
    """
    if not offered:
        return PriceActionView(abstain=True, ranked=())
    scored = sorted(
        ((score_candidate(context, item), _setup_rank(item.candidate.setup), index)
         for index, item in enumerate(offered[:MAX_RANKED])),
        key=lambda row: (row[0].verdict != "TAKE", -row[0].conviction, row[1], row[2]),
    )
    return PriceActionView(abstain=False, ranked=tuple(row[0] for row in scored))
