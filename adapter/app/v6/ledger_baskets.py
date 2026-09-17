"""
V6 basket results in `basket_results` (wire contract section 6.5).

The V6 EA posts one result per closed position to the signed /v6/basket-result
route. Rows go to the V6 database file, the one `runtime.realised_pnl` and the
dashboard read (in production the same file the V1-V5 `Ledger` uses), with the
same columns, so `metrics(version="v6")` works as well. Unlike the V1-V5 writer,
a resent result is stored once: the first receipt time is kept. A row with the same
basket id that is not a V6 row (it can only have come from the unsigned V1-V5 route) is
replaced by the signed result.

Each write opens a short-lived connection (results are rare); the table and its
index are created when missing, with the V1-V5 DDL.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from os import PathLike
from threading import Lock
from typing import Final

from ..models import BasketResultEvent
from .clock import Clock
from .ledger_v6 import BUSY_TIMEOUT_MS

logger = logging.getLogger(__name__)

V6_VERSION: Final[str] = "v6"
MS_PER_SECOND: Final[int] = 1000
# Identical to app/ledger.py, so either writer can create the table first.
BASKET_DDL: Final[tuple[str, ...]] = (
    """CREATE TABLE IF NOT EXISTS basket_results (
        basket_id TEXT PRIMARY KEY, version TEXT, symbol TEXT, side TEXT,
        opened_at_utc TEXT, closed_at_utc TEXT, close_reason TEXT, bursts INTEGER,
        positions INTEGER, gross_profit REAL, gross_loss REAL, net_pnl REAL,
        max_floating_dd REAL, avg_slippage_points REAL, avg_spread_points REAL,
        decision_latency_ms INTEGER, equity_at_open REAL, equity_at_close REAL,
        created_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_basket_results_version"
    " ON basket_results(version, created_at DESC)",
)
COLUMNS: Final[tuple[str, ...]] = (
    "basket_id", "version", "symbol", "side", "opened_at_utc", "closed_at_utc",
    "close_reason", "bursts", "positions", "gross_profit", "gross_loss", "net_pnl",
    "max_floating_dd", "avg_slippage_points", "avg_spread_points", "decision_latency_ms",
    "equity_at_open", "equity_at_close")
# Fixed text built from the literals above; every value is bound. A stored V6 row is kept.
_INSERT_SQL: Final[str] = (
    f"INSERT INTO basket_results ({', '.join(COLUMNS)}, created_at)"
    f" VALUES ({', '.join('?' * (len(COLUMNS) + 1))}) ON CONFLICT(basket_id) DO UPDATE SET "
    + ", ".join(f"{name} = excluded.{name}" for name in (*COLUMNS[1:], "created_at"))
    + f" WHERE basket_results.version IS NOT '{V6_VERSION}'")


def received_at_utc(epoch: float) -> str:
    """The `created_at` text of a result received at `epoch` (the V1-V5 format)."""
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


class BasketJournal:
    """Blocking; async callers wrap `record` in `asyncio.to_thread`."""

    def __init__(self, db_path: str | PathLike[str], clock: Clock) -> None:
        self.path = str(db_path)
        self._clock = clock
        self._lock = Lock()
        self._schema_ready = False

    def record(self, event: BasketResultEvent) -> bool:
        """Store a V6 result once; False when a V6 result with its basket id is stored.

        Raises ValueError for another strategy's result, sqlite3.Error on storage failure.
        """
        if event.version != V6_VERSION:
            raise ValueError(f"not a V6 basket result (version {event.version})")
        values = (*(getattr(event, name) for name in COLUMNS),
                  received_at_utc(self._clock.now_epoch()))
        with self._lock:
            conn = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / MS_PER_SECOND)
            try:
                return self._insert(conn, values)
            finally:
                conn.close()

    def _insert(self, conn: sqlite3.Connection, values: tuple[object, ...]) -> bool:
        with conn:
            if not self._schema_ready:
                for statement in BASKET_DDL:
                    conn.execute(statement)
            inserted = conn.execute(_INSERT_SQL, values).rowcount == 1
        self._schema_ready = True
        return inserted
