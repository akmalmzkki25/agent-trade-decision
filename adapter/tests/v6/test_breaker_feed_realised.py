"""The breaker feed's realised V6 P&L: basket results per period, merged with the EA's day."""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

from app.v6.ledger_cycles import LedgerCycles
from app.v6.ledger_v6 import AccountMark, LedgerV6
from app.v6.risk.breakers import SCOPES, period_key
from app.v6.runtime.breaker_feed import INPUTS_UNUSABLE, BreakerFeed, DayFacts, MarksReader
from app.v6.runtime.realised_pnl import PeriodPnl, RealisedPnl, period_bounds

from . import engine_fixtures_v6 as ef
from .payloads_v6 import as_poll, poll_payload
from .test_breaker_feed import context

LOGIN = "12345"
DAY_START = ef.AS_OF - ef.AS_OF % ef.DAY


class FixedPnl:
    """A RealisedPnlReader stand-in: the same realised figure in every period."""

    def __init__(self, realized: str) -> None:
        self.realized = Decimal(realized)
        self.calls: list[tuple[str, int]] = []

    def read(self, login: str, as_of_epoch: int) -> RealisedPnl:
        self.calls.append((login, as_of_epoch))
        periods = tuple(
            PeriodPnl(scope=scope, period_key=period_key(scope, as_of_epoch),
                      start_epoch=period_bounds(scope, as_of_epoch)[0],
                      end_epoch=period_bounds(scope, as_of_epoch)[1],
                      realized=self.realized, trades=1)
            for scope in SCOPES)
        return RealisedPnl(login=login, as_of_epoch=as_of_epoch, periods=periods)


class BrokenPnl:
    def read(self, login: str, as_of_epoch: int) -> RealisedPnl:
        raise ValueError("1 V6 basket results in the breaker window are unreadable")


@pytest.fixture
def stores(tmp_path: Path) -> Iterator[tuple[LedgerV6, LedgerCycles]]:
    ledger = LedgerV6(tmp_path / "v6.db")
    cycles = LedgerCycles(tmp_path / "v6.db")
    ledger.record_account_mark(AccountMark(
        observed_at=float(DAY_START + 60), login=LOGIN, trade_mode="DEMO", equity=2000.0,
        balance=2000.0, floating_pnl_v6=0.0, open_v6_positions=0))
    yield ledger, cycles
    cycles.close()
    ledger.close()


def feed(stores: tuple[LedgerV6, LedgerCycles], realised: object) -> BreakerFeed:
    ledger, cycles = stores
    return BreakerFeed(ledger=cycles, marks=MarksReader(ledger.path), settings=ef.settings(),
                       clock=ef.clock_at(), realised=realised)  # type: ignore[arg-type]


def test_realised_results_count_in_every_period(stores) -> None:
    realised = FixedPnl("-80")
    status = feed(stores, realised).poll_status(as_poll(poll_payload()), ef.RECEIVED, None)
    assert status is not None and status.tripped
    # -(realised -80 + floating 10.5) = 69.5 against the $2,000 basis
    checks = {scope: status.evaluation.check(scope) for scope in SCOPES}
    assert {scope: check.v6_loss_pct for scope, check in checks.items()} == {
        "daily": 3.475, "weekly": 3.475, "monthly": 3.475}
    assert [check.breached for check in checks.values()] == [True, False, False]
    assert realised.calls == [(LOGIN, int(ef.RECEIVED))]


def test_the_eas_loss_counts_before_its_result_arrives(stores) -> None:
    today = DayFacts(login=LOGIN, day_start_epoch=DAY_START, day_start_equity=2000.0,
                     realized_today=-30.0)
    status = feed(stores, FixedPnl("0")).poll_status(as_poll(poll_payload()), ef.RECEIVED,
                                                     today)
    assert status is not None
    # the EA already knows -30 the journal does not: every period carries it
    assert status.evaluation.check("monthly").v6_loss_pct == pytest.approx(0.975)


def test_an_unreadable_result_fails_closed_on_both_paths(stores) -> None:
    ledger, cycles = stores
    broken = feed(stores, BrokenPnl())
    polled = broken.poll_status(as_poll(poll_payload()), ef.RECEIVED, None)
    assert polled is not None and polled.unavailable == INPUTS_UNUSABLE
    assert broken.context_status(context()).unavailable == INPUTS_UNUSABLE
    assert cycles.active_breakers() == ()


def test_the_default_reader_uses_the_marks_database(stores) -> None:
    ledger, cycles = stores
    default = BreakerFeed(ledger=cycles, marks=MarksReader(ledger.path),
                          settings=ef.settings(), clock=ef.clock_at())
    status = default.poll_status(as_poll(poll_payload()), ef.RECEIVED, None)
    assert status is not None and not status.tripped
