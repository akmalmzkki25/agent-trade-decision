"""
Counts over BarRecords: in total, per UTC hour band, for London + New York hours
(07-20 UTC) and per UTC day (each also split by band). All counts are plain
JSON-ready dicts with keys sorted by count, then name.

Keys of one tally:
  bars, evaluated, insufficient{reason}, gates_pass, gate_fail{code} (any failed
  gate), first_fail{code}, candidates{setup} (every detected candidate),
  candidates_gates_pass{setup}, exit_refusals{code}, sizing_refusals{code}
  (offered candidates at the standard tier), packet_refusals{code} (bars that
  passed every gate and had an offered candidate but got no packet),
  offerable (candidates in a packet on bars whose gates pass),
  offerable_by_setup{setup}, packets_if_flat (bars that would serve a packet if
  the position slot were free and trades remained),
  trades (packets served under the simulation; each becomes a trade).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any, Final

from .evaluate import BANDS, BarRecord

CORE_BANDS: Final[frozenset[str]] = frozenset({"07-11", "11-17", "17-20"})
Tally = dict[str, Any]


def _sorted(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _count_candidates(record: BarRecord, counters: dict[str, Counter[str]]) -> None:
    for item in record.candidates:
        counters["candidates"][item.setup] += 1
        counters["exit_refusals"].update(item.exit_codes)
        counters["sizing_refusals"].update(item.sizing_codes)
        if record.gates_pass:
            counters["candidates_gates_pass"][item.setup] += 1
        if record.gates_pass and item.in_packet:
            counters["offerable_by_setup"][item.setup] += 1


def _count_record(record: BarRecord, counters: dict[str, Counter[str]],
                  totals: Counter[str]) -> None:
    totals["bars"] += 1
    if not record.evaluated:
        counters["insufficient"].update(record.insufficient)
        return
    totals["evaluated"] += 1
    counters["gate_fail"].update(record.failed_gates)
    if record.failed_gates:
        counters["first_fail"][record.failed_gates[0]] += 1
    totals["gates_pass"] += record.gates_pass
    totals["packets_if_flat"] += record.packet_if_flat
    totals["suggestions_if_flat"] += record.suggestion_if_flat
    totals["trades"] += record.traded
    if record.gates_pass:
        totals["offerable"] += len(record.packet_ids)
        if record.packet_refusal and record.had_offer:
            counters["packet_refusals"][record.packet_refusal] += 1
    _count_candidates(record, counters)


COUNTER_KEYS: Final[tuple[str, ...]] = (
    "insufficient", "gate_fail", "first_fail", "candidates", "candidates_gates_pass",
    "exit_refusals", "sizing_refusals", "packet_refusals", "offerable_by_setup")
TOTAL_KEYS: Final[tuple[str, ...]] = (
    "bars", "evaluated", "gates_pass", "offerable", "packets_if_flat", "suggestions_if_flat",
    "trades")


def tally(records: Iterable[BarRecord]) -> Tally:
    counters: dict[str, Counter[str]] = {key: Counter() for key in COUNTER_KEYS}
    totals: Counter[str] = Counter()
    for record in records:
        _count_record(record, counters, totals)
    result: Tally = {key: int(totals[key]) for key in TOTAL_KEYS}
    result["insufficient_bars"] = result["bars"] - result["evaluated"]
    result.update({key: _sorted(counter) for key, counter in counters.items()})
    return result


def _by_band(records: Sequence[BarRecord]) -> dict[str, Tally]:
    return {name: tally(r for r in records if r.band == name) for name, _, _ in BANDS}


def summarise(records: Sequence[BarRecord]) -> dict[str, Any]:
    days = sorted({record.day for record in records})
    per_day = {}
    for day in days:
        chosen = [record for record in records if record.day == day]
        per_day[day] = {"total": tally(chosen), "bands": _by_band(chosen)}
    return {
        "total": tally(records),
        "core_07_20": tally(r for r in records if r.band in CORE_BANDS),
        "bands": _by_band(records),
        "days": per_day,
    }


def record_view(record: BarRecord) -> dict[str, Any]:
    """One bar, compact, for the JSON output."""
    return {
        "bar_t": record.bar_t, "as_of": record.as_of, "day": record.day, "band": record.band,
        "spread_points": record.spread_points, "insufficient": list(record.insufficient),
        "failed_gates": list(record.failed_gates), "open_position": record.open_position,
        "trades_before": record.trades_before, "packet_refusal": record.packet_refusal,
        "atr_m5": record.atr_m5, "friction_atr": record.friction_atr,
        "packet_if_flat": record.packet_if_flat, "suggestion_if_flat": record.suggestion_if_flat,
        "traded": record.traded,
        "candidates": [
            {"id": item.candidate_id, "setup": item.setup, "side": item.side,
             "exit_codes": list(item.exit_codes), "sizing_codes": list(item.sizing_codes),
             "offered": item.offered, "in_packet": item.in_packet}
            for item in record.candidates],
    }
