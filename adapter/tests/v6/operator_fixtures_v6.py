"""
A valid operator packet body and decision, as JSON-shaped dicts.

Tests change a copy and validate it through the JSON path, exactly as the
operator API receives a decision. `packet()` is a flat v3 packet; `managed_packet`
is a pending or position packet; `decision_v3` is the sealed template as an agent
would edit it and `enter_v3` a valid ENTER of the agent's own plan (`plan_v3`).
"""

from __future__ import annotations

import copy
import json
from typing import Any, Final

from app.v6.schemas.operator import (
    OperatorPacket, OperatorPacketBody, allowed_values, canonical_json, seal_packet,
)

from .cycle_fixtures_v6 import liquidity_payload, news_payload, pa_payload, structure_payload

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
        "volume_min": 0.01, "lots_step": 0.01, "max_lots": 0.03,
        "pending_expiry_epoch": BAR_CLOSE + 1800,
        "time_barrier_s": 7200,
        "buy_stop_min": round(ASK + 0.37, 2), "sell_stop_max": round(BID - 0.37, 2),
        "modify_distance": 0.37, "min_tp1_r": 0.5,
        "time_limit_min_minutes": 60, "time_limit_max_minutes": 240,
        "pending_expiry_min_minutes": 15, "pending_expiry_max_minutes": 60,
    }
    return {**block, **changes}


def levels_block() -> dict[str, Any]:
    return {"prior_day_high": 4550.0, "prior_day_low": 4480.0,
            "round_10_below": 4530.0, "round_10_above": 4540.0,
            "round_50_below": 4500.0, "round_50_above": 4550.0,
            "pivots_m15": [{"kind": "low", "price": 4526.4, "t": BAR_OPEN - 3600}],
            "pivots_h1": [{"kind": "high", "price": 4541.0, "t": BAR_OPEN - 18000}]}


def plan_v3(**changes: Any) -> dict[str, Any]:
    """A valid agent plan: a buy LIMIT 2 below the ask, SL 7 under, TP3 at 2R, SL+ steps."""
    plan: dict[str, Any] = {
        "side": "buy", "order_type": "LIMIT", "entry": 4533.35, "sl": 4526.35,
        "tp1": 4537.5, "tp2": 4541.0, "tp3": 4547.35, "sl_after_tp1": 4533.8,
        "sl_after_tp2": 4537.5, "time_limit_min": 150, "pending_expiry_min": 30,
        "lots": 0.01, "thesis": "bounce from the M15 pivot low"}
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
        "schema_version": "v6.operator.packet.3", "packet_kind": "m15", "state": "flat",
        "cycle_id": CYCLE_ID,
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
        "pending_order": None, "position": None, "last_action": None, "last_bias": None,
        "last_bias_at_epoch": None,
    }
    return {**document, **copy.deepcopy(changes)}


def plan_block(**changes: Any) -> dict[str, Any]:
    block = {"tp1": 4539.0, "tp2": 4543.0, "sl_after_tp1": 4535.5, "sl_after_tp2": 4539.0,
             "step": 0, "time_limit_min": 150}
    return {**block, **changes}


def position_block(**changes: Any) -> dict[str, Any]:
    block = {"ticket": 91, "intent_id": "k7w2m4pq3xza", "side": "buy", "lots": 0.01,
             "open_price": 4533.35, "open_epoch": BAR_CLOSE - 600, "sl": 4526.35,
             "tp": 4549.0, "initial_sl": 4526.35, "profit": 1.83, "r_now": 0.26,
             "mae_points": 120.0, "mfe_points": 240.0, "minutes_open": 10.0,
             "time_limit_epoch": BAR_CLOSE - 600 + 9000, "plan": plan_block()}
    return {**block, **changes}


def pending_block(**changes: Any) -> dict[str, Any]:
    block = {"ticket": 77, "intent_id": "k7w2m4pq3xza", "order_type": "BUY_LIMIT",
             "price": 4531.35, "sl": 4524.35, "tp": 4547.0, "lots": 0.01,
             "expiration_epoch": BAR_CLOSE + 900, "distance_from_quote": 4.0,
             "plan": plan_block(tp1=4535.5, tp2=4541.0, sl_after_tp1=4532.0,
                                sl_after_tp2=4535.5)}
    return {**block, **changes}


def managed_packet(state: str, **changes: Any) -> OperatorPacket:
    """A pending or position packet: no suggestions, no agent entry."""
    allowed = allowed_values(operator_agents=("claude_code", "codex"), candidate_ids=(AGENT_ID,),
                             event_ids=(EVENT_ID,), pa_min_conviction=0.6)
    block = ({"position": position_block()} if state == "position"
             else {"pending_order": pending_block()})
    defaults = {"state": state, "candidates": [],
                "limits": limits_block(agent_entry_possible=False),
                "allowed": allowed.model_dump(mode="json"),
                "baseline_views": {**body()["baseline_views"], "price_action": None}, **block}
    return packet(**{**defaults, **changes})


def packet_body(**changes: Any) -> OperatorPacketBody:
    return OperatorPacketBody.model_validate_json(canonical_json(body(**changes)))


def packet(**changes: Any) -> OperatorPacket:
    return seal_packet(packet_body(**changes))


def decision_v3(sealed: OperatorPacket, **changes: Any) -> dict[str, Any]:
    """The sealed template with the agent set (edit it like an agent would)."""
    template = sealed.decision_template.model_dump(mode="json")
    return {**template, "agent": "codex", **copy.deepcopy(changes)}


def enter_views(conviction: float = 0.8) -> dict[str, Any]:
    """Price Action TAKEs the agent entry id; the news desk names the calendar event."""
    return {"price_action": pa_payload(AGENT_ID, conviction=conviction),
            "news_risk": news_payload((EVENT_ID,)), "liquidity": liquidity_payload(),
            "structure": structure_payload()}


def enter_v3(sealed: OperatorPacket, **changes: Any) -> dict[str, Any]:
    """A valid ENTER of `plan_v3` for the flat `packet()`."""
    document = decision_v3(sealed, action="ENTER", views=enter_views(), entry_plan=plan_v3())
    return {**document, **copy.deepcopy(changes)}


def raw(document: dict[str, Any]) -> bytes:
    return json.dumps(document).encode("utf-8")
