"""
Nested drawdown circuit breakers: 3% daily, 6% weekly, 10% monthly (plan section 6
gate 10, knowledge/09 section 6.4). The percentages come from V6Settings, which
may only tighten the ceilings in `risk.limits`.

Periods are keyed in UTC:
  daily    "YYYY-MM-DD"  the UTC calendar day, from 00:00:00 UTC (the EA's day too)
  weekly   "YYYY-Www"    the ISO-8601 week, from Monday 00:00:00 UTC; the year is the
                         ISO week-numbering year, so 2027-01-01 belongs to "2026-W53"
  monthly  "YYYY-MM"     the UTC calendar month, from the 1st at 00:00:00 UTC

Either of two measures trips a period, both realised plus floating:
  equity   account equity now against the equity at the start of the period
  v6_pnl   V6 P&L realised in the period plus the open V6 P&L now, as a loss against
           the capital V6 sizes on: min(period start equity, V6_SIZING_EQUITY_BASIS_USD).
           A large demo account must not hide V6 losing 3% of the $2,000 it risks.
Reaching a limit exactly counts as a breach.

`evaluate_breakers` is pure. Persistence goes through `LedgerCycles`
(v6_breaker_state): a trip keeps its first reason, survives restarts and period
rollover, and is cleared only by `reset_breaker`, which names the actor and is
refused while the same scope is still in breach. Acting on a trip (HALTED,
FLATTEN, CANCEL_PENDING) is the runtime's job; the BREAKER gate reads a
`BreakerStatus`. The ledger is synchronous, so async callers wrap the persistence
calls in `asyncio.to_thread`.
"""

from __future__ import annotations

import logging
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_FLOOR, Decimal
from typing import Final

from ..config import V6Settings
from ..cycle_types import MarketContext
from ..ledger_cycles import BreakerScope, LedgerCycles
from ..ledger_cycles_schema import BreakerRecord
from ..market.sessions import MAX_SUPPORTED_EPOCH
from . import limits

logger = logging.getLogger(__name__)

SCOPES: Final[tuple[BreakerScope, ...]] = ("daily", "weekly", "monthly")
SECONDS_PER_DAY: Final[int] = 86_400
REASON_EQUITY: Final[str] = "EQUITY_DRAWDOWN"
REASON_V6_PNL: Final[str] = "V6_PNL_DRAWDOWN"
REASON_SEPARATOR: Final[str] = "+"
BREAKER_UNAVAILABLE: Final[str] = "UNAVAILABLE"
NOT_EVALUATED_DETAIL: Final[str] = "breakers were not evaluated for this cycle"
LEDGER_FAILED_DETAIL: Final[str] = "breaker ledger unavailable"
RESET_DONE: Final[str] = "RESET_DONE"
RESET_REFUSED_CONDITION: Final[str] = "RESET_REFUSED_CONDITION"
RESET_NOT_EVALUATED: Final[str] = "RESET_NOT_EVALUATED"
RESET_NOT_TRIPPED: Final[str] = "RESET_NOT_TRIPPED"
# A reset must be judged on a fresh evaluation (build it from the latest poll).
RESET_EVALUATION_MAX_AGE_S: Final[int] = 60
ACTOR_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._:@-]{1,64}$")
PCT_DECIMALS: Final[int] = 4
LIMIT_CEILINGS: Final[dict[str, float]] = {
    "daily": limits.MAX_DAILY_LOSS_PCT,
    "weekly": limits.MAX_WEEKLY_LOSS_PCT,
    "monthly": limits.MAX_MONTHLY_LOSS_PCT,
}

_PERCENT: Final[Decimal] = Decimal(100)
_CENT: Final[Decimal] = Decimal("0.01")
_UNIX_EPOCH: Final[datetime] = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ONE_SECOND: Final[timedelta] = timedelta(seconds=1)


# --- periods -------------------------------------------------------------------

def _utc(epoch: int) -> datetime:
    if isinstance(epoch, bool) or not isinstance(epoch, int):
        raise TypeError(f"epoch must be int UTC seconds, got {type(epoch).__name__}")
    if not 0 <= epoch <= MAX_SUPPORTED_EPOCH:
        raise ValueError(f"epoch {epoch} is outside [0, {MAX_SUPPORTED_EPOCH}]")
    return _UNIX_EPOCH + timedelta(seconds=epoch)


