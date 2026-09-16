"""
Session opening-range break (kn/06 §2, second priority in kn/15 §6).

Ranges: London 08:00-08:30 London time and New York 09:30-10:00 New York time, from
`market.sessions.opening_range_windows` (exchange-local clocks, DST-safe; kn/03).
Both are two complete M15 bars.

Trigger: the M15 bar that just closed is the FIRST close at least 3 spreads beyond a
range edge since the range ended (kn/06 §2: confirm on a close, never a wick), and it
closed within 4 bars of the range end. Each side of each range fires at most once, so
a later double break is a new candidate on the other side.

Entry: limit halfway between the broken edge and the breakout close (kn/06 §1: do not
pay the whole move). Invalidation: beyond the opposite edge - a failed break cascades
through the far side 80-99% of the time (kn/06 §2), so that is where the break is wrong.

The range width against ATR(14, D1) is recorded (OR_WIDE / OR_NARROW), not filtered:
the ES/NQ width result has never been measured on gold (kn/06 §2).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from ..cycle_codes import SetupName
from ..market.sessions import (
    LONDON_OPEN_RANGE_NAME, NY_OPEN_RANGE_NAME, OpeningRange, opening_range_windows,
)
from ..types import Bar, Candidate, Side
from .base import (
    CF_ATR_M5, CF_CLOSE_BEYOND, CF_LEVEL_PRICE, CF_STOP_BUFFER, DIRECTION, M15_S,
    RC_CONFIRMED_CLOSE, SIDES, SetupInput, SetupSource, as_setup_input, atr_d1, atr_m5,
    between, bias_codes, build_candidate, htf_structure, stop_buffer,
)

SETUP: Final[SetupName] = "orb"

# kn/06 §2: 74% of 30-minute opening-range breaks come within 15 minutes; a break an
# hour later is another population. Fixed before any result was seen (kn/06 §3).
BREAK_WINDOW_BARS: Final[int] = 4
# kn/06 §2: range width is the best single filter - narrow < 0.3 x ATR, wide > 0.6 x ATR.
NARROW_RANGE_ATR: Final[float] = 0.3
WIDE_RANGE_ATR: Final[float] = 0.6
# kn/06 §1 applied to breaks: rest the limit halfway back to the broken edge.
ENTRY_RETRACE: Final[float] = 0.5

RANGE_VARIANTS: Final[Mapping[str, str]] = MappingProxyType(
    {LONDON_OPEN_RANGE_NAME: "lon", NY_OPEN_RANGE_NAME: "ny"})
RANGE_CODES: Final[Mapping[str, str]] = MappingProxyType(
    {LONDON_OPEN_RANGE_NAME: "OR_LONDON", NY_OPEN_RANGE_NAME: "OR_NY"})
RC_RANGE_WIDE: Final[str] = "OR_WIDE"
RC_RANGE_NARROW: Final[str] = "OR_NARROW"

CF_RANGE_HIGH: Final[str] = "or_high"
CF_RANGE_LOW: Final[str] = "or_low"
CF_RANGE_WIDTH: Final[str] = "or_width"
CF_RANGE_WIDTH_ATR: Final[str] = "or_width_atr_d1"
CF_BARS_SINCE_RANGE: Final[str] = "bars_since_range"


@dataclass(frozen=True)
class RangeBox:
    """A completed opening range: high/low of its M15 bars, `end_epoch` exclusive."""

    name: str
    start_epoch: int
    end_epoch: int
    high: float
    low: float

    @property
    def width(self) -> float:
        return self.high - self.low

    @property
    def variant(self) -> str:
        return RANGE_VARIANTS[self.name]

    @property
    def code(self) -> str:
        return RANGE_CODES[self.name]

    def edge(self, side: Side) -> float:
        """The edge a `side` break goes through."""
        return self.high if side == "buy" else self.low

    def far_edge(self, side: Side) -> float:
        return self.low if side == "buy" else self.high


def _box(window: OpeningRange, m15: tuple[Bar, ...]) -> RangeBox | None:
    rows = between(m15, window.start_epoch, window.end_epoch)
    if len(rows) != (window.end_epoch - window.start_epoch) // M15_S:
        return None
    high, low = max(bar.h for bar in rows), min(bar.l for bar in rows)
    if high <= low:
        return None
    return RangeBox(window.name, window.start_epoch, window.end_epoch, high, low)


def opening_ranges(inp: SetupInput) -> tuple[RangeBox, ...]:
    """Complete ranges of the trigger's UTC day that ended before the trigger opened."""
    m15 = inp.known_before_trigger("M15")
    boxes = (
        _box(window, m15)
        for window in opening_range_windows(inp.bar_open_epoch)
        if window.end_epoch <= inp.bar_open_epoch and window.name in RANGE_VARIANTS
    )
    return tuple(box for box in boxes if box is not None)


def bars_after_range(inp: SetupInput, box: RangeBox) -> tuple[Bar, ...]:
    """M15 bars from the range end up to, not including, the trigger bar."""
    return between(inp.series("M15"), box.end_epoch, inp.bar_open_epoch)


def bars_since_range(inp: SetupInput, box: RangeBox) -> int:
    return (inp.bar_open_epoch - box.end_epoch) // M15_S


def closes_beyond(bar: Bar, box: RangeBox, side: Side, buffer: float) -> bool:
    return DIRECTION[side] * (bar.c - box.edge(side)) >= buffer


def detect(source: SetupSource) -> tuple[Candidate, ...]:
    """First confirmed close beyond each range edge, within the break window."""
    inp = as_setup_input(source)
    trigger = inp.trigger
    if trigger is None:
        return ()
    found = (
        _break_candidate(inp, box, trigger, side)
        for box in opening_ranges(inp)
        if bars_since_range(inp, box) < BREAK_WINDOW_BARS
        for side in SIDES
    )
    return tuple(candidate for candidate in found if candidate is not None)


def _width_codes(width_atr: float | None) -> tuple[str, ...]:
    if width_atr is None:
        return ()
    if width_atr > WIDE_RANGE_ATR:
        return (RC_RANGE_WIDE,)
    return (RC_RANGE_NARROW,) if width_atr < NARROW_RANGE_ATR else ()


def _break_candidate(inp: SetupInput, box: RangeBox, trigger: Bar,
                     side: Side) -> Candidate | None:
    buffer = inp.close_buffer
    if not closes_beyond(trigger, box, side, buffer):
        return None
    if any(closes_beyond(bar, box, side, buffer) for bar in bars_after_range(inp, box)):
        return None
    sign, edge = DIRECTION[side], box.edge(side)
    atr5, daily = atr_m5(inp), atr_d1(inp)
    stop_buf = stop_buffer(inp, atr5)
    width_atr = box.width / daily if daily is not None else None
    features = {
        CF_RANGE_HIGH: box.high, CF_RANGE_LOW: box.low, CF_RANGE_WIDTH: box.width,
        CF_RANGE_WIDTH_ATR: width_atr, CF_LEVEL_PRICE: edge,
        CF_CLOSE_BEYOND: sign * (trigger.c - edge),
        CF_BARS_SINCE_RANGE: float(bars_since_range(inp, box)),
        CF_ATR_M5: atr5, CF_STOP_BUFFER: stop_buf,
    }
    codes = (RC_CONFIRMED_CLOSE, box.code, *_width_codes(width_atr),
             *bias_codes(htf_structure(inp), side))
    return build_candidate(
        inp, setup=SETUP, side=side, entry=edge + ENTRY_RETRACE * (trigger.c - edge),
        invalidation=box.far_edge(side) - sign * stop_buf, features=features,
        reason_codes=codes, variant=box.variant)
