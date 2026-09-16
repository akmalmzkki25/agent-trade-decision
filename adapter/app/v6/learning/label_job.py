"""
The labeling job: label every stored candidate whose triple barrier has resolved.

Runs from the watchdog every LABEL_INTERVAL_S in a worker thread. A candidate is
ready once the stored M1 and M5 bars reach its `available_from`; a refused one
needs no bars. A label that would read across a data hole (DATA_GAP) is held
back while a backfill can still fill the hole (`LabelerConfig.data_gap_grace_s`,
the EA's 3-day M5 backfill) and written as 'unfillable'/'data_gap' after that,
so a hole never becomes a wrong tp/sl/time/unfilled label.
"""

from __future__ import annotations

import asyncio
import logging
import math
import sqlite3
from dataclasses import dataclass
from typing import Final, Protocol

from ..clock import Clock
from ..ledger_cycles import LedgerCycles
from ..ledger_cycles_schema import CandidateRecord
from ..types import TIMEFRAME_SECONDS, Bar
from .labeler import (
    DEFAULT_LABELER_CONFIG, REASON_DATA_EXPIRED, REFUSED_VERDICT, LabelerConfig, LabelResult,
    decision_epoch, label_candidate, unfillable,
)

logger = logging.getLogger(__name__)

PATH_TIMEFRAMES: Final[tuple[str, ...]] = ("M1", "M5")
MAX_REFUSAL_CODES: Final[int] = 5
MAX_CODE_CHARS: Final[int] = 64
ERR_STORAGE: Final[str] = "LABELER_STORAGE"


class BarSource(Protocol):
    """What the job reads bars through; `BarStore` satisfies it."""

    def latest(self, tf: str, n: int) -> tuple[Bar, ...]: ...


@dataclass(frozen=True)
class BarWindow:
    m1: tuple[Bar, ...] = ()
    m5: tuple[Bar, ...] = ()
    covered_from: int | None = None   # earliest decision the load reaches; None = all bars


@dataclass(frozen=True)
class LabelRun:
    results: tuple[LabelResult, ...] = ()   # the results this run tried to write
    labeled: int = 0            # results with label_status 'labeled'
    unfillable: int = 0         # results with label_status 'unfillable'
    written: int = 0            # rows this run changed (others were labeled concurrently)
    waiting: int = 0            # available by `now`, but the bars stop before available_from
    data_gaps: int = 0          # held back: a backfill may still fill their bar hole
    error: str = ""


def data_through(bars: BarSource) -> int | None:
    """Close of the newest stored bar, the earlier of M1 and M5; None without bars."""
    closes = [series[-1].t + TIMEFRAME_SECONDS[tf]
              for tf in PATH_TIMEFRAMES if (series := bars.latest(tf, 1))]
    return min(closes) if closes else None


def _load_window(bars: BarSource, start: int, through: int, config: LabelerConfig) -> BarWindow:
    """Newest bars back to `start`; `covered_from` is set when every series hit the cap."""
    loaded: dict[str, tuple[Bar, ...]] = {}
    capped_from: list[int] = []
    for tf in PATH_TIMEFRAMES:
        slots = math.ceil(max(through - start, 0) / TIMEFRAME_SECONDS[tf])
        want = min(config.max_window_bars, slots + 1)
        loaded[tf] = series = bars.latest(tf, want)
        if len(series) >= want and series[0].t > start:
            capped_from.append(series[0].t)
    covered_from = min(capped_from) if len(capped_from) == len(PATH_TIMEFRAMES) else None
    return BarWindow(m1=loaded["M1"], m5=loaded["M5"], covered_from=covered_from)


def _refusal_codes(ledger: LedgerCycles, candidate: CandidateRecord) -> tuple[str, ...]:
    """Refusal codes from the cycle summary; () when the summary does not carry them."""
    cycle = ledger.get_cycle(candidate.cycle_id)
    try:
        items = cycle.summary().get("candidates") if cycle is not None else None
    except (ValueError, AttributeError):   # not JSON, or not a JSON object
        return ()
    matches = [item for item in (items if isinstance(items, list) else ())
               if isinstance(item, dict) and isinstance(item.get("candidate"), dict)
               and item["candidate"].get("candidate_id") == candidate.candidate_id]
    refusal = matches[0].get("refusal") if matches else None
    codes = refusal.get("codes") if isinstance(refusal, dict) else None
    listed = codes[:MAX_REFUSAL_CODES] if isinstance(codes, list) else []
    return tuple(str(code)[:MAX_CODE_CHARS] for code in listed)


