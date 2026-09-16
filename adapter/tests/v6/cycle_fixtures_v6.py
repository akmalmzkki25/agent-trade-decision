"""
Builders for Phase 2 cycle objects: valid agent views, candidates and results.

Every builder returns a fresh frozen object; pass keyword overrides through
`dataclasses.replace` / `model_copy(update=...)` in the test itself.
"""

from __future__ import annotations

from typing import Any, Final

from app.v6.cycle_types import (
    GATE_SPREAD, CalendarAssessment, CalendarEvent, CandidateAssessment, CycleResult,
    CycleTimings, DeliberationInput, DeskViews, HoldReason, MarketContext, ProtocolDecision,
    ProtocolInput, ShadowIntent, ViewRecord, candidate_id_for,
)
from app.v6.market.sessions import session_state
from app.v6.schemas.agents import (
    ChiefDecision, LiquidityView, NewsRiskView, PriceActionView, RankedCandidate, StructureView,
)
from app.v6.types import Bar, Candidate, ExitPlan, GateResult

from .payloads_v6 import BAR_OPEN, M15, as_snapshot, snapshot_payload

CYCLE_ID: Final[str] = "cyc-1789560000"
CANDIDATE_ID: Final[str] = candidate_id_for("displacement", "buy", BAR_OPEN)
EVENT_ID: Final[str] = "mt5:840030016"
TIME_BARRIER_S: Final[int] = 8 * M15


def pa_payload(candidate_id: str = CANDIDATE_ID, conviction: float = 0.7) -> dict[str, Any]:
    return {"abstain": False, "ranked": [
        {"candidate_id": candidate_id, "verdict": "TAKE", "conviction": conviction,
         "reason_codes": ["LEVEL_CONFLUENCE", "CONFIRMED_CLOSE"], "note": "clean break"}]}


def news_payload(event_ids: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"stance": "CLEAR", "size_multiplier": 1.0, "regime": "QUIET",
            "event_ids": list(event_ids), "reason_codes": ["NO_EVENTS"], "note": ""}


def liquidity_payload() -> dict[str, Any]:
    return {"stance": "OK", "size_multiplier": 1.0, "order_style": "LIMIT",
            "reason_codes": ["SPREAD_NORMAL", "DOM_SYNTHETIC"], "note": ""}


def structure_payload() -> dict[str, Any]:
    return {"regime": "TREND_UP", "counter_structure_veto": False, "size_multiplier": 1.0,
            "named_patterns": ["FLAG"], "reason_codes": ["HH_HL_SEQUENCE"], "note": ""}


def chief_payload(action: str = "ENTER", candidate_id: str | None = CANDIDATE_ID
                  ) -> dict[str, Any]:
    return {"action": action, "candidate_id": candidate_id, "risk_tier": "standard",
            "order_style": "LIMIT", "exit_profile": "STANDARD", "confidence": 0.6,
            "rationale": "PA take with no veto", "dissent": ""}


def desk_views() -> DeskViews:
    return DeskViews(
        price_action=PriceActionView(abstain=False, ranked=(RankedCandidate(
            candidate_id=CANDIDATE_ID, verdict="TAKE", conviction=0.7,
            reason_codes=("LEVEL_CONFLUENCE",), note=""),)),
        news_risk=NewsRiskView.model_validate(news_payload(), strict=False),
        liquidity=LiquidityView.model_validate(liquidity_payload(), strict=False),
        structure=StructureView.model_validate(structure_payload(), strict=False),
    )


def chief_decision(action: str = "ENTER") -> ChiefDecision:
    candidate_id = CANDIDATE_ID if action == "ENTER" else None
    return ChiefDecision.model_validate(chief_payload(action, candidate_id), strict=False)


def candidate(candidate_id: str = CANDIDATE_ID, bar_t: int = BAR_OPEN) -> Candidate:
    return Candidate(candidate_id=candidate_id, setup="displacement", side="buy",
                     entry=4300.0, invalidation=4290.0, bar_t=bar_t,
                     features={"range_atr": 1.8, "body_ratio": 0.7},
                     reason_codes=("AT_H1_LEVEL",))


def exit_plan() -> ExitPlan:
    return ExitPlan(side="buy", entry=4300.0, sl=4289.8, tp=4320.4, stop_distance=10.2,
                    reward_r=2.0, time_barrier_s=TIME_BARRIER_S, labels=("UNMEASURED_GEOMETRY",))


def assessment(candidate_id: str = CANDIDATE_ID, verdict: str = "chosen",
               bar_t: int = BAR_OPEN) -> CandidateAssessment:
    return CandidateAssessment(
        candidate=candidate(candidate_id, bar_t), verdict=verdict,  # type: ignore[arg-type]
        stop=4289.8, target=4320.4, available_from=bar_t + M15 + TIME_BARRIER_S,
        exit_plan=exit_plan())


