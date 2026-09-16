"""
Tests for the V6 position sizer (`app.v6.risk.sizing`): Decimal-exact examples plus
a seeded random sweep checked against an independent Decimal oracle.
"""

from __future__ import annotations

import math
import random
import re
from collections import Counter
from dataclasses import replace
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import pytest

from app.v6.risk import sizing
from app.v6.risk.sizing import min_tradeable_equity, size_position
from app.v6.types import Refusal, SizingRequest, SizingResult, SymbolSpec

XAU_2DIGIT = SymbolSpec(
    digits=2, point=0.01, tick_size=0.01, tick_value=1.0, tick_value_loss=1.0,
    contract_size=100.0, volume_min=0.01, volume_step=0.01, volume_max=100.0,
)
XAU_3DIGIT = replace(XAU_2DIGIT, digits=3, point=0.001, tick_size=0.001,
                     tick_value=0.1, tick_value_loss=0.1)

# The user's live V6 configuration: $2,000 basis, 0.5% risk, V6_MAX_LOTS=0.01.
REAL_CONFIG = {"equity_basis_usd": 2000.0, "max_lots": 0.01, "notional_ratio_max": 10.0}
LOOSE_CAPS = {**REAL_CONFIG, "max_lots": 100.0}


def make_request(**overrides: object) -> SizingRequest:
    """XAUUSD at $4,300 on a $2,000 account, 1:100 margin, $7 stop, standard friction."""
    fields: dict[str, object] = {
        "equity": 2000.0, "balance": 2000.0, "free_margin": 2000.0, "price": 4300.0,
        "stop_distance": 7.0, "risk_pct": 0.5, "size_multiplier": 1.0, "spec": XAU_2DIGIT,
        "remaining_daily_loss_usd": 60.0, "margin_per_lot": 4300.0, "friction_price": 0.40,
    }
    return SizingRequest(**{**fields, **overrides})


def roomy_request(**overrides: object) -> SizingRequest:
    """A request whose notional, margin and daily-loss limits never bind."""
    roomy = {"price": 100.0, "margin_per_lot": 1.0, "free_margin": 1e6,
             "remaining_daily_loss_usd": 1e6}
    return make_request(**{**roomy, **overrides})


def funded(basis: float, **overrides: object) -> tuple[SizingRequest, dict[str, float]]:
    """1% of `basis` over a $100/lot loss (stop 0.60 + friction 0.40): basis / 10,000 lots."""
    fields = {"equity": basis, "balance": basis, "stop_distance": 0.6, "risk_pct": 1.0}
    return roomy_request(**{**fields, **overrides}), {**LOOSE_CAPS, "equity_basis_usd": basis}


def run(req: SizingRequest, **caps: float) -> SizingResult | Refusal:
    return size_position(req, **{**LOOSE_CAPS, **caps})


def sized(req: SizingRequest, **caps: float) -> SizingResult:
    assert isinstance(result := run(req, **caps), SizingResult), result
    return result


def refused(req: SizingRequest, **caps: float) -> Refusal:
    assert isinstance(result := run(req, **caps), Refusal), result
    return result


def names_field(detail: str, field: str) -> bool:
    # `price=` must not match `friction_price=`.
    return re.search(rf"(?<!\w){re.escape(field)}=", detail) is not None


# --- the user's real configuration --------------------------------------------
@pytest.mark.parametrize(
    "account",
    [{}, {"equity": 99_868.0, "balance": 100_000.0, "free_margin": 99_000.0}],
    ids=["equity_2000", "demo_equity_99868"],
)
def test_real_config_sizes_from_the_2000_basis(account) -> None:
    # stop $7.00 + friction $0.40 = $740/lot: 0.01 lots risking $7.40 of $10.
    assert sized(make_request(**account), **REAL_CONFIG) == SizingResult(
        lots=0.01, risk_usd=7.40, risk_budget_usd=10.00, loss_per_lot=740.00,
        notional_usd=4300.00, margin_usd=43.00,
    )
    # A $99,868 basis would afford a $12 stop; the $2,000 basis must not.
    wall = refused(make_request(stop_distance=12.0, **account), **REAL_CONFIG)
    assert wall.codes == ("MIN_LOT_WALL",)


