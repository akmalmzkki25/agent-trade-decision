"""Drawdown breakers: UTC periods, pure evaluation, persistence and manual reset."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.v6.config import V6Settings
from app.v6.ledger_cycles import LedgerCycles
from app.v6.risk.breakers import (
    BREAKER_UNAVAILABLE, LEDGER_FAILED_DETAIL, NOT_EVALUATED_DETAIL, REASON_EQUITY,
    REASON_V6_PNL, RESET_DONE, RESET_NOT_EVALUATED, RESET_NOT_TRIPPED,
    RESET_REFUSED_CONDITION, BreakerEvaluation, BreakerInputs, BreakerStatus, PeriodInput,
    evaluate_breakers, inputs_from_context, period_key, period_start_epoch, refresh_breakers,
    reset_breaker, trip_breakers,
)
from app.v6.schemas.snapshot import PositionBlock

from .cycle_fixtures_v6 import market_context
from .payloads_v6 import BAR_OPEN, M15

AS_OF = BAR_OPEN + M15              # Wed 2026-09-16 12:15 UTC
TODAY, WEEK, MONTH = "2026-09-16", "2026-W38", "2026-09"
NOW = float(AS_OF + 1)


def utc(*parts: int) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp())


def _settings(**overrides: Any) -> V6Settings:  # the cases assume a $2,000 sizing basis
    return V6Settings(_env_file=None, **({"sizing_equity_basis_usd": 2000.0} | overrides))


def _inputs(equity: float = 2000.0, floating: float = 0.0, *, start: float = 2000.0,
            realized: float = 0.0, weekly: tuple[float, float] | None = None,
            as_of: int = AS_OF) -> BreakerInputs:
    week_start, week_realized = (start, realized) if weekly is None else weekly
    return BreakerInputs(as_of_epoch=as_of, equity=equity, floating_v6=floating, periods=(
        PeriodInput("daily", start, realized),
        PeriodInput("weekly", week_start, week_realized),
        PeriodInput("monthly", start, realized)))


def _evaluate(**kwargs: Any) -> BreakerEvaluation:
    return evaluate_breakers(_inputs(**kwargs), _settings())


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[LedgerCycles]:
    led = LedgerCycles(tmp_path / "breakers.db")
    yield led
    led.close()


# --- periods ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("epoch", "scope", "key", "start"),
    [
        (AS_OF, "daily", TODAY, utc(2026, 9, 16)),
        (AS_OF, "weekly", WEEK, utc(2026, 9, 14)),
        (AS_OF, "monthly", MONTH, utc(2026, 9, 1)),
        (utc(2026, 12, 31, 23, 59, 59), "daily", "2026-12-31", utc(2026, 12, 31)),
        (utc(2027, 1, 1), "daily", "2027-01-01", utc(2027, 1, 1)),
        (utc(2027, 1, 1), "weekly", "2026-W53", utc(2026, 12, 28)),
        (utc(2027, 1, 3, 23, 59, 59), "weekly", "2026-W53", utc(2026, 12, 28)),
        (utc(2027, 1, 4), "weekly", "2027-W01", utc(2027, 1, 4)),
        (utc(2027, 1, 1), "monthly", "2027-01", utc(2027, 1, 1)),
        (utc(2026, 3, 1), "monthly", "2026-03", utc(2026, 3, 1)),
        (utc(2026, 2, 28, 23, 59, 59), "monthly", "2026-02", utc(2026, 2, 1)),
    ],
)
def test_periods_are_keyed_in_utc(epoch: int, scope: str, key: str, start: int) -> None:
    assert period_key(scope, epoch) == key  # type: ignore[arg-type]
    assert period_start_epoch(scope, epoch) == start  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("scope", "epoch", "error"),
    [("yearly", AS_OF, ValueError), ("daily", -1, ValueError),
     ("daily", 5_000_000_000, ValueError), ("daily", float(AS_OF), TypeError),
     ("daily", True, TypeError)],
)
def test_period_inputs_are_validated(scope: str, epoch: Any, error: type[Exception]) -> None:
    with pytest.raises(error):
        period_key(scope, epoch)  # type: ignore[arg-type]
    with pytest.raises(error):
        period_start_epoch(scope, epoch)  # type: ignore[arg-type]


# --- inputs ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [("hourly", 2000.0, 0.0), ("daily", 0.0, 0.0), ("daily", float("nan"), 0.0),
     ("daily", 2000.0, float("inf")), ("daily", True, 0.0), ("daily", "2000", 0.0)],
)
def test_period_input_validation(args: tuple[Any, ...]) -> None:
    with pytest.raises(ValueError):
        PeriodInput(*args)


@pytest.mark.parametrize(
    "changes",
    [{"equity": -1.0}, {"equity": float("nan")}, {"floating_v6": float("inf")},
     {"periods": (PeriodInput("daily", 1.0, 0.0),)}, {"as_of_epoch": float(AS_OF)},
     {"periods": (PeriodInput("daily", 1.0, 0.0),) * 3}],
)
def test_breaker_inputs_validation(changes: dict[str, Any]) -> None:
    with pytest.raises((ValueError, TypeError)):
        replace(_inputs(), **changes)


def test_breaker_inputs_accept_periods_in_any_order_and_freeze_them() -> None:
    periods = [PeriodInput(scope, 1.0, 0.0) for scope in ("monthly", "daily", "weekly")]

    inputs = BreakerInputs(as_of_epoch=AS_OF, equity=1.0, floating_v6=0.0,
                           periods=periods)  # type: ignore[arg-type]

    assert isinstance(inputs.periods, tuple)


def _position(profit: float, swap: float) -> PositionBlock:
    return PositionBlock.model_validate_json(json.dumps({
        "ticket": 1, "magic": 250570, "side": "buy", "volume": 0.01, "price_open": 4300.0,
        "sl": 4290.0, "tp": 4320.0, "profit": profit, "swap": swap, "open_epoch": BAR_OPEN,
        "comment": "Q6:abcdefgh2345", "mae_points": 0.0, "mfe_points": 0.0}))


def test_inputs_from_context_use_the_snapshot_day_and_positions() -> None:
    base = market_context()
    context = replace(
        base, positions=(_position(-5.0, -1.0), _position(2.5, 0.0)),
        day=base.day.model_copy(update={"day_start_equity": 2100.0, "realized_today": -12.0}))
    weekly, monthly = PeriodInput("weekly", 2200.0, -30.0), PeriodInput("monthly", 2300.0, -40.0)

    inputs = inputs_from_context(context, weekly=weekly, monthly=monthly)

    assert (inputs.as_of_epoch, inputs.equity, inputs.floating_v6) == (AS_OF, 2010.5, -3.5)
    assert inputs.periods == (PeriodInput("daily", 2100.0, -12.0), weekly, monthly)
    no_day_start = replace(base, day=base.day.model_copy(update={"day_start_equity": 0.0}))
    with pytest.raises(ValueError, match="start_equity"):
        inputs_from_context(no_day_start, weekly=weekly, monthly=monthly)


# --- evaluation -------------------------------------------------------------------


def test_flat_account_has_full_allowances() -> None:
    evaluation = _evaluate()

    assert evaluation.breaches == ()
    assert [(c.scope, c.period_key, c.limit_pct, c.remaining_usd) for c in evaluation.checks] == [
        ("daily", TODAY, 3.0, 60.0), ("weekly", WEEK, 6.0, 120.0), ("monthly", MONTH, 10.0, 200.0)]
    assert evaluation.remaining_loss_usd == 60.0
    assert evaluation.check("weekly").period_start_epoch == utc(2026, 9, 14)


@pytest.mark.parametrize(
    ("kwargs", "daily_reasons", "weekly_reasons", "remaining"),
    [
        ({"equity": 1940.0}, (REASON_EQUITY,), (), 0.0),
        ({"equity": 1940.01}, (), (), 0.01),
        ({"equity": 1950.0, "floating": -20.0, "realized": -40.0}, (REASON_V6_PNL,), (), 0.0),
        ({"equity": 1880.0, "floating": -20.0, "realized": -100.0},
         (REASON_EQUITY, REASON_V6_PNL), (REASON_EQUITY, REASON_V6_PNL), 0.0),
        ({"equity": 1880.0, "start": 1900.0, "weekly": (2000.0, -100.0)}, (),
         (REASON_EQUITY,), 0.0),
        ({"equity": 2050.0, "realized": 50.0}, (), (), 110.0),
    ],
    ids=["equity-exactly-3pct", "just-under", "v6-pnl-only", "both-measures",
         "weekly-only", "gains-extend-the-allowance"],
)
def test_breach_table(kwargs: dict[str, Any], daily_reasons: tuple[str, ...],
                      weekly_reasons: tuple[str, ...], remaining: float) -> None:
    evaluation = _evaluate(**kwargs)

    assert evaluation.check("daily").reasons == daily_reasons
    assert evaluation.check("weekly").reasons == weekly_reasons
    assert evaluation.check("monthly").reasons == ()
    assert evaluation.remaining_loss_usd == remaining


def test_v6_loss_is_measured_against_the_sizing_basis() -> None:
    """A $100k demo account: equity barely moves, but V6 lost 3% of its $2,000."""
    check = _evaluate(start=100_000.0, equity=99_940.0, realized=-40.0,
                      floating=-20.0).check("daily")

    assert (check.reasons, check.reason) == ((REASON_V6_PNL,), REASON_V6_PNL)
    assert (check.equity_drawdown_pct, check.v6_loss_pct) == (0.06, 3.0)
    both = _evaluate(equity=1880.0, floating=-20.0, realized=-100.0).check("daily")
    assert (both.breached, both.reason) == (True, "EQUITY_DRAWDOWN+V6_PNL_DRAWDOWN")
    assert (both.equity_drawdown_pct, both.v6_loss_pct) == (6.0, 6.0)


def test_tighter_settings_trip_earlier() -> None:
    evaluation = evaluate_breakers(_inputs(equity=1960.0), _settings(daily_loss_pct=2.0))

    assert evaluation.check("daily").reasons == (REASON_EQUITY,)


@pytest.mark.parametrize(
    "update",
    [{"daily_loss_pct": 3.5}, {"weekly_loss_pct": 0.0}, {"monthly_loss_pct": float("nan")},
     {"sizing_equity_basis_usd": 0.0}, {"sizing_equity_basis_usd": float("inf")}],
)
def test_unvalidated_settings_outside_the_ceilings_are_refused(update: dict[str, Any]) -> None:
    settings = _settings().model_copy(update=update)

    with pytest.raises(ValueError):
        evaluate_breakers(_inputs(), settings)


def test_evaluation_shape_is_enforced() -> None:
    evaluation = _evaluate()

    with pytest.raises(ValueError, match="one check per scope"):
        BreakerEvaluation(as_of_epoch=AS_OF, checks=evaluation.checks[::-1])
    with pytest.raises(ValueError, match="unknown breaker scope"):
        evaluation.check("yearly")  # type: ignore[arg-type]
    with pytest.raises(FrozenInstanceError):
        evaluation.checks = ()  # type: ignore[misc]


# --- status -------------------------------------------------------------------------


def test_status_without_evaluation_fails_closed() -> None:
    status = BreakerStatus()

    assert status.tripped
    assert (status.problem, status.labels) == (NOT_EVALUATED_DETAIL, (BREAKER_UNAVAILABLE,))
    assert status.detail == NOT_EVALUATED_DETAIL
    assert status.remaining_loss_usd == 0.0


def test_clean_status_exposes_the_allowance() -> None:
    status = BreakerStatus(evaluation=_evaluate(equity=1990.0))

    assert not status.tripped
    assert (status.labels, status.detail, status.remaining_loss_usd) == ((), "", 50.0)


def test_breach_and_persisted_trip_share_one_label(ledger: LedgerCycles) -> None:
    evaluation = _evaluate(equity=1900.0)
    records = trip_breakers(ledger, evaluation, NOW)

    status = BreakerStatus(active=list(records), evaluation=evaluation)  # type: ignore[arg-type]

    assert isinstance(status.active, tuple)
    assert status.tripped and status.remaining_loss_usd == 0.0
    assert status.labels == (f"daily:{TODAY}",)
    assert status.detail == (f"daily:{TODAY} tripped ({REASON_EQUITY}); "
                             f"daily:{TODAY} in breach ({REASON_EQUITY})")


def test_unavailable_status_is_tripped_even_with_a_clean_evaluation() -> None:
    status = BreakerStatus(evaluation=_evaluate(), unavailable="inputs missing")

    assert status.tripped
    assert status.labels == (BREAKER_UNAVAILABLE,)
    assert status.detail == "inputs missing"


# --- persistence --------------------------------------------------------------------


def test_refresh_persists_trips_that_survive_restart_and_recovery(tmp_path: Path) -> None:
    path = tmp_path / "restart.db"
    first = LedgerCycles(path)
    tripped = refresh_breakers(first, _inputs(equity=1900.0), _settings(), NOW)
    first.close()

    reopened = LedgerCycles(path)
    try:
        # Equity recovered, but the persisted trip keeps the gate closed.
        status = refresh_breakers(reopened, _inputs(equity=2000.0), _settings(), NOW + 60)
        record = reopened.get_breaker("daily", TODAY)
    finally:
        reopened.close()

    assert tripped.tripped and [r.period_key for r in tripped.active] == [TODAY]
    assert status.tripped and status.breaches == ()
    assert record is not None and (record.reason, record.tripped_at) == (REASON_EQUITY, NOW)


def test_repeated_trips_keep_the_first_reason(ledger: LedgerCycles) -> None:
    trip_breakers(ledger, _evaluate(equity=1900.0), NOW)

    records = trip_breakers(ledger, _evaluate(equity=1850.0, realized=-150.0), NOW + 5)

    daily = next(r for r in records if r.scope == "daily")
    assert (daily.reason, daily.tripped_at) == (REASON_EQUITY, NOW)
    assert {r.scope for r in records} == {"daily", "weekly"}


def test_ledger_failure_is_reported_as_unavailable(
        ledger: LedgerCycles, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    def broken() -> tuple[()]:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ledger, "active_breakers", broken)

    with caplog.at_level(logging.ERROR, logger="app.v6.risk.breakers"):
        status = refresh_breakers(ledger, _inputs(), _settings(), NOW)

    assert status.tripped and status.unavailable == LEDGER_FAILED_DETAIL
    assert status.evaluation is not None and status.active == ()
    assert "OperationalError" in caplog.text


# --- manual reset ---------------------------------------------------------------------


def test_reset_is_refused_while_the_scope_is_in_breach(ledger: LedgerCycles) -> None:
    breach = _evaluate(equity=1900.0)
    trip_breakers(ledger, breach, NOW)

    result = reset_breaker(ledger, "daily", TODAY, actor="operator", evaluation=breach, now=NOW)

    assert (result.reset, result.code) == (False, RESET_REFUSED_CONDITION)
    assert "still in breach" in result.detail
    assert [r.period_key for r in ledger.active_breakers()] == [TODAY]


@pytest.mark.parametrize(
    ("evaluation", "now"),
    [(None, NOW), ("clean", NOW + 61), ("clean", NOW - 62), ("clean", float("nan"))],
)
def test_reset_needs_a_current_evaluation(ledger: LedgerCycles, evaluation: Any,
                                          now: float) -> None:
    trip_breakers(ledger, _evaluate(equity=1900.0), NOW)
    current = _evaluate() if evaluation == "clean" else None

    result = reset_breaker(ledger, "daily", TODAY, actor="operator", evaluation=current, now=now)

    assert (result.reset, result.code) == (False, RESET_NOT_EVALUATED)
    assert ledger.active_breakers() != ()


def test_reset_after_recovery_records_the_actor(
        ledger: LedgerCycles, caplog: pytest.LogCaptureFixture) -> None:
    trip_breakers(ledger, _evaluate(equity=1900.0), NOW)

    with caplog.at_level(logging.WARNING, logger="app.v6.risk.breakers"):
        result = reset_breaker(ledger, "daily", TODAY, actor="dashboard:alice",
                               evaluation=_evaluate(), now=NOW + 30)
    again = reset_breaker(ledger, "daily", TODAY, actor="dashboard:alice",
                          evaluation=_evaluate(), now=NOW + 31)

    record = ledger.get_breaker("daily", TODAY)
    assert (result.reset, result.code, again.code) == (True, RESET_DONE, RESET_NOT_TRIPPED)
    assert record is not None and (record.reset_by, record.reset_at) == ("dashboard:alice",
                                                                        NOW + 30)
    assert ledger.active_breakers() == ()
    assert "dashboard:alice" in caplog.text


def test_older_period_reset_depends_on_the_current_period(ledger: LedgerCycles) -> None:
    ledger.trip_breaker("daily", "2026-09-15", REASON_EQUITY, NOW - 86_400)

    refused = reset_breaker(ledger, "daily", "2026-09-15", actor="operator",
                            evaluation=_evaluate(equity=1900.0), now=NOW)
    done = reset_breaker(ledger, "daily", "2026-09-15", actor="operator",
                         evaluation=_evaluate(), now=NOW)

    assert (refused.code, done.code) == (RESET_REFUSED_CONDITION, RESET_DONE)


def test_reset_of_a_scope_ignores_breaches_elsewhere(ledger: LedgerCycles) -> None:
    ledger.trip_breaker("monthly", "2026-08", REASON_EQUITY, NOW - 30 * 86_400)
    daily_breach = _evaluate(equity=1940.0)

    result = reset_breaker(ledger, "monthly", "2026-08", actor="operator",
                           evaluation=daily_breach, now=NOW)

    assert result.code == RESET_DONE


@pytest.mark.parametrize(
    ("scope", "actor"),
    [("daily", ""), ("daily", "two words"), ("daily", "x" * 65), ("daily", None),
     ("yearly", "operator")],
)
def test_reset_arguments_are_validated(ledger: LedgerCycles, scope: str, actor: Any) -> None:
    with pytest.raises(ValueError):
        reset_breaker(ledger, scope, TODAY, actor=actor,  # type: ignore[arg-type]
                      evaluation=_evaluate(), now=NOW)
