"""
TREND_BREAKOUT scenario detector.

Trigger conditions:
- H1 ADX strong (>= 0.25).
- |DI balance| >= 0.20 (clear directional bias).
- M15 BB width compressed (bb_width_pct_m15 below ~0.30 percentile).
- Price near 20-bar M15 high (for buy) or low (for sell), within 0.3 * ATR_M15.

Side bias from DI balance sign.

Key levels: breakout_high_m15, breakout_low_m15 (20-bar). Invalidation = EMA50_M15
crossed in opposite direction.
"""

from __future__ import annotations

from ..models import LayerPlanRequest
from .base import Scenario, ScenarioResult, _safe


class TrendBreakoutStop:
    name = "TREND_BREAKOUT"

    def evaluate(self, req: LayerPlanRequest) -> ScenarioResult:
        ctx = req.features.context_tf
        conf = req.features.confirm_tf
        exe = req.features.execution_tf

        adx_h1 = _safe(ctx, "adx_strength")
        di_balance = _safe(ctx, "di_balance")
        bb_width_pct = _safe(conf, "bb_width_pct")  # rolling percentile 0..1
        atr_m15 = _safe(exe, "atr_abs_m15", default=_safe(exe, "atr_abs"))
        ema50_m15 = _safe(conf, "ema50_m15", req.market.last_close)

        brk_high = _safe(conf, "breakout_high_m15", req.market.last_close)
        brk_low = _safe(conf, "breakout_low_m15", req.market.last_close)

        last = req.market.last_close

        score = 0.0
        reasons: list[str] = []

        if adx_h1 >= 0.25:
            score += 0.30
            reasons.append("ADX_STRONG_H1")
        elif adx_h1 >= 0.20:
            score += 0.15

        side = "none"
        invalidation: float | None = None

        # Distance to breakout level.
        if atr_m15 > 0:
            dist_to_high = (brk_high - last) / atr_m15
            dist_to_low = (last - brk_low) / atr_m15
        else:
            dist_to_high = dist_to_low = 99.0

        if di_balance >= 0.20 and 0.0 <= dist_to_high <= 0.30:
            side = "buy"
            score += 0.30
            reasons.append("DI_BULL")
            reasons.append("NEAR_RANGE_HIGH_M15")
            invalidation = ema50_m15
        elif di_balance <= -0.20 and 0.0 <= dist_to_low <= 0.30:
            side = "sell"
            score += 0.30
            reasons.append("DI_BEAR")
            reasons.append("NEAR_RANGE_LOW_M15")
            invalidation = ema50_m15

        # BB squeeze bonus (low width percentile = compression).
        if 0.0 < bb_width_pct <= 0.30:
            score += 0.20
            reasons.append("BB_SQUEEZE_M15")
        elif bb_width_pct <= 0.50:
            score += 0.10

        # ADX alignment for the chosen side.
        if (side == "buy" and di_balance >= 0.30) or (side == "sell" and di_balance <= -0.30):
            score += 0.10
            reasons.append("DI_STRONG")

        confidence = min(1.0, max(0.0, score))

        key_levels = {
            "breakout_high": brk_high,
            "breakout_low": brk_low,
            "ema50_m15": ema50_m15,
        }

        return ScenarioResult(
            name=self.name,
            score=confidence,
            side=side,
            confidence=confidence,
            invalidation_price=invalidation,
            key_levels=key_levels,
            reason_codes=reasons,
        )


Scenario
