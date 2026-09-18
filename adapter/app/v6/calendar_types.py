"""
The calendar records of a cycle: one scheduled release, and the code-computed news veto
at the decision time. `cycle_types` re-exports both, so they are imported from there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CalendarSource = Literal["mt5", "forexfactory", "static"]
Importance = Literal["NONE", "LOW", "MODERATE", "HIGH"]


@dataclass(frozen=True)
class CalendarEvent:
    """One scheduled release, reduced to ids, enums, times and numbers."""

    event_id: str            # "<source>:<id>", unique across sources, matches ID_PATTERN
    source: CalendarSource
    time_epoch: int
    currency: str
    importance: Importance
    code: str                # slug, e.g. "nonfarm-payrolls"
    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None


@dataclass(frozen=True)
class CalendarAssessment:
    """Code-computed news veto; applies even when every LLM is down."""

    as_of_epoch: int
    blackout: bool
    codes: tuple[str, ...]                 # CAL_* codes
    next_event_minutes: float | None
    last_event_minutes_ago: float | None
    stale: bool
    events: tuple[CalendarEvent, ...] = ()  # the events offered to the news desk

    @property
    def event_ids(self) -> frozenset[str]:
        return frozenset(event.event_id for event in self.events)
