"""
Deterministic price-action candidate detectors (plan §2 PA row, kn/06, kn/15 §6).

Each detector is a pure function of a `SetupInput` (or a `MarketContext`) that only
sees bars closed at the cycle's M15 close, and returns a tuple of frozen `Candidate`s
for that bar. Language models may rank these candidates; they never create them.

    from app.v6.setups import detect_candidates
    offered = detect_candidates(ctx)          # <= MAX_CANDIDATES, priority order
"""

from __future__ import annotations

from . import displacement, engulfing, orb, retest
from .base import (
    LEVEL_CODES,
    Level,
    SetupInput,
    SetupSource,
    as_setup_input,
    htf_structure,
    reference_levels,
    vol_unit_m15,
)
from .registry import DETECTORS, MAX_CANDIDATES, Detector, detect_all, detect_candidates

__all__ = [
    "DETECTORS",
    "Detector",
    "LEVEL_CODES",
    "Level",
    "MAX_CANDIDATES",
    "SetupInput",
    "SetupSource",
    "as_setup_input",
    "detect_all",
    "detect_candidates",
    "displacement",
    "engulfing",
    "htf_structure",
    "orb",
    "reference_levels",
    "retest",
    "vol_unit_m15",
]
