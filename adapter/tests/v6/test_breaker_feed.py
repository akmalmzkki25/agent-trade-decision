"""Breaker inputs owned by the runtime: account-mark anchors, snapshot and poll paths."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.v6.deliberation.context_builder import ContextRequest, build_context, load_bars
from app.v6.ledger_cycles import LedgerCycles
from app.v6.ledger_v6 import AccountMark, LedgerV6
from app.v6.risk.breakers import PeriodInput, account_usable, inputs_from_context, period_key
from app.v6.runtime.breaker_feed import (
    INPUTS_UNUSABLE, LEDGER_UNAVAILABLE, BreakerFeed, DayFacts, MarksReader, PeriodAnchors,
    _unavailable,
)
from app.v6.schemas.intent import PollRequest

from . import engine_fixtures_v6 as ef
from .payloads_v6 import as_poll, poll_payload

DAY_START = ef.AS_OF - ef.AS_OF % ef.DAY
WEEK_START = DAY_START - 3 * ef.DAY          # Monday 2026-09-14


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def stores(tmp_path: Path) -> Iterator[tuple[LedgerV6, LedgerCycles]]:
    ledger = LedgerV6(tmp_path / "v6.db")
    cycles = LedgerCycles(tmp_path / "v6.db")
    yield ledger, cycles
    cycles.close()
    ledger.close()


def feed(stores: tuple[LedgerV6, LedgerCycles], **overrides: Any) -> BreakerFeed:
    ledger, cycles = stores
    return BreakerFeed(ledger=cycles, marks=MarksReader(ledger.path),
                       settings=ef.settings(**overrides), clock=ef.clock_at())


LOGIN = "12345"                               # the login of every fixture payload


def mark(ledger: LedgerV6, at: int, equity: float, login: str = LOGIN) -> None:
    ledger.record_account_mark(AccountMark(
        observed_at=float(at), login=login, trade_mode="DEMO", equity=equity,
        balance=equity, floating_pnl_v6=0.0, open_v6_positions=0))


def account_poll(login: str, equity: float) -> PollRequest:
    return as_poll({**poll_payload(equity=equity), "login": login, "balance": equity,
                    "floating_pnl_v6": 0.0})


def day_facts(start_equity: float = 2000.0, realized: float = 0.0, *, login: str = LOGIN,
              day_start: int = DAY_START) -> DayFacts:
    return DayFacts(login=login, day_start_epoch=day_start, day_start_equity=start_equity,
                    realized_today=realized)


def context(**changes: Any):
    snapshot = ef.engine_snapshot(**changes)
    request = ContextRequest(cycle_id="c-1", snapshot=snapshot, received_at=ef.RECEIVED)
    bars = load_bars(ef.MemoryBars({}), snapshot)
    return build_context(request, bars, ef.settings(), ef.RECEIVED)


def test_anchors_are_the_first_marks_of_each_period(stores) -> None:
    ledger, _ = stores
    mark(ledger, WEEK_START - 60, 1500.0)          # last week: ignored
    mark(ledger, WEEK_START + 3600, 2400.0)
    mark(ledger, DAY_START + 60, 2100.0)
    mark(ledger, ef.AS_OF + 600, 1.0)              # after as_of: ignored
    anchors = MarksReader(ledger.path).anchors(ef.AS_OF, LOGIN)
    assert (anchors.daily, anchors.weekly) == (2100.0, 2400.0)
    assert anchors.monthly == 1500.0
    assert MarksReader(ledger.path).anchors(ef.AS_OF, "999") == PeriodAnchors(
        as_of_epoch=ef.AS_OF, daily=None, weekly=None, monthly=None)


def test_the_snapshot_path_evaluates_and_persists_a_weekly_trip(stores) -> None:
    ledger, cycles = stores
    mark(ledger, WEEK_START + 3600, 2200.0)       # 2010.5 is 8.6% below: weekly breach
    status = feed(stores).context_status(context())
    assert status.tripped
    week = period_key("weekly", ef.AS_OF)
    assert f"weekly:{week}" in status.labels
    # The month started with the same mark, but 8.6% is inside the 10% monthly limit.
    assert [record.scope for record in cycles.active_breakers()] == ["weekly"]


def test_the_snapshot_path_is_healthy_without_marks(stores) -> None:
    status = feed(stores).context_status(context())
    assert not status.tripped
    assert status.remaining_loss_usd == 60.0


def test_unusable_inputs_fail_closed(stores) -> None:
    broken = context(day={"day_start_equity": 0.0, "realized_today": 0.0, "trades_today": 0})
    status = feed(stores).context_status(broken)
    assert status.tripped and status.unavailable == INPUTS_UNUSABLE


def test_an_unreadable_marks_table_fails_closed(stores, tmp_path: Path) -> None:
    _, cycles = stores
    missing = BreakerFeed(ledger=cycles, marks=MarksReader(str(tmp_path / "none.db")),
                          settings=ef.settings(), clock=ef.clock_at())
    status = missing.context_status(context())
    assert status.unavailable == LEDGER_UNAVAILABLE


def test_unavailable_survives_a_broken_ledger(monkeypatch: pytest.MonkeyPatch,
                                              stores) -> None:
    _, cycles = stores

    def broken() -> Any:
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(cycles, "active_breakers", broken)
    assert _unavailable(cycles, "x").unavailable == LEDGER_UNAVAILABLE


@pytest.mark.anyio
async def test_the_async_context_path(stores) -> None:
    status = await feed(stores).for_context(context())
    assert status.evaluation is not None


# --- poll path ------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_the_poll_path_needs_a_day_anchor(stores) -> None:
    poll = as_poll(poll_payload())
    assert await feed(stores).for_poll(poll, ef.RECEIVED, None) is None
    stale_day = day_facts(realized=-5.0, day_start=DAY_START - ef.DAY)
    assert feed(stores).poll_status(poll, ef.RECEIVED, stale_day) is None


def test_the_poll_path_prefers_the_eas_day_and_trips_on_a_drop(stores) -> None:
    ledger, cycles = stores
    mark(ledger, DAY_START + 60, 1000.0)
    today = day_facts(realized=-10.0)
    healthy = feed(stores).poll_status(as_poll(poll_payload(equity=1990.0)), ef.RECEIVED, today)
    assert not healthy.tripped
    # realised -10 plus floating -10 (the poll's own P&L) against the $2,000 basis
    assert healthy.evaluation.check("daily").v6_loss_pct == pytest.approx(1.0)
    dropped = feed(stores).poll_status(as_poll(poll_payload(equity=1900.0)), ef.RECEIVED, today)
    assert dropped.tripped
    assert "daily:" + period_key("daily", ef.AS_OF) in dropped.labels
    assert cycles.active_breakers()


def test_the_poll_path_falls_back_to_the_first_mark(stores) -> None:
    ledger, _ = stores
    mark(ledger, DAY_START + 60, 2000.0)
    status = feed(stores).poll_status(as_poll(poll_payload()), ef.RECEIVED, None)
    assert status is not None and not status.tripped


def test_day_facts_come_from_the_context() -> None:
    facts = DayFacts.from_context(context())
    assert facts == day_facts()


# --- one account at a time (finding: anchors mixed MT5 logins) -----------------------------
def test_a_new_login_is_not_measured_against_the_old_accounts_equity(stores) -> None:
    ledger, cycles = stores
    mark(ledger, WEEK_START + 3600, 100_000.0, login="111")
    old_day = day_facts(100_000.0, login="111")
    switched = account_poll("222", 2000.0)

    assert feed(stores).poll_status(switched, ef.RECEIVED, old_day) is None
    mark(ledger, DAY_START + 60, 2000.0, login="222")
    status = feed(stores).poll_status(switched, ef.RECEIVED, old_day)

    assert status is not None and not status.tripped
    assert status.evaluation.check("weekly").equity_drawdown_pct == 0.0
    assert cycles.active_breakers() == ()


def test_another_logins_small_equity_cannot_hide_a_real_loss(stores) -> None:
    ledger, cycles = stores
    mark(ledger, WEEK_START + 60, 2000.0, login="111")
    mark(ledger, WEEK_START + 3600, 10_000.0, login="222")
    poll = account_poll("222", 8900.0)

    status = feed(stores).poll_status(poll, ef.RECEIVED, day_facts(8950.0, login="222"))

    assert status is not None and status.tripped
    assert {"weekly:" + period_key("weekly", ef.AS_OF),
            "monthly:" + period_key("monthly", ef.AS_OF)} <= set(status.labels)
    assert sorted(r.scope for r in cycles.active_breakers()) == ["monthly", "weekly"]


def test_the_snapshot_path_reads_only_its_own_logins_marks(stores) -> None:
    ledger, cycles = stores
    mark(ledger, WEEK_START + 60, 50_000.0, login="999")   # would read as a 96% drop
    status = feed(stores).context_status(context())
    assert not status.tripped and cycles.active_breakers() == ()


# --- unusable readings (finding: a zero-equity poll tripped every breaker) -----------------
@pytest.mark.parametrize(("login", "equity"), [(LOGIN, 0.0), ("0", 2000.0)])
def test_an_unconnected_terminal_poll_fails_closed_without_a_trip(stores, login: str,
                                                                  equity: float) -> None:
    ledger, cycles = stores
    mark(ledger, DAY_START + 60, 2000.0)
    poll = account_poll(login, equity)

    status = feed(stores).poll_status(poll, ef.RECEIVED, day_facts())

    assert status is not None and status.tripped
    assert status.unavailable == INPUTS_UNUSABLE and status.evaluation is None
    assert cycles.active_breakers() == ()


def test_an_unconnected_terminal_snapshot_fails_closed_without_a_trip(stores) -> None:
    _, cycles = stores
    base = context()
    broken = replace(base, account=base.account.model_copy(update={"equity": 0.0}))
    status = feed(stores).context_status(broken)
    assert status.unavailable == INPUTS_UNUSABLE and cycles.active_breakers() == ()


@pytest.mark.parametrize("account", [{"equity": 0.0}, {"login": "0"}, {"login": "000"}])
def test_an_unconnected_account_is_unusable_not_a_total_loss(account: dict[str, Any]) -> None:
    base = context()
    unconnected = replace(base, account=base.account.model_copy(update=account))
    weekly, monthly = PeriodInput("weekly", 2000.0, 0.0), PeriodInput("monthly", 2000.0, 0.0)

    with pytest.raises(ValueError, match="unusable"):
        inputs_from_context(unconnected, weekly=weekly, monthly=monthly)


@pytest.mark.parametrize(("login", "equity", "usable"), [
    ("12345", 2000.0, True), ("10", 0.01, True), ("0", 2000.0, False),
    ("12345", 0.0, False), ("12345", float("nan"), False), ("12345", -1.0, False),
])
def test_account_usable(login: str, equity: float, usable: bool) -> None:
    assert account_usable(login, equity) is usable