@pytest.mark.parametrize(
    ("equity", "balance", "daily_left", "budget"),
    [
        (1500.0, 2000.0, 1e6, 7.50), (2000.0, 1000.0, 1e6, 5.00), (1234.56, 5000.0, 1e6, 6.17),
        (2000.0, 2000.0, 12.0, 6.00),   # half of the remaining daily loss binds
    ],
)
def test_budget_comes_from_the_tightest_source(equity, balance, daily_left, budget) -> None:
    result = sized(roomy_request(equity=equity, balance=balance, stop_distance=0.6,
                                 remaining_daily_loss_usd=daily_left))
    # $100/lot, so the budget floors to whole dollars of 0.01 lots.
    assert (result.risk_budget_usd, result.lots) == (budget, math.floor(budget) / 100)


# --- parametrised grid, caps never bind --------------------------------------
@pytest.mark.parametrize(
    ("stop", "friction", "risk_pct", "multiplier", "expected"),
    [
        (7.0, 0.40, 0.5, 1.0, (0.01, 7.40, 10.00)),     # 10 / 740
        (9.6, 0.40, 0.5, 1.0, (0.01, 10.00, 10.00)),    # 10 / 1000, exactly on budget
        (9.61, 0.40, 0.5, 1.0, ("MIN_LOT_WALL",)),      # 10 / 1001
        (12.0, 0.40, 0.5, 1.0, ("MIN_LOT_WALL",)),
        (4.6, 0.40, 1.0, 1.0, (0.04, 20.00, 20.00)),    # 20 / 500
        (4.6, 0.40, 1.0, 0.5, (0.02, 10.00, 10.00)),    # 10 / 500
        (2.1, 0.40, 0.5, 1.0, (0.04, 10.00, 10.00)),    # 10 / 250
        (0.6, 0.22, 0.25, 1.0, (0.06, 4.92, 5.00)),     # 5 / 82
        (1.6, 0.40, 1.0, 0.25, (0.02, 4.00, 5.00)),     # 5 / 200
        (0.6, 0.0, 0.5, 1.0, (0.16, 9.60, 10.00)),      # 10 / 60, no friction
    ],
)
def test_sizing_grid(stop, friction, risk_pct, multiplier, expected) -> None:
    result = run(roomy_request(stop_distance=stop, friction_price=friction,
                               risk_pct=risk_pct, size_multiplier=multiplier))
    got = result.codes if isinstance(result, Refusal) else (
        result.lots, result.risk_usd, result.risk_budget_usd)
    assert got == expected


# --- float flooring regression -------------------------------------------------
@pytest.mark.parametrize(("lots", "basis"), [(0.29, 2900.0), (0.57, 5700.0), (1.15, 11500.0)])
def test_step_multiples_survive_flooring(lots, basis) -> None:
    # Binary floats floor these a step low: 0.29 / 0.01 == 28.999999999999996.
    assert math.floor(lots / 0.01) * 0.01 < lots
    req, caps = funded(basis)
    assert sized(req, **caps).lots == lots
    big, big_caps = funded(1e5)
    assert sized(big, **{**big_caps, "max_lots": lots}).lots == lots
    assert sized(replace(big, spec=replace(XAU_2DIGIT, volume_max=lots)), **big_caps).lots == lots


@pytest.mark.parametrize(
    ("step", "basis", "expected"),
    [(0.1, 2900.0, 0.2), (1.0, 29000.0, 2.0), (0.05, 2900.0, 0.25),
     (0.1, 900.0, ("MIN_LOT_WALL",))],   # 0.09 lots floors to 0.0
)
def test_coarser_volume_steps(step, basis, expected) -> None:
    req, caps = funded(basis, spec=replace(XAU_2DIGIT, volume_step=step, volume_min=step))
    result = run(req, **caps)
    assert (result.codes if isinstance(result, Refusal) else result.lots) == expected


# --- caps: a $0.60 stop alone would afford 0.10 lots --------------------------
CHEAP_MARKET = {"price": 100.0, "free_margin": 1e6}


@pytest.mark.parametrize(
    ("overrides", "caps", "lots"),
    [
        # 10 x $2,000 / ($4,300 x 100 oz) = 0.0465 -> 0.04 (plan §6 example).
        ({"free_margin": 1e6}, {}, 0.04),
        ({"free_margin": 1e6}, {"notional_ratio_max": 5.0}, 0.02),
        # 0.25 x $200 / $4,300 per lot = 0.0116 -> 0.01.
        ({"free_margin": 200.0}, {}, 0.01),
        (CHEAP_MARKET, {"max_lots": 0.02}, 0.02),
        # A cap only ever reduces: 0.015 floors to 0.01.
        (CHEAP_MARKET, {"max_lots": 0.015}, 0.01),
        ({**CHEAP_MARKET, "spec": replace(XAU_2DIGIT, volume_max=0.03)}, {}, 0.03),
    ],
)
def test_caps_bind(overrides, caps, lots) -> None:
    assert sized(make_request(stop_distance=0.6, **overrides), **caps).lots == lots


