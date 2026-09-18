"""Hard gates (plan section 6): one result per GATE_CODES entry, in order, never short-circuited."""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from typing import Any

import pytest

from app.v6.config import V6Settings
from app.v6.cycle_codes import (
    F_ATR_M5, F_ATR_M5_POINTS, F_FRICTION_ATR, F_FRICTION_PRICE, GATE_CODES, HoldReason,
    hold_reason_for_gates,
)
from app.v6.cycle_types import MarketContext
from app.v6.ledger_cycles_schema import BreakerRecord
from app.v6.market.sessions import session_state
from app.v6.risk.breakers import (
    BreakerInputs, BreakerStatus, PeriodInput, evaluate_breakers,
)
from app.v6.risk.gates import (
    CAL_ASOF_MISMATCH, HALT_SOURCE_EA, HALT_SOURCE_FILE, SPEC_MISMATCH, SPEC_OK, SPEC_UNKNOWN,
    VALUE_ERROR, VALUE_MISSING, RuntimeGateState, evaluate_gates, failed_codes, first_failure,
)
from app.v6.schemas.snapshot import PendingOrderBlock, PositionBlock, SymbolSpecBlock
from app.v6.types import GateResult, SymbolSpec

from .cycle_fixtures_v6 import calendar, market_context
from .payloads_v6 import BAR_OPEN, H1, M15, RECEIVED_AT, snapshot_payload

GOOD_SESSION_EPOCH = BAR_OPEN + H1   # Wed 13:00 UTC: main window, no LBMA pause, no data bar
AS_OF = BAR_OPEN + M15
NOW = RECEIVED_AT + 2.0
GOOD_FEATURES = {F_ATR_M5: 3.1, F_ATR_M5_POINTS: 310.0, F_FRICTION_PRICE: 0.2,
                 F_FRICTION_ATR: 0.0645}
READY = RuntimeGateState(warmed_up=True)


def _settings(**overrides: Any) -> V6Settings:
    return V6Settings(_env_file=None, **overrides)


def _context(features: dict[str, float] | None = None, **changes: Any) -> MarketContext:
    base = market_context(features=GOOD_FEATURES if features is None else features)
    return replace(base, **{"session": session_state(GOOD_SESSION_EPOCH), **changes})


def _clean_breakers() -> BreakerStatus:
    periods = tuple(PeriodInput(scope, 2000.0, 0.0) for scope in ("daily", "weekly", "monthly"))
    inputs = BreakerInputs(as_of_epoch=AS_OF, equity=2000.0, floating_v6=0.0, periods=periods)
    return BreakerStatus(evaluation=evaluate_breakers(inputs, _settings()))


def _evaluate(context: MarketContext | None = None, *, cal: Any = None, runtime: Any = READY,
              settings: V6Settings | None = None, breakers: BreakerStatus | None = None,
              now: float = NOW) -> tuple[GateResult, ...]:
    ctx = _context() if context is None else context
    return evaluate_gates(ctx, calendar() if cal is None else cal, runtime,
                          _settings() if settings is None else settings,
                          _clean_breakers() if breakers is None else breakers, now)


def _gate(gates: tuple[GateResult, ...], code: str) -> GateResult:
    return next(gate for gate in gates if gate.code == code)


def _spec(**changes: Any) -> SymbolSpec:
    data = {**snapshot_payload()["symbol_spec"], **changes}
    return SymbolSpecBlock.model_validate_json(json.dumps(data)).to_spec()


def _position() -> PositionBlock:
    return PositionBlock.model_validate_json(json.dumps({
        "ticket": 7, "magic": 250570, "side": "sell", "volume": 0.01, "price_open": 4300.0,
        "sl": 4310.0, "tp": 4280.0, "profit": 1.0, "swap": 0.0, "open_epoch": BAR_OPEN,
        "comment": "Q6:abcdefgh2345", "mae_points": 0.0, "mfe_points": 0.0}))


