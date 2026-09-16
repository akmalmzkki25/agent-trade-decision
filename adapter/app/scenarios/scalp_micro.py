"""
SCALP_MICRO — V5 scalping signal.

Three analysis layers, all required to pass before a burst is allowed:

1. MICRO-VOLATILITY GATE (ATR sweet spot)
   The bot only trades when M1 ATR sits in a usable band. Too calm and there is
   no range to scalp; too wild (news spike) and spread/slippage eat the edge.

2. VOLUME SPREAD ANALYSIS (VSA)
   Classic VSA compares bar range ("spread" in VSA terms, not bid/ask spread)
   against volume to infer who is in control:
     - high volume + narrow range  -> absorption / stopping volume (reversal)
     - high volume + wide range    -> climax (exhaustion, fade the move)
     - low volume  + wide range    -> no demand / no supply (move is weak)
   VSA adjusts confidence and can veto a continuation entry.

3. MICRO-MOMENTUM + MEAN REVERSION
   3-tick direction for entry timing, Bollinger position on M1 to avoid buying
   the top / selling the bottom, RSI to skip exhausted moves.

Order Book Imbalance (OBI) is approximated by tick volume z-score because most
retail FX/CFD brokers do not expose real DOM for XAUUSD. When a broker does
supply market depth, the EA can fill `dom_imbalance` and it is used directly.
"""

from __future__ import annotations

from typing import Final

from ..models import V5BurstRequest
from .base import ScenarioResult, _safe

# --- Hard gates -------------------------------------------------------------
MAX_SPREAD_POINTS: Final[float] = 30.0

# ATR sweet spot: percentile of current M1 ATR vs its own recent history.
ATR_SWEET_SPOT_MIN: Final[float] = 0.25   # below this = market too quiet
ATR_SWEET_SPOT_MAX: Final[float] = 0.90   # above this = news spike / too wild

RSI_EXTREME: Final[float] = 0.40

# --- VSA thresholds ---------------------------------------------------------
VSA_HIGH_VOLUME_Z: Final[float] = 1.5
VSA_CLIMAX_VOLUME_Z: Final[float] = 2.0
VSA_NARROW_RANGE_Z: Final[float] = -0.5
VSA_WIDE_RANGE_Z: Final[float] = 1.0
VSA_LOW_VOLUME_Z: Final[float] = -0.5

# --- Direction bounds -------------------------------------------------------
BB_EXTREME: Final[float] = 0.85        # don't buy the top / sell the bottom
RSI_NEUTRAL: Final[float] = 0.20       # "plenty of room left" threshold
H1_TREND_ALIGN: Final[float] = 0.10    # DI balance needed to call it aligned

# --- Scoring ----------------------------------------------------------------
BASE_SCORE: Final[float] = 0.50
MIN_TICK_VOL_Z: Final[float] = 0.5
TIGHT_SPREAD_POINTS: Final[float] = 10.0
DOM_IMBALANCE_THRESHOLD: Final[float] = 0.20

SCORE_VSA_EFFORT: Final[float] = 0.10
SCORE_VSA_REVERSAL: Final[float] = 0.05
SCORE_VOL_SPIKE: Final[float] = 0.15
SCORE_SPREAD_TIGHT: Final[float] = 0.10
SCORE_RSI_NEUTRAL: Final[float] = 0.10
SCORE_DOM_ALIGNED: Final[float] = 0.10
SCORE_H1_ALIGN: Final[float] = 0.05
PENALTY_DOM_AGAINST: Final[float] = 0.15


def _classify_vsa(volume_z: float, range_z: float) -> tuple[str, float]:
    """
    Classify the last completed M1 bar using Volume Spread Analysis.

    Returns (signal_name, reversal_bias) where reversal_bias is in [-1, 1]:
      +1 favours fading the current move, -1 favours following it, 0 neutral.
    """
    if volume_z >= VSA_CLIMAX_VOLUME_Z and range_z >= VSA_WIDE_RANGE_Z:
        return "VSA_CLIMAX", 1.0
    if volume_z >= VSA_HIGH_VOLUME_Z and range_z <= VSA_NARROW_RANGE_Z:
        return "VSA_ABSORPTION", 0.8
    if volume_z <= VSA_LOW_VOLUME_Z and range_z >= VSA_WIDE_RANGE_Z:
        return "VSA_NO_DEMAND", 0.5
    if volume_z >= VSA_HIGH_VOLUME_Z and range_z >= 0.0:
        return "VSA_EFFORT_CONFIRMED", -0.6
    return "VSA_NEUTRAL", 0.0


def _reject(reason: str, key_levels: dict[str, float] | None = None) -> ScenarioResult:
    return ScenarioResult(
        "SCALP_MICRO", 0.0, "none", 0.0, None, key_levels or {}, [reason]
    )


