"""
Shared read-only SQLite access for analytics modules.

Analytics (dashboard, metrics) must never block or corrupt the Ledger's write
path, so they open their own connection with `mode=ro`. Keeping this in one
place stops each analytics module from re-implementing the URI dance.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .settings import settings


@contextmanager
def read_only_connection() -> Iterator[sqlite3.Connection]:
    """Open a read-only connection to the ledger; always closed on exit."""
    db_path = Path(settings.db_path).resolve()
    uri = f"file:{db_path.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    """True when `name` exists as a table. Safe on a fresh/empty ledger."""
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def clamp_limit(limit: int, maximum: int, minimum: int = 1) -> int:
    """Bound a caller-supplied row limit so queries stay predictable."""
    return max(minimum, min(limit, maximum))
