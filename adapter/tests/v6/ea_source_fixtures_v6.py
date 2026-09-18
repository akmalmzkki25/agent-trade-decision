"""
Reading the V6 EA sources (`ea/QlipV6_XAUUSD.mq5`, `ea/QlipV6/*.mqh`) for the parity
tests: MT5 cannot run here, so the EA is checked by parsing its source.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Final, Literal, get_args, get_origin

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
EA_MAIN: Final[Path] = REPO_ROOT / "ea" / "QlipV6_XAUUSD.mq5"
EA_DIR: Final[Path] = REPO_ROOT / "ea" / "QlipV6"
CONTRACT_DOC: Final[Path] = REPO_ROOT / "docs" / "v6-wire-contract.md"
GOLDEN: Final[Path] = Path(__file__).resolve().parent / "golden" / "hmac_vectors.json"
VECTORS: Final[dict[str, Any]] = json.loads(GOLDEN.read_text(encoding="utf-8"))

MAX_MAIN_LINES: Final[int] = 300
MAX_INCLUDE_LINES: Final[int] = 400
MAX_FUNCTION_LINES: Final[int] = 50
STRING_LITERAL: Final[str] = r'"((?:[^"\\]|\\.)*)"'
ADD_CALL: Final[re.Pattern[str]] = re.compile(r'\.Add(Str|Num|Int|Bool|Raw|Null)\(\s*"(\w+)"')
DEFINE: Final[re.Pattern[str]] = re.compile(
    r"^#define[ \t]+(\w+)[ \t]+(.+?)[ \t]*(?://.*)?$", re.M)
INPUT: Final[re.Pattern[str]] = re.compile(r"^input[ \t]+\w+[ \t]+(\w+)[ \t]*=[ \t]*(.+?);", re.M)
# The poll-reply fields the canonical string turns into integers.
CANONICAL_SOURCE: Final[dict[str, str]] = {
    "entry_points": "entry", "sl_points": "sl", "tp_points": "tp",
    "lots_hundredths": "lots", "ref_points": "ref_price", "tp1_points": "tp1",
    "tp2_points": "tp2", "sl_after_tp1_points": "sl_after_tp1",
    "sl_after_tp2_points": "sl_after_tp2", "action_sl_points": "action_sl",
    "action_tp_points": "action_tp", "action_tp1_points": "action_tp1",
    "action_tp2_points": "action_tp2", "action_sl1_points": "action_sl1",
    "action_sl2_points": "action_sl2", "action_price_points": "action_price",
}
PRICE_FIELDS: Final[tuple[str, ...]] = (
    "entry", "sl", "tp", "ref_price", "tp1", "tp2", "sl_after_tp1", "sl_after_tp2",
    "action_sl", "action_tp", "action_price")
FORBIDDEN_TRADE_CALLS: Final[tuple[str, ...]] = (
    "TRADE_ACTION_SLTP", "TRADE_ACTION_MODIFY", "OrderModify", "PositionModify",
    "PositionClosePartial", "CTrade", "Trade.mqh", "OrderSendAsync",
)
PRINTERS: Final[tuple[str, ...]] = ("Print", "PrintFormat", "Comment", "Alert", "SendNotification")


# --- source helpers ------------------------------------------------------------
def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def include(name: str) -> str:
    return read(EA_DIR / name)


def all_files() -> list[Path]:
    return [EA_MAIN, *sorted(EA_DIR.glob("*.mqh"))]


def all_sources() -> str:
    return "\n".join(read(path) for path in all_files())


def ea_defines() -> dict[str, str]:
    return dict(DEFINE.findall(all_sources()))


def define_string(name: str) -> str:
    value = ea_defines()[name]
    match = re.fullmatch(STRING_LITERAL, value)
    assert match, f"{name} is not a string literal: {value}"
    return unescape(match.group(1))


def ea_inputs() -> dict[str, str]:
    return dict(INPUT.findall(read(EA_MAIN)))


def unescape(literal: str) -> str:
    """MQL5 string literal body -> text; only \\" and \\\\ are used by these literals."""
    escapes = set(re.findall(r"\\(.)", literal))
    assert escapes <= {'"', "\\"}, f"unexpected escape in {literal[:40]}"
    return re.sub(r'\\(["\\])', r"\1", literal)


def function_body(source: str, header: str) -> str:
    """The text between the braces of the function whose header starts with `header`."""
    start = source.index(header)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        depth += {"{": 1, "}": -1}.get(source[index], 0)
        if depth == 0:
            return source[opening + 1:index]
    raise AssertionError(f"unbalanced braces after {header}")


def emitted(body: str) -> list[tuple[str, str]]:
    return [(name, kind) for kind, name in ADD_CALL.findall(body)]


def writer_kind(annotation: Any) -> set[str]:
    """How Json.mqh must write a value of this type."""
    if get_origin(annotation) is Literal or annotation is str:
        return {"Str"}
    kinds = {bool: "Bool", int: "Int", float: "Num"}
    assert annotation in kinds, f"no EA writer for {annotation!r}"
    return {kinds[annotation]}


def literal_types(annotation: Any) -> set[type]:
    if get_origin(annotation) is Literal:
        return {type(arg) for arg in get_args(annotation)}
    return set().union(*(literal_types(arg) for arg in get_args(annotation)))


def reader_for(annotation: Any) -> str:
    """The Json getter that reads this PollResponse field strictly."""
    readers = {bool: "JsonGetBool", int: "JsonGetLong", float: "JsonGetNumber",
               str: "JsonGetString"}
    if annotation in readers:
        return readers[annotation]
    kinds = literal_types(annotation)  # Literal[...] and unions of literals
    assert len(kinds) == 1 and next(iter(kinds)) in readers, f"no EA reader for {annotation!r}"
    return readers[next(iter(kinds))]


def named(section: str) -> dict[str, dict[str, Any]]:
    return {row["name"]: row for row in VECTORS[section]}
