"""
The engine's work-in-progress record for one cycle and its final CycleResult.

Every step returns a new `CycleDraft` (frozen, `dataclasses.replace`), so a
failure part-way still has everything gathered so far to record.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Final

from ..cycle_codes import CycleStatus, HoldReason
from ..cycle_types import (
    CalendarAssessment, CalendarEvent, CandidateAssessment, CycleResult, CycleTimings,
    DeskViews, MarketContext, ProtocolDecision, ShadowIntent, ViewRecord,
)
from ..providers.base import MS_PER_SECOND, PROVIDER_STATUS_SKIPPED, RULES_PROVIDER_NAME
from ..risk.gates import RuntimeGateState
from ..schemas.agents import ChiefDecision
from ..schemas.snapshot import ProbeBlock, V6Snapshot
from ..types import ExitPlan, GateResult, Refusal, SizingResult

MAX_HOLD_DETAIL_CHARS: Final[int] = 300


@dataclass(frozen=True)
class CycleRequest:
    """One cycle to run: the snapshot plus what only the runtime knows."""

    cycle_id: str
    snapshot: V6Snapshot
    received_at: float
    runtime: RuntimeGateState
    session_id: str | None = None
    carried_events: tuple[CalendarEvent, ...] = ()
    probe: ProbeBlock | None = None


@dataclass(frozen=True)
class CycleOutcome:
    """The recorded result plus what the runtime carries into the next cycle."""

    result: CycleResult
    calendar: CalendarAssessment | None = None
    context: MarketContext | None = None


def elapsed_ms(start: float, end: float) -> int:
    return max(0, int((end - start) * MS_PER_SECOND))


@dataclass(frozen=True)
class CycleDraft:
    request: CycleRequest
    backend: str
    started_at: float
    provider: str = RULES_PROVIDER_NAME
    provider_status: str = PROVIDER_STATUS_SKIPPED
    tier0_ms: int = 0
    deliberation_ms: int = 0
    context: MarketContext | None = field(default=None, repr=False)
    gates: tuple[GateResult, ...] = ()
    candidates: tuple[CandidateAssessment, ...] = ()
    views: DeskViews = DeskViews()
    view_records: tuple[ViewRecord, ...] = ()
    decision: ChiefDecision | None = None
    protocol: ProtocolDecision | None = None
    exit_plan: ExitPlan | None = None
    sizing: SizingResult | None = None
    refusal: Refusal | None = None
    shadow_intent: ShadowIntent | None = None

    def update(self, **changes: object) -> "CycleDraft":
        return replace(self, **changes)  # type: ignore[arg-type]

    def finish(self, status: CycleStatus, hold_reason: HoldReason | None, detail: str,
               finished_at: float) -> CycleOutcome:
        request = self.request
        timings = CycleTimings(started_at=self.started_at,
                               finished_at=max(finished_at, self.started_at),
                               tier0_ms=self.tier0_ms, deliberation_ms=self.deliberation_ms)
        result = CycleResult(
            cycle_id=request.cycle_id, snapshot_id=request.snapshot.snapshot_id,
            bar_open_epoch=request.snapshot.bar_open_epoch, status=status,
            hold_reason=hold_reason, backend=self.backend, provider=self.provider,
            provider_status=self.provider_status, timings=timings,
            hold_detail=detail[:MAX_HOLD_DETAIL_CHARS], session_id=request.session_id,
            gates=self.gates, candidates=self.candidates, views=self.views,
            view_records=self.view_records, decision=self.decision, protocol=self.protocol,
            exit_plan=self.exit_plan, sizing=self.sizing, refusal=self.refusal,
            shadow_intent=self.shadow_intent if status == "ENTER_SHADOW" else None,
        )
        calendar = None if self.context is None else self.context.calendar
        return CycleOutcome(result=result, calendar=calendar, context=self.context)

    def bare(self) -> "CycleDraft":
        """Only the identity, for recording a cycle whose own data broke the record."""
        return CycleDraft(request=self.request, backend=self.backend,
                          started_at=self.started_at, context=self.context)
