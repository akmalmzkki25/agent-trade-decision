"""The broker quote gap as hard entry safety (market.broker_hours)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.v6.market.broker_hours import (
    BLOCK_QUOTE_GAP,
    entry_block,
    minutes_to_gap,
    parse_daily_window,
)

GAP = parse_daily_window("20:00-22:00")
WRAP = parse_daily_window("23:30-00:30")
THIRTY_MIN = 1800


def _utc(hour: int, minute: int = 0) -> int:
    return int(datetime(2026, 9, 16, hour, minute, tzinfo=timezone.utc).timestamp())


@pytest.mark.parametrize(("hour", "minute", "expected"), [
    (19, 0, _utc(20)),
    (20, 0, _utc(20)),
    (21, 59, _utc(21, 59)),
    (22, 0, _utc(20) + 86_400),
    (3, 0, _utc(20)),
])
def test_next_start(hour: int, minute: int, expected: int) -> None:
    assert GAP.next_start(_utc(hour, minute)) == expected


def test_next_start_across_midnight() -> None:
    assert WRAP.next_start(_utc(0, 10)) == _utc(0, 10)
    assert WRAP.next_start(_utc(1, 0)) == _utc(23, 30)


@pytest.mark.parametrize(("start", "end", "expected"), [
    (_utc(19, 0), _utc(19, 30), False),
    (_utc(19, 30), _utc(20, 0), False),
    (_utc(19, 45), _utc(20, 15), True),
    (_utc(20, 30), _utc(20, 31), True),
    (_utc(22, 0), _utc(23, 0), False),
    (_utc(19, 0), _utc(19, 0), False),
])
def test_overlaps(start: int, end: int, expected: bool) -> None:
    assert GAP.overlaps(start, end) is expected


@pytest.mark.parametrize(("hour", "minute", "blocked"), [
    (19, 15, False),
    (19, 30, False),
    (19, 31, True),
    (21, 0, True),
    (22, 0, False),
])
def test_entry_block_with_a_thirty_minute_order(hour: int, minute: int, blocked: bool) -> None:
    expected = (BLOCK_QUOTE_GAP,) if blocked else ()
    assert entry_block(_utc(hour, minute), GAP, THIRTY_MIN) == expected


def test_entry_block_without_a_gap_or_lifetime() -> None:
    assert entry_block(_utc(21), None, THIRTY_MIN) == ()
    assert entry_block(_utc(21), GAP, 0) == (BLOCK_QUOTE_GAP,)
    assert entry_block(_utc(19), GAP, 0) == ()


def test_minutes_to_gap() -> None:
    assert minutes_to_gap(_utc(19, 15), GAP) == 45.0
    assert minutes_to_gap(_utc(20, 30), GAP) == 0.0
    assert minutes_to_gap(_utc(19), None) is None
