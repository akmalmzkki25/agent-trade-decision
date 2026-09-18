"""
Assembly of the runtime parts that live beside the V6 container.

One `RuntimeParts` per app: the cycle ledger, the control plane (sessions and
the pending EA command), the operator queue, the intent book and the execution
desk that arms and disarms, the deliberation worker and its publisher, the
watchdog, the poll replier, the replay cache for signed EA requests and the V6
basket journal. The container starts and stops the tasks; this module only
builds the objects.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..clock import Clock
from ..config import V6Settings
from ..deliberation.engine import build_engine
from ..ledger_actions import ActionRow
from ..ledger_baskets import BasketJournal
from ..ledger_cycles import LedgerCycles
from ..ledger_intents import IntentRecord
from ..ledger_v6 import LedgerV6
from ..market.bar_store import BarStore
from ..providers.operator_queue import OperatorQueue
from ..wire import ReplayCache
from .breaker_feed import BreakerFeed, DayFacts, MarksReader
from .commands import CommandBoard
from .desk import DeskDeps, ExecutionDesk
from .ea_state import EaState
from .intent_book import IntentBook
from .poll_reply import PollReplier
from .publisher import IntentPublisher
from .service import DeliberationRuntime
from .sessions import ControlPlane, build_control_plane
from .watchdog import WATCHDOG_INTERVAL_S, Watchdog, WatchdogDeps


@dataclass(frozen=True)
class LedgerPlans:
    """The engine's `trade_state.PlanReader`: blocking reads of the cycle ledger."""

    ledger: LedgerCycles

    def intent(self, intent_id: str) -> IntentRecord | None:
        return self.ledger.intents.get(intent_id)

    def by_ticket(self, ticket: int) -> IntentRecord | None:
        return self.ledger.intents.by_ticket(ticket)

    def last_action(self, session_id: str) -> ActionRow | None:
        return self.ledger.actions.latest(session_id)


@dataclass(frozen=True)
class RuntimeParts:
    ledger_cycles: LedgerCycles
    control: ControlPlane
    breakers: BreakerFeed
    worker: DeliberationRuntime
    watchdog: Watchdog
    operator_queue: OperatorQueue
    intent_book: IntentBook
    desk: ExecutionDesk
    poll_replier: PollReplier
    replay_cache: ReplayCache
    baskets: BasketJournal


@dataclass(frozen=True)
class CoreParts:
    """What the container already owns and the runtime reads."""

    settings: V6Settings
    clock: Clock
    ledger_v6: LedgerV6
    bar_store: BarStore
    ea_state: EaState
    halt_path: Path
    is_active: Callable[[], bool]


def _desk(core: CoreParts, ledger_cycles: LedgerCycles, breakers: BreakerFeed,
          queue: OperatorQueue, day: Callable[[], DayFacts | None]) -> ExecutionDesk:
    return ExecutionDesk(DeskDeps(
        settings=core.settings, clock=core.clock, ledger=ledger_cycles,
        book=IntentBook(ledger_cycles.intents), commands=CommandBoard(),
        ea_state=core.ea_state, halt_path=core.halt_path, breakers=breakers,
        day=day, queue=queue))


def build_runtime_parts(core: CoreParts, ledger_cycles: LedgerCycles, *,
                        watchdog_interval_s: float = WATCHDOG_INTERVAL_S) -> RuntimeParts:
    settings, clock = core.settings, core.clock
    breakers = BreakerFeed(ledger=ledger_cycles, marks=MarksReader(core.ledger_v6.path),
                           settings=settings, clock=clock)
    queue = OperatorQueue(settings=settings, clock=clock)

    def newest_day() -> DayFacts | None:
        """The worker's newest day facts; only called once the worker below exists."""
        return worker.carry.day

    desk = _desk(core, ledger_cycles, breakers, queue, newest_day)
    control = build_control_plane(ledger=ledger_cycles, control_log=core.ledger_v6,
                                  ea_state=core.ea_state, commands=desk.deps.commands,
                                  desk=desk)
    engine = build_engine(settings, clock, core.bar_store, breakers.for_context,
                          operator=queue, publisher=IntentPublisher(desk),
                          plans=LedgerPlans(ledger_cycles))
    worker = DeliberationRuntime(
        ea_state=core.ea_state, bar_store=core.bar_store, ledger=ledger_cycles, engine=engine,
        sessions=control.sessions, clock=clock, halt_path=core.halt_path,
        is_active=core.is_active)
    watchdog = Watchdog(WatchdogDeps(
        settings=settings, clock=clock, ea_state=core.ea_state,
        halt_path=core.halt_path, ledger=ledger_cycles, bar_store=core.bar_store,
        breakers=breakers, sessions=control.sessions, commands=control.commands,
        carry=lambda: worker.carry, is_active=core.is_active, desk=desk,
    ), interval_s=watchdog_interval_s)
    return RuntimeParts(
        ledger_cycles=ledger_cycles, control=control, breakers=breakers, worker=worker,
        watchdog=watchdog, operator_queue=queue, intent_book=desk.deps.book, desk=desk,
        poll_replier=PollReplier(desk), replay_cache=ReplayCache(),
        baskets=BasketJournal(core.ledger_v6.path, clock))
