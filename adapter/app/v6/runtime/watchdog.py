"""
The V6 watchdog (plan section 4): a short periodic check while the runtime runs.

Every WATCHDOG_INTERVAL_S it
- classifies the runtime (HALTED / BREAKER / STALE / WAITING_EA / RUNNING) from the
  halt file, the breakers and the EA and snapshot ages, logging each transition;
- evaluates the drawdown breakers from the newest EA poll, the account marks and
  the realised V6 results, persisting new trips and queueing FLATTEN for the EA
  when one trips (plan section 6: HALTED + FLATTEN + CANCEL_PENDING);
- closes a daily session that reached the rollover block or outlived its day, and
  reopens it once a start is allowed again when V6_SESSION_AUTO_RENEW is on;
- lets the execution desk expire undelivered intents and disarm an armed session
  whose arming checks fail (halt, breaker, stale or non-DEMO EA; never re-arms);
- and, every LABEL_INTERVAL_S, labels the candidates whose barrier has resolved.

Each step is isolated: a failing step is logged (with its traceback when a
failure streak starts) and counted, never fatal. A breaker evaluation that keeps
failing fails closed after BREAKER_FAIL_CLOSED_AFTER ticks instead of freezing
the last good summary. The sleep function and the clock are injectable for tests.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final, Protocol, TypeVar

from ..clock import Clock
from ..config import V6Settings
from ..ledger_cycles import LedgerCycles
from ..ledger_cycles_schema import BreakerRecord
from ..learning.label_job import label_pending
from ..learning.labeler import LabelerConfig
from ..market.bar_store import BarStore
from ..risk.breakers import BREAKER_UNAVAILABLE, BreakerStatus
from .arming import BreakerView
from .breaker_feed import BreakerFeed
from .ea_state import EaState, EaStateView
from .service import CarryOver
from .sessions import CommandBoard, SessionService
from .status import (  # noqa: F401 - SNAPSHOT_STALE_AFTER_S re-exported
    SNAPSHOT_STALE_AFTER_S, STATUS_RUNNING, classify, snapshot_stale,
)

logger = logging.getLogger(__name__)

WATCHDOG_INTERVAL_S: Final[float] = 5.0
LABEL_INTERVAL_S: Final[float] = 60.0
STATUS_STARTING: Final[str] = "STARTING"
BREAKER_TRIP_REASON: Final[str] = "breaker_trip"
STEP_HALT: Final[str] = "halt"
STEP_BREAKERS: Final[str] = "breakers"
STEP_SESSIONS: Final[str] = "sessions"
STEP_RENEWAL: Final[str] = "renewal"
STEP_SUPERVISE: Final[str] = "supervise"
STEP_LABELER: Final[str] = "labeler"
# Three ticks (15 s) of failed breaker evaluation: stop trusting the last summary.
BREAKER_FAIL_CLOSED_AFTER: Final[int] = 3
# Within a failure streak, repeat the traceback once every this many failures (5 min).
TRACEBACK_EVERY: Final[int] = 60
MAX_ERROR_CHARS: Final[int] = 200
BREAKER_EVALUATION_UNAVAILABLE: Final[str] = "breaker evaluation unavailable"

Sleep = Callable[[float], Awaitable[None]]
StepT = TypeVar("StepT")


@dataclass(frozen=True)
class BreakerSummary:
    tripped: bool = False
    labels: tuple[str, ...] = ()
    detail: str = ""
    active_keys: frozenset[str] = frozenset()

    @classmethod
    def from_status(cls, status: BreakerStatus) -> "BreakerSummary":
        return cls(tripped=status.tripped, labels=status.labels, detail=status.detail,
                   active_keys=_keys(status.active))

    def unavailable(self, failures: int) -> "BreakerSummary":
        """Fail closed on top of the last good summary (its persisted trips are kept)."""
        detail = f"{BREAKER_EVALUATION_UNAVAILABLE} ({failures} consecutive failures)"
        return BreakerSummary(
            tripped=True, labels=tuple(dict.fromkeys((*self.labels, BREAKER_UNAVAILABLE))),
            detail="; ".join(part for part in (self.detail, detail) if part),
            active_keys=self.active_keys)

    @classmethod
    def from_active(cls, active: tuple[BreakerRecord, ...]) -> "BreakerSummary":
        """Persisted trips only (no poll to evaluate yet)."""
        keys = _keys(active)
        detail = "; ".join(f"{r.scope}:{r.period_key} tripped ({r.reason})" for r in active)
        return cls(tripped=bool(active), labels=tuple(sorted(keys)), detail=detail,
                   active_keys=keys)


def _keys(active: tuple[BreakerRecord, ...]) -> frozenset[str]:
    return frozenset(f"{record.scope}:{record.period_key}" for record in active)


@dataclass(frozen=True)
class WatchdogState:
    status: str = STATUS_STARTING
    checked_at: float | None = None
    halted: bool = False
    ea_age_s: float | None = None
    ea_stale: bool = True
    snapshot_stale: bool = False
    breaker: BreakerSummary = BreakerSummary()
    last_label_at: float | None = None
    labels_written: int = 0
    ticks: int = 0
    errors: int = 0

    @property
    def breakers_tripped(self) -> bool:
        return self.breaker.tripped

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["breaker"] = {"tripped": self.breaker.tripped, "labels": list(self.breaker.labels),
                           "detail": self.breaker.detail}
        return data


def next_streaks(previous: Mapping[str, int], ran: Iterable[str],
                 failed: Iterable[str]) -> Mapping[str, int]:
    """Consecutive failures per step; a step that did not run keeps its count."""
    failed_now = frozenset(failed)
    streaks = dict(previous)
    for name in ran:
        streaks[name] = streaks.get(name, 0) + 1 if name in failed_now else 0
    return MappingProxyType(streaks)


class Supervisor(Protocol):
    """The execution desk's watchdog hook (`runtime.desk.ExecutionDesk.supervise`)."""

    async def supervise(self, now: float, *, halted: bool,
                        breakers: BreakerView | None) -> object: ...