def _pending() -> PendingOrderBlock:
    return PendingOrderBlock.model_validate_json(json.dumps({
        "ticket": 8, "magic": 250570, "order_type": "BUY_LIMIT", "price": 4295.0,
        "sl": 4285.0, "tp": 4315.0, "volume": 0.01, "expiration_epoch": AS_OF + 2 * M15,
        "comment": "Q6:abcdefgh2345"}))


def _with(**block_updates: dict[str, Any]) -> MarketContext:
    """Context whose snapshot blocks (quote, day, ea_state) carry `block_updates`."""
    base = _context()
    return replace(base, **{name: getattr(base, name).model_copy(update=update)
                            for name, update in block_updates.items()})


def _closing_at(close_epoch: int) -> MarketContext:
    """A context whose snapshot claims an M15 bar closing at `close_epoch`."""
    return _context(bar_open_epoch=close_epoch - M15, as_of_epoch=close_epoch,
                    sent_at_epoch=int(RECEIVED_AT))


def _record(scope: str = "daily", key: str = "2026-09-16") -> BreakerRecord:
    return BreakerRecord(scope, key, True, "EQUITY_DRAWDOWN", float(AS_OF), None, None)


# --- the full table ------------------------------------------------------------


def test_all_gates_pass_in_the_documented_order() -> None:
    gates = _evaluate()

    assert tuple(gate.code for gate in gates) == GATE_CODES
    assert all(gate.passed for gate in gates), failed_codes(gates)
    assert first_failure(gates) is None
    assert hold_reason_for_gates(gates) is None


# Each case breaks exactly one gate; every other gate must still be evaluated and pass.
Case = Callable[[], dict[str, Any]]
SINGLE_FAILURES: dict[str, tuple[Case, str, object]] = {
    "halt-file": (lambda: {"runtime": RuntimeGateState(
        warmed_up=True, halt_sources=[HALT_SOURCE_FILE])}, "HALTED", HALT_SOURCE_FILE),
    "ea-halt": (lambda: {"context": _with(ea_state={"halted": True})}, "HALTED", HALT_SOURCE_EA),
    "warming-up": (lambda: {"runtime": RuntimeGateState(warmed_up=False)}, "WARMUP",
                   "WARMING_UP"),
    "real-account": (lambda: {"context": _context(trade_mode="REAL")}, "ACCOUNT_POLICY",
                     "POLICY_REAL_REFUSED"),
    "live-server": (lambda: {"context": _context(server="Broker-Live")}, "ACCOUNT_POLICY",
                    "POLICY_SERVER_NOT_DEMO"),
    "stale-snapshot": (lambda: {"now": RECEIVED_AT + 20.5}, "SNAPSHOT_AGE", 20.5),
    "received-after-now": (lambda: {"now": RECEIVED_AT - 1.0}, "SNAPSHOT_AGE", -1.0),
    "clock-not-finite": (lambda: {"now": math.nan}, "SNAPSHOT_AGE", VALUE_MISSING),
    # A still-forming bar (or a PC clock far off) claims a close the adapter has not reached.
    "bar-close-in-future": (lambda: {"context": _closing_at(int(NOW) + 6)}, "SNAPSHOT_AGE",
                            2.0),
    "ea-clock-behind": (lambda: {"context": _context(sent_at_epoch=AS_OF - 5)}, "CLOCK_SKEW",
                        6.0),
    "ea-clock-ahead": (lambda: {"context": _context(sent_at_epoch=AS_OF + 10)}, "CLOCK_SKEW",
                       -9.0),
    "spec-unknown": (lambda: {"context": _context(spec=_spec(calc_loss_per_price=0.0))},
                     "SPEC", SPEC_UNKNOWN),
    "spread": (lambda: {"context": _with(quote={"spread_points": 51})}, "SPREAD", 51),
    "us-data-bar": (lambda: {"context": _context(session=session_state(AS_OF))}, "SESSION",
                    "US_DATA_BAR"),
    "blackout": (lambda: {"cal": calendar(blackout=True)}, "NEWS", "BLACKOUT"),
    "pre-event": (lambda: {"cal": replace(calendar(True), codes=("CAL_PRE_EVENT",))}, "NEWS",
                  "CAL_PRE_EVENT"),
    "calendar-stale": (lambda: {"cal": replace(calendar(), stale=True)}, "NEWS", "CAL_STALE"),
    "stale-coded-once": (lambda: {"cal": replace(calendar(), stale=True, codes=("CAL_STALE",))},
                         "NEWS", "CAL_STALE"),
    "calendar-other-bar": (lambda: {"cal": replace(calendar(), as_of_epoch=AS_OF - M15 - 1)},
                           "NEWS", CAL_ASOF_MISMATCH),
    "friction": (lambda: {"context": _context({**GOOD_FEATURES, F_FRICTION_ATR: 0.15})},
                 "FRICTION_ATR", 0.15),
    "friction-missing": (lambda: {"context": _context({F_ATR_M5_POINTS: 310.0})},
                         "FRICTION_ATR", VALUE_MISSING),
    "friction-zero-atr": (lambda: {"context": _context(
        {F_ATR_M5_POINTS: 310.0, F_ATR_M5: 0.0, F_FRICTION_PRICE: 0.2})}, "FRICTION_ATR",
        VALUE_MISSING),
    "atr-floor": (lambda: {"context": _context({**GOOD_FEATURES, F_ATR_M5_POINTS: 249.9})},
                  "ATR_M5", 249.9),
    "atr-missing": (lambda: {"context": _context({F_FRICTION_ATR: 0.05})}, "ATR_M5",
                    VALUE_MISSING),
    "position-open": (lambda: {"context": _context(positions=(_position(),))}, "OCCUPANCY", 1),
    "order-pending": (lambda: {"context": _context(pending_orders=(_pending(),))},
                      "OCCUPANCY", 1),
    "trades-today": (lambda: {"context": _with(day={"trades_today": 8})}, "TRADES_TODAY", 8),
    "breaker-tripped": (lambda: {"breakers": replace(_clean_breakers(), active=(_record(),))},
                        "BREAKER", "daily:2026-09-16"),
    "breaker-unevaluated": (lambda: {"breakers": BreakerStatus()}, "BREAKER", "UNAVAILABLE"),
    "ea-local-breaker": (lambda: {"context": _with(ea_state={"local_breaker": "daily"})},
                         "BREAKER", "ea:daily"),
}


