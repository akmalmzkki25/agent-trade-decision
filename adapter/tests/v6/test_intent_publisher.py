"""runtime.publisher: an approved operator ENTER becomes a PUBLISHED intent, or why not."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.v6.clock import FakeClock
from app.v6.container import V6Container
from app.v6.cycle_codes import HoldReason
from app.v6.cycle_types import MarketContext, ProtocolDecision
from app.v6.deliberation.publication import PublishOutcome, PublishRequest
from app.v6.market.sessions import session_state
from app.v6.risk import intent_builder as ib
from app.v6.runtime import intent_book as book
from app.v6.runtime.publisher import MAX_DETAIL_CHARS, NOT_ARMED, IntentPublisher, refused
from app.v6.types import ExitPlan, SizingResult

from . import engine_fixtures_v6 as ef
from .cycle_fixtures_v6 import calendar
from .desk_fixtures_v6 import demo_poll, desk_container, open_session, publish


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


def context() -> MarketContext:
    return MarketContext.from_snapshot(
        ef.engine_snapshot(), cycle_id="c-00000000000000bb", received_at=ef.RECEIVED,
        bars={}, session=session_state(ef.AS_OF), calendar=calendar(), features={})


def request(agent: str = "codex") -> PublishRequest:
    decision = ProtocolDecision(action="ENTER", hold_reason=None, candidate_id=ef.CANDIDATE_ID,
                                size_multiplier=1.0, risk_tier="standard")
    plan = ExitPlan(side="buy", entry=4300.0, sl=4292.0, tp=4316.0, stop_distance=8.0,
                    reward_r=2.0, time_barrier_s=7200)
    sizing = SizingResult(lots=0.01, risk_usd=8.4, risk_budget_usd=10.0, loss_per_lot=840.0,
                          notional_usd=4300.0, margin_usd=8.6)
    return PublishRequest(decision=decision, candidate=ef.candidate(), exit_plan=plan,
                          sizing=sizing, context=context(), agent=agent)


async def publish_now(container: V6Container, agent: str = "codex") -> PublishOutcome:
    return await IntentPublisher(container.parts.desk).publish(request(agent))


# --- the refusal table ---------------------------------------------------------------------
@pytest.mark.parametrize(("code", "reason"), [
    ("HALTED", HoldReason.HALTED), ("EA_STALE", HoldReason.STALE),
    (ib.RISK_OVER_BUDGET, HoldReason.SIZE), (ib.LIMIT_NOT_PASSIVE, HoldReason.EXIT),
    (ib.TOO_LATE, HoldReason.LATE), (book.BOOK_OCCUPIED, HoldReason.GATE),
    ("POLICY_OPERATOR_DEMO_ONLY", HoldReason.GATE), (ib.CANDIDATE_MISMATCH, HoldReason.ERROR),
])
def test_refusals_become_holds(code: str, reason: HoldReason) -> None:
    outcome = refused(code, "why")
    assert (outcome.published, outcome.hold_reason, outcome.code) == (False, reason, code)
    assert outcome.detail == f"not published: {code} (why)"


@pytest.mark.parametrize("code", [NOT_ARMED, ib.SESSION_NOT_ARMED, "NO_SESSION",
                                  "MODE_NOT_EXECUTE"])
def test_a_session_that_cannot_publish_only_records_the_decision(code: str) -> None:
    outcome = refused(code, "x" * 400)
    assert (outcome.published, outcome.hold_reason) == (False, None)
    assert len(outcome.detail) == MAX_DETAIL_CHARS


def test_a_published_outcome_carries_no_hold() -> None:
    with pytest.raises(ValueError, match="no hold reason"):
        PublishOutcome(intent_id="k7w2m4pq3xza", hold_reason=HoldReason.GATE)


# --- publishing ------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_without_an_armed_session_nothing_is_published(container: V6Container) -> None:
    demo_poll(container)
    assert (await publish_now(container)).code == NOT_ARMED
    open_session(container)
    outcome = await publish_now(container)
    assert (outcome.code, outcome.hold_reason) == (NOT_ARMED, None)
    assert container.ledger_cycles.intents.active_intent() is None


@pytest.mark.anyio
async def test_an_armed_session_publishes_the_intent(container: V6Container) -> None:
    demo_poll(container)
    open_session(container, armed=True)
    outcome = await publish_now(container)
    assert outcome.published and outcome.code == book.BOOK_PUBLISHED
    record = container.ledger_cycles.intents.get(outcome.intent_id)
    assert (record.status, record.agent, record.order_type, record.lots) == (
        "PUBLISHED", "codex", "BUY_LIMIT", 0.01)


@pytest.mark.anyio
async def test_a_failed_arming_check_holds(container: V6Container) -> None:
    demo_poll(container)
    open_session(container, armed=True)
    container.halt_path.write_text("halt", encoding="utf-8")
    outcome = await publish_now(container)
    assert (outcome.hold_reason, outcome.code) == (HoldReason.HALTED, "HALTED")


@pytest.mark.anyio
async def test_an_intent_builder_refusal_holds(container: V6Container) -> None:
    demo_poll(container)
    open_session(container, armed=True)
    outcome = await publish_now(container, agent="gemini")
    assert (outcome.hold_reason, outcome.code) == (HoldReason.GATE, "POLICY_AGENT_NOT_ALLOWED")


@pytest.mark.anyio
async def test_an_occupied_book_holds(container: V6Container) -> None:
    demo_poll(container)
    session = open_session(container, armed=True)
    publish(container, session.session_id)
    outcome = await publish_now(container)
    assert (outcome.hold_reason, outcome.code) == (HoldReason.GATE, book.BOOK_ACTIVE_INTENT)
    demo_poll(container, open_v6_positions=1)
    outcome = await publish_now(container)
    assert (outcome.hold_reason, outcome.code) == (HoldReason.GATE, book.BOOK_OCCUPIED)
