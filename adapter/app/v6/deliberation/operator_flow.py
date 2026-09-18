"""
The operator backend's part of a cycle (mixed into `engine.DeliberationEngine`).

A bar that passed the hard gates gets a flat packet; a bar with a V6 position or resting
order that failed only on occupancy or market gates gets a management packet
(`trade_state.wants_management`). Before the packet is built the flow reads what the
adapter stored about the trade (the intent and its plan, the session's newest management
action) and the agent's last M15 bias. An accepted decision's bias is remembered; a
management decision is sent through the IntentPort in execute mode and only recorded
otherwise. An agent entry becomes a candidate that takes the ordinary exits, sizing and
intent path.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING, Final

from ..cycle_codes import HoldReason
from ..cycle_types import CandidateAssessment, MarketContext
from ..ledger_actions import ActionRow
from ..ledger_intents import IntentRecord
from ..providers.operator_queue import OperatorQueue
from ..risk.policy import EXECUTE_MODE
from ..schemas.operator import OperatorPacket
from ..schemas.operator_parts import AgentEntryPlan
from ..schemas.operator_plan import EntryPlanV2, PacketState
from ..types import Refusal
from .agent_entry import agent_candidate, limits_from_packet
from .candidates import VERDICT_CHOSEN, assess_candidate
from .context_builder import cycle_friction
from .cycle_draft import CycleDraft, CycleOutcome
from .management import build_action
from .operator_packet import PacketRefusal, PacketRequest
from .operator_tier import OperatorRound, operator_round
from .plan_rules import PROBLEM_TP3_TRIMMED, bounds_from_packet, ladder_after_exit, plan_candidate
from .publication import ManageDispatch
from .trade_state import stored_intent

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .engine import EngineDeps, Tier0

DETAIL_NO_SESSION: Final[str] = "no active trading session: tier 0 only"
DETAIL_LATE: Final[str] = "decision deadline passed"


class OperatorFlow:
    """The operator backend's part of a cycle. Mixed into DeliberationEngine, whose
    `_deps`, `_now`, `_deadline`, `_late`, `_hold`, `_timed`, `_with_panel` and
    `_resolve` it uses."""

    _deps: EngineDeps

    async def _operator_path(self, queue: OperatorQueue, draft: CycleDraft, tier0: Tier0,
                             state: PacketState = "flat") -> CycleOutcome:
        """Every gated-in bar with a session goes to the agent, suggestions or not; a bar
        with a V6 trade goes to it as a management packet."""
        request = draft.request
        if request.session_id is None:
            return self._hold(draft, HoldReason.NO_SESSION, DETAIL_NO_SESSION)
        if self._late(request):
            return self._hold(draft, HoldReason.LATE, DETAIL_LATE)
        return await self._operator_decide(queue, draft, tier0, state)

    async def _facts(self, context: MarketContext, session_id: str, state: PacketState
                     ) -> tuple[IntentRecord | None, ActionRow | None]:
        plans = self._deps.plans
        if plans is None:
            return None, None
        record = (None if state == "flat"
                  else await asyncio.to_thread(stored_intent, plans, context))
        return record, await asyncio.to_thread(plans.last_action, session_id)

    async def _operator_decide(self, queue: OperatorQueue, draft: CycleDraft, tier0: Tier0,
                               state: PacketState) -> CycleOutcome:
        deps, request = self._deps, draft.request
        record, last_action = await self._facts(tier0.context, request.session_id, state)
        bias, bias_at = deps.bias.latest()
        packet_request = PacketRequest(
            context=tier0.context, gates=tier0.gates, offered=tier0.pool.offered,
            baseline=tier0.baseline.views, remaining_loss_usd=tier0.breakers.remaining_loss_usd,
            session_id=request.session_id, armed=request.session_armed, now=self._now(),
            deadline_epoch=self._deadline(request), state=state, record=record,
            last_action=last_action, last_bias=bias, last_bias_at=bias_at,
            kind=request.packet_kind)
        outcome = await operator_round(queue, packet_request, deps.settings, tier0.baseline,
                                       deps.clock)
        if isinstance(outcome, PacketRefusal):
            return self._hold(self._timed(draft), outcome.hold_reason,
                              f"{outcome.code}: {outcome.detail}")
        draft = self._with_panel(draft, tier0, outcome.panel)
        decision = outcome.decision
        if decision is None:
            return self._hold(draft, HoldReason.OPERATOR_TIMEOUT, outcome.detail)
        if decision.bias is not None and outcome.packet.packet_kind == "m15":
            deps.bias.remember(decision.bias, outcome.packet.bar_close_epoch)
        if state != "flat":
            return await self._manage_result(draft, outcome)
        return await self._resolve(draft, tier0, outcome.panel, outcome)

    async def _manage_result(self, draft: CycleDraft, rnd: OperatorRound) -> CycleOutcome:
        """KEEP holds; CLOSE, CANCEL and MODIFY go to the publisher in execute mode."""
        decision, packet = rnd.decision, rnd.packet
        request = None if decision is None else decision.manage
        if request is None or request.op == "KEEP":
            return self._hold(draft, HoldReason.MANAGE_KEPT, f"manage: keep the {packet.state}")
        publisher = self._deps.publisher
        if publisher is None or self._deps.settings.mode != EXECUTE_MODE:
            return self._hold(draft, HoldReason.MANAGE_KEPT,
                              f"manage: {request.op} recorded only (shadow)")
        action = build_action(request, packet, now=self._now())
        outcome = await publisher.manage(ManageDispatch(
            request=request, action=action, cycle_id=packet.cycle_id, agent=decision.agent,
            session_id=draft.request.session_id or ""))
        reason = HoldReason.MANAGE_SENT if outcome.sent else HoldReason.MANAGE_REFUSED
        return self._hold(draft, reason, f"manage {request.op}: {outcome.code} {outcome.detail}")

    def _plan_item(self, tier0: Tier0, packet: OperatorPacket,
                   plan: EntryPlanV2) -> CandidateAssessment:
        """A v3 plan as an assessed candidate; the exit plan must keep TP3 beyond TP2."""
        settings, context = self._deps.settings, tier0.context
        candidate = plan_candidate(plan, bounds_from_packet(packet), context.bar_open_epoch)
        item = assess_candidate(context, candidate, settings,
                                friction_price=cycle_friction(settings, context),
                                tp_r_multiple=candidate.features["reward_r"])
        if item.exit_plan is None:
            return item
        problem = ladder_after_exit(plan, item.exit_plan.tp)
        if problem is not None:
            return replace(item, exit_plan=None,
                           refusal=Refusal((PROBLEM_TP3_TRIMMED,), problem))
        return replace(item, verdict=VERDICT_CHOSEN)

    def _agent_item(self, tier0: Tier0, packet: OperatorPacket,
                    plan: AgentEntryPlan) -> CandidateAssessment:
        """A v2 agent entry as an assessed candidate, judged by the packet's limits."""
        settings, context = self._deps.settings, tier0.context
        candidate = agent_candidate(plan, limits_from_packet(packet), context.bar_open_epoch)
        item = assess_candidate(context, candidate, settings,
                                friction_price=cycle_friction(settings, context),
                                tp_r_multiple=candidate.features["reward_r"])
        if item.exit_plan is None:
            return item
        return replace(item, verdict=VERDICT_CHOSEN)
