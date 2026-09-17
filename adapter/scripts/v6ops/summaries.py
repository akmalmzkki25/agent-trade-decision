"""
Compact views of the control-plane answers (session, status).

Only fields the operator acts on are kept. Missing fields stay None, so an
older or newer adapter never breaks the CLI.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from .context import get_path

POSITIONS_POLICY: Final[str] = "open V6 positions keep running to SL, TP or the time barrier"
SESSION_KEYS: Final[tuple[str, ...]] = (
    "session_id", "started_at", "armed", "armed_at", "disarmed_at", "disarm_reason")
RUNTIME_KEYS: Final[tuple[str, ...]] = (
    "enabled", "mode", "backend", "trade_mode", "ea_last_seen_age_s", "warm",
    "halt_file_present", "server_time_epoch", "operator_agents", "operator_ready", "ea_signing")


def session_fields(session: Any) -> dict[str, object]:
    return {key: get_path(session, key) for key in SESSION_KEYS}


def day_fields(summary: Any) -> dict[str, object]:
    return {
        "trading_day": get_path(summary, "trading_day"), "cycles": get_path(summary, "cycles"),
        "by_status": get_path(summary, "by_status"),
        "hold_reasons": get_path(summary, "hold_reasons"),
        "shadow_intents": get_path(summary, "shadow_intents"),
        "intents": get_path(summary, "intents"),
        "intent_statuses": get_path(summary, "intent_statuses"),
        "open_v6_positions": get_path(summary, "exposure", "open_v6_positions"),
        "pending_v6_orders": get_path(summary, "exposure", "pending_v6_orders"),
        "floating_pnl_v6": get_path(summary, "exposure", "floating_pnl_v6"),
    }


def summarise_start(body: Mapping[str, Any]) -> dict[str, object]:
    """`arm_reason`/`arm_detail`: why an execute session is (not) armed (None in shadow)."""
    session = body.get("session")
    return {**session_fields(session), "created": body.get("created"),
            "trading_day": body.get("trading_day"),
            "backend": get_path(session, "backend"), "mode": get_path(session, "mode"),
            "refusal": body.get("refusal"), "detail": body.get("detail"),
            "arm_reason": get_path(body, "arm", "reason"),
            "arm_detail": get_path(body, "arm", "detail")}


def summarise_stop(body: Mapping[str, Any]) -> dict[str, object]:
    session = body.get("session")
    return {**session_fields(session), **day_fields(body.get("summary")),
            "stopped": body.get("stopped"), "stop_reason": get_path(session, "stop_reason"),
            "command": get_path(body, "command", "command"), "positions": POSITIONS_POLICY}


def summarise_session(body: Mapping[str, Any]) -> dict[str, object]:
    session = body.get("session")
    return {**day_fields(body.get("summary")), **session_fields(session),
            "active": session is not None, "backend": body.get("backend"),
            "mode": body.get("mode"), "halted": body.get("halted"),
            "pending_command": get_path(body, "command", "command")}


def summarise_runtime(body: Mapping[str, Any]) -> dict[str, object]:
    session = body.get("session")
    return {**{key: body.get(key) for key in RUNTIME_KEYS},
            "last_snapshot": get_path(body, "last_snapshot", "bar_open_epoch"),
            "runtime_status": get_path(body, "runtime", "status"),
            "breakers": get_path(body, "runtime", "breakers"),
            "session_active": session is not None if "session" in body else None,
            "session_id": get_path(session, "session_id"),
            "armed": get_path(session, "armed")}


def passthrough(body: Mapping[str, Any]) -> dict[str, object]:
    return dict(body)