def _label_one(ledger: LedgerCycles, candidate: CandidateRecord, window: BarWindow,
               config: LabelerConfig) -> LabelResult:
    if candidate.verdict == REFUSED_VERDICT:
        return label_candidate(candidate, (), (), config,
                               refusal_codes=_refusal_codes(ledger, candidate))
    if window.covered_from is not None and decision_epoch(candidate) < window.covered_from:
        return unfillable(candidate, REASON_DATA_EXPIRED,
                          "window is older than the bar load cap")
    return label_candidate(candidate, window.m1, window.m5, config)


def _write(ledger: LedgerCycles, result: LabelResult, clock: Clock) -> bool:
    """False when another writer labeled the candidate first (write_label writes once)."""
    return ledger.write_label(result.candidate_id, label_status=result.label_status,
                              outcome=result.outcome, outcome_r=result.outcome_r,
                              labeled_at=clock.now_epoch())


def _may_still_heal(result: LabelResult, candidate: CandidateRecord, now: float,
                    config: LabelerConfig) -> bool:
    return result.data_gap and now < candidate.available_from + config.data_gap_grace_s


def _ready(pending: tuple[CandidateRecord, ...],
           through: int | None) -> tuple[CandidateRecord, ...]:
    """A refused candidate needs no bars; the others wait until bars reach their horizon."""
    return tuple(c for c in pending if c.verdict == REFUSED_VERDICT
                 or (through is not None and c.available_from <= through))


def _results(ledger: LedgerCycles, bars: BarSource, ready: tuple[CandidateRecord, ...],
             through: int | None, config: LabelerConfig) -> tuple[LabelResult, ...]:
    starts = [decision_epoch(c) for c in ready if c.verdict != REFUSED_VERDICT]
    window = (_load_window(bars, min(starts), through, config)
              if starts and through is not None else BarWindow())
    return tuple(_label_one(ledger, c, window, config) for c in ready)


def _report(written: int, waiting: int, held: int, final: tuple[LabelResult, ...]) -> None:
    gaps = sum(1 for result in final if result.data_gap)
    if gaps:
        logger.warning("v6 labeler: %d candidates labeled data_gap (bars missing for good)", gaps)
    if written:
        logger.info("v6 labeler: wrote %d labels, %d waiting for bars", written, waiting)
    if held:
        logger.debug("v6 labeler: %d candidates wait for a backfill across a bar hole", held)


def _run(ledger: LedgerCycles, bars: BarSource, now: float, clock: Clock,
         config: LabelerConfig) -> LabelRun:
    pending = ledger.pending_candidates(int(now), config.batch_limit)
    if not pending:
        return LabelRun()
    through = data_through(bars)
    ready = _ready(pending, through)
    results = _results(ledger, bars, ready, through, config)
    final = tuple(result for result, candidate in zip(results, ready)
                  if not _may_still_heal(result, candidate, now, config))
    held = len(results) - len(final)
    written = sum(_write(ledger, result, clock) for result in final)
    labeled = sum(1 for result in final if result.label_status == "labeled")
    waiting = len(pending) - len(ready)
    _report(written, waiting, held, final)
    return LabelRun(results=final, labeled=labeled, unfillable=len(final) - labeled,
                    written=written, waiting=waiting, data_gaps=held)


def label_pending_sync(ledger_cycles: LedgerCycles, bar_store: BarSource, now: float,
                       clock: Clock, config: LabelerConfig = DEFAULT_LABELER_CONFIG) -> LabelRun:
    """Label what is available by `now` and has bars to its horizon; storage errors -> error."""
    try:
        return _run(ledger_cycles, bar_store, now, clock, config)
    except sqlite3.Error as exc:
        logger.error("v6 labeler: storage error (%s); labels left pending", type(exc).__name__)
        return LabelRun(error=ERR_STORAGE)


async def label_pending(ledger_cycles: LedgerCycles, bar_store: BarSource, now: float,
                        clock: Clock, *,
                        config: LabelerConfig = DEFAULT_LABELER_CONFIG) -> LabelRun:
    """Watchdog entry point: `label_pending_sync` in a worker thread."""
    return await asyncio.to_thread(label_pending_sync, ledger_cycles, bar_store, now, clock,
                                   config)
