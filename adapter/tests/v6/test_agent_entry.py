"""deliberation/agent_entry.py: bounds and checks for entries the agent designs itself."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from app.v6.deliberation import agent_entry as ae
from app.v6.deliberation.context_builder import ContextRequest, build_context, load_bars
from app.v6.schemas.operator_parts import AgentEntryPlan
from app.v6.types import SymbolSpec

from . import engine_fixtures_v6 as ef

BOUNDS = ae.EntryLimits(
    agent_entry_id="agent-100", tick_size=0.01, digits=2, bid=4300.0, ask=4300.2,
    max_entry_distance=15.0, stop_floor=6.0, max_stop_distance=20.0, min_reward_r=1.0,
    max_reward_r=5.0, default_reward_r=2.0, risk_budget_usd=25.0)


def plan(**changes: Any) -> AgentEntryPlan:
    base: dict[str, Any] = {"side": "buy", "order_type": "LIMIT", "entry": 4298.0,
                            "stop": 4290.0, "target": 4314.0, "thesis": "t"}
    return AgentEntryPlan.model_validate(base | changes)


def codes(item: AgentEntryPlan, bounds: ae.EntryLimits = BOUNDS) -> tuple[str, ...]:
    return tuple(problem.code for problem in ae.plan_problems(item, bounds))


def context(**settings: Any):
    config = ef.settings(**settings)
    request = ef.request()
    bars = load_bars(ef.MemoryBars(ef.history()), request.snapshot)
    return build_context(ContextRequest(cycle_id=request.cycle_id, snapshot=request.snapshot,
                                        received_at=request.received_at),
                         bars, config, ef.RECEIVED), config


# --- the plan schema ------------------------------------------------------------------------
@pytest.mark.parametrize("changes", [
    {"order_type": "LIMIT", "entry": None}, {"order_type": "MARKET", "entry": 4300.0}])
def test_limit_needs_an_entry_and_market_has_none(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="LIMIT needs an entry"):
        plan(**changes)


def test_the_thesis_is_bounded_printable_text() -> None:
    assert plan(thesis="line\x07one").thesis == "lineone"
    with pytest.raises(ValueError):
        plan(thesis="x" * 301)


# --- checks ---------------------------------------------------------------------------------
def test_a_plan_inside_the_limits_passes() -> None:
    assert codes(plan()) == ()
    assert ae.reward_r(plan(), BOUNDS) == pytest.approx(2.0)


@pytest.mark.parametrize(("changes", "expected"), [
    ({"entry": 4300.2}, ("LIMIT_NOT_PASSIVE",)),
    ({"entry": 4280.0, "stop": 4272.0, "target": 4296.0}, ("ENTRY_TOO_FAR",)),
    ({"side": "sell", "entry": 4300.0, "stop": 4308.0, "target": 4284.0},
     ("LIMIT_NOT_PASSIVE",)),
    ({"stop": 4299.0}, ("STOP_WRONG_SIDE",)),
    ({"stop": 4293.0}, ("STOP_TOO_TIGHT",)),
    ({"stop": 4277.0}, ("STOP_TOO_WIDE",)),
    ({"target": 4290.0}, ("TARGET_WRONG_SIDE",)),
    ({"target": 4304.0}, ("REWARD_TOO_SMALL",)),
    ({"target": 4339.0}, ("REWARD_TOO_LARGE",)),
    ({"entry": 4300.2, "stop": 4300.5}, ("LIMIT_NOT_PASSIVE", "STOP_WRONG_SIDE")),
])
def test_each_problem_is_reported(changes: dict[str, Any], expected: tuple[str, ...]) -> None:
    assert codes(plan(**changes)) == expected


def test_a_sell_limit_above_the_bid_passes() -> None:
    sell = plan(side="sell", entry=4305.0, stop=4313.0, target=4289.0)
    assert codes(sell) == () and ae.reward_r(sell, BOUNDS) == pytest.approx(2.0)


def test_a_market_entry_uses_the_quote() -> None:
    buy = plan(order_type="MARKET", entry=None, stop=4292.2, target=None)
    sell = plan(side="sell", order_type="MARKET", entry=None, stop=4308.0, target=None)
    assert ae.resolved_entry(buy, BOUNDS) == 4300.2 and codes(buy) == ()
    assert ae.resolved_entry(sell, BOUNDS) == 4300.0 and codes(sell) == ()
    assert ae.reward_r(buy, BOUNDS) == BOUNDS.default_reward_r


def test_prices_snap_to_the_tick_grid() -> None:
    odd = plan(entry=4298.004, stop=4290.006, target=4314.001)
    candidate = ae.agent_candidate(odd, BOUNDS, bar_t=100)
    assert (candidate.entry, candidate.invalidation) == (4298.0, 4290.01)
    assert (candidate.candidate_id, candidate.setup, candidate.side) == (
        "agent-100", "agent", "buy")
    assert candidate.reason_codes == (ae.AGENT_REASON_CODE,)
    assert candidate.features["market"] == 0.0


def test_a_zero_risk_target_is_not_a_reward() -> None:
    flat = plan(stop=4298.0)
    assert ae.reward_r(flat, BOUNDS) == 0.0


def test_an_unfundable_budget_refuses_every_plan() -> None:
    poor = replace(BOUNDS, max_stop_distance=5.0)
    assert not poor.possible and codes(plan()) == ()
    assert codes(plan(), poor) == (ae.PROBLEM_NOT_FUNDABLE,)


def test_passive_edges() -> None:
    assert (BOUNDS.buy_limit_max, BOUNDS.sell_limit_min) == (4300.19, 4300.01)


# --- limits from the market -------------------------------------------------------------------
def test_entry_limits_from_the_context() -> None:
    ctx, config = context(sizing_equity_basis_usd=5000.0)
    bounds = ae.entry_limits(ctx, config, remaining_loss_usd=150.0)

    assert bounds.agent_entry_id == f"agent-{ef.T_BAR}"
    assert (bounds.bid, bounds.ask, bounds.stop_floor) == (4300.0, 4300.2, 6.0)
    basis = min(ctx.account.equity, ctx.account.balance, 5000.0)
    assert bounds.risk_budget_usd == pytest.approx(basis * 0.005)
    # min(budget - $0.40 friction, 3 x ATR(M15) $10) - headroom (1.0 + 2 x spread 0.2)
    expected = min(basis * 0.005 - 0.4, 30.0) - 1.4
    assert bounds.max_stop_distance == pytest.approx(expected)
    assert bounds.max_entry_distance == 15.0 and bounds.possible


def test_the_daily_loss_allowance_caps_the_budget() -> None:
    ctx, config = context(sizing_equity_basis_usd=5000.0)
    assert ae.risk_budget_usd(ctx, config, remaining_loss_usd=12.0) == 6.0  # half of 12
    assert ae.risk_budget_usd(ctx, config, remaining_loss_usd=-5.0) == 0.0
    assert not ae.entry_limits(ctx, config, remaining_loss_usd=12.0).possible


def test_without_an_atr_the_entry_distance_uses_the_stop_floor() -> None:
    ctx, config = context()
    features = {key: value for key, value in ctx.features.items() if key != "atr_m15"}
    bounds = ae.entry_limits(replace(ctx, features=features), config, remaining_loss_usd=100.0)
    assert bounds.max_entry_distance == 2 * bounds.stop_floor


def test_fundable_stop() -> None:
    spec = SymbolSpec(digits=2, point=0.01, tick_size=0.01, tick_value=1.0,
                      tick_value_loss=1.0, contract_size=100.0, volume_min=0.01,
                      volume_step=0.01, volume_max=100.0)
    assert ae.fundable_stop(25.0, 0.4, spec) == pytest.approx(24.6)
    assert ae.fundable_stop(0.1, 0.4, spec) == 0.0
    assert ae.fundable_stop(25.0, 0.4, replace(spec, tick_value_loss=0.0)) == 0.0


def test_round_levels_step_past_a_price_on_the_level() -> None:
    from app.v6.deliberation.packet_extras import _round
    assert _round(4300.0, 50.0) == (4300.0, 4350.0)
    assert _round(4312.5, 10.0) == (4310.0, 4320.0)
    assert _round(3.0, 10.0) == (10.0, 10.0)