@pytest.mark.parametrize("case", sorted(SINGLE_FAILURES))
def test_each_gate_fails_on_its_own(case: str) -> None:
    build, code, value = SINGLE_FAILURES[case]

    gates = _evaluate(**build())

    assert tuple(gate.code for gate in gates) == GATE_CODES
    assert failed_codes(gates) == (code,)
    failed = first_failure(gates)
    assert failed is not None and failed.value == value


@pytest.mark.parametrize(
    "kwargs",
    [
        {"now": RECEIVED_AT + 20.0},
        {"now": RECEIVED_AT},
        {"context": _closing_at(int(NOW) + 5)},
        {"context": _context(sent_at_epoch=AS_OF - 4)},
        {"context": _context(sent_at_epoch=AS_OF + 6)},
        {"context": _with(quote={"spread_points": 50})},
        {"context": _context({**GOOD_FEATURES, F_FRICTION_ATR: 0.1499})},
        {"context": _context({**GOOD_FEATURES, F_ATR_M5_POINTS: 250.0})},
        {"context": _with(day={"trades_today": 3})},
        {"cal": replace(calendar(), as_of_epoch=AS_OF + M15)},
        {"context": _context(spec=_spec(calc_profit_per_price=102.0))},
        {"context": _context(spec=_spec(calc_loss_per_price=98.0))},
        {"settings": _settings(backend="operator")},
    ],
    ids=["age-20s", "age-0s", "close-5s-ahead", "skew+5s", "skew-5s", "spread-50",
         "friction-0.1499", "atr-250", "trades-3", "calendar-900s", "tick-value+2%",
         "tick-loss-2%", "operator-on-demo"],
)
def test_boundaries_pass(kwargs: dict[str, Any]) -> None:
    gates = _evaluate(**kwargs)

    assert failed_codes(gates) == ()


