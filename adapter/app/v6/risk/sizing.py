"""
Position sizing for V6: the only code that turns a risk budget into lots.

Money and lot arithmetic runs in `decimal.Decimal`, built from each float's
shortest repr, so sizes such as 0.29, 0.57 and 1.15 lots survive flooring to a
0.01 step (in binary floats 0.29 / 0.01 is 28.999999999999996). Rounding always
leans against the trade: losses and exposure round up, allowances and lot counts
round down.

Two budgets, both floored to cents: the full budget B is the `risk_pct` share of
the sizing equity, capped at half of the remaining daily loss allowance; the
scaled budget is the same with the equity share times `size_multiplier` (so it
never exceeds B). Lots come from the scaled budget. A size that does not fit is
refused with a reason code and is never rounded up to `volume_min` (kn/14 found
exactly that bug in V1-V5), with one bounded exception, the minimum-lot floor:
when only the multiplier pushed the scaled budget below the minimum lot, while B
affords that lot and every cap allows it, the result is exactly the minimum lot,
checked against B and labelled MIN_LOT_FLOOR. An agent that asks for less risk
gets the least risk available instead of a refusal. The floor never applies to a
multiplier below `limits.MIN_SIZE_MULTIPLIER`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, DecimalException, localcontext
from typing import Callable, Final

from ..types import Refusal, SizingRequest, SizingResult, SymbolSpec
from .limits import MAX_MARGIN_USE, MAX_NOTIONAL_RATIO, MAX_RISK_PCT_CEILING, MIN_SIZE_MULTIPLIER

BAD_INPUT: Final[str] = "BAD_INPUT"
ZERO_MULTIPLIER: Final[str] = "ZERO_MULTIPLIER"
NO_RISK_BUDGET: Final[str] = "NO_RISK_BUDGET"
MARGIN_UNKNOWN: Final[str] = "MARGIN_UNKNOWN"
MIN_LOT_WALL: Final[str] = "MIN_LOT_WALL"
CAPACITY: Final[str] = "CAPACITY"
ROUNDING_OVER_BUDGET: Final[str] = "ROUNDING_OVER_BUDGET"
# SizingResult label: the scaled budget missed the minimum lot, the full budget pays it.
MIN_LOT_FLOOR: Final[str] = "MIN_LOT_FLOOR"

# Wide enough that products of two float-derived values stay exact; anything
# that still does not fit is refused rather than approximated.
_PRECISION: Final[int] = 50
_CENT: Final[Decimal] = Decimal("0.01")
_PERCENT: Final[Decimal] = Decimal(100)
_UNSCALED: Final[Decimal] = Decimal(1)
_FLOOR_MIN_MULTIPLIER: Final[Decimal] = Decimal(repr(MIN_SIZE_MULTIPLIER))
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


def _min_lot(spec: SymbolSpec) -> Decimal:
    """The smallest size the sizer can return: the first step multiple at or above volume_min."""
    return _ceil_to_step(_dec(spec.volume_min), _dec(spec.volume_step))


# --- sizing ---------------------------------------------------------------------

def _risk_budget(req: SizingRequest, basis: Decimal, multiplier: Decimal) -> Decimal:
    with localcontext(prec=_PRECISION, rounding=ROUND_FLOOR):
        by_equity = basis * _dec(req.risk_pct) / _PERCENT * multiplier
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


@dataclass(frozen=True)
class _Plan:
    """What every sizing branch reads: the request, both budgets, the loss and the caps."""

    req: SizingRequest
    full_budget: Decimal
    budget: Decimal
    loss_per_lot: Decimal
    caps: _Caps

    @property
    def volume_min(self) -> Decimal:
        return _dec(self.req.spec.volume_min)

    def affordable(self, budget: Decimal) -> Decimal:
        """Lots `budget` pays for at the stop, floored to the volume step."""
        with localcontext(prec=_PRECISION, rounding=ROUND_FLOOR):
            return _floor_to_step(budget / self.loss_per_lot, _dec(self.req.spec.volume_step))

    def caps_below(self, lots: Decimal) -> tuple[tuple[str, Decimal], ...]:
        return tuple((name, value) for name, value in self.caps if value < lots)


def _wall(plan: _Plan, budget: Decimal, note: str = "") -> Refusal:
    return Refusal(
        (MIN_LOT_WALL,),
        f"budget {budget:.2f} USD at {plan.loss_per_lot:.2f} USD/lot affords "
        f"{_fmt(plan.affordable(budget))} lots, below volume_min {_fmt(plan.volume_min)}{note}",
    )


def _capacity(plan: _Plan) -> Refusal:
    volume_min = plan.volume_min
    binding = ", ".join(f"{name}={_fmt(value)}" for name, value in plan.caps_below(volume_min))
    return Refusal((CAPACITY,), f"caps below volume_min {_fmt(volume_min)}: {binding}")


def _result(
    plan: _Plan, lots: Decimal, risk: Decimal, budget: Decimal, labels: tuple[str, ...]
) -> SizingResult:
    req = plan.req
    with localcontext(prec=_PRECISION, rounding=ROUND_CEILING):
        notional = lots * _dec(req.price) * _dec(req.spec.contract_size)
        margin = lots * _dec(req.margin_per_lot)
    return SizingResult(
        lots=float(lots),
        risk_usd=_usd_up(risk),
        risk_budget_usd=float(budget),
        loss_per_lot=_usd_up(plan.loss_per_lot),
        notional_usd=_usd_up(notional),
        margin_usd=_usd_up(margin),
        labels=labels,
    )


def _checked(
    plan: _Plan, lots: Decimal, budget: Decimal, labels: tuple[str, ...] = ()
) -> SizingResult | Refusal:
    """`lots` as a result, unless its loss at the stop exceeds `budget`."""
    with localcontext(prec=_PRECISION, rounding=ROUND_CEILING):
        risk = lots * plan.loss_per_lot
    if risk > budget + _ROUNDING_EPSILON_USD:
        return Refusal(
            (ROUNDING_OVER_BUDGET,),
            f"{_fmt(lots)} lots risk {risk:.2f} USD against a {budget:.2f} USD budget",
        )
    return _result(plan, lots, risk, budget, labels)


def _below_minimum(plan: _Plan) -> SizingResult | Refusal:
    """The scaled budget and the caps leave less than volume_min: floor, wall or capacity."""
    volume_min = plan.volume_min
    if plan.affordable(plan.budget) >= volume_min:
        return _capacity(plan)
    if plan.affordable(plan.full_budget) < volume_min:
        return _wall(plan, plan.full_budget)
    if _dec(plan.req.size_multiplier) < _FLOOR_MIN_MULTIPLIER:
        return _wall(plan, plan.budget,
                     f"; no minimum-lot floor below size_multiplier {MIN_SIZE_MULTIPLIER}")
    if plan.caps_below(volume_min):
        return _capacity(plan)
    # Every cap is a step multiple >= volume_min, so each allows the minimum lot, and the
    # full budget affords a step multiple >= volume_min, so the minimum lot fits it too.
    return _checked(plan, _min_lot(plan.req.spec), plan.full_budget, (MIN_LOT_FLOOR,))


def _size(
    req: SizingRequest, basis_cap: Decimal, max_lots: Decimal, notional_ratio_max: Decimal
) -> SizingResult | Refusal:
    basis = min(_dec(req.equity), _dec(req.balance), basis_cap)
    full_budget = _risk_budget(req, basis, _UNSCALED)
    if full_budget <= 0:
        return Refusal((NO_RISK_BUDGET,), f"risk budget rounds to {full_budget:.2f} USD")
    if req.margin_per_lot == 0:
        return Refusal((MARGIN_UNKNOWN,), "margin_per_lot is 0; margin must be known to size")
    plan = _Plan(
        req=req, full_budget=full_budget,
        budget=_risk_budget(req, basis, _dec(req.size_multiplier)),
        loss_per_lot=_loss_per_lot(req.stop_distance, req.friction_price, req.spec),
        caps=_caps(req, basis, max_lots, notional_ratio_max),
    )
    lots = min(plan.affordable(plan.budget), *(value for _, value in plan.caps))
    if lots < plan.volume_min:
        return _below_minimum(plan)
    return _checked(plan, lots, plan.budget)


def size_position(
    req: SizingRequest, *, equity_basis_usd: float, max_lots: float, notional_ratio_max: float
) -> SizingResult | Refusal:
    """Size one position from the risk budget, or say exactly why it cannot be sized.

    The sizing equity is min(equity, balance, equity_basis_usd). The full budget B is
    the smaller of its `risk_pct` share and half of the remaining daily loss allowance;
    the scaled budget is the same with the share scaled by `size_multiplier`. Lots are
    the scaled budget's lots floored to `volume_step`, then reduced by `volume_max`,
    `max_lots`, the notional cap and the margin cap. When the multiplier alone leaves
    less than the minimum lot while B pays for it and every cap allows it, the result
    is the minimum lot, checked against B and labelled MIN_LOT_FLOOR (never below a
    multiplier of `limits.MIN_SIZE_MULTIPLIER`). Refusals: BAD_INPUT, ZERO_MULTIPLIER,
    NO_RISK_BUDGET (B is zero), MARGIN_UNKNOWN, MIN_LOT_WALL (the budget cannot pay the
    minimum lot), CAPACITY (a cap is below it), ROUNDING_OVER_BUDGET.
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
    min_lot = _min_lot(spec)
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
