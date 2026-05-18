from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from threading import Lock

from .models import (
    DecisionRequest,
    DecisionResponse,
    LayerPlanRequest,
    LayerPlanResponse,
    TradeTransactionEvent,
)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class Ledger:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
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
