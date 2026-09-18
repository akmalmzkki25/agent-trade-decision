"""
The engine's work-in-progress record for one cycle and its final CycleResult.

Every step returns a new `CycleDraft` (frozen, `dataclasses.replace`), so a
failure part-way still has everything gathered so far to record.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Final

from ..cycle_codes import ENTER_STATUSES, CycleStatus, HoldReason
from ..cycle_types import (
    PUBLISHED_STATUS, CalendarAssessment, CalendarEvent, CandidateAssessment, CycleResult,
    CycleTimings, DeskViews, MarketContext, ProtocolDecision, ShadowIntent, ViewRecord,
)
from ..providers.base import MS_PER_SECOND, PROVIDER_STATUS_SKIPPED, RULES_PROVIDER_NAME
from ..risk.gates import RuntimeGateState
from ..schemas.agents import ChiefDecision
from ..schemas.minute import MinuteSnapshot
from ..schemas.operator_parts import PacketKind
from ..schemas.snapshot import ProbeBlock, V6Snapshot
from ..types import TIMEFRAME_SECONDS, ExitPlan, GateResult, Refusal, SizingResult

MAX_HOLD_DETAIL_CHARS: Final[int] = 300
M15_S: Final[int] = TIMEFRAME_SECONDS["M15"]


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
    session_armed: bool = False          # the active session may publish (execute mode)
    minute: MinuteSnapshot | None = None     # an m1 cycle: the minute it decides on

    @property
    def packet_kind(self) -> PacketKind:
        return "m15" if self.minute is None else "m1"

    @property
    def snapshot_id(self) -> str:
        return self.snapshot.snapshot_id if self.minute is None else self.minute.snapshot_id

    @property
    def bar_open_epoch(self) -> int:
        return (self.snapshot.bar_open_epoch if self.minute is None
                else self.minute.bar_open_epoch)

    @property
    def as_of_epoch(self) -> int:
        """The close of the bar this cycle decides on."""
        if self.minute is None:
            return self.snapshot.bar_open_epoch + M15_S
        return self.minute.bar_close_epoch


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
    intent_id: str | None = None

    def update(self, **changes: object) -> "CycleDraft":
        return replace(self, **changes)  # type: ignore[arg-type]

    def finish(self, status: CycleStatus, hold_reason: HoldReason | None, detail: str,
               finished_at: float) -> CycleOutcome:
        request = self.request
        timings = CycleTimings(started_at=self.started_at,
                               finished_at=max(finished_at, self.started_at),
                               tier0_ms=self.tier0_ms, deliberation_ms=self.deliberation_ms)
        result = CycleResult(
            cycle_id=request.cycle_id, snapshot_id=request.snapshot_id,
            bar_open_epoch=request.bar_open_epoch, status=status,
            hold_reason=hold_reason, backend=self.backend, provider=self.provider,
            provider_status=self.provider_status, timings=timings,
            hold_detail=detail[:MAX_HOLD_DETAIL_CHARS], session_id=request.session_id,
            gates=self.gates, candidates=self.candidates, views=self.views,
            view_records=self.view_records, decision=self.decision, protocol=self.protocol,
            exit_plan=self.exit_plan, sizing=self.sizing, refusal=self.refusal,
            shadow_intent=self.shadow_intent if status in ENTER_STATUSES else None,
            intent_id=self.intent_id if status == PUBLISHED_STATUS else None,
        )
        calendar = None if self.context is None else self.context.calendar
        return CycleOutcome(result=result, calendar=calendar, context=self.context)

    def bare(self) -> "CycleDraft":
        """Only the identity, for recording a cycle whose own data broke the record."""
        return CycleDraft(request=self.request, backend=self.backend,
                          started_at=self.started_at, context=self.context)
