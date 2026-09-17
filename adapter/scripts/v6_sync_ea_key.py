#!/usr/bin/env python3
"""
Copy V6_EA_HMAC_KEY into an MT5 terminal data folder (standard library only).

    python adapter/scripts/v6_sync_ea_key.py --terminal-data "<terminal data folder>"
    python adapter/scripts/v6_sync_ea_key.py --terminal-data "<terminal data folder>" --check

`--data-folder` is an alias of `--terminal-data` (docs/v6-wire-contract.md).
The key comes from V6_EA_HMAC_KEY in the environment, else from adapter/.env.
It must be 32-256 printable ASCII characters without whitespace and not a
placeholder, the rule the adapter applies (`app.v6.config.ea_key_ok`). It is
written to <folder>/MQL5/Files/QlipV6/hmac.key as its ASCII bytes with no BOM
and no newline (the EA input InpHmacKeyFile defaults to QlipV6\\hmac.key),
replacing the file atomically. The folder must already contain MQL5\\.

The key is never printed. The output names the file and the key fingerprint
(first 12 hex characters of HMAC(key, "qlip-v6-key-fingerprint")), which the EA
and the adapter may log so the two can be compared.

Exit codes: 0 written, unchanged or matching; 1 `--check` found a missing or
different key, or the write failed; 2 usage or configuration problem.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final, TextIO

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:  # `python -I` leaves the script's directory off sys.path
    sys.path.insert(0, _SCRIPTS_DIR)

from v6ops.envfile import DEFAULT_ENV_FILE, read_env_file_value  # noqa: E402

KEY_ENV: Final[str] = "V6_EA_HMAC_KEY"
MQL5_DIR: Final[str] = "MQL5"
KEY_RELATIVE_PATH: Final[Path] = Path(MQL5_DIR) / "Files" / "QlipV6" / "hmac.key"
EA_KEY_INPUT: Final[str] = "InpHmacKeyFile=QlipV6\\hmac.key"
MIN_KEY_LENGTH: Final[int] = 32
MAX_KEY_LENGTH: Final[int] = 256
KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"[\x21-\x7e]+")
PLACEHOLDER_SECRETS: Final[frozenset[str]] = frozenset(
    {"", "change-me", "change-me-dev-only", "changeme", "your-key-here"})
PLACEHOLDER_FRAGMENTS: Final[tuple[str, ...]] = (
    "change-me", "changeme", "your-key", "placeholder")
FINGERPRINT_LABEL: Final[bytes] = b"qlip-v6-key-fingerprint"
FINGERPRINT_CHARS: Final[int] = 12
EA_IGNORED_TRAILING: Final[str] = "\r\n \t"   # the EA strips these when it reads the file
MAX_KEY_FILE_BYTES: Final[int] = 4 * MAX_KEY_LENGTH
KEY_ENCODING: Final[str] = "ascii"
EXIT_OK: Final[int] = 0
EXIT_FAILED: Final[int] = 1
EXIT_USAGE: Final[int] = 2


class UsageError(Exception):
    """Bad arguments, key or folder; nothing was written."""


def key_problem(key: str) -> str | None:
    """Why `key` is not a usable V6 EA key, or None (same rule as config.ea_key_ok)."""
    if not MIN_KEY_LENGTH <= len(key) <= MAX_KEY_LENGTH:
        return f"must be {MIN_KEY_LENGTH}-{MAX_KEY_LENGTH} characters, not {len(key)}"
    if KEY_PATTERN.fullmatch(key) is None:
        return "must be printable ASCII without whitespace"
    lowered = key.lower()
    if lowered in PLACEHOLDER_SECRETS or any(part in lowered for part in PLACEHOLDER_FRAGMENTS):
        return "is a placeholder"
    return None


def fingerprint(key: str) -> str:
    digest = hmac.new(key.encode(KEY_ENCODING), FINGERPRINT_LABEL, hashlib.sha256).hexdigest()
    return digest[:FINGERPRINT_CHARS]


def load_key(environ: Mapping[str, str], env_file: Path) -> str:
    """The key exactly as set (an environment value is not trimmed); UsageError if unusable."""
    key = environ.get(KEY_ENV) or read_env_file_value(env_file, KEY_ENV)
    if not key:
        raise UsageError(f"{KEY_ENV} is not set (environment or {env_file.name})")
    problem = key_problem(key)
    if problem is not None:
        raise UsageError(f"{KEY_ENV} {problem}")
    return key


def key_path(terminal_data: Path) -> Path:
    if not terminal_data.is_dir():
        raise UsageError(f"terminal data folder not found: {terminal_data}")
    if not (terminal_data / MQL5_DIR).is_dir():
        raise UsageError(f"not an MT5 terminal data folder (no {MQL5_DIR} folder): "
                         f"{terminal_data}")
    return terminal_data / KEY_RELATIVE_PATH


def read_existing(path: Path) -> str | None:
    """The key the EA would read from `path`; None when there is no file.

    Anything that cannot be a key (too large, not ASCII) reads as "", which never
    matches a valid key.
    """
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_KEY_FILE_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(raw) > MAX_KEY_FILE_BYTES:
        return ""
    try:
        return raw.decode(KEY_ENCODING).rstrip(EA_IGNORED_TRAILING)
    except UnicodeDecodeError:
        return ""


def same_key(existing: str | None, key: str) -> bool:
    if existing is None:
        return False
    return hmac.compare_digest(existing.encode(KEY_ENCODING), key.encode(KEY_ENCODING))


def write_key(path: Path, key: str) -> None:
    """Replace `path` with the key's bytes; mkstemp creates the file owner-only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".hmac.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(key.encode(KEY_ENCODING))
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def emit(stream: TextIO, document: Mapping[str, object]) -> None:
    stream.write(json.dumps(document, indent=2, sort_keys=True) + "\n")


