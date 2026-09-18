"""Entry hours: all_day drops only the London-NY window; hard blocks stay."""

from __future__ import annotations

import pytest

from app.v6.config import V6Settings
from app.v6.market.sessions import (
    BLOCK_OUTSIDE_TRADING_HOURS, BLOCK_ROLLOVER, entry_blocks, session_state,
)
from app.v6.risk import gates

ASIA_01_UTC = 1_789_606_800        # Thu 2026-09-17 01:00 UTC
OVERLAP_1245_UTC = 1_789_649_100   # Thu 2026-09-17 12:45 UTC
ROLLOVER_2130_UTC = 1_789_681_800  # Thu 2026-09-17 21:30 UTC (17:30 New York)


@pytest.mark.parametrize(("epoch", "all_day", "london_ny"), [
    (ASIA_01_UTC, (), (BLOCK_OUTSIDE_TRADING_HOURS,)),
    (OVERLAP_1245_UTC, (), ()),
    (ROLLOVER_2130_UTC, (BLOCK_ROLLOVER,), (BLOCK_ROLLOVER, BLOCK_OUTSIDE_TRADING_HOURS)),
])
def test_entry_blocks(epoch: int, all_day: tuple[str, ...], london_ny: tuple[str, ...]) -> None:
    state = session_state(epoch)
    assert entry_blocks(state, "all_day") == all_day
    assert entry_blocks(state, "london_ny") == london_ny


class _Context:
    """The two attributes `_session_gate` reads."""

    def __init__(self, epoch: int) -> None:
        self.session = session_state(epoch)
        self.as_of_epoch = epoch


@pytest.mark.parametrize(("hours", "passed"), [("all_day", True), ("london_ny", False)])
def test_the_session_gate_follows_entry_hours(hours: str, passed: bool) -> None:
    settings = V6Settings(_env_file=None, entry_hours=hours, broker_quote_gap_utc="")
    gate = gates._session_gate(_Context(ASIA_01_UTC), settings)  # noqa: SLF001
    assert gate.passed is passed
    assert f"hours={hours}" in gate.detail


def test_the_rollover_blocks_every_entry_hour() -> None:
    settings = V6Settings(_env_file=None, entry_hours="all_day", broker_quote_gap_utc="")
    gate = gates._session_gate(_Context(ROLLOVER_2130_UTC), settings)  # noqa: SLF001
    assert not gate.passed and gate.value == BLOCK_ROLLOVER
