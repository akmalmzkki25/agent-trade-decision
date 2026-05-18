"""
RANGE_REVERT scenario detector.

Trigger conditions (H1 context + M5 confirm):
- H1 ADX strength low (< 0.20) → ranging market.
- M5 Bollinger band position extreme (|bb_pos| > 0.85) → near edge.
- M5 RSI extreme (|rsi_centered| > 0.40) → exhaustion.

Side bias:
- If M5 bb_pos <= -0.85 AND m5_rsi_centered <= -0.40 -> buy (sell at support / buy_limit lower).
- If M5 bb_pos >=  0.85 AND m5_rsi_centered >=  0.40 -> sell.
- Else if BB squeeze + extreme RSI on M1 → "both" (rare; conservative).

Key levels: H1 swing support & resistance (passed in features as
context_tf.swing_high_h1 / swing_low_h1). Invalidation = break of opposite swing
± 0.5 * ATR_H1.
"""

from __future__ import annotations

from ..models import LayerPlanRequest
from .base import Scenario, ScenarioResult, _safe


class RangeRevert:
    name = "RANGE_REVERT"

    def evaluate(self, req: LayerPlanRequest) -> ScenarioResult:
        ctx = req.features.context_tf
        conf = req.features.confirm_tf
        dec = req.features.decision_tf

        adx_h1 = _safe(ctx, "adx_strength")
        atr_h1 = _safe(ctx, "atr_abs_h1", default=_safe(ctx, "atr_pct"))
        swing_high = _safe(ctx, "swing_high_h1", req.market.last_close)
        swing_low = _safe(ctx, "swing_low_h1", req.market.last_close)

        bb_pos_m5 = _safe(conf, "bb_pos")
        rsi_m5 = _safe(conf, "rsi_centered")
        bb_pos_m1 = _safe(dec, "bb_pos")
        rsi_m1 = _safe(dec, "rsi_centered")

        score = 0.0
        reasons: list[str] = []

        # ADX low => ranging
        if adx_h1 < 0.20:
            score += 0.35
            reasons.append("ADX_LOW_H1")
        elif adx_h1 < 0.25:
            score += 0.10

        side = "none"
        invalidation: float | None = None
        if bb_pos_m5 <= -0.85 and rsi_m5 <= -0.40:
            side = "buy"
            score += 0.35
            reasons.append("BB_LOWER_M5")
            reasons.append("RSI_OVERSOLD_M5")
            invalidation = swing_low - 0.5 * abs(atr_h1)
        elif bb_pos_m5 >= 0.85 and rsi_m5 >= 0.40:
            side = "sell"
            score += 0.35
            reasons.append("BB_UPPER_M5")
            reasons.append("RSI_OVERBOUGHT_M5")
            invalidation = swing_high + 0.5 * abs(atr_h1)

        # M1 alignment bonus.
        if side == "buy" and bb_pos_m1 < -0.5 and rsi_m1 < 0:
            score += 0.15
            reasons.append("M1_ALIGNED")
        elif side == "sell" and bb_pos_m1 > 0.5 and rsi_m1 > 0:
            score += 0.15
            reasons.append("M1_ALIGNED")

        confidence = min(1.0, max(0.0, score))

        key_levels = {
            "support": swing_low,
            "resistance": swing_high,
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


Scenario  # type-check assertion via Protocol structural typing
