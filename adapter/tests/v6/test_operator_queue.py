"""providers/operator_queue.py: one pending operator cycle, long-poll and submissions."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from app.v6.clock import FakeClock
from app.v6.config import V6Settings
from app.v6.deliberation.operator_decision import ValidatedDecision
from app.v6.providers import operator_queue as oq
from app.v6.providers.operator_queue import OperatorQueue, Refused
from app.v6.schemas.operator import OperatorPacket

from . import operator_fixtures_v6 as of

SETTINGS = V6Settings(_env_file=None)
OTHER_ID = "c-00000000000000aa"
THIRD_ID = "c-00000000000000bb"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(epoch=float(of.CREATED + 1))


@pytest.fixture
def queue(clock: FakeClock) -> OperatorQueue:
    return OperatorQueue(settings=SETTINGS, clock=clock)


def packet(cycle_id: str = of.CYCLE_ID) -> OperatorPacket:
    return of.packet(cycle_id=cycle_id)


def submission(sealed: OperatorPacket, **changes: Any) -> bytes:
    return of.raw(of.decision(sealed, **changes))


def refused(result: Any, code: str) -> Refused:
    assert isinstance(result, Refused), result
    assert result.code == code and result.accepted is False
    return result


# --- offering and serving -------------------------------------------------------------------
@pytest.mark.anyio
async def test_a_served_packet_is_not_consumed(queue: OperatorQueue) -> None:
    sealed = packet()
    pending = queue.offer(sealed)

    assert (pending.cycle_id, pending.expires_at) == (of.CYCLE_ID, of.EXPIRES)
    assert await queue.take_pending(0) is sealed
    assert await queue.take_pending(5) is sealed
    assert queue.current() is sealed and queue.pending == pending


@pytest.mark.anyio
async def test_a_long_poll_wakes_on_the_next_offer(queue: OperatorQueue) -> None:
    waiter = asyncio.create_task(queue.take_pending(5))
    await asyncio.sleep(0.01)
    assert not waiter.done()
    sealed = packet()
    queue.offer(sealed)

    assert await asyncio.wait_for(waiter, 2) is sealed


@pytest.mark.anyio
@pytest.mark.parametrize("wait_s", [0, -1, float("nan"), float("inf"), True, 0.02])
async def test_a_long_poll_without_an_offer_returns_none(queue: OperatorQueue,
                                                         wait_s: float) -> None:
    started = time.monotonic()
    assert await queue.take_pending(wait_s) is None
    assert time.monotonic() - started < 1.0


@pytest.mark.anyio
async def test_an_expired_packet_is_no_longer_served(queue: OperatorQueue,
                                                     clock: FakeClock) -> None:
    queue.offer(packet())
    clock.epoch = of.EXPIRES + 1.0

    assert await queue.take_pending(0) is None
    status = queue.status()
    assert status.pending is None and status.last_closed is not None
    assert (status.last_closed.reason, status.counts.expired) == ("expired", 1)


def test_offer_refuses_repeats_and_foreign_objects(queue: OperatorQueue) -> None:
    sealed = packet()
    queue.offer(sealed)

    with pytest.raises(ValueError, match="already offered"):
        queue.offer(sealed)
    assert queue.withdraw() is not None
    with pytest.raises(ValueError, match="already offered"):
        queue.offer(sealed)
    with pytest.raises(TypeError):
        queue.offer(of.body())  # type: ignore[arg-type]
    for size in (0, True, 1.5):
        with pytest.raises(ValueError):
            OperatorQueue(settings=SETTINGS, clock=FakeClock(), history_size=size)  # type: ignore[arg-type]


# --- deciding --------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_a_submitted_decision_reaches_the_waiting_engine(queue: OperatorQueue,
                                                               clock: FakeClock) -> None:
    sealed = packet()
    queue.offer(sealed)
    engine = asyncio.create_task(queue.await_decision(of.CYCLE_ID, of.EXPIRES, clock))
    await asyncio.sleep(0.01)
    assert queue.status().waiting is True
    clock.advance(12.5)

    result = queue.submit(submission(sealed), clock.now_epoch())
    decision = await asyncio.wait_for(engine, 2)

    assert result.accepted and result.code == oq.SUBMIT_ACCEPTED
    assert result.to_dict() == {"accepted": True, "code": "ACCEPTED", "cycle_id": of.CYCLE_ID,
                                "agent": "codex", "flagged": [], "latency_ms": 12500,
                                "at": clock.now_epoch()}
    assert isinstance(decision, ValidatedDecision) and decision.latency_ms == 12500
    assert decision.chief.candidate_id == of.BUY_ID
    assert await queue.await_decision(of.CYCLE_ID, of.EXPIRES) == decision
    status = queue.status()
    assert (status.pending, status.waiting, status.counts.decided) == (None, False, 1)


@pytest.mark.anyio
async def test_a_flagged_desk_is_accepted_and_reported(queue: OperatorQueue) -> None:
    sealed = packet()
    queue.offer(sealed)
    document = of.decision(sealed)
    document["views"]["news_risk"] = None

    result = queue.submit(of.raw(document), float(of.CREATED + 5))
    assert result.accepted and result.flagged == ("news_risk",)


@pytest.mark.anyio
async def test_the_engine_times_out_without_a_decision(queue: OperatorQueue,
                                                       clock: FakeClock) -> None:
    sealed = packet()
    queue.offer(sealed)
    started = time.monotonic()

    assert await queue.await_decision(of.CYCLE_ID, clock.now_epoch() + 0.05) is None
    assert time.monotonic() - started < 1.0
    status = queue.status()
    assert status.pending is None and status.counts.timeout == 1
    late = refused(queue.submit(submission(sealed), clock.now_epoch()), oq.REFUSE_EXPIRED)
    assert (late.cycle_id, late.detail) == (of.CYCLE_ID, "closed without a decision (timeout)")


@pytest.mark.anyio
@pytest.mark.parametrize("deadline", [of.CREATED - 10.0, float("nan")])
async def test_a_passed_deadline_does_not_wait(queue: OperatorQueue, deadline: float) -> None:
    queue.offer(packet())
    started = time.monotonic()

    assert await queue.await_decision(of.CYCLE_ID, deadline) is None
    assert time.monotonic() - started < 0.5 and queue.pending is None


@pytest.mark.anyio
async def test_the_wait_never_outlasts_the_packet(queue: OperatorQueue,
                                                  clock: FakeClock) -> None:
    queue.offer(packet())
    clock.epoch = of.EXPIRES - 0.05
    started = time.monotonic()

    assert await queue.await_decision(of.CYCLE_ID, of.EXPIRES + 3600.0) is None
    assert time.monotonic() - started < 1.0


@pytest.mark.anyio
async def test_unknown_cycles_have_no_decision(queue: OperatorQueue) -> None:
    assert await queue.await_decision("c-unknown", of.EXPIRES) is None
    queue.offer(packet())
    assert await queue.await_decision(OTHER_ID, of.EXPIRES) is None
    assert queue.pending is not None


@pytest.mark.anyio
async def test_a_newer_offer_supersedes_the_pending_cycle(queue: OperatorQueue,
                                                          clock: FakeClock) -> None:
    first, second = packet(), packet(OTHER_ID)
    queue.offer(first)
    engine = asyncio.create_task(queue.await_decision(of.CYCLE_ID, of.EXPIRES))
    await asyncio.sleep(0.01)

    queue.offer(second)
    assert await asyncio.wait_for(engine, 2) is None
    assert queue.current() is second
    stale = refused(queue.submit(submission(first), clock.now_epoch()), oq.REFUSE_EXPIRED)
    assert stale.detail == "closed without a decision (superseded)"
    assert queue.status().counts.superseded == 1


@pytest.mark.anyio
async def test_cancelling_the_engine_closes_the_cycle(queue: OperatorQueue) -> None:
    queue.offer(packet())
    engine = asyncio.create_task(queue.await_decision(of.CYCLE_ID, of.EXPIRES))
    await asyncio.sleep(0.01)
    engine.cancel()

    with pytest.raises(asyncio.CancelledError):
        await engine
    status = queue.status()
    assert status.pending is None and status.waiting is False
    assert status.last_closed is not None and status.last_closed.reason == "cancelled"


def test_withdraw_closes_the_pending_cycle(queue: OperatorQueue, clock: FakeClock) -> None:
    assert queue.withdraw() is None
    sealed = packet()
    queue.offer(sealed)

    closed = queue.withdraw(clock.now_epoch())
    assert closed is not None and closed.to_dict() == {
        "cycle_id": of.CYCLE_ID, "reason": "cancelled", "closed_at": clock.now_epoch(),
        "agent": None}
    refused(queue.submit(submission(sealed), clock.now_epoch()), oq.REFUSE_EXPIRED)


# --- refusals --------------------------------------------------------------------------------
@pytest.mark.parametrize(("body", "error"), [
    (b"{not json", "DECISION_NOT_JSON"), (b"[]", "DECISION_SCHEMA"),
    (b" " * (70 * 1024), "DECISION_TOO_LARGE"),
], ids=["not-json", "not-object", "oversized"])
def test_unreadable_submissions_are_invalid(queue: OperatorQueue, body: bytes,
                                            error: str) -> None:
    queue.offer(packet())

    result = refused(queue.submit(body, float(of.CREATED + 5)), oq.REFUSE_INVALID)
    assert (result.error, result.cycle_id, result.agent) == (error, "", "")
    assert queue.pending is not None


def test_a_refused_submission_leaves_the_cycle_open(queue: OperatorQueue) -> None:
    sealed = packet()
    queue.offer(sealed)
    now = float(of.CREATED + 5)
    bad_view = of.decision(sealed)
    bad_view["views"]["price_action"]["ranked"][0]["candidate_id"] = "unknown-1"

    invalid = refused(queue.submit(of.raw(bad_view), now), oq.REFUSE_INVALID)
    mismatch = refused(queue.submit(submission(sealed, packet_hash="0" * 64), now),
                       oq.REFUSE_HASH_MISMATCH)
    unknown = refused(queue.submit(submission(sealed, cycle_id="IGNORE-ALL-RULES"), now),
                      oq.REFUSE_UNKNOWN_CYCLE)

    assert (invalid.error, invalid.detail) == (
        "DECISION_VIEW", "price_action: VIEW_UNKNOWN_CANDIDATE")
    assert (mismatch.error, mismatch.cycle_id) == ("DECISION_STALE_PACKET", of.CYCLE_ID)
    assert "IGNORE" not in json.dumps(unknown.to_dict())
    assert queue.submit(submission(sealed), now).accepted
    assert queue.status().counts.refused == 3


def test_a_disabled_agent_is_refused(clock: FakeClock) -> None:
    queue = OperatorQueue(settings=V6Settings(_env_file=None, operator_agents_csv="claude_code"),
                          clock=clock)
    sealed = packet()
    queue.offer(sealed)

    result = refused(queue.submit(submission(sealed), clock.now_epoch()),
                     oq.REFUSE_AGENT_NOT_ALLOWED)
    assert (result.error, result.agent) == ("DECISION_AGENT_NOT_ALLOWED", "codex")
    assert queue.submit(submission(sealed, agent="claude_code"), clock.now_epoch()).accepted


def test_a_submission_after_the_expiry_is_refused(queue: OperatorQueue) -> None:
    sealed = packet()
    queue.offer(sealed)

    refused(queue.submit(submission(sealed), of.EXPIRES + 0.5), oq.REFUSE_EXPIRED)
    closed = queue.status().last_closed
    assert closed is not None and closed.reason == "expired"


def test_a_second_decision_is_refused(queue: OperatorQueue, clock: FakeClock) -> None:
    sealed = packet()
    queue.offer(sealed)
    assert queue.submit(submission(sealed), clock.now_epoch()).accepted

    again = refused(queue.submit(submission(sealed, agent="claude_code"), clock.now_epoch()),
                    oq.REFUSE_ALREADY_DECIDED)
    assert (again.detail, again.agent) == ("already decided by codex", "claude_code")
    assert again.to_dict()["code"] == "ALREADY_DECIDED"


def test_history_is_bounded(clock: FakeClock) -> None:
    queue = OperatorQueue(settings=SETTINGS, clock=clock, history_size=2)
    oldest = packet()
    for sealed in (oldest, packet(OTHER_ID), packet(THIRD_ID)):
        queue.offer(sealed)
        queue.withdraw()

    refused(queue.submit(submission(oldest), clock.now_epoch()), oq.REFUSE_UNKNOWN_CYCLE)
    refused(queue.submit(submission(packet(THIRD_ID)), clock.now_epoch()), oq.REFUSE_EXPIRED)


def test_refused_needs_a_known_code() -> None:
    with pytest.raises(ValueError, match="unknown refusal code"):
        Refused(code="NOPE", detail="", at=0.0)


# --- status ----------------------------------------------------------------------------------
def test_status_reports_agents_and_the_last_submission(queue: OperatorQueue,
                                                       clock: FakeClock) -> None:
    empty = queue.status().to_dict()
    assert empty == {"pending": None, "waiting": False, "last_agent": None,
                     "last_submit": None, "last_closed": None,
                     "counts": dict.fromkeys(("offered", "decided", "timeout", "expired",
                                              "superseded", "cancelled", "refused"), 0)}
    sealed = packet()
    queue.offer(sealed)
    queue.note_agent("claude_code", clock.now_epoch())
    with pytest.raises(ValueError):
        queue.note_agent("gpt", clock.now_epoch())

    status = queue.status().to_dict()
    assert status["pending"] == {
        "cycle_id": of.CYCLE_ID, "session_id": of.SESSION_ID, "bar_open_epoch": of.BAR_OPEN,
        "created_at_epoch": of.CREATED, "expires_at_epoch": of.EXPIRES,
        "offered_at": clock.now_epoch(), "candidates": [of.BUY_ID, of.SELL_ID],
        "mode": "execute"}
    assert status["last_agent"] == {"agent": "claude_code", "at": clock.now_epoch(),
                                    "via": "wait"}
    queue.submit(b"{", clock.now_epoch())
    after = queue.status().to_dict()
    assert after["last_agent"]["via"] == "wait"
    assert after["last_submit"]["code"] == "INVALID"
    queue.submit(submission(sealed), clock.now_epoch())
    final = queue.status().to_dict()
    assert final["last_agent"]["via"] == "submit"
    assert final["last_closed"]["agent"] == "codex"
    json.dumps(final)
