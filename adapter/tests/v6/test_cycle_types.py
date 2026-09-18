"""Cycle records: invariants, immutability and JSON summaries."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace

import pytest

from app.v6.cycle_types import (
    CYCLE_STATUSES, PACKET_INPUT_KEY, CalendarAssessment, CandidateAssessment, CycleResult,
    CycleTimings, DeskViews, HoldReason, MarketContext, ProtocolDecision, ViewRecord,
    input_from_packet, rules_packet,
)
from app.v6.providers.base import ERR_TIMEOUT, ProviderResult
from app.v6.schemas.snapshot import ProbeBlock
from app.v6.types import Bar, GateResult

from .cycle_fixtures_v6 import (
    CANDIDATE_ID, CYCLE_ID, EVENT_ID, assessment, calendar, candidate, chief_decision,
    deliberation_input, desk_views, enter_result, hold_result, market_context,
    protocol_decision, protocol_input, shadow_intent, timings,
)
from .payloads_v6 import BAR_OPEN, M15, as_snapshot, snapshot_payload


def test_hold_reason_values_are_stable_codes() -> None:
    assert HoldReason.GATE == "APP-V6-GATE"
    assert str(HoldReason.LATE) == "APP-V6-LATE"
    assert all(reason.value.startswith("APP-V6-") for reason in HoldReason)
    assert len({reason.value for reason in HoldReason}) == len(HoldReason)
    assert CYCLE_STATUSES == ("HOLD", "ENTER", "ENTER_SHADOW", "LATE", "ABORTED", "ERROR")


@pytest.mark.parametrize("status", ["HOLD", "LATE", "ABORTED", "ERROR"])
def test_non_entry_status_needs_a_hold_reason(status: str) -> None:
    result = hold_result(status=status)

    assert result.shadow_intent is None
    with pytest.raises(ValueError):
        replace(result, hold_reason=None)
    with pytest.raises(ValueError):
        replace(result, shadow_intent=shadow_intent())


@pytest.mark.parametrize("status", ["ENTER_SHADOW", "ENTER"])
def test_entry_statuses_need_an_intent_and_no_hold_reason(status: str) -> None:
    result = replace(enter_result(), status=status)

    with pytest.raises(ValueError, match=status):
        replace(result, shadow_intent=None)
    with pytest.raises(ValueError):
        replace(result, hold_reason=HoldReason.VETO)


def test_duplicate_candidates_are_refused() -> None:
    with pytest.raises(ValueError):
        replace(enter_result(), candidates=(assessment(), assessment()))


def test_failed_gates_and_total_ms() -> None:
    result = hold_result()

    assert [gate.code for gate in result.failed_gates] == ["SPREAD"]
    assert result.timings.total_ms == 250
    assert CycleTimings(started_at=10.0, finished_at=9.0).total_ms == 0


def test_result_is_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        enter_result().status = "HOLD"  # type: ignore[misc]


def test_summary_is_json_safe_and_complete() -> None:
    summary = enter_result().to_summary()
    decoded = json.loads(json.dumps(summary, allow_nan=False))

    assert decoded["status"] == "ENTER_SHADOW"
    assert decoded["hold_reason"] is None
    assert decoded["decision"]["candidate_id"] == CANDIDATE_ID
    assert decoded["views"]["structure"]["named_patterns"] == ["FLAG"]
    assert decoded["candidates"][0]["candidate"]["features"]["range_atr"] == 1.8
    assert decoded["shadow_intent"]["labels"] == ["UNMEASURED_GEOMETRY"]
    assert decoded["view_records"][2]["error_code"] == ERR_TIMEOUT
    assert decoded["protocol"]["size_multiplier"] == 1.0
    assert decoded["exit_plan"]["reward_r"] == 2.0


def test_summary_of_hold_uses_the_code_and_drops_non_finite() -> None:
    gate = GateResult(code="ATR_M5", passed=False, value=float("nan"), limit=250)
    result = replace(hold_result(reason=HoldReason.STALE), gates=(gate,),
                     protocol=protocol_decision("HOLD"))

    summary = result.to_summary()

    assert summary["hold_reason"] == "APP-V6-STALE"
    assert summary["gates"][0] == {"code": "ATR_M5", "passed": False, "value": None,
                                   "limit": 250, "detail": ""}
    assert summary["protocol"]["hold_reason"] == "APP-V6-VETO"
    assert summary["protocol"]["vetoes"] == ["VETO_NEWS_BLOCK"]


# --- parts --------------------------------------------------------------------


def test_candidate_assessment_invariants() -> None:
    base_item = assessment()

    with pytest.raises(ValueError):
        replace(base_item, stop=float("nan"))
    with pytest.raises(ValueError):
        replace(base_item, target=0.0)
    with pytest.raises(ValueError):
        replace(base_item, available_from=base_item.candidate.bar_t)


def test_shadow_intent_invariants() -> None:
    intent = shadow_intent()

    for changes in ({"size_multiplier": 1.2}, {"sl": 4300.0}, {"lots": 0.0},
                    {"risk_usd": 99.0}, {"side": "sell"}, {"tp": 4299.0}):
        with pytest.raises(ValueError):
            replace(intent, **changes)
    sell = replace(intent, side="sell", order_type="SELL_LIMIT", sl=4310.0, tp=4280.0)
    assert sell.side == "sell"


def test_view_record_needs_exactly_one_outcome() -> None:
    with pytest.raises(ValueError):
        ViewRecord(role="chief", source="rules", view=None)
    with pytest.raises(ValueError):
        ViewRecord(role="chief", source="rules", view=chief_decision(), error_code=ERR_TIMEOUT)


def test_view_record_from_provider_result() -> None:
    ok = ProviderResult.success(chief_decision(), model="m", latency_ms=5, tokens_in=7,
                                tokens_out=3, cost_usd=0.01)
    failed = ProviderResult.failure(ERR_TIMEOUT, latency_ms=20_000)

    record = ViewRecord.from_result("chief", "operator", ok)
    failure = ViewRecord.from_result("news_risk", "operator", failed)

    assert (record.view, record.model, record.tokens_in, record.cost_usd) == \
        (ok.view, "m", 7, 0.01)
    assert (failure.view, failure.error_code, failure.latency_ms) == (None, ERR_TIMEOUT, 20_000)


def test_calendar_assessment_event_ids() -> None:
    assert calendar().event_ids == frozenset({EVENT_ID})
    assert CalendarAssessment(as_of_epoch=0, blackout=True, codes=("CAL_STALE",),
                              next_event_minutes=None, last_event_minutes_ago=None,
                              stale=True).event_ids == frozenset()


# --- market context -----------------------------------------------------------


def _bar(t: int) -> Bar:
    return Bar(t=t, o=4300.0, h=4301.0, l=4299.0, c=4300.5)


def test_market_context_from_snapshot() -> None:
    ctx = market_context(bars={"M15": (_bar(BAR_OPEN - M15), _bar(BAR_OPEN))})

    assert ctx.cycle_id == CYCLE_ID
    assert ctx.as_of_epoch == BAR_OPEN + M15
    assert ctx.trade_mode == "DEMO"
    assert ctx.spec.tick_value == pytest.approx(1.0)
    assert ctx.spec.reported_tick_value == 0.1
    assert ctx.margin_per_lot_buy == 860.0
    assert ctx.spread_price == pytest.approx(0.2)
    assert ctx.mid == pytest.approx(4300.1)
    assert ctx.positions == () and ctx.pending_orders == ()


def test_market_context_mappings_are_read_only_copies() -> None:
    features = {"atr_m5": 3.0}
    ctx = market_context(features=features)
    features["atr_m5"] = 99.0

    assert ctx.features["atr_m5"] == 3.0
    with pytest.raises(TypeError):
        ctx.features["x"] = 1.0  # type: ignore[index]
    with pytest.raises(TypeError):
        ctx.bars["M1"] = ()  # type: ignore[index]


def test_market_context_refuses_a_forming_bar() -> None:
    with pytest.raises(ValueError, match="not closed"):
        market_context(bars={"H1": (_bar(BAR_OPEN),)})


def test_market_context_refuses_non_finite_features() -> None:
    with pytest.raises(ValueError, match="finite"):
        market_context(features={"atr_m5": float("inf")})


def test_market_context_requires_the_bar_close_as_of() -> None:
    ctx = market_context()

    with pytest.raises(ValueError, match="bar close"):
        replace(ctx, as_of_epoch=ctx.as_of_epoch + 1)


def test_an_m1_cycle_context_closes_its_minute() -> None:
    ctx = market_context()
    minute = replace(ctx, bar_open_epoch=ctx.as_of_epoch - 60)
    assert minute.as_of_epoch - minute.bar_open_epoch == 60


def test_candidate_fixture_matches_the_assessment() -> None:
    item: CandidateAssessment = assessment()

    assert item.candidate == candidate()
    assert item.available_from > item.candidate.bar_t + M15
    assert isinstance(chief_decision("HOLD").candidate_id, type(None))
    assert isinstance(timings(), CycleTimings)
    assert isinstance(market_context(), MarketContext)
    assert isinstance(enter_result(), CycleResult)


def test_market_context_carries_snapshot_metadata() -> None:
    snapshot = as_snapshot(snapshot_payload())
    ctx = market_context()

    assert ctx.sent_at_epoch == snapshot.sent_at_epoch
    assert ctx.server_gmt_offset_s == snapshot.server_gmt_offset_s
    assert ctx.ea_state == snapshot.ea_state
    assert ctx.day == snapshot.day


def test_market_context_probe_falls_back_to_the_cached_one() -> None:
    snapshot = as_snapshot(snapshot_payload())
    cached = ProbeBlock(book_depth=10, trade_ticks_count=0, real_volume_count=0,
                        dom_synthetic=True, gmt_offset_s=10800, dst_active=True,
                        calendar_events_seen=12)
    kwargs = {"cycle_id": CYCLE_ID, "received_at": 1.0, "bars": {},
              "session": market_context().session, "calendar": calendar(), "features": {}}

    own = snapshot.model_copy(update={"probe": cached.model_copy(update={"book_depth": 0})})

    with_cached = MarketContext.from_snapshot(snapshot, probe=cached, **kwargs)
    with_own = MarketContext.from_snapshot(own, probe=cached, **kwargs)

    assert snapshot.probe is None
    assert with_cached.probe == cached
    assert with_own.probe is not None and with_own.probe.book_depth == 0


def test_market_context_freezes_bar_lists_into_tuples() -> None:
    rows = [_bar(BAR_OPEN)]
    ctx = market_context(bars={"M15": rows})  # type: ignore[dict-item]
    rows.append(_bar(BAR_OPEN + M15))

    assert ctx.bars["M15"] == (_bar(BAR_OPEN),)


# --- deliberation input and packets --------------------------------------------


def test_deliberation_input_offers_ids_and_events() -> None:
    inputs = deliberation_input()

    assert inputs.offered_candidate_ids == frozenset({CANDIDATE_ID})
    assert inputs.offered_event_ids == frozenset({EVENT_ID})
    assert inputs.views == DeskViews()
    assert replace(inputs, views=desk_views()).views.price_action is not None


def test_deliberation_input_limits_what_can_be_offered() -> None:
    inputs = deliberation_input()
    items = [assessment(f"cand-{i}", verdict="unranked") for i in range(4)]

    with pytest.raises(ValueError, match="at most"):
        replace(inputs, offered=tuple(items))
    with pytest.raises(ValueError, match="at most"):
        replace(inputs, offered=(items[0], items[0]))
    with pytest.raises(ValueError, match="exit plan"):
        replace(inputs, offered=(replace(items[0], exit_plan=None),))
    assert replace(inputs, offered=()).offered_candidate_ids == frozenset()


def test_rules_packet_round_trip() -> None:
    inputs = deliberation_input()
    packet = rules_packet(inputs)

    assert input_from_packet(packet) is inputs
    assert set(packet) == {PACKET_INPUT_KEY}
    with pytest.raises(TypeError):
        packet[PACKET_INPUT_KEY] = None  # type: ignore[index]
    for bad in ({}, {PACKET_INPUT_KEY: "not an input"}):
        with pytest.raises(TypeError, match="DeliberationInput"):
            input_from_packet(bad)


# --- protocol types ---------------------------------------------------------------


def test_protocol_input_defaults() -> None:
    inputs = protocol_input()

    assert inputs.withdrawn_ids == frozenset()
    assert inputs.decision == chief_decision()
    assert inputs.structure_veto == "log"


def test_protocol_decision_defaults_are_the_cautious_ones() -> None:
    hold = ProtocolDecision(action="HOLD", hold_reason=HoldReason.CHIEF_HOLD,
                            candidate_id=None, size_multiplier=0.0)

    assert (hold.risk_tier, hold.order_style, hold.exit_profile) == \
        ("reduced", "LIMIT", "STANDARD")
    assert hold.vetoes == () and hold.detail == ""
    assert protocol_decision().candidate_id == CANDIDATE_ID


@pytest.mark.parametrize(
    "changes",
    [
        {"size_multiplier": 1.01},
        {"size_multiplier": -0.1},
        {"size_multiplier": float("nan")},
        {"vetoes": ("VETO_SOMETHING",)},
        {"candidate_id": None},
        {"hold_reason": HoldReason.VETO},
    ],
)
def test_protocol_decision_enter_invariants(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        replace(protocol_decision("ENTER"), **changes)


def test_protocol_decision_hold_needs_a_reason_but_may_name_the_pick() -> None:
    hold = protocol_decision("HOLD")

    with pytest.raises(ValueError, match="hold_reason"):
        replace(hold, hold_reason=None)
    assert replace(hold, candidate_id=CANDIDATE_ID,
                   hold_reason=HoldReason.LOW_CONVICTION).candidate_id == CANDIDATE_ID
