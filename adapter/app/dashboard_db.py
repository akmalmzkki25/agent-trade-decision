"""
Read-only query helpers for the dashboard.

Opens its own SQLite connection (read-only URI) so it never blocks writes from
the Ledger. All queries are bounded and parameterized.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .settings import settings


@contextmanager
def _conn():
    db_path = Path(settings.db_path).resolve()
    # Use URI with mode=ro to enforce read-only access.
    uri = f"file:{db_path.as_posix()}?mode=ro"
    cn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    cn.row_factory = sqlite3.Row
    try:
        yield cn
    finally:
        cn.close()


def _table_exists(cn: sqlite3.Connection, name: str) -> bool:
    row = cn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def get_stats() -> dict[str, Any]:
    """Aggregate metrics for the top stat cards."""
    out = {
        "decisions_total": 0,
        "decisions_open": 0,
        "decisions_hold": 0,
        "decisions_degraded": 0,
        "hold_rate_pct": 0.0,
        "avg_latency_ms": 0,
        "plans_total": 0,
        "plans_with_layers": 0,
        "plans_veto": 0,
        "trade_events_total": 0,
        "ledger_available": True,
    }
    try:
        with _conn() as cn:
            if _table_exists(cn, "decisions"):
                row = cn.execute(
                    """
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN action='open' THEN 1 ELSE 0 END) AS opens,
                        SUM(CASE WHEN action='hold' THEN 1 ELSE 0 END) AS holds,
                        SUM(CASE WHEN status='degraded' THEN 1 ELSE 0 END) AS degraded,
                        AVG(latency_ms) AS avg_latency
                    FROM decisions
                    """
                ).fetchone()
                if row:
                    out["decisions_total"] = int(row["total"] or 0)
                    out["decisions_open"] = int(row["opens"] or 0)
                    out["decisions_hold"] = int(row["holds"] or 0)
                    out["decisions_degraded"] = int(row["degraded"] or 0)
                    if out["decisions_total"] > 0:
                        out["hold_rate_pct"] = round(
                            100.0 * out["decisions_hold"] / out["decisions_total"], 1
                        )
                    out["avg_latency_ms"] = int(row["avg_latency"] or 0)
            if _table_exists(cn, "plans"):
                row = cn.execute(
                    """
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN layers_count > 0 THEN 1 ELSE 0 END) AS with_layers,
                        SUM(CASE WHEN status='veto' THEN 1 ELSE 0 END) AS veto
                    FROM plans
                    """
                ).fetchone()
                if row:
                    out["plans_total"] = int(row["total"] or 0)
                    out["plans_with_layers"] = int(row["with_layers"] or 0)
                    out["plans_veto"] = int(row["veto"] or 0)
            if _table_exists(cn, "trade_events"):
                row = cn.execute("SELECT COUNT(*) AS c FROM trade_events").fetchone()
                if row:
                    out["trade_events_total"] = int(row["c"] or 0)
    except sqlite3.OperationalError:
        out["ledger_available"] = False
    return out


def recent_decisions(limit: int = 25) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with _conn() as cn:
            if not _table_exists(cn, "decisions"):
                return rows
            for r in cn.execute(
                """
                SELECT request_id, ts_utc, symbol, timeframe, status, action, side,
                       lots, latency_ms, response_json
                FROM decisions
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall():
                resp = {}
                try:
                    resp = json.loads(r["response_json"] or "{}")
                except json.JSONDecodeError:
                    pass
                dec = resp.get("decision", {})
                rows.append(
                    {
                        "request_id": r["request_id"],
                        "ts_utc": r["ts_utc"],
                        "symbol": r["symbol"],
                        "timeframe": r["timeframe"],
                        "status": r["status"],
                        "action": r["action"],
                        "side": r["side"],
                        "lots": r["lots"],
                        "latency_ms": r["latency_ms"],
                        "confidence": dec.get("confidence", 0.0),
                        "reason_codes": dec.get("reason_codes", []),
                        "rationale": dec.get("rationale_short", ""),
                    }
                )
    except sqlite3.OperationalError:
        pass
    return rows


def recent_plans(limit: int = 25) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with _conn() as cn:
            if not _table_exists(cn, "plans"):
                return rows
            for r in cn.execute(
                """
                SELECT request_id, symbol, scenario, side, confidence, basket_tp_pct,
                       invalidation_price, layers_count, status, created_at
                FROM plans
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall():
                rows.append(dict(r))
    except sqlite3.OperationalError:
        pass
    return rows


def recent_trade_events(limit: int = 25) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with _conn() as cn:
            if not _table_exists(cn, "trade_events"):
                return rows
            for r in cn.execute(
                """
                SELECT id, request_id, symbol, trans_type, order_ticket, deal_ticket,
                       position_ticket, retcode, created_at
                FROM trade_events
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall():
                rows.append(dict(r))
    except sqlite3.OperationalError:
        pass
    return rows


def action_distribution() -> list[dict[str, Any]]:
    """For the bar chart on the dashboard."""
    rows: list[dict[str, Any]] = []
    try:
        with _conn() as cn:
            if not _table_exists(cn, "decisions"):
                return rows
            for r in cn.execute(
                """
                SELECT action, COUNT(*) AS c
                FROM decisions
                GROUP BY action
                ORDER BY c DESC
                """
            ).fetchall():
                rows.append({"action": r["action"] or "unknown", "count": int(r["c"])})
    except sqlite3.OperationalError:
        pass
    return rows


def decisions_timeseries(limit_minutes: int = 360) -> list[dict[str, Any]]:
    """Last N minutes of decisions for the timeline chart."""
    rows: list[dict[str, Any]] = []
    try:
        with _conn() as cn:
            if not _table_exists(cn, "decisions"):
                return rows
            for r in cn.execute(
                """
                SELECT ts_utc, action, side, lots
                FROM decisions
                ORDER BY created_at DESC
                LIMIT 500
                """
            ).fetchall():
                rows.append(dict(r))
    except sqlite3.OperationalError:
        pass
    rows.reverse()
    return rows
