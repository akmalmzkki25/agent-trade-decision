"""
Deterministic Order Flow desk (plan section 2): liquidity and cost, no direction.

Spot XAUUSD has no real order flow (kn/11): the EA's DOM is synthetic and real
volume is zero, so this desk only reads cost and activity proxies and never has
a directional voice. It always prefers LIMIT orders (kn/15 §2).

- NO_TRADE (multiplier 0) on a hard breach: spread above the account ceiling,
  friction / ATR(M5) at or above MAX_FRICTION_TO_ATR_M5 (the hard cost gate), a quote gap of
  QUOTE_GAP_NO_TRADE_MS or more, no quotes at all in the tick window, or the
  rollover block itself.
- CAUTION (multiplier CAUTION_MULTIPLIER) on a soft warning: spread in the top
  of its same-hour distribution, friction from FRICTION_CAUTION_SHARE of kn/15's
  0.08 line (PREFERRED_FRICTION_TO_ATR_M5) up to the gate, a quote gap of QUOTE_GAP_CAUTION_MS or more, a thin quote rate,
  unusually low or high tick activity (unsigned), rollover starting within
  ROLLOVER_LOOKAHEAD_S, or missing cost data.
- OK (multiplier 1) otherwise.

Slippage statistics are not in the Phase 2 context, so SLIPPAGE_HIGH is never
emitted by the rules.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Final

from ..cycle_types import (
    F_DOM_SYNTHETIC, F_FRICTION_ATR, F_QUOTE_GAP_MS, F_SPREAD_PCTL_HOUR, F_SPREAD_POINTS, F_TV_Z,
    MarketContext,
)
from ..market.sessions import session_state
from ..risk.limits import (
    MAX_FRICTION_TO_ATR_M5, PREFERRED_FRICTION_TO_ATR_M5, PREFERRED_SPREAD_POINTS,
)
from ..schemas.agents import (
    MAX_NOTE_CHARS, MAX_REASON_CODES, LiquidityReason, LiquidityStance, LiquidityView,
)
from .structure import finite_feature

# kn/15's 0.08 friction line was drawn at the raw account's $0.22 friction; at the
# standard account's $0.40 the same market sits at 0.145, the hard gate (risk.limits).
# The desk therefore shrinks (CAUTION) between the two lines and vetoes only at the
# gate: a veto at 0.08 blocked every entry on quiet standard-account days. The spread
# line stays kn/15's.
DEFAULT_MAX_SPREAD_POINTS: Final[int] = PREFERRED_SPREAD_POINTS["standard"]
SPREAD_WIDE_PERCENTILE: Final[float] = 0.80
FRICTION_CAUTION_SHARE: Final[float] = 0.75
QUOTE_GAP_CAUTION_MS: Final[int] = 10_000
QUOTE_GAP_NO_TRADE_MS: Final[int] = 60_000
MIN_QUOTES_PER_SECOND: Final[float] = 0.2
ACTIVITY_LOW_Z: Final[float] = -1.0
ACTIVITY_HIGH_Z: Final[float] = 2.5
ROLLOVER_LOOKAHEAD_S: Final[int] = 3600
DOM_REAL_VALUE: Final[float] = 0.0

STANCE_MULTIPLIERS: Final[Mapping[LiquidityStance, float]] = MappingProxyType(
    {"OK": 1.0, "CAUTION": 0.5, "NO_TRADE": 0.0})
CAUTION_MULTIPLIER: Final[float] = STANCE_MULTIPLIERS["CAUTION"]

Codes = tuple[LiquidityReason, ...]


def _unique(codes: Iterable[LiquidityReason]) -> Codes:
    return tuple(dict.fromkeys(codes))


def spread_points(context: MarketContext) -> float:
    """The live spread: the feature when present, else the snapshot quote."""
    value = finite_feature(context.features, F_SPREAD_POINTS)
    return float(context.quote.spread_points) if value is None else value


def quote_gap_ms(context: MarketContext) -> float:
    value = finite_feature(context.features, F_QUOTE_GAP_MS)
    return float(context.ticks.max_gap_ms) if value is None else value


def dom_is_synthetic(context: MarketContext) -> bool:
    """True unless the feature or the probe positively says the DOM is real."""
    value = finite_feature(context.features, F_DOM_SYNTHETIC)
    if value is not None:
        return value != DOM_REAL_VALUE
    return context.probe is None or context.probe.dom_synthetic


def _quote_rate(context: MarketContext) -> float | None:
    ticks = context.ticks
    return ticks.quote_count / ticks.window_s if ticks.window_s > 0 else None


def hard_breaches(context: MarketContext, max_spread_points: int) -> Codes:
    friction = finite_feature(context.features, F_FRICTION_ATR)
    quotes_missing = context.ticks.window_s > 0 and context.ticks.quote_count == 0
    checks: tuple[tuple[bool, LiquidityReason], ...] = (
        (spread_points(context) > max_spread_points, "SPREAD_WIDE"),
        (friction is not None and friction >= MAX_FRICTION_TO_ATR_M5, "FRICTION_HIGH"),
        (quote_gap_ms(context) >= QUOTE_GAP_NO_TRADE_MS, "QUOTE_GAP"),
        (quotes_missing, "QUOTES_THIN"),
        (context.session.rollover_block, "ROLLOVER_NEAR"),
    )
    return _unique(code for failed, code in checks if failed)


def _cost_warnings(context: MarketContext) -> Codes:
    friction = finite_feature(context.features, F_FRICTION_ATR)
    percentile = finite_feature(context.features, F_SPREAD_PCTL_HOUR)
    checks: tuple[tuple[bool, LiquidityReason], ...] = (
        (percentile is not None and percentile >= SPREAD_WIDE_PERCENTILE, "SPREAD_WIDE"),
        (friction is not None
         and friction >= FRICTION_CAUTION_SHARE * PREFERRED_FRICTION_TO_ATR_M5, "FRICTION_HIGH"),
        (friction is None or percentile is None, "DATA_MISSING"),
    )
    return _unique(code for warned, code in checks if warned)


def _activity_warnings(context: MarketContext) -> Codes:
    rate = _quote_rate(context)
    activity = finite_feature(context.features, F_TV_Z)
    rollover_soon = session_state(context.as_of_epoch + ROLLOVER_LOOKAHEAD_S).rollover_block
    checks: tuple[tuple[bool, LiquidityReason], ...] = (
        (quote_gap_ms(context) >= QUOTE_GAP_CAUTION_MS, "QUOTE_GAP"),
        (rate is not None and rate < MIN_QUOTES_PER_SECOND, "QUOTES_THIN"),
        (activity is not None and activity <= ACTIVITY_LOW_Z, "ACTIVITY_LOW"),
        (activity is not None and activity >= ACTIVITY_HIGH_Z, "ACTIVITY_HIGH"),
        (rollover_soon, "ROLLOVER_NEAR"),
    )
    return _unique(code for warned, code in checks if warned)


def soft_warnings(context: MarketContext) -> Codes:
    return _unique(_cost_warnings(context) + _activity_warnings(context))


def _info_codes(context: MarketContext, flagged: Codes) -> Codes:
    normal = () if "SPREAD_WIDE" in flagged else ("SPREAD_NORMAL",)
    synthetic = ("DOM_SYNTHETIC",) if dom_is_synthetic(context) else ()
    return normal + synthetic


def check_spread_ceiling(max_spread_points: int) -> None:
    """Raise ValueError unless the ceiling is a positive int."""
    is_int = isinstance(max_spread_points, int) and not isinstance(max_spread_points, bool)
    if not is_int or max_spread_points <= 0:
        raise ValueError("max_spread_points must be a positive int")


def _stance(hard: Codes, soft: Codes) -> LiquidityStance:
    if hard:
        return "NO_TRADE"
    return "CAUTION" if soft else "OK"


def liquidity_view(context: MarketContext, *,
                   max_spread_points: int = DEFAULT_MAX_SPREAD_POINTS) -> LiquidityView:
    """The rules Order Flow view. `max_spread_points`: the account's spread ceiling."""
    check_spread_ceiling(max_spread_points)
    hard = hard_breaches(context, max_spread_points)
    soft = soft_warnings(context)
    stance = _stance(hard, soft)
    flagged = _unique(hard + soft)
    note = (f"{stance}: spread {spread_points(context):.0f}pt (max {max_spread_points}), "
            f"quote gap {quote_gap_ms(context):.0f}ms, DOM ignored")
    return LiquidityView(
        stance=stance, size_multiplier=STANCE_MULTIPLIERS[stance], order_style="LIMIT",
        reason_codes=_unique(flagged + _info_codes(context, flagged))[:MAX_REASON_CODES],
        note=note[:MAX_NOTE_CHARS],
    )
