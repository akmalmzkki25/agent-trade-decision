"""
What every command receives, plus the output conventions shared by all of them.

Output is ASCII only (a Windows console may not be UTF-8): JSON is written with
`ensure_ascii`, and text taken from a packet passes `clean` first so that no
control or escape character reaches the terminal.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, BinaryIO, Final, TextIO

from .transport import Client

EXIT_OK: Final[int] = 0
EXIT_ERROR: Final[int] = 1
EXIT_USAGE: Final[int] = 2
EXIT_UNREACHABLE: Final[int] = 3
EXIT_TIMEOUT: Final[int] = 3
EXIT_NO_SESSION: Final[int] = 4
EXIT_NOT_DEMO: Final[int] = 5
DEMO_TRADE_MODE: Final[str] = "DEMO"
# risk.policy codes that mean "not a DEMO account the operator may trade" (exit 5).
DEMO_POLICY_CODES: Final[frozenset[str]] = frozenset({
    "POLICY_OPERATOR_DEMO_ONLY", "POLICY_REAL_REFUSED", "POLICY_CONTEST_REFUSED",
    "POLICY_SERVER_NOT_DEMO"})
OPERATOR_BACKEND: Final[str] = "operator"
EXECUTE_MODE: Final[str] = "execute"
SHADOW_MODE: Final[str] = "shadow"
# The operator agents (Claude Code, Codex, Antigravity): app.v6.config.OPERATOR_AGENTS,
# restated because the scripts never import the app.
AGENTS: Final[tuple[str, ...]] = ("claude_code", "codex", "antigravity")
MAX_TEXT_CHARS: Final[int] = 120
PRINTABLE_ASCII_FIRST: Final[int] = 0x20
PRINTABLE_ASCII_LAST: Final[int] = 0x7E
REPLACEMENT: Final[str] = "?"


@dataclass(frozen=True)
class Context:
    client: Client
    stdout: TextIO
    stderr: TextIO
    stdin: BinaryIO | None = None
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep


def emit(stream: TextIO, document: Mapping[str, object], *, compact: bool = False) -> None:
    text = (json.dumps(document, sort_keys=True, separators=(",", ":")) if compact
            else json.dumps(document, indent=2, sort_keys=True))
    stream.write(text + "\n")


def emit_error(ctx: Context, error: str, detail: str, **extra: object) -> None:
    emit(ctx.stderr, {"error": error, "detail": detail, **extra})


def clean(value: object, limit: int = MAX_TEXT_CHARS) -> str:
    """Printable ASCII only, cut to `limit` characters; packet text is untrusted."""
    text = "" if value is None else str(value)
    kept = "".join(ch if PRINTABLE_ASCII_FIRST <= ord(ch) <= PRINTABLE_ASCII_LAST else REPLACEMENT
                   for ch in text[:limit])
    return kept + ("..." if len(text) > limit else "")


def get_path(document: Any, *keys: str) -> Any:
    """document[k1][k2]... or None as soon as a level is missing or not a mapping."""
    value: Any = document
    for key in keys:
        value = value.get(key) if isinstance(value, Mapping) else None
    return value
