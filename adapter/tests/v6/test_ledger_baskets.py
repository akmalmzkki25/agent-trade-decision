"""ledger_baskets and schemas.basket: V6 basket results, stored once in the V6 database."""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.ledger import Ledger
from app.models import BasketResultEvent
from app.v6.clock import FakeClock
from app.v6.cycle_types import CycleResult
from app.v6.ledger_baskets import BasketJournal, received_at_utc
from app.v6.schemas.basket import V6BasketResultEvent

from .cycle_fixtures_v6 import hold_result
from .execute_fixtures_v6 import basket_result

INTENT_ID = "k7w2m4pq3xza"
NOW = 1_789_650_001.5


def event(**changes: Any) -> V6BasketResultEvent:
    body = basket_result(INTENT_ID, "2026-09-17T13:30:00Z", 16.0) | changes
    return V6BasketResultEvent.model_validate_json(json.dumps(body))


def rows(db: Path) -> list[tuple]:
    with sqlite3.connect(str(db)) as conn:
        return conn.execute("SELECT basket_id, version, net_pnl, created_at"
                            " FROM basket_results").fetchall()


def test_a_result_is_stored_once_with_its_first_receipt_time(tmp_path: Path) -> None:
    clock = FakeClock(epoch=NOW)
    journal = BasketJournal(tmp_path / "v6.db", clock)
    assert journal.record(event()) is True
    clock.advance(30)
    assert journal.record(event(net_pnl=99.0)) is False
    assert rows(tmp_path / "v6.db") == [
        (f"XAUUSD-V6B-{INTENT_ID}", "v6", 16.0, "2026-09-17T13:00:01.500000+00:00")]
    assert received_at_utc(float(1_789_650_000)) == "2026-09-17T13:00:00+00:00"


def test_the_journal_shares_the_v1_v5_table(tmp_path: Path) -> None:
    legacy = Ledger(str(tmp_path / "shared.db"))
    try:
        assert BasketJournal(tmp_path / "shared.db", FakeClock(epoch=NOW)).record(event())
    finally:
        legacy.conn.close()
    assert rows(tmp_path / "shared.db")[0][:3] == (f"XAUUSD-V6B-{INTENT_ID}", "v6", 16.0)


def test_other_strategies_are_refused(tmp_path: Path) -> None:
    v5 = BasketResultEvent.model_validate_json(json.dumps(
        basket_result(INTENT_ID, "2026-09-17T13:30:00Z", 1.0) | {"version": "v5"}))
    with pytest.raises(ValueError, match="not a V6 basket result"):
        BasketJournal(tmp_path / "v6.db", FakeClock(epoch=NOW)).record(v5)


@pytest.mark.parametrize("changes", [
    {"version": "v5"}, {"side": "none"}, {"basket_id": "bad id with spaces"},
    {"close_reason": "tp"}, {"symbol": "X"}, {"note": "extra"}, {"closed_at_utc": "<b>"},
    {"bursts": 1.0},
])
def test_the_v6_schema_is_strict(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        event(**changes)


def test_the_v6_schema_refuses_non_finite_numbers() -> None:
    body = json.dumps(basket_result(INTENT_ID, "x", 1.0)).replace('"net_pnl": 1.0',
                                                                  '"net_pnl": NaN')
    with pytest.raises(ValidationError):
        V6BasketResultEvent.model_validate_json(body)
    assert math.isfinite(event().net_pnl)


def test_only_an_enter_cycle_names_a_published_intent() -> None:
    held = hold_result()
    assert held.intent_id is None
    with pytest.raises(ValueError, match="only an ENTER cycle"):
        CycleResult(**{**held.__dict__, "intent_id": INTENT_ID})


def test_a_signed_result_replaces_a_forged_legacy_row_but_never_the_reverse(
        tmp_path: Path) -> None:
    legacy = Ledger(str(tmp_path / "shared.db"))
    forged = BasketResultEvent.model_validate_json(json.dumps(
        basket_result(INTENT_ID, "2026-09-17T13:30:00Z", 0.0) | {"version": "v5"}))
    try:
        legacy.write_basket_result(forged)
        journal = BasketJournal(tmp_path / "shared.db", FakeClock(epoch=NOW))
        assert journal.record(event(net_pnl=-10.0)) is True
        legacy.write_basket_result(forged)
    finally:
        legacy.conn.close()
    assert rows(tmp_path / "shared.db") == [
        (f"XAUUSD-V6B-{INTENT_ID}", "v6", -10.0, "2026-09-17T13:00:01.500000+00:00")]
