"""
`template` and `submit`: turn a packet into a decision and hand it to the adapter.

`template` copies the packet's `decision_template` (the rules views and a HOLD
Chief) and blanks `agent`; `submit --agent` fills it in. The CLI never judges a
decision: POST /v6/operator/decision validates it role by role
(`deliberation/operator_decision.py`) and answers with the verdict, which
`submit` prints with its codes. Local checks only catch what would make the
request itself wrong (size, JSON, agent) and add warnings (stale or expired
packet). `template` never overwrites an edited decision for the same packet
unless `--force` is given.

The route (routes/v6_operator.py) answers 202 {"accepted": true, "code":
"ACCEPTED", "flagged": [...]}, 409 {"code": UNKNOWN_CYCLE | EXPIRED | HASH_MISMATCH |
AGENT_NOT_ALLOWED | ALREADY_DECIDED, "error": DECISION_*}, 422 {"code": "INVALID",
"error": DECISION_*} or 403 {"code": "APP-V6-DEMO-403"}. After an acceptance the
CLI reads GET /v6/status for a few seconds to report the cycle's outcome
(`result`: ENTER, ENTER_SHADOW or HOLD with its reason).

A refusal leaves the packet open, except code EXPIRED: the packet expired or was
withdrawn (session stop, halt, disarm), so resubmitting cannot help. After that
refusal `submit` reads GET /v6/status; when it shows no active session the exit
code is 4.

Exit codes of `submit`: 0 accepted, 1 refused or failed, 2 usage, 3 adapter
unreachable, 4 packet closed and no active session, 5 not a DEMO account.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from .context import (
    AGENTS, EXIT_ERROR, EXIT_NO_SESSION, EXIT_NOT_DEMO, EXIT_OK, EXIT_UNREACHABLE, Context, clean,
    emit, emit_error, get_path,
)
from .files import (
    is_stdin, load_json_file, load_json_object, read_capped, read_stream_capped, write_json_atomic,
)
from .transport import HttpReply, TransportError, UsageError
from .waiting import NEXT_NO_SESSION, collect_codes, demo_refused, session_gone

DECISION_PATH: Final[str] = "/v6/operator/decision"
STATUS_PATH: Final[str] = "/v6/status"
DECISION_SCHEMA: Final[str] = "v6.operator.decision.2"
MAX_DECISION_BYTES: Final[int] = 64 * 1024
RESULT_POLLS: Final[int] = 8
RESULT_POLL_S: Final[float] = 0.5
RESULT_KEYS: Final[tuple[str, ...]] = (
    "status", "hold_reason", "hold_detail", "failed_gates", "shadow_intent", "intent")
NEXT_EDIT: Final[str] = ("edit views and chief in the decision file (keep ids from the packet), "
                         f"then run: submit --agent <{'|'.join(AGENTS)}>")
NEXT_EXPIRED: Final[str] = "the packet has expired: run wait again"
NEXT_ACCEPTED: Final[str] = "run wait again"
NEXT_RESULT_UNKNOWN: Final[str] = ("run wait again; the cycle result was not visible yet "
                                   "(see status: last_cycle)")
NEXT_REFUSED: Final[str] = "fix the codes if the packet is still open, else run wait again"
NEXT_CLOSED: Final[str] = ("the packet is closed (expired or withdrawn): do not resubmit; "
                           "run wait again")
CLOSED_CODE: Final[str] = "EXPIRED"     # operator_queue.REFUSE_EXPIRED
# The adapter's own fallbacks (schemas.operator), used when a packet has no template.
ABSTAIN_VIEW: Final[Mapping[str, Any]] = MappingProxyType({"abstain": True, "ranked": []})
UNKNOWN_VIEWS: Final[Mapping[str, Mapping[str, Any]]] = MappingProxyType({
    "news_risk": MappingProxyType({
        "stance": "CAUTION", "size_multiplier": 0.5, "regime": "UNCLEAR", "event_ids": [],
        "reason_codes": ["DATA_MISSING"], "note": ""}),
    "liquidity": MappingProxyType({
        "stance": "CAUTION", "size_multiplier": 0.5, "order_style": "LIMIT",
        "reason_codes": ["DATA_MISSING"], "note": ""}),
    "structure": MappingProxyType({
        "regime": "UNCLEAR", "counter_structure_veto": False, "size_multiplier": 0.5,
        "named_patterns": [], "reason_codes": ["DATA_MISSING"], "note": ""}),
})
HOLD_CHIEF: Final[Mapping[str, Any]] = MappingProxyType({
    "action": "HOLD", "candidate_id": None, "risk_tier": "reduced", "order_style": "LIMIT",
    "exit_profile": "STANDARD", "confidence": 0.0, "rationale": "", "dissent": ""})


def _plain(value: Any) -> Any:
    """Deep copy with read-only mappings turned into dicts."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return copy.deepcopy(value)