def _require_scope(scope: str) -> None:
    if scope not in SCOPES:
        raise ValueError(f"unknown breaker scope {str(scope)[:16]!r}")


def period_key(scope: BreakerScope, epoch: int) -> str:
    """The v6_breaker_state key of the period containing `epoch` (see module docstring)."""
    _require_scope(scope)
    moment = _utc(epoch)
    if scope == "daily":
        return moment.strftime("%Y-%m-%d")
    if scope == "weekly":
        iso = moment.isocalendar()
        return f"{iso.year:04d}-W{iso.week:02d}"
    return moment.strftime("%Y-%m")


def period_start_epoch(scope: BreakerScope, epoch: int) -> int:
    """UTC epoch at which the period containing `epoch` started."""
    _require_scope(scope)
    moment = _utc(epoch)
    day_start = epoch - epoch % SECONDS_PER_DAY
    if scope == "daily":
        return day_start
    if scope == "weekly":
        return day_start - moment.weekday() * SECONDS_PER_DAY
    first = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (first - _UNIX_EPOCH) // _ONE_SECOND


# --- inputs --------------------------------------------------------------------

def _finite(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


@dataclass(frozen=True)
class PeriodInput:
    """Where one period started. `realized_v6` is signed USD and includes today's deals."""

    scope: BreakerScope
    start_equity: float
    realized_v6: float

    def __post_init__(self) -> None:
        _require_scope(self.scope)
        if not (_finite(self.start_equity) and self.start_equity > 0):
            raise ValueError(f"{self.scope}: start_equity must be positive and finite")
        if not _finite(self.realized_v6):
            raise ValueError(f"{self.scope}: realized_v6 must be finite")


@dataclass(frozen=True)
class BreakerInputs:
    """Account state at `as_of_epoch` plus one PeriodInput per scope."""

    as_of_epoch: int
    equity: float
    floating_v6: float
    periods: tuple[PeriodInput, ...]

    def __post_init__(self) -> None:
        _utc(self.as_of_epoch)
        object.__setattr__(self, "periods", tuple(self.periods))
        if not (_finite(self.equity) and self.equity >= 0) or not _finite(self.floating_v6):
            raise ValueError("equity must be finite and >= 0; floating_v6 must be finite")
        scopes = [period.scope for period in self.periods]
        if sorted(scopes) != sorted(SCOPES):
            raise ValueError("exactly one PeriodInput per scope (daily, weekly, monthly)")


def account_usable(login: str, equity: float) -> bool:
    """False for a terminal that is not connected yet (login 0 or no equity)."""
    return bool(login.strip("0")) and _finite(equity) and equity > 0


def inputs_from_context(context: MarketContext, *, weekly: PeriodInput,
                        monthly: PeriodInput) -> BreakerInputs:
    """Daily figures from the snapshot, weekly/monthly anchors from the runtime.

    The EA keeps the UTC day's first equity and today's realised V6 P&L; the
    runtime supplies the week and month anchors (first account mark of the period,
    realised V6 results including today). Floating V6 P&L is profit + swap of the
    V6 positions in the snapshot. Raises ValueError when an input is unusable
    (no equity, login 0, no day-start equity); report it as `unavailable`.
    """
    if not account_usable(context.account.login, context.account.equity):
        raise ValueError("account login or equity is unusable (terminal not connected?)")
    day = context.day
    daily = PeriodInput(scope="daily", start_equity=day.day_start_equity,
                        realized_v6=day.realized_today)
    floating = math.fsum(position.profit + position.swap for position in context.positions)
    return BreakerInputs(as_of_epoch=context.as_of_epoch, equity=context.account.equity,
                         floating_v6=floating, periods=(daily, weekly, monthly))


# --- evaluation ----------------------------------------------------------------

@dataclass(frozen=True)
class ScopeCheck:
    """One period's measures. Percentages are losses (negative for a gain)."""

    scope: BreakerScope
    period_key: str
    period_start_epoch: int
    limit_pct: float
    equity_drawdown_pct: float
    v6_loss_pct: float
    remaining_usd: float          # loss still allowed before this scope trips, >= 0
    reasons: tuple[str, ...] = ()

    @property
    def breached(self) -> bool:
        return bool(self.reasons)

    @property
    def reason(self) -> str:
        return REASON_SEPARATOR.join(self.reasons)


@dataclass(frozen=True)
class BreakerEvaluation:
    as_of_epoch: int
    checks: tuple[ScopeCheck, ...]   # daily, weekly, monthly

    def __post_init__(self) -> None:
        object.__setattr__(self, "checks", tuple(self.checks))
        if tuple(check.scope for check in self.checks) != SCOPES:
            raise ValueError("an evaluation holds one check per scope: daily, weekly, monthly")

    @property
    def breaches(self) -> tuple[ScopeCheck, ...]:
        return tuple(check for check in self.checks if check.breached)

    @property
    def remaining_loss_usd(self) -> float:
        """Smallest loss that would trip any breaker; feed it to the sizer."""
        return min(check.remaining_usd for check in self.checks)

    def check(self, scope: BreakerScope) -> ScopeCheck:
        _require_scope(scope)
        return self.checks[SCOPES.index(scope)]


def _dec(value: float) -> Decimal:
    return Decimal(repr(float(value)))


def _pct(loss: Decimal, base: Decimal) -> float:
    return float(round(loss / base * _PERCENT, PCT_DECIMALS))


def _limit_pct(scope: str, settings: V6Settings) -> float:
    value = getattr(settings, f"{scope}_loss_pct")
    if not (_finite(value) and 0 < value <= LIMIT_CEILINGS[scope]):
        raise ValueError(f"{scope} loss limit {value!r} is outside (0, {LIMIT_CEILINGS[scope]}]")
    return float(value)


def _check_scope(inputs: BreakerInputs, period: PeriodInput, limit_pct: float,
                 basis_cap: Decimal) -> ScopeCheck:
    start = _dec(period.start_equity)
    v6_basis = min(start, basis_cap)
    limit = _dec(limit_pct) / _PERCENT
    equity_loss = start - _dec(inputs.equity)
    v6_loss = -(_dec(period.realized_v6) + _dec(inputs.floating_v6))
    measures = ((REASON_EQUITY, equity_loss, start), (REASON_V6_PNL, v6_loss, v6_basis))
    reasons = tuple(name for name, loss, base in measures if loss >= limit * base)
    remaining = max(min(limit * base - loss for _, loss, base in measures), Decimal(0))
    as_of = inputs.as_of_epoch
    return ScopeCheck(
        scope=period.scope, period_key=period_key(period.scope, as_of),
        period_start_epoch=period_start_epoch(period.scope, as_of), limit_pct=limit_pct,
        equity_drawdown_pct=_pct(equity_loss, start), v6_loss_pct=_pct(v6_loss, v6_basis),
        remaining_usd=float(remaining.quantize(_CENT, rounding=ROUND_FLOOR)), reasons=reasons)


def evaluate_breakers(inputs: BreakerInputs, settings: V6Settings) -> BreakerEvaluation:
    """Pure: which scopes are in breach now, and how much loss is still allowed.

    Raises ValueError when the settings carry limits outside the built-in
    ceilings or a non-positive sizing basis (only possible without validation).
    """
    basis = settings.sizing_equity_basis_usd
    if not (_finite(basis) and basis > 0):
        raise ValueError("V6_SIZING_EQUITY_BASIS_USD must be positive and finite")
    by_scope = {period.scope: period for period in inputs.periods}
    checks = tuple(
        _check_scope(inputs, by_scope[scope], _limit_pct(scope, settings), _dec(basis))
        for scope in SCOPES)
    return BreakerEvaluation(as_of_epoch=inputs.as_of_epoch, checks=checks)


# --- status (what the BREAKER gate reads) ----------------------------------------

@dataclass(frozen=True)
class BreakerStatus:
    """Persisted trips plus the current evaluation. Fails closed without an evaluation."""

    active: tuple[BreakerRecord, ...] = ()
    evaluation: BreakerEvaluation | None = None
    unavailable: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "active", tuple(self.active))

    @property
    def breaches(self) -> tuple[ScopeCheck, ...]:
        return () if self.evaluation is None else self.evaluation.breaches

    @property
    def problem(self) -> str:
        if self.unavailable:
            return self.unavailable
        return NOT_EVALUATED_DETAIL if self.evaluation is None else ""

    @property
    def tripped(self) -> bool:
        return bool(self.active or self.breaches or self.problem)

    @property
    def remaining_loss_usd(self) -> float:
        """Loss allowance for the sizer; 0 whenever the status blocks trading."""
        if self.evaluation is None or self.tripped:
            return 0.0
        return self.evaluation.remaining_loss_usd

    @property
    def labels(self) -> tuple[str, ...]:
        entries = [f"{record.scope}:{record.period_key}" for record in self.active]
        entries += [f"{check.scope}:{check.period_key}" for check in self.breaches]
        if self.problem:
            entries.append(BREAKER_UNAVAILABLE)
        return tuple(dict.fromkeys(entries))

    @property
    def detail(self) -> str:
        parts = [f"{r.scope}:{r.period_key} tripped ({r.reason})" for r in self.active]
        parts += [f"{c.scope}:{c.period_key} in breach ({c.reason})" for c in self.breaches]
        if self.problem:
            parts.append(self.problem)
        return "; ".join(parts)


