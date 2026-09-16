"""
Hard gates, evaluated before any agent is asked (plan section 6, knowledge/15 gate table).

`evaluate_gates` is pure. It returns one `GateResult` per code in
`cycle_codes.GATE_CODES`, in that order, and evaluates every gate even after a
failure so the dashboard always shows the full table. `first_failure` and
`cycle_codes.hold_reason_for_gates` pick the gate that decides the hold.

The plan's gate list maps onto the codes like this:
  runtime state  -> HALTED (halt sources + EA halt), WARMUP
  demo policy    -> ACCOUNT_POLICY (risk.policy, configured backend)
  freshness      -> SNAPSHOT_AGE (0 <= now - received_at <= V6_SNAPSHOT_STALE_S, and the
                    bar close no later than now + V6_MAX_CLOCK_SKEW_S),
                    CLOCK_SKEW (|received_at - sent_at| <= V6_MAX_CLOCK_SKEW_S)
  spec           -> SPEC (value SPEC_OK / SPEC_UNKNOWN / SPEC_MISMATCH)
  spread         -> SPREAD (<= limit for V6_ACCOUNT_TYPE)
  session        -> SESSION (market.sessions block reasons)
  calendar       -> NEWS (blackout, stale feed, or an assessment for another bar)
  friction       -> FRICTION_ATR (friction / ATR(M5) < V6_FRICTION_ATR_MAX)
  ATR floor      -> ATR_M5 (ATR(14, M5) >= V6_MIN_ATR_M5_POINTS)
  occupancy      -> OCCUPANCY (no V6 position or pending order), TRADES_TODAY
  breaker        -> BREAKER (risk.breakers status, plus the EA's local breaker)
  LLM budget     -> LLM_BUDGET (always passes in Phase 2: rules only, no spend)

Missing data fails closed. A gate that raises is reported as failed with value
"ERROR" instead of hiding the rest of the table.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from ..config import V6Settings
from ..cycle_codes import (
    CAL_STALE, F_ATR_M5, F_ATR_M5_POINTS, F_FRICTION_ATR, F_FRICTION_PRICE, GATE_ACCOUNT_POLICY,
    GATE_ATR, GATE_BREAKER, GATE_CLOCK_SKEW, GATE_FRICTION, GATE_HALTED, GATE_LLM_BUDGET,
    GATE_NEWS, GATE_OCCUPANCY, GATE_SESSION, GATE_SNAPSHOT_AGE, GATE_SPEC, GATE_SPREAD,
    GATE_TRADES_TODAY, GATE_WARMUP,
)
from ..cycle_types import CalendarAssessment, MarketContext
from ..types import TIMEFRAME_SECONDS, GateResult, SymbolSpec
from . import limits
from .breakers import BreakerStatus
from .policy import evaluate_account_policy

logger = logging.getLogger(__name__)

VALUE_OK: Final[str] = "OK"
VALUE_ERROR: Final[str] = "ERROR"
VALUE_MISSING: Final[str] = "MISSING"
VALUE_READY: Final[str] = "READY"
VALUE_WARMING_UP: Final[str] = "WARMING_UP"
VALUE_BLACKOUT: Final[str] = "BLACKOUT"
# Halt sources the runtime may report; the EA's own halt flag is added here.
HALT_SOURCE_FILE: Final[str] = "HALT_FILE"
HALT_SOURCE_DASHBOARD: Final[str] = "DASHBOARD"
HALT_SOURCE_OPERATOR: Final[str] = "OPERATOR"
HALT_SOURCE_EA: Final[str] = "EA_LOCAL_HALT"
SPEC_OK: Final[str] = "SPEC_OK"
SPEC_UNKNOWN: Final[str] = "SPEC_UNKNOWN"
SPEC_MISMATCH: Final[str] = "SPEC_MISMATCH"
ORDER_CALC_SOURCE: Final[str] = "order_calc"
# The calendar assessment must describe this bar, not a cached older one.
CAL_ASOF_MISMATCH: Final[str] = "CAL_ASOF_MISMATCH"
CALENDAR_MAX_OFFSET_S: Final[int] = TIMEFRAME_SECONDS["M15"]
EA_BREAKER_NONE: Final[str] = "none"
MAX_OCCUPANCY: Final[int] = limits.MAX_OPEN_POSITIONS - 1
PHASE2_LLM_SPEND_USD: Final[float] = 0.0
VALUE_DECIMALS: Final[int] = 4
SPEC_PCT_DECIMALS: Final[int] = 1

_SPEC_TOLERANCE: Final[Decimal] = Decimal(repr(limits.SPEC_TOLERANCE))


@dataclass(frozen=True, kw_only=True)
class RuntimeGateState:
    """What the runtime knows that the snapshot does not.

    `halt_sources`: active kill switches (HALT_SOURCE_* values; empty = running).
    `warmed_up`: BarStore holds enough history (plan section 4 warm-up rule).
    """

    warmed_up: bool
    halt_sources: tuple[str, ...] = ()
    warmup_detail: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "halt_sources", tuple(self.halt_sources))

    @property
    def halted(self) -> bool:
        return bool(self.halt_sources)


# --- helpers -------------------------------------------------------------------

def _positive_finite(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and value > 0


def _dec(value: float) -> Decimal:
    return Decimal(repr(float(value)))


def _round(value: float) -> float:
    return round(value, VALUE_DECIMALS)


def _fmt_optional(value: float | None) -> str:
    return "none" if value is None else f"{value:g}"


# --- runtime and account -------------------------------------------------------

def _halted_gate(context: MarketContext, runtime_state: RuntimeGateState) -> GateResult:
    sources = list(runtime_state.halt_sources)
    if context.ea_state.halted:
        sources.append(HALT_SOURCE_EA)
    return GateResult(code=GATE_HALTED, passed=not sources, value=",".join(sources) or VALUE_OK,
                      detail="kill switch active" if sources else "")


def _warmup_gate(runtime_state: RuntimeGateState) -> GateResult:
    ready = runtime_state.warmed_up is True
    return GateResult(code=GATE_WARMUP, passed=ready,
                      value=VALUE_READY if ready else VALUE_WARMING_UP,
                      detail=runtime_state.warmup_detail)


def _account_policy_gate(context: MarketContext, settings: V6Settings) -> GateResult:
    decision = evaluate_account_policy(context.trade_mode, context.server,
                                       context.account.login, settings)
    return GateResult(code=GATE_ACCOUNT_POLICY, passed=decision.allowed, value=decision.code,
                      detail=decision.detail)


def _age_problem(age: float, limit: float, close_ahead: float, skew: float) -> str:
    if age < 0:
        return "received after now"
    if age > limit:
        return f"snapshot is {age:.1f} s old"
    # The bar must have closed by the adapter clock; the EA's own claims cannot vouch for it.
    return "bar close is in the future" if close_ahead > skew else ""


def _snapshot_age_gate(context: MarketContext, settings: V6Settings, now: float) -> GateResult:
    limit = settings.snapshot_stale_s
    age = now - context.received_at
    if not math.isfinite(age):
        return GateResult(code=GATE_SNAPSHOT_AGE, passed=False, value=VALUE_MISSING, limit=limit,
                          detail="receive time or clock is not finite")
    detail = _age_problem(age, limit, context.as_of_epoch - now, settings.max_clock_skew_s)
    return GateResult(code=GATE_SNAPSHOT_AGE, passed=not detail, value=_round(age), limit=limit,
                      detail=detail)


def _clock_skew_gate(context: MarketContext, settings: V6Settings) -> GateResult:
    limit = settings.max_clock_skew_s
    skew = context.received_at - context.sent_at_epoch
    passed = math.isfinite(skew) and abs(skew) <= limit
    return GateResult(code=GATE_CLOCK_SKEW, passed=passed,
                      value=_round(skew) if math.isfinite(skew) else VALUE_MISSING, limit=limit,
                      detail="" if passed else "EA clock and adapter clock disagree")


# --- symbol spec ---------------------------------------------------------------

def _deviation(tick_value: float | None, spec: SymbolSpec) -> Decimal | None:
    """|tick_value / (tick_size x contract_size) - 1|, or None when not computable."""
    values = (tick_value, spec.tick_size, spec.contract_size)
    if tick_value is None or not all(_positive_finite(v) for v in values):
        return None
    expected = _dec(spec.tick_size) * _dec(spec.contract_size)
    return abs(_dec(tick_value) / expected - 1)


def _reported_note(spec: SymbolSpec) -> str:
    reported = (spec.reported_tick_value, spec.reported_tick_value_loss)
    deviations = [d for d in (_deviation(v, spec) for v in reported) if d is not None]
    if not deviations:
        return "reported tick values not available"
    worst = max(deviations)
    if worst <= _SPEC_TOLERANCE:
        return "reported tick values agree with the contract"
    return (f"reported tick_value={_fmt_optional(spec.reported_tick_value)} is off by "
            f"{float(worst) * 100:.{SPEC_PCT_DECIMALS}f}% (informational only)")


def _spec_gate(spec: SymbolSpec) -> GateResult:
    tolerance = limits.SPEC_TOLERANCE
    note = _reported_note(spec)
    if spec.tick_value_source != ORDER_CALC_SOURCE:
        return GateResult(code=GATE_SPEC, passed=False, value=SPEC_UNKNOWN, limit=tolerance,
                          detail=f"tick values not priced by OrderCalcProfit "
                                 f"(source={spec.tick_value_source}); {note}")
    deviations = [_deviation(v, spec) for v in (spec.tick_value, spec.tick_value_loss)]
    known = [d for d in deviations if d is not None]
    if len(known) != len(deviations):
        return GateResult(code=GATE_SPEC, passed=False, value=SPEC_UNKNOWN, limit=tolerance,
                          detail="tick values, tick_size and contract_size must be positive; "
                                 + note)
    worst = max(known)
    passed = worst <= _SPEC_TOLERANCE
    detail = (f"tick_value={spec.tick_value:g} tick_value_loss={spec.tick_value_loss:g} "
              f"deviation={float(worst):.4f}; {note}")
    return GateResult(code=GATE_SPEC, passed=passed, value=SPEC_OK if passed else SPEC_MISMATCH,
                      limit=tolerance, detail=detail)


# --- market conditions -----------------------------------------------------------

def _spread_gate(context: MarketContext, settings: V6Settings) -> GateResult:
    spread = context.quote.spread_points
    limit = settings.effective_max_spread_points
    return GateResult(code=GATE_SPREAD, passed=spread <= limit, value=spread, limit=limit,
                      detail=f"account_type={settings.account_type}")


def _session_gate(context: MarketContext) -> GateResult:
    session = context.session
    reasons = session.block_reasons
    return GateResult(code=GATE_SESSION, passed=session.entries_allowed and not reasons,
                      value=",".join(reasons) or VALUE_OK,
                      detail=f"phase={session.phase} third={session.main_window_third}")


def _news_gate(context: MarketContext, calendar: CalendarAssessment) -> GateResult:
    codes = list(calendar.codes)
    if calendar.stale and CAL_STALE not in codes:
        codes.append(CAL_STALE)
    aligned = abs(calendar.as_of_epoch - context.as_of_epoch) <= CALENDAR_MAX_OFFSET_S
    if not aligned:
        codes.append(CAL_ASOF_MISMATCH)
    passed = aligned and not calendar.blackout and not calendar.stale
    fallback = VALUE_BLACKOUT if calendar.blackout else VALUE_OK
    detail = (f"next_event_min={_fmt_optional(calendar.next_event_minutes)} "
              f"last_event_min_ago={_fmt_optional(calendar.last_event_minutes_ago)} "
              f"events={len(calendar.events)}")
    return GateResult(code=GATE_NEWS, passed=passed, value=",".join(codes) or fallback,
                      detail=detail)


def _friction_ratio(features: Mapping[str, float]) -> float | None:
    ratio = features.get(F_FRICTION_ATR)
    if ratio is not None:
        return ratio
    price, atr = features.get(F_FRICTION_PRICE), features.get(F_ATR_M5)
    if price is None or atr is None or atr <= 0:
        return None
    return price / atr


def _friction_gate(context: MarketContext, settings: V6Settings) -> GateResult:
    limit = settings.friction_atr_max
    ratio = _friction_ratio(context.features)
    if ratio is None:
        return GateResult(code=GATE_FRICTION, passed=False, value=VALUE_MISSING, limit=limit,
                          detail="friction / ATR(M5) is not computable from closed bars")
    return GateResult(code=GATE_FRICTION, passed=0 <= ratio < limit, value=_round(ratio),
                      limit=limit)


def _atr_points(context: MarketContext) -> float | None:
    points = context.features.get(F_ATR_M5_POINTS)
    if points is not None:
        return points
    atr = context.features.get(F_ATR_M5)
    if atr is None or not _positive_finite(context.spec.point):
        return None
    return atr / context.spec.point


def _atr_gate(context: MarketContext, settings: V6Settings) -> GateResult:
    limit = settings.min_atr_m5_points
    points = _atr_points(context)
    if points is None:
        return GateResult(code=GATE_ATR, passed=False, value=VALUE_MISSING, limit=limit,
                          detail="ATR(14, M5) is not computable from closed bars")
    return GateResult(code=GATE_ATR, passed=points >= limit, value=_round(points), limit=limit)


# --- exposure, breakers, budget ----------------------------------------------------

def _occupancy_gate(context: MarketContext) -> GateResult:
    positions, pending = len(context.positions), len(context.pending_orders)
    count = positions + pending
    return GateResult(code=GATE_OCCUPANCY, passed=count <= MAX_OCCUPANCY, value=count,
                      limit=MAX_OCCUPANCY, detail=f"positions={positions} pending={pending}")


def _trades_today_gate(context: MarketContext, settings: V6Settings) -> GateResult:
    trades = context.day.trades_today
    limit = settings.max_trades_per_day
    return GateResult(code=GATE_TRADES_TODAY, passed=trades < limit, value=trades, limit=limit)


def _breaker_gate(context: MarketContext, breaker_status: BreakerStatus) -> GateResult:
    ea_breaker = context.ea_state.local_breaker
    labels = list(breaker_status.labels)
    details = [breaker_status.detail] if breaker_status.detail else []
    if ea_breaker != EA_BREAKER_NONE:
        labels.append(f"ea:{ea_breaker}")
        details.append(f"EA local {ea_breaker} breaker is active")
    passed = not breaker_status.tripped and ea_breaker == EA_BREAKER_NONE
    return GateResult(code=GATE_BREAKER, passed=passed, value=",".join(labels) or VALUE_OK,
                      detail="; ".join(details))


def _llm_budget_gate(settings: V6Settings) -> GateResult:
    return GateResult(code=GATE_LLM_BUDGET, passed=True, value=PHASE2_LLM_SPEND_USD,
                      limit=settings.daily_llm_budget_usd,
                      detail="phase 2 runs the rules baseline only; no LLM spend")


# --- public API ------------------------------------------------------------------

def _run(code: str, check: Callable[[], GateResult]) -> GateResult:
    try:
        result = check()
    except Exception:  # noqa: BLE001 - one broken gate must not hide the others
        logger.exception("v6 gate %s raised; failing closed", code)
        return GateResult(code=code, passed=False, value=VALUE_ERROR,
                          detail="gate evaluation raised; see the adapter log")
    return result


def evaluate_gates(context: MarketContext, calendar_assessment: CalendarAssessment,
                   runtime_state: RuntimeGateState, settings: V6Settings,
                   breaker_status: BreakerStatus, now: float) -> tuple[GateResult, ...]:
    """Every hard gate for this cycle, in GATE_CODES order, without short-circuiting.

    `calendar_assessment` is normally `context.calendar`; `now` is the adapter
    clock (UTC epoch seconds) at evaluation time.
    """
    checks: tuple[tuple[str, Callable[[], GateResult]], ...] = (
        (GATE_HALTED, lambda: _halted_gate(context, runtime_state)),
        (GATE_WARMUP, lambda: _warmup_gate(runtime_state)),
        (GATE_ACCOUNT_POLICY, lambda: _account_policy_gate(context, settings)),
        (GATE_SNAPSHOT_AGE, lambda: _snapshot_age_gate(context, settings, now)),
        (GATE_CLOCK_SKEW, lambda: _clock_skew_gate(context, settings)),
        (GATE_SPEC, lambda: _spec_gate(context.spec)),
        (GATE_SPREAD, lambda: _spread_gate(context, settings)),
        (GATE_SESSION, lambda: _session_gate(context)),
        (GATE_NEWS, lambda: _news_gate(context, calendar_assessment)),
        (GATE_FRICTION, lambda: _friction_gate(context, settings)),
        (GATE_ATR, lambda: _atr_gate(context, settings)),
        (GATE_OCCUPANCY, lambda: _occupancy_gate(context)),
        (GATE_TRADES_TODAY, lambda: _trades_today_gate(context, settings)),
        (GATE_BREAKER, lambda: _breaker_gate(context, breaker_status)),
        (GATE_LLM_BUDGET, lambda: _llm_budget_gate(settings)),
    )
    return tuple(_run(code, check) for code, check in checks)


def first_failure(gates: Iterable[GateResult]) -> GateResult | None:
    """The first failed gate in the order given, or None when every gate passed."""
    return next((gate for gate in gates if not gate.passed), None)


def failed_codes(gates: Iterable[GateResult]) -> tuple[str, ...]:
    """Codes of every failed gate, in order (for logs and the dashboard)."""
    return tuple(gate.code for gate in gates if not gate.passed)