@pytest.mark.parametrize(
    ("overrides", "caps", "cap_name"),
    [
        ({"free_margin": 100.0}, {}, "margin"),     # 0.25 x $100 / $4,300 = 0.0058
        ({"free_margin": 0.0}, {}, "margin"),
        ({"price": 100.0}, {"max_lots": 0.005}, "max_lots"),
        ({"stop_distance": 0.6}, {"notional_ratio_max": 1.0}, "notional"),
        ({"price": 100.0, "stop_distance": 0.6,
          "spec": replace(XAU_2DIGIT, volume_min=0.05, volume_max=0.03)}, {}, "volume_max"),
    ],
)
def test_cap_below_volume_min_is_capacity_not_rounded_up(overrides, caps, cap_name) -> None:
    refusal = refused(make_request(**overrides), **caps)
    assert refusal.codes == ("CAPACITY",)
    assert names_field(refusal.detail, cap_name), refusal.detail


# --- single-reason refusals ----------------------------------------------------
@pytest.mark.parametrize(
    ("overrides", "caps", "code"),
    [
        ({"remaining_daily_loss_usd": 0.0}, {}, "NO_RISK_BUDGET"),
        ({}, {"equity_basis_usd": 0.5}, "NO_RISK_BUDGET"),   # $0.0025 floors to zero cents
        ({"remaining_daily_loss_usd": 12.0}, {}, "MIN_LOT_WALL"),  # $6 cannot carry $7.40
        ({"size_multiplier": 0.0}, {}, "ZERO_MULTIPLIER"),
        ({"margin_per_lot": 0.0}, {}, "MARGIN_UNKNOWN"),     # unknown is not a free pass
        # When the budget alone is too small, that is the reason, whatever the caps say.
        ({"stop_distance": 12.0, "free_margin": 0.0}, {"max_lots": 0.001}, "MIN_LOT_WALL"),
    ],
)
def test_single_reason_refusals(overrides, caps, code) -> None:
    assert refused(make_request(**overrides), **caps).codes == (code,)


def test_rounding_over_budget_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sizing, "_floor_to_step", lambda value, step: (
        (value / step).to_integral_value(rounding=ROUND_CEILING) * step))
    assert refused(make_request()).codes == ("ROUNDING_OVER_BUDGET",)


# --- bad input ---------------------------------------------------------------
NON_FINITE = (math.nan, math.inf, -math.inf)
WRONG_TYPE = (None, "1.0", True, 10**400)   # the int overflows math.isfinite
BAD_INPUT_TABLE = (
    ("req", ("equity", "balance", "price", "stop_distance"), (*NON_FINITE, *WRONG_TYPE, 0, -1.0)),
    ("req", ("free_margin", "margin_per_lot", "remaining_daily_loss_usd", "friction_price"),
     (*NON_FINITE, *WRONG_TYPE, -0.01)),
    ("req", ("risk_pct",), (math.nan, 0.0, -0.5, 1.01)),
    ("req", ("size_multiplier",), (math.nan, -0.1, 1.01)),
    ("spec", ("tick_size", "tick_value_loss", "contract_size", "volume_min", "volume_step",
              "volume_max"), (*NON_FINITE, 0.0, -1.0)),
    ("caps", ("equity_basis_usd", "max_lots"), (0.0, -0.01, math.nan, math.inf)),
    ("caps", ("notional_ratio_max",), (0.0, 10.5, math.inf, math.nan)),  # 10.5 loosens 10:1
)
BAD_INPUT_CASES = [
    pytest.param(where, name, value, id=f"{where}.{name}={value!r:.12}")
    for where, names, values in BAD_INPUT_TABLE for name in names for value in values
]


@pytest.mark.parametrize(("where", "field", "value"), BAD_INPUT_CASES)
def test_bad_input_is_refused(where, field, value) -> None:
    if where == "spec":
        refusal = refused(make_request(spec=replace(XAU_2DIGIT, **{field: value})))
    else:
        refusal = refused(make_request(**({field: value} if where == "req" else {})),
                          **({field: value} if where == "caps" else {}))
    assert refusal.codes == ("BAD_INPUT",)
    assert names_field(refusal.detail, field), refusal.detail


