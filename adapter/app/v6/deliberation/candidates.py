"""
Candidate bookkeeping for one cycle: exit plans, the offer and verdict labels.

Every detected candidate is recorded with label geometry, so the labeler can
score it whether or not it was offered. Candidates whose exit plan was refused
are never offered and do not use an offer slot; the first
MAX_OFFERED_CANDIDATES accepted ones (detector priority order) are offered and
the rest stay "unranked".
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Final

from ..config import V6Settings
from ..cycle_codes import MAX_OFFERED_CANDIDATES, CandidateVerdictLabel
from ..cycle_types import CandidateAssessment, MarketContext
from ..learning.labeler import LabelerConfig, available_from_for
from ..risk.exits import build_exit_plan
from ..schemas.agents import ChiefDecision, PriceActionView
from ..types import Candidate, ExitPlan, Refusal

Detector = Callable[[MarketContext], tuple[Candidate, ...]]

VERDICT_GATED: Final[CandidateVerdictLabel] = "gated"
VERDICT_REFUSED: Final[CandidateVerdictLabel] = "refused"
VERDICT_UNRANKED: Final[CandidateVerdictLabel] = "unranked"
VERDICT_SKIP: Final[CandidateVerdictLabel] = "skip"
VERDICT_TAKE: Final[CandidateVerdictLabel] = "take"
VERDICT_CHOSEN: Final[CandidateVerdictLabel] = "chosen"
_PA_VERDICTS: Final[dict[str, CandidateVerdictLabel]] = {
    "TAKE": VERDICT_TAKE, "SKIP": VERDICT_SKIP}
_DIRECTION: Final[dict[str, int]] = {"buy": 1, "sell": -1}


@dataclass(frozen=True)
class CandidatePool:
    """All detected candidates (priority order) and the ones offered to the panel."""

    assessments: tuple[CandidateAssessment, ...] = ()
    offered: tuple[CandidateAssessment, ...] = ()

    @property
    def offered_ids(self) -> frozenset[str]:
        return frozenset(item.candidate.candidate_id for item in self.offered)

    def find(self, candidate_id: str | None) -> CandidateAssessment | None:
        return next((item for item in self.offered
                     if item.candidate.candidate_id == candidate_id), None)


def _positive(value: float, fallback: float) -> float:
    return value if math.isfinite(value) and value > 0 else fallback


def _raw_geometry(candidate: Candidate, r_multiple: float) -> tuple[float, float]:
    """Stop and target for a refused plan: the structural stop and the R-multiple target."""
    direction = _DIRECTION.get(candidate.side, 1)
    distance = abs(candidate.entry - candidate.invalidation)
    target = candidate.entry + direction * r_multiple * distance
    stop = _positive(candidate.invalidation, candidate.entry)
    return stop, _positive(target, candidate.entry)


def _assess(candidate: Candidate, plan: ExitPlan | Refusal, settings: V6Settings,
            config: LabelerConfig) -> CandidateAssessment:
    available_from = available_from_for(candidate.bar_t, config)
    if isinstance(plan, Refusal):
        stop, target = _raw_geometry(candidate, settings.tp_r_multiple)
        return CandidateAssessment(candidate=candidate, verdict=VERDICT_REFUSED, stop=stop,
                                   target=target, available_from=available_from, refusal=plan)
    return CandidateAssessment(candidate=candidate, verdict=VERDICT_UNRANKED, stop=plan.sl,
                               target=plan.tp, available_from=available_from, exit_plan=plan)


def assess_candidates(context: MarketContext, candidates: tuple[Candidate, ...],
                      settings: V6Settings, *, friction_price: float) -> CandidatePool:
    """Exit plan (or refusal) and label geometry for every candidate; the offer."""
    config = LabelerConfig.from_settings(settings, context.spec.point)
    assessments = tuple(
        _assess(candidate, build_exit_plan(
            candidate, spread_price=context.spread_price, friction_price=friction_price,
            spec=context.spec, stop_floor_points=settings.stop_floor_points,
            tp_r_multiple=settings.tp_r_multiple, time_barrier_s=settings.time_barrier_s,
        ), settings, config)
        for candidate in candidates)
    accepted = tuple(item for item in assessments if item.exit_plan is not None)
    return CandidatePool(assessments=assessments, offered=accepted[:MAX_OFFERED_CANDIDATES])


def _pa_verdicts(view: PriceActionView | None) -> dict[str, CandidateVerdictLabel]:
    if not isinstance(view, PriceActionView):
        return {}
    return {item.candidate_id: _PA_VERDICTS[item.verdict] for item in view.ranked}


def _chosen_id(decision: ChiefDecision | None) -> str | None:
    if isinstance(decision, ChiefDecision) and decision.action == "ENTER":
        return decision.candidate_id
    return None


def label_verdicts(pool: CandidatePool, *, gated: bool = False,
                   price_action: PriceActionView | None = None,
                   decision: ChiefDecision | None = None) -> tuple[CandidateAssessment, ...]:
    """Final verdicts. Refusals are kept, a failed gate marks the rest `gated`."""
    offered = pool.offered_ids
    ranked = _pa_verdicts(price_action)
    chosen = _chosen_id(decision)

    def verdict(item: CandidateAssessment) -> CandidateVerdictLabel:
        candidate_id = item.candidate.candidate_id
        if item.verdict == VERDICT_REFUSED:
            return VERDICT_REFUSED
        if gated:
            return VERDICT_GATED
        if candidate_id not in offered:
            return VERDICT_UNRANKED
        if candidate_id == chosen:
            return VERDICT_CHOSEN
        return ranked.get(candidate_id, VERDICT_UNRANKED)

    return tuple(replace(item, verdict=verdict(item)) for item in pool.assessments)
