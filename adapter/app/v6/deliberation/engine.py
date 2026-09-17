"""
Deliberation engine: one closed M15 bar in, one recorded CycleResult out.

    snapshot -> MarketContext (bars, features, session, calendar)
             -> hard gates (all evaluated) -> setup detectors -> exit plans
             -> R0 rules desk views (every cycle)
             -> HOLD on a failed gate / no candidate / nothing offerable / no session
             -> tier 1: the rules panel, or the operator backend's packet and decision
             -> protocol.resolve -> exits + sizing -> ShadowIntent (the sized order)
             -> execute mode, operator decision: publish through the IntentPort

The operator backend asks on every bar that passes the hard gates while a session
is active, with or without detector suggestions: the agent may design its own
entry (user decision 2026-09-17), which `agent_entry` turns into a candidate that
takes the same exits/sizing/intent path. It waits until bar close +
V6_OPERATOR_DEADLINE_S and holds with APP-V6-OPERATOR-TIMEOUT when no decision
arrives. The rules backend never
publishes (execute mode requires the operator backend). A published intent makes
the cycle ENTER with its intent id; a decision the session may not publish stays
ENTER_SHADOW; a publishing refusal holds with its reason.

The engine never raises: any error becomes status ERROR (APP-V6-ERROR) with the
error type in `hold_detail`, keeping whatever the cycle had gathered. A decision
after the backend's deadline is LATE. With a FakeClock the engine is deterministic.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Final

import anyio

from ..clock import Clock
from ..config import V6Settings
from ..cycle_codes import CycleStatus, HoldReason, hold_reason_for_gates
from ..cycle_types import DeliberationInput, MarketContext, ProtocolDecision, ProtocolInput
from ..providers.base import (
    OPERATOR_PROVIDER_NAME, PROVIDER_STATUS_FAILED, RULES_PROVIDER_NAME, AgentProvider,
)
from ..providers.offline import OfflineProvider
from ..providers.operator_queue import OperatorQueue
from ..risk import limits
from ..risk.breakers import BreakerStatus
from ..risk.gates import evaluate_gates, failed_codes
from ..risk.policy import EXECUTE_MODE
from ..schemas.operator import OperatorPacket
from ..schemas.operator_parts import AgentEntryPlan
from ..setups import detect_all
from ..cycle_types import CandidateAssessment
from ..types import GateResult
from .agent_entry import agent_candidate, limits_from_packet
from .candidates import (
    VERDICT_CHOSEN, CandidatePool, Detector, assess_candidate, assess_candidates, label_verdicts,
)
from .context_builder import (
    BarReader, ContextRequest, as_of_for, build_context, cycle_friction, load_bars,
)
from .cycle_draft import CycleDraft, CycleOutcome, CycleRequest, elapsed_ms
from .operator_packet import PacketRefusal, PacketRequest
from .operator_tier import OperatorRound, operator_round
from .pending_review import REASON_AGENT_CANCEL, review_hold, wants_review
from .panel import Baseline, PanelResult, rules_baseline, run_panel
from .protocol import resolve
from .publication import IntentPort, PublishRequest
from .shadow import shadow_order

logger = logging.getLogger(__name__)

BreakerSource = Callable[[MarketContext], Awaitable[BreakerStatus]]

# R0 is local and cheap; it gets its own budget so a LATE cycle still shows its desks.
BASELINE_BUDGET_S: Final[float] = 10.0
BREAKER_FAILED_DETAIL: Final[str] = "breaker evaluation failed"
DETAIL_NO_CANDIDATE: Final[str] = "detectors found no candidate on this bar"
DETAIL_NO_SESSION: Final[str] = "no active trading session: tier 0 only"
DETAIL_LATE: Final[str] = "decision deadline passed"
DETAIL_AGENT_EXIT: Final[str] = "the agent entry was refused by the exit plan: "
STATUS_HOLD: Final[CycleStatus] = "HOLD"
STATUS_ENTER: Final[CycleStatus] = "ENTER_SHADOW"
STATUS_PUBLISHED: Final[CycleStatus] = "ENTER"
STATUS_LATE: Final[CycleStatus] = "LATE"
STATUS_ERROR: Final[CycleStatus] = "ERROR"


@dataclass(frozen=True)
class EngineDeps:
    settings: V6Settings
    clock: Clock
    bars: BarReader
    breakers: BreakerSource
    rules: OfflineProvider
    panel: AgentProvider | None       # None: operator backend (decided through `operator`)
    detector: Detector = detect_all
    operator: OperatorQueue | None = None     # the operator backend's decision channel
    publisher: IntentPort | None = None       # execute mode: publishes approved entries


@dataclass(frozen=True)
class Tier0:
    context: MarketContext
    gates: tuple[GateResult, ...]
    breakers: BreakerStatus
    pool: CandidatePool
    inputs: DeliberationInput
    baseline: Baseline


def rules_spread_ceiling(settings: V6Settings) -> int:
    """The rules desks keep kn/15's preferred spread line, never above the gate."""
    return min(settings.effective_max_spread_points,
               limits.PREFERRED_SPREAD_POINTS[settings.account_type])


