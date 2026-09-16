"""
V5 Scalping Burst Planner.

Build a "burst" = 3 micro market orders fired at current bid/ask. No per-order
TP/SL — basket-level exits managed by EA based on V5ExitRules.

Sizing rule:
  - Each micro layer = volume_min × MICRO_MULTIPLIER (default 1 = broker minimum)
  - All 3 layers same size for simplicity
  - Hard-capped by max_symbol_exposure_lots / 3 / max_bursts_per_basket
    so worst-case (3 bursts × 3 layers = 9 positions) ≤ exposure cap

Magic per layer: BASE_MAGIC + i (1..MAX_LAYERS_PER_BURST × max_bursts)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..models import V5Burst, V5BurstRequest, V5ExitRules, V5MicroLayer
from ..scenarios.base import ScenarioResult
from .sizing import floor_to_step

LAYERS_PER_BURST = 3
MICRO_MULTIPLIER = 1.0          # broker volume_min × this; raise for bigger micros
BASE_MAGIC = 250560
MAGIC_SLOTS = 10                 # 250560..250569 reserved for V5


def _now_utc_plus(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _empty_burst(reason: str) -> V5Burst:
    return V5Burst(
        scenario="NONE",
        side_bias="none",
        confidence=0.0,
        valid_until_utc=_now_utc_plus(60),
        layers=[],
        reason_codes=[reason],
        rationale_short=f"V5 empty: {reason}"[:240],
    )


def build_burst_v5(
    *,
    scenario: ScenarioResult,
    req: V5BurstRequest,
    exit_rules: V5ExitRules | None = None,
) -> V5Burst:
    if scenario.side not in ("buy", "sell"):
        return _empty_burst("INVALID_SIDE")

    if exit_rules is None:
        exit_rules = V5ExitRules()

    side = scenario.side
    otype = "buy_market" if side == "buy" else "sell_market"

    volume_step = float(req.features.execution_tf.get("volume_step", 0.01))
    volume_min = float(req.features.execution_tf.get("volume_min", 0.01))
    max_exposure = float(req.risk_state.max_symbol_exposure_lots or 0.0)

    # Refuse to build a burst we cannot index safely. The endpoint vetoes this
    # first, but the planner must not depend on a caller-side invariant.
    burst_index = max(0, req.active_basket_bursts)
    if burst_index >= exit_rules.max_bursts_per_basket:
        return _empty_burst("BURST_INDEX_OUT_OF_RANGE")

    # Per-layer size, hard-capped so the worst case (every burst filled) stays
    # within max_symbol_exposure_lots.
    micro_lots = volume_min * MICRO_MULTIPLIER
    if max_exposure > 0:
        per_layer_cap = max_exposure / (LAYERS_PER_BURST * exit_rules.max_bursts_per_basket)
        # The broker's minimum lot can exceed the configured cap. Rounding back
        # up to volume_min would quietly trade above the risk limit, so refuse
        # the burst instead and let the operator widen the cap deliberately.
        if volume_min > per_layer_cap:
            return _empty_burst("EXPOSURE_CAP_BELOW_BROKER_MINIMUM")
        if micro_lots > per_layer_cap:
            micro_lots = floor_to_step(per_layer_cap, volume_step, minimum=volume_min)

    magic_offset = (burst_index * LAYERS_PER_BURST) % MAGIC_SLOTS

    layers: list[V5MicroLayer] = []
    for i in range(LAYERS_PER_BURST):
        layers.append(
            V5MicroLayer(
                layer_id=i + 1 + burst_index * LAYERS_PER_BURST,
                order_type=otype,  # type: ignore[arg-type]
                lots=micro_lots,
                magic=BASE_MAGIC + (magic_offset + i) % MAGIC_SLOTS,
                sl=0.0,
                tp=0.0,
            )
        )

    rationale = (
        f"SCALP_MICRO {side.upper()} V5 burst#{burst_index+1}/{exit_rules.max_bursts_per_basket} · "
        f"{LAYERS_PER_BURST} markets × {micro_lots:.2f} lots · "
        f"basket TP ${exit_rules.basket_tp_usd:.0f}/{exit_rules.basket_tp_pct_equity:.2f}% · "
        f"SL ${exit_rules.basket_sl_usd:.0f}"
    )[:240]

    return V5Burst(
        scenario="SCALP_MICRO",
        side_bias=side,  # type: ignore[arg-type]
        confidence=round(scenario.confidence, 4),
        valid_until_utc=_now_utc_plus(60),
        layers=layers,
        reason_codes=scenario.reason_codes,
        rationale_short=rationale,
        exit_rules=exit_rules,
    )
