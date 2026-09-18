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

# Hard cost gate: friction / ATR(M5) must stay below this. kn/15's "trade only hours
# under 0.08" comes from the kn/01 §4 table, which prices friction at the raw account's
# $0.22; a standard account pays $0.40 in the same market (1.82x), so the equivalent
# line is 0.08 x 1.82 = 0.145. At $0.40 the gate blocks ATR(M5) < $2.67: bars where the
# design minimum (the $6 stop floor with a 2R target, $12) is out of reach within the
# 2 h barrier (ATR(M5) x sqrt(24) < $12 below ATR $2.45). Measured on MetaQuotes-Demo
# (2026-09, all hours): friction / ATR(M5) p50 0.087, p90 0.110.
MAX_FRICTION_TO_ATR_M5: Final[float] = 0.15
# kn/15's original line, now a quality mark (packet flag, rules desks), not a gate.
PREFERRED_FRICTION_TO_ATR_M5: Final[float] = 0.08

# kn/15 gate table: ATR(14, M5) must be at least 250 points ($2.50). London + New York
# bars measured $4.70 (p25) / $6.13 (p50) in 11-17 UTC, so the floor only stops dead markets.
MIN_ATR_M5_POINTS: Final[int] = 250

# Hard spread gate by account type, in points. Standard: MetaQuotes-Demo measured p50 27
# and p95 38 (2026-09-16); kn/01 §2 gives 25-40 in London/NY, 40-80 around the rollover
# and 30-60+ at a US release. 50 clears ordinary bars and keeps 10 x spread ($5.00) under
# the $6.00 stop floor. Raw: kn/01 §2 (not measured on this broker).
MAX_SPREAD_POINTS: Final[dict[str, int]] = {"standard": 50, "raw": 20}
# kn/01 §2 / kn/15's original ceilings, now a quality mark (packet flag, rules desks).
PREFERRED_SPREAD_POINTS: Final[dict[str, int]] = {"standard": 35, "raw": 20}

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

# Demo execution lot cap (user decision 2026-09-17: the agent picks 0.01-0.03 lots; the
# risk budget still caps the size).
MAX_EXECUTE_LOTS: Final[float] = 0.03

# Plan section 5: the V6 magic range (250570 is the default; the rest are reserved).
V6_MAGIC_FIRST: Final[int] = 250570
V6_MAGIC_LAST: Final[int] = 250579

# Fraction of free margin a new position may consume.
MAX_MARGIN_USE: Final[float] = 0.25

# Plan §2 rule 5: a combined size multiplier below this holds (the protocol's
# MIN_COMBINED_MULTIPLIER). The sizer's minimum-lot floor never applies below it either,
# so the floor can at most quadruple the risk an agent asked for, and never exceed the
# unscaled budget.
MIN_SIZE_MULTIPLIER: Final[float] = 0.25

# Agent-designed entries (user decision 2026-09-17): the operator agent picks side, entry,
# stop and target itself, inside these bounds (deliberation.agent_entry).
# A LIMIT entry may rest at most this many ATR(M15) away from the quote: further away it
# is a different trade than the one analysed, and it would rarely fill in the 2-bar expiry.
MAX_AGENT_ENTRY_ATR_M15: Final[float] = 1.5
# A stop wider than this many ATR(M15) is not a structural stop for an M15 decision.
MAX_AGENT_STOP_ATR_M15: Final[float] = 3.0
# Without an ATR(M15) the entry distance falls back to this multiple of the stop floor.
AGENT_ENTRY_FLOOR_MULTIPLE: Final[float] = 2.0
# Reward bounds for an agent target (kn/08: targets beyond ~5R rarely fill before the
# time barrier; below 1R the friction share grows past the kn/01 line).
MIN_AGENT_REWARD_R: Final[float] = 1.0
MAX_AGENT_REWARD_R: Final[float] = 5.0

# Tolerance for tick_value vs tick_size x contract_size before trading is refused.
SPEC_TOLERANCE: Final[float] = 0.02

# --- Phase A of the M1 dynamic-management design (user decisions 2026-09-17) ---------------
# An agent plan holds between 60 min and MAX_TIME_BARRIER_S (4 h).
MIN_TIME_LIMIT_S: Final[int] = 3600
# A LIMIT or STOP rests 15-60 min.
MIN_PENDING_EXPIRY_S: Final[int] = 900
MAX_PENDING_EXPIRY_S: Final[int] = 3600
# TP1 sits at least half the initial risk beyond the entry.
MIN_TP1_R: Final[float] = 0.5
# d_min = max(stops_level, freeze_level) x point + spread + this, in price units.
MODIFY_BUFFER_PRICE: Final[float] = 0.10
# The EA refuses a management action issued longer ago than this.
ACTION_MAX_AGE_S: Final[int] = 30
# 24-hour trading needs more room than the London-NY window did.
MAX_TRADES_PER_DAY_CEILING: Final[int] = 20