def rules_provider_for(settings: V6Settings, clock: Clock) -> OfflineProvider:
    return OfflineProvider(clock=clock, pa_min_conviction=settings.pa_min_conviction,
                           structure_veto=settings.structure_veto,
                           max_spread_points=rules_spread_ceiling(settings))


def panel_for_backend(settings: V6Settings, rules: OfflineProvider) -> AgentProvider | None:
    """The rules backend decides itself; the operator backend decides through its queue."""
    return rules if settings.backend == RULES_PROVIDER_NAME else None


def build_engine(settings: V6Settings, clock: Clock, bars: BarReader,
                 breakers: BreakerSource, *, operator: OperatorQueue | None = None,
                 publisher: IntentPort | None = None) -> "DeliberationEngine":
    rules = rules_provider_for(settings, clock)
    uses_operator = settings.backend == OPERATOR_PROVIDER_NAME
    return DeliberationEngine(EngineDeps(
        settings=settings, clock=clock, bars=bars, breakers=breakers, rules=rules,
        panel=panel_for_backend(settings, rules),
        operator=operator if uses_operator else None,
        publisher=publisher if uses_operator else None))


class DeliberationEngine:
    def __init__(self, deps: EngineDeps) -> None:
        self._deps = deps

    @property
    def settings(self) -> V6Settings:
        return self._deps.settings

    @property
    def deps(self) -> EngineDeps:
        return self._deps

    def _now(self) -> float:
        return self._deps.clock.now_epoch()

    def _deadline(self, request: CycleRequest) -> float:
        settings = self._deps.settings
        budget = (settings.operator_deadline_s if settings.backend == OPERATOR_PROVIDER_NAME
                  else settings.decision_deadline_s)
        return float(as_of_for(request.snapshot) + budget)

    def _late(self, request: CycleRequest) -> bool:
        return self._now() > self._deadline(request)

    # --- public ----------------------------------------------------------------
    async def run(self, request: CycleRequest) -> CycleOutcome:
        """Run one cycle; never raises (CancelledError aside)."""
        draft = CycleDraft(request=request, backend=self._deps.settings.backend,
                           started_at=self._now())
        try:
            return await self._run(draft)
        except Exception as exc:  # noqa: BLE001 - a cycle must always be recorded
            logger.error("v6 cycle %s could not be recorded in full: %s",
                         request.cycle_id, type(exc).__name__, exc_info=True)
            return draft.bare().finish(STATUS_ERROR, HoldReason.ERROR,
                                       f"record: {type(exc).__name__}", self._now())

    # --- stages ------------------------------------------------------------------
    async def _run(self, draft: CycleDraft) -> CycleOutcome:
        try:
            tier0 = await self._tier0(draft.request)
        except Exception as exc:  # noqa: BLE001 - reported as an ERROR cycle
            return self._error(draft, "tier0", exc)
        draft = draft.update(
            context=tier0.context, gates=tier0.gates, views=tier0.baseline.views,
            view_records=tier0.baseline.records, candidates=label_verdicts(tier0.pool),
            tier0_ms=elapsed_ms(draft.started_at, self._now()))
        try:
            return await self._decide(draft, tier0)
        except Exception as exc:  # noqa: BLE001 - reported as an ERROR cycle
            return self._error(draft.update(provider_status=PROVIDER_STATUS_FAILED),
                               "tier1", exc)

    def _error(self, draft: CycleDraft, stage: str, exc: Exception) -> CycleOutcome:
        logger.error("v6 cycle %s failed in %s: %s", draft.request.cycle_id, stage,
                     type(exc).__name__, exc_info=True)
        return draft.finish(STATUS_ERROR, HoldReason.ERROR, f"{stage}: {type(exc).__name__}",
                            self._now())

    async def _breaker_status(self, context: MarketContext) -> BreakerStatus:
        try:
            return await self._deps.breakers(context)
        except Exception as exc:  # noqa: BLE001 - fail closed: the BREAKER gate holds
            logger.error("v6 breaker source failed for %s: %s", context.cycle_id,
                         type(exc).__name__)
            return BreakerStatus(unavailable=BREAKER_FAILED_DETAIL)

    async def _tier0(self, request: CycleRequest) -> Tier0:
        deps = self._deps
        bars = await asyncio.to_thread(load_bars, deps.bars, request.snapshot)
        context = build_context(
            ContextRequest(cycle_id=request.cycle_id, snapshot=request.snapshot,
                           received_at=request.received_at,
                           carried_events=request.carried_events, probe=request.probe),
            bars, deps.settings, self._now())
        breakers = await self._breaker_status(context)
        gates = evaluate_gates(context, context.calendar, request.runtime, deps.settings,
                               breakers, self._now())
        pool = assess_candidates(context, deps.detector(context), deps.settings,
                                 friction_price=cycle_friction(deps.settings, context))
        inputs = DeliberationInput(context=context, gates=gates, offered=pool.offered)
        budget = max(self._deadline(request), self._now() + BASELINE_BUDGET_S)
        baseline = await rules_baseline(deps.rules, inputs, budget, deps.clock)
        return Tier0(context=context, gates=gates, breakers=breakers, pool=pool,
                     inputs=inputs, baseline=baseline)

    def _hold(self, draft: CycleDraft, reason: HoldReason, detail: str,
              status: CycleStatus = STATUS_HOLD) -> CycleOutcome:
        if reason == HoldReason.LATE:
            status = STATUS_LATE
        return draft.finish(status, reason, detail, self._now())

    async def _decide(self, draft: CycleDraft, tier0: Tier0) -> CycleOutcome:
        request, pool = draft.request, tier0.pool
        gate_hold = hold_reason_for_gates(tier0.gates)
        if (gate_hold is not None and self._deps.operator is not None
                and wants_review(tier0.context, tier0.gates)):
            return await self._operator_path(self._deps.operator, draft, tier0, review=True)
        if gate_hold is not None:
            detail = "failed gates: " + ",".join(failed_codes(tier0.gates))
            return self._hold(draft.update(candidates=label_verdicts(pool, gated=True)),
                              gate_hold, detail)
        if self._deps.operator is not None:
            return await self._operator_path(self._deps.operator, draft, tier0)
        if not pool.assessments:
            return self._hold(draft, HoldReason.NO_CANDIDATE, DETAIL_NO_CANDIDATE)
        if not pool.offered:
            return self._hold(draft, HoldReason.EXIT, _refusal_detail(pool))
        if request.session_id is None:
            return self._hold(draft, HoldReason.NO_SESSION, DETAIL_NO_SESSION)
        if self._late(request):
            return self._hold(draft, HoldReason.LATE, DETAIL_LATE)
        panel = await self._tier1(tier0, request)
        if panel is None:
            return self._hold(self._timed(draft), HoldReason.LATE, DETAIL_LATE)
        return await self._resolve(self._with_panel(draft, tier0, panel), tier0, panel)

    def _timed(self, draft: CycleDraft) -> CycleDraft:
        deliberation_ms = elapsed_ms(draft.started_at, self._now()) - draft.tier0_ms
        return draft.update(deliberation_ms=max(0, deliberation_ms))

    def _with_panel(self, draft: CycleDraft, tier0: Tier0, panel: PanelResult) -> CycleDraft:
        return self._timed(draft).update(
            provider=panel.provider, provider_status=panel.status, views=panel.views,
            view_records=draft.view_records + panel.records, decision=panel.decision,
            candidates=label_verdicts(tier0.pool, price_action=panel.views.price_action,
                                      decision=panel.decision))

    async def _tier1(self, tier0: Tier0, request: CycleRequest) -> PanelResult | None:
        deps = self._deps
        deadline = self._deadline(request)
        panel: PanelResult | None = None
        with anyio.move_on_after(max(0.0, deadline - self._now())):
            panel = await run_panel(deps.panel, deps.rules, tier0.inputs, tier0.baseline,
                                    deadline, deps.clock)
        return panel

    async def _operator_path(self, queue: OperatorQueue, draft: CycleDraft,
                             tier0: Tier0, review: bool = False) -> CycleOutcome:
        """Every gated-in bar with a session goes to the agent, suggestions or not; a bar
        with a resting V6 order goes to it as a review."""
        request = draft.request
        if request.session_id is None:
            return self._hold(draft, HoldReason.NO_SESSION, DETAIL_NO_SESSION)
        if self._late(request):
            return self._hold(draft, HoldReason.LATE, DETAIL_LATE)
        return await self._operator_decide(queue, draft, tier0, review)

    async def _operator_decide(self, queue: OperatorQueue, draft: CycleDraft,
                               tier0: Tier0, review: bool = False) -> CycleOutcome:
        deps, request = self._deps, draft.request
        packet_request = PacketRequest(
            context=tier0.context, gates=tier0.gates, offered=tier0.pool.offered,
            baseline=tier0.baseline.views, remaining_loss_usd=tier0.breakers.remaining_loss_usd,
            session_id=request.session_id, armed=request.session_armed, now=self._now(),
            deadline_epoch=self._deadline(request), review=review)
        outcome = await operator_round(queue, packet_request, deps.settings, tier0.baseline,
                                       deps.clock)
        if isinstance(outcome, PacketRefusal):
            return self._hold(self._timed(draft), outcome.hold_reason,
                              f"{outcome.code}: {outcome.detail}")
        draft = self._with_panel(draft, tier0, outcome.panel)
        if outcome.decision is None:
            return self._hold(draft, HoldReason.OPERATOR_TIMEOUT, outcome.detail)
        if review:
            return await self._review_result(draft, outcome.decision.pending_action)
        return await self._resolve(draft, tier0, outcome.panel, outcome)

    async def _review_result(self, draft: CycleDraft, action: str | None) -> CycleOutcome:
        reason, detail, cancel = review_hold(action)
        publisher = self._deps.publisher
        if cancel and publisher is not None and self._deps.settings.mode == EXECUTE_MODE:
            cancelled = await publisher.cancel_pending(REASON_AGENT_CANCEL)
            detail += f" (undelivered intents cancelled: {len(cancelled)})"
        return self._hold(draft, reason, detail)

    async def _resolve(self, draft: CycleDraft, tier0: Tier0, panel: PanelResult,
                       operator: OperatorRound | None = None) -> CycleOutcome:
        settings = self._deps.settings
        protocol = resolve(ProtocolInput(
            gates=tier0.gates, calendar=tier0.context.calendar,
            offered_ids=tier0.pool.offered_ids if operator is None else operator.offered_ids,
            views=panel.views, decision=panel.decision,
            pa_min_conviction=settings.pa_min_conviction,
            structure_veto=settings.structure_veto,
            withdrawn_ids=frozenset() if operator is None else operator.withdrawn_ids,
        ), fallback=tier0.baseline.views)
        draft = draft.update(protocol=protocol)
        if protocol.hold_reason is not None:
            return self._hold(draft, protocol.hold_reason, protocol.detail)
        agent = None if operator is None else operator.agent
        lots = None if operator is None or operator.decision is None else (
            operator.decision.lots or tier0.context.spec.volume_min)
        item = tier0.pool.find(protocol.candidate_id)
        plan = None if operator is None or operator.decision is None else (
            operator.decision.entry_plan)
        if item is None and plan is not None:
            item = self._agent_item(tier0, operator.packet, plan)
            draft = draft.update(candidates=draft.candidates + (item,))
            if item.exit_plan is None:
                codes = "" if item.refusal is None else ",".join(item.refusal.codes)
                return self._hold(draft, HoldReason.EXIT, DETAIL_AGENT_EXIT + codes)
        if item is None:
            raise ValueError("the protocol picked a candidate that was not offered")
        return await self._enter(draft, tier0, panel, protocol, agent, item, lots)

    def _agent_item(self, tier0: Tier0, packet: OperatorPacket,
                    plan: AgentEntryPlan) -> CandidateAssessment:
        """The agent's own entry as an assessed candidate, judged by the packet's limits."""
        settings, context = self._deps.settings, tier0.context
        candidate = agent_candidate(plan, limits_from_packet(packet), context.bar_open_epoch)
        item = assess_candidate(context, candidate, settings,
                                friction_price=cycle_friction(settings, context),
                                tp_r_multiple=candidate.features["reward_r"])
        if item.exit_plan is None:
            return item
        return replace(item, verdict=VERDICT_CHOSEN)

    async def _enter(self, draft: CycleDraft, tier0: Tier0, panel: PanelResult,
                     protocol: ProtocolDecision, agent: str | None,
                     item: CandidateAssessment, lots: float | None = None) -> CycleOutcome:
        order = shadow_order(tier0.context, item, protocol, source=panel.provider,
                             remaining_loss_usd=tier0.breakers.remaining_loss_usd,
                             settings=self._deps.settings, lots_cap=lots)
        draft = draft.update(exit_plan=order.exit_plan, sizing=order.sizing,
                             refusal=order.refusal)
        if order.policy is not None and not order.policy.allowed:
            return self._hold(draft, HoldReason.GATE,
                              f"intent source refused: {order.policy.code}")
        if order.intent is None or order.sizing is None:
            codes = "" if order.refusal is None else ",".join(order.refusal.codes)
            return self._hold(draft, HoldReason.SIZE, f"sizing refused: {codes}")
        if self._late(draft.request):
            return self._hold(draft, HoldReason.LATE, DETAIL_LATE)
        draft = draft.update(shadow_intent=order.intent)
        publisher = self._deps.publisher
        if agent is None or publisher is None or self._deps.settings.mode != EXECUTE_MODE:
            return draft.finish(STATUS_ENTER, None, "", self._now())
        return await self._publish(publisher, draft, PublishRequest(
            decision=protocol, candidate=item.candidate, exit_plan=order.exit_plan,
            sizing=order.sizing, context=tier0.context, agent=agent))

    async def _publish(self, publisher: IntentPort, draft: CycleDraft,
                       request: PublishRequest) -> CycleOutcome:
        outcome = await publisher.publish(request)
        if outcome.published:
            return draft.update(intent_id=outcome.intent_id).finish(
                STATUS_PUBLISHED, None, "", self._now())
        if outcome.hold_reason is None:
            return draft.finish(STATUS_ENTER, None, outcome.detail, self._now())
        return self._hold(draft, outcome.hold_reason, outcome.detail)


def _refusal_detail(pool: CandidatePool) -> str:
    codes = sorted({code for item in pool.assessments if item.refusal is not None
                    for code in item.refusal.codes})
    return "every candidate was refused by the exit plan: " + ",".join(codes)
