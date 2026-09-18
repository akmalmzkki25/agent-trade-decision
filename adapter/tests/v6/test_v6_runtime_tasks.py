"""Container wiring of the Phase 2 runtime: parts, task lifecycle and failure cleanup."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.main import allowed_hosts
from app.settings import settings as adapter_settings
from app.v6 import container as container_module
from app.v6.container import build_container, start_runtime, stop_runtime

from . import engine_fixtures_v6 as ef


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _names(container: Any) -> list[str]:
    tasks = container.switch.take_tasks()
    names = sorted(task.get_name() for task in tasks)
    container.switch.turn_on(*tasks)
    return names


@pytest.mark.anyio
async def test_the_runtime_starts_and_stops_worker_and_watchdog(tmp_path: Path) -> None:
    container = build_container(ef.settings(halt_file=str(tmp_path / "H")),
                                tmp_path / "v6.db", ef.clock_at())
    try:
        assert container.ledger_cycles is container.parts.ledger_cycles
        assert await start_runtime(container)
        assert _names(container) == ["v6-heartbeat", "v6-minutes", "v6-watchdog",
                                     "v6-worker"]
        for _ in range(100):
            if container.parts.worker.stats.running and container.parts.minutes.stats.running:
                break
            await asyncio.sleep(0.01)
        assert container.parts.worker.stats.running
        assert container.parts.minutes.stats.running
        await stop_runtime(container)
        assert container.switch.take_tasks() == ()
        assert container.parts.worker.stats.running is False
        assert container.parts.minutes.stats.running is False
    finally:
        container.close()


@pytest.mark.anyio
async def test_data_plane_containers_run_only_the_heartbeat(tmp_path: Path) -> None:
    container = build_container(ef.settings(), tmp_path / "v6.db", ef.clock_at(),
                                run_tasks=False)
    try:
        assert await start_runtime(container)
        assert _names(container) == ["v6-heartbeat"]
        await stop_runtime(container)
    finally:
        container.close()


@pytest.mark.anyio
async def test_stopping_a_runtime_that_never_started_is_harmless(tmp_path: Path) -> None:
    container = build_container(ef.settings(), tmp_path / "v6.db", ef.clock_at())
    try:
        await stop_runtime(container)
        assert not container.active
    finally:
        container.close()


def test_a_failed_cycle_ledger_closes_the_v6_ledger(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[str] = []
    real_close = container_module.LedgerV6.close

    def tracking_close(self: Any) -> None:
        closed.append(self.path)
        real_close(self)

    def broken(_path: Any) -> Any:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(container_module.LedgerV6, "close", tracking_close)
    monkeypatch.setattr(container_module, "LedgerCycles", broken)
    with pytest.raises(sqlite3.OperationalError):
        build_container(ef.settings(), tmp_path / "v6.db", ef.clock_at())
    assert closed == [str(tmp_path / "v6.db")]


def test_a_failed_assembly_closes_both_ledgers(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[str] = []
    real = container_module.LedgerCycles

    class TrackingCycles(real):  # type: ignore[misc, valid-type]
        def close(self) -> None:
            closed.append("cycles")
            super().close()

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("wiring bug")

    monkeypatch.setattr(container_module, "LedgerCycles", TrackingCycles)
    monkeypatch.setattr(container_module, "build_runtime_parts", broken)
    with pytest.raises(RuntimeError):
        build_container(ef.settings(), tmp_path / "v6.db", ef.clock_at())
    assert closed == ["cycles"]


def test_allowed_hosts_add_a_specific_bind_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_settings, "adapter_host", "127.0.0.1")
    # "testserver" is allowed only because tests/conftest.py opts in via ALLOWED_HOSTS.
    assert allowed_hosts() == ["127.0.0.1", "localhost", "[::1]", "testserver"]
    monkeypatch.setattr(adapter_settings, "adapter_host", "10.1.2.3")
    assert "10.1.2.3" in allowed_hosts()
    monkeypatch.setattr(adapter_settings, "adapter_host", "0.0.0.0")
    assert "0.0.0.0" not in allowed_hosts()
