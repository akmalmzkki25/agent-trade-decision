"""
Assembly of the Phase 2 runtime parts that live beside the V6 container.

One `RuntimeParts` per app: the cycle ledger, the control plane (sessions and
the pending EA command), the deliberation worker and the watchdog. The
container starts and stops the tasks; this module only builds the objects.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..clock import Clock
from ..config import V6Settings
from ..deliberation.engine import build_engine
from ..ledger_cycles import LedgerCycles
from ..ledger_v6 import LedgerV6
from ..market.bar_store import BarStore
from .breaker_feed import BreakerFeed, MarksReader
from .ea_state import EaState
from .service import DeliberationRuntime
from .sessions import ControlPlane, build_control_plane
from .watchdog import WATCHDOG_INTERVAL_S, Watchdog, WatchdogDeps


@dataclass(frozen=True)
class RuntimeParts:
    ledger_cycles: LedgerCycles
    control: ControlPlane
    breakers: BreakerFeed
    worker: DeliberationRuntime
    watchdog: Watchdog


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


def build_runtime_parts(core: CoreParts, ledger_cycles: LedgerCycles, *,
                        watchdog_interval_s: float = WATCHDOG_INTERVAL_S) -> RuntimeParts:
    control = build_control_plane(ledger=ledger_cycles, control_log=core.ledger_v6,
                                  ea_state=core.ea_state)
    breakers = BreakerFeed(ledger=ledger_cycles, marks=MarksReader(core.ledger_v6.path),
                           settings=core.settings, clock=core.clock)
    engine = build_engine(core.settings, core.clock, core.bar_store, breakers.for_context)
    worker = DeliberationRuntime(
        ea_state=core.ea_state, bar_store=core.bar_store, ledger=ledger_cycles, engine=engine,
        sessions=control.sessions, clock=core.clock, halt_path=core.halt_path,
        is_active=core.is_active)
    watchdog = Watchdog(WatchdogDeps(
        settings=core.settings, clock=core.clock, ea_state=core.ea_state,
        halt_path=core.halt_path, ledger=ledger_cycles, bar_store=core.bar_store,
        breakers=breakers, sessions=control.sessions, commands=control.commands,
        carry=lambda: worker.carry, is_active=core.is_active,
    ), interval_s=watchdog_interval_s)
    return RuntimeParts(ledger_cycles=ledger_cycles, control=control, breakers=breakers,
                        worker=worker, watchdog=watchdog)
