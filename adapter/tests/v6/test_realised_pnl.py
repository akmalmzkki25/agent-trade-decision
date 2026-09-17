"""
Reading V6 results from the journal and summing them per breaker period.

Wednesday 2026-09-16 13:45 UTC is `AS_OF`: its day started at midnight, its ISO
week on Monday 2026-09-14 and its month on 2026-09-01.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Final

import pytest

from app.v6.config import V6Settings
from app.v6.cycle_types import candidate_id_for
from app.v6.learning.outcomes import OutcomeRecord
from app.v6.ledger_v6 import LedgerV6
from app.v6.risk.breakers import REASON_V6_PNL, evaluate_breakers
from app.v6.runtime.realised_pnl import (
    OutcomeReader, RealisedPnl, RealisedPnlReader, period_bounds, realised_pnl, window_start,
)
from app.v6.schemas.intent import basket_id_for

from .cycle_fixtures_v6 import enter_result
from .payloads_v6 import BAR_OPEN, M15
from .test_outcomes import (  # noqa: F401 - `journal` is a fixture
    CLOSE, DAY, FIRST, LOGIN, OTHER_LOGIN, SECOND, THIRD, Journal, _new_intent, event,
    intent_record, iso, journal, linked, result, seed_trade, store_result, unlinked,
)

AS_OF: Final[int] = CLOSE + 3600
DAY_START: Final[int] = 1_789_516_800          # Wednesday 2026-09-16 00:00 UTC
WEEK_START: Final[int] = DAY_START - 2 * DAY   # Monday 2026-09-14
MONTH_START: Final[int] = WEEK_START - 13 * DAY  # Tuesday 2026-09-01
OCTOBER: Final[int] = MONTH_START + 30 * DAY
FOURTH: Final[str] = "d4e5f6g7h2i3"
BAD_BASKET: Final[str] = "XAUUSD-V6B-zzzzzzzzzzzz"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def undated(intent_id: str, net_pnl: float) -> OutcomeRecord:
    unknown_time = replace(result(intent_id, net_pnl=net_pnl), closed_at_utc="",
                           received_at_utc="")
    return OutcomeRecord(unknown_time, intent_record(intent_id, cycle_id=f"c-{intent_id}"),
                         None, LOGIN)


def mixed_outcomes() -> tuple[OutcomeRecord, ...]:
    return (
        linked(FIRST, -10.4),                                    # today
        linked(SECOND, 4.0, closed_at=CLOSE - DAY),              # Tuesday
        linked(THIRD, -6.0, closed_at=CLOSE - 6 * DAY),          # last Thursday
        linked(FOURTH, -100.0, closed_at=MONTH_START - 3600),    # August
        linked(FIRST, -50.0, login=OTHER_LOGIN),                 # another account
        unlinked(-7.0), unlinked(9.0),                           # account unknown
        linked(SECOND, -1.0, login=None, closed_at=WEEK_START + 3600),
        undated(THIRD, -3.0), undated(FOURTH, 2.0),
    )


def insert_bad_row(journal: Journal) -> None:
    journal.v1.conn.execute(
        "INSERT INTO basket_results (basket_id, version, net_pnl, created_at)"
        " VALUES (?, 'v6', 'lots', ?)", (BAD_BASKET, iso(CLOSE + 9)))


# --- periods ---------------------------------------------------------------------------------
@pytest.mark.parametrize(("scope", "epoch", "bounds"), [
    ("daily", AS_OF, (DAY_START, DAY_START + DAY)),
    ("daily", DAY_START, (DAY_START, DAY_START + DAY)),
    ("weekly", AS_OF, (WEEK_START, WEEK_START + 7 * DAY)),
    ("weekly", WEEK_START - 1, (WEEK_START - 7 * DAY, WEEK_START)),
    ("monthly", AS_OF, (MONTH_START, OCTOBER)),
    ("monthly", OCTOBER - 1, (MONTH_START, OCTOBER)),
    ("monthly", 1_830_297_599, (1_827_619_200, 1_830_297_600)),   # December 2027
    ("monthly", 1_835_481_599, (1_832_976_000, 1_835_481_600)),   # 29 February 2028
])
def test_period_bounds_follow_the_breaker_periods(scope: str, epoch: int,
                                                  bounds: tuple[int, int]) -> None:
    assert period_bounds(scope, epoch) == bounds  # type: ignore[arg-type]


def test_period_arguments_are_checked() -> None:
    assert window_start(AS_OF) == MONTH_START
    assert window_start(MONTH_START + 3600) == MONTH_START - DAY   # the week began in August
    with pytest.raises(ValueError, match="scope"):
        period_bounds("hourly", AS_OF)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        period_bounds("daily", 1.5)  # type: ignore[arg-type]


# --- summing ---------------------------------------------------------------------------------
def test_realised_pnl_sums_one_login_and_fails_closed_on_unknown_links() -> None:
    pnl = realised_pnl(mixed_outcomes(), LOGIN, AS_OF)

    daily, weekly, monthly = (pnl.period(scope) for scope in ("daily", "weekly", "monthly"))
    assert (daily.realized, daily.trades) == (Decimal("-13.4"), 2)
    assert (daily.unattributed_loss, daily.unattributed_trades) == (Decimal(-7), 1)
    assert (weekly.realized, weekly.trades, weekly.unattributed_loss) == (
        Decimal("-9.4"), 3, Decimal(-8))
    assert (monthly.realized, monthly.trades, monthly.unattributed_trades) == (
        Decimal("-15.4"), 4, 2)
    assert (daily.period_key, weekly.period_key, monthly.period_key) == (
        "2026-09-16", "2026-W38", "2026-09")
    assert [pnl.breaker_realized(s) for s in ("daily", "weekly", "monthly")] == [
        -20.4, -17.4, -23.4]
    other = realised_pnl(mixed_outcomes(), OTHER_LOGIN, AS_OF).period("daily")
    assert (other.realized, other.base) == (Decimal(-50), Decimal(-57))


@pytest.mark.parametrize(("ea_today", "expected"), [
    (-25.0, [-25.0, -22.0, -28.0]),    # the EA knows a loss the journal does not have yet
    (-13.4, [-20.4, -17.4, -23.4]),    # the EA does not see the unattributed loss
    (5.0, [-20.4, -17.4, -23.4]),      # a profit never loosens the journal figure
])
def test_the_ea_day_only_ever_adds_a_missing_loss(ea_today: float,
                                                  expected: list[float]) -> None:
    pnl = realised_pnl(mixed_outcomes(), LOGIN, AS_OF)
    assert [pnl.breaker_realized(s, ea_today) for s in ("daily", "weekly", "monthly")] == expected


def test_a_loss_seen_by_both_the_ea_and_the_journal_counts_once() -> None:
    pnl = realised_pnl((unlinked(-10.0),), LOGIN, AS_OF)
    assert pnl.breaker_realized("daily", -10.0) == pnl.breaker_realized("weekly", -10.0) == -10.0


def test_realised_losses_trip_the_v6_breaker_of_a_large_account() -> None:
    settings = V6Settings(_env_file=None)
    pnl = realised_pnl((linked(FIRST, -200.0, closed_at=CLOSE - 6 * DAY),), LOGIN, AS_OF)

    inputs = pnl.breaker_inputs(equity=10_000.0, floating_v6=0.0, daily_start=10_000.0,
                                weekly_start=10_000.0, monthly_start=10_200.0)
    evaluation = evaluate_breakers(inputs, settings)

    assert [(c.scope, c.reason) for c in evaluation.breaches] == [("monthly", REASON_V6_PNL)]
    assert inputs.as_of_epoch == AS_OF
    lagged = pnl.period_inputs(daily_start=1.0, weekly_start=1.0, monthly_start=1.0,
                               ea_realized_today=-4.0)
    assert [p.realized_v6 for p in lagged] == [-4.0, -4.0, -204.0]
    with pytest.raises(ValueError, match="start_equity"):
        pnl.period_inputs(daily_start=0.0, weekly_start=1.0, monthly_start=1.0)


@pytest.mark.parametrize("login", ["12a45", "", "1" * 21, 12345])
def test_logins_are_digits(login: object) -> None:
    with pytest.raises(ValueError, match="login"):
        realised_pnl((), login, AS_OF)  # type: ignore[arg-type]


def test_bad_breaker_arguments_are_refused() -> None:
    pnl = realised_pnl((), LOGIN, AS_OF)
    with pytest.raises(ValueError, match="finite"):
        pnl.breaker_realized("daily", float("nan"))
    with pytest.raises(ValueError, match="finite"):
        pnl.breaker_realized("daily", True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="scope"):
        pnl.period("hourly")  # type: ignore[arg-type]
    document = pnl.to_dict()
    assert json.loads(json.dumps(document)) == document
    assert document["periods"]["weekly"] == {  # type: ignore[index]
        "period_key": "2026-W38", "start_epoch": WEEK_START, "end_epoch": WEEK_START + 7 * DAY,
        "trades": 0, "realized": 0.0, "unattributed_loss": 0.0, "unattributed_trades": 0,
        "breaker_realized": 0.0}


# --- the journal reader ----------------------------------------------------------------------
def test_reader_joins_results_intents_labels_and_accounts(journal: Journal) -> None:
    first = seed_trade(journal, FIRST, bar_open=BAR_OPEN - DAY, net_pnl=-10.4, label_r=None)
    second = seed_trade(journal, SECOND, login=OTHER_LOGIN)
    store_result(journal, event(THIRD, net_pnl=-5.0), CLOSE + 60)
    store_result(journal, event(basket_id="XAUUSD-v6b-" + FIRST, net_pnl=1.0), CLOSE + 120)
    store_result(journal, event(basket_id="XAUUSD-V5B-9", version="v5"), CLOSE + 180)
    reader = OutcomeReader(journal.path)

    recent = reader.recent(10)

    assert [r.result.basket_id for r in recent.records] == [
        "XAUUSD-v6b-" + FIRST, basket_id_for("XAUUSD", THIRD), second, first]
    assert recent.invalid == ()
    lower, orphan, other, older = recent.records
    assert (lower.intent, orphan.intent, orphan.login) == (None, None, None)
    assert other.login == OTHER_LOGIN and other.label_r == Decimal("1.9")
    assert other.intent is not None and other.intent.basket_id == second
    assert (older.login, older.label_r, older.r_multiple) == (LOGIN, None, Decimal(-1))
    assert older.label is not None and older.label.label_status == "pending"
    assert [r.result.basket_id for r in reader.since(BAR_OPEN).records] == [
        "XAUUSD-v6b-" + FIRST, basket_id_for("XAUUSD", THIRD), second]
    found = reader.for_basket(first)
    assert found is not None and found.intent is not None and found.intent.intent_id == FIRST
    assert reader.for_basket(BAD_BASKET) is None
    assert [r.result.basket_id for r in reader.recent(1).records] == ["XAUUSD-v6b-" + FIRST]


def test_a_pending_label_and_a_missing_snapshot_still_link(journal: Journal) -> None:
    candidate_id = candidate_id_for("displacement", "buy", BAR_OPEN)
    cycle = enter_result("c-" + FIRST, candidate_ids=(candidate_id,))
    assert journal.cycles.record_cycle(cycle, float(BAR_OPEN + M15))
    journal.cycles.intents.insert(_new_intent(FIRST, "c-" + FIRST, BAR_OPEN))
    store_result(journal, event(FIRST), CLOSE)

    (record,) = OutcomeReader(journal.path).recent().records

    assert record.label is not None and record.label.label_status == "pending"
    assert record.intent is not None and record.intent.status == "PUBLISHED"
    assert (record.label_r, record.login) == (None, None)


def test_reader_without_a_basket_table_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "v6-only.db"
    LedgerV6(path).close()
    reader = OutcomeReader(path)
    assert reader.recent().records == () and reader.since(BAR_OPEN).invalid == ()
    assert reader.for_basket(basket_id_for("XAUUSD", FIRST)) is None
    assert RealisedPnlReader(path).read(LOGIN, AS_OF).breaker_realized("monthly") == 0.0
    with pytest.raises(ValueError, match="limit"):
        reader.recent(0)
    with pytest.raises(TypeError, match="since_epoch"):
        reader.since(True)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_value", ["'lots'", "9e999"])
def test_reader_reports_unreadable_rows_without_summing_them(
        journal: Journal, caplog: pytest.LogCaptureFixture, bad_value: str) -> None:
    seed_trade(journal, FIRST)
    journal.v1.conn.execute(
        f"INSERT INTO basket_results (basket_id, version, net_pnl, created_at)"
        f" VALUES (?, 'v6', {bad_value}, ?)", (BAD_BASKET, iso(CLOSE + 9)))
    journal.v1.conn.execute(
        "INSERT INTO basket_results (basket_id, version) VALUES ('bare-row', 'v6')")

    with caplog.at_level(logging.WARNING):
        found = OutcomeReader(journal.path).since(BAR_OPEN)

    assert found.invalid == (BAD_BASKET,) and "unreadable" in caplog.text
    bare = next(r for r in found.records if r.result.basket_id == "bare-row")
    assert (bare.kind, bare.result.closed_epoch, bare.result.side) == ("flat", None, "")
    assert len(found.records) == 2


# --- the breaker facade ----------------------------------------------------------------------
def _seed_week(journal: Journal) -> None:
    seed_trade(journal, FIRST, bar_open=BAR_OPEN - DAY, net_pnl=4.0)
    seed_trade(journal, SECOND, net_pnl=-10.4)
    seed_trade(journal, THIRD, bar_open=BAR_OPEN - 6 * DAY, login=OTHER_LOGIN, net_pnl=-30.0)
    store_result(journal, event(FOURTH, net_pnl=-7.0), CLOSE + 60)


def test_the_facade_reads_each_login_from_the_journal(journal: Journal) -> None:
    _seed_week(journal)
    reader = RealisedPnlReader(journal.path)

    mine, theirs = reader.read(LOGIN, AS_OF), reader.read(OTHER_LOGIN, AS_OF)

    assert [(p.realized, p.trades) for p in mine.periods] == [
        (Decimal("-10.4"), 1), (Decimal("-6.4"), 2), (Decimal("-6.4"), 2)]
    assert [p.unattributed_loss for p in mine.periods] == [Decimal(-7)] * 3
    assert [p.realized for p in theirs.periods] == [Decimal(0), Decimal(0), Decimal(-30)]
    assert theirs.breaker_realized("monthly") == -37.0


def test_the_facade_fails_closed_on_an_unreadable_row(journal: Journal) -> None:
    _seed_week(journal)
    insert_bad_row(journal)
    with pytest.raises(ValueError, match="unreadable"):
        RealisedPnlReader(journal.path).read(LOGIN, AS_OF)
    with pytest.raises(ValueError, match="login"):
        RealisedPnlReader(journal.path).read("abc", AS_OF)


@pytest.mark.anyio
async def test_the_facade_reads_off_the_event_loop(journal: Journal) -> None:
    _seed_week(journal)
    reader = RealisedPnlReader(journal.path)
    pnl = await reader.for_login(LOGIN, AS_OF)
    assert isinstance(pnl, RealisedPnl) and pnl == reader.read(LOGIN, AS_OF)


def test_a_forged_legacy_result_cannot_hide_a_v6_loss(journal: Journal) -> None:
    seed_trade(journal, FIRST, net_pnl=-10.4)
    journal.v1.write_basket_result(event(FIRST, net_pnl=0.0, version="v5"))
    assert RealisedPnlReader(journal.path).read(LOGIN, AS_OF).breaker_realized("weekly") == -10.4
