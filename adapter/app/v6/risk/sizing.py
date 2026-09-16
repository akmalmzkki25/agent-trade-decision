"""
Position sizing for V6: the only code that turns a risk budget into lots.

Money and lot arithmetic runs in `decimal.Decimal`, built from each float's
shortest repr, so sizes such as 0.29, 0.57 and 1.15 lots survive flooring to a
0.01 step (in binary floats 0.29 / 0.01 is 28.999999999999996). Rounding always
leans against the trade: losses and exposure round up, allowances and lot counts
round down. A size that does not fit is refused with a reason code and is never
rounded up to `volume_min` (kn/14 found exactly that bug in V1-V5).
"""

from __future__ import annotations

import math
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, DecimalException, localcontext
from typing import Callable, Final

from ..types import Refusal, SizingRequest, SizingResult, SymbolSpec
from .limits import MAX_MARGIN_USE, MAX_NOTIONAL_RATIO, MAX_RISK_PCT_CEILING

BAD_INPUT: Final[str] = "BAD_INPUT"
ZERO_MULTIPLIER: Final[str] = "ZERO_MULTIPLIER"
NO_RISK_BUDGET: Final[str] = "NO_RISK_BUDGET"
MARGIN_UNKNOWN: Final[str] = "MARGIN_UNKNOWN"
MIN_LOT_WALL: Final[str] = "MIN_LOT_WALL"
CAPACITY: Final[str] = "CAPACITY"
ROUNDING_OVER_BUDGET: Final[str] = "ROUNDING_OVER_BUDGET"

# Wide enough that products of two float-derived values stay exact; anything
# that still does not fit is refused rather than approximated.
_PRECISION: Final[int] = 50
_CENT: Final[Decimal] = Decimal("0.01")
_PERCENT: Final[Decimal] = Decimal(100)
# One trade may spend at most half of what is left of the daily loss allowance.
_DAILY_LOSS_SHARE: Final[Decimal] = Decimal("0.5")
_MARGIN_USE: Final[Decimal] = Decimal(repr(MAX_MARGIN_USE))
_ROUNDING_EPSILON_USD: Final[Decimal] = Decimal("1e-9")
_SPEC_POSITIVE_FIELDS: Final[tuple[str, ...]] = (
    "tick_size", "tick_value_loss", "contract_size", "volume_min", "volume_step", "volume_max",
)

_Check = tuple[str, object, Callable[[float], bool]]
_Caps = tuple[tuple[str, Decimal], ...]


# --- input validation ---------------------------------------------------------

def _positive(value: float) -> bool:
    return value > 0


def _non_negative(value: float) -> bool:
    return value >= 0


def _valid_risk_pct(value: float) -> bool:
    return 0 < value <= MAX_RISK_PCT_CEILING


def _unit_interval(value: float) -> bool:
    return 0 <= value <= 1


def _valid_notional_ratio(value: float) -> bool:
    return 0 < value <= MAX_NOTIONAL_RATIO


def _is_finite_number(value: object) -> bool:
    # bool is an int subclass, but a flag is never a price or a balance.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        # An int beyond float range cannot be a real price or balance.
        return False


def _invalid_fields(checks: tuple[_Check, ...]) -> tuple[str, ...]:
    return tuple(
        f"{name}={value!r}"
        for name, value, is_valid in checks
        if not (_is_finite_number(value) and is_valid(value))
    )


def _spec_checks(spec: SymbolSpec) -> tuple[_Check, ...]:
    return tuple(
        (f"spec.{name}", getattr(spec, name), _positive) for name in _SPEC_POSITIVE_FIELDS
    )


def _request_checks(
    req: SizingRequest, equity_basis_usd: float, max_lots: float, notional_ratio_max: float
) -> tuple[_Check, ...]:
    return (
        ("equity", req.equity, _positive),
        ("balance", req.balance, _positive),
        ("price", req.price, _positive),
        ("stop_distance", req.stop_distance, _positive),
        ("free_margin", req.free_margin, _non_negative),
        ("margin_per_lot", req.margin_per_lot, _non_negative),
        ("remaining_daily_loss_usd", req.remaining_daily_loss_usd, _non_negative),
        ("friction_price", req.friction_price, _non_negative),
        ("risk_pct", req.risk_pct, _valid_risk_pct),
        ("size_multiplier", req.size_multiplier, _unit_interval),
        ("equity_basis_usd", equity_basis_usd, _positive),
        ("max_lots", max_lots, _positive),
        ("notional_ratio_max", notional_ratio_max, _valid_notional_ratio),
    ) + _spec_checks(req.spec)


