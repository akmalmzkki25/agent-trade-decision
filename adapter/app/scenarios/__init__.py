from __future__ import annotations

from ..models import LayerPlanRequest, V3PlanRequest, V4PlanRequest, V5BurstRequest
from .aggressive_bias import AggressiveBias
from .base import Scenario, ScenarioResult
from .continuation_pullback import ContinuationPullback
from .liquidity_zone import LiquidityZoneEntry
from .momentum_m1 import MomentumM1
from .range_revert import RangeRevert
from .scalp_micro import ScalpMicro
from .trend_breakout_stop import TrendBreakoutStop

MIN_SCENARIO_SCORE = 0.55
MIN_SCENARIO_SCORE_V3 = 0.35   # V3 aggressive: floor + AGGRESSIVE_BIAS fallback
MIN_SCENARIO_SCORE_V4 = 0.55   # V4 quality-first: only confirmed zones
MIN_SCENARIO_SCORE_V5 = 0.55   # V5 scalper: confirmed micro setup only


def all_scenarios() -> list[Scenario]:
    return [RangeRevert(), TrendBreakoutStop(), ContinuationPullback()]


def all_scenarios_v3() -> list[Scenario]:
    return [
        RangeRevert(),
        TrendBreakoutStop(),
        ContinuationPullback(),
        MomentumM1(),
        AggressiveBias(),   # always-on fallback
    ]


def select_best(req: LayerPlanRequest) -> ScenarioResult | None:
    results = [s.evaluate(req) for s in all_scenarios()]
    candidates = [r for r in results if r.score >= MIN_SCENARIO_SCORE and r.side != "none"]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r.score)


def select_best_v3(req: V3PlanRequest) -> ScenarioResult | None:
    results = [s.evaluate(req) for s in all_scenarios_v3()]
    candidates = [r for r in results if r.score >= MIN_SCENARIO_SCORE_V3 and r.side != "none"]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r.score)


def select_best_v4(req: V4PlanRequest) -> ScenarioResult | None:
    # V4 = single scenario, quality only.
    r = LiquidityZoneEntry().evaluate(req)
    if r.score >= MIN_SCENARIO_SCORE_V4 and r.side != "none":
        return r
    return None


def evaluate_v5(req: V5BurstRequest) -> ScenarioResult:
    """
    Raw scalp evaluation, returned whether or not it clears the threshold.

    The endpoint needs the losing result too: "ATR_TOO_QUIET" and
    "VSA_CLIMAX_VETO" are the answer to "why isn't the bot trading?", and
    collapsing them into a bare None throws that away.
    """
    return ScalpMicro().evaluate(req)


def select_best_v5(req: V5BurstRequest) -> ScenarioResult | None:
    # V5 = single scalp scenario, tick-driven.
    r = evaluate_v5(req)
    if r.score >= MIN_SCENARIO_SCORE_V5 and r.side != "none":
        return r
    return None


__all__ = [
    "MIN_SCENARIO_SCORE",
    "MIN_SCENARIO_SCORE_V3",
    "MIN_SCENARIO_SCORE_V4",
    "MIN_SCENARIO_SCORE_V5",
    "Scenario",
    "ScenarioResult",
    "RangeRevert",
    "TrendBreakoutStop",
    "ContinuationPullback",
    "MomentumM1",
    "AggressiveBias",
    "LiquidityZoneEntry",
    "ScalpMicro",
    "all_scenarios",
    "all_scenarios_v3",
    "evaluate_v5",
    "select_best",
    "select_best_v3",
    "select_best_v4",
    "select_best_v5",
]
