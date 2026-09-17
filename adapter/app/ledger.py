from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from threading import Lock

logger = logging.getLogger(__name__)

from .models import (
    BasketResultEvent,
    DecisionRequest,
    DecisionResponse,
    LayerPlanRequest,
    LayerPlanResponse,
    TradeTransactionEvent,
    V3PlanRequest,
    V3PlanResponse,
    V4PlanRequest,
    V4PlanResponse,
    V5BurstRequest,
    V5BurstResponse,
)

_BASKET_VALUE_COLUMNS = (
    "version", "symbol", "side", "opened_at_utc", "closed_at_utc", "close_reason", "bursts",
    "positions", "gross_profit", "gross_loss", "net_pnl", "max_floating_dd",
    "avg_slippage_points", "avg_spread_points", "decision_latency_ms", "equity_at_open",
    "equity_at_close", "created_at",
)
# Fixed text built from the literals above; a stored V6 row is never overwritten.
_BASKET_UPSERT = (
    " ON CONFLICT(basket_id) DO UPDATE SET "
    + ", ".join(f"{name} = excluded.{name}" for name in _BASKET_VALUE_COLUMNS)
    + " WHERE basket_results.version IS NOT 'v6'"
)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class Ledger:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables and run repeatable column migrations."""
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS decisions (
                request_id TEXT PRIMARY KEY,
                mode TEXT,
                symbol TEXT,
                timeframe TEXT,
                ts_utc TEXT,
                request_json TEXT,
                response_json TEXT,
                status TEXT,
                action TEXT,
                side TEXT,
                lots REAL,
                anthropic_request_id TEXT,
                latency_ms INTEGER,
                created_at TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS plans (
                request_id TEXT PRIMARY KEY,
                symbol TEXT,
                scenario TEXT,
                side TEXT,
                confidence REAL,
                basket_tp_pct REAL,
                invalidation_price REAL,
                layers_count INTEGER,
                status TEXT,
                request_json TEXT,
                response_json TEXT,
                created_at TEXT
            )
            """
        )
        self._add_columns_if_missing(
            "ALTER TABLE plans ADD COLUMN version TEXT DEFAULT 'v2'",
            "ALTER TABLE plans ADD COLUMN basket_slot TEXT",
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS plan_layers (
                id TEXT PRIMARY KEY,
                request_id TEXT,
                layer_id INTEGER,
                order_type TEXT,
                price REAL,
                lots REAL,
                sl REAL,
                tp REAL,
                expiration_utc TEXT,
                magic INTEGER,
                created_at TEXT
            )
            """
        )
        self._add_columns_if_missing(
            "ALTER TABLE plan_layers ADD COLUMN is_anchor INTEGER DEFAULT 0",
            "ALTER TABLE plan_layers ADD COLUMN weight REAL",
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS basket_results (
                basket_id TEXT PRIMARY KEY,
                version TEXT,
                symbol TEXT,
                side TEXT,
                opened_at_utc TEXT,
                closed_at_utc TEXT,
                close_reason TEXT,
                bursts INTEGER,
                positions INTEGER,
                gross_profit REAL,
                gross_loss REAL,
                net_pnl REAL,
                max_floating_dd REAL,
                avg_slippage_points REAL,
                avg_spread_points REAL,
                decision_latency_ms INTEGER,
                equity_at_open REAL,
                equity_at_close REAL,
                created_at TEXT
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_basket_results_version "
            "ON basket_results(version, created_at DESC)"
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_events (
                id TEXT PRIMARY KEY,
                request_id TEXT,
                symbol TEXT,
                trans_type TEXT,
                order_ticket INTEGER,
                deal_ticket INTEGER,
                position_ticket INTEGER,
                retcode INTEGER,
                payload_json TEXT,
                created_at TEXT
            )
            """
        )

    def _add_columns_if_missing(self, *ddl_statements: str) -> None:
        """
        Run ADD COLUMN migrations that are safe to repeat on every startup.

        Only "duplicate column" is tolerated — a locked database, a disk error
        or a typo in the DDL must surface instead of leaving the schema half
        migrated while later INSERTs silently write NULLs.
        """
        for ddl in ddl_statements:
            try:
                self.conn.execute(ddl)
            except sqlite3.OperationalError as exc:
                if "duplicate column" in str(exc).lower():
                    continue
                logger.error("ledger migration failed: %s (%s)", ddl, exc)
                raise

    def write_decision(self, req: DecisionRequest, resp: DecisionResponse) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO decisions
                (request_id, mode, symbol, timeframe, ts_utc, request_json, response_json,
                 status, action, side, lots, anthropic_request_id, latency_ms, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    req.request_id,
                    req.mode,
                    req.symbol,
                    req.timeframe,
                    req.timestamp_utc,
                    req.model_dump_json(),
                    resp.model_dump_json(),
                    resp.status,
                    resp.decision.action,
                    resp.decision.side,
                    resp.decision.lots,
                    resp.meta.anthropic_request_id,
                    resp.meta.latency_ms,
                    now_utc(),
                ),
            )

    def write_plan(self, req: LayerPlanRequest, resp: LayerPlanResponse) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO plans
                (request_id, symbol, scenario, side, confidence, basket_tp_pct,
                 invalidation_price, layers_count, status, request_json, response_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    req.request_id,
                    req.symbol,
                    resp.plan.scenario,
                    resp.plan.side_bias,
                    resp.plan.confidence,
                    resp.plan.basket_tp_pct_equity,
                    resp.plan.scenario_invalidation_price,
                    len(resp.plan.layers),
                    resp.status,
                    req.model_dump_json(),
                    resp.model_dump_json(),
                    now_utc(),
                ),
            )
            for layer in resp.plan.layers:
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO plan_layers
                    (id, request_id, layer_id, order_type, price, lots, sl, tp,
                     expiration_utc, magic, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{req.request_id}-L{layer.layer_id}",
                        req.request_id,
                        layer.layer_id,
                        layer.order_type,
                        layer.price,
                        layer.lots,
                        layer.sl,
                        layer.tp,
                        layer.expiration_utc,
                        layer.magic,
                        now_utc(),
                    ),
                )

    def write_v3_plan(self, req: V3PlanRequest, resp: V3PlanResponse) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO plans
                (request_id, symbol, scenario, side, confidence, basket_tp_pct,
                 invalidation_price, layers_count, status, request_json, response_json,
                 created_at, version, basket_slot)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    req.request_id,
                    req.symbol,
                    resp.plan.scenario,
                    resp.plan.side_bias,
                    resp.plan.confidence,
                    resp.plan.basket_tp_pct_equity,
                    resp.plan.scenario_invalidation_price,
                    len(resp.plan.layers),
                    resp.status,
                    req.model_dump_json(),
                    resp.model_dump_json(),
                    now_utc(),
                    "v3",
                    resp.plan.basket_slot,
                ),
            )
            for layer in resp.plan.layers:
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO plan_layers
                    (id, request_id, layer_id, order_type, price, lots, sl, tp,
                     expiration_utc, magic, created_at, is_anchor, weight)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{req.request_id}-L{layer.layer_id}",
                        req.request_id,
                        layer.layer_id,
                        layer.order_type,
                        layer.price,
                        layer.lots,
                        layer.sl,
                        layer.tp,
                        resp.plan.valid_until_utc,
                        layer.magic,
                        now_utc(),
                        1 if layer.is_anchor else 0,
                        layer.weight,
                    ),
                )

    def write_v4_plan(self, req: V4PlanRequest, resp: V4PlanResponse) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO plans
                (request_id, symbol, scenario, side, confidence, basket_tp_pct,
                 invalidation_price, layers_count, status, request_json, response_json,
                 created_at, version, basket_slot)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    req.request_id,
                    req.symbol,
                    resp.plan.scenario,
                    resp.plan.side_bias,
                    resp.plan.confidence,
                    0.0,   # V4 doesn't use basket TP %
                    (resp.plan.zone.sl_price if resp.plan.zone else None),
                    len(resp.plan.layers),
                    resp.status,
                    req.model_dump_json(),
                    resp.model_dump_json(),
                    now_utc(),
                    "v4",
                    None,
                ),
            )
            for layer in resp.plan.layers:
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO plan_layers
                    (id, request_id, layer_id, order_type, price, lots, sl, tp,
                     expiration_utc, magic, created_at, is_anchor, weight)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{req.request_id}-L{layer.layer_id}",
                        req.request_id,
                        layer.layer_id,
                        layer.order_type,
                        layer.price,
                        layer.lots,
                        layer.sl,
                        None,
                        resp.plan.valid_until_utc,
                        layer.magic,
                        now_utc(),
                        0,
                        0.5,   # 50/50 split
                    ),
                )

    def write_v5_burst(self, req: V5BurstRequest, resp: V5BurstResponse) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO plans
                (request_id, symbol, scenario, side, confidence, basket_tp_pct,
                 invalidation_price, layers_count, status, request_json, response_json,
                 created_at, version, basket_slot)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    req.request_id,
                    req.symbol,
                    resp.burst.scenario,
                    resp.burst.side_bias,
                    resp.burst.confidence,
                    resp.burst.exit_rules.basket_tp_pct_equity,
                    None,   # no invalidation price for scalper
                    len(resp.burst.layers),
                    resp.status,
                    req.model_dump_json(),
                    resp.model_dump_json(),
                    now_utc(),
                    "v5",
                    None,
                ),
            )
            for layer in resp.burst.layers:
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO plan_layers
                    (id, request_id, layer_id, order_type, price, lots, sl, tp,
                     expiration_utc, magic, created_at, is_anchor, weight)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{req.request_id}-L{layer.layer_id}",
                        req.request_id,
                        layer.layer_id,
                        layer.order_type,
                        0.0,                  # market order — no price
                        layer.lots,
                        0.0,
                        0.0,
                        resp.burst.valid_until_utc,
                        layer.magic,
                        now_utc(),
                        1 if layer.layer_id == 1 else 0,
                        1.0 / max(1, len(resp.burst.layers)),
                    ),
                )

    def write_basket_result(self, event: BasketResultEvent) -> None:
        """Insert or replace a result, but never replace a stored V6 result (the V6
        breakers read those, and only the signed V6 route may write them)."""
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO basket_results
                (basket_id, version, symbol, side, opened_at_utc, closed_at_utc,
                 close_reason, bursts, positions, gross_profit, gross_loss, net_pnl,
                 max_floating_dd, avg_slippage_points, avg_spread_points,
                 decision_latency_ms, equity_at_open, equity_at_close, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """ + _BASKET_UPSERT,
                (
                    event.basket_id,
                    event.version,
                    event.symbol,
                    event.side,
                    event.opened_at_utc,
                    event.closed_at_utc,
                    event.close_reason,
                    event.bursts,
                    event.positions,
                    event.gross_profit,
                    event.gross_loss,
                    event.net_pnl,
                    event.max_floating_dd,
                    event.avg_slippage_points,
                    event.avg_spread_points,
                    event.decision_latency_ms,
                    event.equity_at_open,
                    event.equity_at_close,
                    now_utc(),
                ),
            )

    def write_trade_event(self, event: TradeTransactionEvent) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO trade_events
                (id, request_id, symbol, trans_type, order_ticket, deal_ticket, position_ticket,
                 retcode, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    event.request_id,
                    event.symbol,
                    event.trans_type,
                    event.order,
                    event.deal,
                    event.position,
                    event.retcode,
                    event.model_dump_json(),
                    now_utc(),
                ),
            )
