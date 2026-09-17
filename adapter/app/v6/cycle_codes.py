"""
Bounded code sets shared by every Phase 2 module (plan sections 2, 6 and 8).

Statuses, hold reasons, gate codes, veto codes, calendar codes, setup names,
candidate verdict labels, candidate ids and feature keys live here so that the
gates, setups, desks, protocol, engine, labeler and dashboard agree on spelling.
`app.v6.cycle_types` re-exports all of it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Literal

from .schemas.agents import ID_PATTERN
from .types import GateResult, Side

__all__ = [
    "CycleStatus", "CYCLE_STATUSES", "ENTER_STATUSES", "HoldReason",
    "GATE_HALTED", "GATE_WARMUP", "GATE_ACCOUNT_POLICY", "GATE_SNAPSHOT_AGE",
    "GATE_CLOCK_SKEW", "GATE_SPEC", "GATE_SPREAD", "GATE_SESSION", "GATE_NEWS",
    "GATE_FRICTION", "GATE_ATR", "GATE_OCCUPANCY", "GATE_TRADES_TODAY", "GATE_BREAKER",
    "GATE_CODES", "GATE_HOLD_REASONS", "hold_reason_for_gates",
    "VETO_CALENDAR", "VETO_NEWS", "VETO_LIQUIDITY", "VETO_STRUCTURE", "VETO_STRUCTURE_LOGGED",
    "VETO_CODES", "MIN_COMBINED_MULTIPLIER", "REDUCED_TIER_FACTOR",
    "CAL_PRE_EVENT", "CAL_POST_EVENT", "CAL_POST_SURPRISE", "CAL_US_DATA_BAR", "CAL_STALE",
    "CALENDAR_CODES", "SetupName", "SETUP_NAMES", "MAX_OFFERED_CANDIDATES",
    "CandidateVerdictLabel", "CANDIDATE_VERDICTS", "candidate_id_for",
    "F_ATR_M5", "F_ATR_M5_POINTS", "F_ATR_M15", "F_ATR_H1", "F_SLOT_TR_M15", "F_ATR_RATIO",
    "F_FRICTION_PRICE", "F_FRICTION_ATR", "F_SPREAD_POINTS", "F_SPREAD_PCTL_HOUR", "F_TV_Z",
    "F_QUOTE_GAP_MS", "F_RV_RATIO", "F_ER_M15", "F_VR_M5", "F_AC1_M5", "F_ADX_H1",
    "F_STRUCTURE_M15", "F_ROUND_DISTANCE", "F_DOM_SYNTHETIC", "FEATURE_KEYS",
]

# --- cycle status -------------------------------------------------------------
# ENTER: the intent was published to the EA (execute mode, armed session).
# ENTER_SHADOW: the same decision, recorded only (shadow mode, or not published).
CycleStatus = Literal["HOLD", "ENTER", "ENTER_SHADOW", "LATE", "ABORTED", "ERROR"]
CYCLE_STATUSES: Final[tuple[CycleStatus, ...]] = (
    "HOLD", "ENTER", "ENTER_SHADOW", "LATE", "ABORTED", "ERROR")
ENTER_STATUSES: Final[frozenset[str]] = frozenset({"ENTER", "ENTER_SHADOW"})


class HoldReason(StrEnum):
    """Why a cycle did not produce a (shadow) entry. Values are stable log codes."""

    GATE = "APP-V6-GATE"                      # a hard gate failed (see gates)
    NO_CANDIDATE = "APP-V6-NO-CANDIDATE"      # detectors found nothing offerable
    INVALID_VIEW = "APP-V6-INVALID-VIEW"      # PA or Chief view missing/invalid
    NO_TAKE = "APP-V6-NO-TAKE"                # no PA TAKE, or Chief picked a non-TAKE
    VETO = "APP-V6-VETO"                      # calendar/news/liquidity/structure veto
    CHIEF_HOLD = "APP-V6-CHIEF-HOLD"          # Chief chose HOLD
    LOW_CONVICTION = "APP-V6-LOW-CONVICTION"  # chosen candidate below V6_PA_MIN_CONVICTION
    LOW_MULTIPLIER = "APP-V6-LOW-MULTIPLIER"  # combined size multiplier < MIN_COMBINED_MULTIPLIER
    EXIT = "APP-V6-EXIT"                      # exit plan refused (floor, geometry)
    SIZE = "APP-V6-SIZE"                      # sizer refused (min lot wall, caps)
    WARMUP = "APP-V6-WARMUP"
    HALTED = "APP-V6-HALTED"
    BREAKER = "APP-V6-BREAKER"
    NO_SESSION = "APP-V6-NO-SESSION"          # no active daily session: tier 0 only
    STALE = "APP-V6-STALE"
    LATE = "APP-V6-LATE"                      # decision after bar close + deadline
    OPERATOR_TIMEOUT = "APP-V6-OPERATOR-TIMEOUT"  # no operator decision by the deadline
    ABORTED = "APP-V6-ABORTED"
    ERROR = "APP-V6-ERROR"


# --- hard gates (plan section 6), in evaluation order --------------------------
GATE_HALTED: Final[str] = "HALTED"                  # halt file, dashboard HALT, EA halted
GATE_WARMUP: Final[str] = "WARMUP"                  # BarStore not warm yet
GATE_ACCOUNT_POLICY: Final[str] = "ACCOUNT_POLICY"  # demo policy, allowed logins
GATE_SNAPSHOT_AGE: Final[str] = "SNAPSHOT_AGE"      # now - received_at <= V6_SNAPSHOT_STALE_S
GATE_CLOCK_SKEW: Final[str] = "CLOCK_SKEW"          # |received_at - sent_at_epoch| <= skew
GATE_SPEC: Final[str] = "SPEC"                      # tick value consistent with contract
GATE_SPREAD: Final[str] = "SPREAD"
GATE_SESSION: Final[str] = "SESSION"
GATE_NEWS: Final[str] = "NEWS"                      # CalendarAssessment.blackout / stale
GATE_FRICTION: Final[str] = "FRICTION_ATR"          # friction / ATR(M5) < limit
GATE_ATR: Final[str] = "ATR_M5"                     # ATR(14, M5) >= limit points
GATE_OCCUPANCY: Final[str] = "OCCUPANCY"            # no V6 position or pending order
GATE_TRADES_TODAY: Final[str] = "TRADES_TODAY"
GATE_BREAKER: Final[str] = "BREAKER"                # any active breaker
GATE_CODES: Final[tuple[str, ...]] = (
    GATE_HALTED, GATE_WARMUP, GATE_ACCOUNT_POLICY, GATE_SNAPSHOT_AGE, GATE_CLOCK_SKEW,
    GATE_SPEC, GATE_SPREAD, GATE_SESSION, GATE_NEWS, GATE_FRICTION, GATE_ATR,
    GATE_OCCUPANCY, GATE_TRADES_TODAY, GATE_BREAKER,
)
GATE_HOLD_REASONS: Final[Mapping[str, HoldReason]] = MappingProxyType({
    GATE_HALTED: HoldReason.HALTED, GATE_WARMUP: HoldReason.WARMUP,
    GATE_SNAPSHOT_AGE: HoldReason.STALE, GATE_CLOCK_SKEW: HoldReason.STALE,
    GATE_BREAKER: HoldReason.BREAKER,
})


def hold_reason_for_gates(gates: Iterable[GateResult]) -> HoldReason | None:
    """Hold reason of the first failed gate (in the order given); None if all passed."""
    for gate in gates:
        if not gate.passed:
            return GATE_HOLD_REASONS.get(gate.code, HoldReason.GATE)
    return None


# --- vetoes and aggregation (plan section 2, rules 3, 5 and 6) ------------------
VETO_CALENDAR: Final[str] = "VETO_CALENDAR"
VETO_NEWS: Final[str] = "VETO_NEWS_BLOCK"
VETO_LIQUIDITY: Final[str] = "VETO_LIQUIDITY_NO_TRADE"
VETO_STRUCTURE: Final[str] = "VETO_COUNTER_STRUCTURE"
# Counter-structure veto observed while V6_STRUCTURE_VETO=log: recorded, not enforced.
VETO_STRUCTURE_LOGGED: Final[str] = "VETO_COUNTER_STRUCTURE_LOGGED"
VETO_CODES: Final[frozenset[str]] = frozenset(
    {VETO_CALENDAR, VETO_NEWS, VETO_LIQUIDITY, VETO_STRUCTURE, VETO_STRUCTURE_LOGGED})
MIN_COMBINED_MULTIPLIER: Final[float] = 0.25
REDUCED_TIER_FACTOR: Final[float] = 0.5

# --- calendar assessment codes -------------------------------------------------
CAL_PRE_EVENT: Final[str] = "CAL_PRE_EVENT"          # within 15 min before a HIGH USD event
CAL_POST_EVENT: Final[str] = "CAL_POST_EVENT"        # within 15 min after one
CAL_POST_SURPRISE: Final[str] = "CAL_POST_SURPRISE"  # extended 35 min after a large miss
CAL_US_DATA_BAR: Final[str] = "CAL_US_DATA_BAR"      # 12:30/13:30 UTC data bar
CAL_STALE: Final[str] = "CAL_STALE"                  # feeds stale: fail closed
CALENDAR_CODES: Final[frozenset[str]] = frozenset(
    {CAL_PRE_EVENT, CAL_POST_EVENT, CAL_POST_SURPRISE, CAL_US_DATA_BAR, CAL_STALE})

# --- setups and candidates -------------------------------------------------------
SetupName = Literal["displacement", "orb", "retest", "engulfing"]
SETUP_NAMES: Final[tuple[SetupName, ...]] = ("displacement", "orb", "retest", "engulfing")
MAX_OFFERED_CANDIDATES: Final[int] = 3

# Stored per candidate in v6_candidates.verdict:
#   gated    - a hard gate failed; nothing was offered
#   refused  - the exit plan refused it (stop below floor, geometry); never offered
#   unranked - no usable PA ranking: over the offer cap, abstain, invalid view, no session
#   skip     - PA ranked it SKIP
#   take     - PA ranked it TAKE but it is not the Chief's ENTER pick
#   chosen   - the Chief's ENTER pick (even if protocol or sizing then held)
CandidateVerdictLabel = Literal["gated", "refused", "unranked", "skip", "take", "chosen"]
CANDIDATE_VERDICTS: Final[tuple[CandidateVerdictLabel, ...]] = (
    "gated", "refused", "unranked", "skip", "take", "chosen")

_ID_RE: Final[re.Pattern[str]] = re.compile(ID_PATTERN)
_VARIANT_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9]{1,12}$")


def candidate_id_for(setup: SetupName, side: Side, bar_t: int, variant: str = "") -> str:
    """Globally unique, deterministic id: `<setup>-<side>-<bar_t>[-<variant>]`.

    `bar_t` is the open epoch of the M15 bar whose close produced the candidate.
    Re-detecting the same setup on the same bar yields the same id, which the
    ledger stores once.
    """
    if setup not in SETUP_NAMES or side not in ("buy", "sell"):
        raise ValueError("unknown setup or side")
    if isinstance(bar_t, bool) or not isinstance(bar_t, int) or bar_t < 0:
        raise ValueError("bar_t must be a non-negative int epoch")
    if variant and not _VARIANT_RE.match(variant):
        raise ValueError("variant must be 1-12 lowercase letters or digits")
    candidate_id = f"{setup}-{side}-{bar_t}" + (f"-{variant}" if variant else "")
    if not _ID_RE.match(candidate_id):
        raise ValueError("candidate id does not match ID_PATTERN")
    return candidate_id


# --- MarketContext.features keys ------------------------------------------------
# Emit a key only when it could be computed from CLOSED bars; consumers must
# treat a missing key as "unavailable" (never as zero). Units in the comments.
F_ATR_M5: Final[str] = "atr_m5"                        # ATR(14) on M5, price
F_ATR_M5_POINTS: Final[str] = "atr_m5_points"          # the same in points
F_ATR_M15: Final[str] = "atr_m15"                      # ATR(14) on M15, price
F_ATR_H1: Final[str] = "atr_h1"                        # ATR(14) on H1, price
F_SLOT_TR_M15: Final[str] = "slot_tr_m15"              # same-slot true range, 10 sessions, price
F_ATR_RATIO: Final[str] = "atr_ratio_m15_slot"         # atr_m15 / slot_tr_m15
F_FRICTION_PRICE: Final[str] = "friction_price"        # spread + commission per unit, price
F_FRICTION_ATR: Final[str] = "friction_atr_m5"         # friction_price / atr_m5
F_SPREAD_POINTS: Final[str] = "spread_points"          # live spread, points
F_SPREAD_PCTL_HOUR: Final[str] = "spread_pctl_hour"    # [0, 1] vs same UTC hour history
F_TV_Z: Final[str] = "tick_volume_z"                   # activity, unsigned; not volume
F_QUOTE_GAP_MS: Final[str] = "max_quote_gap_ms"        # last 15 min
F_RV_RATIO: Final[str] = "rv_ratio"                    # realised vol 15 min / slot norm
F_ER_M15: Final[str] = "er_m15"                        # Kaufman ER, [0, 1]
F_VR_M5: Final[str] = "vr_m5"                          # signed variance ratio
F_AC1_M5: Final[str] = "ac1_m5"                        # lag-1 autocorrelation, [-1, 1]
F_ADX_H1: Final[str] = "adx_h1"                        # [0, 100]
F_STRUCTURE_M15: Final[str] = "structure_m15"          # +1 up, -1 down, 0 range (absent: unknown)
F_ROUND_DISTANCE: Final[str] = "round_distance"        # price - nearest $50 level, price
F_DOM_SYNTHETIC: Final[str] = "dom_synthetic"          # 1.0 synthetic, 0.0 real
FEATURE_KEYS: Final[frozenset[str]] = frozenset({
    F_ATR_M5, F_ATR_M5_POINTS, F_ATR_M15, F_ATR_H1, F_SLOT_TR_M15, F_ATR_RATIO,
    F_FRICTION_PRICE, F_FRICTION_ATR, F_SPREAD_POINTS, F_SPREAD_PCTL_HOUR, F_TV_Z,
    F_QUOTE_GAP_MS, F_RV_RATIO, F_ER_M15, F_VR_M5, F_AC1_M5, F_ADX_H1, F_STRUCTURE_M15,
    F_ROUND_DISTANCE, F_DOM_SYNTHETIC,
})
