"""
Trading-session calendar for XAUUSD, anchored to exchange-local clocks.

Every window is defined in Europe/London or America/New_York wall-clock time and
converted per call, never as fixed UTC or broker-server hours: the US and EU move
their clocks on different Sundays, so a fixed-UTC calendar is silently one hour
wrong for weeks each year (knowledge/03 section 2). Inputs are UTC epoch seconds
and nothing here reads the system clock, so backtests and live runs agree.

Entries are allowed in London + New York trading hours (London 08:00 to New York
16:00: 07:00-20:00 UTC in summer, 08:00-21:00 in winter) except in the hard
blocks: weekend, rollover, the LBMA fix pauses and the US data bar. The main
window (London noon to New York 13:00, kn/03's core 11:00-17:00 UTC), its thirds,
the post-London continuation zone and `quality` are information for the agents,
not blocks (user decision 2026-09-17).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Final, Literal
from zoneinfo import ZoneInfo

Phase = Literal["weekend", "rollover", "asia", "london", "overlap", "ny_late"]
MainWindowThird = Literal["early", "mid", "late", "outside"]
# prime: the main window; active: London morning; thin: New York after 13:00 (kn/03:
# liquidity falls, retests fail more often); closed: outside the trading window.
SessionQuality = Literal["prime", "active", "thin", "closed"]

LONDON: Final[ZoneInfo] = ZoneInfo("Europe/London")
NEW_YORK: Final[ZoneInfo] = ZoneInfo("America/New_York")

# Upper bound keeps callers from passing milliseconds by mistake.
MAX_SUPPORTED_EPOCH: Final[int] = 4_102_444_800  # 2100-01-01T00:00:00Z

FRIDAY: Final[int] = 4
SATURDAY: Final[int] = 5
SUNDAY: Final[int] = 6

# New York local clock. The broker day closes at 17:00 NY; the weekend and the
# daily rollover both hang off that close.
NY_DAILY_CLOSE: Final[time] = time(17, 0)
NY_ROLLOVER_END: Final[time] = time(19, 0)
NY_MAIN_WINDOW_END: Final[time] = time(13, 0)
# London + New York trading hours end at the 16:00 New York close: 20:00 UTC in summer,
# where MetaQuotes-Demo stops quoting (market.broker_hours), 21:00 UTC in winter.
NY_TRADING_END: Final[time] = time(16, 0)
# +/-15 minutes around the 08:30 ET macro release bar.
NY_DATA_BLOCK_START: Final[time] = time(8, 15)
NY_DATA_BLOCK_END: Final[time] = time(8, 45)
NY_OPEN_RANGE_START: Final[time] = time(9, 30)
NY_OPEN_RANGE_END: Final[time] = time(10, 0)

# London local clock.
LONDON_OPEN: Final[time] = time(8, 0)
LONDON_OPEN_RANGE_END: Final[time] = time(8, 30)
LONDON_MAIN_WINDOW_START: Final[time] = time(12, 0)
LBMA_FIXES: Final[tuple[time, ...]] = (time(10, 30), time(15, 0))
LBMA_PAUSE_BEFORE_S: Final[int] = 60
LBMA_PAUSE_AFTER_S: Final[int] = 120

# The genuinely dead Asian stretch is a fixed UTC band in the evidence base.
ASIA_QUIET_START_UTC: Final[time] = time(3, 0)
ASIA_QUIET_END_UTC: Final[time] = time(5, 0)

MAIN_WINDOW_THIRD_LABELS: Final[tuple[MainWindowThird, ...]] = ("early", "mid", "late")
OUTSIDE: Final[MainWindowThird] = "outside"

LONDON_OPEN_RANGE_NAME: Final[str] = "london_open"
NY_OPEN_RANGE_NAME: Final[str] = "ny_open"

BLOCK_WEEKEND: Final[str] = "WEEKEND"
BLOCK_ROLLOVER: Final[str] = "ROLLOVER"
BLOCK_OUTSIDE_TRADING_HOURS: Final[str] = "OUTSIDE_TRADING_HOURS"
BLOCK_LBMA_PAUSE: Final[str] = "LBMA_PAUSE"
BLOCK_US_DATA_BAR: Final[str] = "US_DATA_BAR"

QUALITY_PRIME: Final[SessionQuality] = "prime"
QUALITY_ACTIVE: Final[SessionQuality] = "active"
QUALITY_THIN: Final[SessionQuality] = "thin"
QUALITY_CLOSED: Final[SessionQuality] = "closed"

_UNIX_EPOCH: Final[datetime] = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ONE_SECOND: Final[timedelta] = timedelta(seconds=1)


@dataclass(frozen=True)
class SessionState:
    """
    Session facts for one instant.

    `entries_allowed` is True exactly when `block_reasons` is empty (hard blocks only).
    `continuation_allowed` is information: False in the post-London zone, where kn/03
    finds continuation setups failing more often; it never blocks, and it does not
    override `entries_allowed`. `in_main_window`, `main_window_third`, `asia_quiet`
    and `quality` are information too.
    """

    phase: Phase
    in_main_window: bool
    main_window_third: MainWindowThird
    rollover_block: bool
    weekend: bool
    lbma_pause: bool
    us_data_block: bool
    continuation_allowed: bool
    asia_quiet: bool
    entries_allowed: bool
    block_reasons: tuple[str, ...]
    in_trading_window: bool

    @property
    def quality(self) -> SessionQuality:
        """prime (main window), active (London morning), thin (NY afternoon), closed."""
        if not self.in_trading_window:
            return QUALITY_CLOSED
        if self.in_main_window:
            return QUALITY_PRIME
        return QUALITY_ACTIVE if self.phase == "london" else QUALITY_THIN


@dataclass(frozen=True)
class OpeningRange:
    """Half-open [start_epoch, end_epoch) opening-range window, UTC seconds."""

    name: str
    start_epoch: int
    end_epoch: int


# --- public API ----------------------------------------------------------------


def session_state(epoch: int) -> SessionState:
    """Classify a UTC epoch against the London/New York session calendar."""
    _validate_epoch(epoch)
    ny = _to_zone(epoch, NEW_YORK)
    london = _to_zone(epoch, LONDON)

    weekend = _is_weekend(epoch, ny)
    # Rollover is not weekday-gated: the Sunday reopen is the widest-spread
    # rollover of the week, and blocking on Friday/Saturday costs nothing.
    rollover = _within(epoch, NEW_YORK, ny.date(), NY_DAILY_CLOSE, NY_ROLLOVER_END)
    third = _main_window_third(epoch, london)
    in_main = third != OUTSIDE
    in_trading = _in_trading_window(epoch, london)
    lbma = _is_weekday(london.date()) and _in_lbma_pause(epoch, london.date())
    us_data = _is_weekday(ny.date()) and _within(
        epoch, NEW_YORK, ny.date(), NY_DATA_BLOCK_START, NY_DATA_BLOCK_END
    )
    # Main window end -> rollover end: retests fail more often after London
    # (knowledge/03). Reported to the agents; no longer a block.
    post_london = _within(epoch, NEW_YORK, ny.date(), NY_MAIN_WINDOW_END, NY_ROLLOVER_END)
    reasons = _block_reasons(
        weekend=weekend, rollover=rollover, in_trading=in_trading, lbma=lbma, us_data=us_data
    )
    return SessionState(
        phase=_phase(epoch, weekend=weekend, rollover=rollover, in_main=in_main,
                     ny=ny, london=london),
        in_main_window=in_main,
        main_window_third=third,
        rollover_block=rollover,
        weekend=weekend,
        lbma_pause=lbma,
        us_data_block=us_data,
        continuation_allowed=not (weekend or post_london),
        asia_quiet=not weekend and _is_asia_quiet(epoch),
        entries_allowed=not reasons,
        block_reasons=reasons,
        in_trading_window=in_trading,
    )


def opening_range_windows(epoch: int) -> tuple[OpeningRange, ...]:
    """
    London (08:00-08:30 London) and New York (09:30-10:00 New York) opening
    ranges for the UTC day containing `epoch`; empty on Saturday/Sunday UTC.
    """
    _validate_epoch(epoch)
    day = _to_zone(epoch, timezone.utc).date()
    if not _is_weekday(day):
        return ()
    # Both opens fall on the same calendar date in UTC and local time in every
    # DST combination, so the UTC date doubles as the local date.
    return (
        _opening_range(LONDON_OPEN_RANGE_NAME, LONDON, day, LONDON_OPEN, LONDON_OPEN_RANGE_END),
        _opening_range(NY_OPEN_RANGE_NAME, NEW_YORK, day, NY_OPEN_RANGE_START, NY_OPEN_RANGE_END),
    )


def is_new_york_dst(epoch: int) -> bool:
    """True while America/New_York observes daylight saving time (EDT)."""
    _validate_epoch(epoch)
    return _observes_dst(epoch, NEW_YORK)


def is_london_dst(epoch: int) -> bool:
    """True while Europe/London observes daylight saving time (BST)."""
    _validate_epoch(epoch)
    return _observes_dst(epoch, LONDON)


# --- helpers -----------------------------------------------------------------------


def _validate_epoch(epoch: int) -> None:
    # bool is an int subclass; a stray flag must not read as 1970-01-01.
    if isinstance(epoch, bool) or not isinstance(epoch, int):
        raise TypeError(f"epoch must be int UTC seconds, got {type(epoch).__name__}.")
    if not 0 <= epoch <= MAX_SUPPORTED_EPOCH:
        raise ValueError(
            f"epoch {epoch} is outside [0, {MAX_SUPPORTED_EPOCH}] UTC seconds "
            "(milliseconds passed by mistake?)."
        )


def _to_zone(epoch: int, zone: timezone | ZoneInfo) -> datetime:
    # Arithmetic from a fixed origin avoids the platform localtime() call that
    # datetime.fromtimestamp() makes, which misbehaves for some epochs on Windows.
    return (_UNIX_EPOCH + timedelta(seconds=epoch)).astimezone(zone)


def _local_epoch(zone: ZoneInfo, day: date, clock: time) -> int:
    # Every anchor used here sits hours away from the 01:00/02:00 local DST
    # transitions, so each wall-clock time maps to exactly one instant.
    local = datetime.combine(day, clock, tzinfo=zone)
    return (local - _UNIX_EPOCH) // _ONE_SECOND


def _within(epoch: int, zone: ZoneInfo, day: date, start: time, end: time) -> bool:
    return _local_epoch(zone, day, start) <= epoch < _local_epoch(zone, day, end)


def _observes_dst(epoch: int, zone: ZoneInfo) -> bool:
    offset = _to_zone(epoch, zone).dst()
    return offset is not None and offset != timedelta(0)


def _is_weekday(day: date) -> bool:
    return day.weekday() < SATURDAY


def _is_weekend(epoch: int, ny: datetime) -> bool:
    weekday = ny.weekday()
    if weekday == SATURDAY:
        return True
    if weekday not in (FRIDAY, SUNDAY):
        return False
    close = _local_epoch(NEW_YORK, ny.date(), NY_DAILY_CLOSE)
    return epoch >= close if weekday == FRIDAY else epoch < close


def _main_window_third(epoch: int, london: datetime) -> MainWindowThird:
    day = london.date()
    if not _is_weekday(day):
        return OUTSIDE
    # London noon to New York 13:00: 6 h when both zones agree on DST, 5 h in
    # the mismatch weeks. Anchoring on the London date is safe because the
    # whole window lies on that date in London, New York and UTC.
    start = _local_epoch(LONDON, day, LONDON_MAIN_WINDOW_START)
    end = _local_epoch(NEW_YORK, day, NY_MAIN_WINDOW_END)
    if not start <= epoch < end:
        return OUTSIDE
    parts = len(MAIN_WINDOW_THIRD_LABELS)
    width = (end - start) // parts
    index = min((epoch - start) // width, parts - 1)
    return MAIN_WINDOW_THIRD_LABELS[index]


def _in_trading_window(epoch: int, london: datetime) -> bool:
    """London 08:00 to New York 16:00 on a weekday.

    Anchoring on the London date is safe for the same reason as the main window:
    the whole window lies on that date in London, New York and UTC.
    """
    day = london.date()
    if not _is_weekday(day):
        return False
    start = _local_epoch(LONDON, day, LONDON_OPEN)
    return start <= epoch < _local_epoch(NEW_YORK, day, NY_TRADING_END)


def _in_lbma_pause(epoch: int, london_day: date) -> bool:
    fixes = (_local_epoch(LONDON, london_day, clock) for clock in LBMA_FIXES)
    return any(
        fix - LBMA_PAUSE_BEFORE_S <= epoch < fix + LBMA_PAUSE_AFTER_S for fix in fixes
    )


def _is_asia_quiet(epoch: int) -> bool:
    clock = _to_zone(epoch, timezone.utc).time()
    return ASIA_QUIET_START_UTC <= clock < ASIA_QUIET_END_UTC


def _phase(
    epoch: int,
    *,
    weekend: bool,
    rollover: bool,
    in_main: bool,
    ny: datetime,
    london: datetime,
) -> Phase:
    if weekend:
        return "weekend"
    if rollover:
        return "rollover"
    if _within(epoch, NEW_YORK, ny.date(), NY_MAIN_WINDOW_END, NY_DAILY_CLOSE):
        return "ny_late"
    if in_main:
        return "overlap"
    if _within(epoch, LONDON, london.date(), LONDON_OPEN, LONDON_MAIN_WINDOW_START):
        return "london"
    return "asia"


def _block_reasons(
    *, weekend: bool, rollover: bool, in_trading: bool, lbma: bool, us_data: bool
) -> tuple[str, ...]:
    ordered = (
        (weekend, BLOCK_WEEKEND),
        (rollover, BLOCK_ROLLOVER),
        (not in_trading, BLOCK_OUTSIDE_TRADING_HOURS),
        (lbma, BLOCK_LBMA_PAUSE),
        (us_data, BLOCK_US_DATA_BAR),
    )
    return tuple(code for active, code in ordered if active)


def _opening_range(
    name: str, zone: ZoneInfo, day: date, start: time, end: time
) -> OpeningRange:
    return OpeningRange(
        name=name,
        start_epoch=_local_epoch(zone, day, start),
        end_epoch=_local_epoch(zone, day, end),
    )
