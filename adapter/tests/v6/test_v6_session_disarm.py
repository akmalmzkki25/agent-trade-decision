"""Stopping an armed session disarms it, and the stop outcome says when and why."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.v6.clock import FakeClock
from app.v6.ledger_cycles import LedgerCycles
from app.v6.ledger_v6 import LedgerV6
from app.v6.runtime.ea_state import EaState
from app.v6.runtime.sessions import build_control_plane

from .payloads_v6 import RECEIVED_AT, as_poll, poll_payload

NOW: float = RECEIVED_AT


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def stores(tmp_path: Path) -> Iterator[tuple[LedgerCycles, LedgerV6]]:
    ledger = LedgerCycles(tmp_path / "disarm.db")
    control_log = LedgerV6(tmp_path / "disarm.db", clock=FakeClock(epoch=NOW))
    yield ledger, control_log
    ledger.close()
    control_log.close()


@pytest.mark.anyio
async def test_stopping_an_armed_session_records_the_disarm(
        stores: tuple[LedgerCycles, LedgerV6]) -> None:
    ledger, control_log = stores
    ea_state = EaState()
    ea_state.record_poll(as_poll(poll_payload()), NOW)
    plane = build_control_plane(ledger=ledger, control_log=control_log, ea_state=ea_state)
    started = await plane.sessions.start(backend="operator", mode="execute",
                                         actor="operator", now=NOW)
    assert started.session is not None
    assert ledger.set_session_armed(started.session.session_id, True, at=NOW + 1)

    outcome = await plane.sessions.stop(actor="operator", reason="done_for_today",
                                        now=NOW + 60)

    session = outcome.session
    assert session is not None and not session.armed
    assert (session.armed_at, session.disarmed_at, session.disarm_reason) == (
        NOW + 1, NOW + 60, "done_for_today")
    assert ledger.sessions_for_day(session.trading_day)[0] == session
    assert outcome.to_dict()["session"]["disarmed_at"] == NOW + 60


@pytest.mark.anyio
async def test_a_session_closed_meanwhile_is_not_reported_as_stopped(
        stores: tuple[LedgerCycles, LedgerV6], monkeypatch: pytest.MonkeyPatch) -> None:
    ledger, control_log = stores
    ea_state = EaState()
    ea_state.record_poll(as_poll(poll_payload()), NOW)
    plane = build_control_plane(ledger=ledger, control_log=control_log, ea_state=ea_state)
    await plane.sessions.start(backend="operator", mode="execute", actor="operator", now=NOW)
    monkeypatch.setattr(ledger, "stop_session", lambda *_args, **_kwargs: False)

    outcome = await plane.sessions.stop(actor="operator", reason="cukup", now=NOW + 60)

    assert (outcome.stopped, outcome.session, outcome.cancelled_intents) == (False, None, ())
    assert (outcome.command.command, outcome.command.reason) == ("CANCEL_PENDING", "cukup")
