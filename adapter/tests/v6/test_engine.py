"""The Phase 2 engine on the rules backend: every tier-0 branch and the shadow entry."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any

import pytest

from app.v6.clock import FakeClock
from app.v6.cycle_codes import GATE_CODES, HoldReason
from app.v6.cycle_types import ProtocolDecision
from app.v6.deliberation import engine as engine_module
from app.v6.deliberation.engine import (
    DeliberationEngine, EngineDeps, build_engine, panel_for_backend, rules_provider_for,
)
from app.v6.providers.base import (
    ERR_UNAVAILABLE, PROVIDER_STATUS_FAILED, PROVIDER_STATUS_OK, PROVIDER_STATUS_SKIPPED,
)
from app.v6.risk.breakers import BreakerStatus
from app.v6.risk.gates import HALT_SOURCE_FILE
from app.v6.risk.limits import FRICTION_PRICE

from . import engine_fixtures_v6 as ef

OPERATOR = {"backend": "operator", "operator_token": "operator-token-" + "t" * 40}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def make_engine(*candidates: Any, clock: FakeClock | None = None, bars: Any = None,
                breakers: Any = None, **overrides: Any) -> tuple[DeliberationEngine, FakeClock]:
    config = ef.settings(**overrides)
    fake = clock or ef.clock_at()
    rules = rules_provider_for(config, fake)
    deps = EngineDeps(
        settings=config, clock=fake, bars=bars or ef.MemoryBars(ef.history()),
        breakers=breakers or ef.healthy_breakers(config), rules=rules,
        panel=panel_for_backend(config, rules), detector=ef.detector_of(*candidates))
    return DeliberationEngine(deps), fake


def verdicts(result: Any) -> dict[str, str]:
    return {item.candidate.candidate_id: item.verdict for item in result.candidates}


# --- shadow entry ------------------------------------------------------------------
@pytest.mark.anyio
async def test_rules_backend_reaches_a_shadow_entry() -> None:
    engine, _ = make_engine(ef.candidate())
    outcome = await engine.run(ef.request())
    result = outcome.result
    assert (result.status, result.hold_reason, result.hold_detail) == ("ENTER_SHADOW", None, "")
    assert (result.provider, result.provider_status, result.backend) == (
        "rules", PROVIDER_STATUS_OK, "rules")
    assert tuple(gate.code for gate in result.gates) == GATE_CODES
    assert all(gate.passed for gate in result.gates)
    intent = result.shadow_intent
    assert (intent.side, intent.order_type, intent.entry, intent.sl, intent.tp) == (
        "buy", "BUY_LIMIT", 4300.0, 4292.0, 4316.0)
    assert (intent.lots, intent.risk_usd, intent.risk_budget_usd) == (0.01, 8.4, 10.0)
    assert intent.valid_until_epoch == ef.AS_OF + 2 * ef.M15
    assert intent.source == "rules" and intent.risk_tier == "standard"
    assert verdicts(result) == {ef.CANDIDATE_ID: "chosen"}
    assert [(r.role, r.source) for r in result.view_records] == [
        ("price_action", "rules"), ("news_risk", "rules"), ("liquidity", "rules"),
        ("structure", "rules"), ("chief", "rules")]
    assert result.session_id == "sess-1"
    assert outcome.calendar is outcome.context.calendar
    assert outcome.context.features["friction_atr_m5"] == pytest.approx(0.05)


@pytest.mark.anyio
async def test_the_candidate_keeps_its_exit_plan_geometry_for_the_labeler() -> None:
    engine, _ = make_engine(ef.candidate())
    item = (await engine.run(ef.request())).result.candidates[0]
    assert (item.stop, item.target) == (4292.0, 4316.0)
    assert item.available_from == ef.AS_OF + 2 * ef.M15 + 8 * ef.M15
    assert item.exit_plan is not None and item.refusal is None


@pytest.mark.anyio
async def test_an_operator_backend_without_a_channel_holds() -> None:
    """Direction never comes from the rules desks when the operator backend is selected."""
    engine, _ = make_engine(ef.candidate(), **OPERATOR)
    result = (await engine.run(ef.request())).result
    assert (result.status, result.hold_reason) == ("HOLD", HoldReason.INVALID_VIEW)
    assert (result.backend, result.provider, result.provider_status) == (
        "operator", "operator", PROVIDER_STATUS_FAILED)
    assert result.shadow_intent is None and result.decision is None
    assert result.views.price_action is None and result.views.news_risk is not None
    last = result.view_records[-1]
    assert (last.role, last.source, last.error_code) == ("chief", "operator", ERR_UNAVAILABLE)
    assert verdicts(result) == {ef.CANDIDATE_ID: "unranked"}


def test_build_engine_selects_the_panel_by_backend() -> None:
    clock = ef.clock_at()
    rules_engine = build_engine(ef.settings(), clock, ef.MemoryBars({}), ef.healthy_breakers(
        ef.settings()))
    assert rules_engine.settings.backend == "rules"
    assert rules_engine.deps.panel is rules_engine.deps.rules
    other = build_engine(ef.settings(**OPERATOR), clock, ef.MemoryBars({}),
                         ef.healthy_breakers(ef.settings()))
    assert other.deps.panel is None


# --- tier 0 holds ------------------------------------------------------------------
@pytest.mark.anyio
async def test_a_halt_holds_with_the_full_gate_table_and_gated_candidates() -> None:
    engine, _ = make_engine(ef.candidate())
    result = (await engine.run(ef.request(halt_sources=(HALT_SOURCE_FILE,)))).result
    assert (result.status, result.hold_reason) == ("HOLD", HoldReason.HALTED)
    assert result.hold_detail == "failed gates: HALTED"
    assert len(result.gates) == len(GATE_CODES)
    assert result.provider_status == PROVIDER_STATUS_SKIPPED
    assert verdicts(result) == {ef.CANDIDATE_ID: "gated"}
    assert result.views.news_risk is not None, "R0 desk views are recorded on every cycle"
    assert {r.role for r in result.view_records} == {
        "price_action", "news_risk", "liquidity", "structure"}


@pytest.mark.anyio
async def test_warm_up_holds() -> None:
    engine, _ = make_engine(ef.candidate())
    result = (await engine.run(ef.request(warmed_up=False))).result
    assert result.hold_reason == HoldReason.WARMUP


@pytest.mark.anyio
async def test_a_calendar_blackout_is_a_gate_hold() -> None:
    engine, _ = make_engine(ef.candidate())
    snapshot = ef.engine_snapshot(calendar=ef.calendar_block(offset_s=10 * 60))
    result = (await engine.run(ef.request(snapshot))).result
    assert result.hold_reason == HoldReason.GATE
    assert "NEWS" in result.hold_detail


@pytest.mark.anyio
async def test_a_failing_breaker_source_fails_closed() -> None:
    async def broken(_context: Any) -> BreakerStatus:
        raise RuntimeError("ledger down")

    engine, _ = make_engine(ef.candidate(), breakers=broken)
    result = (await engine.run(ef.request())).result
    assert result.hold_reason == HoldReason.BREAKER
    breaker = next(gate for gate in result.gates if gate.code == "BREAKER")
    assert "breaker evaluation failed" in breaker.detail


@pytest.mark.anyio
async def test_no_candidate_holds() -> None:
    engine, _ = make_engine()
    result = (await engine.run(ef.request())).result
    assert (result.status, result.hold_reason) == ("HOLD", HoldReason.NO_CANDIDATE)
    assert result.candidates == ()


@pytest.mark.anyio
async def test_refused_exit_plans_are_recorded_with_raw_geometry() -> None:
    tight = ef.candidate(invalidation=ef.PRICE - 2.0)
    engine, _ = make_engine(tight)
    result = (await engine.run(ef.request())).result
    assert result.hold_reason == HoldReason.EXIT
    assert result.hold_detail.endswith("STOP_BELOW_FLOOR")
    item = result.candidates[0]
    assert item.verdict == "refused" and item.refusal.codes == ("STOP_BELOW_FLOOR",)
    assert (item.stop, item.target) == (ef.PRICE - 2.0, ef.PRICE + 4.0)
    assert item.exit_plan is None


@pytest.mark.anyio
async def test_refused_candidates_stay_refused_when_a_gate_fails() -> None:
    engine, _ = make_engine(ef.candidate(invalidation=ef.PRICE - 2.0))
    result = (await engine.run(ef.request(warmed_up=False))).result
    assert verdicts(result) == {result.candidates[0].candidate.candidate_id: "refused"}


@pytest.mark.anyio
async def test_without_a_session_only_tier_0_runs() -> None:
    engine, _ = make_engine(ef.candidate())
    result = (await engine.run(ef.request(session_id=None))).result
    assert (result.status, result.hold_reason) == ("HOLD", HoldReason.NO_SESSION)
    assert result.session_id is None
    assert verdicts(result) == {ef.CANDIDATE_ID: "unranked"}
    assert result.decision is None and result.protocol is None
    assert result.provider_status == PROVIDER_STATUS_SKIPPED


@pytest.mark.anyio
async def test_only_three_accepted_candidates_are_offered() -> None:
    found = [ef.candidate(setup=setup, invalidation=ef.PRICE - 8.0 - i)
             for i, setup in enumerate(("displacement", "orb", "retest", "engulfing"))]
    refused = ef.candidate(side="sell", invalidation=ef.PRICE - 1.0)
    engine, _ = make_engine(refused, *found)
    result = (await engine.run(ef.request())).result
    labels = verdicts(result)
    assert labels[refused.candidate_id] == "refused"
    assert labels[found[3].candidate_id] == "unranked"
    assert labels[found[0].candidate_id] == "chosen"
    ranked = result.views.price_action.ranked
    assert {r.candidate_id for r in ranked} == {c.candidate_id for c in found[:3]}
    assert {labels[c.candidate_id] for c in found[1:3]} <= {"take", "skip"}


# --- tier 1 holds ------------------------------------------------------------------
@pytest.mark.anyio
async def test_a_stop_too_wide_for_the_budget_is_a_size_hold() -> None:
    wide = ef.candidate(invalidation=ef.PRICE - 12.0)
    engine, _ = make_engine(wide)
    result = (await engine.run(ef.request())).result
    assert (result.status, result.hold_reason) == ("HOLD", HoldReason.SIZE)
    assert result.refusal.codes == ("MIN_LOT_WALL",)
    assert result.hold_detail == "sizing refused: MIN_LOT_WALL"
    assert result.exit_plan is not None and result.sizing is None
    assert verdicts(result) == {wide.candidate_id: "chosen"}
    assert result.shadow_intent is None


@pytest.mark.anyio
async def test_a_raw_account_charges_its_own_friction() -> None:
    engine, _ = make_engine(ef.candidate(), account_type="raw", max_spread_points=20)
    outcome = await engine.run(ef.request())
    assert outcome.context.features["friction_price"] == FRICTION_PRICE["raw"]
    assert outcome.result.sizing.loss_per_lot == pytest.approx(822.0)


@pytest.mark.anyio
async def test_a_cycle_past_the_deadline_is_late_before_tier_1() -> None:
    clock = ef.clock_at(ef.AS_OF + 121)
    engine, _ = make_engine(ef.candidate(), clock=clock, snapshot_stale_s=120)
    result = (await engine.run(ef.request())).result
    assert (result.status, result.hold_reason) == ("LATE", HoldReason.LATE)
    assert result.decision is None
    assert all(gate.passed for gate in result.gates)


@pytest.mark.anyio
async def test_a_panel_slower_than_the_deadline_is_late(
        monkeypatch: pytest.MonkeyPatch) -> None:
    clock = ef.clock_at(ef.AS_OF + 120 - 0.05)

    async def slow_panel(*_args: Any) -> Any:
        await asyncio.sleep(5)

    monkeypatch.setattr(engine_module, "run_panel", slow_panel)
    engine, _ = make_engine(ef.candidate(), clock=clock, snapshot_stale_s=120)
    result = (await engine.run(ef.request())).result
    assert (result.status, result.hold_reason) == ("LATE", HoldReason.LATE)


@pytest.mark.anyio
async def test_an_entry_decided_after_the_deadline_is_late(
        monkeypatch: pytest.MonkeyPatch) -> None:
    clock = ef.clock_at()
    real_resolve = engine_module.resolve

    def slow_resolve(*args: Any, **kwargs: Any) -> Any:
        clock.advance(600)
        return real_resolve(*args, **kwargs)

    monkeypatch.setattr(engine_module, "resolve", slow_resolve)
    engine, _ = make_engine(ef.candidate(), clock=clock)
    result = (await engine.run(ef.request())).result
    assert (result.status, result.hold_reason) == ("LATE", HoldReason.LATE)
    assert result.sizing is not None and result.shadow_intent is None
    assert result.timings.total_ms == 600_000


# --- errors ------------------------------------------------------------------------
class BrokenBars:
    def latest(self, tf: str, n: int) -> Any:
        raise RuntimeError("store unavailable")


@pytest.mark.anyio
async def test_a_tier_0_failure_is_an_error_cycle(caplog: pytest.LogCaptureFixture) -> None:
    engine, _ = make_engine(ef.candidate(), bars=BrokenBars())
    with caplog.at_level(logging.ERROR, logger="app.v6.deliberation.engine"):
        result = (await engine.run(ef.request())).result
    assert (result.status, result.hold_reason) == ("ERROR", HoldReason.ERROR)
    assert result.hold_detail == "tier0: RuntimeError"
    assert result.provider_status == PROVIDER_STATUS_SKIPPED
    assert result.cycle_id in caplog.text


@pytest.mark.anyio
async def test_a_tier_1_failure_keeps_the_tier_0_record(
        monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise ValueError("bad protocol input")

    monkeypatch.setattr(engine_module, "resolve", broken)
    engine, _ = make_engine(ef.candidate())
    result = (await engine.run(ef.request())).result
    assert (result.status, result.hold_detail) == ("ERROR", "tier1: ValueError")
    assert result.provider_status == "failed"
    assert len(result.gates) == len(GATE_CODES) and len(result.candidates) == 1


@pytest.mark.anyio
async def test_a_pick_outside_the_offer_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def rogue(*_args: Any, **_kwargs: Any) -> ProtocolDecision:
        return ProtocolDecision(action="ENTER", hold_reason=None, candidate_id="orb-buy-1",
                                size_multiplier=1.0)

    monkeypatch.setattr(engine_module, "resolve", rogue)
    engine, _ = make_engine(ef.candidate())
    result = (await engine.run(ef.request())).result
    assert (result.status, result.hold_detail) == ("ERROR", "tier1: ValueError")
    assert result.shadow_intent is None


@pytest.mark.anyio
async def test_a_record_that_cannot_be_built_still_yields_a_bare_error() -> None:
    twin = ef.candidate(invalidation=ef.PRICE - 1.0)
    engine, _ = make_engine(twin, replace(twin))
    result = (await engine.run(ef.request())).result
    assert (result.status, result.hold_detail) == ("ERROR", "record: ValueError")
    assert result.candidates == () and result.gates == ()


@pytest.mark.anyio
async def test_the_engine_is_deterministic_under_a_fake_clock() -> None:
    first, _ = make_engine(ef.candidate())
    second, _ = make_engine(ef.candidate())
    a = (await first.run(ef.request())).result.to_summary()
    b = (await second.run(ef.request())).result.to_summary()
    assert a == b
