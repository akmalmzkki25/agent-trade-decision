"""
V6 composition root: one container per FastAPI app, plus its lifespan.

`create_app` builds the container only when V6 is enabled, so a disabled V6
never opens its database. The lifespan then turns the runtime on: it takes the
single-instance lock, hydrates the bar cache and keeps the lock alive with a
heartbeat. Until that succeeds, and again after the lock is lost, every
`/v6/*` route answers 404 through `require_active_container`.

Phase 2 adds the runtime parts (`runtime.wiring`): the cycle ledger, the control
plane, the deliberation worker and the watchdog. `start_runtime` starts the
worker and the watchdog beside the heartbeat, and only once the lock is held, so
a second process on the same database never runs a worker.

A container serves one app lifespan: shutdown closes its ledger connections.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from secrets import token_hex
from typing import Final

from fastapi import FastAPI, HTTPException, Request

from .clock import Clock, SystemClock
from .config import ADAPTER_DIR, V6Settings, load_v6_settings
from .ledger_cycles import LedgerCycles
from .ledger_v6 import LedgerV6
from .market.bar_store import BarStore
from .runtime.ea_state import EaState
from .runtime.watchdog import WATCHDOG_INTERVAL_S
from .runtime.wiring import CoreParts, RuntimeParts, build_runtime_parts

logger = logging.getLogger(__name__)

APP_STATE_KEY: Final[str] = "v6_container"
DISABLED_DETAIL: Final[str] = "V6 disabled"
HEARTBEAT_INTERVAL_S: Final[float] = 10.0
# Three missed heartbeats before another process may take the runtime over.
LOCK_STALE_AFTER_S: Final[float] = 3 * HEARTBEAT_INTERVAL_S
CONTROL_ACTOR: Final[str] = "runtime"
MAX_HOLDER_CHARS: Final[int] = 64
MAX_HOST_CHARS: Final[int] = 24
HOLDER_NONCE_BYTES: Final[int] = 6

Sleep = Callable[[float], Awaitable[None]]


def _new_holder() -> str:
    """Unique per container, so two apps in one process still exclude each other."""
    host = socket.gethostname()[:MAX_HOST_CHARS] or "host"
    return f"{host}:{os.getpid()}:{token_hex(HOLDER_NONCE_BYTES)}"[:MAX_HOLDER_CHARS]


def _resolve_halt_path(halt_file: str) -> Path:
    path = Path(halt_file)
    return path if path.is_absolute() else ADAPTER_DIR / path


class RuntimeSwitch:
    """Whether this process currently acts as the V6 runtime.

    Flipped only on the event loop thread (lifespan and heartbeat), so a plain
    attribute is enough. The task handles (heartbeat, worker, watchdog) are kept
    after a switch-off so shutdown can still cancel and await them.
    """

    def __init__(self) -> None:
        self._active = False
        self._tasks: tuple[asyncio.Task[None], ...] = ()

    @property
    def active(self) -> bool:
        return self._active

    def turn_on(self, *tasks: asyncio.Task[None]) -> None:
        self._tasks = tuple(tasks)
        self._active = True

    def turn_off(self) -> None:
        self._active = False

    def take_tasks(self) -> tuple[asyncio.Task[None], ...]:
        tasks, self._tasks = self._tasks, ()
        return tasks


@dataclass(frozen=True)
class V6Container:
    settings: V6Settings
    clock: Clock
    ledger_v6: LedgerV6
    bar_store: BarStore
    ea_state: EaState
    holder: str
    halt_path: Path
    switch: RuntimeSwitch
    parts: RuntimeParts
    heartbeat_interval_s: float = HEARTBEAT_INTERVAL_S
    run_tasks: bool = True

    @property
    def active(self) -> bool:
        return self.switch.active

    @property
    def ledger_cycles(self) -> LedgerCycles:
        return self.parts.ledger_cycles

    def close(self) -> None:
        try:
            self.parts.ledger_cycles.close()
        finally:
            self.ledger_v6.close()


def _parts(core: CoreParts, db_path: str | PathLike[str],
           watchdog_interval_s: float) -> RuntimeParts:
    cycles = LedgerCycles(db_path)
    try:
        return build_runtime_parts(core, cycles, watchdog_interval_s=watchdog_interval_s)
    except Exception:
        cycles.close()
        raise


def build_container(
    v6_settings: V6Settings,
    db_path: str | PathLike[str],
    clock: Clock | None = None,
    *,
    heartbeat_interval_s: float = HEARTBEAT_INTERVAL_S,
    run_tasks: bool = True,
    watchdog_interval_s: float = WATCHDOG_INTERVAL_S,
) -> V6Container:
    """`run_tasks=False` keeps the worker and the watchdog off (data-plane tests)."""
    if not 0 < heartbeat_interval_s < LOCK_STALE_AFTER_S:
        raise ValueError(f"heartbeat interval must be in (0, {LOCK_STALE_AFTER_S}) seconds")
    resolved_clock: Clock = clock if clock is not None else SystemClock()
    ledger = LedgerV6(db_path, clock=resolved_clock)
    switch, ea_state, bar_store = RuntimeSwitch(), EaState(), BarStore(ledger)
    halt_path = _resolve_halt_path(v6_settings.halt_file)
    core = CoreParts(settings=v6_settings, clock=resolved_clock, ledger_v6=ledger,
                     bar_store=bar_store, ea_state=ea_state, halt_path=halt_path,
                     is_active=lambda: switch.active)
    try:
        parts = _parts(core, db_path, watchdog_interval_s)
    except Exception:
        ledger.close()
        raise
    return V6Container(
        settings=v6_settings, clock=resolved_clock, ledger_v6=ledger, bar_store=bar_store,
        ea_state=ea_state, holder=_new_holder(), halt_path=halt_path, switch=switch,
        parts=parts, heartbeat_interval_s=heartbeat_interval_s, run_tasks=run_tasks,
    )


def container_for_app(
    v6_settings: V6Settings | None, db_path: str | PathLike[str], clock: Clock | None,
    *, run_tasks: bool = True,
) -> V6Container | None:
    """None when V6 is disabled or its mode is `off`; invalid V6 configuration raises at startup."""
    resolved = v6_settings if v6_settings is not None else load_v6_settings()
    if not resolved.enabled or resolved.mode == "off":
        return None
    return build_container(resolved, db_path, clock, run_tasks=run_tasks)


def require_active_container(request: Request) -> V6Container:
    """FastAPI dependency: the app's container, or 404 while V6 is not running here."""
    container = getattr(request.app.state, APP_STATE_KEY, None)
    if container is None or not container.active:
        raise HTTPException(status_code=404, detail=DISABLED_DETAIL)
    return container


