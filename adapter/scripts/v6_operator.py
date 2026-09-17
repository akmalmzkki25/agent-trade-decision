#!/usr/bin/env python3
"""
V6 operator CLI (standard library only). Run from adapter/ or give full paths.

    python scripts/v6_operator.py preflight [--agent AGENT] [--allow-shadow]
    python scripts/v6_operator.py session start|stop|status [--reason R]
    python scripts/v6_operator.py wait [--agent AGENT] [--timeout 240] [--out F.json]
    python scripts/v6_operator.py template [--packet P] [--out .v6_operator/decision.json] [--force]
    python scripts/v6_operator.py submit --agent AGENT [--file F|-] [--packet P]
    python scripts/v6_operator.py halt|resume [--reason R]
    python scripts/v6_operator.py breaker-reset --scope daily --period-key 2026-09-16
    python scripts/v6_operator.py status

AGENT names the operator agent: claude_code (Claude Code), codex (Codex) or
antigravity (Antigravity). All three run the same loop (AGENTS.md and
.agents/skills/v6-trading/SKILL.md), on DEMO accounts only.

The operator token is read from V6_OPERATOR_TOKEN in the environment, or from
the adapter's .env file when the environment does not set it. It is never a
command-line argument, never printed, only sent to a loopback adapter, and a
redirect is never followed. `status`, `preflight` without a token and
`template` need no token. Working files live in adapter/.v6_operator/.

Output: one JSON document on stdout (`wait` prints a text summary when a packet
arrives). Failures: one JSON document on stderr.
Exit codes: 0 ok, 1 the adapter answered with an error / not ready / refused,
2 usage or configuration problem, 3 the adapter could not be reached (`wait`:
no packet before --timeout; an unreachable adapter is exit 1 there), 4 no
active daily session (`wait`, `submit`), 5 the account is not DEMO (`preflight`,
`wait`, `submit`): the operator must refuse and stop.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Final, TextIO

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:  # `python -I` leaves the script's directory off sys.path
    sys.path.insert(0, _SCRIPTS_DIR)

from v6ops import decisions, preflight, waiting  # noqa: E402
from v6ops.context import (  # noqa: E402,F401  (exit codes re-exported)
    AGENTS, EXIT_ERROR, EXIT_NO_SESSION, EXIT_NOT_DEMO, EXIT_OK, EXIT_TIMEOUT, EXIT_UNREACHABLE,
    EXIT_USAGE, Context, emit, emit_error,
)
from v6ops.envfile import DEFAULT_ENV_FILE, lookup  # noqa: E402
from v6ops.files import DECISION_FILE, PACKET_FILE, json_path, json_path_or_stdin  # noqa: E402
from v6ops.summaries import (  # noqa: E402
    passthrough, summarise_runtime, summarise_session, summarise_start, summarise_stop,
)
from v6ops.transport import (  # noqa: E402,F401  (re-exported for callers and tests)
    BASE_URL_ENV, DEFAULT_BASE_URL, DEFAULT_TIMEOUT_S, TOKEN_ENV, Client, HttpReply, Transport,
    TransportError, UsageError, error_document, resolve_base_url, urllib_transport,
)

EXIT_HTTP_ERROR: Final[int] = EXIT_ERROR
REASON_MAX_CHARS: Final[int] = 64
BREAKER_SCOPES: Final[tuple[str, ...]] = ("daily", "weekly", "monthly")
# The adapter's period keys: YYYY-MM-DD (daily), YYYY-Www (weekly), YYYY-MM (monthly);
# the adapter also checks that the key's form matches the scope.
PERIOD_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[0-9]{4}-([0-9]{2}-[0-9]{2}|W[0-9]{2}|[0-9]{2})")
MIN_WAIT_TIMEOUT_S: Final[float] = 1.0
MAX_WAIT_TIMEOUT_S: Final[float] = 3600.0
# Commands that must have the token before anything is sent.
TOKEN_COMMANDS: Final[frozenset[str]] = frozenset(
    {"session", "halt", "resume", "breaker-reset", "wait", "submit"})
LOCAL_COMMANDS: Final[frozenset[str]] = frozenset({"template"})


@dataclass(frozen=True)
class Call:
    method: str
    path: str
    body: Mapping[str, object] | None
    needs_token: bool
    summarise: Callable[[Mapping[str, Any]], dict[str, object]]


def load_token(environ: Mapping[str, str], env_file: Path) -> str | None:
    return lookup(TOKEN_ENV, environ, env_file)


def _reason(raw: str) -> str:
    if not 0 < len(raw) <= REASON_MAX_CHARS:
        raise argparse.ArgumentTypeError(f"reason must be 1-{REASON_MAX_CHARS} characters")
    return raw


def _period_key(raw: str) -> str:
    if not PERIOD_KEY_PATTERN.fullmatch(raw):
        raise argparse.ArgumentTypeError("period key must be YYYY-MM-DD, YYYY-Www or YYYY-MM")
    return raw


def _wait_timeout(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        raise argparse.ArgumentTypeError("timeout must be a number of seconds") from None
    if not MIN_WAIT_TIMEOUT_S <= value <= MAX_WAIT_TIMEOUT_S:
        raise argparse.ArgumentTypeError(
            f"timeout must be {MIN_WAIT_TIMEOUT_S:g}-{MAX_WAIT_TIMEOUT_S:g} seconds")
    return value


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


# --- parser ------------------------------------------------------------------------
def _add_agent(command: argparse.ArgumentParser, *, required: bool) -> None:
    """The same --agent choices on every loop command (AGENTS, DEMO accounts only)."""
    command.add_argument("--agent", choices=AGENTS, required=required, default=None,
                         help="your operator agent name (recorded; refused if not enabled "
                              "in V6_OPERATOR_AGENTS)")


def _add_loop_commands(commands: Any) -> None:
    check = commands.add_parser("preflight", help="is everything ready to trade? (JSON)")
    _add_agent(check, required=False)
    check.add_argument("--allow-shadow", action="store_true",
                       help="accept V6_MODE=shadow (rehearsal, nothing is executed)")
    wait = commands.add_parser("wait", help="block until the next operator packet")
    wait.add_argument("--timeout", type=_wait_timeout, default=waiting.DEFAULT_WAIT_TIMEOUT_S,
                      help="seconds to wait for a packet (default %(default)g); exit 3 after")
    wait.add_argument("--out", type=json_path, default=PACKET_FILE,
                      help="packet file (default adapter/.v6_operator/packet.json)")
    _add_agent(wait, required=False)
    template = commands.add_parser("template", help="write a decision skeleton for the packet")
    template.add_argument("--packet", type=json_path, default=PACKET_FILE,
                          help="packet file (default adapter/.v6_operator/packet.json)")
    template.add_argument("--out", type=json_path, default=DECISION_FILE,
                          help="decision file (default adapter/.v6_operator/decision.json)")
    template.add_argument("--force", action="store_true",
                          help="overwrite an edited decision for the same cycle")
    submit = commands.add_parser("submit", help="send the decision to the adapter")
    _add_agent(submit, required=True)
    submit.add_argument("--file", type=json_path_or_stdin, default=DECISION_FILE,
                        help="decision JSON file, or - for standard input")
    submit.add_argument("--packet", type=json_path, default=PACKET_FILE,
                        help="packet file used for warnings only")


def _add_control_commands(commands: Any) -> None:
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="v6_operator", description="Qlip V6 operator CLI")
    parser.add_argument("--base-url", default=None, help=f"default {DEFAULT_BASE_URL}")
    parser.add_argument("--timeout", dest="http_timeout", type=float, default=DEFAULT_TIMEOUT_S,
                        help="HTTP timeout in seconds (the wait long-poll uses its own)")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE,
                        help=f"fallback for {TOKEN_ENV}")
    commands = parser.add_subparsers(dest="command", required=True)
    _add_loop_commands(commands)
    _add_control_commands(commands)
    return parser


# --- running -------------------------------------------------------------------------
def run(call: Call, ctx: Context) -> int:
    body = None if call.body is None else _json_bytes(call.body)
    try:
        reply = ctx.client.send(call.method, call.path, body, auth=call.needs_token)
    except TransportError as exc:
        emit_error(ctx, "unreachable", str(exc), base_url=ctx.client.base_url)
        return EXIT_UNREACHABLE
    if not reply.ok:
        emit(ctx.stderr, error_document(reply))
        return EXIT_HTTP_ERROR
    decoded = reply.json()
    if not isinstance(decoded, Mapping):
        emit(ctx.stderr, {"error": reply.status, "detail": "non-JSON response"})
        return EXIT_HTTP_ERROR
    emit(ctx.stdout, call.summarise(decoded))
    return EXIT_OK


def _json_bytes(document: Mapping[str, object]) -> bytes:
    return json.dumps(document).encode("utf-8")


def dispatch(args: argparse.Namespace, ctx: Context) -> int:
    if args.command == "preflight":
        return preflight.run_preflight(ctx, agent=args.agent, allow_shadow=args.allow_shadow)
    if args.command == "wait":
        return waiting.run_wait(ctx, timeout_s=args.timeout, out_path=args.out,
                                agent=args.agent)
    if args.command == "template":
        return decisions.run_template(ctx, packet_path=args.packet, out_path=args.out,
                                      force=args.force)
    if args.command == "submit":
        return decisions.run_submit(ctx, agent=args.agent, decision_path=args.file,
                                    packet_path=args.packet)
    return run(build_call(args), ctx)


def build_client(args: argparse.Namespace, environ: Mapping[str, str],
                 transport: Transport) -> Client:
    """Raises UsageError before anything is sent."""
    base_url = resolve_base_url(args.base_url or environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL)
    wants_token = args.command not in LOCAL_COMMANDS and args.command != "status"
    token = load_token(environ, args.env_file) if wants_token else None
    if args.command in TOKEN_COMMANDS and token is None:
        raise UsageError(f"{TOKEN_ENV} is not set (environment or {args.env_file.name})")
    return Client(base_url=base_url, transport=transport, timeout=args.http_timeout, token=token)


def main(argv: Sequence[str] | None = None, *, environ: Mapping[str, str] | None = None,
         transport: Transport = urllib_transport, stdout: TextIO | None = None,
         stderr: TextIO | None = None, stdin: BinaryIO | None = None,
         clock: Callable[[], float] = time.time,
         sleep: Callable[[float], None] = time.sleep) -> int:
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    env = os.environ if environ is None else environ
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse already printed usage or help
        return EXIT_OK if exc.code in (0, None) else EXIT_USAGE
    source = getattr(sys.stdin, "buffer", None) if stdin is None else stdin
    try:
        client = build_client(args, env, transport)
        ctx = Context(client=client, stdout=out, stderr=err, stdin=source, clock=clock,
                      sleep=sleep)
        return dispatch(args, ctx)
    except UsageError as exc:
        emit(err, {"error": "usage", "detail": str(exc)})
        return EXIT_USAGE
    except OSError as exc:
        emit(err, {"error": "io", "detail": f"{type(exc).__name__}: {exc.filename}"})
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
