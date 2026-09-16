"""
Hand-maintained USD HIGH events for the calendar veto (fallback and cross-check).

FOMC 2026 statement 14:00 and press conference 14:30 New York; NFP 08:30 New
York for 2026-2027 by the BLS guideline. CPI is left to MT5 and the 08:30 data
bar block: its 2026 dates cannot be justified here (the 2025 shutdown moved
several). `market.calendar` re-exports everything public here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Final

from ..cycle_types import CalendarEvent
from .sessions import NEW_YORK, SATURDAY

BLACKOUT_CURRENCY: Final[str] = "USD"

MT5_SOURCE: Final[str] = "mt5"
STATIC_SOURCE: Final[str] = "static"
CODE_FOMC_STATEMENT: Final[str] = "fomc-statement"
CODE_FOMC_PRESS_CONFERENCE: Final[str] = "fomc-press-conference"
CODE_NONFARM_PAYROLLS: Final[str] = "nonfarm-payrolls"
FOMC_STATEMENT_NY: Final[time] = time(14, 0)
FOMC_PRESS_CONFERENCE_NY: Final[time] = time(14, 30)
NFP_RELEASE_NY: Final[time] = time(8, 30)
# Static releases whose outcome can surprise (the press conference is a speech):
# without an actual they extend the post-release blackout (market.calendar).
SURPRISE_PRONE_STATIC_CODES: Final[frozenset[str]] = frozenset(
    {CODE_NONFARM_PAYROLLS, CODE_FOMC_STATEMENT})
_FOMC_RELEASES: Final[tuple[tuple[str, time], ...]] = (
    (CODE_FOMC_STATEMENT, FOMC_STATEMENT_NY),
    (CODE_FOMC_PRESS_CONFERENCE, FOMC_PRESS_CONFERENCE_NY))
# Federal Reserve 2026 schedule, second (statement) day of each meeting.
FOMC_STATEMENT_DAYS_2026: Final[tuple[date, ...]] = (
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
    date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9),
)
NFP_RULE_YEARS: Final[tuple[int, ...]] = (2026, 2027)
# Days for which every static kind is known. 2027 FOMC dates are not in the table,
# so an empty MT5 block cannot be vouched for in 2027.
STATIC_COVERAGE_START: Final[date] = date(2026, 1, 1)
STATIC_COVERAGE_END: Final[date] = date(2026, 12, 31)

JANUARY: Final[int] = 1
JULY: Final[int] = 7
DECEMBER: Final[int] = 12
# BLS: the release is the third Friday after the reference week (Sunday-Saturday
# holding the 12th of the prior month); Saturday + 20 days is that Friday.
NFP_REFERENCE_DAY: Final[int] = 12
NFP_DAYS_AFTER_REFERENCE_WEEK: Final[int] = 20
# December data is never released on Jan 1-3 (2015, 2016, 2020, 2021: one week later).
NFP_JANUARY_EARLIEST_DAY: Final[int] = 4
# A Friday July 3 (observed holiday) or July 4 moves the release to Thursday.
NFP_JULY_HOLIDAY_DAYS: Final[tuple[int, ...]] = (3, 4)

_UNIX_EPOCH: Final[datetime] = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ONE_SECOND: Final[timedelta] = timedelta(seconds=1)
_ONE_DAY: Final[timedelta] = timedelta(days=1)
_ONE_WEEK: Final[timedelta] = timedelta(weeks=1)


def event_order(event: CalendarEvent) -> tuple[int, str]:
    return (event.time_epoch, event.event_id)


def _utc_day(epoch: int) -> date:
    return (_UNIX_EPOCH + timedelta(seconds=epoch)).date()


def _new_york_epoch(day: date, clock: time) -> int:
    # 08:30 and 14:00 are hours away from the 02:00 DST switch, so each maps to one instant.
    return (datetime.combine(day, clock, tzinfo=NEW_YORK) - _UNIX_EPOCH) // _ONE_SECOND


def _static_event(code: str, day: date, clock: time) -> CalendarEvent:
    epoch = _new_york_epoch(day, clock)
    return CalendarEvent(
        event_id=f"{STATIC_SOURCE}:{code}:{epoch}", source="static", time_epoch=epoch,
        currency=BLACKOUT_CURRENCY, importance="HIGH", code=code,
    )


@dataclass(frozen=True)
class StaticCalendar:
    """Hand-maintained USD HIGH events and the UTC days for which they are complete."""

    events: tuple[CalendarEvent, ...]
    first_day: date
    last_day: date

    def __post_init__(self) -> None:
        if self.first_day > self.last_day:
            raise ValueError("static calendar coverage must not end before it starts")
        object.__setattr__(self, "events", tuple(sorted(self.events, key=event_order)))

    def covers(self, start_epoch: int, end_epoch: int) -> bool:
        """True when every UTC day from start to end lies inside the coverage."""
        return self.first_day <= _utc_day(start_epoch) and _utc_day(end_epoch) <= self.last_day


def nfp_release_day(year: int, month: int) -> date:
    """Employment Situation release day for `month` (data of the prior month).

    Follows the BLS guideline, which matches published days such as 2017-03-10,
    2019-11-01, 2020-05-08, 2020-07-02 and 2021-01-08; the plain "first Friday"
    rule misses several of them. Shutdown reschedules cannot be predicted: MT5
    stays authoritative, and a static event only ever adds a blackout.
    """
    if not JANUARY <= month <= DECEMBER:
        raise ValueError(f"month must be 1-12, got {month}")
    ref_year, ref_month = (year - 1, DECEMBER) if month == JANUARY else (year, month - 1)
    reference = date(ref_year, ref_month, NFP_REFERENCE_DAY)
    week_end = reference + timedelta(days=(SATURDAY - reference.weekday()) % 7)
    release = week_end + timedelta(days=NFP_DAYS_AFTER_REFERENCE_WEEK)
    if month == JANUARY and release.day < NFP_JANUARY_EARLIEST_DAY:
        return release + _ONE_WEEK
    if month == JULY and release.day in NFP_JULY_HOLIDAY_DAYS:
        return release - _ONE_DAY
    return release


def static_us_events(fomc_days: Iterable[date] = FOMC_STATEMENT_DAYS_2026,
                     nfp_years: Iterable[int] = NFP_RULE_YEARS) -> tuple[CalendarEvent, ...]:
    """FOMC statement + press conference and NFP releases, sorted by time."""
    fomc = (_static_event(code, day, clock)
            for day in fomc_days for code, clock in _FOMC_RELEASES)
    nfp = (_static_event(CODE_NONFARM_PAYROLLS, nfp_release_day(year, month), NFP_RELEASE_NY)
           for year in nfp_years for month in range(JANUARY, DECEMBER + 1))
    return tuple(sorted((*fomc, *nfp), key=event_order))


STATIC_CALENDAR: Final[StaticCalendar] = StaticCalendar(
    events=static_us_events(), first_day=STATIC_COVERAGE_START, last_day=STATIC_COVERAGE_END)
