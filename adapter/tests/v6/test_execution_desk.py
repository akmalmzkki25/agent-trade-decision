"""runtime.desk: arming, disarming and the watchdog's supervision of an execute session."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.v6.clock import FakeClock
from app.v6.container import V6Container
from app.v6.runtime.desk import ExecutionDesk, PersistedTrips
from app.v6.runtime.watchdog import BreakerSummary

from . import engine_fixtures_v6 as ef
from .desk_fixtures_v6 import demo_poll, desk_container, open_session, publish

TRIPPED = BreakerSummary(tripped=True, labels=("daily:2026-09-17",))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def clock() -> FakeClock:
    return ef.clock_at()


@pytest.fixture
def container(tmp_path: Path, clock: FakeClock) -> Iterator[V6Container]:
    with desk_container(tmp_path, clock) as built:
        yield built


def desk_of(container: V6Container) -> ExecutionDesk:
    return container.parts.desk


class BrokenPath:
    def exists(self) -> bool:
        raise PermissionError("denied")


# --- facts --------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_an_unreadable_halt_file_counts_as_halted(
        container: V6Container, caplog: pytest.LogCaptureFixture) -> None:
    broken = ExecutionDesk(replace(desk_of(container).deps, halt_path=BrokenPath()))
    with caplog.at_level(logging.ERROR, logger="app.v6.runtime.desk"):
        assert await broken.halted() is True
    assert "treated as halted" in caplog.text
    assert await desk_of(container).halted() is False
    container.halt_path.write_text("halt", encoding="utf-8")
    assert await desk_of(container).halted() is True


@pytest.mark.anyio
async def test_before_any_poll_only_persisted_trips_count(container: V6Container) -> None:
    desk = desk_of(container)
    assert await desk.breaker_view(ef.RECEIVED) == PersistedTrips(tripped=False)
    container.ledger_cycles.trip_breaker("daily", "2026-09-17", "EQUITY_DRAWDOWN", ef.RECEIVED)
    assert await desk.breaker_view(ef.RECEIVED) == PersistedTrips(tripped=True)


@pytest.mark.anyio
async def test_breakers_that_cannot_be_read_fail_closed(
        container: V6Container, monkeypatch: pytest.MonkeyPatch) -> None:
    demo_poll(container)

    async def broken(*_args: Any) -> Any:
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(container.parts.breakers, "for_poll", broken)
    desk = desk_of(container)
    assert await desk.breaker_view(ef.RECEIVED) is None
    decision = await desk.arm_decision(open_session(container), ef.RECEIVED)
    assert (decision.armed, decision.reason) == (False, "BREAKER")


# --- arming ---------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_a_fresh_demo_poll_arms_the_session(container: V6Container) -> None:
    demo_poll(container)
    session, decision = await desk_of(container).try_arm(open_session(container), ef.RECEIVED)
    assert (decision.armed, decision.reason) == (True, "ARMED")
    assert (session.armed, session.armed_at) == (True, ef.RECEIVED)


@pytest.mark.anyio
async def test_a_session_stays_disarmed_until_the_ea_polls(
        container: V6Container, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="app.v6.runtime.desk"):
        session, decision = await desk_of(container).try_arm(open_session(container),
                                                             ef.RECEIVED)
    assert (session.armed, decision.reason) == (False, "EA_NOT_SEEN")
    assert "stays disarmed: EA_NOT_SEEN" in caplog.text


@pytest.mark.anyio
async def test_a_refused_armed_session_is_disarmed_with_its_side_effects(
        container: V6Container) -> None:
    demo_poll(container)
    session = open_session(container, armed=True)
    draft = publish(container, session.session_id)
    container.halt_path.write_text("halt", encoding="utf-8")
    session, decision = await desk_of(container).try_arm(session, ef.RECEIVED)
    assert (session.armed, session.disarm_reason, decision.reason) == (False, "HALTED", "HALTED")
    record = container.ledger_cycles.intents.get(draft.row.intent_id)
    assert (record.status, record.report_reason) == ("CANCELLED", "HALTED")
    command = container.parts.control.commands.current(ef.RECEIVED)
    assert (command.command, command.reason) == ("CANCEL_PENDING", "HALTED")


@pytest.mark.anyio
async def test_shadow_mode_never_arms(tmp_path: Path, clock: FakeClock) -> None:
    with desk_container(tmp_path, clock, mode="shadow") as shadow:
        demo_poll(shadow)
        session = open_session(shadow, mode="shadow")
        assert await shadow.parts.desk.try_arm(session, ef.RECEIVED) == (session, None)


@pytest.mark.anyio
async def test_a_session_closed_meanwhile_is_returned_as_given(
        container: V6Container, monkeypatch: pytest.MonkeyPatch) -> None:
    demo_poll(container)
    session = open_session(container)
    monkeypatch.setattr(container.ledger_cycles, "active_session", lambda: None)
    returned, decision = await desk_of(container).try_arm(session, ef.RECEIVED)
    assert returned is session and decision.armed


@pytest.mark.anyio
async def test_disarm_without_a_session_still_cancels_and_withdraws(
        container: V6Container) -> None:
    session = open_session(container, armed=True)
    draft = publish(container, session.session_id)
    cancelled = await desk_of(container).disarm(None, reason="x", cancel_reason="SESSION_STOP",
                                                now=ef.RECEIVED)
    assert cancelled == (draft.row.intent_id,)
    assert container.ledger_cycles.active_session().armed is True


# --- supervision ------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_supervision_ignores_missing_or_disarmed_sessions(container: V6Container) -> None:
    desk = desk_of(container)
    assert await desk.supervise(ef.RECEIVED, halted=True, breakers=TRIPPED) is None
    open_session(container)
    assert await desk.supervise(ef.RECEIVED, halted=True, breakers=TRIPPED) is None


@pytest.mark.anyio
async def test_supervision_keeps_a_healthy_armed_session(container: V6Container) -> None:
    demo_poll(container)
    open_session(container, armed=True)
    decision = await desk_of(container).supervise(ef.RECEIVED, halted=False,
                                                  breakers=BreakerSummary())
    assert decision is not None and decision.armed
    assert container.ledger_cycles.active_session().armed is True


@pytest.mark.anyio
async def test_supervision_disarms_on_a_breaker_and_expires_stale_intents(
        container: V6Container, clock: FakeClock) -> None:
    demo_poll(container)
    session = open_session(container, armed=True)
    draft = publish(container, session.session_id)
    clock.advance(121)
    decision = await desk_of(container).supervise(clock.now_epoch(), halted=False,
                                                  breakers=TRIPPED)
    assert decision is not None and decision.reason == "BREAKER"
    assert container.ledger_cycles.active_session().disarm_reason == "BREAKER"
    record = container.ledger_cycles.intents.get(draft.row.intent_id)
    assert (record.status, record.report_reason) == ("EXPIRED", "TTL_EXPIRED")


@pytest.mark.anyio
async def test_a_stale_ea_disarms_the_session(container: V6Container, clock: FakeClock) -> None:
    demo_poll(container)
    open_session(container, armed=True)
    clock.advance(container.settings.ea_stale_s + 1)
    decision = await desk_of(container).supervise(clock.now_epoch(), halted=False,
                                                  breakers=BreakerSummary())
    assert decision is not None and decision.reason == "EA_STALE"
    assert container.ledger_cycles.active_session().armed is False
