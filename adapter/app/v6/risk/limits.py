"""
Hard risk ceilings for V6.

These are code constants on purpose. Configuration may only make them stricter;
`app.v6.config.V6Settings` rejects any value that would loosen one. Each number
traces to the evidence review in `knowledge/`.
"""

from __future__ import annotations

from typing import Final

# kn/09 §6.2: ruin probability rises ~120x from 1% to 5% per trade.
MAX_RISK_PCT_CEILING: Final[float] = 1.0

# kn/09 §3.4: 10:1 notional is the working cap (ESMA's 20:1 is the outer bound).
MAX_NOTIONAL_RATIO: Final[float] = 10.0

# kn/07 §2: below 600 points ($6.00) friction eats the realistic intraday edge.
MIN_STOP_FLOOR_POINTS: Final[int] = 600

# kn/01 §3: stop must be at least 10x the live spread.
MIN_STOP_SPREAD_MULTIPLE: Final[float] = 10.0

# kn/01 §3: friction / stop distance must stay at or below 10%.
MAX_FRICTION_TO_STOP: Final[float] = 0.10

# kn/15 gate table: friction / ATR(M5) must stay below 8% for the hour.
MAX_FRICTION_TO_ATR_M5: Final[float] = 0.08

# kn/15 gate table: ATR(14, M5) must be at least 250 points.
MIN_ATR_M5_POINTS: Final[int] = 250

# kn/01 §2 / kn/15: spread gate by account type, in points.
MAX_SPREAD_POINTS: Final[dict[str, int]] = {"standard": 35, "raw": 20}

# kn/01 §2: round-trip friction per ounce, in price units.
FRICTION_PRICE: Final[dict[str, float]] = {"standard": 0.40, "raw": 0.22}

# kn/09 §6.4: nested circuit breakers, percent of equity.
MAX_DAILY_LOSS_PCT: Final[float] = 3.0
MAX_WEEKLY_LOSS_PCT: Final[float] = 6.0
MAX_MONTHLY_LOSS_PCT: Final[float] = 10.0

# kn/08 §10: time barrier; 4 hours is the ceiling for M15 intraday.
MAX_TIME_BARRIER_S: Final[int] = 4 * 3600

# V6.0 carries at most one position (no layering, user decision).
MAX_OPEN_POSITIONS: Final[int] = 1

# Plan section 11, phase 5: demo execution trades the minimum lot, never more.
MAX_EXECUTE_LOTS: Final[float] = 0.01

# Plan section 5: the V6 magic range (250570 is the default; the rest are reserved).
V6_MAGIC_FIRST: Final[int] = 250570
V6_MAGIC_LAST: Final[int] = 250579

# Fraction of free margin a new position may consume.
MAX_MARGIN_USE: Final[float] = 0.25

# Tolerance for tick_value vs tick_size x contract_size before trading is refused.
SPEC_TOLERANCE: Final[float] = 0.02