def _check_gates(req: V5BurstRequest, feats: dict[str, float]) -> ScenarioResult | None:
    """Hard vetoes. Returns a rejection result, or None when all gates pass."""
    if req.market.spread_points > MAX_SPREAD_POINTS:
        return _reject("SPREAD_TOO_WIDE")

    atr_percentile = feats["atr_percentile"]
    if atr_percentile < ATR_SWEET_SPOT_MIN:
        return _reject("ATR_TOO_QUIET", {"atr_percentile": atr_percentile})
    if atr_percentile > ATR_SWEET_SPOT_MAX:
        return _reject("ATR_TOO_WILD", {"atr_percentile": atr_percentile})

    if abs(feats["rsi_m1"]) >= RSI_EXTREME:
        return _reject("RSI_EXTREME_M1")
    if feats["tick_mom"] == 0:
        return _reject("NO_TICK_MOMENTUM")
    return None


def _resolve_direction(feats: dict[str, float]) -> str:
    """Tick momentum, bounded by Bollinger position. 'none' when blocked."""
    tick_mom = feats["tick_mom"]
    bb_pos = feats["bb_pos_m1"]
    if tick_mom > 0 and bb_pos < BB_EXTREME:
        return "buy"
    if tick_mom < 0 and bb_pos > -BB_EXTREME:
        return "sell"
    return "none"


def _score_setup(
    req: V5BurstRequest, feats: dict[str, float], side: str, reversal_bias: float
) -> tuple[float, list[str]]:
    """Confidence for a setup that already passed the gates."""
    score = BASE_SCORE
    reasons: list[str] = ["SCALP_MICRO", "TICK_MOM_OK", "ATR_SWEET_SPOT"]

    # Volume genuinely backing the move supports continuation; absorption and
    # no-demand mean the push is tiring, which still suits a mean-reversion
    # scalper, just less strongly.
    if reversal_bias < 0:
        score += SCORE_VSA_EFFORT
    elif reversal_bias > 0:
        score += SCORE_VSA_REVERSAL

    if feats["tick_vol_z"] >= MIN_TICK_VOL_Z:
        score += SCORE_VOL_SPIKE
        reasons.append("VOL_SPIKE")
    if req.market.spread_points <= TIGHT_SPREAD_POINTS:
        score += SCORE_SPREAD_TIGHT
        reasons.append("SPREAD_TIGHT")
    if abs(feats["rsi_m1"]) < RSI_NEUTRAL:
        score += SCORE_RSI_NEUTRAL
        reasons.append("RSI_NEUTRAL")

    # Real order-book imbalance when the broker provides depth; 0.0 otherwise.
    dom_imbalance = feats["dom_imbalance"]
    if dom_imbalance != 0.0:
        aligned = dom_imbalance if side == "buy" else -dom_imbalance
        if aligned >= DOM_IMBALANCE_THRESHOLD:
            score += SCORE_DOM_ALIGNED
            reasons.append("DOM_IMBALANCE_ALIGNED")
        elif aligned <= -DOM_IMBALANCE_THRESHOLD:
            score -= PENALTY_DOM_AGAINST
            reasons.append("DOM_IMBALANCE_AGAINST")

    di_balance = feats["di_balance"]
    if (side == "buy" and di_balance > H1_TREND_ALIGN) or (
        side == "sell" and di_balance < -H1_TREND_ALIGN
    ):
        score += SCORE_H1_ALIGN
        reasons.append("H1_TREND_ALIGN")

    return max(0.0, min(1.0, score)), reasons


class ScalpMicro:
    name: str = "SCALP_MICRO"

    def evaluate(self, req: V5BurstRequest) -> ScenarioResult:
        dec = req.features.decision_tf
        feats = {
            "tick_mom": _safe(dec, "tick_momentum_signed"),
            "tick_vol_z": _safe(dec, "tick_volume_z"),
            "bb_pos_m1": _safe(dec, "bb_pos"),
            "rsi_m1": _safe(dec, "rsi_centered"),
            "atr_percentile": _safe(dec, "atr_m1_percentile"),
            "vsa_volume_z": _safe(dec, "vsa_volume_z"),
            "vsa_range_z": _safe(dec, "vsa_range_z"),
            "dom_imbalance": _safe(dec, "dom_imbalance"),
            "di_balance": _safe(req.features.context_tf, "di_balance"),
        }

        rejected = _check_gates(req, feats)
        if rejected is not None:
            return rejected

        side = _resolve_direction(feats)
        if side == "none":
            return _reject("BB_EXTREME_BLOCK")

        vsa_signal, reversal_bias = _classify_vsa(feats["vsa_volume_z"], feats["vsa_range_z"])
        # Entering a blow-off bar in its own direction is the classic way a
        # scalper gets run over, so climax is a hard veto rather than a penalty.
        if vsa_signal == "VSA_CLIMAX":
            return _reject(
                "VSA_CLIMAX_VETO",
                {"vsa_volume_z": feats["vsa_volume_z"], "vsa_range_z": feats["vsa_range_z"]},
            )

        score, reasons = _score_setup(req, feats, side, reversal_bias)
        if vsa_signal != "VSA_NEUTRAL":
            reasons.append(vsa_signal)

        return ScenarioResult(
            name=self.name,
            score=score,
            side=side,
            confidence=score,
            invalidation_price=None,
            key_levels={
                "atr_percentile": feats["atr_percentile"],
                "vsa_volume_z": feats["vsa_volume_z"],
                "vsa_range_z": feats["vsa_range_z"],
                "dom_imbalance": feats["dom_imbalance"],
            },
            reason_codes=reasons,
        )
