"""runtime.poll_reply: command precedence, armed intent serving and signing."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from app.v6 import wire
from app.v6.clock import FakeClock
from app.v6.container import V6Container
from app.v6.runtime.desk import ExecutionDesk
from app.v6.runtime.intent_book import IntentBook
from app.v6.runtime.poll_reply import PollFacts, PollReplier

from . import engine_fixtures_v6 as ef
from .desk_fixtures_v6 import EA_KEY, demo_poll, desk_container, open_session, publish
from .payloads_v6 import as_poll, poll_payload

CALM = PollFacts(halted=False, breaker_tripped=False, point=0.01)


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


async def reply(container: V6Container, facts: PollFacts = CALM, **changes: Any):
    demo_poll(container, **changes)
    poll = as_poll({**poll_payload(), **changes})
    replier = PollReplier(container.parts.desk)
    return await replier.reply(poll, container.clock.now_epoch(), facts)


def signed(response: Any, point: float = 0.01) -> bool:
    return wire.verify_intent(SecretStr(EA_KEY), response, point)


# --- commands ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_an_idle_answer_is_signed(container: V6Container) -> None:
    response = await reply(container)
    assert (response.has_intent, response.command) == (False, "NONE")
    assert signed(response)


@pytest.mark.anyio
async def test_flatten_outranks_a_halt_and_repeats_until_the_ea_is_flat(
        container: V6Container) -> None:
    commands = container.parts.control.commands
    commands.request_flatten("breaker_trip", ef.RECEIVED)
    commands.request_cancel_pending("halt", ef.RECEIVED)      # never downgrades FLATTEN
    halted = PollFacts(halted=True, breaker_tripped=True, point=0.01)
    first = await reply(container, halted, open_v6_positions=1)
    second = await reply(container, halted, pending_v6_orders=0, open_v6_positions=1)
    flat = await reply(container, halted)
    assert [first.command, second.command, flat.command] == [
        "FLATTEN", "FLATTEN", "CANCEL_PENDING"]
    assert all(signed(response) for response in (first, second, flat))


@pytest.mark.anyio
async def test_a_pending_command_holds_the_intent_back(container: V6Container) -> None:
    session = open_session(container, armed=True)
    publish(container, session.session_id)
    container.parts.control.commands.request_cancel_pending("operator", ef.RECEIVED)
    response = await reply(container)
    assert (response.command, response.has_intent) == ("CANCEL_PENDING", False)


# --- intents ------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_an_armed_session_is_served_its_intent_once_written(
        container: V6Container) -> None:
    session = open_session(container, armed=True)
    draft = publish(container, session.session_id)
    response = await reply(container)
    assert (response.has_intent, response.intent_id) == (True, draft.row.intent_id)
    assert signed(response)
    record = container.ledger_cycles.intents.get(draft.row.intent_id)
    assert (record.status, record.delivered_at) == ("DELIVERED", ef.RECEIVED)


@pytest.mark.anyio
@pytest.mark.parametrize("session_kind", ["none", "disarmed", "other"])
async def test_only_the_armed_session_that_published_is_served(
        container: V6Container, session_kind: str) -> None:
    session = open_session(container, armed=session_kind != "disarmed")
    publish(container, "0123456789ab" if session_kind == "other" else session.session_id)
    if session_kind == "none":
        container.ledger_cycles.stop_session(session.session_id, stopped_at=ef.RECEIVED,
                                             reason="x")
    assert (await reply(container)).has_intent is False


@pytest.mark.anyio
async def test_an_intent_past_its_validity_is_not_served(container: V6Container,
                                                        clock: FakeClock) -> None:
    session = open_session(container, armed=True)
    publish(container, session.session_id)
    clock.advance(120)
    assert (await reply(container)).has_intent is False


@pytest.mark.anyio
async def test_a_failed_arming_check_holds_the_intent_back(
        container: V6Container, caplog: pytest.LogCaptureFixture) -> None:
    session = open_session(container, armed=True)
    publish(container, session.session_id)
    container.halt_path.write_text("halt", encoding="utf-8")
    with caplog.at_level(logging.INFO, logger="app.v6.runtime.poll_reply"):
        assert (await reply(container)).has_intent is False
    assert "held back: HALTED" in caplog.text


@pytest.mark.anyio
async def test_an_intent_without_its_draft_is_not_served(container: V6Container) -> None:
    """After a restart the book has no draft: the stored intent is never served."""
    session = open_session(container, armed=True)
    draft = publish(container, session.session_id)
    restarted = IntentBook(container.ledger_cycles.intents)
    desk = ExecutionDesk(replace(container.parts.desk.deps, book=restarted))
    demo_poll(container)
    response = await PollReplier(desk).reply(as_poll(poll_payload()), ef.RECEIVED, CALM)
    assert response.has_intent is False
    assert container.ledger_cycles.intents.get(draft.row.intent_id).status == "PUBLISHED"


@pytest.mark.anyio
async def test_an_unsignable_intent_is_answered_idle(
        container: V6Container, caplog: pytest.LogCaptureFixture) -> None:
    session = open_session(container, armed=True)
    publish(container, session.session_id)
    odd = PollFacts(halted=False, breaker_tripped=False, point=0.3)
    with caplog.at_level(logging.ERROR, logger="app.v6.runtime.poll_reply"):
        response = await reply(container, odd)
    assert response.has_intent is False and signed(response, 0.3)
    assert "cannot be served" in caplog.text


@pytest.mark.anyio
async def test_a_broken_point_answers_idle_and_unsigned(container: V6Container) -> None:
    broken = PollFacts(halted=False, breaker_tripped=False, point=0.0)
    response = await reply(container, broken)
    assert (response.has_intent, response.sig) == (False, "")


@pytest.mark.anyio
async def test_an_unreadable_ledger_answers_idle(container: V6Container,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(container.ledger_cycles, "active_session", broken)
    response = await reply(container)
    assert (response.has_intent, response.command) == (False, "NONE")


@pytest.mark.anyio
async def test_shadow_mode_never_looks_for_an_intent(tmp_path: Path, clock: FakeClock) -> None:
    with desk_container(tmp_path, clock, mode="shadow") as shadow:
        session = open_session(shadow, armed=True)
        publish(shadow, session.session_id)
        response = await reply(shadow)
    assert response.has_intent is False and signed(response)
