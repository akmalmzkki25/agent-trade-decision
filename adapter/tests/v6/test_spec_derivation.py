"""SymbolSpecBlock.to_spec: tick values come from OrderCalcProfit when priced.

Probe 2026-09-16 (MetaQuotes-Demo): SYMBOL_TRADE_TICK_VALUE = 0.1 but a 1.00-lot
move of 1.00 pays $100, so the true tick value for tick_size 0.01 is 1.0.
"""

from __future__ import annotations

import json
import math
from typing import Any

import pytest
from pydantic import ValidationError

from app.v6.risk.sizing import size_position
from app.v6.schemas.snapshot import SymbolSpecBlock
from app.v6.types import SizingRequest, SizingResult, SymbolSpec

from .payloads_v6 import snapshot_payload


def _block(**changes: Any) -> SymbolSpecBlock:
    data = {**snapshot_payload()["symbol_spec"], **changes}
    return SymbolSpecBlock.model_validate_json(json.dumps(data))


def test_verified_spec_derives_true_tick_values() -> None:
    spec = _block().to_spec()

    assert spec.tick_value == pytest.approx(1.0)
    assert spec.tick_value_loss == pytest.approx(1.0)
    assert math.isclose(spec.tick_value, spec.tick_size * spec.contract_size)
    assert spec.tick_value_source == "order_calc"
    assert (spec.reported_tick_value, spec.reported_tick_value_loss) == (0.1, 0.1)


def test_asymmetric_calc_values_are_kept_per_side() -> None:
    spec = _block(calc_profit_per_price=100.0, calc_loss_per_price=100.5).to_spec()

    assert spec.tick_value == pytest.approx(1.0)
    assert spec.tick_value_loss == pytest.approx(1.005)


def test_zero_calc_values_fall_back_to_reported() -> None:
    spec = _block(calc_profit_per_price=0.0, calc_loss_per_price=0.0).to_spec()

    assert (spec.tick_value, spec.tick_value_loss) == (0.1, 0.1)
    assert spec.tick_value_source == "reported"


@pytest.mark.parametrize(
    ("profit", "loss", "tick_value", "tick_value_loss"),
    [(100.0, 0.0, 1.0, 0.1), (0.0, 100.0, 0.1, 1.0)],
)
def test_one_missing_calc_value_is_marked_mixed(
    profit: float, loss: float, tick_value: float, tick_value_loss: float
) -> None:
    spec = _block(calc_profit_per_price=profit, calc_loss_per_price=loss).to_spec()

    assert spec.tick_value == pytest.approx(tick_value)
    assert spec.tick_value_loss == pytest.approx(tick_value_loss)
    assert spec.tick_value_source == "mixed"


@pytest.mark.parametrize(
    "changes",
    [
        {"calc_profit_per_price": -1.0},
        {"calc_loss_per_price": -0.01},
        {"calc_profit_per_price": "100"},
    ],
)
def test_invalid_calc_values_are_refused(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _block(**changes)


def test_calc_fields_are_required() -> None:
    data = dict(snapshot_payload()["symbol_spec"])
    del data["calc_loss_per_price"]

    with pytest.raises(ValidationError):
        SymbolSpecBlock.model_validate_json(json.dumps(data))


def test_nan_calc_value_is_refused() -> None:
    raw = json.dumps(snapshot_payload()["symbol_spec"]).replace(
        '"calc_profit_per_price": 100.0', '"calc_profit_per_price": NaN')

    with pytest.raises(ValidationError):
        SymbolSpecBlock.model_validate_json(raw)


def test_directly_built_spec_keeps_backward_compatible_defaults() -> None:
    spec = SymbolSpec(digits=2, point=0.01, tick_size=0.01, tick_value=1.0,
                      tick_value_loss=1.0, contract_size=100.0, volume_min=0.01,
                      volume_step=0.01, volume_max=100.0)

    assert spec.reported_tick_value is None
    assert spec.reported_tick_value_loss is None
    assert spec.tick_value_source == "reported"


def test_derived_spec_sizes_the_plan_example() -> None:
    """Plan section 6: a $7 stop costs ~$740/lot, so 0.5% of $2000 buys 0.01 lot.
    With the reported 0.1 tick value the sizer would have allowed 10x that."""
    request = SizingRequest(
        equity=2000.0, balance=2000.0, free_margin=2000.0, price=4300.0, stop_distance=7.0,
        risk_pct=0.5, size_multiplier=1.0, remaining_daily_loss_usd=60.0,
        margin_per_lot=2150.0, friction_price=0.40, spec=_block().to_spec())

    result = size_position(request, equity_basis_usd=2000.0, max_lots=1.0,
                           notional_ratio_max=10.0)

    assert isinstance(result, SizingResult)
    assert result.lots == 0.01
    assert result.loss_per_lot == pytest.approx(740.0)
