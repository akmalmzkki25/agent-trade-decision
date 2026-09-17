"""The compact markdown summary printed by scripts/v6_replay.py."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

from .evaluate import BANDS

DAY_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("bars", "bars"), ("insufficient", "insufficient_bars"), ("evaluated", "evaluated"),
    ("all gates pass", "gates_pass"), ("candidates", "candidates"),
    ("offerable", "offerable"), ("packets if flat", "packets_if_flat"),
    ("with suggestion", "suggestions_if_flat"),
    ("trades (sim)", "trades"),
)
MAX_CODE_ROWS: Final[int] = 20


def _cell(tally: Mapping[str, Any], key: str) -> str:
    value = tally[key]
    return str(sum(value.values())) if isinstance(value, Mapping) else str(value)


def _table(header: Sequence[str], rows: Iterable[Sequence[object]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return lines


def _tally_rows(named: Iterable[tuple[str, Mapping[str, Any]]]) -> list[list[str]]:
    return [[name] + [_cell(tally, key) for _, key in DAY_COLUMNS] for name, tally in named]


def _days(summary: Mapping[str, Any]) -> list[str]:
    days = summary["days"]
    rows = _tally_rows([(day, days[day]["total"]) for day in days]
                       + [("**total**", summary["total"])])
    return ["## Per UTC day"] + _table(["day"] + [label for label, _ in DAY_COLUMNS], rows)


def _bands(summary: Mapping[str, Any]) -> list[str]:
    named = [(name, summary["bands"][name]) for name, _, _ in BANDS]
    rows = _tally_rows(named + [("07-20 (London+NY)", summary["core_07_20"])])
    return ["## Per UTC hour band (bar close)"] + _table(
        ["band"] + [label for label, _ in DAY_COLUMNS], rows)


def _day_bands(summary: Mapping[str, Any], key: str, title: str) -> list[str]:
    names = [name for name, _, _ in BANDS]
    rows = [[day] + [entry["bands"][name][key] for name in names] + [entry["total"][key]]
            for day, entry in summary["days"].items()]
    return [f"## {title} by day and band"] + _table(["day"] + names + ["total"], rows)


def _gates(summary: Mapping[str, Any], gate_codes: Sequence[str]) -> list[str]:
    total, core = summary["total"], summary["core_07_20"]
    rows = [[code, total["gate_fail"].get(code, 0), total["first_fail"].get(code, 0),
             core["gate_fail"].get(code, 0), core["first_fail"].get(code, 0)]
            for code in gate_codes]
    rows = [row for row in rows if any(row[1:])]
    header = ["gate", "failed (all)", "first (all)", "failed 07-20", "first 07-20"]
    note = (f"evaluated bars: {total['evaluated']} (07-20: {core['evaluated']}); "
            "a bar can fail several gates")
    return ["## Gate failures", note, ""] + _table(header, rows)


def _setups(summary: Mapping[str, Any]) -> list[str]:
    total = summary["total"]
    setups = sorted(set(total["candidates"]) | set(total["candidates_gates_pass"]))
    rows = [[name, total["candidates"].get(name, 0),
             total["candidates_gates_pass"].get(name, 0),
             total["offerable_by_setup"].get(name, 0)] for name in setups]
    return ["## Candidates by setup"] + _table(
        ["setup", "all evaluated bars", "on gate-pass bars", "offerable"], rows)


def _codes(title: str, counts: Mapping[str, int]) -> list[str]:
    rows = list(counts.items())[:MAX_CODE_ROWS] or [("none", 0)]
    return [f"## {title}"] + _table(["code", "count"], rows)


def render_markdown(summary: Mapping[str, Any], meta: Mapping[str, Any],
                    gate_codes: Sequence[str]) -> str:
    total = summary["total"]
    head = [
        "# V6 tier-0 replay", "",
        f"source: {meta['source']}; bars closed {meta['first_close']} .. {meta['last_close']} UTC; "
        f"overrides: {meta['overrides'] or 'none'}",
        "",
    ]
    sections = (
        _days(summary), _bands(summary),
        _day_bands(summary, "packets_if_flat", "Packets if flat"),
        _day_bands(summary, "trades", "Simulated trades"),
        _gates(summary, gate_codes), _setups(summary),
        _codes("Insufficient data (bars)", total["insufficient"]),
        _codes("Exit-plan refusals (candidates)", total["exit_refusals"]),
        _codes("Sizing refusals (offered candidates, standard tier)", total["sizing_refusals"]),
        _codes("Packet refusals (gate-pass bars)", total["packet_refusals"]),
    )
    lines = list(head)
    for section in sections:
        lines += section + [""]
    return "\n".join(lines)
