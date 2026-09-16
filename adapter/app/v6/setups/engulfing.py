"""
Engulfing bar at a higher-timeframe level (kn/06 §4, last priority in kn/15 §6).

Shadow weight: every candidate carries SHADOW_WEIGHT. Engulfing is the only reversal
pattern with a peer-reviewed signal, but that signal was measured against the next
Open/High/Low, not the Close (kn/04, Heinz et al. 2021), so it is detected and labelled
for measurement, and the registry ranks it last.

Trigger (bullish; bearish is the mirror): the previous M15 bar is bearish and the
trigger bullish, the trigger body is larger, the trigger closes above the previous
open, its range is >= 1.3 x the same-slot true range, its low is within 0.5 x that unit
of a level the previous bar opened above (price came into the level from outside), and
it closes >= 3 spreads above the level.

Entry: limit at 50% of the trigger body. kn/06 §4 enters on a stop order 1 tick past
the high; V6 sends limit orders only (plan §5, kn/15 §2), so the displacement rule's
retracement limit is used instead. Invalidation: beyond the lower of the two lows by
max(3 x spread, 0.15 x ATR(14, M5)) (kn/06 §4).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from ..cycle_codes import SetupName
from ..market.features import body_ratio
from ..types import Bar, Candidate, Side
from .base import (
    BODY_RETRACE, CF_ATR_M5, CF_BAR_RANGE, CF_BODY_RATIO, CF_CLOSE_BEYOND, CF_LEVEL_PRICE,
    CF_RANGE_VOL, CF_STOP_BUFFER, CF_VOL_UNIT, DIRECTION, RC_CONFIRMED_CLOSE, Level,
    SetupInput, SetupSource, as_setup_input, atr_m5, bias_codes, build_candidate,
    htf_structure, level_codes, pick_level, reference_levels, side_of, stop_buffer,
    vol_unit_m15, wider_stop,
)

SETUP: Final[SetupName] = "engulfing"

# kn/06 §4: range[i] >= 1.3 x ATR(14); the M15 unit is the same-slot true range (kn/02 §1).
MIN_RANGE_VOL: Final[float] = 1.3
# kn/06 §4: the trigger's extreme lies within 0.5 x ATR of the level.
MAX_LEVEL_DISTANCE_VOL: Final[float] = 0.5

RC_SHADOW_WEIGHT: Final[str] = "SHADOW_WEIGHT"
CF_PREV_BODY: Final[str] = "prev_body"
CF_LEVEL_DISTANCE: Final[str] = "level_distance_vol"

_OPPOSITE: Final[Mapping[Side, Side]] = MappingProxyType({"buy": "sell", "sell": "buy"})


def _engulfs(trigger: Bar, previous: Bar, side: Side) -> bool:
    return (side_of(previous) == _OPPOSITE[side] and trigger.body > previous.body
            and DIRECTION[side] * (trigger.c - previous.o) > 0)


def _extreme(bar: Bar, side: Side) -> float:
    """The extreme that tests the level: the low for a buy, the high for a sell."""
    return bar.l if side == "buy" else bar.h


def _levels_at(inp: SetupInput, trigger: Bar, previous: Bar, side: Side,
               unit: float) -> tuple[Level, ...]:
    sign, extreme = DIRECTION[side], _extreme(trigger, side)
    return tuple(
        level for level in reference_levels(inp)
        if abs(extreme - level.price) <= MAX_LEVEL_DISTANCE_VOL * unit
        and sign * (trigger.c - level.price) >= inp.close_buffer
        and sign * (previous.o - level.price) > 0
    )


def detect(source: SetupSource) -> tuple[Candidate, ...]:
    """At most one engulfing candidate for the trigger bar."""
    inp = as_setup_input(source)
    trigger, previous = inp.trigger, inp.previous
    if trigger is None or previous is None:
        return ()
    side = side_of(trigger)
    if side is None or not _engulfs(trigger, previous, side):
        return ()
    vol = vol_unit_m15(inp)
    if vol is None or trigger.range / vol[0] < MIN_RANGE_VOL:
        return ()
    unit, vol_code = vol
    near = _levels_at(inp, trigger, previous, side, unit)
    if not near:
        return ()
    level = pick_level(near, _extreme(trigger, side))
    codes = (*level_codes(level, near), vol_code)
    candidate = _candidate(inp, trigger, previous, side, level, unit, codes)
    return () if candidate is None else (candidate,)


def _candidate(inp: SetupInput, trigger: Bar, previous: Bar, side: Side, level: Level,
               unit: float, level_and_vol_codes: tuple[str, ...]) -> Candidate | None:
    sign = DIRECTION[side]
    atr5 = atr_m5(inp)
    buffer = stop_buffer(inp, atr5)
    origin = wider_stop(side, _extreme(trigger, side), _extreme(previous, side))
    features = {
        CF_RANGE_VOL: trigger.range / unit,
        CF_BODY_RATIO: body_ratio(trigger),
        CF_PREV_BODY: previous.body,
        CF_VOL_UNIT: unit,
        CF_BAR_RANGE: trigger.range,
        CF_LEVEL_PRICE: level.price,
        CF_LEVEL_DISTANCE: (_extreme(trigger, side) - level.price) / unit,
        CF_CLOSE_BEYOND: sign * (trigger.c - level.price),
        CF_ATR_M5: atr5,
        CF_STOP_BUFFER: buffer,
    }
    codes = (RC_SHADOW_WEIGHT, RC_CONFIRMED_CLOSE, *level_and_vol_codes,
             *bias_codes(htf_structure(inp), side))
    return build_candidate(
        inp, setup=SETUP, side=side, entry=trigger.o + BODY_RETRACE * (trigger.c - trigger.o),
        invalidation=origin - sign * buffer, features=features, reason_codes=codes)
