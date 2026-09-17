"""
`preflight`: is everything in place for an operator trading session?

Prints one JSON object whatever happens. Exit codes: 0 ready, 1 not ready,
3 adapter unreachable, 5 the account is refused as not DEMO (the agent must
refuse and stop). Reads GET /v6/status (no token), GET /v6/control/session
(token, so a wrong token is caught before the session starts) and, with the
operator backend, GET /v6/operator/status for the adapter's own account-policy
verdict (trade mode, demo server name, login allow-list).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final

from .context import (
    DEMO_POLICY_CODES, DEMO_TRADE_MODE, EXECUTE_MODE, EXIT_ERROR, EXIT_NOT_DEMO, EXIT_OK,
    EXIT_UNREACHABLE, OPERATOR_BACKEND, SHADOW_MODE, Context, emit, get_path,
)
from .summaries import session_fields
from .transport import HttpReply, TransportError

STATUS_PATH: Final[str] = "/v6/status"
SESSION_PATH: Final[str] = "/v6/control/session"
OPERATOR_STATUS_PATH: Final[str] = "/v6/operator/status"
POLICY_OK: Final[str] = "POLICY_OK"
POLICY_UNKNOWN_TRADE_MODE: Final[str] = "POLICY_UNKNOWN_TRADE_MODE"
RUNTIME_RUNNING: Final[str] = "RUNNING"
REQUIRED_SIGNING: Final[str] = "required"
HTTP_UNAUTHORIZED: Final[int] = 401
HTTP_NOT_FOUND: Final[int] = 404
HTTP_UNAVAILABLE: Final[int] = 503
TOKEN_ENV_NAME: Final[str] = "V6_OPERATOR_TOKEN"

P_UNREACHABLE: Final[str] = "ADAPTER_UNREACHABLE"
P_STATUS: Final[str] = "STATUS_UNAVAILABLE"
P_SESSION_STATUS: Final[str] = "SESSION_STATUS_UNAVAILABLE"
P_DISABLED: Final[str] = "V6_DISABLED"
P_BACKEND: Final[str] = "BACKEND_NOT_OPERATOR"
P_MODE: Final[str] = "MODE_NOT_EXECUTE"
P_TOKEN_ADAPTER: Final[str] = "OPERATOR_TOKEN_NOT_CONFIGURED"
P_TOKEN_MISSING: Final[str] = "TOKEN_MISSING"
P_TOKEN_REJECTED: Final[str] = "TOKEN_REJECTED"
P_SIGNING: Final[str] = "EA_SIGNING_NOT_REQUIRED"
P_AGENT: Final[str] = "AGENT_NOT_ALLOWED"
P_EA_NOT_SEEN: Final[str] = "EA_NOT_SEEN"
P_EA_STALE: Final[str] = "EA_STALE"
P_RUNTIME: Final[str] = "RUNTIME_NOT_RUNNING"
P_HALTED: Final[str] = "HALTED"
P_BREAKER: Final[str] = "BREAKER_TRIPPED"
P_TRADE_MODE_UNKNOWN: Final[str] = "TRADE_MODE_UNKNOWN"
P_NOT_DEMO: Final[str] = "NOT_DEMO"
P_WARMUP: Final[str] = "WARMUP"
P_OPERATOR_API: Final[str] = "OPERATOR_API_UNAVAILABLE"
P_ACCOUNT_POLICY: Final[str] = "ACCOUNT_POLICY"

RUNTIME_PROBLEMS: Final[Mapping[str, str]] = MappingProxyType({
    "HALTED": P_HALTED, "BREAKER": P_BREAKER, "WAITING_EA": P_EA_NOT_SEEN,
    "STALE": P_EA_STALE, "DISABLED": P_DISABLED})
HINTS: Final[Mapping[str, str]] = MappingProxyType({
    P_UNREACHABLE: "start the adapter from adapter/: ../.venv/Scripts/python.exe -m uvicorn "
                   "app.main:app --host 127.0.0.1 --port 8765 --workers 1",
    P_STATUS: "GET /v6/status failed; read the adapter log",
    P_SESSION_STATUS: "GET /v6/control/session failed; read the adapter log",
    P_DISABLED: "set V6_ENABLED=true in adapter/.env and restart the adapter",
    P_BACKEND: "set V6_BACKEND=operator in adapter/.env and restart the adapter",
    P_MODE: "set V6_MODE=execute (DEMO only) and restart; --allow-shadow accepts shadow "
            "for a rehearsal",
    P_TOKEN_ADAPTER: "set V6_OPERATOR_TOKEN (32+ random characters) in adapter/.env and "
                     "restart the adapter",
    P_TOKEN_MISSING: "no V6_OPERATOR_TOKEN in the environment or adapter/.env",
    P_TOKEN_REJECTED: "this token differs from the adapter's V6_OPERATOR_TOKEN",
    P_SIGNING: "execute mode must require EA signatures: check V6_EA_HMAC_KEY",
    P_AGENT: "this agent is not in V6_OPERATOR_AGENTS",
    P_EA_NOT_SEEN: "attach QlipV6_XAUUSD to an XAUUSD chart; allow http://127.0.0.1:8765 in "
                   "Tools > Options > Expert Advisors > WebRequest",
    P_EA_STALE: "the EA stopped polling: check MT5 (connection, AutoTrading, Experts log)",
    P_RUNTIME: "the V6 runtime is not RUNNING; see GET /v6/status",
    P_HALTED: "a kill switch is active; tell the user, never resume on your own",
    P_BREAKER: "a loss breaker is tripped; only the user may reset it",
    P_TRADE_MODE_UNKNOWN: "no EA poll yet, so the account type is unknown",
    P_NOT_DEMO: "REFUSE: operator agents decide for DEMO accounts only; do not start",
    P_WARMUP: "history backfill is incomplete; cycles hold with APP-V6-WARMUP",
    P_OPERATOR_API: "GET /v6/operator/status is 404: restart the adapter with "
                    "V6_BACKEND=operator (the operator queue is not running)",
    P_ACCOUNT_POLICY: "the adapter's account policy refuses this account (see "
                      "account_policy, e.g. V6_ALLOWED_LOGINS)",
})
NEXT_REFUSE: Final[str] = "refuse and stop: this is not a DEMO account"
NEXT_FIX: Final[str] = "fix the problems listed, then run preflight again"
NEXT_START: Final[str] = "run: session start"
NEXT_WAIT: Final[str] = "session already active and armed: run wait"
NEXT_PENDING: Final[str] = "a packet is pending now: run wait at once"
NO_OPERATOR_FIELDS: Final[Mapping[str, object]] = MappingProxyType(
    {"operator_api": None, "account_policy": None, "pending_cycle": None})


@dataclass(frozen=True)
class OperatorProbe:
    problems: tuple[str, ...] = ()
    extra: Mapping[str, object] = field(default_factory=lambda: dict(NO_OPERATOR_FIELDS))


@dataclass(frozen=True)
class SessionProbe:
    document: Mapping[str, Any] | None = None
    problems: tuple[str, ...] = ()
    token_accepted: bool | None = None
    extra: Mapping[str, object] = field(default_factory=dict)


def run_preflight(ctx: Context, *, agent: str | None, allow_shadow: bool) -> int:
    report = collect(ctx, agent=agent, allow_shadow=allow_shadow)
    emit(ctx.stdout, report)
    return exit_code_for(report)


def exit_code_for(report: Mapping[str, Any]) -> int:
    problems = report.get("problems") or ()
    if not problems:
        return EXIT_OK
    if P_NOT_DEMO in problems:
        return EXIT_NOT_DEMO
    return EXIT_UNREACHABLE if report.get("adapter_reachable") is False else EXIT_ERROR


def collect(ctx: Context, *, agent: str | None, allow_shadow: bool) -> dict[str, object]:
    base = {"checked_at_epoch": int(ctx.clock()), "agent": agent, "allow_shadow": allow_shadow}
    try:
        reply = ctx.client.send("GET", STATUS_PATH, auth=False)
    except TransportError as exc:
        return finalize({**base, **status_fields({}), **NO_OPERATOR_FIELDS,
                         "adapter_reachable": False, "transport_error": str(exc),
                         "session": None, "token_accepted": None}, [P_UNREACHABLE])
    status, problems = read_status(reply)
    problems += status_problems(status, agent=agent, allow_shadow=allow_shadow)
    probe = probe_session(ctx)
    operator = probe_operator(ctx, status)
    source = status if probe.document is None else probe.document
    document = {**base, **status_fields(status), "adapter_reachable": True,
                "token_accepted": probe.token_accepted, **probe.extra, **operator.extra,
                "session": session_view(source.get("session"))}
    return finalize(document, [*problems, *probe.problems, *operator.problems])


def read_status(reply: HttpReply) -> tuple[Mapping[str, Any], list[str]]:
    if reply.status == HTTP_NOT_FOUND:
        return {}, [P_DISABLED]
    body = reply.json()
    if not reply.ok or not isinstance(body, Mapping):
        return {}, [P_STATUS]
    return body, []


def status_problems(status: Mapping[str, Any], *, agent: str | None,
                    allow_shadow: bool) -> list[str]:
    if not status:
        return []
    mode = status.get("mode")
    runtime = str(get_path(status, "runtime", "status"))
    trade_mode = status.get("trade_mode")
    modes = (EXECUTE_MODE, SHADOW_MODE) if allow_shadow else (EXECUTE_MODE,)
    checks = (
        (status.get("enabled") is True, P_DISABLED),
        (status.get("backend") == OPERATOR_BACKEND, P_BACKEND),
        (mode in modes, P_MODE),
        (status.get("operator_ready") is True, P_TOKEN_ADAPTER),
        (mode != EXECUTE_MODE or status.get("ea_signing") == REQUIRED_SIGNING, P_SIGNING),
        (agent is None or agent in (status.get("operator_agents") or ()), P_AGENT),
        (runtime == RUNTIME_RUNNING, RUNTIME_PROBLEMS.get(runtime, P_RUNTIME)),
        (status.get("halt_file_present") is not True, P_HALTED),
        (not get_path(status, "runtime", "breakers"), P_BREAKER),
        (trade_mode is not None, P_TRADE_MODE_UNKNOWN),
        (trade_mode in (None, DEMO_TRADE_MODE), P_NOT_DEMO),
        (status.get("warm") is True, P_WARMUP),
    )
    return [code for ok, code in checks if not ok]


def probe_session(ctx: Context) -> SessionProbe:
    """GET /v6/control/session with the token: proves the token and reads the session."""
    if ctx.client.token is None:
        return SessionProbe(problems=(P_TOKEN_MISSING,))
    try:
        reply = ctx.client.send("GET", SESSION_PATH)
    except TransportError:
        return SessionProbe(problems=(P_UNREACHABLE,))
    body = reply.json()
    detail = str(get_path(body, "detail") or "")
    if reply.status == HTTP_UNAUTHORIZED:
        return SessionProbe(problems=(P_TOKEN_REJECTED,), token_accepted=False)
    if reply.status == HTTP_NOT_FOUND:
        return SessionProbe(problems=(P_DISABLED,))
    if reply.status == HTTP_UNAVAILABLE and TOKEN_ENV_NAME in detail:
        return SessionProbe(problems=(P_TOKEN_ADAPTER,))
    if not reply.ok or not isinstance(body, Mapping):
        return SessionProbe(problems=(P_SESSION_STATUS,))
    extra = {"pending_command": get_path(body, "command", "command"),
             "intents_today": get_path(body, "summary", "intents"),
             "cycles_today": get_path(body, "summary", "cycles")}
    return SessionProbe(document=body, token_accepted=True, extra=extra)


def policy_problems(policy: object) -> tuple[str, ...]:
    if policy in (None, POLICY_OK):
        return ()
    if policy in DEMO_POLICY_CODES:
        return (P_NOT_DEMO,)
    return (P_TRADE_MODE_UNKNOWN,) if policy == POLICY_UNKNOWN_TRADE_MODE else (P_ACCOUNT_POLICY,)


def probe_operator(ctx: Context, status: Mapping[str, Any]) -> OperatorProbe:
    """GET /v6/operator/status: the adapter's own account-policy verdict and the queue."""
    if ctx.client.token is None or status.get("backend") != OPERATOR_BACKEND:
        return OperatorProbe()
    try:
        reply = ctx.client.send("GET", OPERATOR_STATUS_PATH)
    except TransportError:
        return OperatorProbe(problems=(P_UNREACHABLE,))
    if reply.status == HTTP_NOT_FOUND:
        return OperatorProbe(problems=(P_OPERATOR_API,),
                             extra={**NO_OPERATOR_FIELDS, "operator_api": False})
    body = reply.json()
    if not reply.ok or not isinstance(body, Mapping):
        return OperatorProbe()        # 401 and 503 are reported by the session probe
    policy = body.get("account_policy")
    extra = {"operator_api": True, "account_policy": policy,
             "pending_cycle": get_path(body, "pending", "cycle_id")}
    return OperatorProbe(problems=policy_problems(policy), extra=extra)


