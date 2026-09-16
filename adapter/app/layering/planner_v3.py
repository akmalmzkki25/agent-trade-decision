"""
V3 Mixed-Order Planner.

Build a 5-layer ladder with mixed types:
  L1 = market  (anchor; weight 0.30)
  L2 = limit   (pullback shallow; weight 0.20)
  L3 = limit   (pullback deep; weight 0.15)
  L4 = stop    (momentum confirm; weight 0.20)
  L5 = stop    (momentum strong; weight 0.15)

Total risk = `equity * total_risk_pct` (default 1%), split by weight.

Spacing uses ATR_M1 (faster cadence vs V2 which uses ATR_M15):
  L2 offset = -0.4 * ATR_M1
  L3 offset = -0.8 * ATR_M1
  L4 offset = +0.4 * ATR_M1
  L5 offset = +0.8 * ATR_M1

SL per layer = max(1.0 * ATR_M5, distance to invalidation).

Hard cap per layer = max_symbol_exposure_lots * weight  (defends against
broker tick_value misreporting that could 100x oversize, same anti-bug pattern
as V2 fix).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..models import (
    BasketSlot,
    OrderType,
    V3ExitRules,
    V3Layer,
    V3Plan,
    V3PlanRequest,
)
from ..news import apply_bias_to_score
from ..scenarios.base import ScenarioResult
from .sizing import compute_lots, floor_to_step

DEFAULT_TOTAL_RISK_PCT = 0.01     # 1% per basket
BASKET_RR = 1.2
MAX_LIFETIME_SECONDS = 600        # 10 minutes
LAYER_COUNT = 5

# (offset_atr_m1, weight, type_suffix)
# offset signs are relative to anchor; multiplied by side direction.
_RECIPE_BUY: list[tuple[float, float, str]] = [
    ( 0.0,  0.30, "buy_market"),
    (-0.4,  0.20, "buy_limit"),
    (-0.8,  0.15, "buy_limit"),
    (+0.4,  0.20, "buy_stop"),
    (+0.8,  0.15, "buy_stop"),
]
_RECIPE_SELL: list[tuple[float, float, str]] = [
    ( 0.0,  0.30, "sell_market"),
    (+0.4,  0.20, "sell_limit"),
    (+0.8,  0.15, "sell_limit"),
    (-0.4,  0.20, "sell_stop"),
    (-0.8,  0.15, "sell_stop"),
]

_MAGIC_BASE_BY_SLOT = {"A": 250530, "B": 250540}


def _now_utc_plus(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _atr(req: V3PlanRequest, key: str, fallback_key: str = "atr_abs") -> float:
    exe = req.features.execution_tf
    val = exe.get(key, exe.get(fallback_key, 0.0))
    try:
        return float(val) if float(val) > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _round(v: float, digits: int) -> float:
    return round(v, max(0, digits))


def build_plan_v3(
    *,
    scenario: ScenarioResult,
    req: V3PlanRequest,
    slot: BasketSlot,
    gold_bias: float = 0.0,
    exit_rules: V3ExitRules | None = None,
) -> V3Plan:
    if scenario.side not in ("buy", "sell"):
        raise ValueError(f"scenario.side must be buy/sell, got {scenario.side}")

    if exit_rules is None:
        exit_rules = V3ExitRules()
    side = scenario.side

    atr_m1 = _atr(req, "atr_abs_m1")
    atr_m5 = _atr(req, "atr_abs_m5", fallback_key="atr_abs_m1")
    if atr_m1 <= 0 or atr_m5 <= 0:
        return _empty(scenario, slot, "ATR_MISSING")

    anchor = req.market.ask if side == "buy" else req.market.bid
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

    recipe = _RECIPE_BUY if side == "buy" else _RECIPE_SELL
    invalidation = scenario.invalidation_price
    base_magic = _MAGIC_BASE_BY_SLOT[slot]

    layers: list[V3Layer] = []
    for i, (offset_atr, weight, otype) in enumerate(recipe):
        price = anchor + offset_atr * atr_m1
        price = _round(price, digits)

        # SL distance: 1.0 * ATR_M5, or distance to invalidation if larger.
        sl_dist_atr = 1.0 * atr_m5
        sl_dist_inv = 0.0
        if invalidation is not None:
            if side == "buy":
                sl_dist_inv = max(0.0, price - invalidation)
            else:
                sl_dist_inv = max(0.0, invalidation - price)
        sl_distance = max(sl_dist_atr, sl_dist_inv)

        sl_price = (price - sl_distance) if side == "buy" else (price + sl_distance)
        sl_price = _round(sl_price, digits)

        # Per-layer TP at RR ratio (default 1.0 = 1:1). Set to None when ratio=0.
        rr = exit_rules.rr_ratio
        if rr > 0:
            tp_distance = sl_distance * rr
            tp_price = (price + tp_distance) if side == "buy" else (price - tp_distance)
            tp_price = _round(tp_price, digits)
        else:
            tp_price = None

        risk_for_layer = total_risk_amount * weight
        lots = compute_lots(
            risk_amount=risk_for_layer,
            sl_distance_price=sl_distance,
            tick_size=tick_size,
            tick_value=tick_value,
            volume_step=volume_step,
            volume_min=volume_min,
            volume_max=volume_max,
        )
        # Hard cap per layer = exposure cap × weight (anti tick_value 100x bug).
        if max_exposure > 0:
            cap = max_exposure * weight
            if lots > cap:
                lots = max(volume_min, floor_to_step(cap, volume_step, minimum=volume_min))

        layers.append(
            V3Layer(
                layer_id=i + 1,
                order_type=otype,  # type: ignore[arg-type]
                price=price,
                lots=lots,
                sl=sl_price,
                tp=tp_price,
                magic=base_magic + (i + 1),
                is_anchor=(i == 0),
                weight=weight,
            )
        )

    # Apply bias modifier to confidence (info only here; score already used in selector).
    confidence = apply_bias_to_score(scenario.confidence, side, gold_bias)

    rationale = (
        f"{scenario.name} {side.upper()} V3 mixed-ladder · slot {slot} · "
        f"anchor={anchor:.{digits}f} · atr_m1={atr_m1:.{digits}f} · "
        f"risk={total_risk_pct*100:.2f}% · bias={gold_bias:+.2f}"
    )[:240]

    return V3Plan(
        scenario=scenario.name,  # type: ignore[arg-type]
        side_bias=side,  # type: ignore[arg-type]
        confidence=round(confidence, 4),
        basket_slot=slot,
        basket_tp_pct_equity=total_risk_pct * BASKET_RR * 100.0,
        scenario_invalidation_price=(
            _round(invalidation, digits) if invalidation is not None else None
        ),
        max_lifetime_seconds=MAX_LIFETIME_SECONDS,
        valid_until_utc=_now_utc_plus(MAX_LIFETIME_SECONDS),
        layers=layers,
        reason_codes=scenario.reason_codes,
        rationale_short=rationale,
        exit_rules=exit_rules,
    )


def _empty(scenario: ScenarioResult, slot: BasketSlot, reason: str) -> V3Plan:
    return V3Plan(
        scenario="NONE",
        side_bias="none",
        confidence=scenario.confidence,
        basket_slot=slot,
        basket_tp_pct_equity=0.0,
        scenario_invalidation_price=scenario.invalidation_price,
        max_lifetime_seconds=MAX_LIFETIME_SECONDS,
        valid_until_utc=_now_utc_plus(MAX_LIFETIME_SECONDS),
        layers=[],
        reason_codes=scenario.reason_codes + [reason],
        rationale_short=f"V3 empty plan: {reason}",
    )


def assign_slot(active_baskets: list, side: str) -> tuple[BasketSlot | None, str]:
    """
    Returns (slot, reason). slot=None => veto with reason code.
    """
    sides_in_use = {b.side: b.slot for b in active_baskets}
    if side in sides_in_use:
        return None, "APP-CONC-409"   # same-side parallel forbidden
    used_slots = {b.slot for b in active_baskets}
    if "A" not in used_slots:
        return "A", "OK"
    if "B" not in used_slots:
        return "B", "OK"
    return None, "APP-CAPACITY-409"
