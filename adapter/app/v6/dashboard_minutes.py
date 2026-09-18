"""
The minute packets on the dashboard (spec sections 4.6 and 9): how many m1 packets the
agent answered before their deadline in the last two hours and the last day, the entries
and management actions they produced, the adapter's p95 time per minute, and the newest
minute rows. Blocking reads: call `read_minutes` in a worker thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .ledger_cycles import LedgerCycles
from .ledger_minutes import MinuteRow, MinuteStats

WINDOW_S: Final[int] = 2 * 3_600          # the spec's "two busy hours"
DAY_S: Final[int] = 86_400
RECENT_ROWS: Final[int] = 12


@dataclass(frozen=True)
class MinuteTables:
    window: MinuteStats
    day: MinuteStats
    recent: tuple[MinuteRow, ...]


def read_minutes(ledger: LedgerCycles, now: float) -> MinuteTables:
    minutes = ledger.minutes
    return MinuteTables(window=minutes.stats(int(now) - WINDOW_S),
                        day=minutes.stats(int(now) - DAY_S),
                        recent=minutes.recent(RECENT_ROWS))


def minute_overview(tables: MinuteTables) -> dict[str, object]:
    return {"minutes": {"window_hours": WINDOW_S // 3_600, "window": tables.window.to_dict(),
                        "day": tables.day.to_dict(),
                        "recent": [row.to_dict() for row in tables.recent]}}
