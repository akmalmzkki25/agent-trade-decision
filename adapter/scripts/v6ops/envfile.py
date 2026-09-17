"""
dotenv lookups for the V6 scripts, with the parser the adapter itself uses.

A value set in the environment wins over `adapter/.env`, as it does for the
adapter (pydantic-settings). The file is read with `dotenv.dotenv_values` and the
encoding pydantic-settings passes, so quotes, escapes, `${VAR}` expansion and
comments give the scripts exactly the value the adapter sees (a simpler parser once
let the EA key file and the adapter hold different keys). Names match without
regard to case, as in the adapter settings.

python-dotenv is imported lazily so the scripts still start without site-packages
(`python -S`). Only then is the file read by `simple_env_values`, which strips
quotes but neither expands `${VAR}` nor decodes escapes.

Values are returned to the caller only; nothing here prints or logs them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final

ADAPTER_DIR: Final[Path] = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE: Final[Path] = ADAPTER_DIR / ".env"
# pydantic-settings reads the env file with dotenv_values(path, encoding="utf8").
ENV_FILE_ENCODING: Final[str] = "utf8"
QUOTES: Final[str] = "\"'"
INLINE_COMMENT: Final[str] = " #"
EXPORT_PREFIX: Final[str] = "export "

EnvReader = Callable[[Path], Mapping[str, "str | None"]]


def env_value(raw: str) -> str:
    """The value part of one `KEY=value` line (fallback parser)."""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in QUOTES:
        return value[1:-1]
    return value.split(INLINE_COMMENT, 1)[0].strip()


def simple_env_values(env_file: Path) -> dict[str, str | None]:
    """Fallback parser for runs without python-dotenv; the last assignment wins."""
    found: dict[str, str | None] = {}
    for line in env_file.read_text(encoding=ENV_FILE_ENCODING).splitlines():
        key, sep, value = line.strip().partition("=")
        if sep:
            found[key.strip().removeprefix(EXPORT_PREFIX).strip()] = env_value(value)
    return found


def dotenv_reader() -> EnvReader:
    """python-dotenv's reader (the adapter's), else the fallback parser."""
    try:
        from dotenv import dotenv_values
    except ImportError:
        return simple_env_values

    def read(env_file: Path) -> Mapping[str, str | None]:
        return dotenv_values(env_file, encoding=ENV_FILE_ENCODING)

    return read


def read_env_file_value(env_file: Path, name: str,
                        reader: EnvReader | None = None) -> str | None:
    """The value the adapter reads for `name` from `env_file`; None if absent or empty."""
    if not env_file.is_file():
        return None
    try:
        values = (reader or dotenv_reader())(env_file)
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    wanted = name.lower()
    found: str | None = None
    for key, value in values.items():
        if key.lower() == wanted:
            found = value or None
    return found


def lookup(name: str, environ: Mapping[str, str], env_file: Path) -> str | None:
    """`name` from the environment, else from `env_file`; None when unset or empty."""
    value = environ.get(name, "").strip()
    return value or read_env_file_value(env_file, name)
