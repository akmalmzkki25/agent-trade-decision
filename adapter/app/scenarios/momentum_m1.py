"""
MOMENTUM_M1 — V3 only.

Triggers (M1-specific fast momentum):
- M1 ATR percentile high (>= 0.70) — vol spike.
- 3 consecutive M1 candles same direction.
- RSI_M1 centered NOT extreme (|x| < 0.40) — room to extend.
- Spread to ATR_M1 reasonable (< 0.10).

Side bias: same direction as last 3 candles, must align with M5 EMA20 side.

Score:
- Base 0.40 at trigger.
- +0.20 if ADX_M15 >= 0.20.
- +0.15 if DI balance aligned with side.
- +0.15 if spread_to_atr_m1 < 0.10.
- Capped 1.0.

Invalidation: EMA50 M5 on the opposite side.

Required EA features (sent in V3PlanRequest):
- decision_tf:  m1_atr_pct, rsi_centered, last3_dir (+1/-1/0)
- confirm_tf:   ema20_m5, ema50_m5, adx_strength_m15
- context_tf:   adx_strength, di_balance
- execution_tf: atr_abs_m1, spread_to_atr_ratio
"""

from __future__ import annotations

from ..models import LayerPlanRequest, V3PlanRequest
from .base import ScenarioResult, _safe


class MomentumM1:
    name = "MOMENTUM_M1"

    def evaluate(self, req):
        # Accept both LayerPlanRequest (V2 reuse) and V3PlanRequest.
        ctx = req.features.context_tf
        conf = req.features.confirm_tf
        dec = req.features.decision_tf
        exe = req.features.execution_tf

        atr_pct_m1 = _safe(dec, "m1_atr_pct")
        rsi_m1 = _safe(dec, "rsi_centered")
        last3 = _safe(dec, "last3_dir")   # +1 = 3 up, -1 = 3 down, 0 = mixed
        spread_to_atr = _safe(exe, "spread_to_atr_ratio")

        ema20_m5 = _safe(conf, "ema20_m5", req.market.last_close)
        ema50_m5 = _safe(conf, "ema50_m5", req.market.last_close)
        adx_m15 = _safe(conf, "adx_strength_m15", default=_safe(ctx, "adx_strength"))
        adx_h1 = _safe(ctx, "adx_strength")
        di_balance = _safe(ctx, "di_balance")

        last = req.market.last_close

        # Hard gate: no vol spike → no momentum scenario.
        if atr_pct_m1 < 0.70:
            return ScenarioResult(self.name, 0.0, "none", 0.0, None, {}, ["ATR_M1_LOW"])
        if last3 == 0:
            return ScenarioResult(self.name, 0.0, "none", 0.0, None, {}, ["NO_3CANDLE_RUN"])
        if abs(rsi_m1) >= 0.40:
            return ScenarioResult(self.name, 0.0, "none", 0.0, None, {}, ["RSI_EXTREME_M1"])

        # Determine side: 3 candles + M5 EMA alignment.
        side = "none"
        invalidation = None
        if last3 > 0 and last > ema20_m5:
            side = "buy"
            invalidation = ema50_m5
        elif last3 < 0 and last < ema20_m5:
            side = "sell"
            invalidation = ema50_m5
        else:
            return ScenarioResult(self.name, 0.0, "none", 0.0, None, {}, ["EMA_MISALIGNED"])

        score = 0.40
        reasons: list[str] = ["MOMENTUM_M1"]

        if adx_m15 >= 0.20:
            score += 0.20
            reasons.append("ADX_M15_OK")
        if (side == "buy" and di_balance > 0.10) or (side == "sell" and di_balance < -0.10):
            score += 0.15
            reasons.append("DI_ALIGNED")
        if 0 <= spread_to_atr < 0.10:
            score += 0.15
            reasons.append("SPREAD_OK")
        if adx_h1 >= 0.25:
            score += 0.05
            reasons.append("H1_TREND_BONUS")

        confidence = min(1.0, max(0.0, score))

        return ScenarioResult(
            name=self.name,
            score=confidence,
            side=side,
            confidence=confidence,
            invalidation_price=invalidation,
            key_levels={"ema20_m5": ema20_m5, "ema50_m5": ema50_m5},
            reason_codes=reasons,
        )
