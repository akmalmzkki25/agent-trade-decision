"""
`wait`: long-poll the operator API until a packet, the timeout or the end of the session.

Each round first reads GET /v6/status (no token): a non-DEMO account ends the
wait with exit 5, no active session with exit 4. It then long-polls
POST /v6/operator/wait {"timeout_s": 25, "agent"?} (routes/v6_operator.py):

    200 {"pending": packet, "session": {...}, "armed", "mode", ...}
                                                    packet: written, summarised, exit 0
    200 {"pending": null, "session": {...}, ...}    nothing yet: next round
    200 {..., "session": null}                      no session: exit 4
    403 {"code": "APP-V6-DEMO-403", "policy": ...}  a demo-only policy refusal: exit 5
      ... with POLICY_UNKNOWN_TRADE_MODE             the EA has not reported yet: retried
      ... with another policy (login allow-list)     exit 1, as preflight reports it
    5xx, unreachable                                retried; the third in a row: exit 1
    anything else (401, 403 agent, 404, 400)        exit 1

`packet` is read as an alias of `pending`, and a bare packet body is accepted
too. The loop ends with exit 3 once `--timeout` has passed without a packet; a
round already running may overrun it by up to the status read's HTTP timeout
plus WAIT_POLL_S + HTTP_MARGIN_S (45 s with the defaults), so the agents'
`wait --timeout 240` ends within one 300 s tool call. A packet that is
malformed, expired or not for a DEMO account is never written.
The previous packet file is moved to packet.prev.json first, so a stale packet
can never be mistaken for the new one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .context import (
    DEMO_POLICY_CODES, DEMO_TRADE_MODE, EXIT_ERROR, EXIT_NO_SESSION, EXIT_NOT_DEMO, EXIT_OK,
    EXIT_TIMEOUT, OPERATOR_BACKEND, Context, clean, emit, emit_error, get_path,
)
from .files import archive, write_json_atomic
from .packet_view import render_packet
from .transport import HttpReply, TransportError

WAIT_PATH: Final[str] = "/v6/operator/wait"
STATUS_PATH: Final[str] = "/v6/status"
PACKET_SCHEMA: Final[str] = "v6.operator.packet.3"
PACKET_KEYS: Final[tuple[str, ...]] = ("pending", "packet")
PACKET_STATES: Final[tuple[str, ...]] = ("flat", "pending", "position")
WAIT_POLL_S: Final[float] = 25.0            # the route's MAX_WAIT_S
HTTP_MARGIN_S: Final[float] = 10.0
# What every operator agent passes (`wait --timeout 240`): with the worst-case overrun
# it still ends inside a 300 s blocking tool call (Claude Code, Codex, Antigravity).
DEFAULT_WAIT_TIMEOUT_S: Final[float] = 240.0
MAX_TRANSIENT_FAILURES: Final[int] = 3
RETRY_PAUSE_S: Final[float] = 2.0
FAST_REPLY_S: Final[float] = 1.0
IDLE_PAUSE_S: Final[float] = 2.0
HTTP_NO_CONTENT: Final[int] = 204
HTTP_NOT_FOUND: Final[int] = 404
MAX_DETAIL_CHARS: Final[int] = 200
HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")
DEMO_REFUSAL_MARKERS: Final[tuple[str, ...]] = (
    "APP-V6-DEMO-403", "POLICY_OPERATOR_DEMO_ONLY", "POLICY_REAL_REFUSED",
    "POLICY_CONTEST_REFUSED", "APP-V6-SESSION-NOT-DEMO")
NO_SESSION_MARKERS: Final[tuple[str, ...]] = ("APP-V6-NO-SESSION",)
# The account is not known yet (no EA poll since the adapter started): not a refusal.
UNKNOWN_ACCOUNT_MARKERS: Final[tuple[str, ...]] = ("POLICY_UNKNOWN_TRADE_MODE",)
CODE_KEYS: Final[tuple[str, ...]] = (
    "code", "error", "policy", "refusal", "codes", "reason", "error_code", "outcome",
    "status", "detail")

KIND_PACKET: Final[str] = "packet"
KIND_EMPTY: Final[str] = "empty"
KIND_NO_SESSION: Final[str] = "no_session"
KIND_NOT_DEMO: Final[str] = "not_demo"
KIND_ERROR: Final[str] = "error"
NEXT_NO_SESSION: Final[str] = "stop the loop and report: the daily session is not active"
NEXT_NOT_DEMO: Final[str] = "refuse and stop: operator agents decide for DEMO accounts only"
NEXT_TIMEOUT: Final[str] = "no packet yet: run wait again"
SKIPPED_EXPIRED: Final[str] = "an expired packet was skipped"


class TransientReply(Exception):
    """A 5xx answer, or an account no EA poll has reported yet: retried."""


@dataclass(frozen=True)
class WaitOutcome:
    kind: str
    packet: Mapping[str, Any] | None = None
    detail: str = ""
    http_status: int | None = None


def wait_request(agent: str | None) -> bytes:
    body: dict[str, object] = {"timeout_s": WAIT_POLL_S}
    if agent is not None:
        body["agent"] = agent
    return json.dumps(body).encode("ascii")


def collect_codes(body: object) -> tuple[str, ...]:
    """Strings under the usual refusal keys (one nested level), for marker matching."""
    if not isinstance(body, Mapping):
        return ()
    found: list[str] = []
    for key in CODE_KEYS:
        value = body.get(key)
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, str):
                found.append(item)
            elif isinstance(item, Mapping):
                found.extend(text for text in (item.get(k) for k in CODE_KEYS)
                             if isinstance(text, str))
    return tuple(found)


def has_marker(found: tuple[str, ...], markers: tuple[str, ...]) -> bool:
    return any(marker in text for text in found for marker in markers)


def demo_refused(found: tuple[str, ...], body: object = None) -> bool:
    """A demo-policy refusal about a known account (an unknown one is only transient).

    When the reply names its `policy`, only a demo-only policy code counts: the
    operator API sends APP-V6-DEMO-403 for every account-policy refusal.
    """
    policy = body.get("policy") if isinstance(body, Mapping) else None
    if isinstance(policy, str):
        return policy in DEMO_POLICY_CODES
    return has_marker(found, DEMO_REFUSAL_MARKERS) and not has_marker(
        found, UNKNOWN_ACCOUNT_MARKERS)


def describe(found: tuple[str, ...]) -> str:
    return clean(": ".join(dict.fromkeys(found)), MAX_DETAIL_CHARS)


def session_gone(body: object) -> bool:
    """The reply says there is no active session (its `session` is null or inactive) and
    none is about to reopen (`session_renewal_due`: the rollover closed it and the
    adapter reopens it when the block ends, so `wait` keeps waiting)."""
    if not isinstance(body, Mapping) or body.get("session_renewal_due") is True:
        return False
    session = body.get("session", {})
    return (session is None or get_path(session, "active") is False
            or body.get("session_active") is False)


def packet_problems(packet: Mapping[str, Any]) -> list[str]:
    checks = (
        (packet.get("schema_version") == PACKET_SCHEMA, "schema_version"),
        (isinstance(packet.get("cycle_id"), str) and bool(packet.get("cycle_id")), "cycle_id"),
        (isinstance(packet.get("packet_hash"), str)
         and HASH_PATTERN.fullmatch(packet["packet_hash"]) is not None, "packet_hash"),
        (type(packet.get("expires_at_epoch")) is int, "expires_at_epoch"),
        # A packet may carry no suggestion: the agent can design its own entry.
        (isinstance(packet.get("candidates"), list), "candidates"),
        (isinstance(packet.get("limits"), Mapping), "limits"),
        (packet.get("state") in PACKET_STATES, "state"),
        (packet.get("state") != "position" or isinstance(packet.get("position"), Mapping),
         "position"),
        (packet.get("state") != "pending" or isinstance(packet.get("pending_order"), Mapping),
         "pending_order"),
    )
    return [name for ok, name in checks if not ok]


def packet_outcome(packet: object) -> WaitOutcome:
    if not isinstance(packet, Mapping):
        return WaitOutcome(KIND_ERROR, detail="the packet is not a JSON object")
    problems = packet_problems(packet)
    if problems:
        return WaitOutcome(KIND_ERROR, detail="malformed packet: " + ",".join(problems))
    trade_mode = get_path(packet, "account", "trade_mode")
    if trade_mode != DEMO_TRADE_MODE:
        return WaitOutcome(KIND_NOT_DEMO, detail=f"packet trade_mode is {clean(trade_mode)}")
    return WaitOutcome(KIND_PACKET, packet=packet)


def packet_in(body: Mapping[str, Any]) -> object:
    for key in PACKET_KEYS:
        if key in body:
            return body[key]
    return body if body.get("schema_version") == PACKET_SCHEMA else None


def classify_wait_reply(reply: HttpReply) -> WaitOutcome:
    body = reply.json() if reply.body else None
    found = collect_codes(body)
    if has_marker(found, UNKNOWN_ACCOUNT_MARKERS):
        raise TransientReply(describe(found))
    if demo_refused(found, body):
        return WaitOutcome(KIND_NOT_DEMO, detail=describe(found), http_status=reply.status)
    if has_marker(found, NO_SESSION_MARKERS) or session_gone(body):
        return WaitOutcome(KIND_NO_SESSION, detail=describe(found) or "no active session",
                           http_status=reply.status)
    if reply.server_error:
        raise TransientReply(f"HTTP {reply.status}")
    if not reply.ok:
        return WaitOutcome(KIND_ERROR, detail=describe(found) or "request refused",
                           http_status=reply.status)
    if reply.status == HTTP_NO_CONTENT or not reply.body:
        return WaitOutcome(KIND_EMPTY)
    if not isinstance(body, Mapping):
        return WaitOutcome(KIND_ERROR, detail="non-JSON response", http_status=reply.status)
    packet = packet_in(body)
    return WaitOutcome(KIND_EMPTY) if packet is None else packet_outcome(packet)


def guard_outcome(reply: HttpReply) -> WaitOutcome | None:
    """What GET /v6/status says about continuing; None to go on."""
    if reply.server_error:
        raise TransientReply(f"HTTP {reply.status}")
    body = reply.json()
    if reply.status == HTTP_NOT_FOUND:
        return WaitOutcome(KIND_ERROR, detail="V6 is disabled on the adapter", http_status=404)
    if not reply.ok or not isinstance(body, Mapping):
        return WaitOutcome(KIND_ERROR, detail="GET /v6/status failed", http_status=reply.status)
    trade_mode = body.get("trade_mode")
    if trade_mode is not None and trade_mode != DEMO_TRADE_MODE:
        return WaitOutcome(KIND_NOT_DEMO, detail=f"the EA reports trade_mode {clean(trade_mode)}")
    if body.get("backend") != OPERATOR_BACKEND:
        return WaitOutcome(KIND_ERROR, detail="V6_BACKEND is not operator")
    if session_gone(body):
        return WaitOutcome(KIND_NO_SESSION, detail="no active daily session")
    return None


def status_brief(reply: HttpReply) -> dict[str, object]:
    body = reply.json()
    return {"runtime_status": get_path(body, "runtime", "status"),
            "halted": get_path(body, "halt_file_present"),
            "session_id": get_path(body, "session", "session_id"),
            "armed": get_path(body, "session", "armed")}


@dataclass(frozen=True)
class WaitPlan:
    started: float
    deadline: float
    out_path: Path
    request: bytes


def _pause(ctx: Context, plan: WaitPlan, seconds: float) -> None:
    ctx.sleep(max(0.0, min(seconds, plan.deadline - ctx.clock())))


def poll_once(ctx: Context, plan: WaitPlan) -> tuple[WaitOutcome, dict[str, object]]:
    """One guard read and one long-poll; raises TransportError or TransientReply."""
    status = ctx.client.send("GET", STATUS_PATH, auth=False)
    guard = guard_outcome(status)
    brief = status_brief(status)
    if guard is not None:
        return guard, brief
    sent_at = ctx.clock()
    reply = ctx.client.send("POST", WAIT_PATH, plan.request, timeout=WAIT_POLL_S + HTTP_MARGIN_S)
    outcome = classify_wait_reply(reply)
    if outcome.kind == KIND_PACKET and outcome.packet is not None \
            and outcome.packet["expires_at_epoch"] <= ctx.clock():
        outcome = WaitOutcome(KIND_EMPTY, detail=SKIPPED_EXPIRED)
    if outcome.kind == KIND_EMPTY and ctx.clock() - sent_at < FAST_REPLY_S:
        _pause(ctx, plan, IDLE_PAUSE_S)
    return outcome, brief


def run_wait(ctx: Context, *, timeout_s: float, out_path: Path,
             agent: str | None = None) -> int:
    started = ctx.clock()
    plan = WaitPlan(started=started, deadline=started + timeout_s, out_path=out_path,
                    request=wait_request(agent))
    archive(out_path)
    failures = skipped = 0
    brief: dict[str, object] = {}
    while ctx.clock() < plan.deadline:
        try:
            outcome, brief = poll_once(ctx, plan)
        except (TransportError, TransientReply) as exc:
            failures += 1
            if failures >= MAX_TRANSIENT_FAILURES:
                label = "unreachable" if isinstance(exc, TransportError) else "unavailable"
                emit_error(ctx, label, str(exc), base_url=ctx.client.base_url)
                return EXIT_ERROR
            _pause(ctx, plan, RETRY_PAUSE_S)
            continue
        failures = 0
        skipped += outcome.detail == SKIPPED_EXPIRED
        if outcome.kind != KIND_EMPTY:
            return finish(ctx, plan, outcome)
    emit(ctx.stdout, {"outcome": "timeout", "waited_s": round(ctx.clock() - started),
                      "skipped_expired": skipped, **brief, "next": NEXT_TIMEOUT})
    return EXIT_TIMEOUT


def finish(ctx: Context, plan: WaitPlan, outcome: WaitOutcome) -> int:
    if outcome.kind == KIND_PACKET and outcome.packet is not None:
        try:
            write_json_atomic(plan.out_path, outcome.packet)
        except (OSError, ValueError) as exc:
            emit_error(ctx, "write_failed", f"{type(exc).__name__}: {plan.out_path}")
            return EXIT_ERROR
        ctx.stdout.write(render_packet(outcome.packet, now=ctx.clock(), path=plan.out_path))
        return EXIT_OK
    if outcome.kind == KIND_NO_SESSION:
        emit(ctx.stdout, {"outcome": KIND_NO_SESSION, "detail": outcome.detail,
                          "next": NEXT_NO_SESSION})
        return EXIT_NO_SESSION
    if outcome.kind == KIND_NOT_DEMO:
        emit(ctx.stdout, {"outcome": KIND_NOT_DEMO, "detail": outcome.detail,
                          "next": NEXT_NOT_DEMO})
        return EXIT_NOT_DEMO
    emit_error(ctx, "wait_failed", outcome.detail, http_status=outcome.http_status)
    return EXIT_ERROR
