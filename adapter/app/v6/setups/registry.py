"""
Runs every setup detector for one cycle and returns a clean, ordered candidate list.

Order is the evidence priority of kn/15 §6 (displacement, opening-range break, retest,
engulfing), then candidate id, so the result is deterministic. Duplicates are removed
twice: by candidate id, and by identical economics (side, entry, invalidation), where the
higher-priority setup is kept. `detect_candidates` caps the list at the offer cap;
`detect_all` returns everything, for the ledger and the labeler.

A detector that raises ValueError or ArithmeticError is logged and skipped so one bad
series cannot silence the others; anything else propagates as a bug.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from types import MappingProxyType
from typing import Final

from ..cycle_codes import MAX_OFFERED_CANDIDATES, SETUP_NAMES, SetupName
from ..types import Candidate
from . import displacement, engulfing, orb, retest
from .base import SetupInput, SetupSource, as_setup_input

logger = logging.getLogger(__name__)

Detector = Callable[[SetupInput], tuple[Candidate, ...]]

# Plan §2 / MAX_OFFERED_CANDIDATES: PA ranks at most three candidates per cycle.
MAX_CANDIDATES: Final[int] = MAX_OFFERED_CANDIDATES

DETECTORS: Final[tuple[tuple[SetupName, Detector], ...]] = (
    (displacement.SETUP, displacement.detect),
    (orb.SETUP, orb.detect),
    (retest.SETUP, retest.detect),
    (engulfing.SETUP, engulfing.detect),
)

_PRIORITY: Final[Mapping[str, int]] = MappingProxyType(
    {name: rank for rank, name in enumerate(SETUP_NAMES)})


def _run(name: SetupName, detector: Detector, inp: SetupInput) -> tuple[Candidate, ...]:
    try:
        found = detector(inp)
    except (ValueError, ArithmeticError) as exc:
        logger.warning("v6 setup detector %s failed at %s: %s: %s",
                       name, inp.as_of_epoch, type(exc).__name__, exc)
        return ()
    return tuple(found)


def _sort_key(candidate: Candidate) -> tuple[int, str]:
    return _PRIORITY.get(candidate.setup, len(_PRIORITY)), candidate.candidate_id


def _dedupe(candidates: Iterable[Candidate]) -> tuple[Candidate, ...]:
    seen_ids: set[str] = set()
    seen_trades: set[tuple[str, float, float]] = set()
    kept: list[Candidate] = []
    for candidate in candidates:
        trade = (candidate.side, candidate.entry, candidate.invalidation)
        if candidate.candidate_id in seen_ids or trade in seen_trades:
            continue
        seen_ids.add(candidate.candidate_id)
        seen_trades.add(trade)
        kept.append(candidate)
    return tuple(kept)


def detect_all(
    source: SetupSource,
    detectors: tuple[tuple[SetupName, Detector], ...] = DETECTORS,
) -> tuple[Candidate, ...]:
    """Every candidate for the cycle bar, deduplicated and in priority order."""
    inp = as_setup_input(source)
    found = [candidate for name, detector in detectors for candidate in _run(name, detector, inp)]
    return _dedupe(sorted(found, key=_sort_key))


def detect_candidates(
    source: SetupSource,
    limit: int = MAX_CANDIDATES,
    detectors: tuple[tuple[SetupName, Detector], ...] = DETECTORS,
) -> tuple[Candidate, ...]:
    """The first `limit` (1..MAX_CANDIDATES) candidates of `detect_all`."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_CANDIDATES:
        raise ValueError(f"limit must be an int in [1, {MAX_CANDIDATES}]")
    return detect_all(source, detectors)[:limit]
