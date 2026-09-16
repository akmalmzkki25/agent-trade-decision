"""
Economic-calendar veto for one cycle (plan section 6 gate 6, knowledge/03 section 4).

Pure functions over UTC epoch seconds: no clock, file or network access.

* Only USD events of HIGH importance count; anything else the EA sends is dropped.
* Blackout while the assessed interval touches [event - 15 min, event + 15 min],
  edges included (CAL_PRE_EVENT before the release, CAL_POST_EVENT from it on). The
  interval runs from the decision bar's close (`decision_epoch`, when given and
  earlier than now) to now + `horizon_s`, so whether the release bar is blocked
  never depends on how many seconds after its close the cycle ran.
* Surprise (CAL_POST_SURPRISE): if |actual - forecast| / max(|forecast|,
  SURPRISE_MIN_DENOMINATOR) >= SURPRISE_REL_THRESHOLD, the post window runs to
  event + 35 min. knowledge/03 (Sobti) finds 30-35 min of illiquidity after large
  negative gold jumps; the sign of a miss for gold is unknown per event, so both
  directions extend. A released data event whose actual is not known yet (a
  forecast but no actual, or a static NFP / FOMC statement) may have been such a
  miss and extends too: fail closed. The relative rule under-reads level series
  (policy rate, unemployment rate); the FOMC has static statement and
  press-conference windows and the unemployment rate shares the NFP release.
* US data bar: sessions.py blocks the 08:15-08:45 New York bar (SESSION gate); the
  calendar only reports it (CAL_US_DATA_BAR) and never blacks out for it.
* Static table (`calendar_static`, fallback and cross-check): a static event within
  STATIC_DEDUP_TOLERANCE_S of a known event is dropped, otherwise it adds its own
  blackout.
* Fail closed (stale, blackout, CAL_STALE) when the snapshot is older than
  CALENDAR_MAX_AGE_S, or when the MT5 block has no upcoming USD HIGH event in the
  weekday main window and the static table cannot vouch: it must cover the whole
  MT5 horizon, hold no event inside it (MT5 would have listed it), and the probe
  must not report an unreadable terminal calendar.

The EA lists events from EVENT_LOOKBACK_S before its send time, with their actuals
once published. Pass the previous assessment's `events` as `carried_events` too:
events in the current block replace carried ones with the same id.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Final

from ..cycle_codes import CAL_POST_EVENT, CAL_POST_SURPRISE, CAL_PRE_EVENT, CAL_STALE
from ..cycle_codes import CAL_US_DATA_BAR
from ..cycle_types import CalendarAssessment, CalendarEvent
from ..schemas.agents import MAX_EVENT_IDS
from ..schemas.snapshot import CalendarEventBlock, ProbeBlock, V6Snapshot
from .calendar_static import (  # noqa: F401 - re-exported for callers of this module
    BLACKOUT_CURRENCY, CODE_FOMC_PRESS_CONFERENCE, CODE_FOMC_STATEMENT, CODE_NONFARM_PAYROLLS,
    FOMC_STATEMENT_DAYS_2026, MT5_SOURCE, STATIC_CALENDAR, STATIC_SOURCE,
    SURPRISE_PRONE_STATIC_CODES, StaticCalendar, event_order, nfp_release_day,
    static_us_events,
)
from .sessions import MAX_SUPPORTED_EPOCH, session_state

SECONDS_PER_MINUTE: Final[int] = 60
SECONDS_PER_HOUR: Final[int] = 3600
BLACKOUT_IMPORTANCE: Final[str] = "HIGH"
PRE_EVENT_BLACKOUT_S: Final[int] = 15 * SECONDS_PER_MINUTE
POST_EVENT_BLACKOUT_S: Final[int] = 15 * SECONDS_PER_MINUTE
POST_SURPRISE_BLACKOUT_S: Final[int] = 35 * SECONDS_PER_MINUTE
SURPRISE_REL_THRESHOLD: Final[float] = 0.25
# Keeps near-zero forecasts (e.g. CPI m/m 0.0) from dividing by zero: a 0.1 miss
# on a 0.0 forecast reads as 1.0.
SURPRISE_MIN_DENOMINATOR: Final[float] = 0.1
# One M15 bar: an older calendar means the EA stopped sending snapshots.
CALENDAR_MAX_AGE_S: Final[int] = 15 * SECONDS_PER_MINUTE
# Mirrors CALENDAR_HORIZON_S in ea/QlipV6/Snapshot.mqh.
MT5_CALENDAR_HORIZON_S: Final[int] = 24 * SECONDS_PER_HOUR
# Past events stay in the assessment this long (>= the surprise window); mirrors
# CALENDAR_LOOKBACK_S in ea/QlipV6/Snapshot.mqh.
EVENT_LOOKBACK_S: Final[int] = 2 * SECONDS_PER_HOUR
# The longest time barrier the plan allows (4 h).
MAX_ASSESS_HORIZON_S: Final[int] = 4 * SECONDS_PER_HOUR
MAX_OFFERED_EVENTS: Final[int] = MAX_EVENT_IDS
STATIC_DEDUP_TOLERANCE_S: Final[int] = 2 * SECONDS_PER_MINUTE
STATIC_CROSSCHECK_MARGIN_S: Final[int] = 5 * SECONDS_PER_MINUTE
# The EA writes MqlCalendarEvent.id cast to a signed long.
MAX_MT5_EVENT_ID: Final[int] = 2**63 - 1

CODE_ORDER: Final[tuple[str, ...]] = (
    CAL_STALE, CAL_PRE_EVENT, CAL_POST_EVENT, CAL_POST_SURPRISE, CAL_US_DATA_BAR)
BLACKOUT_CODES: Final[frozenset[str]] = frozenset(
    {CAL_STALE, CAL_PRE_EVENT, CAL_POST_EVENT, CAL_POST_SURPRISE})

# --- event helpers -----------------------------------------------------------------


def event_from_mt5(block: CalendarEventBlock) -> CalendarEvent:
    """Reduce an MT5 block to a CalendarEvent with id `mt5:<event id>:<time>`."""
    if block.event_id > MAX_MT5_EVENT_ID:
        raise ValueError("MT5 calendar event id is out of range")
    _require_epoch("calendar time_epoch", block.time_epoch)
    return CalendarEvent(
        event_id=f"{MT5_SOURCE}:{block.event_id}:{block.time_epoch}", source="mt5",
        time_epoch=block.time_epoch, currency=block.currency, importance=block.importance,
        code=block.code, actual=block.actual, forecast=block.forecast,
        previous=block.previous,
    )


def is_blackout_event(event: CalendarEvent) -> bool:
    return event.currency == BLACKOUT_CURRENCY and event.importance == BLACKOUT_IMPORTANCE


def surprise_ratio(event: CalendarEvent) -> float | None:
    """|actual - forecast| / max(|forecast|, floor); None unless both are finite."""
    actual, forecast = event.actual, event.forecast
    if (actual is None or forecast is None
            or not (math.isfinite(actual) and math.isfinite(forecast))):
        return None
    return abs(actual - forecast) / max(abs(forecast), SURPRISE_MIN_DENOMINATOR)


def is_surprise(event: CalendarEvent) -> bool:
    ratio = surprise_ratio(event)
    return ratio is not None and ratio >= SURPRISE_REL_THRESHOLD


def surprise_unknown(event: CalendarEvent) -> bool:
    """A data release whose actual has not arrived: it may have been a large miss."""
    expects_number = event.forecast is not None or (
        event.source == STATIC_SOURCE and event.code in SURPRISE_PRONE_STATIC_CODES)
    return event.actual is None and expects_number


def may_be_surprise(event: CalendarEvent) -> bool:
    return is_surprise(event) or surprise_unknown(event)


def minutes_to_next_high(events: Iterable[CalendarEvent], now_epoch: int) -> float | None:
    """Minutes until the next USD HIGH event strictly after `now_epoch`."""
    ahead = [e.time_epoch - now_epoch for e in events
             if is_blackout_event(e) and e.time_epoch > now_epoch]
    return min(ahead) / SECONDS_PER_MINUTE if ahead else None


def minutes_since_last_high(events: Iterable[CalendarEvent], now_epoch: int) -> float | None:
    """Minutes since the latest USD HIGH event at or before `now_epoch`."""
    behind = [now_epoch - e.time_epoch for e in events
              if is_blackout_event(e) and e.time_epoch <= now_epoch]
    return min(behind) / SECONDS_PER_MINUTE if behind else None


def event_codes(events: Iterable[CalendarEvent]) -> tuple[str, ...]:
    """Distinct MT5/static event codes of USD HIGH events, in time order."""
    ordered = sorted((e for e in events if is_blackout_event(e)), key=event_order)
    return tuple(dict.fromkeys(e.code for e in ordered))


def mt5_calendar_readable(probe: ProbeBlock | None) -> bool | None:
    """The probe counts USD events a week ahead; zero means the terminal cannot read them."""
    return None if probe is None else probe.calendar_events_seen > 0


# --- assessment ----------------------------------------------------------------------


def assess_calendar(
    mt5_events: Sequence[CalendarEventBlock], *, now_epoch: int, sent_at_epoch: int,
    carried_events: Sequence[CalendarEvent] = (), mt5_readable: bool | None = None,
    horizon_s: int = 0, static: StaticCalendar = STATIC_CALENDAR,
    decision_epoch: int | None = None,
) -> CalendarAssessment:
    """Calendar veto at `now_epoch` (the adapter clock, UTC seconds).

    `sent_at_epoch` is when the EA read `mt5_events`; `mt5_readable` comes from
    `mt5_calendar_readable(probe)`. The windows are checked over
    [min(decision_epoch, now), now + horizon_s]; staleness is judged at now.
    """
    _require_epoch("now_epoch", now_epoch)
    _require_epoch("sent_at_epoch", sent_at_epoch)
    _require_horizon(horizon_s)
    start = now_epoch if decision_epoch is None else _decision_start(decision_epoch, now_epoch)
    fresh = tuple(e for e in map(event_from_mt5, mt5_events) if is_blackout_event(e))
    events = _events_in_window(_merge_known(fresh, carried_events), static, now_epoch)
    session = session_state(now_epoch)
    stale = _is_stale(
        has_upcoming=any(e.time_epoch >= sent_at_epoch for e in fresh), now_epoch=now_epoch,
        sent_at_epoch=sent_at_epoch, in_main_window=session.in_main_window,
        mt5_readable=mt5_readable, static=static)
    codes = _assessment_codes(events, start, now_epoch + horizon_s,
                              stale=stale, us_data_bar=session.us_data_block)
    return CalendarAssessment(
        as_of_epoch=now_epoch,
        blackout=not BLACKOUT_CODES.isdisjoint(codes),
        codes=codes,
        next_event_minutes=minutes_to_next_high(events, now_epoch),
        last_event_minutes_ago=minutes_since_last_high(events, now_epoch),
        stale=stale,
        events=_offered(events, now_epoch),
    )


def assess_snapshot_calendar(
    snapshot: V6Snapshot, *, now_epoch: int, carried_events: Sequence[CalendarEvent] = (),
    probe: ProbeBlock | None = None, horizon_s: int = 0, decision_epoch: int | None = None,
) -> CalendarAssessment:
    """`assess_calendar` for a snapshot; `probe` is the cached one for probe-less snapshots."""
    effective_probe = snapshot.probe if snapshot.probe is not None else probe
    return assess_calendar(
        snapshot.calendar, now_epoch=now_epoch, sent_at_epoch=snapshot.sent_at_epoch,
        carried_events=carried_events, mt5_readable=mt5_calendar_readable(effective_probe),
        horizon_s=horizon_s, decision_epoch=decision_epoch,
    )


# --- helpers ---------------------------------------------------------------------------


def _merge_known(fresh: Sequence[CalendarEvent],
                 carried: Iterable[CalendarEvent]) -> tuple[CalendarEvent, ...]:
    """Carried USD HIGH events (never static ones), replaced by fresh events with the same id."""
    merged = {e.event_id: e for e in carried
              if is_blackout_event(e) and e.source != STATIC_SOURCE}
    merged.update((e.event_id, e) for e in fresh)
    return tuple(merged.values())


def _events_in_window(known: Sequence[CalendarEvent], static: StaticCalendar,
                      now_epoch: int) -> tuple[CalendarEvent, ...]:
    """Known plus uncovered static events in [now - lookback, now + MT5 horizon]."""
    start, end = now_epoch - EVENT_LOOKBACK_S, now_epoch + MT5_CALENDAR_HORIZON_S
    known_times = [e.time_epoch for e in known]
    statics = (
        s for s in static.events
        if start <= s.time_epoch <= end
        and all(abs(s.time_epoch - t) > STATIC_DEDUP_TOLERANCE_S for t in known_times)
    )
    in_window = (e for e in (*known, *statics) if start <= e.time_epoch <= end)
    return tuple(sorted(in_window, key=event_order))


def _is_stale(*, has_upcoming: bool, now_epoch: int, sent_at_epoch: int,
              in_main_window: bool, mt5_readable: bool | None, static: StaticCalendar) -> bool:
    """Released events prove the feed is readable, not that its upcoming list is complete."""
    if now_epoch - sent_at_epoch > CALENDAR_MAX_AGE_S:
        return True
    if has_upcoming or not in_main_window:
        return False
    return not _static_vouches(static, sent_at_epoch, mt5_readable)


def _static_vouches(static: StaticCalendar, sent_at_epoch: int,
                    mt5_readable: bool | None) -> bool:
    """Can an empty MT5 block be believed? See the module docstring."""
    horizon_end = sent_at_epoch + MT5_CALENDAR_HORIZON_S
    if mt5_readable is False or not static.covers(sent_at_epoch, horizon_end):
        return False
    first = sent_at_epoch + STATIC_CROSSCHECK_MARGIN_S
    last = horizon_end - STATIC_CROSSCHECK_MARGIN_S
    return not any(first <= e.time_epoch <= last for e in static.events)


def _assessment_codes(events: Iterable[CalendarEvent], start: int, end: int, *,
                      stale: bool, us_data_bar: bool) -> tuple[str, ...]:
    found = {code for event in events for code in _window_codes(event, start, end)}
    if stale:
        found.add(CAL_STALE)
    if us_data_bar:
        found.add(CAL_US_DATA_BAR)
    return tuple(code for code in CODE_ORDER if code in found)


def _window_codes(event: CalendarEvent, start: int, end: int) -> tuple[str, ...]:
    """Codes for one event over the assessed interval [start, end]."""
    if end < event.time_epoch - PRE_EVENT_BLACKOUT_S:
        return ()
    if start < event.time_epoch:
        return (CAL_PRE_EVENT,)
    elapsed = start - event.time_epoch
    codes = (CAL_POST_EVENT,) if elapsed <= POST_EVENT_BLACKOUT_S else ()
    if elapsed <= POST_SURPRISE_BLACKOUT_S and may_be_surprise(event):
        codes += (CAL_POST_SURPRISE,)
    return codes


def _offered(events: Sequence[CalendarEvent], now_epoch: int) -> tuple[CalendarEvent, ...]:
    """The MAX_OFFERED_EVENTS events nearest to now (surprises first on ties), by time.

    Nearest-first keeps the events that can still set a blackout, which is what a
    caller carries into the next assessment.
    """
    nearest = sorted(events, key=lambda e: (
        abs(e.time_epoch - now_epoch), not may_be_surprise(e), e.event_id))
    return tuple(sorted(nearest[:MAX_OFFERED_EVENTS], key=event_order))


def _require_epoch(name: str, value: object) -> None:
    # bool is an int subclass; a stray flag must not read as 1970-01-01.
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be int UTC seconds, got {type(value).__name__}")
    if not 0 <= value <= MAX_SUPPORTED_EPOCH:
        raise ValueError(f"{name} {value} is outside [0, {MAX_SUPPORTED_EPOCH}] UTC seconds")


def _decision_start(decision_epoch: int, now_epoch: int) -> int:
    _require_epoch("decision_epoch", decision_epoch)
    return min(decision_epoch, now_epoch)


def _require_horizon(horizon_s: object) -> None:
    if isinstance(horizon_s, bool) or not isinstance(horizon_s, int):
        raise TypeError("horizon_s must be int seconds")
    if not 0 <= horizon_s <= MAX_ASSESS_HORIZON_S:
        raise ValueError(f"horizon_s must be within [0, {MAX_ASSESS_HORIZON_S}]")
