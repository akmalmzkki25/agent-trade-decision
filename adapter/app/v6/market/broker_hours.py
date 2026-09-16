"""
Broker quote hours: facts of one trade server, not of the exchange calendar.

`market.sessions` models the London / New York calendar (weekend, rollover). A
server can also stop quoting on its own schedule: the probe of 2026-09-16
(MetaQuotes-Demo, UTC+3) saw no XAUUSD quotes daily from 20:00 to 22:00 UTC. No
bar forms and the EA sends no snapshot in that gap, so a missing bar there is
not a data hole. The gap is configurable (V6_BROKER_QUOTE_GAP_UTC) because it
belongs to the broker; winter hours are not verified yet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from .sessions import session_state

SECONDS_PER_MINUTE: Final[int] = 60
SECONDS_PER_HOUR: Final[int] = 3_600
SECONDS_PER_DAY: Final[int] = 86_400
DEFAULT_QUOTE_GAP_UTC: Final[str] = "20:00-22:00"
WINDOW_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"([01][0-9]|2[0-3]):([0-5][0-9])-([01][0-9]|2[0-3]):([0-5][0-9])")


@dataclass(frozen=True)
class DailyUtcWindow:
    """[start_s, end_s) seconds after 00:00 UTC, every day; an end before the start wraps."""

    start_s: int
    end_s: int

    def __post_init__(self) -> None:
        bounds = (self.start_s, self.end_s)
        if not all(isinstance(v, int) and 0 <= v < SECONDS_PER_DAY for v in bounds):
            raise ValueError("window bounds must be seconds within one UTC day")
        if self.start_s == self.end_s:
            raise ValueError("a daily window must not be empty")

    def contains(self, epoch: int) -> bool:
        second = epoch % SECONDS_PER_DAY
        if self.start_s < self.end_s:
            return self.start_s <= second < self.end_s
        return second >= self.start_s or second < self.end_s


def parse_daily_window(text: str) -> DailyUtcWindow | None:
    """'HH:MM-HH:MM' (UTC) to a window; '' means none. Raises ValueError otherwise."""
    value = text.strip()
    if not value:
        return None
    match = WINDOW_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"expected HH:MM-HH:MM in UTC, got {value[:16]!r}")
    start_h, start_m, end_h, end_m = (int(group) for group in match.groups())
    return DailyUtcWindow(start_s=start_h * SECONDS_PER_HOUR + start_m * SECONDS_PER_MINUTE,
                          end_s=end_h * SECONDS_PER_HOUR + end_m * SECONDS_PER_MINUTE)


DEFAULT_QUOTE_GAP: Final[DailyUtcWindow | None] = parse_daily_window(DEFAULT_QUOTE_GAP_UTC)


def market_closed(epoch: int, quote_gap: DailyUtcWindow | None) -> bool:
    """No quotes are expected: weekend, the daily rollover block, or the broker's gap."""
    state = session_state(epoch)
    in_gap = quote_gap is not None and quote_gap.contains(epoch)
    return state.weekend or state.rollover_block or in_gap