def test_several_failures_are_all_reported_and_the_first_decides() -> None:
    context = _with(quote={"spread_points": 51}, ea_state={"halted": True})

    gates = _evaluate(context, runtime=RuntimeGateState(warmed_up=False), now=NOW + 60)

    assert failed_codes(gates) == ("HALTED", "WARMUP", "SNAPSHOT_AGE", "SPREAD")
    assert hold_reason_for_gates(gates) is HoldReason.HALTED


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [({"now": NOW + 60}, HoldReason.STALE), ({"breakers": BreakerStatus()}, HoldReason.BREAKER),
     ({"context": _with(quote={"spread_points": 99})}, HoldReason.GATE)],
)
def test_failed_gates_map_to_hold_reasons(kwargs: dict[str, Any], reason: HoldReason) -> None:
    assert hold_reason_for_gates(_evaluate(**kwargs)) is reason


def test_non_finite_receive_time_fails_both_freshness_gates() -> None:
    gates = _evaluate(_context(received_at=math.nan))

    assert failed_codes(gates) == ("SNAPSHOT_AGE", "CLOCK_SKEW")
    assert _gate(gates, "CLOCK_SKEW").value == VALUE_MISSING


def test_a_raising_gate_fails_closed_without_hiding_the_rest(
        caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="app.v6.risk.gates"):
        gates = _evaluate(runtime=object())

    assert failed_codes(gates) == ("HALTED", "WARMUP")
    assert {_gate(gates, code).value for code in ("HALTED", "WARMUP")} == {VALUE_ERROR}
    assert "v6 gate HALTED raised" in caplog.text


# --- SPEC ------------------------------------------------------------------------


def test_verified_server_spec_passes_and_notes_the_wrong_reported_value() -> None:
    gate = _gate(_evaluate(), "SPEC")

    assert (gate.passed, gate.value, gate.limit) == (True, SPEC_OK, 0.02)
    assert "reported tick_value=0.1 is off by 90.0% (informational only)" in gate.detail


def test_reported_values_that_agree_are_noted() -> None:
    spec = _spec(tick_value=1.0, tick_value_loss=1.0)

    gate = _gate(_evaluate(_context(spec=spec)), "SPEC")

    assert gate.passed and "agree with the contract" in gate.detail


@pytest.mark.parametrize(
    ("spec", "value", "fragment"),
    [
        (_spec(calc_profit_per_price=0.0, calc_loss_per_price=0.0), SPEC_UNKNOWN,
         "source=reported"),
        (_spec(calc_profit_per_price=0.0), SPEC_UNKNOWN, "source=mixed"),
        (SymbolSpec(digits=2, point=0.01, tick_size=0.01, tick_value=1.0, tick_value_loss=1.0,
                    contract_size=100.0, volume_min=0.01, volume_step=0.01, volume_max=100.0),
         SPEC_UNKNOWN, "reported tick values not available"),
        (replace(_spec(), tick_size=0.0), SPEC_UNKNOWN, "must be positive"),
        (replace(_spec(), tick_value_loss=math.inf), SPEC_UNKNOWN, "must be positive"),
        (_spec(calc_profit_per_price=105.0), SPEC_MISMATCH, "deviation=0.0500"),
        (_spec(calc_loss_per_price=97.0), SPEC_MISMATCH, "deviation=0.0300"),
        (_spec(calc_profit_per_price=10.0, calc_loss_per_price=10.0), SPEC_MISMATCH,
         "deviation=0.9000"),
    ],
    ids=["no-calc", "one-calc", "direct-spec", "zero-tick-size", "inf-loss",
         "profit-5pct", "loss-3pct", "calc-10x-off"],
)
def test_spec_failures(spec: SymbolSpec, value: str, fragment: str) -> None:
    gates = _evaluate(_context(spec=spec))

    gate = _gate(gates, "SPEC")
    assert failed_codes(gates) == ("SPEC",)
    assert (gate.passed, gate.value) == (False, value)
    assert fragment in gate.detail


# --- derived features, settings ----------------------------------------------------


