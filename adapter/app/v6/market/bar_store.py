"""
Closed-bar store for V6: the ledger is the source of truth, memory is a cache.

The cache keeps at most `cache_limit` bars per timeframe as immutable tuples
inside a read-only mapping. An update builds new tuples and a new mapping and
swaps the reference, so anything already handed to a reader never changes and
readers need no lock.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from threading import Lock
from types import MappingProxyType
from typing import Final

from ..ledger_v6 import MAX_LOAD_BARS, SECONDS_PER_DAY, Coverage, LedgerV6, require_timeframe
from ..types import TIMEFRAME_SECONDS, Bar

__all__ = [
    "BarStore",
    "Coverage",
    "DEFAULT_CACHE_BARS",
    "DEFAULT_COVERAGE_LOOKBACK_S",
    "MAX_LOAD_BARS",
    "WARM_M15_DAYS",
    "WARM_M5_DAYS",
]

# Covers the 500-bar snapshot window per timeframe with room to spare.
DEFAULT_CACHE_BARS: Final[int] = 1000
# Ten M15 sessions span two weekends; three weeks still fit one with a holiday.
DEFAULT_COVERAGE_LOOKBACK_S: Final[int] = 21 * SECONDS_PER_DAY
# A UTC day counts as a session only with half a day's bars, so the Sunday
# open stub (a few bars before midnight) does not pass as a trading day.
FULL_DAY_FRACTION: Final[float] = 0.5
# Plan §4 warm-up: HOLD until 10 M15 sessions and 3 M5 days are stored.
WARM_M15_DAYS: Final[int] = 10
WARM_M5_DAYS: Final[int] = 3
_NO_BARS: Final[tuple[Bar, ...]] = ()


def _dedupe(bars: Iterable[Bar]) -> tuple[Bar, ...]:
    """One bar per open time (the last occurrence wins), oldest first."""
    by_time = {bar.t: bar for bar in bars}
    return tuple(by_time[t] for t in sorted(by_time))


def _merge(base: tuple[Bar, ...], newer: tuple[Bar, ...], limit: int) -> tuple[Bar, ...]:
    """Union by open time where `newer` wins, trimmed to the newest `limit` bars."""
    return _dedupe((*base, *newer))[-limit:]


def _min_bars_per_day(tf: str) -> int:
    return max(1, int(SECONDS_PER_DAY // TIMEFRAME_SECONDS[tf] * FULL_DAY_FRACTION))


class BarStore:
    def __init__(self, ledger: LedgerV6, cache_limit: int = DEFAULT_CACHE_BARS) -> None:
        if not 1 <= cache_limit <= MAX_LOAD_BARS:
            raise ValueError(f"cache_limit must be in [1, {MAX_LOAD_BARS}]")
        self._ledger = ledger
        self._cache_limit = cache_limit
        # Serialises writers so persist-then-swap stays ordered.
        self._lock = Lock()
        self._cache: Mapping[str, tuple[Bar, ...]] = MappingProxyType({})

    def ingest(self, tf: str, bars: Iterable[Bar]) -> int:
        """Persist closed bars and refresh the cache; returns the distinct bars written.

        The ledger is written first: if that fails the cache is left as it was,
        so memory never holds a bar the database does not.
        """
        require_timeframe(tf)
        unique = _dedupe(bars)
        if not unique:
            return 0
        with self._lock:
            self._ledger.upsert_bars(tf, unique)
            self._swap(tf, _merge(self._cached(tf), unique, self._cache_limit))
        return len(unique)

    def latest(self, tf: str, n: int) -> tuple[Bar, ...]:
        """Newest `n` bars, oldest first; reads the ledger when the cache is short."""
        require_timeframe(tf)
        if n < 1:
            raise ValueError("n must be at least 1")
        cached = self._cached(tf)
        if len(cached) >= n:
            return cached[-n:]
        return self._ledger.load_bars(tf, 0, n)

    def warm(self, timeframes: Iterable[str] = tuple(TIMEFRAME_SECONDS)) -> Mapping[str, int]:
        """Hydrate the cache from the ledger at startup; returns cached counts per timeframe."""
        wanted = tuple(require_timeframe(tf) for tf in timeframes)
        counts: dict[str, int] = {}
        with self._lock:
            for tf in wanted:
                stored = self._ledger.load_bars(tf, 0, self._cache_limit)
                merged = _merge(stored, self._cached(tf), self._cache_limit)
                self._swap(tf, merged)
                counts[tf] = len(merged)
        return MappingProxyType(counts)

    def cached_count(self, tf: str) -> int:
        return len(self._cached(tf))

    def coverage(
        self, tf: str, as_of_epoch: int, lookback_s: int = DEFAULT_COVERAGE_LOOKBACK_S
    ) -> Coverage:
        """Bars that had closed by `as_of_epoch` and opened within `lookback_s` before it."""
        require_timeframe(tf)
        if lookback_s <= 0:
            raise ValueError("lookback_s must be positive")
        return self._ledger.bar_coverage(
            tf,
            since_epoch=as_of_epoch - lookback_s,
            until_epoch=as_of_epoch - TIMEFRAME_SECONDS[tf],
            min_bars_per_day=_min_bars_per_day(tf),
        )

    def is_warm(
        self, as_of_epoch: int, min_m15_days: int = WARM_M15_DAYS, min_m5_days: int = WARM_M5_DAYS
    ) -> bool:
        if self.coverage("M15", as_of_epoch).distinct_days < min_m15_days:
            return False
        return self.coverage("M5", as_of_epoch).distinct_days >= min_m5_days

    def _cached(self, tf: str) -> tuple[Bar, ...]:
        return self._cache.get(tf, _NO_BARS)

    def _swap(self, tf: str, bars: tuple[Bar, ...]) -> None:
        self._cache = MappingProxyType({**self._cache, tf: bars})
