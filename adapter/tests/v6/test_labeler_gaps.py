"""
Bar holes and the labeler: a missing span is DATA_GAP, never a wrong label.

Snapshots carry 4 h of M5 and the EA backfills only when it starts, so an adapter
outage leaves holes. Buckets where no quotes are expected (weekend, rollover,
the broker's daily 20:00-22:00 UTC gap) are closures, not holes.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.v6.clock import FakeClock
from app.v6.config import V6Settings
from app.v6.learning import (
    OUTCOME_DATA_GAP, REASON_BARRIER, REASON_DATA_GAP, LabelerConfig, label_candidate,
    label_pending_sync, uncovered_buckets,
)
from app.v6.ledger_cycles import LedgerCycles
from app.v6.ledger_v6 import LedgerV6
from app.v6.market.bar_store import BarStore
from app.v6.market.broker_hours import (
    DEFAULT_QUOTE_GAP, DailyUtcWindow, market_closed, parse_daily_window,
)

from .test_labeler import (
    AVAILABLE, CFG, DECISION, _at, _bar, _flat, _m5_of, _path, _record, _seed,
)

M1, M5 = 60, 300
HOLE = (DECISION, DECISION + 45 * M1)          # the adapter was down for 45 minutes
# The finding's trade: buy limit 4000, stop 3990, target 4020.
LIMIT = {"entry": 4000.0, "stop": 3990.0, "invalidation": 3990.0, "target": 4020.0}


def utc(*parts: int) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp())


def _rally() -> tuple:
    """3998 for the first 10 minutes (fills the limit), then a run to 4025."""
    early = tuple(_at(i, 3998.0, 3998.5, 3997.5, 3998.0) for i in range(10))
    late = tuple(_at(i, 4025.0, 4025.5, 4024.5, 4025.0) for i in range(10, 150))
    return early + late


def _without(bars: tuple, start: int, end: int) -> tuple:
    return tuple(b for b in bars if not start <= b.t < end)


# --- pure labeler -------------------------------------------------------------------
def test_a_hole_before_the_fill_is_a_data_gap_not_unfilled() -> None:
    m1 = _rally()
    full = label_candidate(_record(**LIMIT), m1, _m5_of(m1), CFG)
    assert (full.outcome, full.outcome_r) == ("tp", pytest.approx(1.96))

    holed = _without(m1, *HOLE)
    result = label_candidate(_record(**LIMIT), holed, _m5_of(holed), CFG)

    assert (result.label_status, result.outcome, result.reason) == (
        "unfillable", OUTCOME_DATA_GAP, REASON_DATA_GAP)
    assert result.outcome_r is None and result.data_gap
    # The unfilled reading depends only on the 30-minute fill window: six 5-minute buckets.
    assert f"first at {DECISION}" in result.detail and result.detail.startswith("6 ")


def test_a_hole_after_the_fill_is_a_data_gap_not_a_time_exit() -> None:
    m1 = _without(_path(_at(0, 4305.0, 4305.0, 4299.5, 4301.0)), DECISION + 60 * M1,
                  DECISION + 90 * M1)
    assert label_candidate(_record(), m1, _m5_of(m1), CFG).outcome == OUTCOME_DATA_GAP


def test_holes_after_the_outcome_is_known_change_nothing() -> None:
    target_hit = _path(_at(0, 4305.0, 4305.0, 4299.5, 4301.0), _at(10, 4305.0, 4320.0,
                                                                   4304.0, 4318.0))
    late_hole = _without(target_hit, DECISION + 60 * M1, DECISION + 120 * M1)
    result = label_candidate(_record(), late_hole, _m5_of(late_hole), CFG)
    assert (result.outcome, result.reason, result.resolved_at) == (
        "tp", REASON_BARRIER, DECISION + 11 * M1)

    never_filled = _without(_path(), CFG.pending_expiry_s + DECISION, AVAILABLE)
    unfilled = label_candidate(_record(), never_filled, _m5_of(never_filled), CFG)
    assert unfilled.outcome == "unfilled"


def test_the_brokers_quote_gap_is_a_closure_not_a_hole() -> None:
    decision = utc(2026, 9, 16, 19, 30)                  # Wednesday; gap 20:00-22:00 UTC
    record = _record(bar_t=decision - 900, available_from=decision + 150 * M1)
    m1 = tuple(_bar(decision + i * M1, 4305.0, 4305.0, 4299.5, 4301.0) for i in range(30))

    with_gap = label_candidate(record, m1, _m5_of(m1), CFG)
    assert (with_gap.outcome, with_gap.resolved_at) == ("time", decision + 30 * M1)

    no_gap = label_candidate(record, m1, _m5_of(m1), replace(CFG, quote_gap=None))
    assert no_gap.outcome == OUTCOME_DATA_GAP       # 20:00-21:00 is open market then


def test_uncovered_buckets_rules() -> None:
    m1 = _flat(DECISION, 12, 4305.0)                  # buckets 0 and 1 complete, 2 partial
    m5 = (_bar(DECISION + 3 * M5, 4305.0, 4305.0, 4305.0, 4305.0),)
    beyond = (_bar(DECISION + 4 * M5, 4305.0, 4305.0, 4305.0, 4305.0),)

    holes = uncovered_buckets(m1, m5 + beyond, DECISION, DECISION + 5 * M5,
                              limit=DECISION + 4 * M5, quote_gap=None)

    assert holes == (DECISION + 2 * M5, DECISION + 4 * M5)
    assert uncovered_buckets(m1, m5, DECISION + 1, DECISION + 2 * M5,
                             limit=DECISION + 4 * M5, quote_gap=None) == ()


# --- ledger job ---------------------------------------------------------------------
@pytest.fixture
def stores(tmp_path: Path) -> Iterator[tuple[LedgerCycles, BarStore]]:
    bars_ledger = LedgerV6(tmp_path / "gaps.db")
    cycles = LedgerCycles(tmp_path / "gaps.db")
    yield cycles, BarStore(bars_ledger)
    cycles.close()
    bars_ledger.close()


def _job(stores: tuple[LedgerCycles, BarStore], now: float, **config: Any):
    return label_pending_sync(*stores, now, FakeClock(epoch=now), replace(CFG, **config))


def test_a_gap_waits_for_a_backfill_and_heals(stores: tuple[LedgerCycles, BarStore]) -> None:
    ledger, store = stores
    m1 = _rally()
    holed = _without(m1, *HOLE)
    _seed(stores, _record(**LIMIT), bars=holed)

    waiting = _job(stores, AVAILABLE + 60.0)

    assert (waiting.written, waiting.data_gaps, waiting.results) == (0, 1, ())
    (pending,) = ledger.candidates_for_cycle("cyc-1")
    assert pending.label_status == "pending"
    store.ingest("M5", _m5_of(m1))                     # the EA restarted and backfilled
    healed = _job(stores, AVAILABLE + 120.0)
    assert (healed.written, healed.data_gaps) == (1, 0)
    assert ledger.candidates_for_cycle("cyc-1")[0].outcome == "tp"


def test_a_gap_past_the_grace_is_written_as_data_gap(
        stores: tuple[LedgerCycles, BarStore], caplog: pytest.LogCaptureFixture) -> None:
    ledger, _ = stores
    holed = _without(_rally(), *HOLE)
    _seed(stores, _record(**LIMIT), bars=holed)

    with caplog.at_level("WARNING"):
        run = _job(stores, AVAILABLE + 3601.0, data_gap_grace_s=3600)

    assert (run.written, run.unfillable, run.data_gaps) == (1, 1, 0)
    (stored,) = ledger.candidates_for_cycle("cyc-1")
    assert (stored.label_status, stored.outcome, stored.outcome_r) == (
        "unfillable", OUTCOME_DATA_GAP, None)
    assert "data_gap" in caplog.text


def test_the_ledger_accepts_data_gap_only_as_unfillable(
        stores: tuple[LedgerCycles, BarStore]) -> None:
    ledger, _ = stores
    _seed(stores, _record(), bars=())
    with pytest.raises(ValueError):
        ledger.write_label(_record().candidate_id, label_status="labeled",
                           outcome=OUTCOME_DATA_GAP, outcome_r=1.0, labeled_at=1.0)
    assert ledger.write_label(_record().candidate_id, label_status="unfillable",
                              outcome=OUTCOME_DATA_GAP, outcome_r=None, labeled_at=1.0)


# --- configuration ------------------------------------------------------------------
@pytest.mark.parametrize("overrides", [{"quote_gap": "20:00-22:00"}, {"data_gap_grace_s": -1},
                                       {"data_gap_grace_s": 1.5}])
def test_labeler_config_rejects_bad_gap_settings(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=next(iter(overrides))):
        LabelerConfig(**overrides)


def test_the_quote_gap_comes_from_the_settings() -> None:
    settings = V6Settings(_env_file=None, broker_quote_gap_utc="21:30-23:00")
    assert LabelerConfig.from_settings(settings).quote_gap == DailyUtcWindow(77_400, 82_800)
    assert V6Settings(_env_file=None, broker_quote_gap_utc="").quote_gap is None
    assert V6Settings(_env_file=None).quote_gap == DEFAULT_QUOTE_GAP
    with pytest.raises(ValidationError, match="V6_BROKER_QUOTE_GAP_UTC"):
        V6Settings(_env_file=None, broker_quote_gap_utc="8pm-10pm")


@pytest.mark.parametrize(("text", "start", "end"), [
    ("20:00-22:00", 72_000, 79_200), (" 23:30-00:30 ", 84_600, 1_800), ("00:00-00:01", 0, 60),
])
def test_parse_daily_window(text: str, start: int, end: int) -> None:
    assert parse_daily_window(text) == DailyUtcWindow(start, end)


@pytest.mark.parametrize("text", ["24:00-01:00", "20:00", "20:00-20:00", "2000-2200", "x"])
def test_parse_daily_window_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        parse_daily_window(text)


@pytest.mark.parametrize(("bounds", "error"), [((-1, 60), ValueError), ((0, 86_400), ValueError),
                                               ((1.5, 60), ValueError)])
def test_daily_window_bounds(bounds: tuple[Any, Any], error: type[Exception]) -> None:
    with pytest.raises(error):
        DailyUtcWindow(*bounds)


def test_windows_wrap_midnight_and_market_closed_combines_the_rules() -> None:
    wrap = DailyUtcWindow(84_600, 1_800)
    day = utc(2026, 9, 16)
    assert wrap.contains(day + 84_600) and wrap.contains(day + 1_799)
    assert not wrap.contains(day + 1_800) and not wrap.contains(day + 84_599)
    assert market_closed(utc(2026, 9, 16, 20, 40), DEFAULT_QUOTE_GAP)
    assert not market_closed(utc(2026, 9, 16, 20, 40), None)
    assert market_closed(utc(2026, 9, 16, 21, 30), None)          # rollover block (EDT)
    assert market_closed(utc(2026, 9, 19, 12), None)              # Saturday
    assert not market_closed(utc(2026, 9, 16, 12), DEFAULT_QUOTE_GAP)
