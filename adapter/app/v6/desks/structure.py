"""
Deterministic Structure desk (plan section 2, "Chart Pattern"): regime and veto.

No directional voice. It reads the M15 swing structure (confirmed pivots from
`market.levels`), Kaufman ER (kn/09), the signed variance ratio and lag-1
autocorrelation (kn/12 §9, kn/15 §1), ADX(H1) and the same-slot ATR ratio
(kn/02), and returns:

- `regime`: TREND_UP/TREND_DOWN when a confirmed HH/HL (LH/LL) sequence is
  backed by efficiency votes, RANGE for a range without trend votes,
  TRANSITION when structure and efficiency disagree, VOLATILE when the slot
  ATR ratio explodes, UNCLEAR without data.
- `counter_structure_veto`: every offered candidate trades against a
  confirmed HH/HL or LH/LL sequence (cycle-level; the protocol decides whether
  it is only logged or enforced).
- `named_patterns`: a few classic shapes from the same pivots, weight 0
  (logged for later measurement only; kn/05 and kn/15 give them no edge).

All thresholds are provisional (UNMEASURED) until the process study.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from ..cycle_types import (
    F_AC1_M5, F_ADX_H1, F_ATR_M15, F_ATR_RATIO, F_ER_M15, F_ROUND_DISTANCE, F_STRUCTURE_M15,
    F_VR_M5, CandidateAssessment, MarketContext,
)
from ..market.levels import (
    Pivot, Structure, confirmed_pivots, round_level_distance, swing_structure,
)
from ..risk.exits import ROUND_ZONE
from ..schemas.agents import (
    MAX_NAMED_PATTERNS, MAX_NOTE_CHARS, MAX_REASON_CODES, NamedPattern, StructureReason,
    StructureRegime, StructureView,
)
from ..types import TIMEFRAME_SECONDS

M15_SECONDS: Final[int] = TIMEFRAME_SECONDS["M15"]
PIVOT_STRENGTH: Final[int] = 2

# Efficiency votes: each feature votes +1 (trend), -1 (chop / mean reversion) or 0.
ER_TREND_MIN: Final[float] = 0.35
ER_CHOP_MAX: Final[float] = 0.20
VR_MOMENTUM_MIN: Final[float] = 1.10        # signed VR > 1 trends (kn/12 §9)
VR_MEAN_REVERT_MAX: Final[float] = 0.90
AC1_MOMENTUM_MIN: Final[float] = 0.05
AC1_MEAN_REVERT_MAX: Final[float] = -0.05
ADX_STRONG_MIN: Final[float] = 25.0
ADX_WEAK_MAX: Final[float] = 20.0

# Same-slot ATR ratio bands (kn/02: volatility per slot, not ATR(14)).
ATR_RATIO_EXPANDING: Final[float] = 1.5
ATR_RATIO_CONTRACTING: Final[float] = 0.7
ATR_RATIO_VOLATILE: Final[float] = 2.0

# Price within this many ATR(M15) of a $50 level counts as "near" (kn/07 §4).
NEAR_ROUND_ATR: Final[float] = 0.5
# Two pivots within this many ATR(M15) count as an equal high / low.
EQUAL_EXTREME_ATR: Final[float] = 0.25
_PATTERN_PIVOTS: Final[int] = 3

REGIME_MULTIPLIERS: Final[Mapping[StructureRegime, float]] = MappingProxyType({
    "TREND_UP": 1.0, "TREND_DOWN": 1.0, "RANGE": 1.0,
    "TRANSITION": 0.75, "UNCLEAR": 0.75, "VOLATILE": 0.5,
})

_SWING_CODES: Final[Mapping[Structure, StructureReason]] = MappingProxyType({
    "up": "HH_HL_SEQUENCE", "down": "LH_LL_SEQUENCE", "range": "RANGE_BOUND",
    "unknown": "DATA_MISSING",
})
# Feature, trend threshold (>=), chop threshold (<=), trend code, chop code.
_VOTE_TABLE: Final[tuple[tuple[str, float, float, StructureReason, StructureReason], ...]] = (
    (F_ER_M15, ER_TREND_MIN, ER_CHOP_MAX, "EFFICIENT_TREND", "INEFFICIENT_CHOP"),
    (F_VR_M5, VR_MOMENTUM_MIN, VR_MEAN_REVERT_MAX, "MOMENTUM", "MEAN_REVERTING"),
    (F_AC1_M5, AC1_MOMENTUM_MIN, AC1_MEAN_REVERT_MAX, "MOMENTUM", "MEAN_REVERTING"),
    (F_ADX_H1, ADX_STRONG_MIN, ADX_WEAK_MAX, "ADX_STRONG", "ADX_WEAK"),
)
_CONFIRMED: Final[Mapping[Structure, str]] = MappingProxyType({"up": "buy", "down": "sell"})


@dataclass(frozen=True)
class Efficiency:
    """Sum of the efficiency votes, how many features voted, and their codes."""

    votes: int
    known: int
    codes: tuple[StructureReason, ...]

    @property
    def trending(self) -> bool:
        return self.votes > 0


def finite_feature(values: Mapping[str, float], key: str) -> float | None:
    """A usable number under `key`, or None (missing, bool, non-numeric or non-finite)."""
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def m15_pivots(context: MarketContext) -> tuple[Pivot, ...]:
    """Confirmed M15 pivots known at the cycle's bar close (no look-ahead)."""
    bars = context.bars.get("M15", ())
    pivots = confirmed_pivots(bars, PIVOT_STRENGTH, M15_SECONDS)
    return tuple(p for p in pivots if p.confirmed_at <= context.as_of_epoch)