# --- Decimal helpers ------------------------------------------------------------

def _dec(value: float) -> Decimal:
    # The shortest repr is the decimal the float was written as (0.29, not 0.28999...).
    return Decimal(repr(float(value)))


def _to_step(value: Decimal, step: Decimal, rounding: str) -> Decimal:
    with localcontext(prec=_PRECISION, rounding=rounding):
        return (value / step).to_integral_value(rounding=rounding) * step


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    return _to_step(value, step, ROUND_FLOOR)


def _ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    return _to_step(value, step, ROUND_CEILING)


def _cents(value: Decimal, rounding: str) -> Decimal:
    with localcontext(prec=_PRECISION):
        return value.quantize(_CENT, rounding=rounding)


def _usd_up(value: Decimal) -> float:
    return float(_cents(value, ROUND_CEILING))


def _fmt(value: Decimal) -> str:
    # Equal values print identically whatever exponent the arithmetic left behind.
    with localcontext(prec=_PRECISION):
        return f"{value.normalize():f}"


def _loss_per_lot(stop_distance: float, friction_price: float, spec: SymbolSpec) -> Decimal:
    """USD lost per lot at the stop; friction is charged per price unit like the stop."""
    tick_size, tick_loss = _dec(spec.tick_size), _dec(spec.tick_value_loss)
    with localcontext(prec=_PRECISION, rounding=ROUND_CEILING):
        stop_loss = _dec(stop_distance) / tick_size * tick_loss
        friction_loss = _dec(friction_price) / tick_size * tick_loss
        return stop_loss + friction_loss


# --- sizing ---------------------------------------------------------------------

def _risk_budget(req: SizingRequest, basis: Decimal) -> Decimal:
    with localcontext(prec=_PRECISION, rounding=ROUND_FLOOR):
        by_equity = basis * _dec(req.risk_pct) / _PERCENT * _dec(req.size_multiplier)
        by_daily_loss = _DAILY_LOSS_SHARE * _dec(req.remaining_daily_loss_usd)
    return _cents(min(by_equity, by_daily_loss), ROUND_FLOOR)


def _caps(
    req: SizingRequest, basis: Decimal, max_lots: Decimal, notional_ratio_max: Decimal
) -> _Caps:
    """Every ceiling in lots, floored to the step. Each one can only reduce the size."""
    spec = req.spec
    step = _dec(spec.volume_step)
    with localcontext(prec=_PRECISION, rounding=ROUND_FLOOR):
        lot_notional = _dec(req.price) * _dec(spec.contract_size)
        notional = notional_ratio_max * basis / lot_notional
        margin = _MARGIN_USE * _dec(req.free_margin) / _dec(req.margin_per_lot)
    return (
        ("volume_max", _floor_to_step(_dec(spec.volume_max), step)),
        ("max_lots", _floor_to_step(max_lots, step)),
        ("notional", _floor_to_step(notional, step)),
        ("margin", _floor_to_step(margin, step)),
    )


def _below_minimum(
    budget_lots: Decimal, caps: _Caps, volume_min: Decimal, budget: Decimal,
    loss_per_lot: Decimal,
) -> Refusal:
    if budget_lots < volume_min:
        return Refusal(
            (MIN_LOT_WALL,),
            f"budget {budget:.2f} USD at {loss_per_lot:.2f} USD/lot affords "
            f"{_fmt(budget_lots)} lots, below volume_min {_fmt(volume_min)}",
        )
    binding = ", ".join(f"{name}={_fmt(value)}" for name, value in caps if value < volume_min)
    return Refusal((CAPACITY,), f"caps below volume_min {_fmt(volume_min)}: {binding}")


def _result(
    req: SizingRequest, lots: Decimal, risk: Decimal, budget: Decimal, loss_per_lot: Decimal
) -> SizingResult:
    with localcontext(prec=_PRECISION, rounding=ROUND_CEILING):
        notional = lots * _dec(req.price) * _dec(req.spec.contract_size)
        margin = lots * _dec(req.margin_per_lot)
    return SizingResult(
        lots=float(lots),
        risk_usd=_usd_up(risk),
        risk_budget_usd=float(budget),
        loss_per_lot=_usd_up(loss_per_lot),
        notional_usd=_usd_up(notional),
        margin_usd=_usd_up(margin),
    )