def fallback_template(packet: Mapping[str, Any]) -> dict[str, Any]:
    baseline = packet.get("baseline_views")
    pa_view = get_path(baseline, "price_action")
    views = {"price_action": _plain(pa_view if isinstance(pa_view, Mapping) else ABSTAIN_VIEW)}
    for role, default in UNKNOWN_VIEWS.items():
        view = get_path(baseline, role)
        views[role] = _plain(view if isinstance(view, Mapping) else default)
    return {"schema_version": DECISION_SCHEMA, "cycle_id": packet.get("cycle_id"),
            "packet_hash": packet.get("packet_hash"), "agent": None, "views": views,
            "chief": _plain(HOLD_CHIEF), "rebuttal": {}, "entry_plan": None, "lots": None,
            "pending_action": "KEEP" if isinstance(packet.get("pending_order"), Mapping)
            else None}


def build_template(packet: Mapping[str, Any]) -> dict[str, Any]:
    """The packet's decision template with `agent` left for `submit --agent`."""
    template = packet.get("decision_template")
    decision = _plain(template) if isinstance(template, Mapping) else fallback_template(packet)
    return {**decision, "agent": None, "rebuttal": decision.get("rebuttal") or {},
            "entry_plan": decision.get("entry_plan"), "lots": decision.get("lots"),
            "pending_action": decision.get("pending_action")}


def agent_entry_example(packet: Mapping[str, Any]) -> dict[str, Any] | None:
    """How to enter the agent's own trade: the fields to set (values are placeholders)."""
    limits = packet.get("limits")
    if not isinstance(limits, Mapping) or not limits.get("agent_entry_possible"):
        return None
    entry_id = limits.get("agent_entry_id")
    return {
        "views.price_action.ranked": [{"candidate_id": entry_id, "verdict": "TAKE",
                                       "conviction": 0.7, "reason_codes": [], "note": ""}],
        "chief": {"action": "ENTER", "candidate_id": entry_id,
                  "order_style": "LIMIT (must equal entry_plan.order_type)"},
        "lots": f"{limits.get('volume_min')}..{limits.get('max_lots')} in steps of "
                f"{limits.get('lots_step')} (null = the minimum; the budget may reduce it)",
        "entry_plan": {"side": "buy|sell", "order_type": "LIMIT|MARKET",
                       "entry": "price for LIMIT (buy <= buy_limit_max, sell >= "
                                "sell_limit_min), null for MARKET",
                       "stop": "price, distance in [stop_floor, max_stop_distance]",
                       "target": "price between min_reward_r and max_reward_r, or null",
                       "thesis": "<= 300 chars"},
    }


def _existing_edit(out_path: Path, fresh: Mapping[str, Any]) -> bool:
    """True when `out_path` already holds an edited decision for the same packet."""
    try:
        current = load_json_file(out_path, "decision file", MAX_DECISION_BYTES)
    except UsageError:
        return False
    same_packet = all(current.get(key) == fresh.get(key) for key in ("cycle_id", "packet_hash"))
    return same_packet and current != fresh


def run_template(ctx: Context, *, packet_path: Path, out_path: Path, force: bool) -> int:
    packet = load_json_file(packet_path, "packet file")
    expires = packet.get("expires_at_epoch")
    left = int(expires - ctx.clock()) if type(expires) is int else 0
    summary = {"cycle_id": packet.get("cycle_id"), "packet_hash": packet.get("packet_hash"),
               "expires_at_epoch": expires, "seconds_left": max(left, 0),
               "candidate_ids": get_path(packet, "allowed", "candidate_ids"),
               "event_ids": get_path(packet, "allowed", "event_ids"),
               "pa_min_conviction": get_path(packet, "allowed", "pa_min_conviction"),
               "limits": packet.get("limits"),
               "agent_entry_example": agent_entry_example(packet),
               "agents": get_path(packet, "allowed", "agents")}
    if left <= 0:
        emit(ctx.stdout, {**summary, "written": None, "expired": True, "next": NEXT_EXPIRED})
        return EXIT_ERROR
    decision = build_template(packet)
    if not force and _existing_edit(out_path, decision):
        raise UsageError(f"{out_path} already holds an edited decision for this cycle; "
                         "pass --force to overwrite it")
    write_json_atomic(out_path, decision)
    emit(ctx.stdout, {**summary, "written": str(out_path), "expired": False, "next": NEXT_EDIT})
    return EXIT_OK


# --- submit --------------------------------------------------------------------------
def with_agent(decision: Mapping[str, Any], agent: str) -> dict[str, Any]:
    if agent not in AGENTS:
        raise UsageError(f"--agent must be one of {', '.join(AGENTS)}")
    named = decision.get("agent")
    if named not in (None, "") and named != agent:
        raise UsageError(f"the decision names agent {clean(named, 20)!r} but --agent is {agent}")
    return {**decision, "agent": agent}