# --- persistence -----------------------------------------------------------------

def trip_breakers(ledger: LedgerCycles, evaluation: BreakerEvaluation,
                  now: float) -> tuple[BreakerRecord, ...]:
    """Persist every scope in breach; an already tripped period keeps its first reason."""
    return tuple(ledger.trip_breaker(check.scope, check.period_key, check.reason, now)
                 for check in evaluation.breaches)


def refresh_breakers(ledger: LedgerCycles, inputs: BreakerInputs, settings: V6Settings,
                     now: float) -> BreakerStatus:
    """Evaluate, persist new trips and read every active breaker (sync; use to_thread).

    A ledger failure is logged and reported as `unavailable`, which fails the gate.
    Invalid settings raise ValueError, as in `evaluate_breakers`.
    """
    evaluation = evaluate_breakers(inputs, settings)
    try:
        trip_breakers(ledger, evaluation, now)
        active = ledger.active_breakers()
    except sqlite3.Error as exc:
        logger.error("v6 breaker ledger failed (%s); failing closed", type(exc).__name__)
        return BreakerStatus(evaluation=evaluation, unavailable=LEDGER_FAILED_DETAIL)
    return BreakerStatus(active=active, evaluation=evaluation)


@dataclass(frozen=True)
class ResetResult:
    reset: bool
    code: str
    detail: str = ""


