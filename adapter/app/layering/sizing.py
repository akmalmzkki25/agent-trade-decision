from __future__ import annotations

import math


def floor_to_step(value: float, step: float, minimum: float = 0.01) -> float:
    if step <= 0:
        return max(minimum, round(value, 2))
    n = math.floor(value / step) * step
    return max(minimum, round(n, 6))


def compute_lots(
    *,
    risk_amount: float,
    sl_distance_price: float,
    tick_size: float,
    tick_value: float,
    volume_step: float = 0.01,
    volume_min: float = 0.01,
    volume_max: float = 100.0,
) -> float:
    """
    risk_amount: USD willing to lose if SL hit.
    sl_distance_price: absolute price distance from entry to SL.
    Returns lots rounded down to volume_step.
    """
    if sl_distance_price <= 0 or tick_size <= 0 or tick_value <= 0:
        return volume_min
    sl_ticks = sl_distance_price / tick_size
    if sl_ticks <= 0:
        return volume_min
    raw = risk_amount / (sl_ticks * tick_value)
    lots = floor_to_step(raw, volume_step, minimum=volume_min)
    return min(lots, volume_max)
