"""Engine building blocks: bars window, context, candidate bookkeeping, panel, shadow order."""

from __future__ import annotations

import json
import math
from dataclasses import replace

import pytest

from app.v6.cycle_codes import HoldReason
from app.v6.cycle_types import CandidateAssessment, DeliberationInput, DeskViews
from app.v6.deliberation import candidates as cand
from app.v6.deliberation.context_builder import (
    LOOKBACK_BARS, ContextRequest, build_context, calendar_horizon_s, load_bars,
)
from app.v6.deliberation.cycle_draft import CycleDraft
from app.v6.deliberation.engine import rules_provider_for
from app.v6.deliberation.panel import Baseline, rules_baseline, run_panel, unavailable_panel
from app.v6.deliberation.shadow import shadow_order
from app.v6.providers.base import ERR_UNAVAILABLE, PROVIDER_STATUS_FAILED, PROVIDER_STATUS_OK
from app.v6.schemas.agents import StructureView
from app.v6.schemas.snapshot import ProbeBlock

from . import engine_fixtures_v6 as ef
from .cycle_fixtures_v6 import protocol_decision, shadow_intent, structure_payload

PROBE = {"book_depth": 0, "trade_ticks_count": 0, "real_volume_count": 0,
         "dom_synthetic": False, "gmt_offset_s": 10800, "dst_active": True,
         "calendar_events_seen": 0}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def context_for(snapshot=None, bars=None, *, probe=None, now: float = ef.RECEIVED):
    snap = snapshot or ef.engine_snapshot()
    request = ContextRequest(cycle_id="c-1", snapshot=snap, received_at=ef.RECEIVED,
                             probe=probe)
    window = load_bars(ef.MemoryBars(ef.history() if bars is None else bars), snap)
    return build_context(request, window, ef.settings(), now)


# --- bars window and context ---------------------------------------------------------
def test_load_bars_merges_snapshot_rows_and_cuts_forming_bars() -> None:
    history = ef.history()
    future = ef.flat_bars(ef.M15, ef.AS_OF, ef.AS_OF + 4 * ef.M15, 50.0)
    reader = ef.MemoryBars({**history, "M15": history["M15"] + future})
    fresh = [[ef.T_BAR, 4300.0, 4311.0, 4299.0, 4310.0, 500, 20]]
    snapshot = ef.engine_snapshot(bars={"M15": fresh})
    window = load_bars(reader, snapshot)
    assert window["M15"][-1].t == ef.T_BAR and window["M15"][-1].c == 4310.0
    assert all(bar.t + ef.M15 <= ef.AS_OF for bar in window["M15"])
    assert "M1" not in window
    assert len(window["M5"]) == LOOKBACK_BARS["M5"]


def test_future_bars_in_the_store_change_nothing() -> None:
    history = ef.history()
    later = {tf: bars + ef.flat_bars(step, ef.AS_OF, ef.AS_OF + 3 * 3600, 90.0)
             for (tf, bars), step in zip(history.items(), (ef.M5, ef.M15, ef.H1))}
    later["D1"] = history["D1"]
    assert context_for(bars=later).features == context_for().features


def test_context_uses_the_snapshot_probe_before_the_cached_one() -> None:
    cached = ProbeBlock.model_validate_json(json.dumps(PROBE))
    assert context_for(probe=cached).probe == cached
    own = ef.engine_snapshot(probe={**PROBE, "dom_synthetic": True})
    context = context_for(own, probe=cached)
    assert context.probe.dom_synthetic is True
    assert context.features["dom_synthetic"] == 1.0


def test_context_refuses_a_non_finite_clock() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        context_for(now=math.nan)


def test_calendar_horizon_is_the_pending_order_lifetime() -> None:
    assert calendar_horizon_s(ef.settings()) == 2 * ef.M15
    assert calendar_horizon_s(ef.settings(pending_expiry_bars=8)) == 8 * ef.M15


# --- candidates ------------------------------------------------------------------------
def test_raw_geometry_for_sells_and_impossible_targets() -> None:
    sell = ef.candidate(side="sell", invalidation=ef.PRICE + 5.0)
    assert cand._raw_geometry(sell, 2.0) == (ef.PRICE + 5.0, ef.PRICE - 10.0)
    deep = ef.candidate(side="sell", entry=10.0, invalidation=100.0)
    assert cand._raw_geometry(deep, 2.0) == (100.0, 10.0)


