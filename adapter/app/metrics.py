"""
Performance metrics for scalper evaluation.

Implements the metric set the strategy brief calls mandatory:

  Profit & Loss
    - profit_factor   : gross_profit / |gross_loss|          (target > 1.3-1.5)
                        Undefined when there are no losses at all; the payload
                        carries `profit_factor_is_undefined` so a flawless run
                        is never confused with "no data".
    - win_rate        : wins / (wins + losses)
                        Breakeven baskets (net_pnl == 0) are excluded from the
                        denominator: they are neither a win nor a loss, and
                        counting them would drag the rate toward zero.
    - avg_win/avg_loss: the R:R side of the equation
    - expected_value  : (win_rate * avg_win) - (loss_rate * |avg_loss|)
                        i.e. expected PnL per *decided* basket.

  Execution
    - avg / p95 decision latency  (brief targets p95 < 20ms for the decision leg)
    - avg slippage points
    - avg spread points

  Risk
    - max_floating_drawdown : worst floating PnL seen inside any basket
    - worst/best_basket_pnl : realised extremes
    - equity_curve          : running net PnL per basket, for charting

  Statistical maturity
    - sample_size + is_significant (the brief requires 1000+ trades before a
      scalper's edge should be trusted)

All queries are read-only and bounded. A version filter ("v5", "v3", ...) lets
the dashboard show per-strategy performance.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from typing import Any, Final

from .db import clamp_limit, read_only_connection, table_exists

logger = logging.getLogger(__name__)

MIN_SIGNIFICANT_SAMPLE: Final[int] = 1000
PROFIT_FACTOR_TARGET: Final[float] = 1.3
LATENCY_TARGET_MS: Final[int] = 20

MAX_METRIC_ROWS: Final[int] = 20000
MAX_CURVE_ROWS: Final[int] = 5000
MAX_RESULT_ROWS: Final[int] = 200

_METRIC_COLUMNS: Final[str] = (
    "net_pnl, gross_profit, gross_loss, max_floating_dd, "
    "decision_latency_ms, avg_slippage_points, avg_spread_points"
)


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank percentile (ceil convention). `sorted_values` ascending."""
    if not sorted_values:
        return 0.0
    rank = math.ceil(pct * len(sorted_values))
    rank = max(1, min(rank, len(sorted_values)))
    return sorted_values[rank - 1]


def _empty_metrics(version: str | None) -> dict[str, Any]:
    return {
        "version": version or "all",
        "sample_size": 0,
        "is_significant": False,
        "min_significant_sample": MIN_SIGNIFICANT_SAMPLE,
        "wins": 0,
        "losses": 0,
        "breakeven": 0,
        "win_rate_pct": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "net_pnl": 0.0,
        "profit_factor": 0.0,
        "profit_factor_is_undefined": False,
        "profit_factor_target": PROFIT_FACTOR_TARGET,
        "meets_profit_factor_target": False,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "expected_value": 0.0,
        "max_floating_drawdown": 0.0,
        "worst_basket_pnl": 0.0,
        "best_basket_pnl": 0.0,
        "avg_latency_ms": 0.0,
        "p95_latency_ms": 0.0,
        "latency_target_ms": LATENCY_TARGET_MS,
        "avg_slippage_points": 0.0,
        "avg_spread_points": 0.0,
        "ledger_available": True,
    }


def _select_baskets(columns: str, version: str | None, limit: int) -> list[sqlite3.Row] | None:
    """
    Fetch basket rows, newest first. Returns None when the ledger is unreadable
    so callers can distinguish "DB problem" from "no rows yet".

    `columns` is a module-level literal, never caller input; only the version
    filter and limit are bound as parameters.
    """
    # Scalper baskets can open and close inside the same second, so created_at
    # alone is not a stable sort key. rowid breaks ties in true insertion order,
    # which keeps the equity curve deterministic.
    order = "ORDER BY created_at DESC, rowid DESC"
    try:
        with read_only_connection() as connection:
            if not table_exists(connection, "basket_results"):
                return []
            if version:
                return connection.execute(
                    f"SELECT {columns} FROM basket_results WHERE version = ? {order} LIMIT ?",
                    (version, limit),
                ).fetchall()
            return connection.execute(
                f"SELECT {columns} FROM basket_results {order} LIMIT ?",
                (limit,),
            ).fetchall()
    except sqlite3.OperationalError:
        logger.warning("basket_results unreadable (version=%s)", version, exc_info=True)
        return None