@dataclass(frozen=True)
class WatchdogDeps:
    settings: V6Settings
    clock: Clock
    ea_state: EaState
    halt_path: Path
    ledger: LedgerCycles
    bar_store: BarStore
    breakers: BreakerFeed
    sessions: SessionService
    commands: CommandBoard
    carry: Callable[[], CarryOver]
    is_active: Callable[[], bool]
    desk: Supervisor | None = None


class Watchdog:
    def __init__(self, deps: WatchdogDeps, *, interval_s: float = WATCHDOG_INTERVAL_S,
                 label_interval_s: float = LABEL_INTERVAL_S,
                 sleep: Sleep = asyncio.sleep) -> None:
        if not (interval_s > 0 and label_interval_s > 0):
            raise ValueError("watchdog intervals must be positive")
        self._deps = deps
        self._interval_s = interval_s
        self._label_interval_s = label_interval_s
        self._sleep = sleep
        self._state = WatchdogState()
        self._streaks: Mapping[str, int] = MappingProxyType({})
        self._good_breaker = BreakerSummary()

    @property
    def state(self) -> WatchdogState:
        return self._state

    @property
    def deps(self) -> WatchdogDeps:
        return self._deps

    async def run_forever(self) -> None:
        while True:
            await self._sleep(self._interval_s)
            if self._deps.is_active():
                await self.tick()

    @property
    def streaks(self) -> Mapping[str, int]:
        return self._streaks

    async def tick(self) -> WatchdogState:
        deps, previous = self._deps, self._state
        now = deps.clock.now_epoch()
        view = deps.ea_state.view()
        errors: list[str] = []
        halted = await self._step(STEP_HALT, self._halted, True, errors)
        breaker = await self._step(STEP_BREAKERS, lambda: self._breakers(view, now),
                                   self._good_breaker, errors)
        self._streaks = next_streaks(self._streaks, (STEP_HALT, STEP_BREAKERS), errors)
        breaker = self._trusted(breaker)
        later = [STEP_SESSIONS, STEP_RENEWAL]
        await self._step(STEP_SESSIONS, lambda: deps.sessions.auto_close_if_rollover(now),
                         None, errors)
        await self._step(STEP_RENEWAL, lambda: deps.sessions.renew_if_due(now), None, errors)
        desk = deps.desk
        if desk is not None:
            later.append(STEP_SUPERVISE)
            await self._step(STEP_SUPERVISE, lambda: desk.supervise(
                now, halted=halted, breakers=breaker), None, errors)
        written: int | None = None
        if self._label_due(now):
            later.append(STEP_LABELER)
            written = await self._step(STEP_LABELER, lambda: self._label(now), 0, errors)
        self._streaks = next_streaks(self._streaks, later, errors)
        state = self._compose(previous, now, view, halted, breaker, written, len(errors))
        self._log_transition(previous, state)
        self._state = state
        return state

    # --- steps -------------------------------------------------------------------------
    async def _step(self, name: str, step: Callable[[], Awaitable[StepT]], default: StepT,
                    errors: list[str]) -> StepT:
        try:
            return await step()
        except Exception as exc:  # noqa: BLE001 - one failing check must not stop the rest
            errors.append(name)
            failures = self._streaks.get(name, 0) + 1
            logger.error("v6 watchdog step %s failed (%d in a row): %s: %s", name, failures,
                         type(exc).__name__, str(exc)[:MAX_ERROR_CHARS],
                         exc_info=(failures - 1) % TRACEBACK_EVERY == 0)
            return default

    def _trusted(self, breaker: BreakerSummary) -> BreakerSummary:
        """The summary to act on: the last good one briefly, then fail closed."""
        failures = self._streaks.get(STEP_BREAKERS, 0)
        if failures == 0:
            self._good_breaker = breaker
            return breaker
        if failures < BREAKER_FAIL_CLOSED_AFTER:
            return breaker
        return breaker.unavailable(failures)

    async def _halted(self) -> bool:
        return await asyncio.to_thread(self._deps.halt_path.exists)

    async def _breakers(self, view: EaStateView, now: float) -> BreakerSummary:
        deps = self._deps
        status: BreakerStatus | None = None
        if view.last_poll is not None:
            status = await deps.breakers.for_poll(view.last_poll.poll, now, deps.carry().day)
        if status is None:
            summary = BreakerSummary.from_active(
                await asyncio.to_thread(deps.ledger.active_breakers))
        else:
            summary = BreakerSummary.from_status(status)
        self._react_to_trips(summary, now)
        return summary

    def _react_to_trips(self, summary: BreakerSummary, now: float) -> None:
        new = summary.active_keys - self._state.breaker.active_keys
        if not new:
            return
        self._deps.commands.request_flatten(BREAKER_TRIP_REASON, now)
        logger.warning("v6 breaker tripped (%s): no new entries until a manual reset; "
                       "FLATTEN queued", ", ".join(sorted(new)))

    def _label_due(self, now: float) -> bool:
        last = self._state.last_label_at
        return last is None or now - last >= self._label_interval_s

    async def _label(self, now: float) -> int:
        deps = self._deps
        config = LabelerConfig.from_settings(deps.settings, deps.carry().point)
        run = await label_pending(deps.ledger, deps.bar_store, now, deps.clock, config=config)
        if run.error:
            raise RuntimeError(run.error)
        return run.written

    # --- state ---------------------------------------------------------------------------
    def _compose(self, previous: WatchdogState, now: float, view: EaStateView, halted: bool,
                 breaker: BreakerSummary, written: int | None, errors: int) -> WatchdogState:
        settings = self._deps.settings
        ea_age = view.ea_age_s(now)
        stale_snapshot = snapshot_stale(view, now, quote_gap=settings.quote_gap)
        status = classify(active=self._deps.is_active(), halted=halted,
                          breaker_tripped=breaker.tripped, ea_age_s=ea_age,
                          ea_stale_s=settings.ea_stale_s, stale_snapshot=stale_snapshot)
        return WatchdogState(
            status=status, checked_at=now, halted=halted, ea_age_s=ea_age,
            ea_stale=ea_age is None or ea_age > settings.ea_stale_s,
            snapshot_stale=stale_snapshot, breaker=breaker,
            last_label_at=now if written is not None else previous.last_label_at,
            labels_written=previous.labels_written + (written or 0),
            ticks=previous.ticks + 1, errors=previous.errors + errors)

    @staticmethod
    def _log_transition(previous: WatchdogState, current: WatchdogState) -> None:
        if previous.status == current.status:
            return
        level = logging.INFO if current.status == STATUS_RUNNING else logging.WARNING
        logger.log(level, "v6 runtime %s -> %s (halted=%s breaker=%s ea_age=%s)",
                   previous.status, current.status, current.halted,
                   ",".join(current.breaker.labels) or "-",
                   "n/a" if current.ea_age_s is None else f"{current.ea_age_s:.1f}s")
