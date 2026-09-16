"""
Prior-day levels and ATR(D1) ignore weekend-session stub bars.

On a GMT+0 server (the Exness trial accounts the plan allows) the Sunday reopen
builds a 2-3 h D1 bar dated Sunday; on MetaQuotes-Demo (UTC+3) the reopen falls
inside Monday's bar, which opens Sunday 21:00 UTC and is a full day.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.v6.setups.base import (
    MIN_D1_OPEN_HOURS, _open_hours, atr_d1, full_days, reference_levels,
)
from app.v6.types import Bar

from .setup_fixtures_v6 import day_bar, setup_input

DAY = 86_400


def utc(*parts: int) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp())


FRI = utc(2026, 9, 11)
SAT = utc(2026, 9, 12)
SUN = utc(2026, 9, 13)
MON = utc(2026, 9, 14)
MONDAY_NOON_CLOSE = MON + 12 * 3600 + 900


@pytest.mark.parametrize(("day_open", "hours"), [
    (FRI, 21),                          # GMT+0 Friday: the weekend starts 17:00 New York
    (SAT, 0),
    (SUN, 3),                           # GMT+0 Sunday stub from 21:00 UTC (EDT)
    (MON, 24),
    (SUN + 21 * 3600, 24),              # UTC+3 server: Monday's bar opens Sunday 21:00 UTC
    (utc(2026, 11, 8), 2),              # EST: the reopen is 22:00 UTC
])
def test_open_hours_per_d1_bar(day_open: int, hours: int) -> None:
    assert _open_hours(day_open) == hours


def test_full_days_drop_weekend_stubs_only() -> None:
    bars = tuple(day_bar(t, 4310.0, 4290.0) for t in (FRI, SAT, SUN, MON))
    assert [bar.t for bar in full_days(bars)] == [FRI, MON]
    assert MIN_D1_OPEN_HOURS > 3


def test_monday_prior_day_levels_skip_the_sunday_stub() -> None:
    daily = (day_bar(FRI, 4350.0, 4280.0), day_bar(SUN, 4302.0, 4298.0))
    inp = setup_input(MONDAY_NOON_CLOSE, D1=daily)

    levels = {level.kind: level for level in reference_levels(inp)}

    assert (levels["pdh"].price, levels["pdl"].price) == (4350.0, 4280.0)
    assert levels["pdh"].known_at == FRI + DAY


def test_a_utc_plus_3_monday_bar_is_the_prior_day() -> None:
    monday_bar = day_bar(SUN + 21 * 3600, 4330.0, 4300.0)     # closes Monday 21:00 UTC
    inp = setup_input(utc(2026, 9, 15, 12, 15), D1=(day_bar(FRI - 3 * 3600, 4350.0, 4280.0),
                                                    monday_bar))
    levels = {level.kind: level.price for level in reference_levels(inp)}
    assert (levels["pdh"], levels["pdl"]) == (4330.0, 4300.0)


def _daily(start: int, days: int, width: float) -> tuple[Bar, ...]:
    return tuple(day_bar(start + i * DAY, 4300.0 + width / 2, 4300.0 - width / 2)
                 for i in range(days))


def test_daily_atr_ignores_stub_ranges() -> None:
    weeks = _daily(MON - 28 * DAY, 26, 20.0)                  # Mon 2026-08-17 .. Fri 09-11
    narrow_stubs = tuple(bar if full_days((bar,)) else day_bar(bar.t, 4300.5, 4299.5)
                         for bar in weeks)
    inp_full = setup_input(MONDAY_NOON_CLOSE, D1=weeks)
    inp_stubbed = setup_input(MONDAY_NOON_CLOSE, D1=narrow_stubs + (day_bar(SUN, 4301.0, 4299.0),))

    assert atr_d1(inp_full) == pytest.approx(20.0)
    assert atr_d1(inp_stubbed) == pytest.approx(20.0)
