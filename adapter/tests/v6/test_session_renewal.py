"""A session closed by the rollover reopens (and re-arms) when the block ends."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.v6.clock import FakeClock
from app.v6.ledger_cycles import LedgerCycles
from app.v6.ledger_v6 import LedgerV6
from app.v6.runtime.ea_state import EaState
from app.v6.runtime.sessions import (
    ACTOR_RUNTIME, STOP_REASON_ROLLOVER, SessionService, build_control_plane,
)

from . import engine_fixtures_v6 as ef
from .execute_fixtures_v6 import armed_session, execute_settings, running
from .operator_cli_fixtures_v6 import cli  # noqa: F401 (puts the CLI on sys.path)
from .payloads_v6 import as_poll, poll_payload
from .test_v6_sessions import MIDDAY, ROLLOVER_START

AFTER_ROLLOVER: int = ROLLOVER_START + 2 * 3600 + 300      # 23:05 UTC, the block is over
waiting = importlib.import_module("v6ops.waiting")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[LedgerCycles]:
    led = LedgerCycles(tmp_path / "renewal.db")
    yield led
    led.close()


@pytest.fixture
def control_log(tmp_path: Path) -> Iterator[LedgerV6]:
    log = LedgerV6(tmp_path / "renewal.db", clock=FakeClock(epoch=MIDDAY))
    yield log
    log.close()


def service_for(ledger: LedgerCycles, control_log: LedgerV6, *,
                auto_renew: bool) -> SessionService:
    ea_state = EaState()
    ea_state.record_poll(as_poll(poll_payload()), MIDDAY)
    plane = build_control_plane(ledger=ledger, control_log=control_log, ea_state=ea_state,
                                auto_renew=auto_renew, backend="rules", mode="shadow")
    return plane.sessions


@pytest.mark.anyio
async def test_rollover_close_then_renewal(ledger: LedgerCycles,
                                           control_log: LedgerV6) -> None:
    service = service_for(ledger, control_log, auto_renew=True)
    started = await service.start(backend="rules", mode="shadow", actor="operator", now=MIDDAY)
    closed = await service.auto_close_if_rollover(float(ROLLOVER_START + 5))
    assert closed is not None and closed.session.stop_reason == STOP_REASON_ROLLOVER
    assert service.renewal_due
    assert await service.renew_if_due(float(ROLLOVER_START + 60)) is None     # still closed
    renewed = await service.renew_if_due(float(AFTER_ROLLOVER))
    assert renewed is not None and renewed.session is not None and renewed.created
    assert renewed.session.session_id != started.session.session_id
    assert (renewed.session.backend, renewed.session.mode) == ("rules", "shadow")
    assert not service.renewal_due
    assert await service.renew_if_due(float(AFTER_ROLLOVER + 60)) is None


@pytest.mark.anyio
async def test_a_user_stop_cancels_the_renewal(ledger: LedgerCycles,
                                               control_log: LedgerV6) -> None:
    service = service_for(ledger, control_log, auto_renew=True)
    await service.start(backend="rules", mode="shadow", actor="operator", now=MIDDAY)
    await service.auto_close_if_rollover(float(ROLLOVER_START + 5))
    await service.stop(actor="operator", reason="sudah_cukup", now=float(ROLLOVER_START + 30))
    assert not service.renewal_due
    assert await service.renew_if_due(float(AFTER_ROLLOVER)) is None
    assert await service.active() is None


@pytest.mark.anyio
async def test_a_runtime_stop_keeps_the_renewal(ledger: LedgerCycles,
                                                control_log: LedgerV6) -> None:
    service = service_for(ledger, control_log, auto_renew=True)
    await service.start(backend="rules", mode="shadow", actor="operator", now=MIDDAY)
    await service.auto_close_if_rollover(float(ROLLOVER_START + 5))
    await service.stop(actor=ACTOR_RUNTIME, reason="rollover_auto_close",
                       now=float(ROLLOVER_START + 30))
    assert service.renewal_due


@pytest.mark.anyio
async def test_without_auto_renew_nothing_reopens(ledger: LedgerCycles,
                                                  control_log: LedgerV6) -> None:
    service = service_for(ledger, control_log, auto_renew=False)
    await service.start(backend="rules", mode="shadow", actor="operator", now=MIDDAY)
    await service.auto_close_if_rollover(float(ROLLOVER_START + 5))
    assert not service.renewal_due
    assert await service.renew_if_due(float(AFTER_ROLLOVER)) is None


@pytest.mark.anyio
async def test_auto_renew_needs_a_backend_and_a_mode(ledger: LedgerCycles,
                                                     control_log: LedgerV6) -> None:
    ea_state = EaState()
    ea_state.record_poll(as_poll(poll_payload()), MIDDAY)
    service = build_control_plane(ledger=ledger, control_log=control_log, ea_state=ea_state,
                                  auto_renew=True).sessions
    await service.start(backend="rules", mode="shadow", actor="operator", now=MIDDAY)
    await service.auto_close_if_rollover(float(ROLLOVER_START + 5))
    assert not service.renewal_due


def test_the_cli_waits_through_a_renewal() -> None:
    assert waiting.session_gone({"session": None, "session_renewal_due": False})
    assert not waiting.session_gone({"session": None, "session_renewal_due": True})
    assert waiting.session_gone({"session": None})


# --- the running adapter ------------------------------------------------------------------
def test_the_runtime_reopens_and_rearms_after_the_rollover(tmp_path: Path) -> None:
    clock = FakeClock(epoch=float(ef.AS_OF))
    with running(tmp_path, execute_settings(tmp_path), clock) as adapter:
        first = armed_session(adapter)
        parts = adapter.container.parts
        closing = float(ROLLOVER_START + 86_400 + 5)    # Thursday's rollover block
        clock.epoch = closing
        adapter.poll()
        adapter.run(parts.watchdog.tick)
        status = adapter.client.get("/v6/status").json()
        assert (status["session"], status["session_renewal_due"]) == (None, True)
        clock.epoch = float(AFTER_ROLLOVER + 86_400)
        adapter.poll()
        adapter.run(parts.watchdog.tick)
        status = adapter.client.get("/v6/status").json()
        assert status["session_renewal_due"] is False
        assert status["session"]["session_id"] != first and status["armed"] is True