def test_friction_ratio_falls_back_to_price_over_atr() -> None:
    features = {F_ATR_M5: 4.0, F_ATR_M5_POINTS: 400.0, F_FRICTION_PRICE: 0.2}

    gate = _gate(_evaluate(_context(features)), "FRICTION_ATR")

    assert (gate.passed, gate.value, gate.limit) == (True, 0.05, 0.15)


def test_atr_points_fall_back_to_price_over_point() -> None:
    gates = _evaluate(_context({F_ATR_M5: 2.4, F_FRICTION_ATR: 0.05}))

    assert (_gate(gates, "ATR_M5").passed, _gate(gates, "ATR_M5").value) == (False, 240.0)


@pytest.mark.parametrize("point", [0.0, None, True])
def test_atr_fallback_needs_a_usable_point(point: Any) -> None:
    context = _context({F_ATR_M5: 3.1, F_FRICTION_ATR: 0.05},
                       spec=replace(_spec(), point=point))

    assert _gate(_evaluate(context), "ATR_M5").value == VALUE_MISSING


def test_raw_account_uses_the_tighter_spread_limit() -> None:
    gates = _evaluate(_with(quote={"spread_points": 21}),
                      settings=_settings(account_type="raw"))

    gate = _gate(gates, "SPREAD")
    assert (gate.passed, gate.value, gate.limit, gate.detail) == (
        False, 21, 20, "account_type=raw")


def test_tightened_settings_are_used() -> None:
    settings = _settings(friction_atr_max=0.06, min_atr_m5_points=320, max_trades_per_day=1,
                         snapshot_stale_s=5, max_clock_skew_s=1)
    context = _with(day={"trades_today": 1})

    gates = _evaluate(context, settings=settings, now=RECEIVED_AT + 6)

    assert failed_codes(gates) == ("SNAPSHOT_AGE", "FRICTION_ATR", "ATR_M5", "TRADES_TODAY")


def test_allowed_logins_apply_to_the_account_gate() -> None:
    gate = _gate(_evaluate(settings=_settings(V6_ALLOWED_LOGINS="999")), "ACCOUNT_POLICY")

    assert (gate.passed, gate.value) == (False, "POLICY_LOGIN_NOT_ALLOWED")


@pytest.mark.parametrize("trade_mode", ["CONTEST", "REAL"])
def test_the_operator_backend_is_refused_for_non_demo_accounts(trade_mode: str) -> None:
    context = _context(trade_mode=trade_mode)

    gate = _gate(_evaluate(context, settings=_settings(backend="operator")), "ACCOUNT_POLICY")

    assert (gate.passed, gate.value) == (False, "POLICY_OPERATOR_DEMO_ONLY")


def test_breaker_gate_lists_every_active_period() -> None:
    status = BreakerStatus(active=(_record(), _record("weekly", "2026-W38")))
    context = _with(ea_state={"local_breaker": "daily"})

    gate = _gate(_evaluate(context, breakers=status), "BREAKER")

    assert gate.value == "daily:2026-09-16,weekly:2026-W38,UNAVAILABLE,ea:daily"
    assert gate.detail.endswith("EA local daily breaker is active")


# --- RuntimeGateState --------------------------------------------------------------


def test_runtime_gate_state_is_frozen_and_keyword_only() -> None:
    state = RuntimeGateState(warmed_up=False, halt_sources=["DASHBOARD", "OPERATOR"],
                             warmup_detail="M15 sessions 7/10")

    assert state.halt_sources == ("DASHBOARD", "OPERATOR") and state.halted
    assert not READY.halted
    with pytest.raises(FrozenInstanceError):
        state.warmed_up = True  # type: ignore[misc]
    with pytest.raises(TypeError):
        RuntimeGateState(True)  # type: ignore[misc]
    gate = _gate(_evaluate(runtime=state), "WARMUP")
    assert gate.detail == "M15 sessions 7/10"


def test_a_future_bar_close_names_the_problem() -> None:
    gate = _gate(_evaluate(_closing_at(int(NOW) + 600)), "SNAPSHOT_AGE")
    assert (gate.passed, gate.detail) == (False, "bar close is in the future")