def test_bad_input_names_every_offending_field() -> None:
    detail = refused(make_request(equity=math.nan, price=-1.0)).detail
    assert names_field(detail, "equity") and names_field(detail, "price")
    assert not names_field(detail, "friction_price")


def test_out_of_range_magnitude_is_bad_input() -> None:
    huge = roomy_request(equity=1e300, balance=1e300, remaining_daily_loss_usd=1e300)
    refusal = refused(huge, equity_basis_usd=1e300)
    assert refusal.codes == ("BAD_INPUT",) and "range" in refusal.detail


# --- digit parity --------------------------------------------------------------
@pytest.mark.parametrize("stop", [0.6, 3.33, 7.0, 9.6, 9.61, 12.0])
@pytest.mark.parametrize("friction", [0.40, 0.22])
def test_three_digit_spec_sizes_like_two_digit(stop, friction) -> None:
    two = make_request(stop_distance=stop, friction_price=friction)
    assert run(replace(two, spec=XAU_3DIGIT)) == run(two)


# --- min_tradeable_equity ----------------------------------------------------
@pytest.mark.parametrize("spec", [XAU_2DIGIT, XAU_3DIGIT], ids=["2digit", "3digit"])
@pytest.mark.parametrize(
    ("stop", "friction", "risk_pct", "expected"),
    [
        (7.0, 0.40, 0.5, 1480.00), (9.6, 0.40, 0.5, 2000.00), (12.0, 0.40, 0.5, 2480.00),
        (7.0, 0.22, 0.5, 1444.00), (7.0, 0.40, 1.0, 740.00),
        (7.0, 0.40, 0.3, 2466.67),   # 7.40 / 0.003, rounded up
    ],
)
def test_min_tradeable_equity(spec, stop, friction, risk_pct, expected) -> None:
    assert min_tradeable_equity(stop, friction, risk_pct, spec) == expected


@pytest.mark.parametrize(
    ("stop", "friction", "risk_pct", "spec"),
    [
        (7.0, 0.40, 0.5, XAU_2DIGIT),
        (12.0, 0.22, 0.3, XAU_3DIGIT),
        (7.0, 0.40, 0.5, replace(XAU_2DIGIT, tick_value_loss=1.0005)),   # $740.37/lot
        (7.0, 0.40, 0.7, replace(XAU_2DIGIT, volume_min=0.015)),         # min lot is 0.02
        (3.0, 0.40, 1.0, replace(XAU_2DIGIT, volume_step=0.1, volume_min=0.1)),
    ],
)
def test_min_tradeable_equity_agrees_with_sizer(stop, friction, risk_pct, spec) -> None:
    edge = min_tradeable_equity(stop, friction, risk_pct, spec)

    def at(equity: float) -> SizingResult | Refusal:
        req, caps = funded(equity, stop_distance=stop, friction_price=friction,
                           risk_pct=risk_pct, spec=spec)
        return run(req, **caps)

    fits, short = at(edge), at(round(edge - 0.01, 2))
    assert isinstance(fits, SizingResult) and fits.lots >= spec.volume_min
    assert isinstance(short, Refusal) and short.codes == ("MIN_LOT_WALL",)


@pytest.mark.parametrize(
    ("stop", "friction", "risk_pct", "spec"),
    [
        (0.0, 0.4, 0.5, XAU_2DIGIT), (math.nan, 0.4, 0.5, XAU_2DIGIT),
        (7.0, -0.1, 0.5, XAU_2DIGIT), (7.0, 0.4, 0.0, XAU_2DIGIT), (7.0, 0.4, 1.5, XAU_2DIGIT),
        (7.0, 0.4, 0.5, replace(XAU_2DIGIT, tick_size=0.0)),
        (1e300, 0.4, 0.5, XAU_2DIGIT),   # outside the Decimal working range
    ],
)
def test_min_tradeable_equity_rejects_bad_input(stop, friction, risk_pct, spec) -> None:
    with pytest.raises(ValueError):
        min_tradeable_equity(stop, friction, risk_pct, spec)


# --- seeded property sweep -----------------------------------------------------
SWEEP_SEED = 20260916
SWEEP_CASES = 3000
STEPS = ("0.01", "0.1", "1", "0.05", "0.001")
TICKS = ("0.01", "0.001", "0.1", "0.05", "0.25")   # all terminate, so the oracle is exact


def _d(value: float) -> Decimal:
    return Decimal(repr(value))


