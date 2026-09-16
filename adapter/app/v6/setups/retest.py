"""
Retest continuation of an opening-range break (kn/06 §3, third priority in kn/15 §6).

Definitions, for a completed opening range of width W (see `orb`) and side s
(+1 buy through the high, -1 sell through the low):

* break bar  - the first M15 close >= 3 spreads beyond the edge after the range ended;
* leg        - the break bar and every bar after it, up to (not including) the trigger;
* extension  - how far the leg reached, in range widths from the FAR edge:
               s x (peak - far_edge) / W. 1.0x is the edge itself, 1.35x is 0.35 W past it;
* retest bar - the trigger: its wick comes back to within 3 spreads of the edge, it
               closes in the trade direction (close beyond open) and still >= 3
               spreads past the edge (closing back through the level voids the setup);
* first      - no leg bar after the break bar had already come back to the edge.

The candidate fires only when 1.0 <= extension <= 1.35 (kn/06 §3: past ~1.4x fading
the retest is the better side) and within 8 M15 bars of the range end, roughly the
first third of the London / New York session where continuation dominates.

Entry: limit at the retest bar's close (the kn/06 §3 aggressive entry, as a limit).
Invalidation: beyond the retest extreme by the stop buffer, or 1 x ATR(14, M5)
beyond the edge, whichever is wider.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from ..cycle_codes import SetupName
from ..types import Bar, Candidate, Side
from .base import (
    CF_ATR_M5, CF_CLOSE_BEYOND, CF_LEVEL_PRICE, CF_STOP_BUFFER, DIRECTION, RC_CONFIRMED_CLOSE,
    SIDES, STOP_MIN_ATR, SetupInput, SetupSource, as_setup_input, atr_m5, bias_codes,
    build_candidate, htf_structure, stop_buffer, wider_stop,
)
from .orb import (
    CF_BARS_SINCE_RANGE, CF_RANGE_HIGH, CF_RANGE_LOW, CF_RANGE_WIDTH, RangeBox,
    bars_after_range, bars_since_range, closes_beyond, opening_ranges,
)

SETUP: Final[SetupName] = "retest"

# kn/06 §3: retest-and-continue beats reversal only for extensions of ~1.0x-1.35x.
MIN_EXTENSION: Final[float] = 1.0
MAX_EXTENSION: Final[float] = 1.35
# kn/06 §3: no source fixes an expiry, so it is fixed in advance: 8 M15 bars (2 h)
# after the range, the early part of the session (58% continue vs 23% late).
WINDOW_BARS: Final[int] = 8
# kn/06 §3: fast extensions continue 62% vs 42% for slow grinds. "Fast" = the peak
# came within 2 bars of the break; recorded, not filtered (the source gives no cut).
FAST_EXTENSION_BARS: Final[int] = 2

RC_CLEAN_RETEST: Final[str] = "CLEAN_RETEST"
RC_FAST_EXTENSION: Final[str] = "FAST_EXTENSION"
RC_SLOW_EXTENSION: Final[str] = "SLOW_EXTENSION"

CF_EXTENSION: Final[str] = "extension"
CF_EXTENSION_BARS: Final[str] = "extension_bars"
CF_BARS_SINCE_BREAK: Final[str] = "bars_since_break"
CF_RETEST_EXTREME: Final[str] = "retest_extreme"


def detect(source: SetupSource) -> tuple[Candidate, ...]:
    """First retest of each broken range edge, inside the retest window."""
    inp = as_setup_input(source)
    trigger = inp.trigger
    if trigger is None:
        return ()
    found = (
        _retest_candidate(inp, box, trigger, side)
        for box in opening_ranges(inp)
        if bars_since_range(inp, box) < WINDOW_BARS
        for side in SIDES
    )
    return tuple(candidate for candidate in found if candidate is not None)


def _against_extreme(bar: Bar, side: Side) -> float:
    """The bar's price furthest against the trade (low for a buy)."""
    return bar.l if side == "buy" else bar.h


def _touches(bar: Bar, edge: float, side: Side, buffer: float) -> bool:
    return DIRECTION[side] * (_against_extreme(bar, side) - edge) <= buffer


def _is_rejection(bar: Bar, edge: float, side: Side, buffer: float) -> bool:
    sign = DIRECTION[side]
    return (_touches(bar, edge, side, buffer) and sign * (bar.c - bar.o) > 0
            and sign * (bar.c - edge) >= buffer)


def _break_leg(inp: SetupInput, box: RangeBox, side: Side) -> tuple[Bar, ...] | None:
    """Break bar onwards, or None without a break or with an earlier retest."""
    buffer = inp.close_buffer
    after = bars_after_range(inp, box)
    start = next((i for i, bar in enumerate(after) if closes_beyond(bar, box, side, buffer)),
                 None)
    if start is None:
        return None
    leg = after[start:]
    edge = box.edge(side)
    if any(_touches(bar, edge, side, buffer) for bar in leg[1:]):
        return None
    return leg


def _peak(leg: tuple[Bar, ...], side: Side) -> tuple[int, float]:
    """(index, price) of the leg's furthest excursion in the trade direction."""
    prices = [bar.h if side == "buy" else bar.l for bar in leg]
    best = max(prices) if side == "buy" else min(prices)
    return prices.index(best), best


def _retest_candidate(inp: SetupInput, box: RangeBox, trigger: Bar,
                      side: Side) -> Candidate | None:
    edge = box.edge(side)
    if not _is_rejection(trigger, edge, side, inp.close_buffer):
        return None
    leg = _break_leg(inp, box, side)
    if leg is None:
        return None
    sign = DIRECTION[side]
    peak_index, peak = _peak(leg, side)
    extension = sign * (peak - box.far_edge(side)) / box.width
    if not MIN_EXTENSION <= extension <= MAX_EXTENSION:
        return None
    speed = RC_FAST_EXTENSION if peak_index <= FAST_EXTENSION_BARS else RC_SLOW_EXTENSION
    return _build(inp, box, trigger, side, len(leg),
                  {CF_EXTENSION: extension, CF_EXTENSION_BARS: float(peak_index)},
                  (RC_CLEAN_RETEST, RC_CONFIRMED_CLOSE, box.code, speed))


def _build(inp: SetupInput, box: RangeBox, trigger: Bar, side: Side, leg_bars: int,
           leg_features: Mapping[str, float], leg_codes: tuple[str, ...]) -> Candidate | None:
    sign, edge = DIRECTION[side], box.edge(side)
    atr5 = atr_m5(inp)
    stop_buf = stop_buffer(inp, atr5)
    extreme = _against_extreme(trigger, side)
    structural = extreme - sign * stop_buf
    invalidation = (structural if atr5 is None
                    else wider_stop(side, structural, edge - sign * STOP_MIN_ATR * atr5))
    features = {
        **leg_features,
        CF_RANGE_HIGH: box.high, CF_RANGE_LOW: box.low, CF_RANGE_WIDTH: box.width,
        CF_LEVEL_PRICE: edge, CF_CLOSE_BEYOND: sign * (trigger.c - edge),
        CF_RETEST_EXTREME: extreme, CF_BARS_SINCE_BREAK: float(leg_bars),
        CF_BARS_SINCE_RANGE: float(bars_since_range(inp, box)),
        CF_ATR_M5: atr5, CF_STOP_BUFFER: stop_buf,
    }
    codes = (*leg_codes, *bias_codes(htf_structure(inp), side))
    return build_candidate(inp, setup=SETUP, side=side, entry=trigger.c,
                           invalidation=invalidation, features=features,
                           reason_codes=codes, variant=box.variant)
