"""
A valid operator packet body and decision, as JSON-shaped dicts.

Tests change a copy and validate it through the JSON path, exactly as the
operator API receives a decision.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Final

from app.v6.schemas.operator import (
    OperatorPacket, OperatorPacketBody, allowed_values, canonical_json, seal_packet,
)

from .cycle_fixtures_v6 import (
    chief_payload, liquidity_payload, news_payload, pa_payload, structure_payload,
)

BAR_OPEN: Final[int] = 1_789_564_500
BAR_CLOSE: Final[int] = BAR_OPEN + 900
CREATED: Final[int] = BAR_CLOSE + 2
EXPIRES: Final[int] = BAR_CLOSE + 300
CYCLE_ID: Final[str] = "c-0123456789abcdef"
SESSION_ID: Final[str] = "a1b2c3d4e5f6"
BUY_ID: Final[str] = f"displacement-buy-{BAR_OPEN}"
SELL_ID: Final[str] = f"orb-sell-{BAR_OPEN}-ny"
EVENT_ID: Final[str] = "mt5:840030016"


def candidate(candidate_id: str = BUY_ID, side: str = "buy") -> dict[str, Any]:
    sign = 1 if side == "buy" else -1
    return {
        "candidate_id": candidate_id, "setup": candidate_id.split("-")[0], "side": side,
        "entry": 4535.07, "invalidation": 4535.07 - sign * 7.0,
        "reason_codes": ["AT_H1_LEVEL"], "features": {"body_ratio": 0.8, "range_vol": 2.5},
        "exit": {"sl": 4535.07 - sign * 7.0, "tp": 4535.07 + sign * 14.0,
                 "stop_distance": 7.0, "reward_r": 2.0, "time_barrier_s": 7200},
        "sizing": {"lots": 0.01, "risk_usd": 7.4, "loss_per_lot": 740.0},
        "sizing_refusal": [],
    }


def body(**changes: Any) -> dict[str, Any]:
    allowed = allowed_values(operator_agents=("claude_code", "codex"),
                             candidate_ids=(BUY_ID, SELL_ID), event_ids=(EVENT_ID,),
                             pa_min_conviction=0.6)
    document: dict[str, Any] = {
        "schema_version": "v6.operator.packet.1", "cycle_id": CYCLE_ID,
        "created_at_epoch": CREATED, "expires_at_epoch": EXPIRES,
        "bar_open_epoch": BAR_OPEN, "bar_close_epoch": BAR_CLOSE, "mode": "execute",
        "session_id": SESSION_ID,
        "account": {"trade_mode": "DEMO", "server": "MetaQuotes-Demo", "equity_band": "1k_2k"},
        "market": {"bid": 4535.18, "ask": 4535.35, "spread_points": 17, "atr_m5": 3.1,
                   "atr_m15": 8.0, "atr_h1": None,
                   "features": {"atr_m5": 3.1, "friction_atr_m5": 0.06, "er_m15": 0.4}},
        "session": {"phase": "overlap", "main_window_third": "mid", "entries_allowed": True,
                    "continuation_allowed": True, "block_reasons": [], "armed": True},
        "bars": {"M15": [[BAR_OPEN, 4530.0, 4536.0, 4529.5, 4535.2]],
                 "H1": [[BAR_OPEN - 2700, 4525.0, 4536.0, 4520.0, 4531.0]]},
        "gates": [{"code": "SPREAD", "passed": True, "value": 17, "limit": 35,
                   "detail": "account_type=standard"},
                  {"code": "SESSION", "passed": True, "value": "OK", "limit": None,
                   "detail": "phase=overlap third=mid"}],
        "calendar": {"as_of_epoch": BAR_CLOSE, "blackout": False, "stale": False, "codes": [],
                     "next_event_minutes": 105.0, "last_event_minutes_ago": None,
                     "events": [{"event_id": EVENT_ID, "time_epoch": BAR_CLOSE + 6300,
                                 "currency": "USD", "importance": "HIGH", "code": "cpi-yy",
                                 "actual": None, "forecast": 2.9, "previous": 2.8}]},
        "candidates": [candidate(), candidate(SELL_ID, "sell")],
        "baseline_views": {"price_action": pa_payload(BUY_ID), "news_risk": news_payload(),
                           "liquidity": liquidity_payload(),
                           "structure": structure_payload()},
        "allowed": allowed.model_dump(mode="json"),
    }
    return {**document, **copy.deepcopy(changes)}


def packet_body(**changes: Any) -> OperatorPacketBody:
    return OperatorPacketBody.model_validate_json(canonical_json(body(**changes)))


def packet(**changes: Any) -> OperatorPacket:
    return seal_packet(packet_body(**changes))


def decision(sealed: OperatorPacket, **changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": "v6.operator.decision.1", "cycle_id": sealed.cycle_id,
        "packet_hash": sealed.packet_hash, "agent": "codex",
        "views": {"price_action": pa_payload(BUY_ID, conviction=0.8),
                  "news_risk": news_payload((EVENT_ID,)), "liquidity": liquidity_payload(),
                  "structure": structure_payload()},
        "chief": chief_payload("ENTER", BUY_ID), "rebuttal": {BUY_ID: "maintain"},
    }
    return {**document, **copy.deepcopy(changes)}


def raw(document: dict[str, Any]) -> bytes:
    return json.dumps(document).encode("utf-8")
