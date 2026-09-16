"""
Displacement bar through a higher-timeframe level (kn/06 §1, first priority in kn/15 §6).

Trigger: the M15 bar that just closed is wide (range >= 2 x the same-slot true range),
mostly body (>= 70%), and its close went through a prior-day extreme or a confirmed
H1 pivot by at least 3 spreads. The candidate continues in the bar's direction.

Entry: limit at 50% of the trigger body. Invalidation: beyond the bar's origin
extreme by the stop buffer, or 1 x ATR(14, M5) from entry, whichever is wider.

Tick-volume z >= 1.5 is part of the kn/06 trigger but is only recorded here: the
feed's tick volume is a quote count, not volume (kn/11), and a 100-bar M15 baseline
smears sessions the same way ATR(14) does (kn/02 §1).
"""

from __future__ import annotations

from typing import Final

from ..cycle_codes import SetupName
from ..market.features import body_ratio, tick_volume_z
from ..types import Bar, Candidate, Side
from .base import (
    BODY_RETRACE, CF_ATR_M5, CF_BAR_RANGE, CF_BODY_RATIO, CF_CLOSE_BEYOND, CF_LEVEL_PRICE,
    CF_RANGE_VOL, CF_STOP_BUFFER, CF_VOL_UNIT, DIRECTION, RC_CONFIRMED_CLOSE, STOP_MIN_ATR,
    Level, SetupInput, SetupSource, as_setup_input, atr_m5, bias_codes, build_candidate,
    crossed_levels, htf_structure, level_codes, pick_level, reference_levels, side_of,
    stop_buffer, vol_unit_m15, wider_stop,
)

SETUP: Final[SetupName] = "displacement"

# kn/06 §1: range[i] >= 2.0 x ATR(14) of the bar's own timeframe; on M15 the unit is
# the same-slot true range (kn/02 §1).
MIN_RANGE_VOL: Final[float] = 2.0
# kn/06 §1: body[i] >= 0.70 x range[i].
MIN_BODY_RATIO: Final[float] = 0.70
# kn/06 §1: tick-volume z-score >= +1.5 against a rolling 100-bar window.
ACTIVITY_Z_MIN: Final[float] = 1.5
TV_Z_WINDOW: Final[int] = 100

RC_STRONG_DISPLACEMENT: Final[str] = "STRONG_DISPLACEMENT"
RC_ACTIVITY_HIGH: Final[str] = "ACTIVITY_HIGH"
CF_TV_Z: Final[str] = "tick_volume_z"


def detect(source: SetupSource) -> tuple[Candidate, ...]:
    """At most one continuation candidate for the trigger bar."""
    inp = as_setup_input(source)
    trigger, previous = inp.trigger, inp.previous
    if trigger is None or previous is None:
        return ()
    side = side_of(trigger)
    vol = vol_unit_m15(inp)
    if side is None or vol is None:
        return ()
    unit, vol_code = vol
    if trigger.range / unit < MIN_RANGE_VOL or body_ratio(trigger) < MIN_BODY_RATIO:
        return ()
    crossed = crossed_levels(reference_levels(inp), side, previous.c, trigger.c,
                             inp.close_buffer)
    if not crossed:
        return ()
    level = pick_level(crossed, trigger.c)
    codes = (*level_codes(level, crossed), vol_code)
    candidate = _candidate(inp, trigger, side, level, unit, codes)
    return () if candidate is None else (candidate,)


def _invalidation(inp: SetupInput, trigger: Bar, side: Side, entry: float,
                  atr5: float | None) -> tuple[float, float]:
    """(stop price, buffer): beyond the origin extreme, or 1 ATR(M5) from entry."""
    sign = DIRECTION[side]
    buffer = stop_buffer(inp, atr5)
    origin = trigger.l if side == "buy" else trigger.h
    structural = origin - sign * buffer
    if atr5 is None:
        return structural, buffer
    return wider_stop(side, structural, entry - sign * STOP_MIN_ATR * atr5), buffer


def _candidate(inp: SetupInput, trigger: Bar, side: Side, level: Level, unit: float,
               level_and_vol_codes: tuple[str, ...]) -> Candidate | None:
    atr5 = atr_m5(inp)
    entry = trigger.o + BODY_RETRACE * (trigger.c - trigger.o)
    invalidation, buffer = _invalidation(inp, trigger, side, entry, atr5)
    tv_z = tick_volume_z(inp.series("M15")[-(TV_Z_WINDOW + 1):], TV_Z_WINDOW)
    activity = (RC_ACTIVITY_HIGH,) if tv_z is not None and tv_z >= ACTIVITY_Z_MIN else ()
    features = {
        CF_RANGE_VOL: trigger.range / unit,
        CF_BODY_RATIO: body_ratio(trigger),
        CF_VOL_UNIT: unit,
        CF_BAR_RANGE: trigger.range,
        CF_LEVEL_PRICE: level.price,
        CF_CLOSE_BEYOND: DIRECTION[side] * (trigger.c - level.price),
        CF_ATR_M5: atr5,
        CF_STOP_BUFFER: buffer,
        CF_TV_Z: tv_z,
    }
    codes = (RC_CONFIRMED_CLOSE, RC_STRONG_DISPLACEMENT, *level_and_vol_codes, *activity,
             *bias_codes(htf_structure(inp), side))
    return build_candidate(inp, setup=SETUP, side=side, entry=entry,
                           invalidation=invalidation, features=features, reason_codes=codes)