def submission_warnings(decision: Mapping[str, Any], packet: Mapping[str, Any] | None,
                        now: float) -> list[str]:
    warnings: list[str] = []
    if decision.get("schema_version") != DECISION_SCHEMA:
        warnings.append(f"schema_version is not {DECISION_SCHEMA}")
    if packet is None:
        return warnings
    same = (decision.get("cycle_id") == packet.get("cycle_id")
            and decision.get("packet_hash") == packet.get("packet_hash"))
    if not same:
        warnings.append("the decision answers another packet than the packet file")
    expires = packet.get("expires_at_epoch")
    if same and type(expires) is int and now > expires:
        warnings.append(f"the packet expired {int(now - expires)} s ago (DECISION_EXPIRED)")
    return warnings


def read_decision(ctx: Context, decision_path: Path) -> dict[str, Any]:
    raw = (read_stream_capped(ctx.stdin, MAX_DECISION_BYTES, "decision on stdin")
           if is_stdin(decision_path)
           else read_capped(decision_path, MAX_DECISION_BYTES, "decision file"))
    return load_json_object(raw, "decision")


def _optional_packet(packet_path: Path) -> dict[str, Any] | None:
    try:
        return load_json_file(packet_path, "packet file")
    except UsageError:
        return None


def encode_decision(decision: Mapping[str, Any]) -> bytes:
    body = json.dumps(decision, separators=(",", ":"), allow_nan=False).encode("ascii")
    if len(body) > MAX_DECISION_BYTES:
        raise UsageError(f"the decision is {len(body)} bytes; the limit is {MAX_DECISION_BYTES}")
    return body


def verdict(reply: HttpReply, decision: Mapping[str, Any],
            warnings: list[str]) -> tuple[dict[str, object], int]:
    body = reply.json()
    document = body if isinstance(body, Mapping) else {}
    found = collect_codes(document)
    accepted = reply.ok and isinstance(body, Mapping) and document.get("accepted") is not False
    summary = {"accepted": accepted, "http_status": reply.status,
               "code": document.get("code"), "error": document.get("error"),
               "cycle_id": decision.get("cycle_id"), "agent": decision.get("agent"),
               "chief_action": get_path(decision, "chief", "action"),
               "chief_candidate": get_path(decision, "chief", "candidate_id"),
               "flagged": document.get("flagged"), "codes": list(dict.fromkeys(found)),
               "warnings": warnings, "response": document,
               "next": NEXT_ACCEPTED if accepted else refused_next(document)}
    if accepted:
        return summary, EXIT_OK
    if demo_refused(found, document):
        return summary, EXIT_NOT_DEMO
    return summary, EXIT_ERROR


def refused_next(document: Mapping[str, Any]) -> str:
    return NEXT_CLOSED if document.get("code") == CLOSED_CODE else NEXT_REFUSED


def session_ended(ctx: Context) -> bool:
    """GET /v6/status shows no active session (an unreadable status says nothing)."""
    try:
        reply = ctx.client.send("GET", STATUS_PATH, auth=False)
    except TransportError:
        return False
    return reply.ok and session_gone(reply.json())


def cycle_result(ctx: Context, cycle_id: object) -> dict[str, object] | None:
    """The finished cycle as GET /v6/status reports it, once it shows up (a few seconds)."""
    for _ in range(RESULT_POLLS):
        ctx.sleep(RESULT_POLL_S)
        try:
            reply = ctx.client.send("GET", STATUS_PATH, auth=False)
        except TransportError:
            return None
        cycle = get_path(reply.json(), "last_cycle")
        if isinstance(cycle, Mapping) and cycle.get("cycle_id") == cycle_id:
            return {key: cycle[key] for key in RESULT_KEYS if key in cycle}
    return None


def with_result(ctx: Context, summary: dict[str, object]) -> dict[str, object]:
    result = cycle_result(ctx, summary["cycle_id"])
    action = None if result is None else result.get("status")
    return {**summary, "result": result, "action": action,
            "next": NEXT_ACCEPTED if result is not None else NEXT_RESULT_UNKNOWN}


def run_submit(ctx: Context, *, agent: str, decision_path: Path, packet_path: Path) -> int:
    decision = with_agent(read_decision(ctx, decision_path), agent)
    warnings = submission_warnings(decision, _optional_packet(packet_path), ctx.clock())
    body = encode_decision(decision)
    try:
        reply = ctx.client.send("POST", DECISION_PATH, body)
    except TransportError as exc:
        emit_error(ctx, "unreachable", str(exc), base_url=ctx.client.base_url)
        return EXIT_UNREACHABLE
    summary, code = verdict(reply, decision, warnings)
    if code == EXIT_ERROR and summary["code"] == CLOSED_CODE and session_ended(ctx):
        summary, code = {**summary, "next": NEXT_NO_SESSION}, EXIT_NO_SESSION
    emit(ctx.stdout, with_result(ctx, summary) if code == EXIT_OK else summary)
    return code