def swing_state(context: MarketContext, pivots: Sequence[Pivot]) -> Structure:
    """The features' M15 structure when present, else the swing structure of `pivots`."""
    value = finite_feature(context.features, F_STRUCTURE_M15)
    if value is None:
        return swing_structure(pivots, context.as_of_epoch)
    if value > 0:
        return "up"
    return "down" if value < 0 else "range"


def efficiency(features: Mapping[str, float]) -> Efficiency:
    votes, known, codes = 0, 0, []
    for key, trend_min, chop_max, trend_code, chop_code in _VOTE_TABLE:
        value = finite_feature(features, key)
        if value is None:
            continue
        known += 1
        if value >= trend_min:
            votes, codes = votes + 1, [*codes, trend_code]
        elif value <= chop_max:
            votes, codes = votes - 1, [*codes, chop_code]
    return Efficiency(votes=votes, known=known, codes=tuple(dict.fromkeys(codes)))


def classify_regime(swing: Structure, eff: Efficiency, atr_ratio: float | None) -> StructureRegime:
    if atr_ratio is not None and atr_ratio >= ATR_RATIO_VOLATILE:
        return "VOLATILE"
    if swing in _CONFIRMED:
        if not eff.trending:
            return "TRANSITION"
        return "TREND_UP" if swing == "up" else "TREND_DOWN"
    if swing == "range":
        return "TRANSITION" if eff.trending else "RANGE"
    if eff.known and eff.votes < 0:
        return "RANGE"
    return "UNCLEAR"


def counter_structure(swing: Structure,
                      offered: Sequence[CandidateAssessment]) -> tuple[bool, bool]:
    """(veto, any_opposed): veto only when every offered candidate opposes the swing."""
    aligned_side = _CONFIRMED.get(swing)
    if aligned_side is None or not offered:
        return False, False
    opposed = [item.candidate.side != aligned_side for item in offered]
    return all(opposed), any(opposed)