def test_verdicts_ignore_a_view_of_the_wrong_type() -> None:
    context = context_for()
    pool = cand.assess_candidates(context, (ef.candidate(),), ef.settings(),
                                  friction_price=0.4)
    view = StructureView.model_validate(structure_payload(), strict=False)
    labelled = cand.label_verdicts(pool, price_action=view)  # type: ignore[arg-type]
    assert [item.verdict for item in labelled] == ["unranked"]
    assert pool.find("missing") is None


# --- panel -------------------------------------------------------------------------------
def _inputs() -> DeliberationInput:
    context = context_for()
    pool = cand.assess_candidates(context, (ef.candidate(),), ef.settings(),
                                  friction_price=0.4)
    return DeliberationInput(context=context, gates=(), offered=pool.offered)


@pytest.mark.anyio
async def test_the_rules_panel_reuses_the_baseline() -> None:
    clock = ef.clock_at()
    rules = rules_provider_for(ef.settings(), clock)
    inputs = _inputs()
    baseline = await rules_baseline(rules, inputs, ef.AS_OF + 120, clock)
    panel = await run_panel(rules, rules, inputs, baseline, ef.AS_OF + 120, clock)
    assert panel.views == baseline.views
    assert (panel.provider, panel.status) == ("rules", PROVIDER_STATUS_OK)
    assert [record.role for record in panel.records] == ["chief"]


@pytest.mark.anyio
async def test_a_panel_past_its_deadline_fails() -> None:
    clock = ef.clock_at()
    rules = rules_provider_for(ef.settings(), clock)
    inputs = _inputs()
    baseline = await rules_baseline(rules, inputs, clock.epoch - 1, clock)
    assert baseline.views == DeskViews()
    assert {record.error_code for record in baseline.records} == {"PROVIDER_DEADLINE_PASSED"}
    panel = await run_panel(rules, rules, inputs, baseline, clock.epoch - 1, clock)
    assert (panel.decision, panel.status) == (None, PROVIDER_STATUS_FAILED)


# --- shadow order and draft ---------------------------------------------------------------
def test_shadow_order_needs_an_exit_plan() -> None:
    context = context_for()
    item = CandidateAssessment(candidate=ef.candidate(), verdict="chosen", stop=4292.0,
                               target=4316.0, available_from=ef.AS_OF + 3600)
    with pytest.raises(ValueError, match="exit plan"):
        shadow_order(context, item, protocol_decision(), source="rules",
                     remaining_loss_usd=60.0, settings=ef.settings())


def test_a_draft_clamps_a_backwards_clock() -> None:
    draft = CycleDraft(request=ef.request(), backend="rules", started_at=ef.RECEIVED)
    outcome = draft.update(provider="rules").finish("HOLD", HoldReason.GATE, "x" * 500,
                                                    ef.RECEIVED - 5)
    assert outcome.result.timings.total_ms == 0
    assert len(outcome.result.hold_detail) == 300
    assert outcome.calendar is None and outcome.context is None
    assert replace(draft).bare() == draft


@pytest.mark.parametrize(("status", "kept"), [("ENTER", True), ("ENTER_SHADOW", True),
                                              ("LATE", False)])
def test_a_draft_keeps_the_sized_order_only_for_entries(status: str, kept: bool) -> None:
    intent = shadow_intent()
    draft = CycleDraft(request=ef.request(), backend="operator", started_at=ef.RECEIVED)
    reason = None if kept else HoldReason.LATE
    result = draft.update(shadow_intent=intent).finish(status, reason, "", ef.RECEIVED).result
    assert (result.shadow_intent is intent) is kept and result.status == status


def test_the_unavailable_panel_names_the_operator() -> None:
    panel = unavailable_panel(Baseline(views=DeskViews(), records=()))
    assert (panel.provider, panel.status, panel.decision) == (
        "operator", PROVIDER_STATUS_FAILED, None)
    assert [(r.role, r.source, r.error_code) for r in panel.records] == [
        ("chief", "operator", ERR_UNAVAILABLE)]
