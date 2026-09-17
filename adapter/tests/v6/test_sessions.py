"""
Session calendar tests, pinned to the 2026 DST calendar.

US DST: Sun 2026-03-08 07:00 UTC -> Sun 2026-11-01 06:00 UTC.
EU DST: Sun 2026-03-29 01:00 UTC -> Sun 2026-10-25 01:00 UTC.
2026-03-09..03-27 and 2026-10-26..10-30 are the one-hour US/EU mismatch weeks.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from app.v6.market.sessions import (
    OpeningRange,
    SessionState,
    is_london_dst,
    is_new_york_dst,
    opening_range_windows,
    session_state,
)

HOUR = 3600


def utc(y: int, m: int, d: int, hh: int = 0, mm: int = 0, ss: int = 0) -> int:
    return int(datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc).timestamp())


# Representative weekdays per season.
SUMMER = (2026, 7, 15)  # Wed, BST + EDT
WINTER = (2026, 1, 14)  # Wed, GMT + EST
MISMATCH_MAR = (2026, 3, 18)  # Wed, GMT + EDT
MISMATCH_OCT = (2026, 10, 28)  # Wed, GMT + EDT


# --- DST helpers -----------------------------------------------------------


@pytest.mark.parametrize(
    ("fn", "epoch", "expected"),
    [
        (is_new_york_dst, utc(2026, 3, 8, 6, 59, 59), False),
        (is_new_york_dst, utc(2026, 3, 8, 7), True),
        (is_new_york_dst, utc(2026, 11, 1, 5, 59, 59), True),
        (is_new_york_dst, utc(2026, 11, 1, 6), False),
        (is_new_york_dst, utc(*MISMATCH_MAR, 12), True),
        (is_new_york_dst, utc(*WINTER, 12), False),
        (is_london_dst, utc(2026, 3, 29, 0, 59, 59), False),
        (is_london_dst, utc(2026, 3, 29, 1), True),
        (is_london_dst, utc(2026, 10, 25, 0, 59, 59), True),
        (is_london_dst, utc(2026, 10, 25, 1), False),
        (is_london_dst, utc(*MISMATCH_MAR, 12), False),
        (is_london_dst, utc(*MISMATCH_OCT, 12), False),
        (is_london_dst, utc(*SUMMER, 12), True),
    ],
)
def test_dst_flags_flip_at_the_transition_instant(fn, epoch: int, expected: bool) -> None:
    assert fn(epoch) is expected


# --- main window -------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "start_h", "end_h"),
    [
        (SUMMER, 11, 17),
        (WINTER, 12, 18),
        (MISMATCH_MAR, 12, 17),
        (MISMATCH_OCT, 12, 17),
        ((2026, 3, 6), 12, 18),  # Fri before the US switch
        ((2026, 3, 9), 12, 17),  # Mon, first mismatch day
        ((2026, 3, 27), 12, 17),  # Fri, last mismatch day in spring
        ((2026, 3, 30), 11, 17),  # Mon after the EU switch
        ((2026, 10, 23), 11, 17),  # Fri before the EU switch
        ((2026, 10, 26), 12, 17),  # Mon, first autumn mismatch day
        ((2026, 10, 30), 12, 17),  # Fri, last autumn mismatch day
        ((2026, 11, 2), 12, 18),  # Mon after the US switch
    ],
)
def test_main_window_follows_london_noon_to_new_york_one_pm(
    day: tuple[int, int, int], start_h: int, end_h: int
) -> None:
    assert session_state(utc(*day, start_h) - 1).in_main_window is False
    assert session_state(utc(*day, start_h)).in_main_window is True
    assert session_state(utc(*day, end_h) - 1).in_main_window is True
    assert session_state(utc(*day, end_h)).in_main_window is False


def test_main_window_is_closed_on_saturday() -> None:
    state = session_state(utc(2026, 7, 18, 14))
    assert state.in_main_window is False
    assert state.main_window_third == "outside"


@pytest.mark.parametrize(
    ("day", "boundaries"),
    [
        (SUMMER, ((11, 0), (13, 0), (15, 0), (17, 0))),
        (WINTER, ((12, 0), (14, 0), (16, 0), (18, 0))),
        (MISMATCH_MAR, ((12, 0), (13, 40), (15, 20), (17, 0))),
    ],
)
def test_main_window_thirds_split_the_window_evenly(
    day: tuple[int, int, int], boundaries: tuple[tuple[int, int], ...]
) -> None:
    edges = [utc(*day, hh, mm) for hh, mm in boundaries]
    labels = ("early", "mid", "late")
    assert session_state(edges[0] - 1).main_window_third == "outside"
    for label, lo, hi in zip(labels, edges, edges[1:]):
        assert session_state(lo).main_window_third == label
        assert session_state(hi - 1).main_window_third == label
    assert session_state(edges[-1]).main_window_third == "outside"


# --- US data bar ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "bar_h", "other_h"),
    [
        (SUMMER, 12, 13),
        (WINTER, 13, 12),
        (MISMATCH_MAR, 12, 13),
        (MISMATCH_OCT, 12, 13),
    ],
)
def test_us_data_block_tracks_new_york_half_past_eight(
    day: tuple[int, int, int], bar_h: int, other_h: int
) -> None:
    assert session_state(utc(*day, bar_h, 14, 59)).us_data_block is False
    assert session_state(utc(*day, bar_h, 15)).us_data_block is True
    assert session_state(utc(*day, bar_h, 30)).us_data_block is True
    assert session_state(utc(*day, bar_h, 44, 59)).us_data_block is True
    assert session_state(utc(*day, bar_h, 45)).us_data_block is False
    assert session_state(utc(*day, other_h, 30)).us_data_block is False


# --- weekend -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("friday", "close_h"),
    [
        ((2026, 7, 17), 21),
        ((2026, 1, 16), 22),
        ((2026, 3, 6), 22),  # EST, two days before the US switch
        ((2026, 10, 30), 21),  # EDT, two days before the US switch back
    ],
)
def test_weekend_starts_at_friday_new_york_five_pm(
    friday: tuple[int, int, int], close_h: int
) -> None:
    assert session_state(utc(*friday, close_h) - 1).weekend is False
    state = session_state(utc(*friday, close_h))
    assert state.weekend is True
    assert state.phase == "weekend"
    assert state.entries_allowed is False


@pytest.mark.parametrize(
    ("sunday", "open_h"),
    [
        ((2026, 7, 19), 21),
        ((2026, 1, 18), 22),
        ((2026, 3, 8), 21),  # US switches to EDT that morning
        ((2026, 11, 1), 22),  # US switches back to EST that morning
    ],
)
def test_weekend_ends_at_sunday_new_york_five_pm_into_rollover(
    sunday: tuple[int, int, int], open_h: int
) -> None:
    before = session_state(utc(*sunday, open_h) - 1)
    after = session_state(utc(*sunday, open_h))
    assert before.weekend is True
    assert after.weekend is False
    assert after.phase == "rollover"
    assert after.rollover_block is True


def test_saturday_switches_off_every_weekday_event() -> None:
    # 14:00 UTC is 15:00 London (PM fix) and 10:00 New York in July.
    state = session_state(utc(2026, 7, 18, 14))
    assert (state.in_main_window, state.main_window_third) == (False, "outside")
    assert (state.lbma_pause, state.continuation_allowed) == (False, False)
    assert session_state(utc(2026, 7, 18, 12, 30)).us_data_block is False


@pytest.mark.parametrize(
    ("epoch", "phase", "reasons"),
    [
        (utc(*SUMMER, 12, 30), "overlap", ("US_DATA_BAR",)),
        (utc(*SUMMER, 14), "overlap", ("LBMA_PAUSE",)),
        (utc(*SUMMER, 4), "asia", ("OUTSIDE_TRADING_HOURS",)),
        (utc(*SUMMER, 9, 30), "london", ("LBMA_PAUSE",)),
        (utc(*SUMMER, 20), "ny_late", ("OUTSIDE_TRADING_HOURS",)),
        (utc(2026, 7, 18, 12), "weekend", ("WEEKEND", "OUTSIDE_TRADING_HOURS")),
        (utc(2026, 7, 17, 21, 30), "weekend", ("WEEKEND", "ROLLOVER", "OUTSIDE_TRADING_HOURS")),
    ],
)
def test_block_reasons_name_every_active_block(
    epoch: int, phase: str, reasons: tuple[str, ...]
) -> None:
    state = session_state(epoch)
    assert state.phase == phase
    assert state.block_reasons == reasons
    assert state.entries_allowed is False


# --- rollover and continuation ---------------------------------------------------


@pytest.mark.parametrize(
    ("day", "start_h"),
    [(SUMMER, 21), (WINTER, 22), (MISMATCH_MAR, 21), (MISMATCH_OCT, 21)],
)
def test_rollover_block_is_new_york_five_to_seven_pm(
    day: tuple[int, int, int], start_h: int
) -> None:
    start = utc(*day, start_h)
    assert session_state(start - 1).rollover_block is False
    assert session_state(start - 1).phase == "ny_late"
    assert session_state(start).rollover_block is True
    assert session_state(start).phase == "rollover"
    assert session_state(start).block_reasons == ("ROLLOVER", "OUTSIDE_TRADING_HOURS")
    assert session_state(start + 2 * HOUR - 1).rollover_block is True
    assert session_state(start + 2 * HOUR).rollover_block is False
    assert session_state(start + 2 * HOUR).phase == "asia"


@pytest.mark.parametrize(
    ("day", "main_end_h"),
    [(SUMMER, 17), (WINTER, 18), (MISMATCH_MAR, 17), (MISMATCH_OCT, 17)],
)
def test_continuation_is_off_from_main_window_end_until_rollover_ends(
    day: tuple[int, int, int], main_end_h: int
) -> None:
    main_end = utc(*day, main_end_h)
    rollover_end = main_end + 6 * HOUR
    assert session_state(main_end - 1).continuation_allowed is True
    assert session_state(main_end).continuation_allowed is False
    assert session_state(rollover_end - 1).continuation_allowed is False
    assert session_state(rollover_end).continuation_allowed is True


# --- LBMA auctions -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "fix_h", "fix_m"),
    [
        (SUMMER, 9, 30),
        (SUMMER, 14, 0),
        (WINTER, 10, 30),
        (WINTER, 15, 0),
        (MISMATCH_MAR, 15, 0),
        (MISMATCH_OCT, 10, 30),
    ],
)
def test_lbma_pause_is_one_minute_before_to_two_minutes_after_the_fix(
    day: tuple[int, int, int], fix_h: int, fix_m: int
) -> None:
    fix = utc(*day, fix_h, fix_m)
    assert session_state(fix - 61).lbma_pause is False
    assert session_state(fix - 60).lbma_pause is True
    assert session_state(fix + 119).lbma_pause is True
    assert session_state(fix + 120).lbma_pause is False


@pytest.mark.parametrize("day", [WINTER, MISMATCH_MAR])
def test_lbma_pm_pause_does_not_fire_at_the_summer_utc_hour(day: tuple[int, int, int]) -> None:
    assert session_state(utc(*day, 14)).lbma_pause is False


# --- phases and Asia --------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "clock", "phase"),
    [
        (SUMMER, (2, 0), "asia"),
        (SUMMER, (6, 59), "asia"),
        (SUMMER, (7, 0), "london"),
        (SUMMER, (10, 59), "london"),
        (SUMMER, (11, 0), "overlap"),
        (SUMMER, (17, 0), "ny_late"),
        (SUMMER, (23, 0), "asia"),
        (WINTER, (7, 59), "asia"),
        (WINTER, (8, 0), "london"),
        (WINTER, (12, 0), "overlap"),
        (WINTER, (18, 0), "ny_late"),
        (MISMATCH_MAR, (8, 0), "london"),
        (MISMATCH_MAR, (11, 30), "london"),
        (MISMATCH_MAR, (17, 0), "ny_late"),
        (WINTER, (23, 59), "rollover"),  # winter rollover spills past UTC midnight
        ((2026, 1, 15), (0, 0), "asia"),
    ],
)
def test_phase_follows_local_exchange_clocks(
    day: tuple[int, int, int], clock: tuple[int, int], phase: str
) -> None:
    assert session_state(utc(*day, *clock)).phase == phase


@pytest.mark.parametrize(
    ("epoch", "expected"),
    [
        (utc(*SUMMER, 2, 59, 59), False),
        (utc(*SUMMER, 3), True),
        (utc(*WINTER, 4, 59, 59), True),
        (utc(*WINTER, 5), False),
        (utc(2026, 7, 18, 4), False),
    ],
)
def test_asia_quiet_is_fixed_utc_and_weekday_only(epoch: int, expected: bool) -> None:
    assert session_state(epoch).asia_quiet is expected


def test_clean_main_window_bar_allows_entries() -> None:
    state = session_state(utc(*SUMMER, 15, 30))
    assert state == SessionState(
        phase="overlap", in_main_window=True, main_window_third="late",
        rollover_block=False, weekend=False, lbma_pause=False, us_data_block=False,
        continuation_allowed=True, asia_quiet=False, entries_allowed=True, block_reasons=(),
        in_trading_window=True,
    )
    assert state.quality == "prime"


# --- opening ranges ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "london_h", "ny_start"),
    [(SUMMER, 7, (13, 30)), (WINTER, 8, (14, 30)),
     (MISMATCH_MAR, 8, (13, 30)), (MISMATCH_OCT, 8, (13, 30))],
)
def test_opening_ranges_follow_local_opens(
    day: tuple[int, int, int], london_h: int, ny_start: tuple[int, int]
) -> None:
    ny_open = utc(*day, *ny_start)
    expected = (
        OpeningRange("london_open", utc(*day, london_h), utc(*day, london_h, 30)),
        OpeningRange("ny_open", ny_open, ny_open + 30 * 60),
    )
    assert opening_range_windows(utc(*day)) == expected
    assert opening_range_windows(utc(*day, 23, 59, 59)) == expected


@pytest.mark.parametrize("day", [(2026, 7, 18), (2026, 7, 19)])
def test_opening_ranges_are_empty_on_weekend_days(day: tuple[int, int, int]) -> None:
    assert opening_range_windows(utc(*day, 12)) == ()


# --- invariants and validation -------------------------------------------------------


def test_invariants_hold_across_the_spring_mismatch() -> None:
    step = 5 * 60
    for epoch in range(utc(2026, 3, 1), utc(2026, 4, 6), step):
        state = session_state(epoch)
        assert state.entries_allowed is (not state.block_reasons)
        assert state.in_main_window is (state.main_window_third != "outside")
        assert state.in_main_window is (state.phase == "overlap")
        assert state.in_trading_window >= state.in_main_window
        assert (not state.entries_allowed) or state.in_trading_window
        assert (state.quality == "closed") is (not state.in_trading_window)
        hour = datetime.fromtimestamp(epoch, tz=timezone.utc).hour
        london, new_york = int(is_london_dst(epoch)), int(is_new_york_dst(epoch))
        if state.in_main_window:
            assert 12 - london <= hour < 18 - new_york
        if state.in_trading_window:
            assert 8 - london <= hour < 21 - new_york


def test_results_are_frozen() -> None:
    state = session_state(utc(*SUMMER, 12))
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.phase = "asia"  # type: ignore[misc]
    window = opening_range_windows(utc(*SUMMER))[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        window.start_epoch = 0  # type: ignore[misc]


PUBLIC_FNS = (session_state, opening_range_windows, is_new_york_dst, is_london_dst)


@pytest.mark.parametrize(
    ("bad", "error"),
    [(1.5, TypeError), ("1789560000", TypeError), (None, TypeError), (True, TypeError),
     (-1, ValueError), (10**12, ValueError)],  # 10**12: milliseconds, not seconds
)
@pytest.mark.parametrize("fn", PUBLIC_FNS)
def test_invalid_epochs_are_rejected(fn, bad, error: type[Exception]) -> None:
    with pytest.raises(error):
        fn(bad)
