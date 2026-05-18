"""
Build a LayerPlan from a winning ScenarioResult.

Number of layers (N):
  score in [0.55, 0.65) → 3
  score in [0.65, 0.78) → 4
  score >= 0.78         → 5

Spacing:
  step_i = max(0.5*ATR_M15, base_step) * (1 + 0.4*i)   (i=0..N-1, in price units)

Anchor & direction depend on scenario name + side.

Risk:
  total_risk_amount = equity * total_risk_pct
  risk_per_layer = total_risk_amount / N
  SL distance per layer = max(1.5 * ATR_M15, distance_to_invalidation)
  Lots per layer = risk_per_layer / (sl_ticks * tick_value)

Basket TP:
  basket_tp_pct_equity = total_risk_pct * BASKET_RR   (default RR = 1.5)

Expiration: now + EXPIRY_MINUTES.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..models import LayerEntry, LayerPlan, LayerPlanRequest, OrderType
from ..scenarios.base import ScenarioResult
from .sizing import compute_lots, floor_to_step

DEFAULT_TOTAL_RISK_PCT = 0.01      # 1% equity per scenario
BASKET_RR = 1.5
EXPIRY_MINUTES = 30
BASE_MAGIC = 250519


def _layer_count(score: float) -> int:
    if score >= 0.78:
        return 5
    if score >= 0.65:
        return 4
    return 3


def _atr_m15(req: LayerPlanRequest) -> float:
    exe = req.features.execution_tf
    val = exe.get("atr_abs_m15", exe.get("atr_abs", 0.0))
    try:
        return float(val) if float(val) > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _expiration_iso(minutes: int = EXPIRY_MINUTES) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def _round_price(price: float, digits: int) -> float:
    return round(price, max(0, digits))


def _order_type_for(scenario: str, side: str) -> OrderType:
    """Map scenario + side to MT5 pending order type."""
    if scenario == "RANGE_REVERT":
        return "buy_limit" if side == "buy" else "sell_limit"
    if scenario == "TREND_BREAKOUT":
        return "buy_stop" if side == "buy" else "sell_stop"
    if scenario == "CONTINUATION_PULLBACK":
        return "buy_limit" if side == "buy" else "sell_limit"
    raise ValueError(f"Unknown scenario: {scenario}")


def _anchor_for(scenario: str, side: str, kl: dict[str, float], last_close: float) -> float:
    if scenario == "RANGE_REVERT":
        return kl.get("support") if side == "buy" else kl.get("resistance")  # type: ignore[return-value]
    if scenario == "TREND_BREAKOUT":
        return kl.get("breakout_high") if side == "buy" else kl.get("breakout_low")  # type: ignore[return-value]
    if scenario == "CONTINUATION_PULLBACK":
        return kl.get("ema20_m5", last_close)
    return last_close


def _layer_direction_sign(scenario: str, side: str) -> int:
    """+1 means price ADD as layer index grows; -1 means SUBTRACT."""
    if scenario == "RANGE_REVERT":
        # buy_limit below support → subtract; sell_limit above resistance → add.
        return -1 if side == "buy" else +1
    if scenario == "TREND_BREAKOUT":
        # buy_stop above high → add; sell_stop below low → subtract.
        return +1 if side == "buy" else -1
    if scenario == "CONTINUATION_PULLBACK":
        # buy_limit below ema20 → subtract; sell_limit above → add.
        return -1 if side == "buy" else +1
    return 0


def build_plan(
    *,
    scenario: ScenarioResult,
    req: LayerPlanRequest,
    base_step: float | None = None,
) -> LayerPlan:
    if scenario.side not in ("buy", "sell"):
        raise ValueError(f"Scenario side must be buy/sell, got {scenario.side}")

    side = scenario.side
    n = _layer_count(scenario.score)
    atr_m15 = _atr_m15(req)
    if atr_m15 <= 0:
        # No ATR → can't size safely; emit empty plan signal by returning 0 layers.
        return LayerPlan(
            scenario="NONE",  # type: ignore[arg-type]
            side_bias="none",
            confidence=scenario.confidence,
            basket_tp_pct_equity=0.0,
            scenario_invalidation_price=scenario.invalidation_price,
            valid_until_utc=_expiration_iso(EXPIRY_MINUTES),
            layers=[],
            reason_codes=scenario.reason_codes + ["ATR_MISSING"],
            rationale_short="ATR missing; cannot build layers.",
        )

    step = max(0.5 * atr_m15, base_step or 0.0)
    sign = _layer_direction_sign(scenario.name, side)
    anchor = _anchor_for(scenario.name, side, scenario.key_levels, req.market.last_close)
    if anchor is None or anchor <= 0:
        anchor = req.market.last_close

    total_risk_pct = (
        req.total_risk_pct_override
        if req.total_risk_pct_override is not None
        else DEFAULT_TOTAL_RISK_PCT
    )
    total_risk_amount = req.account.equity * total_risk_pct
    risk_per_layer = total_risk_amount / n

    invalidation = scenario.invalidation_price
    digits = max(0, req.market.digits)
    tick_size = req.market.tick_size if req.market.tick_size > 0 else 10 ** (-digits)
    tick_value = req.market.tick_value if req.market.tick_value > 0 else 1.0

    volume_step = float(req.features.execution_tf.get("volume_step", 0.01))
    volume_min = float(req.features.execution_tf.get("volume_min", 0.01))
    volume_max = float(req.features.execution_tf.get("volume_max", 100.0))

    layers: list[LayerEntry] = []
    for i in range(n):
        offset = step * (1.0 + 0.4 * i)
        price = anchor + sign * offset
        price = _round_price(price, digits)

        sl_dist_atr = 1.5 * atr_m15
        sl_dist_inv = 0.0
        if invalidation is not None:
            if side == "buy":
                sl_dist_inv = max(0.0, price - invalidation)
            else:
                sl_dist_inv = max(0.0, invalidation - price)
        sl_distance = max(sl_dist_atr, sl_dist_inv)

        sl_price = price - sl_distance if side == "buy" else price + sl_distance
        sl_price = _round_price(sl_price, digits)

        lots = compute_lots(
            risk_amount=risk_per_layer,
            sl_distance_price=sl_distance,
            tick_size=tick_size,
            tick_value=tick_value,
            volume_step=volume_step,
            volume_min=volume_min,
            volume_max=volume_max,
        )

        layers.append(
            LayerEntry(
                layer_id=i + 1,
                order_type=_order_type_for(scenario.name, side),
                price=price,
                lots=lots,
                sl=sl_price,
                tp=None,
                expiration_utc=_expiration_iso(EXPIRY_MINUTES),
                magic=BASE_MAGIC + (i + 1),
            )
        )

    rationale = (
        f"{scenario.name} {side.upper()} — {n} layers, anchor={anchor:.{digits}f}, "
        f"step≈{step:.{digits}f}, total_risk={total_risk_pct*100:.2f}%."
    )[:240]

    return LayerPlan(
        scenario=scenario.name,  # type: ignore[arg-type]
        side_bias=side,  # type: ignore[arg-type]
        confidence=scenario.confidence,
        basket_tp_pct_equity=total_risk_pct * BASKET_RR * 100.0,  # percent points
        scenario_invalidation_price=(
            _round_price(invalidation, digits) if invalidation is not None else None
        ),
        valid_until_utc=_expiration_iso(EXPIRY_MINUTES),
        layers=layers,
        reason_codes=scenario.reason_codes,
        rationale_short=rationale,
    )
