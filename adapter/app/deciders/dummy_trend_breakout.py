"""
Dummy Trend Breakout decider — Phase 1 baseline.

Rule-based policy used to validate the end-to-end EA <-> Adapter <-> Ledger path
before plugging in Claude Opus 4.7 (Phase 2). Thresholds follow the prior table
in knowledge/Rancangan Bot Trading... (line 124-131): trend stack requires
ADX strength, directional bias, and a confirmed breakout off the recent range.

Feature contract (the EA MUST send these keys):
  context_tf  (H1):  adx_strength, di_balance, ma_gap_atr, atr_pct
  decision_tf (M15): rsi_centered, bb_pos, price_vs_ma_fast_atr,
                     breakout_up_flag, breakout_dn_flag
  execution_tf:      spread_to_atr_ratio, last_bar_body_atr, atr_abs

Missing keys default to 0.0 (treated as weak/neutral evidence).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from ..models import (
    AdapterError,
    DecisionRequest,
    DecisionResponse,
    ResponseMeta,
    TradeDecision,
)
from ..settings import settings

# Engineering priors (see knowledge docs). Tunable later via env if needed.
MIN_ADX_STRENGTH = 0.20      # ADX >= 20
MIN_DI_BALANCE = 0.15
MIN_PRICE_VS_MA_ATR = 0.10
MAX_SPREAD_TO_ATR = 0.20     # spread too wide vs ATR => skip
SL_ATR_MULT = 2.0
TP_ATR_MULT = 3.0
MIN_CONFIDENCE = 0.55
DEFAULT_RISK_PCT = 0.005     # 0.5% per trade if not provided


def _safe(features: dict[str, float], key: str, default: float = 0.0) -> float:
    val = features.get(key, default)
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _valid_until(seconds: int = 120) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _hold(req: DecisionRequest, reason: str, code: str, confidence: float = 0.0) -> DecisionResponse:
    return DecisionResponse(
        schema_version="trade-decision-response.v1",
        request_id=req.request_id,
        status="ok",
        decision=TradeDecision(
            action="hold",
            side="flat",
            order_type="none",
            lots=0.0,
            entry_price=None,
            sl=None,
            tp=None,
            max_deviation_points=settings.adapter_default_max_deviation_points,
            valid_until_utc=_valid_until(60),
            confidence=confidence,
            rationale_short=reason[:240],
            reason_codes=[code],
            risk_note="",
        ),
        meta=ResponseMeta(model="dummy_trend_breakout"),
        error=None,
    )


def _round_lots(lots: float, step: float = 0.01, min_lots: float = 0.01) -> float:
    if lots <= 0:
        return 0.0
    rounded = math.floor(lots / step) * step
    return max(min_lots, round(rounded, 2))


def _compute_lots(equity: float, risk_pct: float, sl_distance_price: float, tick_size: float, tick_value: float) -> float:
    """Approximate sizing: risk_amount / (sl_distance_in_ticks * tick_value)."""
    if sl_distance_price <= 0 or tick_size <= 0 or tick_value <= 0:
        return 0.01
    risk_amount = equity * risk_pct
    sl_ticks = sl_distance_price / tick_size
    if sl_ticks <= 0:
        return 0.01
    raw_lots = risk_amount / (sl_ticks * tick_value)
    return _round_lots(raw_lots)


class DummyTrendBreakoutDecider:
    name = "dummy_trend_breakout"

    def decide(self, req: DecisionRequest) -> DecisionResponse:
        ctx = req.features.context_tf
        dec = req.features.decision_tf
        exe = req.features.execution_tf

        # Hard precheck: spread vs ATR.
        spread_ratio = _safe(exe, "spread_to_atr_ratio")
        if spread_ratio > MAX_SPREAD_TO_ATR:
            return _hold(req, f"Spread too wide vs ATR ({spread_ratio:.3f}).", "SPREAD_GUARD")

        adx_strength = _safe(ctx, "adx_strength")
        di_balance = _safe(ctx, "di_balance")
        price_vs_ma = _safe(dec, "price_vs_ma_fast_atr")
        brk_up = _safe(dec, "breakout_up_flag")
        brk_dn = _safe(dec, "breakout_dn_flag")
        atr_abs = _safe(exe, "atr_abs")

        if adx_strength < MIN_ADX_STRENGTH:
            return _hold(req, f"ADX too weak ({adx_strength:.2f}).", "ADX_WEAK")

        # Resolve direction.
        side: str = "flat"
        if di_balance >= MIN_DI_BALANCE and price_vs_ma >= MIN_PRICE_VS_MA_ATR and brk_up > 0.5:
            side = "buy"
        elif di_balance <= -MIN_DI_BALANCE and price_vs_ma <= -MIN_PRICE_VS_MA_ATR and brk_dn > 0.5:
            side = "sell"
        else:
            return _hold(req, "No directional breakout confirmation.", "NO_BREAKOUT")

        # Confidence: simple monotonic mapping bounded by ADX & alignment.
        align = min(abs(di_balance), 1.0) * 0.5 + min(abs(price_vs_ma), 1.0) * 0.3
        confidence = max(0.0, min(0.85, 0.45 + adx_strength + align * 0.15))
        if confidence < MIN_CONFIDENCE:
            return _hold(req, f"Confidence below threshold ({confidence:.2f}).", "LOW_CONFIDENCE", confidence)

        # Build SL/TP using ATR.
        if atr_abs <= 0:
            return _hold(req, "Missing ATR for sizing.", "ATR_MISSING", confidence)

        entry_price = req.market.ask if side == "buy" else req.market.bid
        sl_distance = SL_ATR_MULT * atr_abs
        tp_distance = TP_ATR_MULT * atr_abs
        if side == "buy":
            sl = entry_price - sl_distance
            tp = entry_price + tp_distance
        else:
            sl = entry_price + sl_distance
            tp = entry_price - tp_distance

        # Sizing.
        risk_pct = (req.risk_state.max_risk_per_trade_pct / 100.0) if req.risk_state.max_risk_per_trade_pct > 0 else DEFAULT_RISK_PCT
        lots = _compute_lots(
            equity=req.account.equity,
            risk_pct=risk_pct,
            sl_distance_price=sl_distance,
            tick_size=req.market.tick_size,
            tick_value=req.market.tick_value,
        )

        # Cap by max_symbol_exposure_lots.
        if req.risk_state.max_symbol_exposure_lots > 0:
            lots = min(lots, req.risk_state.max_symbol_exposure_lots)
        if lots <= 0:
            return _hold(req, "Computed lots <= 0.", "LOTS_ZERO", confidence)

        digits = max(0, req.market.digits)
        sl = round(sl, digits) if digits else sl
        tp = round(tp, digits) if digits else tp

        reasons = ["TREND_OK", "MOMENTUM_OK"]
        if spread_ratio < 0.05:
            reasons.append("SPREAD_OK")
        if confidence >= 0.70:
            reasons.append("HIGH_CONF")

        decision = TradeDecision(
            action="open",
            side=side,
            order_type="market",
            lots=lots,
            entry_price=None,
            sl=sl,
            tp=tp,
            max_deviation_points=settings.adapter_default_max_deviation_points,
            valid_until_utc=_valid_until(120),
            confidence=round(confidence, 4),
            rationale_short=(
                f"ADX {adx_strength:.2f}, DI {di_balance:+.2f}, "
                f"breakout {'up' if side == 'buy' else 'dn'}, ATR sizing."
            )[:240],
            reason_codes=reasons,
            risk_note=f"Skip if spread_to_atr > {MAX_SPREAD_TO_ATR} before send.",
        )

        return DecisionResponse(
            schema_version="trade-decision-response.v1",
            request_id=req.request_id,
            status="ok",
            decision=decision,
            meta=ResponseMeta(model=self.name),
            error=None,
        )


# Convenience export for direct construction
__all__ = ["DummyTrendBreakoutDecider"]
