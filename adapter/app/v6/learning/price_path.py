"""
The price path the triple-barrier labeler walks, and the check that it is complete.

Path: M1 bars in complete 5-minute buckets, else the bucket's M5 bar (coarser,
still stop-first): snapshots carry 12 of 15 M1 bars, so live M1 has holes.

A 5-minute bucket with neither a complete M1 bucket nor an M5 bar is a data hole
unless no quotes were expected then (weekend, rollover block, the broker's daily
quote gap). Snapshots carry only 4 h of M5 and the EA backfills only when it
starts, so an adapter outage or a sleeping PC leaves such holes; a label read
across one is wrong, so the labeler must not write it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from ..market.broker_hours import DailyUtcWindow, market_closed
from ..types import TIMEFRAME_SECONDS, Bar

M1_S: Final[int] = TIMEFRAME_SECONDS["M1"]
M5_S: Final[int] = TIMEFRAME_SECONDS["M5"]
MINUTES_PER_BUCKET: Final[int] = M5_S // M1_S


class SpreadSource(Protocol):
    """What a path needs from the labeler configuration."""

    @property
    def point(self) -> float: ...

    @property
    def fallback_spread_points(self) -> int: ...


@dataclass(frozen=True)
class PathBar:
    """One step of the simulated price path; `spread` is in price units."""

    t: int
    span_s: int
    o: float
    h: float
    l: float  # noqa: E741 - conventional OHLC name
    c: float
    spread: float
    coarse: bool = False

    @property
    def end(self) -> int:
        return self.t + self.span_s


def _path_bar(bar: Bar, span_s: int, config: SpreadSource, coarse: bool) -> PathBar:
    points = bar.spr if bar.spr > 0 else config.fallback_spread_points
    return PathBar(t=bar.t, span_s=span_s, o=bar.o, h=bar.h, l=bar.l, c=bar.c,
                   spread=points * config.point, coarse=coarse)


def _minutes_by_bucket(m1: Sequence[Bar], start: int, end: int) -> dict[int, dict[int, Bar]]:
    minutes: dict[int, dict[int, Bar]] = {}
    for bar in m1:
        if start <= bar.t and bar.t + M1_S <= end:
            minutes.setdefault(bar.t - bar.t % M5_S, {})[bar.t] = bar
    return minutes


def build_price_path(m1: Sequence[Bar], m5: Sequence[Bar], start: int, end: int,
                     config: SpreadSource) -> tuple[PathBar, ...]:
    """Path over [start, end): M1 bars in complete 5-minute buckets, else the M5 bar."""
    minutes = _minutes_by_bucket(m1, start, end)
    fives = {bar.t: bar for bar in m5 if start <= bar.t and bar.t + M5_S <= end}
    path: list[PathBar] = []
    for bucket in sorted(set(minutes) | set(fives)):
        rows = minutes.get(bucket, {})
        if len(rows) == MINUTES_PER_BUCKET or bucket not in fives:
            path.extend(_path_bar(rows[t], M1_S, config, False) for t in sorted(rows))
        else:
            path.append(_path_bar(fives[bucket], M5_S, config, True))
    return tuple(path)


def uncovered_buckets(m1: Sequence[Bar], m5: Sequence[Bar], start: int, until: int, *,
                      limit: int, quote_gap: DailyUtcWindow | None) -> tuple[int, ...]:
    """Open-market buckets starting in [start, until) that the path has no full data for.

    Bars count when they lie inside [start, limit), the window the path is built on.
    """
    counts = Counter(bar.t - bar.t % M5_S for bar in m1
                     if start <= bar.t and bar.t + M1_S <= limit)
    fives = {bar.t for bar in m5 if start <= bar.t and bar.t + M5_S <= limit}
    first = start + (-start) % M5_S
    return tuple(
        bucket for bucket in range(first, until, M5_S)
        if bucket not in fives and counts[bucket] < MINUTES_PER_BUCKET
        and not market_closed(bucket, quote_gap)
    )
