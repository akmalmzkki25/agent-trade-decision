"""
LIQUIDITY_ZONE_ENTRY — V4 only.

Identify a fresh H1 liquidity pool (recent untested swing low/high), then build
a 30-pip zone with a 10-pip offset and an extra 20-pip SL buffer (=50 pip total
from far edge, matching the user's spec example):

  zone buy 4156-4153 → SL 4150  (60 pip from L1=4156, 30 pip from L2=4153)

Required features sent by EA (V4PlanRequest):
  context_tf:
    - swing_high_h1  : recent H1 swing high (lookback 20 bar)
    - swing_low_h1   : recent H1 swing low (lookback 20 bar)
  confirm_tf:                   (M15 + M5 context)
    - close_m15      : last closed M15 close
    - close_m5       : last closed M5 close
    - m5_body_atr    : last M5 candle body / atr_m5
    - m5_direction   : sign of last M5 close - open (+1/-1/0)
  decision_tf:                  (M1 — execution-time)
    - close_m1
  execution_tf:
    - pip_size       : pip in price units (e.g. 0.10 for XAUUSD)
    - atr_abs_m5

Rules (BUY):
  - Price currently ABOVE swing_low_h1 (approaching zone from above).
  - Zone top = swing_low_h1 + 10 * pip_size
  - Zone bottom = top - 30 * pip_size
  - Zone is "fresh" if price is more than 30 pips above zone top right now
    (i.e. we haven't reached the zone yet — we'll wait with limits).
  - M15 close > zone top  (approaching, not already inside)
  - M5 last candle rejection candle preferred: bearish body (m5_direction < 0)
    OR small body (consolidating before pullback).

Returns ScenarioResult with side, score, and key_levels carrying the zone
coordinates for the planner.
"""

from __future__ import annotations

from ..models import V4PlanRequest
from .base import ScenarioResult, _safe

ZONE_WIDTH_PIPS = 30.0
ZONE_OFFSET_PIPS = 10.0       # offset from swing high/low into the zone
SL_BUFFER_PIPS = 20.0         # SL distance beyond zone (BUY: below bottom)
MIN_DISTANCE_TO_ZONE_PIPS = 5.0   # zone must not be currently inside
MAX_DISTANCE_TO_ZONE_PIPS = 200.0  # but not too far either


class LiquidityZoneEntry:
    name = "LIQUIDITY_ZONE_ENTRY"

    def evaluate(self, req: V4PlanRequest) -> ScenarioResult:
        ctx = req.features.context_tf
        conf = req.features.confirm_tf
        exe = req.features.execution_tf

        swing_high_h1 = _safe(ctx, "swing_high_h1")
        swing_low_h1 = _safe(ctx, "swing_low_h1")
        close_m15 = _safe(conf, "close_m15", req.market.last_close)
        m5_direction = _safe(conf, "m5_direction")
        m5_body_atr = _safe(conf, "m5_body_atr")
        pip_size = _safe(exe, "pip_size", 0.10)
        atr_m5 = _safe(exe, "atr_abs_m5")

        if pip_size <= 0 or atr_m5 <= 0 or swing_high_h1 <= 0 or swing_low_h1 <= 0:
            return ScenarioResult(
                self.name, 0.0, "none", 0.0, None, {}, ["FEATURES_MISSING"]
            )

        last = req.market.last_close
        zone_w = ZONE_WIDTH_PIPS * pip_size
        zone_off = ZONE_OFFSET_PIPS * pip_size
        sl_buf = SL_BUFFER_PIPS * pip_size
        min_d = MIN_DISTANCE_TO_ZONE_PIPS * pip_size
        max_d = MAX_DISTANCE_TO_ZONE_PIPS * pip_size

        # --- Try BUY zone (above swing_low_h1) ---
        buy_top = swing_low_h1 + zone_off
        buy_bottom = buy_top - zone_w
        dist_to_buy_top = last - buy_top
        if (
            dist_to_buy_top > min_d
            and dist_to_buy_top < max_d
            and close_m15 > buy_top
        ):
            score = 0.55
            reasons = ["H1_SWING_LOW", "M15_ABOVE_ZONE"]
            # Bonus: M5 rejection candle (bearish small move = pulling back)
            if m5_direction <= 0:
                score += 0.10
                reasons.append("M5_PULLBACK")
            if m5_body_atr < 0.30:
                score += 0.05
                reasons.append("M5_SMALL_BODY")
            sl_price = buy_bottom - sl_buf
            return ScenarioResult(
                name=self.name,
                score=min(score, 0.85),
                side="buy",
                confidence=min(score, 0.85),
                invalidation_price=sl_price,
                key_levels={
                    "zone_top": buy_top,
                    "zone_bottom": buy_bottom,
                    "sl_price": sl_price,
                    "pip_size": pip_size,
                },
                reason_codes=reasons,
            )

        # --- Try SELL zone (below swing_high_h1) ---
        sell_bottom = swing_high_h1 - zone_off
        sell_top = sell_bottom + zone_w
        dist_to_sell_bottom = sell_bottom - last
        if (
            dist_to_sell_bottom > min_d
            and dist_to_sell_bottom < max_d
            and close_m15 < sell_bottom
        ):
            score = 0.55
            reasons = ["H1_SWING_HIGH", "M15_BELOW_ZONE"]
            if m5_direction >= 0:
                score += 0.10
                reasons.append("M5_PULLBACK")
            if m5_body_atr < 0.30:
                score += 0.05
                reasons.append("M5_SMALL_BODY")
            sl_price = sell_top + sl_buf
            return ScenarioResult(
                name=self.name,
                score=min(score, 0.85),
                side="sell",
                confidence=min(score, 0.85),
                invalidation_price=sl_price,
                key_levels={
                    "zone_top": sell_top,
                    "zone_bottom": sell_bottom,
                    "sl_price": sl_price,
                    "pip_size": pip_size,
                },
                reason_codes=reasons,
            )

        return ScenarioResult(
            self.name, 0.0, "none", 0.0, None, {},
            ["NO_VALID_ZONE_OR_OUT_OF_RANGE"]
        )
