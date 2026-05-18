"""
CONTINUATION_PULLBACK scenario detector.

Trigger conditions:
- M15 ADX moderate (>= 0.22).
- Price above EMA50_M15 (bullish trend) or below (bearish trend) — bias by trend.
- Price pulled back close to EMA20_M5 (|price - ema20_m5| / atr_m5 <= 0.50).
- M5 RSI not extreme on the opposite side (avoid catching reversal).

Key levels: EMA20_M5, EMA50_M5, EMA50_M15. Invalidation = close M15 across EMA50_M15
in opposite direction.
"""

from __future__ import annotations

from ..models import LayerPlanRequest
from .base import Scenario, ScenarioResult, _safe


class ContinuationPullback:
    name = "CONTINUATION_PULLBACK"

    def evaluate(self, req: LayerPlanRequest) -> ScenarioResult:
        ctx = req.features.context_tf
        conf = req.features.confirm_tf
        exe = req.features.execution_tf

        adx_h1 = _safe(ctx, "adx_strength")
        adx_m15 = _safe(conf, "adx_strength_m15", default=adx_h1)
        ema50_m15 = _safe(conf, "ema50_m15", req.market.last_close)
        ema20_m5 = _safe(conf, "ema20_m5", req.market.last_close)
        ema50_m5 = _safe(conf, "ema50_m5", req.market.last_close)
        atr_m5 = _safe(exe, "atr_abs_m5", default=_safe(exe, "atr_abs"))
        rsi_m5 = _safe(conf, "rsi_centered")

        last = req.market.last_close

        score = 0.0
        reasons: list[str] = []

        if adx_m15 >= 0.22:
            score += 0.25
            reasons.append("ADX_TREND_M15")
        elif adx_m15 >= 0.18:
            score += 0.10

        side = "none"
        invalidation: float | None = None

        trend_bull = last > ema50_m15
        trend_bear = last < ema50_m15

        if atr_m5 > 0:
            dist_to_ema20 = abs(last - ema20_m5) / atr_m5
        else:
            dist_to_ema20 = 99.0

        # Pullback proximity.
        proximity_ok = dist_to_ema20 <= 0.50

        if trend_bull and proximity_ok and rsi_m5 > -0.40:
            side = "buy"
            score += 0.30
            reasons.append("UPTREND_M15")
            reasons.append("PULLBACK_TO_EMA20_M5")
            invalidation = ema50_m15
        elif trend_bear and proximity_ok and rsi_m5 < 0.40:
            side = "sell"
            score += 0.30
            reasons.append("DOWNTREND_M15")
            reasons.append("PULLBACK_TO_EMA20_M5")
            invalidation = ema50_m15

        # Bonus: pullback shallow vs EMA50_M5.
        if side == "buy" and last > ema50_m5:
            score += 0.15
            reasons.append("ABOVE_EMA50_M5")
        elif side == "sell" and last < ema50_m5:
            score += 0.15
            reasons.append("BELOW_EMA50_M5")

        if adx_h1 >= 0.25 and (
            (side == "buy" and ema50_m15 < last) or (side == "sell" and ema50_m15 > last)
        ):
            score += 0.15
            reasons.append("H1_TREND_CONFIRM")

        confidence = min(1.0, max(0.0, score))

        key_levels = {
            "ema20_m5": ema20_m5,
            "ema50_m5": ema50_m5,
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
