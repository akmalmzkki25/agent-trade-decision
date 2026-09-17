"""
An execute-mode container without tasks, for the execution desk, the publisher, the
poll replier and startup recovery. The clock sits one second after the engine
fixtures' decision bar closed (Thursday 2026-09-17 13:00:01 UTC).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

from pydantic import SecretStr

from app.v6.clock import FakeClock
from app.v6.container import V6Container, build_container
from app.v6.ledger_cycles_schema import SessionRecord
from app.v6.ledger_intents import NewIntent
from app.v6.ledger_v6 import AccountMark
from app.v6.risk.intent_builder import IntentDraft
from app.v6.runtime.intent_book import Occupancy
from app.v6.runtime.sessions import trading_day_for

from . import engine_fixtures_v6 as ef
from .payloads_v6 import as_poll, poll_payload

TOKEN: Final[str] = "operator-" + "d" * 40
EA_KEY: Final[str] = "ea-desk-key-" + "e" * 40
EXECUTE: Final[dict[str, Any]] = {
    "mode": "execute", "backend": "operator", "operator_token": SecretStr(TOKEN),
    "ea_hmac_key": SecretStr(EA_KEY)}
INTENT_ID: Final[str] = "k7w2m4pq3xza"
FREE: Final[Occupancy] = Occupancy(open_positions=0, pending_orders=0)


@contextmanager
def desk_container(tmp_path: Path, clock: FakeClock, **overrides: Any) -> Iterator[V6Container]:
    config = ef.settings(halt_file=str(tmp_path / "V6_HALT"), **(EXECUTE | overrides))
    built = build_container(config, tmp_path / "desk.db", clock, run_tasks=False)
    built.switch.turn_on()
    try:
        yield built
    finally:
        built.switch.turn_off()
        built.close()


def demo_poll(container: V6Container, **changes: Any) -> None:
    """A fresh DEMO poll and the account mark the poll route would write."""
    now = container.clock.now_epoch()
    poll = as_poll({**poll_payload(), **changes})
    container.ea_state.record_poll(poll, now)
    container.ledger_v6.record_account_mark(AccountMark.from_poll(poll, now))


def open_session(container: V6Container, *, armed: bool = False,
                 mode: str = "execute") -> SessionRecord:
    now = container.clock.now_epoch()
    started = container.ledger_cycles.start_session(
        trading_day=trading_day_for(int(now)), backend="operator", mode=mode,
        started_at=now, armed=armed)
    return started.session


def intent_draft(session_id: str, now: float, intent_id: str = INTENT_ID,
                 **row_changes: Any) -> IntentDraft:
    row: dict[str, Any] = dict(
        intent_id=intent_id, cycle_id="c-00000000000000aa", session_id=session_id,
        agent="codex", source="operator", side="buy", order_type="BUY_LIMIT",
        entry=4300.0, sl=4292.0, tp=4316.0, lots=0.01, risk_usd=8.4,
        valid_until_epoch=int(now) + 120, pending_expiry_epoch=int(now) + 1800,
        time_barrier_s=7200, created_at=now)
    return IntentDraft(row=NewIntent(**{**row, **row_changes}), candidate_id=ef.CANDIDATE_ID,
                       ref_price=4300.2, max_drift_points=160, max_spread_points=35,
                       magic=250570)


def publish(container: V6Container, session_id: str, **row_changes: Any) -> IntentDraft:
    now = container.clock.now_epoch()
    draft = intent_draft(session_id, now, **row_changes)
    outcome = container.parts.intent_book.publish(draft, FREE, now)
    assert outcome.ok, outcome
    return draft
