"""
Synthetic history for the replay tests (scripts/v6replay).

One seeded M5 random walk from Monday 2026-09-14, aggregated into M15, H1 and D1
so every timeframe agrees. Eleven days of it give the replay enough warm-up
(ten full M15 sessions) to evaluate Thursday 2026-09-24, a day with no static
calendar event. The M5 range is wide enough for FRICTION_ATR to pass.
"""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

from app.v6.cycle_types import MarketContext, candidate_id_for
from app.v6.types import Bar, Candidate

from .fixtures_v6 import DEFAULT_START_EPOCH, random_walk_bars

SCRIPTS_DIR: Final[Path] = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from v6replay.data import BarSet  # noqa: E402

DAY: Final[int] = 86_400
M5: Final[int] = 300
HISTORY_DAYS: Final[int] = 11
EVAL_DAY_START: Final[int] = DEFAULT_START_EPOCH + 10 * DAY      # Thu 2026-09-24 00:00 UTC
EVAL_DAY: Final[str] = "2026-09-24"
SEED: Final[int] = 6
SIGMA: Final[float] = 5.0
WICK: Final[float] = 2.0
STOP_DISTANCE: Final[float] = 10.0
SPREAD_POINTS: Final[int] = 30
CSV_HEADER: Final[str] = "t,o,h,l,c,tick_volume,spread"


def aggregate(bars: Sequence[Bar], step: int) -> tuple[Bar, ...]:
    """Coarser bars from finer ones (bucketed by open time)."""
    groups: dict[int, list[Bar]] = {}
    for bar in bars:
        groups.setdefault(bar.t - bar.t % step, []).append(bar)
    return tuple(
        Bar(t=t, o=group[0].o, h=max(b.h for b in group), l=min(b.l for b in group),
            c=group[-1].c, tv=sum(b.tv for b in group), spr=min(b.spr for b in group))
        for t, group in sorted(groups.items()))


def history_series(days: int = HISTORY_DAYS) -> dict[str, tuple[Bar, ...]]:
    raw = random_walk_bars(days * DAY // M5, seed=SEED, sigma=SIGMA, wick=WICK, tf="M5")
    m5 = tuple(Bar(t=b.t, o=b.o, h=b.h, l=b.l, c=b.c, tv=b.tv, spr=SPREAD_POINTS)
               for b in raw)
    return {"M5": m5, "M15": aggregate(m5, 900), "H1": aggregate(m5, 3600),
            "D1": aggregate(m5, DAY)}


def history(days: int = HISTORY_DAYS) -> BarSet:
    return BarSet(series=history_series(days))


def buy_detector(context: MarketContext) -> tuple[Candidate, ...]:
    """One sized buy on every bar: entry at the close, a $10 structural stop."""
    close = context.bars["M15"][-1].c
    bar_t = context.bar_open_epoch
    return (Candidate(
        candidate_id=candidate_id_for("displacement", "buy", bar_t), setup="displacement",
        side="buy", entry=close, invalidation=round(close - STOP_DISTANCE, 2), bar_t=bar_t,
        features={"body_ratio": 0.8}, reason_codes=("CONFIRMED_CLOSE",)),)


def no_detector(_context: MarketContext) -> tuple[Candidate, ...]:
    return ()


Detector = Callable[[MarketContext], tuple[Candidate, ...]]


def write_csv(directory: Path, series: dict[str, tuple[Bar, ...]],
              symbol: str = "XAUUSD", extra: Sequence[str] = ()) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for tf, bars in series.items():
        lines = [CSV_HEADER] + [f"{b.t},{b.o},{b.h},{b.l},{b.c},{b.tv},{b.spr}" for b in bars]
        (directory / f"{symbol}_{tf}.csv").write_text("\n".join([*lines, *extra]) + "\n",
                                                       encoding="utf-8")


def write_ledger(path: Path, series: dict[str, tuple[Bar, ...]],
                 payloads: Sequence[str] = ()) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE v6_bars (tf TEXT NOT NULL, t INTEGER NOT NULL, o REAL, "
                    "h REAL, l REAL, c REAL, tv INTEGER, spr INTEGER, PRIMARY KEY (tf, t))")
        con.execute("CREATE TABLE v6_snapshots (snapshot_id TEXT PRIMARY KEY, "
                    "bar_open_epoch INTEGER, payload_json TEXT)")
        con.executemany("INSERT INTO v6_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        [(tf, b.t, b.o, b.h, b.l, b.c, b.tv, b.spr)
                         for tf, bars in series.items() for b in bars])
        con.executemany("INSERT INTO v6_snapshots VALUES (?, ?, ?)",
                        [(f"snap-{i}", i, payload) for i, payload in enumerate(payloads)])
        con.commit()
    finally:
        con.close()