def check(path: Path, key: str, stdout: TextIO) -> int:
    existing = read_existing(path)
    matches = same_key(existing, key)
    other = None if not existing or key_problem(existing) else fingerprint(existing)
    emit(stdout, {"match": matches, "present": existing is not None, "path": str(path),
                  "fingerprint": fingerprint(key), "file_fingerprint": other})
    return EXIT_OK if matches else EXIT_FAILED


def sync(path: Path, key: str, stdout: TextIO) -> int:
    unchanged = same_key(read_existing(path), key)
    if not unchanged:
        write_key(path, key)
    emit(stdout, {"written": not unchanged, "unchanged": unchanged, "path": str(path),
                  "bytes": len(key), "fingerprint": fingerprint(key), "ea_input": EA_KEY_INPUT})
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="v6_sync_ea_key",
                                     description="Copy V6_EA_HMAC_KEY to the MT5 terminal")
    parser.add_argument("--terminal-data", "--data-folder", dest="terminal_data", type=Path,
                        required=True, help="MT5 terminal data folder (File > Open Data Folder)")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE,
                        help=f"fallback for {KEY_ENV}")
    parser.add_argument("--check", action="store_true",
                        help="compare the file with the key; write nothing")
    return parser


def main(argv: Sequence[str] | None = None, *, environ: Mapping[str, str] | None = None,
         stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    env = os.environ if environ is None else environ
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse already printed usage or help
        return EXIT_OK if exc.code in (0, None) else EXIT_USAGE
    try:
        key = load_key(env, args.env_file)
        path = key_path(args.terminal_data)
        return check(path, key, out) if args.check else sync(path, key, out)
    except UsageError as exc:
        emit(err, {"error": "usage", "detail": str(exc)})
        return EXIT_USAGE
    except OSError as exc:
        emit(err, {"error": "io", "detail": f"{type(exc).__name__}: {exc.filename}"})
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
