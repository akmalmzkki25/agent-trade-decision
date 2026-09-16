"""
Deterministic News/Sentiment desk (plan section 2): no direction, only risk.

Built from the code-computed CalendarAssessment and the realised-volatility
feature (kn/10: sentiment belongs in the risk layer, never the signal layer):

- BLOCK (multiplier 0) during a calendar blackout, or when the calendar feeds
  are stale (fail closed, mirroring the NEWS gate).
- CAUTION (multiplier CAUTION_MULTIPLIER) when a scheduled event is within
  NEAR_EVENT_MINUTES ahead or RECENT_EVENT_MINUTES behind, when the
  assessment carries any calendar code, when it was computed more than
  ASSESSMENT_MAX_AGE_S before this bar close ("stale-ish"), or when
  rv_ratio >= RV_ELEVATED_RATIO.
- CLEAR (multiplier 1) otherwise.

Regime: EVENT_RISK around events; QUIET with normal realised volatility;
USD_DRIVEN when volatility is elevated within USD_DRIVEN_WINDOW_MINUTES of a
USD release; UNCLEAR when volatility is elevated without one or cannot be read.
RISK_ON / RISK_OFF, HEADLINE_RISK and SAFE_HAVEN_FLOW need cross-asset or
headline data that Phase 2 does not have, so the rules never emit them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Final

from ..cycle_types import (
    CAL_POST_EVENT, CAL_POST_SURPRISE, CAL_PRE_EVENT, CAL_STALE, CAL_US_DATA_BAR, F_RV_RATIO,
    CalendarAssessment, CalendarEvent, MarketContext,
)
from ..schemas.agents import (
    ID_PATTERN, MAX_EVENT_IDS, MAX_NOTE_CHARS, MAX_REASON_CODES, NewsReason, NewsRegime,
    NewsRiskView, NewsStance,
)
from .structure import finite_feature

NEAR_EVENT_MINUTES: Final[float] = 60.0
RECENT_EVENT_MINUTES: Final[float] = 60.0
USD_DRIVEN_WINDOW_MINUTES: Final[float] = 240.0
ASSESSMENT_MAX_AGE_S: Final[int] = 900
RV_ELEVATED_RATIO: Final[float] = 2.0
SECONDS_PER_MINUTE: Final[int] = 60
USD: Final[str] = "USD"
HIGH_IMPORTANCE: Final[str] = "HIGH"

STANCE_MULTIPLIERS: Final[Mapping[NewsStance, float]] = MappingProxyType(
    {"CLEAR": 1.0, "CAUTION": 0.5, "BLOCK": 0.0})
CAUTION_MULTIPLIER: Final[float] = STANCE_MULTIPLIERS["CAUTION"]

CALENDAR_REASONS: Final[Mapping[str, NewsReason]] = MappingProxyType({
    CAL_PRE_EVENT: "EVENT_IMMINENT", CAL_POST_EVENT: "EVENT_RECENT",
    CAL_POST_SURPRISE: "SURPRISE_LARGE", CAL_US_DATA_BAR: "HIGH_IMPACT_USD",
    CAL_STALE: "CALENDAR_STALE",
})
CODE_PRIORITY: Final[tuple[NewsReason, ...]] = (
    "CALENDAR_STALE", "SURPRISE_LARGE", "EVENT_IMMINENT", "EVENT_RECENT", "HIGH_IMPACT_USD",
    "VOL_ELEVATED", "HEADLINE_RISK", "SAFE_HAVEN_FLOW", "DATA_MISSING", "NO_EVENTS",
)
_ID_RE: Final[re.Pattern[str]] = re.compile(ID_PATTERN)
_EVENT_CODES: Final[frozenset[NewsReason]] = frozenset(
    {"EVENT_IMMINENT", "EVENT_RECENT", "SURPRISE_LARGE", "HIGH_IMPACT_USD"})


def _ordered(codes: Iterable[NewsReason]) -> tuple[NewsReason, ...]:
    unique = set(codes)
    return tuple(code for code in CODE_PRIORITY if code in unique)[:MAX_REASON_CODES]


def _within(minutes: float | None, limit: float) -> bool:
    return minutes is not None and 0 <= minutes <= limit


def _calendar_codes(calendar: CalendarAssessment) -> tuple[NewsReason, ...]:
    return tuple(CALENDAR_REASONS[code] for code in calendar.codes if code in CALENDAR_REASONS)


def _timing_codes(calendar: CalendarAssessment) -> tuple[NewsReason, ...]:
    imminent = _within(calendar.next_event_minutes, NEAR_EVENT_MINUTES)
    recent = _within(calendar.last_event_minutes_ago, RECENT_EVENT_MINUTES)
    return (("EVENT_IMMINENT",) if imminent else ()) + (("EVENT_RECENT",) if recent else ())


def _is_stale_ish(context: MarketContext) -> bool:
    return context.as_of_epoch - context.calendar.as_of_epoch > ASSESSMENT_MAX_AGE_S


def _vol_codes(rv_ratio: float | None) -> tuple[NewsReason, ...]:
    if rv_ratio is None:
        return ("DATA_MISSING",)
    return ("VOL_ELEVATED",) if rv_ratio >= RV_ELEVATED_RATIO else ()


def _is_high_usd(event: CalendarEvent) -> bool:
    return event.currency == USD and event.importance == HIGH_IMPORTANCE


def _recent_usd_release(context: MarketContext) -> bool:
    window_s = USD_DRIVEN_WINDOW_MINUTES * SECONDS_PER_MINUTE
    return any(
        event.currency == USD and 0 <= context.as_of_epoch - event.time_epoch <= window_s
        for event in context.calendar.events)


def _regime(context: MarketContext, event_driven: bool, rv_ratio: float | None) -> NewsRegime:
    if event_driven:
        return "EVENT_RISK"
    if context.calendar.stale or rv_ratio is None:
        return "UNCLEAR"
    if rv_ratio < RV_ELEVATED_RATIO:
        return "QUIET"
    return "USD_DRIVEN" if _recent_usd_release(context) else "UNCLEAR"


def offered_event_ids(context: MarketContext) -> tuple[str, ...]:
    """Well-formed event ids, nearest in time first, at most MAX_EVENT_IDS."""
    events = sorted(context.calendar.events,
                    key=lambda event: (abs(event.time_epoch - context.as_of_epoch), event.event_id))
    ids = (event.event_id for event in events if _ID_RE.match(event.event_id))
    return tuple(dict.fromkeys(ids))[:MAX_EVENT_IDS]


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def _stance(block: bool, caution_codes: tuple[NewsReason, ...]) -> NewsStance:
    if block:
        return "BLOCK"
    return "CAUTION" if caution_codes else "CLEAR"


def news_risk_view(context: MarketContext) -> NewsRiskView:
    """The rules News/Sentiment view for one cycle."""
    calendar = context.calendar
    rv_ratio = finite_feature(context.features, F_RV_RATIO)
    stale_ish = ("CALENDAR_STALE",) if calendar.stale or _is_stale_ish(context) else ()
    event_codes = _calendar_codes(calendar) + _timing_codes(calendar)
    vol_codes = _vol_codes(rv_ratio)
    caution = event_codes + stale_ish + tuple(c for c in vol_codes if c != "DATA_MISSING")
    stance = _stance(calendar.blackout or calendar.stale, caution)
    event_driven = bool(_EVENT_CODES.intersection(event_codes)) or (
        calendar.blackout and not calendar.stale)
    info: tuple[NewsReason, ...] = (
        (("HIGH_IMPACT_USD",) if any(map(_is_high_usd, calendar.events)) else ())
        + (("NO_EVENTS",) if not calendar.events and not calendar.stale else ())
    )
    note = (f"{stance}: next event in {_fmt(calendar.next_event_minutes)} min, "
            f"last {_fmt(calendar.last_event_minutes_ago)} min ago, rv_ratio {_fmt(rv_ratio)}")
    return NewsRiskView(
        stance=stance, size_multiplier=STANCE_MULTIPLIERS[stance],
        regime=_regime(context, event_driven, rv_ratio),
        event_ids=offered_event_ids(context),
        reason_codes=_ordered(caution + vol_codes + info),
        note=note[:MAX_NOTE_CHARS],
    )
