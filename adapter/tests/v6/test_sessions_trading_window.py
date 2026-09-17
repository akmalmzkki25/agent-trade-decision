"""
The London + New York trading window and the session quality label (user decision
2026-09-17): entries run from the London open to the 16:00 New York close, and the
main window, its thirds and the post-London zone are information, not blocks.
"""

from __future__ import annotations

import pytest

from app.v6.market.sessions import session_state

from .test_sessions import MISMATCH_MAR, MISMATCH_OCT, SUMMER, WINTER, utc


# --- London + New York trading window ------------------------------------------------


@pytest.mark.parametrize(
    ("day", "start_h", "end_h"),
    [
        (SUMMER, 7, 20),        # BST + EDT
        (WINTER, 8, 21),        # GMT + EST
        (MISMATCH_MAR, 8, 20),  # GMT + EDT
        (MISMATCH_OCT, 8, 20),  # GMT + EDT
        ((2026, 3, 30), 7, 20),  # Mon after the EU switch
        ((2026, 11, 2), 8, 21),  # Mon after the US switch back
    ],
)
def test_trading_window_is_london_open_to_new_york_four_pm(
    day: tuple[int, int, int], start_h: int, end_h: int
) -> None:
    before, first = session_state(utc(*day, start_h) - 1), session_state(utc(*day, start_h))
    last, after = session_state(utc(*day, end_h) - 1), session_state(utc(*day, end_h))
    assert (before.in_trading_window, before.block_reasons) == (False, ("OUTSIDE_TRADING_HOURS",))
    assert (first.in_trading_window, first.entries_allowed, first.phase) == (True, True, "london")
    assert (last.in_trading_window, last.entries_allowed, last.phase) == (True, True, "ny_late")
    assert (after.in_trading_window, after.entries_allowed) == (False, False)


@pytest.mark.parametrize(
    ("clock", "quality", "third", "continuation"),
    [
        ((6, 45), "closed", "outside", True),
        ((7, 0), "active", "outside", True),     # London open
        ((10, 45), "active", "outside", True),
        ((11, 0), "prime", "early", True),       # main window
        ((16, 45), "prime", "late", True),
        ((17, 0), "thin", "outside", False),     # post-London: continuation discouraged
        ((19, 45), "thin", "outside", False),
        ((20, 0), "closed", "outside", False),
        ((22, 0), "closed", "outside", False),   # rollover
    ],
)
def test_session_quality_across_a_summer_day(
    clock: tuple[int, int], quality: str, third: str, continuation: bool
) -> None:
    state = session_state(utc(*SUMMER, *clock))
    assert (state.quality, state.main_window_third) == (quality, third)
    assert state.continuation_allowed is continuation
    # Quality and continuation are information: only the hard blocks stop entries.
    assert state.entries_allowed is (quality != "closed")


def test_weekend_days_are_closed() -> None:
    state = session_state(utc(2026, 7, 18, 12))
    assert (state.in_trading_window, state.quality) == (False, "closed")
