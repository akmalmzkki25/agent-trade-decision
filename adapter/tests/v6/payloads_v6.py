"""
Wire-format V6 request bodies for route and runtime tests.

The V6 schemas are strict, so tests build JSON-shaped dicts here and send or
validate them through `model_validate_json`, exactly as the adapter receives
them from the EA.
"""

from __future__ import annotations

import json
from typing import Any, Final

from app.v6.schemas.intent import PollRequest
from app.v6.schemas.snapshot import V6Snapshot
from app.v6.types import TIMEFRAME_SECONDS

from .fixtures_v6 import to_rows, trend_bars

# Wednesday 2026-09-16 12:00 UTC, aligned to every intraday grid.
BAR_OPEN: Final[int] = 1_789_560_000
M5: Final[int] = TIMEFRAME_SECONDS["M5"]
M15: Final[int] = TIMEFRAME_SECONDS["M15"]
H1: Final[int] = TIMEFRAME_SECONDS["H1"]
# One second after the snapshot bar closed: when the EA would post it.
RECEIVED_AT: Final[float] = float(BAR_OPEN + M15 + 1)
M15_ROWS: Final[int] = 16
M5_ROWS: Final[int] = 48
H1_ROWS: Final[int] = 8
INTENT_ID: Final[str] = "abcdefgh2345"


def closed_bar_blocks(bar_open: int = BAR_OPEN) -> dict[str, list[list[Any]]]:
    """M15/M5/H1 rows whose newest bar has closed by `bar_open + M15`."""
    close = bar_open + M15
    last_h1 = (close // H1) * H1 - H1
    blocks = {
        "M15": trend_bars(M15_ROWS, tf="M15", start_t=close - M15_ROWS * M15),
        "M5": trend_bars(M5_ROWS, tf="M5", start_t=close - M5_ROWS * M5),
        "H1": trend_bars(H1_ROWS, tf="H1", start_t=last_h1 - (H1_ROWS - 1) * H1),
    }
    return {tf: [list(row) for row in to_rows(bars)] for tf, bars in blocks.items()}


def snapshot_payload(
    snapshot_id: str = "snap-0001",
    *,
    bar_open: int = BAR_OPEN,
    bars: dict[str, list[list[Any]]] | None = None,
    trade_mode: str = "DEMO",
) -> dict[str, Any]:
    return {
        "schema_version": "v6.snapshot.1", "snapshot_id": snapshot_id, "symbol": "XAUUSD",
        "sent_at_epoch": bar_open + M15, "server_gmt_offset_s": 10_800,
        "bar_tf": "M15", "bar_open_epoch": bar_open,
        "account": {"login": "12345", "trade_mode": trade_mode, "server": "Broker-Demo",
                    "currency": "USD", "leverage": 500, "balance": 2000.0, "equity": 2010.5,
                    "margin": 0.0, "free_margin": 2010.5, "margin_level": 0.0},
        "symbol_spec": {"digits": 2, "point": 0.01, "tick_size": 0.01, "tick_value": 1.0,
                        "tick_value_loss": 1.0, "contract_size": 100.0, "volume_min": 0.01,
                        "volume_step": 0.01, "volume_max": 100.0, "stops_level": 0,
                        "freeze_level": 0, "margin_per_lot_buy": 860.0,
                        "margin_per_lot_sell": 860.0, "filling_modes": 1,
                        "expiration_modes": 15},
        "quote": {"bid": 4300.0, "ask": 4300.2, "spread_points": 20, "time_msc": 1},
        "bars": closed_bar_blocks(bar_open) if bars is None else bars,
        "ticks": {"window_s": 900, "quote_count": 900, "max_gap_ms": 800,
                  "spread_p50_points": 20.0, "spread_p95_points": 25.0, "mid_rv": 0.1},
        "positions": [], "pending_orders": [],
        "day": {"day_start_equity": 2000.0, "realized_today": 0.0, "trades_today": 0},
        "calendar": [],
        "ea_state": {"ea_version": "6.0.0", "execute_enabled": False, "halted": False,
                     "local_breaker": "none", "outbox_pending": 0, "last_intent_id": ""},
    }


def backfill_payload(tf: str = "M15", rows: list[list[Any]] | None = None) -> dict[str, Any]:
    default_rows = [list(row) for row in to_rows(trend_bars(20, tf=tf))]
    return {
        "schema_version": "v6.backfill.1", "symbol": "XAUUSD", "tf": tf,
        "sent_at_epoch": BAR_OPEN, "rows": default_rows if rows is None else rows,
    }


def poll_payload(*, trade_mode: str = "DEMO", equity: float = 2010.5) -> dict[str, Any]:
    return {
        "schema_version": "v6.poll.1", "login": "12345", "trade_mode": trade_mode,
        "server": "Broker-Demo", "sent_at_epoch": BAR_OPEN, "balance": 2000.0,
        "equity": equity, "free_margin": equity, "bid": 4300.0, "ask": 4300.2,
        "spread_points": 20, "open_v6_positions": 0, "pending_v6_orders": 0,
        "floating_pnl_v6": equity - 2000.0, "last_intent_id": "", "local_halt": False,
    }


def execution_payload(status: str = "filled", sent_at: int = BAR_OPEN) -> dict[str, Any]:
    return {
        "schema_version": "v6.execution.1", "intent_id": INTENT_ID, "status": status,
        "reason_code": "NONE", "ticket": 777, "retcode": 10009, "requested_price": 4300.0,
        "fill_price": 4300.3, "slippage_points": 30.0, "spread_points": 21,
        "latency_ms": 85, "sent_at_epoch": sent_at,
    }


def as_snapshot(payload: dict[str, Any]) -> V6Snapshot:
    return V6Snapshot.model_validate_json(json.dumps(payload))


def as_poll(payload: dict[str, Any]) -> PollRequest:
    return PollRequest.model_validate_json(json.dumps(payload))
