from __future__ import annotations

from ..models import LayerPlanRequest
from .base import Scenario, ScenarioResult
from .continuation_pullback import ContinuationPullback
from .range_revert import RangeRevert
from .trend_breakout_stop import TrendBreakoutStop

MIN_SCENARIO_SCORE = 0.55


def all_scenarios() -> list[Scenario]:
    return [RangeRevert(), TrendBreakoutStop(), ContinuationPullback()]


def select_best(req: LayerPlanRequest) -> ScenarioResult | None:
    results = [s.evaluate(req) for s in all_scenarios()]
    candidates = [r for r in results if r.score >= MIN_SCENARIO_SCORE and r.side != "none"]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r.score)


__all__ = [
    "MIN_SCENARIO_SCORE",
    "Scenario",
    "ScenarioResult",
    "RangeRevert",
    "TrendBreakoutStop",
    "ContinuationPullback",
    "all_scenarios",
    "select_best",
]
