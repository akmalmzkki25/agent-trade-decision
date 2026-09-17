"""
Stop, target and time-barrier geometry for one V6 candidate.

The candidate's structural invalidation is the stop. This module never tightens or
widens it to clear the floor: a stop under the floor is a skipped trade (kn/07 §3).
Round numbers are handled the way Osler's order-book evidence points (kn/07 §4):
stop clusters sit just beyond a round level, take-profit clusters sit at it. Only
$50/$100 multiples still matter at current gold prices, hence the default step.

Every plan is labelled UNMEASURED_GEOMETRY: the 2R target and the round-number
distances are provisional until the process study measures them (kn/08).
"""

from __future__ import annotations

import math
from typing import Final

from ..types import Candidate, ExitPlan, Refusal, SymbolSpec
from . import limits

# kn/07 §4: executed stops bunch within ~$1 of the round level on its far side.
ROUND_ZONE: Final[float] = 1.0
# kn/07 §4: targets go just before the level where take-profit orders stack up.
TP_BEFORE_ROUND: Final[float] = 0.10
DEFAULT_ROUND_STEP: Final[float] = 50.0
# kn/07 §4: $10 levels pass twice per M15 bar at ~$4,300; finer steps carry no signal.
MIN_ROUND_STEP: Final[float] = 10.0
MIN_REWARD_R: Final[float] = 1.0
MAX_DIGITS: Final[int] = 8
REWARD_R_DECIMALS: Final[int] = 4
# Absorbs binary float noise only; far below one point on any gold symbol.
PRICE_EPS: Final[float] = 1e-9

CODE_BAD_INPUT: Final[str] = "BAD_INPUT"
CODE_BAD_GEOMETRY: Final[str] = "BAD_GEOMETRY"
CODE_STOP_BELOW_FLOOR: Final[str] = "STOP_BELOW_FLOOR"
CODE_STOPS_LEVEL: Final[str] = "STOPS_LEVEL"
CODE_TP_BELOW_1R: Final[str] = "TP_BELOW_1R"

LABEL_SL_ROUND_SHIFTED: Final[str] = "SL_ROUND_SHIFTED"
LABEL_TP_ROUND_PULLED: Final[str] = "TP_ROUND_PULLED"
LABEL_UNMEASURED: Final[str] = "UNMEASURED_GEOMETRY"

_DIRECTION: Final[dict[str, int]] = {"buy": 1, "sell": -1}


def build_exit_plan(
    candidate: Candidate,
    *,
    spread_price: float,
    friction_price: float,
    spec: SymbolSpec,
    stop_floor_points: int,
    tp_r_multiple: float,
    time_barrier_s: int,
    round_step: float = DEFAULT_ROUND_STEP,
) -> ExitPlan | Refusal:
    """Return the stop/target/barrier for `candidate`, or a Refusal explaining why not."""
    problem = _input_problem(
        candidate, spread_price, friction_price, spec,
        stop_floor_points, tp_r_multiple, time_barrier_s, round_step,
    )
    if problem is not None:
        return Refusal((CODE_BAD_INPUT,), problem)
    direction = _DIRECTION[candidate.side]
    digits = spec.digits
    entry = round(candidate.entry, digits)
    if not _stop_on_losing_side(direction, entry, candidate.invalidation):
        return _geometry_refusal(candidate, entry)
    sl, sl_shifted = _stop_price(direction, candidate.invalidation, spread_price, round_step, digits)
    stop_distance = round(direction * (entry - sl), digits)
    refusal = _stop_refusal(
        stop_distance, _stop_floor(spec, stop_floor_points, spread_price, friction_price), spec
    )
    if refusal is not None:
        return refusal
    raw_tp = round(entry + direction * tp_r_multiple * stop_distance, digits)
    tp, tp_pulled = _pull_target(direction, entry, raw_tp, round_step, digits)
    tp_distance = round(direction * (tp - entry), digits)
    reward_r = tp_distance / stop_distance
    refusal = _target_refusal(tp_distance, reward_r, spec)
    if refusal is not None:
        return refusal
    return ExitPlan(
        side=candidate.side,
        entry=entry,
        sl=sl,
        tp=tp,
        stop_distance=stop_distance,
        reward_r=round(reward_r, REWARD_R_DECIMALS),
        time_barrier_s=time_barrier_s,
        labels=_labels(sl_shifted, tp_pulled),
    )


def _labels(sl_shifted: bool, tp_pulled: bool) -> tuple[str, ...]:
    shifted = (LABEL_SL_ROUND_SHIFTED,) if sl_shifted else ()
    pulled = (LABEL_TP_ROUND_PULLED,) if tp_pulled else ()
    return shifted + pulled + (LABEL_UNMEASURED,)


