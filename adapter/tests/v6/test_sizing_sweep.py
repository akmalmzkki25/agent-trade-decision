"""
Seeded random sweeps of the V6 position sizer (`app.v6.risk.sizing`), checked against
an independent Decimal oracle, plus the promise the operator packet relies on: a size
at the standard tier (multiplier 1) survives every multiplier the protocol accepts.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import replace
from decimal import ROUND_FLOOR, Decimal, localcontext

from app.v6.cycle_codes import MIN_COMBINED_MULTIPLIER
from app.v6.risk.sizing import MIN_LOT_FLOOR, size_position
from app.v6.types import Refusal, SizingRequest, SizingResult, SymbolSpec

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


def _cents_down(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)


def _oracle(req: SizingRequest, caps: dict[str, float]) -> dict[str, Decimal]:
    """The contract restated with exact Decimal integer division."""
    spec, step = req.spec, _d(req.spec.volume_step)
    basis = min(_d(req.equity), _d(req.balance), _d(caps["equity_basis_usd"]))
    share, daily = basis * _d(req.risk_pct) / 100, _d(req.remaining_daily_loss_usd) / 2
    full = _cents_down(min(share, daily))
    budget = _cents_down(min(share * _d(req.size_multiplier), daily))
    loss_per_lot = ((_d(req.stop_distance) + _d(req.friction_price))
                    / _d(spec.tick_size) * _d(spec.tick_value_loss))
    with localcontext(prec=60, rounding=ROUND_FLOOR):   # exact enough to floor at a step
        cap_lots = min(
            _d(spec.volume_max), _d(caps["max_lots"]),
            _d(caps["notional_ratio_max"]) * basis / (_d(req.price) * _d(spec.contract_size)),
            Decimal("0.25") * _d(req.free_margin) / _d(req.margin_per_lot))
    return {"basis": basis, "budget": budget, "full": full, "loss_per_lot": loss_per_lot,
            "budget_lots": budget // (loss_per_lot * step) * step,
            "full_lots": full // (loss_per_lot * step) * step,
            "cap_lots": cap_lots // step * step}


def _expected(req: SizingRequest, oracle: dict[str, Decimal]) -> str:
    """"sized", "floor" or the refusal code the contract demands."""
    volume_min = _d(req.spec.volume_min)
    caps_ok = oracle["cap_lots"] >= volume_min
    if oracle["full"] <= 0:
        return "NO_RISK_BUDGET"
    if oracle["budget_lots"] >= volume_min:
        return "sized" if caps_ok else "CAPACITY"
    if oracle["full_lots"] < volume_min or req.size_multiplier < 0.25:
        return "MIN_LOT_WALL"
    return "floor" if caps_ok else "CAPACITY"


def _within(req, caps, oracle, size: Decimal, budget: Decimal) -> bool:
    return (size * oracle["loss_per_lot"] <= budget
            and size <= _d(req.spec.volume_max) and size <= _d(caps["max_lots"])
            and size * _d(req.price) * _d(req.spec.contract_size)
            <= _d(caps["notional_ratio_max"]) * oracle["basis"]
            and size * _d(req.margin_per_lot) <= Decimal("0.25") * _d(req.free_margin))


def _assert_sized_correctly(req, caps, res: SizingResult, oracle: dict[str, Decimal],
                            expected: str) -> None:
    step, lots, budget = _d(req.spec.volume_step), _d(res.lots), _d(res.risk_budget_usd)
    assert lots % step == 0 and lots >= _d(req.spec.volume_min)
    assert res.risk_usd <= res.risk_budget_usd
    assert _within(req, caps, oracle, lots, budget)
    if expected == "floor":
        # The smallest size the sizer can return, paid for by the full budget.
        assert (budget, res.labels) == (oracle["full"], ("MIN_LOT_FLOOR",))
        assert lots - step < _d(req.spec.volume_min) and oracle["budget_lots"] < lots
        return
    assert (budget, res.labels) == (oracle["budget"], ())
    assert not _within(req, caps, oracle, lots + step, budget)   # never undersized by a step


def test_seeded_sweep_upholds_invariants() -> None:
    rng = random.Random(SWEEP_SEED)
    outcomes: Counter[str] = Counter()
    for _ in range(SWEEP_CASES):
        req, caps = _random_case(rng)
        result, oracle = size_position(req, **caps), _oracle(req, caps)
        expected = _expected(req, oracle)
        if isinstance(result, Refusal):
            assert result.codes == (expected,), (req, caps, result)
        else:
            _assert_sized_correctly(req, caps, result, oracle, expected)
        outcomes[expected] += 1
    # Every path must be exercised or the sweep proves nothing.
    assert set(outcomes) == {"sized", "floor", "NO_RISK_BUDGET", "MIN_LOT_WALL", "CAPACITY"}, (
        outcomes)
    assert outcomes["sized"] >= SWEEP_CASES // 10, outcomes


# --- sized at multiplier 1 means sized at every multiplier the protocol accepts ------------
ALLOWED_MULTIPLIERS = (MIN_COMBINED_MULTIPLIER, 0.3, 0.375, 0.5, 0.64, 0.75, 0.8, 1.0)


def test_a_standard_tier_size_survives_every_allowed_multiplier() -> None:
    rng = random.Random(SWEEP_SEED + 1)
    outcomes: Counter[str] = Counter()
    for _ in range(SWEEP_CASES):
        base, caps = _random_case(rng)
        standard = size_position(replace(base, size_multiplier=1.0), **caps)
        results = [size_position(replace(base, size_multiplier=m), **caps)
                   for m in ALLOWED_MULTIPLIERS]
        if isinstance(standard, Refusal):
            # Refused at the standard tier: refused for the same reason at every tier.
            assert all(isinstance(r, Refusal) and r.codes == standard.codes for r in results)
            outcomes["refused"] += 1
            continue
        for multiplier, result in zip(ALLOWED_MULTIPLIERS, results):
            assert isinstance(result, SizingResult), (base, caps, multiplier, result)
            # Less risk asked, never more lots, never more than the standard budget at risk.
            assert result.lots <= standard.lots
            assert result.risk_usd <= standard.risk_budget_usd
            assert result.risk_budget_usd <= standard.risk_budget_usd
            outcomes["floor" if MIN_LOT_FLOOR in result.labels else "sized"] += 1
    assert {"refused", "floor", "sized"} <= set(outcomes), outcomes