def protocol_decision(action: str = "ENTER") -> ProtocolDecision:
    if action == "ENTER":
        return ProtocolDecision(action="ENTER", hold_reason=None, candidate_id=CANDIDATE_ID,
                                size_multiplier=1.0, risk_tier="standard")
    return ProtocolDecision(action="HOLD", hold_reason=HoldReason.VETO, candidate_id=None,
                            size_multiplier=0.0, vetoes=("VETO_NEWS_BLOCK",))


def shadow_intent() -> ShadowIntent:
    return ShadowIntent(
        cycle_id=CYCLE_ID, candidate_id=CANDIDATE_ID, setup="displacement", side="buy",
        order_type="BUY_LIMIT", entry=4300.0, sl=4289.8, tp=4320.4, lots=0.01,
        risk_usd=10.4, risk_budget_usd=10.0 + 0.5, size_multiplier=1.0, risk_tier="standard",
        exit_profile="STANDARD", time_barrier_s=TIME_BARRIER_S,
        valid_until_epoch=BAR_OPEN + 3 * M15, source="rules", labels=("UNMEASURED_GEOMETRY",))


def view_records() -> tuple[ViewRecord, ...]:
    views = desk_views()
    return (
        ViewRecord(role="price_action", source="rules", view=views.price_action, latency_ms=1),
        ViewRecord(role="news_risk", source="rules", view=views.news_risk),
        ViewRecord(role="chief", source="openrouter", view=None,
                   error_code="PROVIDER_TIMEOUT", latency_ms=20_000, model="m",
                   tokens_in=900, tokens_out=0, cost_usd=0.001),
    )


def timings() -> CycleTimings:
    return CycleTimings(started_at=BAR_OPEN + M15 + 1.0, finished_at=BAR_OPEN + M15 + 1.25,
                        tier0_ms=40, deliberation_ms=200)


def hold_result(cycle_id: str = CYCLE_ID, *, bar_open: int = BAR_OPEN,
                reason: HoldReason = HoldReason.GATE, status: str = "HOLD") -> CycleResult:
    return CycleResult(
        cycle_id=cycle_id, snapshot_id=f"snap-{cycle_id}", bar_open_epoch=bar_open,
        status=status, hold_reason=reason, backend="rules",  # type: ignore[arg-type]
        provider="rules", provider_status="skipped", timings=timings(),
        gates=(GateResult(code=GATE_SPREAD, passed=False, value=40, limit=35),),
    )


def enter_result(cycle_id: str = CYCLE_ID, *, bar_open: int = BAR_OPEN,
                 candidate_ids: tuple[str, ...] = (CANDIDATE_ID,)) -> CycleResult:
    return CycleResult(
        cycle_id=cycle_id, snapshot_id=f"snap-{cycle_id}", bar_open_epoch=bar_open,
        status="ENTER_SHADOW", hold_reason=None, backend="openrouter", provider="rules",
        provider_status="backend_not_built", timings=timings(), session_id="abc123",
        gates=(GateResult(code=GATE_SPREAD, passed=True, value=20, limit=35),),
        candidates=tuple(assessment(cid, bar_t=bar_open) for cid in candidate_ids),
        views=desk_views(), view_records=view_records(), decision=chief_decision(),
        protocol=protocol_decision(), exit_plan=exit_plan(), shadow_intent=shadow_intent(),
    )


def calendar(blackout: bool = False) -> CalendarAssessment:
    event = CalendarEvent(event_id=EVENT_ID, source="mt5", time_epoch=BAR_OPEN + 2 * 3600,
                          currency="USD", importance="HIGH", code="cpi-yy", forecast=2.9)
    return CalendarAssessment(as_of_epoch=BAR_OPEN + M15, blackout=blackout, codes=(),
                              next_event_minutes=105.0, last_event_minutes_ago=None,
                              stale=False, events=(event,))


def market_context(features: dict[str, float] | None = None,
                   bars: dict[str, tuple[Bar, ...]] | None = None) -> MarketContext:
    snapshot = as_snapshot(snapshot_payload())
    return MarketContext.from_snapshot(
        snapshot, cycle_id=CYCLE_ID, received_at=float(BAR_OPEN + M15 + 1),
        bars={} if bars is None else bars,  # type: ignore[arg-type]
        session=session_state(BAR_OPEN + M15), calendar=calendar(),
        features={"atr_m5": 3.1} if features is None else features)


def deliberation_input(views: DeskViews | None = None) -> DeliberationInput:
    return DeliberationInput(
        context=market_context(),
        gates=(GateResult(code=GATE_SPREAD, passed=True, value=20, limit=35),),
        offered=(assessment(verdict="unranked"),),
        views=DeskViews() if views is None else views)


def protocol_input(decision: ChiefDecision | None = None) -> ProtocolInput:
    return ProtocolInput(
        gates=(), calendar=calendar(), offered_ids=frozenset({CANDIDATE_ID}),
        views=desk_views(), decision=chief_decision() if decision is None else decision,
        pa_min_conviction=0.6, structure_veto="log")
