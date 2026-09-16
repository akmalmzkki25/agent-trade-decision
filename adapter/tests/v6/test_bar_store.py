from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.v6.ledger_v6 import LedgerV6
from app.v6.market import bar_store
from app.v6.market.bar_store import BarStore, Coverage
from app.v6.types import Bar

M5 = 300
M15 = 900
DAY = 86_400
DAY0 = 20_712 * DAY  # a UTC midnight
BASE_T = DAY0 + 12 * 3600


def _bar(t: int, c: float = 2400.0) -> Bar:
    return Bar(t=t, o=c, h=c + 1.0, l=c - 1.0, c=c, tv=5, spr=20)


def _bars(start: int, count: int, step: int = M15) -> tuple[Bar, ...]:
    return tuple(_bar(start + i * step, c=2400.0 + i) for i in range(count))


def _days(first_day: int, days: int, step: int) -> tuple[Bar, ...]:
    return _bars(DAY0 + first_day * DAY, days * (DAY // step), step=step)


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[LedgerV6]:
    led = LedgerV6(str(tmp_path / "bars.db"))
    yield led
    led.close()


@pytest.fixture
def store(ledger: LedgerV6) -> BarStore:
    return BarStore(ledger)


class _FailingLedger:
    """Stands in for a ledger whose disk write fails."""

    def upsert_bars(self, tf: str, bars: tuple[Bar, ...]) -> int:
        raise sqlite3.OperationalError("database is locked")


# --- ingest / latest ---------------------------------------------------------

def test_ingest_dedupes_by_time_and_last_occurrence_wins(store: BarStore, ledger: LedgerV6) -> None:
    first, second = _bar(BASE_T, 2400.0), _bar(BASE_T + M15, 2401.0)
    revised = _bar(BASE_T, 2399.0)
    assert store.ingest("M15", (first, second, revised)) == 2
    assert store.latest("M15", 2) == (revised, second)
    assert ledger.load_bars("M15", 0, 10) == (revised, second)


def test_out_of_order_ingest_is_kept_sorted(store: BarStore) -> None:
    bars = _bars(BASE_T, 6)
    store.ingest("M15", (bars[4], bars[1], bars[5]))
    store.ingest("M15", (bars[3], bars[0], bars[2]))
    assert store.latest("M15", 6) == bars
    assert store.latest("M15", 2) == bars[4:]


def test_ingest_replaces_rather_than_mutates_returned_tuples(store: BarStore) -> None:
    bars = _bars(BASE_T, 4)
    store.ingest("M15", bars[:2])
    before = store.latest("M15", 2)
    store.ingest("M15", bars[2:])
    assert before == bars[:2]
    assert store.latest("M15", 2) == bars[2:]


def test_cache_is_bounded_and_latest_falls_back_to_the_ledger(ledger: LedgerV6) -> None:
    store = BarStore(ledger, cache_limit=5)
    bars = _bars(BASE_T, 8)
    store.ingest("M15", bars)
    assert store.cached_count("M15") == 5
    assert store.latest("M15", 5) == bars[-5:]
    assert store.latest("M15", 8) == bars
    assert store.latest("M15", 50) == bars


def test_latest_on_an_empty_timeframe_is_empty(store: BarStore) -> None:
    assert store.latest("H1", 3) == ()
    assert store.ingest("H1", ()) == 0
    assert store.cached_count("H1") == 0


def test_invalid_arguments_are_rejected(ledger: LedgerV6, store: BarStore) -> None:
    with pytest.raises(ValueError):
        store.ingest("M2", (_bar(BASE_T),))
    with pytest.raises(ValueError):
        store.latest("M15", 0)
    with pytest.raises(ValueError):
        store.coverage("M15", BASE_T, lookback_s=0)
    with pytest.raises(ValueError):
        BarStore(ledger, cache_limit=0)
    with pytest.raises(ValueError):
        BarStore(ledger, cache_limit=bar_store.MAX_LOAD_BARS + 1)


def test_failed_persist_leaves_the_cache_untouched() -> None:
    store = BarStore(_FailingLedger())  # type: ignore[arg-type]
    with pytest.raises(sqlite3.OperationalError):
        store.ingest("M15", _bars(BASE_T, 3))
    assert store.cached_count("M15") == 0


# --- warm-up -----------------------------------------------------------------

def test_warm_hydrates_the_cache_from_the_ledger(ledger: LedgerV6, tmp_path: Path) -> None:
    ledger.upsert_bars("M15", _bars(BASE_T, 7))
    ledger.upsert_bars("M5", _bars(BASE_T, 4, step=M5))
    store = BarStore(ledger, cache_limit=6)
    counts = store.warm()
    assert counts == {"M1": 0, "M5": 4, "M15": 6, "H1": 0, "D1": 0}
    with pytest.raises(TypeError):
        counts["M1"] = 1  # type: ignore[index]
    ledger.close()
    # Served from memory: the closed connection is never touched.
    assert store.latest("M15", 6) == _bars(BASE_T, 7)[-6:]


def test_warm_merges_with_bars_ingested_earlier(ledger: LedgerV6) -> None:
    ledger.upsert_bars("M15", _bars(BASE_T, 3))
    store = BarStore(ledger)
    late = _bar(BASE_T + 3 * M15, 2500.0)
    store.ingest("M15", (late,))
    assert store.warm(("M15",)) == {"M15": 4}
    assert store.latest("M15", 4) == (*_bars(BASE_T, 3), late)


def test_warm_rejects_unknown_timeframes(store: BarStore) -> None:
    with pytest.raises(ValueError):
        store.warm(("M15", "W1"))


# --- coverage / warmth -------------------------------------------------------

def test_coverage_counts_closed_bars_inside_the_lookback(store: BarStore) -> None:
    assert store.coverage("M15", DAY0) == Coverage(0, None, None, 0)
    store.ingest("M15", _days(0, 2, M15))
    as_of = DAY0 + DAY + 10 * M15  # ten bars of day 1 have closed
    cov = store.coverage("M15", as_of)
    assert cov == Coverage(count=106, first_t=DAY0, last_t=DAY0 + DAY + 9 * M15, distinct_days=1)
    lookback = DAY - 60 * M15  # leaves 26 bars of day 0 and 10 of day 1
    narrow = store.coverage("M15", as_of, lookback_s=lookback)
    assert (narrow.count, narrow.first_t) == (36, as_of - lookback)
    assert narrow.distinct_days == 0


def test_partial_days_do_not_count_as_sessions(store: BarStore) -> None:
    sunday_open = DAY0 + 22 * 3600
    store.ingest("M15", _bars(sunday_open, 8))
    store.ingest("M15", _days(1, 1, M15))
    cov = store.coverage("M15", DAY0 + 2 * DAY)
    assert cov.count == 104
    assert cov.distinct_days == 1


def test_is_warm_requires_both_timeframes(store: BarStore) -> None:
    as_of = DAY0 + 10 * DAY
    assert store.is_warm(as_of) is False
    store.ingest("M15", _days(0, 10, M15))
    assert store.is_warm(as_of) is False
    store.ingest("M5", _days(8, 2, M5))
    assert store.is_warm(as_of) is False
    assert store.is_warm(as_of, min_m5_days=2) is True
    store.ingest("M5", _days(7, 1, M5))
    assert store.is_warm(as_of) is True
    assert store.is_warm(as_of, min_m15_days=11) is False


def test_is_warm_ignores_history_older_than_the_lookback(store: BarStore) -> None:
    store.ingest("M15", _days(0, 10, M15))
    store.ingest("M5", _days(7, 3, M5))
    assert store.is_warm(DAY0 + 10 * DAY) is True
    assert store.is_warm(DAY0 + 40 * DAY) is False
