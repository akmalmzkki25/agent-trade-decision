"""
Replay inputs: closed bars per timeframe and the calendar events the EA once sent.

Sources:
  * the adapter ledger (`v6_bars`, `v6_snapshots`), always opened read-only
    through an SQLite `mode=ro` URI;
  * CSV files written by ea/Scripts/QlipV6_ExportBars.mq5, one per timeframe,
    named `<SYMBOL>_<TF>.csv` with the header `t,o,h,l,c,tick_volume,spread`
    (`t` = bar open, UTC epoch seconds).

Every row passes the wire validator (`schemas.snapshot.validate_bar_rows`); a row
that fails is dropped and counted, never repaired.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from bisect import bisect_right
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Final

from app.v6.schemas.snapshot import BarRow, CalendarEventBlock, validate_bar_rows
from app.v6.types import TIMEFRAME_SECONDS, Bar

TIMEFRAMES: Final[tuple[str, ...]] = ("M1", "M5", "M15", "H1", "D1")
CSV_COLUMNS: Final[tuple[str, ...]] = ("t", "o", "h", "l", "c", "tick_volume", "spread")
SOURCE_SQLITE: Final[str] = "sqlite"
SOURCE_CSV: Final[str] = "csv"
_BARS_SQL: Final[str] = "SELECT t, o, h, l, c, tv, spr FROM v6_bars WHERE tf = ? ORDER BY t"
_TABLE_SQL: Final[str] = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?"
_SNAPSHOT_SQL: Final[str] = "SELECT payload_json FROM v6_snapshots ORDER BY bar_open_epoch"


@dataclass(frozen=True)
class BarSet:
    """Closed bars per timeframe, oldest first, with a bisect index on open times."""

    series: Mapping[str, tuple[Bar, ...]]
    source: str = SOURCE_SQLITE
    dropped: Mapping[str, int] = field(default_factory=dict)
    _times: Mapping[str, tuple[int, ...]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        series = {tf: tuple(bars) for tf, bars in self.series.items()}
        object.__setattr__(self, "series", MappingProxyType(series))
        object.__setattr__(self, "dropped", MappingProxyType(dict(self.dropped)))
        times = {tf: tuple(bar.t for bar in bars) for tf, bars in series.items()}
        object.__setattr__(self, "_times", MappingProxyType(times))

    def bars(self, tf: str) -> tuple[Bar, ...]:
        return self.series.get(tf, ())

    def closed_by(self, tf: str, as_of: int) -> tuple[Bar, ...]:
        """Bars whose close (open + timeframe) is at or before `as_of`."""
        times = self._times.get(tf, ())
        end = bisect_right(times, as_of - TIMEFRAME_SECONDS[tf])
        return self.bars(tf)[:end]

    def truncated(self, as_of: int) -> "BarSet":
        """Only the bars closed by `as_of` (what a replay of that moment may read)."""
        cut = {tf: self.closed_by(tf, as_of) for tf in self.series}
        return BarSet(series=cut, source=self.source, dropped=self.dropped)

    def coverage(self) -> dict[str, dict[str, int]]:
        return {tf: {"bars": len(bars), "first_t": bars[0].t, "last_t": bars[-1].t}
                for tf, bars in self.series.items() if bars}


def clean_rows(rows: Iterable[BarRow], tf: str) -> tuple[tuple[Bar, ...], int]:
    """Valid rows as bars (one per open time, the last wins, sorted); the dropped count."""
    by_time: dict[int, BarRow] = {}
    dropped = 0
    for row in rows:
        try:
            validate_bar_rows([row], tf)
        except (ValueError, TypeError):
            dropped += 1
            continue
        by_time[row[0]] = row
    bars = tuple(Bar(t=t, o=o, h=h, l=lo, c=c, tv=tv, spr=spr)
                 for t, o, h, lo, c, tv, spr in (by_time[k] for k in sorted(by_time)))
    return bars, dropped


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise FileNotFoundError(f"ledger not found: {db_path}")
    return sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(_TABLE_SQL, (name,)).fetchone() is not None


def load_sqlite(db_path: Path) -> BarSet:
    """Every stored bar of `v6_bars`, read-only."""
    series: dict[str, tuple[Bar, ...]] = {}
    dropped: dict[str, int] = {}
    con = _connect_ro(db_path)
    try:
        if not _has_table(con, "v6_bars"):
            raise ValueError(f"{db_path.name} has no v6_bars table")
        for tf in TIMEFRAMES:
            rows = [(int(t), float(o), float(h), float(lo), float(c), int(tv), int(spr))
                    for t, o, h, lo, c, tv, spr in con.execute(_BARS_SQL, (tf,))]
            series[tf], dropped[tf] = clean_rows(rows, tf)
    finally:
        con.close()
    return BarSet(series=series, source=SOURCE_SQLITE, dropped=dropped)


def _parse_csv_row(record: Mapping[str, str]) -> BarRow:
    return (int(record["t"]), float(record["o"]), float(record["h"]), float(record["l"]),
            float(record["c"]), int(record["tick_volume"]), int(record["spread"]))


def read_csv_bars(path: Path, tf: str) -> tuple[tuple[Bar, ...], int]:
    """One exported timeframe file; unparsable or invalid rows are dropped and counted."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = [name for name in CSV_COLUMNS if name not in (reader.fieldnames or ())]
        if missing:
            raise ValueError(f"{path.name}: missing columns {','.join(missing)}")
        rows: list[BarRow] = []
        unparsable = 0
        for record in reader:
            try:
                rows.append(_parse_csv_row(record))
            except (TypeError, ValueError):
                unparsable += 1
    bars, invalid = clean_rows(rows, tf)
    return bars, unparsable + invalid


def load_csv_dir(directory: Path, symbol: str) -> BarSet:
    """`<symbol>_<TF>.csv` for every timeframe present in `directory`."""
    if not directory.is_dir():
        raise FileNotFoundError(f"CSV directory not found: {directory}")
    series: dict[str, tuple[Bar, ...]] = {}
    dropped: dict[str, int] = {}
    for tf in TIMEFRAMES:
        path = directory / f"{symbol}_{tf}.csv"
        if path.is_file():
            series[tf], dropped[tf] = read_csv_bars(path, tf)
    if not series:
        raise ValueError(f"no {symbol}_<TF>.csv files in {directory}")
    return BarSet(series=series, source=SOURCE_CSV, dropped=dropped)


def _event_blocks(payload_json: str) -> list[CalendarEventBlock]:
    try:
        items = json.loads(payload_json).get("calendar", [])
    except (ValueError, AttributeError):
        return []
    blocks = []
    for item in items if isinstance(items, list) else []:
        try:
            blocks.append(CalendarEventBlock.model_validate_json(json.dumps(item)))
        except ValueError:
            continue
    return blocks


def load_stored_events(db_path: Path) -> tuple[CalendarEventBlock, ...]:
    """Distinct calendar events found in stored snapshot payloads, by time."""
    con = _connect_ro(db_path)
    try:
        if not _has_table(con, "v6_snapshots"):
            return ()
        payloads = [row[0] for row in con.execute(_SNAPSHOT_SQL)]
    finally:
        con.close()
    unique = {(block.event_id, block.time_epoch): block
              for payload in payloads for block in _event_blocks(payload)}
    return tuple(unique[key] for key in sorted(unique, key=lambda k: (k[1], k[0])))
