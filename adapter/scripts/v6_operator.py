#!/usr/bin/env python3
"""
V6 operator CLI (standard library only).

    python adapter/scripts/v6_operator.py session start
    python adapter/scripts/v6_operator.py session stop [--reason done_for_today]
    python adapter/scripts/v6_operator.py session status
    python adapter/scripts/v6_operator.py halt [--reason R]
    python adapter/scripts/v6_operator.py resume [--reason R]
    python adapter/scripts/v6_operator.py breaker-reset --scope daily --period-key 2026-09-16
    python adapter/scripts/v6_operator.py status

The operator token is read from V6_OPERATOR_TOKEN in the environment, or from
the adapter's .env file when the environment does not set it. It is never a
command-line argument, never printed, only sent to a loopback adapter, and a
redirect is never followed. `status` needs no token.

Output: one JSON document on stdout. Failures: one JSON document on stderr.
Exit codes: 0 ok, 1 the adapter answered with an error, 2 usage or
configuration problem, 3 the adapter could not be reached.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, TextIO

TOKEN_ENV: Final[str] = "V6_OPERATOR_TOKEN"
BASE_URL_ENV: Final[str] = "V6_ADAPTER_URL"
DEFAULT_BASE_URL: Final[str] = "http://127.0.0.1:8765"
DEFAULT_ENV_FILE: Final[Path] = Path(__file__).resolve().parents[1] / ".env"
DEFAULT_TIMEOUT_S: Final[float] = 10.0
MAX_RESPONSE_BYTES: Final[int] = 1_000_000
REASON_MAX_CHARS: Final[int] = 64
LOOPBACK_NAMES: Final[frozenset[str]] = frozenset({"localhost"})
EXIT_OK: Final[int] = 0
EXIT_HTTP_ERROR: Final[int] = 1
EXIT_USAGE: Final[int] = 2
EXIT_UNREACHABLE: Final[int] = 3
HTTP_OK_MIN: Final[int] = 200
HTTP_OK_MAX: Final[int] = 299
QUOTES: Final[str] = "\"'"
BREAKER_SCOPES: Final[tuple[str, ...]] = ("daily", "weekly", "monthly")
# The adapter's period keys: YYYY-MM-DD (daily), YYYY-Www (weekly), YYYY-MM (monthly);
# the adapter also checks that the key's form matches the scope.
PERIOD_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[0-9]{4}-([0-9]{2}-[0-9]{2}|W[0-9]{2}|[0-9]{2})")
INLINE_COMMENT: Final[str] = " #"


class UsageError(Exception):
    """Bad arguments or configuration; nothing was sent."""


class TransportError(Exception):
    """The adapter could not be reached; `str()` names the failure class only."""


@dataclass(frozen=True)
class HttpReply:
    status: int
    body: bytes


@dataclass(frozen=True)
class Call:
    method: str
    path: str
    body: Mapping[str, object] | None
    needs_token: bool
    summarise: Callable[[Mapping[str, Any]], dict[str, object]]


Transport = Callable[[str, str, bytes | None, Mapping[str, str], float], HttpReply]


# --- transport -----------------------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the bearer token elsewhere; surface it as an error."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def urllib_transport(method: str, url: str, body: bytes | None, headers: Mapping[str, str],
                     timeout: float) -> HttpReply:
    # An empty ProxyHandler ignores HTTP(S)_PROXY: the token stays on this machine.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with opener.open(request, timeout=timeout) as response:
            return HttpReply(response.status, response.read(MAX_RESPONSE_BYTES))
    except urllib.error.HTTPError as exc:
        payload = exc.read(MAX_RESPONSE_BYTES) if exc.fp is not None else b""
        return HttpReply(exc.code, payload)
    except (urllib.error.URLError, OSError) as exc:
        raise TransportError(type(exc).__name__) from exc


# --- configuration -------------------------------------------------------------
def _is_loopback(host: str) -> bool:
    if host.lower() in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def resolve_base_url(raw: str) -> str:
    """Only plain http to this machine: the token must never leave it."""
    parts = urllib.parse.urlsplit(raw.strip())
    host = parts.hostname or ""
    if parts.scheme != "http" or not _is_loopback(host):
        raise UsageError("base URL must be http:// on a loopback host")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise UsageError("base URL must not carry credentials, a query or a fragment")
    if parts.path not in ("", "/"):
        raise UsageError("base URL must not carry a path")
    return f"http://{parts.netloc}"


def _env_value(raw: str) -> str:
    """dotenv-style value: quotes are removed; an unquoted value ends at ` #`."""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in QUOTES:
        return value[1:-1]
    return value.split(INLINE_COMMENT, 1)[0].strip()


def _token_from_env_file(env_file: Path) -> str | None:
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, IsADirectoryError, PermissionError, UnicodeDecodeError):
        return None
    for line in lines:
        key, sep, value = line.strip().partition("=")
        if sep and key.strip().removeprefix("export ").strip() == TOKEN_ENV:
            return _env_value(value) or None
    return None


def load_token(environ: Mapping[str, str], env_file: Path) -> str | None:
    token = environ.get(TOKEN_ENV, "").strip()
    return token or _token_from_env_file(env_file)


def _reason(raw: str) -> str:
    if not 0 < len(raw) <= REASON_MAX_CHARS:
        raise argparse.ArgumentTypeError(f"reason must be 1-{REASON_MAX_CHARS} characters")
    return raw


def _period_key(raw: str) -> str:
    if not PERIOD_KEY_PATTERN.fullmatch(raw):
        raise argparse.ArgumentTypeError("period key must be YYYY-MM-DD, YYYY-Www or YYYY-MM")
    return raw


# --- summaries -----------------------------------------------------------------
def _get(document: Mapping[str, Any] | None, *keys: str) -> Any:
    value: Any = document
    for key in keys:
        value = value.get(key) if isinstance(value, Mapping) else None
    return value


def _day(summary: Mapping[str, Any] | None) -> dict[str, object]:
    return {
        "trading_day": _get(summary, "trading_day"), "cycles": _get(summary, "cycles"),
        "shadow_intents": _get(summary, "shadow_intents"),
        "hold_reasons": _get(summary, "hold_reasons"),
        "open_v6_positions": _get(summary, "exposure", "open_v6_positions"),
        "pending_v6_orders": _get(summary, "exposure", "pending_v6_orders"),
        "floating_pnl_v6": _get(summary, "exposure", "floating_pnl_v6"),
    }


def summarise_start(body: Mapping[str, Any]) -> dict[str, object]:
    return {"created": body.get("created"), "trading_day": body.get("trading_day"),
            "session_id": _get(body, "session", "session_id"),
            "backend": _get(body, "session", "backend"), "mode": _get(body, "session", "mode"),
            "refusal": body.get("refusal"), "detail": body.get("detail")}


def summarise_stop(body: Mapping[str, Any]) -> dict[str, object]:
    return {"stopped": body.get("stopped"), "session_id": _get(body, "session", "session_id"),
            "command": _get(body, "command", "command"), **_day(body.get("summary"))}


def summarise_session(body: Mapping[str, Any]) -> dict[str, object]:
    session = body.get("session")
    return {"active": session is not None, "session_id": _get(session, "session_id"),
            "backend": body.get("backend"), "mode": body.get("mode"),
            "halted": body.get("halted"), "pending_command": _get(body, "command", "command"),
            **_day(body.get("summary"))}


def summarise_runtime(body: Mapping[str, Any]) -> dict[str, object]:
    keys = ("enabled", "mode", "backend", "trade_mode", "ea_last_seen_age_s", "warm",
            "halt_file_present", "server_time_epoch")
    return {**{key: body.get(key) for key in keys},
            "last_snapshot": _get(body, "last_snapshot", "bar_open_epoch")}


def passthrough(body: Mapping[str, Any]) -> dict[str, object]:
    return dict(body)


def build_call(args: argparse.Namespace) -> Call:
    if args.command == "status":
        return Call("GET", "/v6/status", None, False, summarise_runtime)
    if args.command == "breaker-reset":
        body = {"scope": args.scope, "period_key": args.period_key, "reason": args.reason}
        return Call("POST", "/v6/control/breaker/reset", body, True, passthrough)
    if args.command in ("halt", "resume"):
        return Call("POST", f"/v6/control/{args.command}", {"reason": args.reason}, True,
                    passthrough)
    if args.action == "status":
        return Call("GET", "/v6/control/session", None, True, summarise_session)
    if args.action == "start":
        return Call("POST", "/v6/control/session", {"action": "start"}, True, summarise_start)
    return Call("POST", "/v6/control/session", {"action": "stop", "reason": args.reason},
                True, summarise_stop)


# --- entry point -----------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="v6_operator", description="Qlip V6 operator CLI")
    parser.add_argument("--base-url", default=None, help=f"default {DEFAULT_BASE_URL}")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE,
                        help=f"fallback for {TOKEN_ENV}")
    commands = parser.add_subparsers(dest="command", required=True)
    session = commands.add_parser("session", help="daily session start/stop/status")
    session.add_argument("action", choices=("start", "stop", "status"))
    session.add_argument("--reason", type=_reason, default="operator_stop")
    for name, default in (("halt", "operator_halt"), ("resume", "operator_resume")):
        command = commands.add_parser(name, help=f"{name} V6")
        command.add_argument("--reason", type=_reason, default=default)
    reset = commands.add_parser("breaker-reset",
                                help="clear one tripped breaker (refused while still in breach)")
    reset.add_argument("--scope", required=True, choices=BREAKER_SCOPES)
    reset.add_argument("--period-key", required=True, type=_period_key)
    reset.add_argument("--reason", type=_reason, default="operator_reset")
    commands.add_parser("status", help="adapter and EA status (no token needed)")
    return parser


def _headers(token: str | None, has_body: bool) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if has_body:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _decode(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8")) if body else {}
    except (UnicodeDecodeError, ValueError):
        return None


def _emit(stream: TextIO, document: Mapping[str, object]) -> None:
    stream.write(json.dumps(document, indent=2, sort_keys=True) + "\n")


def _error_document(reply: HttpReply) -> dict[str, object]:
    decoded = _decode(reply.body)
    if not isinstance(decoded, Mapping):
        return {"error": reply.status, "detail": "non-JSON response"}
    fields = {k: decoded[k] for k in ("detail", "refusal", "code", "halted") if k in decoded}
    return {"error": reply.status, **fields}


def run(call: Call, *, base_url: str, token: str | None, timeout: float,
        transport: Transport, stdout: TextIO, stderr: TextIO) -> int:
    body = None if call.body is None else json.dumps(call.body).encode("utf-8")
    try:
        reply = transport(call.method, base_url + call.path, body,
                          _headers(token if call.needs_token else None, body is not None),
                          timeout)
    except TransportError as exc:
        _emit(stderr, {"error": "unreachable", "detail": str(exc), "base_url": base_url})
        return EXIT_UNREACHABLE
    if not HTTP_OK_MIN <= reply.status <= HTTP_OK_MAX:
        _emit(stderr, _error_document(reply))
        return EXIT_HTTP_ERROR
    decoded = _decode(reply.body)
    if not isinstance(decoded, Mapping):
        _emit(stderr, {"error": reply.status, "detail": "non-JSON response"})
        return EXIT_HTTP_ERROR
    _emit(stdout, call.summarise(decoded))
    return EXIT_OK


def main(argv: Sequence[str] | None = None, *, environ: Mapping[str, str] | None = None,
         transport: Transport = urllib_transport, stdout: TextIO | None = None,
         stderr: TextIO | None = None) -> int:
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    env = os.environ if environ is None else environ
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse already printed usage or help
        return EXIT_OK if exc.code in (0, None) else EXIT_USAGE
    call = build_call(args)
    try:
        base_url = resolve_base_url(args.base_url or env.get(BASE_URL_ENV) or DEFAULT_BASE_URL)
        token = load_token(env, args.env_file) if call.needs_token else None
        if call.needs_token and token is None:
            raise UsageError(f"{TOKEN_ENV} is not set (environment or {args.env_file.name})")
    except UsageError as exc:
        _emit(err, {"error": "usage", "detail": str(exc)})
        return EXIT_USAGE
    return run(call, base_url=base_url, token=token, timeout=args.timeout,
               transport=transport, stdout=out, stderr=err)


if __name__ == "__main__":
    sys.exit(main())