def status_fields(status: Mapping[str, Any]) -> dict[str, object]:
    last_cycle = status.get("last_cycle")
    return {
        "v6_enabled": status.get("enabled"), "backend": status.get("backend"),
        "mode": status.get("mode"), "operator_agents": status.get("operator_agents"),
        "operator_ready": status.get("operator_ready"), "ea_signing": status.get("ea_signing"),
        "runtime_status": get_path(status, "runtime", "status"),
        "ea_last_seen_age_s": status.get("ea_last_seen_age_s"),
        "trade_mode": status.get("trade_mode"),
        "server": status.get("server") or get_path(status, "account", "server"),
        "warm": status.get("warm"), "halted": status.get("halt_file_present"),
        "breakers": get_path(status, "runtime", "breakers"),
        "last_snapshot_age_s": get_path(status, "last_snapshot", "age_s"),
        "last_cycle": None if not isinstance(last_cycle, Mapping) else {
            key: last_cycle.get(key) for key in ("cycle_id", "status", "hold_reason")},
    }


def session_view(session: Any) -> dict[str, object] | None:
    if not isinstance(session, Mapping):
        return None
    return {**session_fields(session), "trading_day": session.get("trading_day"),
            "backend": session.get("backend"), "mode": session.get("mode")}


def next_step(problems: list[str], document: Mapping[str, object]) -> str:
    session = document.get("session")
    if P_NOT_DEMO in problems:
        return NEXT_REFUSE
    if problems:
        return NEXT_FIX
    if not isinstance(session, Mapping):
        return NEXT_START
    if document.get("pending_cycle") is not None:
        return NEXT_PENDING
    return NEXT_WAIT if session.get("armed") is True else NEXT_START


def finalize(document: Mapping[str, object], problems: list[str]) -> dict[str, object]:
    unique = list(dict.fromkeys(problems))
    return {**document, "ready": not unique, "problems": unique,
            "hints": {code: HINTS[code] for code in unique},
            "next": next_step(unique, document)}
