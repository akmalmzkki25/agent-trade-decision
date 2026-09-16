"""
AGGRESSIVE_BIAS — V3 always-on fallback scenario.

Designed so V3 fires bulk entries every M1 close UNLESS market is truly
indecisive. Picks side from a multi-factor vote of cheap TA signals:

  +1 vote BUY if price > EMA20_M5;        -1 if price < EMA20_M5
  +1 vote BUY if di_balance > +0.05;      -1 if di_balance < -0.05
  +1 vote BUY if last 3 M1 candles up;    -1 if last 3 M1 down
  +0.5 BUY if RSI_M1 centered > +0.10;    -0.5 if < -0.10
  +1 vote BUY if last > EMA50_M15;        -1 if last < EMA50_M15

  → side = sign(total_votes)
  → score = clamp(0.50 + 0.05 * |votes|, max 0.85)

Returns "none" only if |votes| < 1.0 (truly mixed).

Invalidation = EMA50_M15 (regime breakdown level).

The intent is high-frequency exposure: this is the floor that ensures V3
always has something to trade when other scenarios don't trigger.
"""

from __future__ import annotations

from ..models import V3PlanRequest
from .base import ScenarioResult, _safe


class AggressiveBias:
    name = "AGGRESSIVE_BIAS"

    def evaluate(self, req):
        ctx = req.features.context_tf
        conf = req.features.confirm_tf
        dec = req.features.decision_tf

        last = req.market.last_close
        ema20_m5 = _safe(conf, "ema20_m5", last)
        ema50_m5 = _safe(conf, "ema50_m5", last)
        ema50_m15 = _safe(conf, "ema50_m15", last)

        di_balance = _safe(ctx, "di_balance")
        rsi_m1 = _safe(dec, "rsi_centered")
        last3 = _safe(dec, "last3_dir")

        votes = 0.0
        reasons: list[str] = []

        if last > ema20_m5:
            votes += 1.0
            reasons.append("ABOVE_EMA20_M5")
        elif last < ema20_m5:
            votes -= 1.0
            reasons.append("BELOW_EMA20_M5")

        if di_balance > 0.05:
            votes += 1.0
            reasons.append("DI_BULL")
        elif di_balance < -0.05:
            votes -= 1.0
            reasons.append("DI_BEAR")

        if last3 > 0.5:
            votes += 1.0
            reasons.append("3CANDLES_UP")
        elif last3 < -0.5:
            votes -= 1.0
            reasons.append("3CANDLES_DOWN")

        if rsi_m1 > 0.10:
            votes += 0.5
            reasons.append("RSI_BULL_M1")
        elif rsi_m1 < -0.10:
            votes -= 0.5
            reasons.append("RSI_BEAR_M1")

        if last > ema50_m15:
            votes += 1.0
            reasons.append("ABOVE_EMA50_M15")
        elif last < ema50_m15:
            votes -= 1.0
            reasons.append("BELOW_EMA50_M15")

        if abs(votes) < 1.0:
            return ScenarioResult(
                name=self.name,
                score=0.30,
                side="none",
                confidence=0.30,
                invalidation_price=None,
                key_levels={},
                reason_codes=["FLAT_NO_BIAS"],
            )

        side = "buy" if votes > 0 else "sell"
        score = min(0.85, 0.50 + 0.05 * abs(votes))

        return ScenarioResult(
            name=self.name,
            score=score,
            side=side,
            confidence=score,
            invalidation_price=ema50_m15,
            key_levels={
                "ema20_m5": ema20_m5,
                "ema50_m5": ema50_m5,
                "ema50_m15": ema50_m15,
            },
            reason_codes=[f"AGG_BIAS_{side.upper()}", f"VOTES_{votes:+.1f}"] + reasons,
        )