def _aggregate(rows: list[sqlite3.Row], version: str | None) -> dict[str, Any]:
    """Turn raw basket rows into the brief's metric set."""
    out = _empty_metrics(version)
    if not rows:
        return out

    wins: list[float] = []
    losses: list[float] = []
    net_pnls: list[float] = []
    latencies: list[float] = []
    slippages: list[float] = []
    spreads: list[float] = []
    gross_profit = 0.0
    gross_loss = 0.0
    max_floating_dd = 0.0
    breakeven = 0

    for row in rows:
        net = float(row["net_pnl"] or 0.0)
        net_pnls.append(net)
        if net > 0:
            wins.append(net)
        elif net < 0:
            losses.append(net)
        else:
            breakeven += 1

        gross_profit += float(row["gross_profit"] or 0.0)
        gross_loss += float(row["gross_loss"] or 0.0)
        max_floating_dd = min(max_floating_dd, float(row["max_floating_dd"] or 0.0))

        latency = float(row["decision_latency_ms"] or 0.0)
        if latency > 0:
            latencies.append(latency)
        slippages.append(abs(float(row["avg_slippage_points"] or 0.0)))
        spreads.append(float(row["avg_spread_points"] or 0.0))

    win_count = len(wins)
    loss_count = len(losses)
    decided = win_count + loss_count

    avg_win = (sum(wins) / win_count) if win_count else 0.0
    avg_loss = (sum(losses) / loss_count) if loss_count else 0.0
    win_rate = (win_count / decided) if decided else 0.0
    loss_rate = (1.0 - win_rate) if decided else 0.0

    # A run with profit and zero losses has an undefined (infinite) profit
    # factor — the best possible outcome. Reporting 0.0 there would be
    # indistinguishable from "no data", so flag it explicitly instead.
    abs_gross_loss = abs(gross_loss)
    if abs_gross_loss > 0:
        profit_factor = gross_profit / abs_gross_loss
        profit_factor_is_undefined = False
        meets_target = profit_factor >= PROFIT_FACTOR_TARGET
    elif gross_profit > 0:
        profit_factor = 0.0
        profit_factor_is_undefined = True
        meets_target = True
    else:
        profit_factor = 0.0
        profit_factor_is_undefined = False
        meets_target = False

    latencies.sort()

    out.update(
        {
            "sample_size": len(rows),
            "is_significant": len(rows) >= MIN_SIGNIFICANT_SAMPLE,
            "wins": win_count,
            "losses": loss_count,
            "breakeven": breakeven,
            "win_rate_pct": round(win_rate * 100.0, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "net_pnl": round(sum(net_pnls), 2),
            "profit_factor": round(profit_factor, 3),
            "profit_factor_is_undefined": profit_factor_is_undefined,
            "meets_profit_factor_target": meets_target,
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "expected_value": round((win_rate * avg_win) - (loss_rate * abs(avg_loss)), 4),
            "max_floating_drawdown": round(max_floating_dd, 2),
            "worst_basket_pnl": round(min(net_pnls), 2),
            "best_basket_pnl": round(max(net_pnls), 2),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
            "p95_latency_ms": round(_percentile(latencies, 0.95), 2),
            "avg_slippage_points": round(sum(slippages) / len(slippages), 2) if slippages else 0.0,
            "avg_spread_points": round(sum(spreads) / len(spreads), 2) if spreads else 0.0,
        }
    )
    return out


def get_performance_metrics(version: str | None = None, limit: int = 5000) -> dict[str, Any]:
    """
    Aggregate basket results into the brief's metric set.

    Args:
        version: filter to one strategy ("v5") or None for all.
        limit: max baskets to scan, newest first.
    """
    rows = _select_baskets(_METRIC_COLUMNS, version, clamp_limit(limit, MAX_METRIC_ROWS))
    if rows is None:
        out = _empty_metrics(version)
        out["ledger_available"] = False
        return out
    return _aggregate(rows, version)


def get_equity_curve(version: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    """Running cumulative net PnL, oldest first, for the dashboard chart."""
    rows = _select_baskets(
        "basket_id, closed_at_utc, net_pnl, close_reason",
        version,
        clamp_limit(limit, MAX_CURVE_ROWS),
    )
    if not rows:
        return []

    curve: list[dict[str, Any]] = []
    running = 0.0
    for row in reversed(rows):   # stored newest-first; chart reads oldest-first
        net = float(row["net_pnl"] or 0.0)
        running += net
        curve.append(
            {
                "basket_id": row["basket_id"],
                "closed_at_utc": row["closed_at_utc"],
                "net_pnl": round(net, 2),
                "cumulative_pnl": round(running, 2),
                "close_reason": row["close_reason"],
            }
        )
    return curve


def recent_basket_results(version: str | None = None, limit: int = 25) -> list[dict[str, Any]]:
    """Most recent closed baskets, newest first."""
    rows = _select_baskets("*", version, clamp_limit(limit, MAX_RESULT_ROWS))
    return [dict(row) for row in rows] if rows else []