def _near_round(context: MarketContext) -> bool:
    distance = finite_feature(context.features, F_ROUND_DISTANCE)
    if distance is None:
        distance = round_level_distance(context.mid)[1]
    atr = finite_feature(context.features, F_ATR_M15)
    zone = NEAR_ROUND_ATR * atr if atr is not None and atr > 0 else ROUND_ZONE
    return abs(distance) <= zone


def _atr_codes(atr_ratio: float | None) -> tuple[StructureReason, ...]:
    if atr_ratio is None:
        return ()
    if atr_ratio >= ATR_RATIO_EXPANDING:
        return ("ATR_EXPANDING",)
    return ("ATR_CONTRACTING",) if atr_ratio <= ATR_RATIO_CONTRACTING else ()


def _is_head_shoulders(values: Sequence[float], tolerance: float) -> bool:
    if len(values) < _PATTERN_PIVOTS:
        return False
    left, head, right = values[-_PATTERN_PIVOTS:]
    return head > max(left, right) + tolerance and abs(left - right) <= tolerance


def _equal_last_two(values: Sequence[float], tolerance: float) -> bool:
    return len(values) >= 2 and abs(values[-1] - values[-2]) <= tolerance


def _contracting(highs: Sequence[float], lows: Sequence[float], tolerance: float) -> bool:
    if len(highs) < 2 or len(lows) < 2:
        return False
    return highs[-1] < highs[-2] - tolerance and lows[-1] > lows[-2] + tolerance


def named_patterns(pivots: Sequence[Pivot], atr_m15: float | None) -> tuple[NamedPattern, ...]:
    """Classic shapes from the last pivots. Weight 0: recorded, never acted on."""
    if atr_m15 is None or atr_m15 <= 0:
        return ()
    tolerance = EQUAL_EXTREME_ATR * atr_m15
    highs = [p.price for p in pivots if p.kind == "high"][-_PATTERN_PIVOTS:]
    lows = [p.price for p in pivots if p.kind == "low"][-_PATTERN_PIVOTS:]
    top, bottom = _equal_last_two(highs, tolerance), _equal_last_two(lows, tolerance)
    shapes: tuple[tuple[bool, NamedPattern], ...] = (
        (top and bottom, "RECTANGLE"),
        (top and not bottom, "DOUBLE_TOP"),
        (bottom and not top, "DOUBLE_BOTTOM"),
        (_contracting(highs, lows, tolerance), "TRIANGLE"),
        (_is_head_shoulders(highs, tolerance), "HEAD_SHOULDERS"),
        (_is_head_shoulders([-low for low in lows], tolerance), "INV_HEAD_SHOULDERS"),
    )
    return tuple(name for found, name in shapes if found)[:MAX_NAMED_PATTERNS]


def structure_view(context: MarketContext,
                   offered: Sequence[CandidateAssessment] = ()) -> StructureView:
    """The rules Structure view for one cycle."""
    pivots = m15_pivots(context)
    swing = swing_state(context, pivots)
    eff = efficiency(context.features)
    atr_ratio = finite_feature(context.features, F_ATR_RATIO)
    regime = classify_regime(swing, eff, atr_ratio)
    veto, any_opposed = counter_structure(swing, offered)
    codes: tuple[StructureReason, ...] = (
        (("COUNTER_STRUCTURE",) if any_opposed else ())
        + (_SWING_CODES[swing],) + eff.codes + _atr_codes(atr_ratio)
        + (("NEAR_ROUND_NUMBER",) if _near_round(context) else ())
    )
    note = f"swing {swing}, efficiency votes {eff.votes:+d} of {eff.known}, regime {regime}"
    return StructureView(
        regime=regime, counter_structure_veto=veto,
        size_multiplier=REGIME_MULTIPLIERS[regime],
        named_patterns=named_patterns(pivots, finite_feature(context.features, F_ATR_M15)),
        reason_codes=tuple(dict.fromkeys(codes))[:MAX_REASON_CODES],
        note=note[:MAX_NOTE_CHARS],
    )
