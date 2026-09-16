"""
V4 Planner — Liquidity Zone Entry with Partial TP + Break-Even.

Build a 2-layer ladder inside a liquidity zone identified by LiquidityZoneEntry.

For BUY (zone_top=4156, zone_bottom=4153, sl_price=4150):
  L1 = buy_limit @ zone_top    (4156)   — fills first if pullback shallow
  L2 = buy_limit @ zone_bottom (4153)   — fills if pullback deeper
  SL both layers = sl_price (4150)

Risk distribution:
  total_risk = equity * total_risk_pct (default 1%)
  Each layer = 50% of total risk
  Lots per layer sized so layer's loss-at-SL = its risk share

Exit (EA enforces):
  - At +30 pip profit on a position: close 50% volume + move SL to entry (BE)
  - At +100 pip profit: close remainder
  - At basket max lifetime (30 min): force close all

Magic numbers: 250550..250554.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..models import LiquidityZone, V4Layer, V4Plan, V4PlanRequest
from ..scenarios.base import ScenarioResult
from .sizing import compute_lots, floor_to_step

DEFAULT_TOTAL_RISK_PCT = 0.01
MAX_LIFETIME_SECONDS = 1800     # 30 min
BASE_MAGIC = 250550
PARTIAL_TP_PIPS = 30.0
PARTIAL_CLOSE_FRACTION = 0.50
RUNNER_CAP_PIPS = 100.0
LAYER_FRACTIONS = (0.50, 0.50)  # split 50/50 across 2 layers


def _now_utc_plus(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _round(v: float, digits: int) -> float:
    return round(v, max(0, digits))


def _empty_plan(reason: str) -> V4Plan:
    return V4Plan(
        scenario="NONE",
        side_bias="none",
        confidence=0.0,
        zone=None,
        max_lifetime_seconds=MAX_LIFETIME_SECONDS,
        valid_until_utc=_now_utc_plus(MAX_LIFETIME_SECONDS),
        layers=[],
        reason_codes=[reason],
        rationale_short=f"V4 empty: {reason}"[:240],
    )


def build_plan_v4(
    *,
    scenario: ScenarioResult,
    req: V4PlanRequest,
) -> V4Plan:
    if scenario.side not in ("buy", "sell"):
        return _empty_plan("INVALID_SIDE")

    side = scenario.side
    kl = scenario.key_levels
    zone_top = float(kl.get("zone_top", 0.0))
    zone_bottom = float(kl.get("zone_bottom", 0.0))
    sl_price = float(kl.get("sl_price", 0.0))
    pip_size = float(kl.get("pip_size", 0.10))

    if zone_top <= 0 or zone_bottom <= 0 or sl_price <= 0 or pip_size <= 0:
        return _empty_plan("ZONE_INVALID")

    digits = max(0, req.market.digits)
    tick_size = req.market.tick_size if req.market.tick_size > 0 else 10 ** (-digits)
    tick_value = req.market.tick_value if req.market.tick_value > 0 else 1.0

    volume_step = float(req.features.execution_tf.get("volume_step", 0.01))
    volume_min = float(req.features.execution_tf.get("volume_min", 0.01))
    volume_max = float(req.features.execution_tf.get("volume_max", 100.0))
    max_exposure = float(req.risk_state.max_symbol_exposure_lots or 0.0)

    total_risk_pct = (
        req.total_risk_pct_override
        if req.total_risk_pct_override is not None
        else DEFAULT_TOTAL_RISK_PCT
    )
    total_risk_amount = req.account.equity * total_risk_pct

    # Layer prices: BUY = top, bottom (descending). SELL = bottom, top (ascending).
    if side == "buy":
        layer_prices = (zone_top, zone_bottom)
        order_type = "buy_limit"
    else:
        layer_prices = (zone_bottom, zone_top)
        order_type = "sell_limit"

    layers: list[V4Layer] = []
    for i, (price_raw, frac) in enumerate(zip(layer_prices, LAYER_FRACTIONS)):
        price = _round(price_raw, digits)
        sl_distance = abs(price - sl_price)
        if sl_distance <= 0:
            continue

        risk_for_layer = total_risk_amount * frac
        lots = compute_lots(
            risk_amount=risk_for_layer,
            sl_distance_price=sl_distance,
            tick_size=tick_size,
            tick_value=tick_value,
            volume_step=volume_step,
            volume_min=volume_min,
            volume_max=volume_max,
        )
        if max_exposure > 0:
            cap = max_exposure * frac
            if lots > cap:
                lots = max(volume_min, floor_to_step(cap, volume_step, minimum=volume_min))

        layers.append(
            V4Layer(
                layer_id=i + 1,
                order_type=order_type,  # type: ignore[arg-type]
                price=price,
                lots=lots,
                sl=_round(sl_price, digits),
                magic=BASE_MAGIC + (i + 1),
                partial_tp_pips=PARTIAL_TP_PIPS,
                partial_close_fraction=PARTIAL_CLOSE_FRACTION,
                runner_cap_pips=RUNNER_CAP_PIPS,
                move_sl_to_entry_after_partial=True,
            )
        )

    if not layers:
        return _empty_plan("NO_LAYERS_BUILT")

    width_pips = abs(zone_top - zone_bottom) / pip_size
    far_edge = zone_bottom if side == "buy" else zone_top
    sl_pips_from_zone = abs(far_edge - sl_price) / pip_size

    zone = LiquidityZone(
        side=side,  # type: ignore[arg-type]
        top=_round(zone_top, digits),
        bottom=_round(zone_bottom, digits),
        sl_price=_round(sl_price, digits),
        pip_size=pip_size,
        width_pips=round(width_pips, 2),
        sl_pips_from_zone=round(sl_pips_from_zone, 2),
    )

    rationale = (
        f"LIQUIDITY_ZONE {side.upper()} · zone {zone.bottom}-{zone.top} ({zone.width_pips}p) · "
        f"SL {zone.sl_price} (+{zone.sl_pips_from_zone}p) · partial @+{PARTIAL_TP_PIPS}p · "
        f"runner cap +{RUNNER_CAP_PIPS}p"
    )[:240]

    return V4Plan(
        scenario="LIQUIDITY_ZONE_ENTRY",
        side_bias=side,  # type: ignore[arg-type]
        confidence=round(scenario.confidence, 4),
        zone=zone,
        max_lifetime_seconds=MAX_LIFETIME_SECONDS,
        valid_until_utc=_now_utc_plus(MAX_LIFETIME_SECONDS),
        layers=layers,
        reason_codes=scenario.reason_codes,
        rationale_short=rationale,
    )