def _size(
    req: SizingRequest, basis_cap: Decimal, max_lots: Decimal, notional_ratio_max: Decimal
) -> SizingResult | Refusal:
    spec = req.spec
    basis = min(_dec(req.equity), _dec(req.balance), basis_cap)
    budget = _risk_budget(req, basis)
    if budget <= 0:
        return Refusal((NO_RISK_BUDGET,), f"risk budget rounds to {budget:.2f} USD")
    loss_per_lot = _loss_per_lot(req.stop_distance, req.friction_price, spec)
    with localcontext(prec=_PRECISION, rounding=ROUND_FLOOR):
        budget_lots = _floor_to_step(budget / loss_per_lot, _dec(spec.volume_step))
    if req.margin_per_lot == 0:
        return Refusal((MARGIN_UNKNOWN,), "margin_per_lot is 0; margin must be known to size")
    caps = _caps(req, basis, max_lots, notional_ratio_max)
    lots = min(budget_lots, *(value for _, value in caps))
    volume_min = _dec(spec.volume_min)
    if lots < volume_min:
        return _below_minimum(budget_lots, caps, volume_min, budget, loss_per_lot)
    with localcontext(prec=_PRECISION, rounding=ROUND_CEILING):
        risk = lots * loss_per_lot
    if risk > budget + _ROUNDING_EPSILON_USD:
        return Refusal(
            (ROUNDING_OVER_BUDGET,),
            f"{_fmt(lots)} lots risk {risk:.2f} USD against a {budget:.2f} USD budget",
        )
    return _result(req, lots, risk, budget, loss_per_lot)


def size_position(
    req: SizingRequest, *, equity_basis_usd: float, max_lots: float, notional_ratio_max: float
) -> SizingResult | Refusal:
    """Size one position from the risk budget, or say exactly why it cannot be sized.

    The sizing equity is min(equity, balance, equity_basis_usd); the budget is the
    smaller of its `risk_pct` share (scaled by `size_multiplier`) and half of the
    remaining daily loss allowance. Lots are floored to `volume_step` and then
    reduced by `volume_max`, `max_lots`, the notional cap and the margin cap.
    """
    invalid = _invalid_fields(
        _request_checks(req, equity_basis_usd, max_lots, notional_ratio_max)
    )
    if invalid:
        return Refusal((BAD_INPUT,), "invalid inputs: " + ", ".join(invalid))
    if req.size_multiplier == 0:
        return Refusal((ZERO_MULTIPLIER,), "size_multiplier is 0; nothing to size")
    try:
        return _size(req, _dec(equity_basis_usd), _dec(max_lots), _dec(notional_ratio_max))
    except DecimalException as exc:
        return Refusal(
            (BAD_INPUT,), f"inputs outside the supported numeric range ({type(exc).__name__})"
        )


def _min_equity(
    stop_distance: float, friction_price: float, risk_pct: float, spec: SymbolSpec
) -> float:
    # The sizer floors lots to the step, so the smallest size it can return is
    # the first step multiple at or above volume_min.
    min_lot = _ceil_to_step(_dec(spec.volume_min), _dec(spec.volume_step))
    loss_per_lot = _loss_per_lot(stop_distance, friction_price, spec)
    with localcontext(prec=_PRECISION, rounding=ROUND_CEILING):
        # The sizer floors its budget to cents, so the minimum lot needs a
        # whole-cent budget before it fits.
        min_budget = _cents(min_lot * loss_per_lot, ROUND_CEILING)
        equity = min_budget * _PERCENT / _dec(risk_pct)
    return float(_cents(equity, ROUND_CEILING))


def min_tradeable_equity(
    stop_distance: float, friction_price: float, risk_pct: float, spec: SymbolSpec
) -> float:
    """Smallest sizing equity, in whole cents, at which the minimum lot fits the budget.

    Mirrors `size_position` at size_multiplier 1 and ignores the daily-loss and
    capacity limits, so it is a floor rather than a promise that a trade sizes.

    Raises:
        ValueError: an input is non-finite, out of range, or too large to size exactly.
    """
    invalid = _invalid_fields(
        (
            ("stop_distance", stop_distance, _positive),
            ("friction_price", friction_price, _non_negative),
            ("risk_pct", risk_pct, _valid_risk_pct),
        )
        + _spec_checks(spec)
    )
    if invalid:
        raise ValueError("invalid inputs: " + ", ".join(invalid))
    try:
        return _min_equity(stop_distance, friction_price, risk_pct, spec)
    except DecimalException as exc:
        raise ValueError(
            f"inputs outside the supported numeric range ({type(exc).__name__})"
        ) from exc
