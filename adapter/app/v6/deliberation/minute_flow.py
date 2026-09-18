"""
The m1 part of a cycle (spec sections 1, 2.5 and 4.2; mixed into `engine.DeliberationEngine`).

An m1 cycle starts from the newest M15 cycle (`MinuteBase`): its context with the minute
swapped in (`minute_context`), the calendar assessed again at the minute close, and every
hard gate evaluated anew (a minute snapshot is stale after V6_MINUTE_STALE_S). A flat
minute that fails a gate, or a managed minute that fails a management-blocking gate,
serves no packet: the run is skipped with the failed gate codes. Otherwise the ordinary
operator path serves an m1 packet whose views are the M15 cycle's (the accepted
decision's, or that cycle's rules views), waits until the minute close +
V6_M1_DEADLINE_S, and enters, manages or holds exactly like an m15 cycle.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from ..cycle_codes import hold_reason_for_gates
from ..cycle_types import CalendarEvent, DeliberationInput, DeskViews, MarketContext
from ..market.calendar import assess_snapshot_calendar
from ..market.features import bars_closed_by
from ..risk.gates import evaluate_gates, failed_codes
from ..schemas.operator_plan import PacketState
from ..schemas.snapshot import ProbeBlock, V6Snapshot
from ..types import TIMEFRAME_SECONDS, Bar
from .candidates import CandidatePool
from .context_builder import calendar_horizon_s
from .cycle_draft import CycleDraft, CycleOutcome, CycleRequest, elapsed_ms
from .minute_context import minute_context, minute_settings
from .minute_packet import MINUTE_PACKET_M1_BARS
from .panel import Baseline
from .trade_state import trade_state, wants_management

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .engine import EngineDeps, Tier0

logger = logging.getLogger(__name__)

M1_S: Final[int] = TIMEFRAME_SECONDS["M1"]
SKIP_GATES: Final[str] = "GATES"
SKIP_NO_OPERATOR: Final[str] = "NO_OPERATOR"
SKIP_ERROR: Final[str] = "ERROR"


@dataclass(frozen=True)
class MinuteBase:
    """The newest M15 cycle an m1 cycle starts from."""

    snapshot: V6Snapshot
    context: MarketContext
    views: DeskViews
    events: tuple[CalendarEvent, ...] = ()
    probe: ProbeBlock | None = None


@dataclass(frozen=True)
class MinuteRun:
    """What an m1 cycle did: `outcome` when a packet was served, else why not."""

    state: PacketState
    tier0_ms: int = 0
    outcome: CycleOutcome | None = None
    skipped: str = ""


def _merged(stored: tuple[Bar, ...], fresh: Bar) -> tuple[Bar, ...]:
    by_time = {bar.t: bar for bar in stored}
    by_time[fresh.t] = fresh
    return tuple(by_time[t] for t in sorted(by_time))


def skip_reason(tier0: "Tier0", state: PacketState) -> str:
    """"" when a packet is due; else the failed gates (a managed trade is skipped only on
    a management-blocking gate, exactly like an m15 cycle)."""
    if hold_reason_for_gates(tier0.gates) is None:
        return ""
    if state != "flat" and wants_management(tier0.context, tier0.gates):
        return ""
    return f"{SKIP_GATES}:" + ",".join(failed_codes(tier0.gates))


class MinuteFlow:
    """Mixed into DeliberationEngine, whose `_deps`, `_now`, `_breaker_status` and
    `_operator_path` it uses."""

    _deps: "EngineDeps"

    async def run_minute(self, request: CycleRequest, base: MinuteBase) -> MinuteRun:
        """One m1 cycle; never raises (CancelledError aside)."""
        started = self._now()
        try:
            tier0 = await self._minute_tier0(request, base)
        except Exception as exc:  # noqa: BLE001 - a failed minute is skipped and logged
            logger.error("v6 minute %s failed in tier 0: %s", request.cycle_id,
                         type(exc).__name__, exc_info=True)
            return MinuteRun(state="flat", skipped=f"{SKIP_ERROR}:{type(exc).__name__}")
        state = trade_state(tier0.context)
        tier0_ms = elapsed_ms(started, self._now())
        skipped = skip_reason(tier0, state)
        queue = self._deps.operator
        if skipped or queue is None:
            return MinuteRun(state=state, tier0_ms=tier0_ms,
                             skipped=skipped or SKIP_NO_OPERATOR)
        draft = CycleDraft(request=request, backend=self._deps.settings.backend,
                           started_at=started).update(
            context=tier0.context, gates=tier0.gates, views=base.views, tier0_ms=tier0_ms)
        outcome = await self._operator_path(queue, draft, tier0, state)
        return MinuteRun(state=state, tier0_ms=tier0_ms, outcome=outcome)

    async def _minute_tier0(self, request: CycleRequest, base: MinuteBase) -> "Tier0":
        from .engine import Tier0  # the engine module imports this one at load time
        deps, minute = self._deps, request.minute
        if minute is None:
            raise ValueError("an m1 cycle needs its minute snapshot")
        settings, close = deps.settings, minute.bar_close_epoch
        stored = await asyncio.to_thread(deps.bars.latest, "M1", MINUTE_PACKET_M1_BARS + 1)
        bars = bars_closed_by(_merged(stored, minute.to_bar()), close, M1_S)
        calendar = assess_snapshot_calendar(
            base.snapshot, now_epoch=int(self._now()), carried_events=base.events,
            probe=base.probe, horizon_s=calendar_horizon_s(settings), decision_epoch=close)
        context = minute_context(base.context, minute, bars[-MINUTE_PACKET_M1_BARS:],
                                 cycle_id=request.cycle_id, received_at=request.received_at,
                                 calendar=calendar, settings=settings)
        breakers = await self._breaker_status(context)
        gates = evaluate_gates(context, calendar, request.runtime, minute_settings(settings),
                               breakers, self._now())
        return Tier0(context=context, gates=gates, breakers=breakers, pool=CandidatePool(),
                     inputs=DeliberationInput(context=context, gates=gates, offered=()),
                     baseline=Baseline(views=base.views, records=()))
