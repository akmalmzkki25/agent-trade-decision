"""
The minimum-lot floor of `app.v6.risk.sizing` (user decision 2026-09-17) as a truth table
on the live configuration: a $5,000 basis at 0.5 % (a $25.00 budget), V6_MAX_LOTS 0.01,
XAUUSD at $4,345 with $0.40 friction, so 0.01 lot loses $1 per $1 of stop plus $0.40.

Rows read: multiplier m, stop, request changes, caps -> (lots, risk, budget, labels) or
the refusal code. The scaled budget is $25 x m; the full budget B is $25 (or half the
remaining daily loss allowance when that is smaller).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from app.v6.cycle_codes import MIN_COMBINED_MULTIPLIER, REDUCED_TIER_FACTOR
from app.v6.risk import limits
from app.v6.risk.sizing import (
    CAPACITY, MIN_LOT_FLOOR, MIN_LOT_WALL, NO_RISK_BUDGET, ZERO_MULTIPLIER, size_position,
)
from app.v6.types import Refusal, SizingRequest, SizingResult, SymbolSpec

XAU = SymbolSpec(digits=2, point=0.01, tick_size=0.01, tick_value=1.0, tick_value_loss=1.0,
                 contract_size=100.0, volume_min=0.01, volume_step=0.01, volume_max=100.0)
LIVE_CAPS = {"equity_basis_usd": 5000.0, "max_lots": 0.01, "notional_ratio_max": 10.0}
LOOSE = {"max_lots": 1.0}
FLOOR = (MIN_LOT_FLOOR,)
NO_FLOOR = ()
DEMO_EQUITY = 96_896.0


def request(stop: float, multiplier: float, **changes: Any) -> SizingRequest:
    fields: dict[str, Any] = {
        "equity": DEMO_EQUITY, "balance": DEMO_EQUITY, "free_margin": DEMO_EQUITY,
        "price": 4345.0, "stop_distance": stop, "risk_pct": 0.5,
        "size_multiplier": multiplier, "remaining_daily_loss_usd": 150.0,
        "margin_per_lot": 2172.5, "friction_price": 0.40, "spec": XAU,
    }
    return SizingRequest(**(fields | changes))


def outcome(result: SizingResult | Refusal) -> tuple[Any, ...]:
    if isinstance(result, Refusal):
        return result.codes
    return result.lots, result.risk_usd, result.risk_budget_usd, result.labels


TRUTH_TABLE = [
    # m = 1 never needs the floor.
    pytest.param(1.0, 7.0, {}, {}, (0.01, 7.40, 25.00, NO_FLOOR), id="standard"),
    pytest.param(1.0, 24.60, {}, {}, (0.01, 25.00, 25.00, NO_FLOOR), id="standard-at-B"),
    pytest.param(1.0, 24.61, {}, {}, (MIN_LOT_WALL,), id="standard-wall"),
    # The scaled budget still pays for the minimum lot: the ordinary path.
    pytest.param(0.5, 7.0, {}, {}, (0.01, 7.40, 12.50, NO_FLOOR), id="scaled-pays"),
    pytest.param(0.5, 12.10, {}, {}, (0.01, 12.50, 12.50, NO_FLOOR), id="scaled-exactly"),
    pytest.param(0.24, 5.60, {}, {}, (0.01, 6.00, 6.00, NO_FLOOR), id="tiny-m-scaled-pays"),
    # Only the multiplier stops the minimum lot, B pays for it: the floor.
    pytest.param(0.5, 12.11, {}, {}, (0.01, 12.51, 25.00, FLOOR), id="floor-just"),
    pytest.param(0.5, 24.60, {}, {}, (0.01, 25.00, 25.00, FLOOR), id="floor-at-B"),
    pytest.param(0.25, 24.60, {}, {}, (0.01, 25.00, 25.00, FLOOR), id="floor-min-m"),
    pytest.param(0.375, 20.0, {}, {}, (0.01, 20.40, 25.00, FLOOR), id="floor-reduced-x0.75"),
    pytest.param(0.25, 7.0, {}, LOOSE, (0.01, 7.40, 25.00, FLOOR), id="floor-is-one-lot-only"),
    pytest.param(0.5, 7.0, {}, LOOSE, (0.01, 7.40, 12.50, NO_FLOOR), id="loose-scaled-pays"),
    # The floor never exceeds B.
    pytest.param(0.5, 24.61, {}, {}, (MIN_LOT_WALL,), id="B-cannot-pay"),
    pytest.param(0.5, 24.0, {"remaining_daily_loss_usd": 30.0}, {}, (MIN_LOT_WALL,),
                 id="daily-cap-B-cannot-pay"),
    pytest.param(0.5, 12.0, {"remaining_daily_loss_usd": 30.0}, {},
                 (0.01, 12.40, 12.50, NO_FLOOR), id="daily-cap-scaled-pays"),
    pytest.param(0.25, 12.0, {"remaining_daily_loss_usd": 30.0}, {},
                 (0.01, 12.40, 15.00, FLOOR), id="daily-cap-floor-against-B"),
    # No floor below the protocol's minimum multiplier.
    pytest.param(0.24, 24.60, {}, {}, (MIN_LOT_WALL,), id="below-min-m"),
    # Caps and the other refusals win exactly as before.
    pytest.param(0.5, 24.0, {"free_margin": 50.0}, {}, (CAPACITY,), id="cap-blocks-floor"),
    pytest.param(1.0, 7.0, {"free_margin": 50.0}, {}, (CAPACITY,), id="cap-blocks-standard"),
    pytest.param(0.5, 7.0, {"free_margin": 50.0}, {}, (CAPACITY,), id="cap-blocks-scaled"),
    pytest.param(0.0, 7.0, {}, {}, (ZERO_MULTIPLIER,), id="zero-m"),
    pytest.param(0.5, 7.0, {"remaining_daily_loss_usd": 0.0}, {}, (NO_RISK_BUDGET,),
                 id="no-budget"),
    # A minimum volume off the step grid: the floor is the first step multiple above it.
    pytest.param(0.25, 12.0, {"spec": replace(XAU, volume_min=0.015)}, LOOSE,
                 (0.02, 24.80, 25.00, FLOOR), id="off-grid-min-floor"),
    pytest.param(0.25, 12.11, {"spec": replace(XAU, volume_min=0.015)}, LOOSE,
                 (MIN_LOT_WALL,), id="off-grid-min-wall"),
]


@pytest.mark.parametrize(("multiplier", "stop", "changes", "caps", "expected"), TRUTH_TABLE)
def test_minimum_lot_floor_truth_table(multiplier: float, stop: float, changes: dict[str, Any],
                                       caps: dict[str, float], expected: tuple[Any, ...]) -> None:
    result = size_position(request(stop, multiplier, **changes), **(LIVE_CAPS | caps))
    assert outcome(result) == expected


def test_the_floor_bound_is_the_protocol_bound() -> None:
    assert limits.MIN_SIZE_MULTIPLIER == MIN_COMBINED_MULTIPLIER == 0.25
    # The reduced tier alone (x0.5) and with any desk multiplier down to 0.5 stays sizable.
    assert REDUCED_TIER_FACTOR * 0.5 >= MIN_COMBINED_MULTIPLIER


def test_the_wall_below_the_minimum_multiplier_says_why() -> None:
    result = size_position(request(24.60, 0.24), **LIVE_CAPS)
    assert isinstance(result, Refusal)
    assert "affords 0 lots" in result.detail and "no minimum-lot floor below" in result.detail


def test_a_floor_never_hides_a_capacity_problem() -> None:
    result = size_position(request(24.0, 0.5, free_margin=50.0), **LIVE_CAPS)
    assert isinstance(result, Refusal) and "margin=0" in result.detail


@pytest.mark.parametrize("stop", [6.0, 9.6, 15.0, 19.6, 24.6])
@pytest.mark.parametrize("multiplier", [0.25, 0.375, 0.5, 0.64, 0.75, 0.8, 1.0])
def test_every_stop_the_standard_tier_sizes_still_sizes_at_lower_m(
        stop: float, multiplier: float) -> None:
    standard = size_position(request(stop, 1.0), **LIVE_CAPS)
    reduced = size_position(request(stop, multiplier), **LIVE_CAPS)
    assert isinstance(standard, SizingResult) and isinstance(reduced, SizingResult)
    assert (reduced.lots, reduced.risk_usd) == (standard.lots, standard.risk_usd)
    assert reduced.risk_usd <= standard.risk_budget_usd == 25.0