def _random_case(rng: random.Random) -> tuple[SizingRequest, dict[str, float]]:
    step, tick = Decimal(rng.choice(STEPS)), Decimal(rng.choice(TICKS))
    vol_min = step * rng.randint(1, 5)
    contract = rng.choice((1.0, 10.0, 100.0))
    spec = SymbolSpec(
        digits=2, point=float(tick), tick_size=float(tick), tick_value=float(tick) * contract,
        tick_value_loss=round(float(tick) * contract * rng.uniform(0.9, 1.1), 6),
        contract_size=contract, volume_min=float(vol_min), volume_step=float(step),
        volume_max=float(vol_min + step * rng.randint(0, 5000)),
    )
    req = SizingRequest(
        equity=round(rng.uniform(50, 200_000), 2), balance=round(rng.uniform(50, 200_000), 2),
        free_margin=round(rng.uniform(0, 100_000), 2), price=round(rng.uniform(1, 5000), 2),
        stop_distance=round(rng.uniform(0.05, 50), 2), risk_pct=round(rng.uniform(0.01, 1.0), 3),
        size_multiplier=rng.choice((1.0, 0.5, round(rng.uniform(0.01, 1.0), 2))),
        remaining_daily_loss_usd=0.0 if rng.random() < 0.03 else round(rng.uniform(0, 500), 2),
        margin_per_lot=round(rng.uniform(1, 5000), 2),
        friction_price=round(rng.uniform(0, 1), 2), spec=spec,
    )
    caps = {"equity_basis_usd": round(rng.uniform(100, 50_000), 2),
            "max_lots": float(step * rng.randint(1, 2000)),
            "notional_ratio_max": round(rng.uniform(0.5, 10.0), 2)}
    return req, caps


def _oracle(req: SizingRequest, caps: dict[str, float]) -> dict[str, Decimal]:
    """The contract restated with exact Decimal integer division."""
    spec, step = req.spec, _d(req.spec.volume_step)
    basis = min(_d(req.equity), _d(req.balance), _d(caps["equity_basis_usd"]))
    by_equity = basis * _d(req.risk_pct) / 100 * _d(req.size_multiplier)
    budget = min(by_equity, _d(req.remaining_daily_loss_usd) / 2)
    budget = budget.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
    loss_per_lot = ((_d(req.stop_distance) + _d(req.friction_price))
                    / _d(spec.tick_size) * _d(spec.tick_value_loss))
    return {"basis": basis, "budget": budget, "loss_per_lot": loss_per_lot,
            "budget_lots": budget // (loss_per_lot * step) * step}


def _assert_sized_correctly(req, caps, res: SizingResult, oracle: dict[str, Decimal]) -> None:
    step, lots, budget = _d(req.spec.volume_step), _d(res.lots), _d(res.risk_budget_usd)
    assert budget == oracle["budget"]
    assert lots % step == 0 and lots >= _d(req.spec.volume_min)
    assert res.risk_usd <= res.risk_budget_usd

    def within(size: Decimal) -> bool:
        return (size * oracle["loss_per_lot"] <= budget
                and size <= _d(req.spec.volume_max) and size <= _d(caps["max_lots"])
                and size * _d(req.price) * _d(req.spec.contract_size)
                <= _d(caps["notional_ratio_max"]) * oracle["basis"]
                and size * _d(req.margin_per_lot) <= Decimal("0.25") * _d(req.free_margin))

    assert within(lots)
    assert not within(lots + step)   # never undersized by a whole step


def test_seeded_sweep_upholds_invariants() -> None:
    rng = random.Random(SWEEP_SEED)
    outcomes: Counter[str] = Counter()
    for _ in range(SWEEP_CASES):
        req, caps = _random_case(rng)
        result, oracle = size_position(req, **caps), _oracle(req, caps)
        if isinstance(result, Refusal):
            wall = oracle["budget_lots"] < _d(req.spec.volume_min)
            code = "NO_RISK_BUDGET" if oracle["budget"] <= 0 else (
                "MIN_LOT_WALL" if wall else "CAPACITY")
            assert result.codes == (code,), (req, caps, result)
            outcomes[code] += 1
        else:
            _assert_sized_correctly(req, caps, result, oracle)
            outcomes["sized"] += 1
    # Every path must be exercised or the sweep proves nothing.
    assert set(outcomes) == {"sized", "NO_RISK_BUDGET", "MIN_LOT_WALL", "CAPACITY"}, outcomes
    assert outcomes["sized"] >= SWEEP_CASES // 10, outcomes
