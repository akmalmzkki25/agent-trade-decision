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
AGENT_ID: Final[str] = f"agent-{BAR_OPEN}"
BID: Final[float] = 4535.18
ASK: Final[float] = 4535.35


def limits_block(**changes: Any) -> dict[str, Any]:
    """The packet `limits` for this bar (standard account, $25 budget)."""
    block: dict[str, Any] = {
        "agent_entry_id": AGENT_ID, "agent_entry_possible": True, "tick_size": 0.01,
        "digits": 2, "buy_limit_max": round(ASK - 0.01, 2),
        "sell_limit_min": round(BID + 0.01, 2), "max_entry_distance": 12.0,
        "stop_floor": 6.0, "max_stop_distance": 22.5, "min_reward_r": 1.0,
        "max_reward_r": 5.0, "default_reward_r": 2.0, "risk_budget_usd": 25.0,
        "volume_min": 0.01, "max_lots": 0.01, "pending_expiry_epoch": BAR_CLOSE + 1800,
        "time_barrier_s": 7200,
    }
    return {**block, **changes}


def levels_block() -> dict[str, Any]:
    return {"prior_day_high": 4550.0, "prior_day_low": 4480.0,
            "round_10_below": 4530.0, "round_10_above": 4540.0,
            "round_50_below": 4500.0, "round_50_above": 4550.0,
            "pivots_m15": [{"kind": "low", "price": 4526.4, "t": BAR_OPEN - 3600}],
            "pivots_h1": [{"kind": "high", "price": 4541.0, "t": BAR_OPEN - 18000}]}


def entry_plan(**changes: Any) -> dict[str, Any]:
    """A valid agent plan: a buy LIMIT 2 below the ask, stop 8 under, 2R target."""
    plan: dict[str, Any] = {"side": "buy", "order_type": "LIMIT", "entry": 4533.35,
                            "stop": 4525.35, "target": 4549.35,
                            "thesis": "bounce from the M15 pivot low"}
    return {**plan, **changes}


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
                             candidate_ids=(BUY_ID, SELL_ID, AGENT_ID), event_ids=(EVENT_ID,),
                             pa_min_conviction=0.6)
    document: dict[str, Any] = {
        "schema_version": "v6.operator.packet.2", "cycle_id": CYCLE_ID,
        "created_at_epoch": CREATED, "expires_at_epoch": EXPIRES,
        "bar_open_epoch": BAR_OPEN, "bar_close_epoch": BAR_CLOSE, "mode": "execute",
        "session_id": SESSION_ID,
        "account": {"trade_mode": "DEMO", "server": "MetaQuotes-Demo", "equity_band": "1k_2k"},
        "market": {"bid": BID, "ask": ASK, "spread_points": 17, "atr_m5": 3.1,
                   "atr_m15": 8.0, "atr_h1": None,
                   "features": {"atr_m5": 3.1, "friction_atr_m5": 0.06, "er_m15": 0.4}},
        "session": {"phase": "overlap", "main_window_third": "mid", "entries_allowed": True,
                    "continuation_allowed": True, "block_reasons": [], "armed": True,
                    "quality": "prime", "in_main_window": True},
        "bars": {"M1": [[BAR_OPEN + 840, 4534.8, 4535.4, 4534.6, 4535.2]],
                 "M5": [[BAR_OPEN + 600, 4533.0, 4535.6, 4532.8, 4535.2]],
                 "M15": [[BAR_OPEN, 4530.0, 4536.0, 4529.5, 4535.2]],
                 "H1": [[BAR_OPEN - 2700, 4525.0, 4536.0, 4520.0, 4531.0]],
                 "D1": [[BAR_OPEN - 86_400, 4490.0, 4550.0, 4480.0, 4528.0]]},
        "levels": levels_block(),
        "limits": limits_block(),
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
        "schema_version": "v6.operator.decision.2", "cycle_id": sealed.cycle_id,
        "packet_hash": sealed.packet_hash, "agent": "codex",
        "views": {"price_action": pa_payload(BUY_ID, conviction=0.8),
                  "news_risk": news_payload((EVENT_ID,)), "liquidity": liquidity_payload(),
                  "structure": structure_payload()},
        "chief": chief_payload("ENTER", BUY_ID), "rebuttal": {BUY_ID: "maintain"},
    }
    return {**document, **copy.deepcopy(changes)}


def raw(document: dict[str, Any]) -> bytes:
    return json.dumps(document).encode("utf-8")
