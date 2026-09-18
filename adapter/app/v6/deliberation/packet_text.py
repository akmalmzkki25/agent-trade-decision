"""
Packet values from untrusted or computed inputs (docs/v6-wire-contract.md section 9):
finite numbers, printable text cut to its bound, codes and feature names that fit their
patterns. A value that does not fit is dropped here instead of failing the whole packet.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from typing import Final

from ..schemas.operator_parts import MAX_CODES

MAX_GATE_VALUE_CHARS: Final[int] = 64
# The same patterns as schemas.operator_parts.Code and FeatureName.
CODE_RE: Final[re.Pattern[str]] = re.compile(r"[A-Z0-9_:.-]{1,48}")
FEATURE_NAME_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9_]{1,40}")


def is_finite(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # an int beyond float range
        return False


def number(value: object) -> float | None:
    return float(value) if is_finite(value) else None  # type: ignore[arg-type]


def positive_number(value: object) -> float | None:
    result = number(value)
    return result if result is not None and result > 0 else None


def clean_text(value: object, limit: int) -> str:
    """Untrusted text: printable characters only, cut to `limit`."""
    return "".join(ch for ch in str(value) if ch.isprintable())[:limit]


def gate_value(value: object) -> float | str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).upper()
    if isinstance(value, (int, float)):
        return number(value)
    return clean_text(value, MAX_GATE_VALUE_CHARS)


def codes(values: Iterable[object], limit: int = MAX_CODES) -> list[str]:
    valid = (value for value in values if isinstance(value, str) and CODE_RE.fullmatch(value))
    return list(dict.fromkeys(valid))[:limit]


def features(values: Mapping[str, object], limit: int,
             allowed: frozenset[str] | None = None) -> dict[str, float]:
    usable = [(key, float(value))  # type: ignore[arg-type]
              for key, value in sorted(values.items(), key=lambda item: str(item[0]))
              if isinstance(key, str) and FEATURE_NAME_RE.fullmatch(key)
              and (allowed is None or key in allowed) and is_finite(value)]
    return dict(usable[:limit])
