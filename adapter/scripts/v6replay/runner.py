"""
The sequential replay: every stored M15 bar in order, with the position simulation.

A bar whose packet would be served while the slot is free and trades remain
becomes a trade: it occupies the single V6 position from the bar close until
the time barrier and counts toward that UTC day's entries. Exposure is carried
forward only, so a bar's record depends on earlier bars and never on later ones.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final

from app.v6.config import V6Settings
from app.v6.schemas.snapshot import CalendarEventBlock
from app.v6.types import TIMEFRAME_SECONDS

from .data import BarSet
from .evaluate import SECONDS_PER_DAY, BarRecord, evaluate_bar
from .synth import Exposure

M15_S: Final[int] = TIMEFRAME_SECONDS["M15"]


@dataclass(frozen=True)
class ReplayWindow:
    """Bar-close range [start, end) in UTC epoch seconds; None means unbounded."""

    start: int | None = None
    end: int | None = None

    def contains(self, as_of: int) -> bool:
        return ((self.start is None or as_of >= self.start)
                and (self.end is None or as_of < self.end))


def _day(epoch: int) -> int:
    return epoch // SECONDS_PER_DAY


def advance(exposure: Exposure, record: BarRecord, settings: V6Settings) -> Exposure:
    """Exposure after `record`: a trade takes the slot and counts as an entry."""
    if not record.traded:
        return exposure
    chosen = next(item for item in record.candidates if item.in_packet)
    return replace(exposure, trades_today=exposure.trades_today + 1,
                   open_until=record.as_of + settings.time_barrier_s, side=chosen.side,
                   entry=chosen.entry, opened_at=record.as_of)


def _for_day(exposure: Exposure, previous_as_of: int | None, as_of: int) -> Exposure:
    if previous_as_of is None or _day(previous_as_of) == _day(as_of):
        return exposure
    return replace(exposure, trades_today=0)


def run_replay(bars: BarSet, stored_events: Sequence[CalendarEventBlock],
               settings: V6Settings,
               window: ReplayWindow = ReplayWindow()) -> tuple[BarRecord, ...]:
    """One BarRecord per stored M15 bar whose close lies in `window`."""
    selected = tuple(bar for bar in bars.bars("M15") if window.contains(bar.t + M15_S))
    exposure = Exposure()
    previous: int | None = None
    records: list[BarRecord] = []
    for bar in selected:
        as_of = bar.t + M15_S
        exposure = _for_day(exposure, previous, as_of)
        record = evaluate_bar(bar, bars, stored_events, settings, exposure)
        exposure = advance(exposure, record, settings)
        records.append(record)
        previous = as_of
    return tuple(records)
