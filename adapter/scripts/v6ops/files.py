"""
The operator's working files under `adapter/.v6_operator/` (gitignored).

    packet.json       the packet `wait` received (no secrets: DEMO, equity band only)
    packet.prev.json  the packet before it, kept when `wait` starts again
    decision.json     the decision `template` wrote and the agent edits

Files are replaced atomically, so a reader never sees half a document. JSON is
read strictly: NaN, Infinity and duplicate keys are refused.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any, BinaryIO, Final

from .envfile import ADAPTER_DIR
from .transport import UsageError

STATE_DIR: Final[Path] = ADAPTER_DIR / ".v6_operator"
PACKET_FILE: Final[Path] = STATE_DIR / "packet.json"
DECISION_FILE: Final[Path] = STATE_DIR / "decision.json"
PREVIOUS_PACKET_NAME: Final[str] = "packet.prev.json"
JSON_SUFFIX: Final[str] = ".json"
STDIN_MARKER: Final[str] = "-"
MAX_PACKET_FILE_BYTES: Final[int] = 1_000_000
TEXT_ENCODING: Final[str] = "utf-8"
READ_ENCODING: Final[str] = "utf-8-sig"


def json_path(raw: str) -> Path:
    """argparse type: only *.json files are ever written or read as documents."""
    path = Path(raw)
    if path.suffix.lower() != JSON_SUFFIX:
        raise ValueError("path must end in .json")
    return path


def json_path_or_stdin(raw: str) -> Path:
    """argparse type: a *.json file, or "-" for standard input."""
    return Path(STDIN_MARKER) if raw == STDIN_MARKER else json_path(raw)


def is_stdin(path: Path) -> bool:
    return str(path) == STDIN_MARKER


def write_json_atomic(path: Path, document: object) -> None:
    """Replace `path` with `document` (indented ASCII JSON); raises OSError."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(document, indent=2, allow_nan=False) + "\n"
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding=TEXT_ENCODING, newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def archive(path: Path, archived_name: str = PREVIOUS_PACKET_NAME) -> bool:
    """Move `path` aside (replacing an older copy); False when there was nothing to move."""
    try:
        os.replace(path, path.with_name(archived_name))
    except FileNotFoundError:
        return False
    return True


def read_capped(path: Path, limit: int, what: str) -> bytes:
    """The file's bytes; UsageError when it is missing, unreadable or larger than `limit`."""
    try:
        size = path.stat().st_size
        if size > limit:
            raise UsageError(f"{what} is {size} bytes; the limit is {limit}")
        return path.read_bytes()
    except OSError as exc:
        raise UsageError(f"{what} cannot be read ({type(exc).__name__}): {path}") from None


def read_stream_capped(stream: BinaryIO | None, limit: int, what: str) -> bytes:
    if stream is None:
        raise UsageError(f"{what}: no standard input")
    raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise UsageError(f"{what} is larger than {limit} bytes")
    return raw


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not allowed in JSON")


def _unique_keys(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"duplicate key {key[:40]!r}")
        document[key] = value
    return document


def load_json_object(raw: bytes, what: str) -> dict[str, Any]:
    """A JSON object from `raw`; UsageError for anything else."""
    try:
        document = json.loads(raw.decode(READ_ENCODING), parse_constant=_reject_constant,
                              object_pairs_hook=_unique_keys)
    except (UnicodeDecodeError, ValueError) as exc:
        raise UsageError(f"{what} is not valid JSON: {str(exc)[:120]}") from None
    if not isinstance(document, dict):
        raise UsageError(f"{what} must hold a JSON object")
    return document


def load_json_file(path: Path, what: str, limit: int = MAX_PACKET_FILE_BYTES) -> dict[str, Any]:
    return load_json_object(read_capped(path, limit, what), what)
