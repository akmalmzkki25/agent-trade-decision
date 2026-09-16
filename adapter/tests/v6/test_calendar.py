"""
Economic-calendar veto tests, pinned to the 2026 calendar (US DST 2026-03-08 -> 11-01).

FOMC statements are 14:00 New York (18:00 UTC in summer, 19:00 in winter); NFP 08:30.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timezone
from typing import Any

import pytest

from app.v6.cycle_codes import CAL_POST_EVENT, CAL_POST_SURPRISE, CAL_PRE_EVENT, CAL_STALE
from app.v6.cycle_codes import CAL_US_DATA_BAR
from app.v6.cycle_types import CalendarEvent
from app.v6.market import calendar as cal
from app.v6.market.calendar import (
    STATIC_CALENDAR, StaticCalendar, assess_calendar, assess_snapshot_calendar, event_codes,
    event_from_mt5, is_surprise, minutes_since_last_high, minutes_to_next_high,
    mt5_calendar_readable, nfp_release_day, static_us_events, surprise_ratio,
)
from app.v6.schemas.agents import ID_PATTERN, MAX_EVENT_IDS, validate_view
from app.v6.schemas.snapshot import CalendarEventBlock, ProbeBlock

from .payloads_v6 import as_snapshot, snapshot_payload

MIN = 60
HOUR = 3600


def utc(y: int, m: int, d: int, hh: int = 0, mm: int = 0, ss: int = 0) -> int:
    return int(datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc).timestamp())


FOMC_SEP = utc(2026, 9, 16, 18)
# Tue 2026-09-22: no static event within 24 h; both instants are in the main window
# and outside the 08:15-08:45 New York data block.
QUIET_NOON = utc(2026, 9, 22, 12)
QUIET_AFTERNOON = utc(2026, 9, 22, 14)
# A static table with no events that vouches for every day: isolates MT5 behaviour.
WIDE_OPEN = StaticCalendar(events=(), first_day=date(2000, 1, 1), last_day=date(2099, 12, 31))


def block(when: int = FOMC_SEP, event_id: int = 840030016, *, currency: str = "USD",
          importance: str = "HIGH", code: str = "fed-interest-rate-decision",
          actual: float | None = None, forecast: float | None = None) -> CalendarEventBlock:
    return CalendarEventBlock(event_id=event_id, time_epoch=when, currency=currency,
                              importance=importance, code=code, name="Event",
                              actual=actual, forecast=forecast, previous=None)


def assess(blocks: tuple[CalendarEventBlock, ...], now: int, *, sent_at: int | None = None,
           **kwargs: Any):
    return assess_calendar(blocks, now_epoch=now,
                           sent_at_epoch=now if sent_at is None else sent_at, **kwargs)


def static_times(code: str) -> list[int]:
    return [e.time_epoch for e in static_us_events() if e.code == code]


# --- static table ---------------------------------------------------------


def test_fomc_2026_statements_are_14_00_new_york() -> None:
    expected = [utc(2026, 1, 28, 19), utc(2026, 3, 18, 18), utc(2026, 4, 29, 18),
                utc(2026, 6, 17, 18), utc(2026, 7, 29, 18), FOMC_SEP,
                utc(2026, 10, 28, 18), utc(2026, 12, 9, 19)]

    assert static_times(cal.CODE_FOMC_STATEMENT) == expected
    assert static_times(cal.CODE_FOMC_PRESS_CONFERENCE) == [t + 30 * MIN for t in expected]


@pytest.mark.parametrize(("year", "month", "expected"), [
    (2017, 3, date(2017, 3, 10)), (2019, 11, date(2019, 11, 1)), (2020, 5, date(2020, 5, 8)),
    (2020, 7, date(2020, 7, 2)), (2020, 1, date(2020, 1, 10)), (2021, 1, date(2021, 1, 8)),
    (2019, 1, date(2019, 1, 4)), (2018, 1, date(2018, 1, 5)), (2024, 7, date(2024, 7, 5)),
    (2024, 8, date(2024, 8, 2)), (2025, 7, date(2025, 7, 3)), (2025, 9, date(2025, 9, 5)),
])
def test_nfp_rule_matches_published_bls_release_days(year: int, month: int, expected: date) -> None:
    assert nfp_release_day(year, month) == expected


def test_nfp_first_friday_rule_and_its_exceptions() -> None:
    # Usually the first Friday, including the 1st itself ...
    assert nfp_release_day(2019, 11) == date(2019, 11, 1)
    assert nfp_release_day(2026, 9) == date(2026, 9, 4)
    # ... but not when the reference week ends late: May 1 2026 is a Friday.
    assert nfp_release_day(2026, 5) == date(2026, 5, 8)
    # January releases never fall on Jan 1-3; July 3 (observed holiday) -> Thursday.
    assert nfp_release_day(2027, 1) == date(2027, 1, 8)
    assert nfp_release_day(2026, 7) == date(2026, 7, 2)
    assert [nfp_release_day(2026, m).day for m in range(1, 13)] == [
        9, 6, 6, 3, 8, 5, 2, 7, 4, 2, 6, 4]
    for month in (0, 13):
        with pytest.raises(ValueError):
            nfp_release_day(2026, month)


def test_nfp_static_times_follow_new_york_dst() -> None:
    times = static_times(cal.CODE_NONFARM_PAYROLLS)

    for expected in (utc(2026, 3, 6, 13, 30), utc(2026, 4, 3, 12, 30),
                     utc(2026, 10, 2, 12, 30), utc(2026, 11, 6, 13, 30),
                     utc(2027, 1, 8, 13, 30), utc(2027, 7, 2, 12, 30)):
        assert expected in times
    assert len(times) == 24


def test_static_events_are_sorted_usd_high_with_valid_ids() -> None:
    events = static_us_events()

    assert list(events) == sorted(events, key=lambda e: (e.time_epoch, e.event_id))
    assert all(e.currency == "USD" and e.importance == "HIGH" and e.source == "static"
               for e in events)
    assert all(re.match(ID_PATTERN, e.event_id) for e in events)
    assert STATIC_CALENDAR.events == events


def test_static_calendar_coverage_validation_and_window_constants() -> None:
    assert STATIC_CALENDAR.covers(utc(2026, 1, 1), utc(2026, 12, 31, 23, 59))
    assert not STATIC_CALENDAR.covers(utc(2026, 12, 31, 12), utc(2027, 1, 1, 12))
    with pytest.raises(ValueError):
        StaticCalendar(events=(), first_day=date(2026, 2, 1), last_day=date(2026, 1, 1))
    assert cal.EVENT_LOOKBACK_S >= cal.POST_SURPRISE_BLACKOUT_S
    assert cal.MAX_ASSESS_HORIZON_S + cal.PRE_EVENT_BLACKOUT_S <= cal.MT5_CALENDAR_HORIZON_S
    assert cal.MAX_OFFERED_EVENTS == MAX_EVENT_IDS


# --- blackout windows -----------------------------------------------------------


@pytest.mark.parametrize(("offset", "blackout", "codes"), [
    (-901, False, ()), (-900, True, (CAL_PRE_EVENT,)), (-1, True, (CAL_PRE_EVENT,)),
    (0, True, (CAL_POST_EVENT,)), (900, True, (CAL_POST_EVENT,)), (901, False, ()),
])
def test_fomc_2026_09_16_blackout_edges(offset: int, blackout: bool, codes: tuple) -> None:
    result = assess((block(),), FOMC_SEP + offset, static=WIDE_OPEN)

    assert (result.blackout, result.codes, result.stale) == (blackout, codes, False)
    assert result.as_of_epoch == FOMC_SEP + offset


@pytest.mark.parametrize(("offset", "blackout"), [
    (-901, False), (-900, True), (25 * MIN, True), (45 * MIN, True), (45 * MIN + 1, False),
])
def test_static_fomc_statement_and_press_windows_without_mt5(offset: int, blackout: bool) -> None:
    # 17:45-18:45 UTC is after the main window, so an empty block is not stale.
    result = assess((), FOMC_SEP + offset)

    assert (result.blackout, result.stale) == (blackout, False)


def test_mt5_event_replaces_the_static_duplicate() -> None:
    result = assess((block(FOMC_SEP + 30),), FOMC_SEP - 60)
    codes = [e.code for e in result.events]

    assert codes == ["fed-interest-rate-decision", cal.CODE_FOMC_PRESS_CONFERENCE]
    assert result.events[0].event_id == f"mt5:840030016:{FOMC_SEP + 30}"


@pytest.mark.parametrize(("nfp", "fomc"), [
    (utc(2026, 3, 6, 13, 30), utc(2026, 3, 18, 18)),    # EST NFP; FOMC in the mismatch week
    (utc(2026, 11, 6, 13, 30), utc(2026, 10, 28, 18)),  # EST since Nov 1; EDT on Oct 28
])
def test_dst_weeks_use_new_york_time(nfp: int, fomc: int) -> None:
    other = (block(nfp + 5 * HOUR, 1),)

    # The session's 08:15 New York data block moves with the release too.
    assert assess(other, nfp - 15 * MIN).codes == (CAL_PRE_EVENT, CAL_US_DATA_BAR)
    assert not assess(other, nfp - 75 * MIN).blackout
    assert assess((), fomc - 15 * MIN).blackout
    assert not assess((), fomc + 45 * MIN + 1).blackout


def test_horizon_extends_the_pre_event_check() -> None:
    blocks = (block(),)
    now = FOMC_SEP - 20 * MIN

    assert not assess(blocks, now, static=WIDE_OPEN).blackout
    assert not assess(blocks, now, static=WIDE_OPEN, horizon_s=299).blackout
    assert assess(blocks, now, static=WIDE_OPEN, horizon_s=300).codes == (CAL_PRE_EVENT,)


@pytest.mark.parametrize(("now", "sent_at", "horizon", "error"), [
    (True, FOMC_SEP, 0, TypeError), (1.5, FOMC_SEP, 0, TypeError), (-1, FOMC_SEP, 0, ValueError),
    (FOMC_SEP, FOMC_SEP * 1000, 0, ValueError), (FOMC_SEP, FOMC_SEP, -1, ValueError),
    (FOMC_SEP, FOMC_SEP, cal.MAX_ASSESS_HORIZON_S + 1, ValueError),
    (FOMC_SEP, FOMC_SEP, True, TypeError), (FOMC_SEP, FOMC_SEP, 1.5, TypeError),
])
def test_invalid_inputs_are_rejected(now: Any, sent_at: Any, horizon: Any, error: type) -> None:
    with pytest.raises(error):
        assess((block(),), now, sent_at=sent_at, horizon_s=horizon)


def test_us_data_bar_is_reported_but_left_to_the_session_gate() -> None:
    result = assess((block(),), utc(2026, 9, 16, 12, 30))

    assert (result.codes, result.blackout) == ((CAL_US_DATA_BAR,), False)
    assert CAL_US_DATA_BAR not in assess((block(),), utc(2026, 9, 16, 12, 45)).codes


# --- fail closed -------------------------------------------------------------------


@pytest.mark.parametrize(("blocks", "now", "readable", "age", "stale"), [
    ((block(),), QUIET_NOON, None, 900, False),              # old event, fresh snapshot
    ((block(),), QUIET_NOON, None, 901, True),               # snapshot too old
    ((), QUIET_NOON, None, 0, False),                        # static table vouches
    ((), QUIET_NOON, True, 0, False),
    ((), QUIET_NOON, False, 0, True),                        # terminal calendar unreadable
    ((block(QUIET_NOON + HOUR),), QUIET_NOON, False, 0, False),
    ((), utc(2026, 9, 16, 12), None, 0, True),               # MT5 missed today's FOMC
    ((), utc(2026, 10, 1, 14), None, 0, True),               # MT5 missed tomorrow's NFP
    ((), utc(2027, 2, 10, 14), None, 0, True),               # no static coverage in 2027
    ((block(utc(2027, 2, 10, 15)),), utc(2027, 2, 10, 14), None, 0, False),
    ((block(utc(2027, 2, 10, 15), currency="EUR"),), utc(2027, 2, 10, 14), None, 0, True),
    ((), utc(2026, 9, 22, 2), False, 0, False),              # outside the main window
    ((), utc(2026, 9, 19, 12), False, 0, False),             # Saturday
    ((), utc(2026, 12, 31, 12), None, 0, True),              # horizon leaves the table
])
def test_calendar_fails_closed_when_stale_or_unvouched(
        blocks: tuple, now: int, readable: bool | None, age: int, stale: bool) -> None:
    result = assess(blocks, now, sent_at=now - age, mt5_readable=readable)

    assert (result.stale, result.blackout) == (stale, stale)
    assert result.codes == ((CAL_STALE,) if stale else ())


# --- surprise extension ---------------------------------------------------------------


@pytest.mark.parametrize(("actual", "offset", "codes"), [
    (250_000.0, 5 * MIN, (CAL_POST_EVENT, CAL_POST_SURPRISE)),
    (250_000.0, 16 * MIN, (CAL_POST_SURPRISE,)), (250_000.0, 35 * MIN, (CAL_POST_SURPRISE,)),
    (250_000.0, 35 * MIN + 1, ()), (160_000.0, 5 * MIN, (CAL_POST_EVENT,)),
    (160_000.0, 16 * MIN, ()),
])
def test_large_miss_extends_the_post_window(actual: float, offset: int, codes: tuple) -> None:
    # A released value carries `actual` (a feed with a lookback would send it).
    released = block(actual=actual, forecast=150_000.0)
    result = assess((released,), FOMC_SEP + offset, static=WIDE_OPEN)

    assert (result.codes, result.blackout) == (codes, bool(codes))


@pytest.mark.parametrize(("actual", "forecast", "ratio", "surprise"), [
    (0.25, 1.0, 0.75, True), (1.25, 1.0, 0.25, True), (1.2, 1.0, 0.2, False),
    (0.1, 0.0, 1.0, True), (0.02, 0.0, 0.2, False), (-0.2, -0.4, 0.5, True),
    (None, 1.0, None, False), (1.0, None, None, False), (math.nan, 1.0, None, False),
])
def test_surprise_ratio_rule(actual: float | None, forecast: float | None,
                             ratio: float | None, surprise: bool) -> None:
    event = CalendarEvent(event_id="x:1", source="forexfactory", time_epoch=0, currency="USD",
                          importance="HIGH", code="cpi", actual=actual, forecast=forecast)

    got = surprise_ratio(event)
    assert (got is None) if ratio is None else math.isclose(got, ratio)
    assert is_surprise(event) is surprise


# --- currencies, carried events, offered events ------------------------------------------


@pytest.mark.parametrize(("currency", "importance"), [("EUR", "HIGH"), ("USD", "MODERATE")])
def test_other_currencies_and_importances_are_ignored(currency: str, importance: str) -> None:
    other = block(currency=currency, importance=importance)
    result = assess((other,), FOMC_SEP, static=WIDE_OPEN)

    assert (result.blackout, result.events, result.next_event_minutes) == (False, (), None)
    assert minutes_since_last_high([event_from_mt5(other)], FOMC_SEP) is None


def test_carried_events_keep_post_windows_and_fresh_events_replace_them() -> None:
    carried = assess((block(),), FOMC_SEP - 5 * MIN, static=WIDE_OPEN).events
    after = FOMC_SEP + 10 * MIN
    ignored = (CalendarEvent(event_id=f"static:x:{after}", source="static", time_epoch=after,
                             currency="USD", importance="HIGH", code="x"),
               CalendarEvent(event_id=f"mt5:9:{after}", source="mt5", time_epoch=after,
                             currency="EUR", importance="HIGH", code="ecb"))

    # The EA no longer lists the released event; the carried copy keeps its window.
    assert assess((), after, static=WIDE_OPEN, carried_events=carried).codes == (CAL_POST_EVENT,)
    assert not assess((), after, static=WIDE_OPEN).blackout
    assert not assess((), after, static=WIDE_OPEN, carried_events=ignored).blackout
    fresh = assess((block(actual=3.0, forecast=5.0),), after + 10 * MIN, static=WIDE_OPEN,
                   carried_events=carried)
    assert (fresh.codes, len(fresh.events)) == ((CAL_POST_SURPRISE,), 1)


def test_minutes_to_and_since_high_events() -> None:
    now = FOMC_SEP
    both = assess((block(now - 30 * MIN, 1), block(now + 45 * MIN, 2)), now, static=WIDE_OPEN)

    assert (both.last_event_minutes_ago, both.next_event_minutes) == (30.0, 45.0)
    at_event = assess((block(now, 3),), now, static=WIDE_OPEN)
    assert (at_event.last_event_minutes_ago, at_event.next_event_minutes) == (0.0, None)
    assert minutes_to_next_high(at_event.events, now - 90) == 1.5


def test_offered_events_keep_the_nearest_ten_and_drop_old_ones() -> None:
    now = FOMC_SEP
    blocks = (block(now - 60 * MIN, 100), block(now - 121 * MIN, 101),
              *(block(now + h * HOUR, h) for h in range(1, 12)))
    result = assess(blocks, now, static=WIDE_OPEN)

    times = [e.time_epoch for e in result.events]
    assert times == [now - 60 * MIN, *(now + h * HOUR for h in range(1, 10))]
    assert result.last_event_minutes_ago == 60.0


def test_offered_events_prefer_surprises_on_ties() -> None:
    now = FOMC_SEP + 20 * MIN
    plain = [block(FOMC_SEP, i) for i in range(1, 12)]
    hot = block(FOMC_SEP, 99, actual=2.0, forecast=1.0)
    result = assess((*plain, hot), now, static=WIDE_OPEN)

    assert len(result.events) == MAX_EVENT_IDS
    assert f"mt5:99:{FOMC_SEP}" in result.event_ids
    assert result.codes == (CAL_POST_SURPRISE,)


def test_offered_event_ids_are_accepted_by_the_news_desk_validator() -> None:
    result = assess((block(),), FOMC_SEP - 5 * MIN)
    view = {"stance": "BLOCK", "size_multiplier": 0.0, "regime": "EVENT_RISK",
            "event_ids": sorted(result.event_ids), "reason_codes": ["EVENT_IMMINENT"],
            "note": ""}

    parsed = validate_view("news_risk", json.dumps(view), frozenset(), result.event_ids)
    assert set(parsed.event_ids) == result.event_ids


# --- conversion helpers --------------------------------------------------------------


def test_event_from_mt5_copies_fields_into_a_valid_id() -> None:
    event = event_from_mt5(block(actual=1.0, forecast=2.0))

    assert event == CalendarEvent(
        event_id=f"mt5:840030016:{FOMC_SEP}", source="mt5", time_epoch=FOMC_SEP,
        currency="USD", importance="HIGH", code="fed-interest-rate-decision",
        actual=1.0, forecast=2.0, previous=None)
    assert re.match(ID_PATTERN, event_from_mt5(block(event_id=2**63 - 1)).event_id)


@pytest.mark.parametrize(("event_id", "when"), [(2**63, FOMC_SEP), (1, FOMC_SEP * 1000)])
def test_event_from_mt5_rejects_out_of_range_values(event_id: int, when: int) -> None:
    with pytest.raises(ValueError):
        event_from_mt5(block(when, event_id))


def test_event_codes_are_distinct_usd_high_in_time_order() -> None:
    events = [event_from_mt5(b) for b in (
        block(FOMC_SEP + 60, 1, code="b"), block(FOMC_SEP, 2, code="a"),
        block(FOMC_SEP + 120, 3, code="a"), block(FOMC_SEP, 4, code="z", currency="EUR"))]

    assert event_codes(events) == ("a", "b")


def _probe(seen: int) -> ProbeBlock:
    return ProbeBlock(book_depth=10, trade_ticks_count=0, real_volume_count=0,
                      dom_synthetic=True, gmt_offset_s=10_800, dst_active=False,
                      calendar_events_seen=seen)


def _snapshot(probe_seen: int | None, calendar: list[dict[str, Any]] | None = None):
    payload = snapshot_payload(bar_open=QUIET_AFTERNOON)
    payload["calendar"] = calendar or []
    if probe_seen is not None:
        payload["probe"] = _probe(probe_seen).model_dump()
    return as_snapshot(payload)


def test_probe_readability_reaches_the_snapshot_assessment() -> None:
    now = QUIET_AFTERNOON + 15 * MIN + 2

    assert mt5_calendar_readable(None) is None
    assert mt5_calendar_readable(_probe(0)) is False
    assert mt5_calendar_readable(_probe(12)) is True
    assert not assess_snapshot_calendar(_snapshot(None), now_epoch=now).stale
    assert assess_snapshot_calendar(_snapshot(None), now_epoch=now, probe=_probe(0)).stale
    assert not assess_snapshot_calendar(_snapshot(5), now_epoch=now, probe=_probe(0)).stale


def test_assess_snapshot_calendar_reads_the_block_and_carries() -> None:
    event = block(QUIET_AFTERNOON + 20 * MIN, 7).model_dump()
    snap = _snapshot(None, [event])
    now = snap.sent_at_epoch + 5

    first = assess_snapshot_calendar(snap, now_epoch=now)
    assert first.codes == (CAL_PRE_EVENT,)
    later = assess_snapshot_calendar(_snapshot(None), now_epoch=now + 10 * MIN,
                                     carried_events=first.events, horizon_s=60)
    assert later.codes == (CAL_POST_EVENT,)
    assert later == assess_snapshot_calendar(_snapshot(None), now_epoch=now + 10 * MIN,
                                             carried_events=first.events, horizon_s=60)