# --- validation --------------------------------------------------------------


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_positive(value: object) -> bool:
    return _is_number(value) and math.isfinite(value) and value > 0  # type: ignore[arg-type]


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _input_problem(
    candidate: Candidate,
    spread_price: float,
    friction_price: float,
    spec: SymbolSpec,
    stop_floor_points: int,
    tp_r_multiple: float,
    time_barrier_s: int,
    round_step: float,
) -> str | None:
    """Name the first unusable input, or None. Bad data is refused, never defaulted."""
    checks = (
        (candidate.side in _DIRECTION, "side must be 'buy' or 'sell'"),
        (_is_positive(candidate.entry), "entry must be finite and > 0"),
        (_is_positive(candidate.invalidation), "invalidation must be finite and > 0"),
        (_is_positive(spread_price), "spread_price must be finite and > 0"),
        (_is_positive(friction_price), "friction_price must be finite and > 0"),
        (_is_positive(tp_r_multiple), "tp_r_multiple must be finite and > 0"),
        (_is_positive(round_step) and round_step >= MIN_ROUND_STEP,
         f"round_step must be finite and >= {MIN_ROUND_STEP}"),
        (_is_int(stop_floor_points) and stop_floor_points >= limits.MIN_STOP_FLOOR_POINTS,
         f"stop_floor_points must be an int >= {limits.MIN_STOP_FLOOR_POINTS}"),
        (_is_int(time_barrier_s) and 0 < time_barrier_s <= limits.MAX_TIME_BARRIER_S,
         f"time_barrier_s must be an int in (0, {limits.MAX_TIME_BARRIER_S}]"),
        (_is_positive(spec.point), "spec.point must be finite and > 0"),
        (_is_int(spec.digits) and 0 <= spec.digits <= MAX_DIGITS,
         f"spec.digits must be an int in [0, {MAX_DIGITS}]"),
        (_is_int(spec.stops_level) and spec.stops_level >= 0,
         "spec.stops_level must be an int >= 0"),
    )
    return next((reason for ok, reason in checks if not ok), None)


# --- stop --------------------------------------------------------------------


def _stop_on_losing_side(direction: int, entry: float, invalidation: float) -> bool:
    # Judged on the raw invalidation: a short's spread buffer must not rescue a
    # structural level that sits below entry.
    return direction * (entry - invalidation) > 0


def _geometry_refusal(candidate: Candidate, entry: float) -> Refusal:
    return Refusal(
        (CODE_BAD_GEOMETRY,),
        f"{candidate.side} invalidation {candidate.invalidation} is not beyond entry {entry}",
    )


def _stop_price(
    direction: int, invalidation: float, spread_price: float, step: float, digits: int
) -> tuple[float, bool]:
    """Structural stop plus the short-side spread buffer, moved off round-level clusters."""
    # Charts show Bid. A long's stop fires on Bid, a short's on Ask = Bid + spread,
    # so only the short needs the buffer to reach parity (kn/07 §4).
    buffer = spread_price if direction < 0 else 0.0
    sl = round(invalidation + buffer, digits)
    level = _level_toward(sl, step, direction)
    if direction * (level - sl) > ROUND_ZONE + PRICE_EPS:
        return sl, False
    # Inside [R - zone, R] (long) or [R, R + zone] (short): step past the cluster
    # so its cascade does not fill us badly (Osler).
    return round(level - direction * (ROUND_ZONE + spread_price), digits), True


def stop_floor_price(
    spec: SymbolSpec, stop_floor_points: int, spread_price: float, friction_price: float
) -> float:
    """The smallest stop distance (price units) `build_exit_plan` accepts."""
    return _stop_floor(spec, stop_floor_points, spread_price, friction_price)


def stop_shift_allowance(spread_price: float, side: str) -> float:
    """How far `build_exit_plan` may move a stop beyond the requested level: the short
    side's spread buffer plus the round-level step-past."""
    buffer = spread_price if side == "sell" else 0.0
    return buffer + ROUND_ZONE + spread_price


def _stop_floor(
    spec: SymbolSpec, stop_floor_points: int, spread_price: float, friction_price: float
) -> float:
    return max(
        stop_floor_points * spec.point,
        limits.MIN_STOP_SPREAD_MULTIPLE * spread_price,
        friction_price / limits.MAX_FRICTION_TO_STOP,
    )


def _stop_refusal(stop_distance: float, floor: float, spec: SymbolSpec) -> Refusal | None:
    # A zero distance can survive rounding; it must never reach the reward division.
    if stop_distance <= 0 or stop_distance + PRICE_EPS < floor:
        return Refusal(
            (CODE_STOP_BELOW_FLOOR,),
            f"stop distance {stop_distance} is under the floor {floor:.5f}; skip, do not resize",
        )
    if not _meets_stops_level(stop_distance, spec):
        return Refusal(
            (CODE_STOPS_LEVEL,),
            f"stop distance {stop_distance} is inside the broker stops level "
            f"({spec.stops_level} points)",
        )
    return None


def _meets_stops_level(distance: float, spec: SymbolSpec) -> bool:
    # MT5 rejects SL/TP closer than stops_level points; equal distance is allowed.
    return abs(distance) + PRICE_EPS >= spec.stops_level * spec.point


# --- target ------------------------------------------------------------------


def _pull_target(
    direction: int, entry: float, tp: float, step: float, digits: int
) -> tuple[float, bool]:
    """Pull the target in front of the first round level at or before it."""
    first_level = _level_toward(entry, step, -direction) + direction * step
    if direction * (tp - first_level) < -PRICE_EPS:
        return tp, False
    return round(first_level - direction * TP_BEFORE_ROUND, digits), True


def _target_refusal(tp_distance: float, reward_r: float, spec: SymbolSpec) -> Refusal | None:
    # reward_r is signed, so a pull that lands behind entry is refused as well.
    below_1r = (CODE_TP_BELOW_1R,) if reward_r + PRICE_EPS < MIN_REWARD_R else ()
    too_close = () if _meets_stops_level(tp_distance, spec) else (CODE_STOPS_LEVEL,)
    codes = below_1r + too_close
    if not codes:
        return None
    return Refusal(codes, f"target distance {tp_distance} gives {reward_r:.4f}R")


# --- round levels ------------------------------------------------------------


def _level_toward(price: float, step: float, direction: int) -> float:
    """Nearest multiple of `step` at or above `price` (direction > 0) or at or below it."""
    ratio = price / step
    if direction > 0:
        return math.ceil(ratio - PRICE_EPS) * step
    return math.floor(ratio + PRICE_EPS) * step
