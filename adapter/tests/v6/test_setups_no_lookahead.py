"""
No look-ahead (plan §12): candidates for an as-of time may not depend on any bar that
had not closed by then, and levels may not depend on anything after the trigger bar
opened. Pivots only count once their confirmation lag has passed (kn/05 §11, kn/06 §7).

The property tests run on a seeded multi-timeframe random walk (M5 with jumps,
aggregated to M15/H1/D1) that is known to exercise all four detectors.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping
from dataclasses import replace

import pytest

from app.v6.cycle_codes import SETUP_NAMES, candidate_id_for
from app.v6.market.levels import confirmed_pivots
from app.v6.setups import (
    SetupInput, detect_all, displacement, htf_structure, reference_levels, vol_unit_m15,
)
from app.v6.types import TIMEFRAME_SECONDS, Bar, Candidate

from .setup_fixtures_v6 import (
    DAY, DIGITS, HOUR, M15, POINT, SPREAD, WED, bar, flat, h1_swing_high, m5_jump_walk,
    multi_timeframe, setup_input,
)

SEED = 3                            # chosen because it yields all four setups
WALK_DAYS = 12
START = WED - 16 * DAY              # Monday 2026-08-31 00:00 UTC
M5_PER_DAY = DAY // 300
DATA = multi_timeframe(m5_jump_walk(WALK_DAYS * M5_PER_DAY, seed=SEED, start=START))
AS_OFS = tuple(b.t + M15 for b in DATA["M15"][DAY // M15:])
SAMPLE = 120

Bars = Mapping[str, tuple[Bar, ...]]
Run = dict[int, tuple[Candidate, ...]]


def _inp(data: Bars, as_of: int) -> SetupInput:
    return SetupInput(as_of_epoch=as_of, bars=data, spread_price=SPREAD, point=POINT,
                      digits=DIGITS)


def _closed(data: Bars, cutoff: int) -> dict[str, tuple[Bar, ...]]:
    """What a live system holds at `cutoff`: bars whose close is not after it."""
    return {tf: tuple(b for b in rows if b.t + TIMEFRAME_SECONDS[tf] <= cutoff)
            for tf, rows in data.items()}


def _shock(item: Bar) -> Bar:
    return replace(item, o=item.c, c=item.o, h=item.h + 50.0, l=item.l - 50.0,
                   tv=item.tv * 20)


def _shock_unclosed(data: Bars, as_of: int) -> dict[str, tuple[Bar, ...]]:
    """Rewrite every bar still open at `as_of` (forming H1/D1 bars included)."""
    return {tf: tuple(b if b.t + TIMEFRAME_SECONDS[tf] <= as_of else _shock(b) for b in rows)
            for tf, rows in data.items()}


@pytest.fixture(scope="module")
def full_run() -> Run:
    return {as_of: detect_all(_inp(DATA, as_of)) for as_of in AS_OFS}


def _checked_as_ofs(run: Run) -> list[int]:
    with_hits = [as_of for as_of, found in run.items() if found]
    rng = random.Random(SEED)
    return sorted(set(with_hits) | set(rng.sample(AS_OFS, SAMPLE)))


# --- the walk is a meaningful test bed ----------------------------------------------------

def test_walk_exercises_every_setup_with_sane_candidates(full_run: Run) -> None:
    found = [(as_of, c) for as_of, cands in full_run.items() for c in cands]
    assert {c.setup for _, c in found} == set(SETUP_NAMES), "pick another SEED"
    ids = [c.candidate_id for _, c in found]
    assert len(ids) == len(set(ids))
    for as_of, cand in found:
        sign = 1 if cand.side == "buy" else -1
        assert cand.bar_t == as_of - M15
        assert cand.candidate_id.startswith(candidate_id_for(cand.setup, cand.side, cand.bar_t))
        assert sign * (cand.entry - cand.invalidation) > 0
        assert all(math.isfinite(v) for v in cand.features.values())


# --- no look-ahead -----------------------------------------------------------------------

def test_appending_future_bars_never_changes_earlier_candidates(full_run: Run) -> None:
    rng = random.Random(SEED + 1)
    for as_of in _checked_as_ofs(full_run):
        later = min(AS_OFS[-1], as_of + rng.randrange(0, 3 * DAY, M15))
        assert detect_all(_inp(_closed(DATA, as_of), as_of)) == full_run[as_of]
        assert detect_all(_inp(_closed(DATA, later), as_of)) == full_run[as_of]


def test_rewriting_bars_not_yet_closed_changes_nothing(full_run: Run) -> None:
    for as_of in _checked_as_ofs(full_run):
        assert detect_all(_inp(_shock_unclosed(DATA, as_of), as_of)) == full_run[as_of]


def test_trigger_bar_cannot_shape_its_own_context(full_run: Run) -> None:
    for as_of in _checked_as_ofs(full_run)[::4]:
        bar_open = as_of - M15
        live = _inp(DATA, as_of)
        before = _inp({**_closed(DATA, bar_open), "M5": live.series("M5")}, as_of)
        assert reference_levels(live) == reference_levels(before)
        assert htf_structure(live) == htf_structure(before)
        assert vol_unit_m15(live) == vol_unit_m15(before)
        assert all(level.known_at <= bar_open for level in reference_levels(live))


# --- pivot confirmation lag ---------------------------------------------------------------

PIVOT_PRICE = 4310.0
PIVOT_CONFIRMED_AT = WED + 5 * HOUR        # bar at 04:00 (2 to the right) closes at 05:00


def _pivot_case(trigger_t: int) -> SetupInput:
    m15 = flat(WED, trigger_t, price=4305.0) + (bar(trigger_t, 4305.0, 4313.5, 4304.5, 4313.0),)
    return setup_input(trigger_t + M15, M15=m15, H1=h1_swing_high(WED, PIVOT_PRICE))


def test_swing_fixture_confirms_where_expected() -> None:
    pivots = confirmed_pivots(h1_swing_high(WED, PIVOT_PRICE), 2, HOUR)
    assert [(p.kind, p.price, p.confirmed_at) for p in pivots] == [
        ("high", PIVOT_PRICE, PIVOT_CONFIRMED_AT)]


@pytest.mark.parametrize(("trigger_t", "expected"), [
    (PIVOT_CONFIRMED_AT - 2 * M15, False),   # confirming H1 bar still open at the close
    (PIVOT_CONFIRMED_AT - M15, False),       # confirmed exactly at the close: too late
    (PIVOT_CONFIRMED_AT, True),              # confirmed when the trigger opened
    (PIVOT_CONFIRMED_AT + 5 * HOUR, True),   # still inside the 48 h level lookback
])
def test_pivot_level_respects_confirmation_lag(trigger_t: int, expected: bool) -> None:
    inp = _pivot_case(trigger_t)
    found = displacement.detect(inp)
    assert bool(found) is expected
    if expected:
        assert found[0].reason_codes[2] == "LEVEL_H1_PIVOT_HIGH"
        assert found[0].features["level_price"] == PIVOT_PRICE
    levels = reference_levels(inp)
    assert (PIVOT_PRICE in {lv.price for lv in levels}) is (trigger_t >= PIVOT_CONFIRMED_AT)


def test_pivot_level_expires_after_the_lookback() -> None:
    late = PIVOT_CONFIRMED_AT + 2 * DAY
    m15 = flat(late - DAY, late, price=4305.0) + (bar(late, 4305.0, 4313.5, 4304.5, 4313.0),)
    inp = setup_input(late + M15, M15=m15, H1=h1_swing_high(WED, PIVOT_PRICE))
    assert reference_levels(inp) == ()
    assert displacement.detect(inp) == ()