# --- runtime lifecycle -------------------------------------------------------
async def _audit(container: V6Container, action: str) -> None:
    detail = {"holder": container.holder, "pid": os.getpid(),
              "mode": container.settings.mode, "backend": container.settings.backend}
    try:
        await asyncio.to_thread(container.ledger_v6.log_control, CONTROL_ACTOR, action, detail)
    except sqlite3.Error:
        logger.exception("v6 control log write failed for %s", action)


async def _acquire(container: V6Container) -> bool:
    ledger = container.ledger_v6
    acquired = await asyncio.to_thread(
        ledger.acquire_lock, container.holder, os.getpid(),
        container.clock.now_epoch(), LOCK_STALE_AFTER_S)
    if not acquired:
        current = await asyncio.to_thread(ledger.read_lock)
        owner = "unknown" if current is None else f"{current.holder} (pid {current.pid})"
        logger.error("v6 runtime lock is held by %s; V6 stays disabled in this process", owner)
    return acquired


async def _release(container: V6Container) -> None:
    try:
        released = await asyncio.to_thread(container.ledger_v6.release_lock, container.holder)
    except sqlite3.Error:
        logger.exception("v6 runtime lock release failed; it expires after %.0f s",
                         LOCK_STALE_AFTER_S)
        return
    if not released:
        logger.warning("v6 runtime lock was no longer held by %s at shutdown", container.holder)


def _runtime_tasks(container: V6Container) -> tuple[asyncio.Task[None], ...]:
    heartbeat = asyncio.create_task(heartbeat_loop(container), name="v6-heartbeat")
    if not container.run_tasks:
        return (heartbeat,)
    parts = container.parts
    return (heartbeat,
            asyncio.create_task(parts.worker.run_forever(), name="v6-worker"),
            asyncio.create_task(parts.watchdog.run_forever(), name="v6-watchdog"))


async def start_runtime(container: V6Container) -> bool:
    """Take the lock, hydrate the bar cache, start heartbeat, worker and watchdog.

    False leaves V6 off in this process (no task is started).
    """
    try:
        if not await _acquire(container):
            return False
    except sqlite3.Error:
        logger.exception("v6 runtime lock could not be taken; V6 stays disabled")
        return False
    try:
        counts = await asyncio.to_thread(container.bar_store.warm)
    except sqlite3.Error:
        logger.exception("v6 bar cache hydration failed; V6 stays disabled")
        await _release(container)
        return False
    container.switch.turn_on(*_runtime_tasks(container))
    await _audit(container, "runtime_start")
    logger.info("v6 runtime started (holder=%s mode=%s backend=%s cached_bars=%s tasks=%s)",
                container.holder, container.settings.mode, container.settings.backend,
                dict(counts), container.run_tasks)
    return True


async def stop_runtime(container: V6Container) -> None:
    container.switch.turn_off()
    tasks = container.switch.take_tasks()
    for task in tasks:
        task.cancel()
    if tasks:
        # wait() never raises, so a cancellation aimed at shutdown itself still propagates.
        await asyncio.wait(set(tasks))
    await _audit(container, "runtime_stop")
    await _release(container)
    logger.info("v6 runtime stopped (holder=%s)", container.holder)


async def heartbeat_once(container: V6Container) -> bool:
    """Refresh the lock; False means it was lost and V6 is now off in this process."""
    try:
        held = await asyncio.to_thread(
            container.ledger_v6.heartbeat_lock, container.holder, container.clock.now_epoch())
    except sqlite3.Error as exc:
        # Transient (e.g. busy): the next beat retries; staleness decides takeover.
        logger.warning("v6 heartbeat write failed, retrying: %s", exc)
        return True
    if not held:
        container.switch.turn_off()
        logger.error("v6 runtime lock lost by %s; V6 disabled in this process",
                     container.holder)
    return held


async def heartbeat_loop(container: V6Container, sleep: Sleep = asyncio.sleep) -> None:
    while True:
        await sleep(container.heartbeat_interval_s)
        if not await heartbeat_once(container):
            return


@asynccontextmanager
async def v6_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run the V6 runtime for the app's lifetime; a no-op when V6 is disabled."""
    container: V6Container | None = getattr(app.state, APP_STATE_KEY, None)
    if container is None:
        yield
        return
    started = await start_runtime(container)
    try:
        yield
    finally:
        if started:
            await stop_runtime(container)
        await asyncio.to_thread(container.close)