def _reset_refusal(scope: BreakerScope, evaluation: BreakerEvaluation | None,
                   now: float) -> ResetResult | None:
    if evaluation is None:
        return ResetResult(False, RESET_NOT_EVALUATED, NOT_EVALUATED_DETAIL)
    age = now - evaluation.as_of_epoch
    if not (math.isfinite(age) and abs(age) <= RESET_EVALUATION_MAX_AGE_S):
        return ResetResult(False, RESET_NOT_EVALUATED,
                           f"evaluation is not within {RESET_EVALUATION_MAX_AGE_S} s of now")
    check = evaluation.check(scope)
    if check.breached:
        return ResetResult(False, RESET_REFUSED_CONDITION,
                           f"{scope} {check.period_key} is still in breach ({check.reason})")
    return None


def reset_breaker(ledger: LedgerCycles, scope: BreakerScope, key: str, *, actor: str,
                  evaluation: BreakerEvaluation | None, now: float) -> ResetResult:
    """Manually clear one tripped breaker, unless its scope is still in breach.

    `evaluation` must be current (within RESET_EVALUATION_MAX_AGE_S of `now`).
    Raises ValueError for an unknown scope or an actor outside ACTOR_PATTERN.
    """
    _require_scope(scope)
    if not isinstance(actor, str) or not ACTOR_PATTERN.match(actor):
        raise ValueError("actor must be 1-64 characters of [A-Za-z0-9._:@-]")
    refusal = _reset_refusal(scope, evaluation, now)
    if refusal is not None:
        logger.warning("v6 breaker reset refused: %s %s by %s (%s)",
                       scope, key[:64], actor, refusal.code)
        return refusal
    if not ledger.reset_breaker(scope, key, actor, now):
        return ResetResult(False, RESET_NOT_TRIPPED, f"{scope} {key[:64]} is not tripped")
    logger.warning("v6 breaker reset: %s %s by %s", scope, key[:64], actor)
    return ResetResult(True, RESET_DONE, f"{scope} {key[:64]} reset by {actor}")
