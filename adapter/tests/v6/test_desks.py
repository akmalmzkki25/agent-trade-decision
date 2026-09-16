"""Deterministic desk views: price action, news risk, liquidity and structure."""

from __future__ import annotations

from dataclasses import fields, replace
from itertools import product
from typing import Any, get_args

import pytest

from app.v6.cycle_types import (
    CAL_PRE_EVENT, CAL_STALE, CAL_US_DATA_BAR, CalendarAssessment, CalendarEvent,
    CandidateAssessment, MarketContext, candidate_id_for,
)
from app.v6.desks import liquidity_view, news_risk_view, price_action_view, structure_view
from app.v6.desks import liquidity as liq
from app.v6.desks import news_risk as news
from app.v6.desks import price_action as pa
from app.v6.desks import structure as st
from app.v6.market.levels import Pivot, confirmed_pivots
from app.v6.market.sessions import session_state
from app.v6.schemas.agents import (
    MAX_REASON_CODES, NewsReason, PriceActionReason, PriceActionView, validate_view,
)
from app.v6.schemas.snapshot import ProbeBlock

from .cycle_fixtures_v6 import EVENT_ID, assessment
from .fixtures_v6 import random_walk_bars, range_bars, trend_bars
from .payloads_v6 import BAR_OPEN, M15, as_snapshot, snapshot_payload

HOUR = 3600
AS_OF = BAR_OPEN + M15
MID, LATE, OUTSIDE = BAR_OPEN + 2 * HOUR, BAR_OPEN + 4 * HOUR, BAR_OPEN - 3 * HOUR
RVOL, TVZ, WIDTH, EXT = "range_vol", "tick_volume_z", "or_width_atr_d1", "extension"
STRONG = {"body_ratio": 0.8, RVOL: 2.5, TVZ: 2.0}
CALM = {"spread_points": 20.0, "spread_pctl_hour": 0.5, "friction_atr_m5": 0.03}
UP, DOWN, SWING = {"structure_m15": 1.0}, {"structure_m15": -1.0}, {"structure_m15": 0.0}
RV = {"rv_ratio": 1.0}
PROBE = {"book_depth": 10, "trade_ticks_count": 0, "real_volume_count": 0, "dom_synthetic": True,
         "gmt_offset_s": 10_800, "dst_active": True, "calendar_events_seen": 5}
DISP, ENG, ORB, RET = "displacement", "engulfing", "orb", "retest"
GOOD, WEAK, EDGE = "STRONG_DISPLACEMENT", "WEAK_DISPLACEMENT", "NO_EDGE"
POOR = "SESSION_TIMING_POOR"


def cal(**overrides: Any) -> CalendarAssessment:
    base = CalendarAssessment(AS_OF, False, (), None, None, False)
    return replace(base, **overrides)


def event(offset_s: int, event_id: str = EVENT_ID) -> CalendarEvent:
    return CalendarEvent(event_id=event_id, source="mt5", time_epoch=AS_OF + offset_s,
                         currency="USD", importance="HIGH", code="cpi-yy")


def context(features: dict[str, float] | None = None, *, bar_open: int = BAR_OPEN,
            calendar: CalendarAssessment | None = None, session_at: int | None = None,
            bars: dict[str, Any] | None = None, probe: ProbeBlock | None = None,
            ticks: dict[str, int] | None = None) -> MarketContext:
    snapshot = as_snapshot(snapshot_payload(bar_open=bar_open))
    snapshot = snapshot.model_copy(update={"ticks": snapshot.ticks.model_copy(update=ticks or {})})
    as_of = bar_open + M15
    return MarketContext.from_snapshot(
        snapshot, cycle_id="cyc-test", received_at=float(as_of + 1), bars=bars or {},
        session=session_state(as_of if session_at is None else session_at),
        calendar=calendar or cal(as_of_epoch=as_of), features=features or {}, probe=probe)


def offer(setup: str = "displacement", side: str = "buy", features: dict[str, Any] = STRONG,
          *, codes: tuple[str, ...] = ("LEVEL_PDH", "LEVEL_CONFLUENCE"), reward_r: float = 2.0,
          variant: str = "") -> CandidateAssessment:
    base = assessment(candidate_id_for(setup, side, BAR_OPEN, variant), verdict="unranked")
    candidate = replace(base.candidate, setup=setup, side=side, features=dict(features),
                        reason_codes=codes)
    plan = replace(base.exit_plan, side=side, reward_r=reward_r)
    return replace(base, candidate=candidate, exit_plan=plan)


# --- price action ---------------------------------------------------------------
def test_abstains_without_candidates_and_takes_a_strong_aligned_displacement() -> None:
    assert price_action_view(context(), ()) == PriceActionView(abstain=True, ranked=())
    item = price_action_view(context(UP), (offer(),)).ranked[0]
    assert (item.verdict, item.conviction) == ("TAKE", 0.8)
    assert item.reason_codes == (
        "STRONG_DISPLACEMENT", "HTF_ALIGNED", "LEVEL_CONFLUENCE", "SESSION_TIMING_GOOD")


@pytest.mark.parametrize(("setup", "features", "quality", "code"), [
    (DISP, STRONG, 1, GOOD), (DISP, {"body_ratio": 0.8, RVOL: 2.5}, 1, GOOD),
    (DISP, {**STRONG, TVZ: 1.0}, -1, WEAK), (DISP, {**STRONG, "body_ratio": 0.5}, -1, WEAK),
    (DISP, {**STRONG, RVOL: 1.9}, -1, WEAK), (DISP, {"body_ratio": 0.8}, 0, EDGE),
    (DISP, {**STRONG, "body_ratio": float("nan")}, 0, EDGE), (ENG, {RVOL: 1.3}, 1, GOOD),
    (ENG, {}, 0, EDGE), (ENG, {RVOL: 1.2}, -1, WEAK), (ORB, {}, 0, EDGE),
    (ORB, {WIDTH: 0.61}, 1, "CONFIRMED_CLOSE"), (ORB, {WIDTH: 0.6}, 0, EDGE),
    (ORB, {WIDTH: 0.29}, -1, WEAK), (RET, {EXT: 1.2}, 1, "CLEAN_RETEST"),
    (RET, {EXT: True}, 0, EDGE), (RET, {EXT: 0.9}, -1, WEAK),
    (RET, {EXT: 1.4}, -1, "EXTENDED_MOVE"),
])
def test_quality_grading(setup: str, features: dict[str, Any], quality: int, code: str) -> None:
    signals, codes = pa.grade_candidate(context(), offer(setup, features=features))
    assert signals.quality == quality and code in codes


@pytest.mark.parametrize(("features", "session_at", "name", "value", "code"), [
    (DOWN, None, "htf", -1, "HTF_OPPOSED"), (SWING, None, "htf", 0, None),
    ({}, MID, "timing", 0, None), ({}, LATE, "timing", -1, POOR), ({}, OUTSIDE, "timing", -1, POOR),
    ({"friction_atr_m5": 0.08}, None, "cost", -1, "FRICTION_HIGH"),
    ({"friction_atr_m5": 0.079}, None, "cost", 0, None), ({"er_m15": 0.5}, None, "chop", 0, None),
    ({"er_m15": 0.2}, None, "chop", -1, "CHOPPY_CONTEXT"),
    ({"atr_m15": 0.0}, None, "reach", 0, None), ({"atr_m15": 9.0}, None, "reach", 0, None),
    ({"atr_m15": 3.0}, None, "reach", -1, "STOP_TOO_WIDE"),
])
def test_context_signals(features: dict[str, float], session_at: int | None, name: str,
                         value: int, code: str | None) -> None:
    signals, codes = pa.grade_candidate(context(features, session_at=session_at), offer())
    assert getattr(signals, name) == value
    assert code is None or code in codes


@pytest.mark.parametrize(("setup", "codes", "reward_r", "name", "value"), [
    (ORB, ("OR_WIDE",), 2.0, "quality", 1), (ORB, ("OR_NARROW",), 2.0, "quality", -1),
    (RET, ("FAST_EXTENSION",), 2.0, "quality", 1), (RET, ("SLOW_EXTENSION",), 2.0, "quality", -1),
    (DISP, ("LEVEL_PDH",), 2.0, "level", 0), (DISP, ("LEVEL_CONFLUENCE",), 2.0, "level", 1),
    (DISP, ("HTF_OPPOSED",), 2.0, "htf", -1), (DISP, ("HTF_ALIGNED",), 2.0, "htf", 1),
    (DISP, (), 1.4, "reward", -1), (DISP, (), 1.5, "reward", 0),
])
def test_candidate_signals_read_detector_codes(setup: str, codes: tuple[str, ...],
                                               reward_r: float, name: str, value: int) -> None:
    features = {ORB: {}, RET: {EXT: 1.2}}.get(setup, STRONG)
    ctx = context(DOWN if "HTF_ALIGNED" in codes else UP)
    item = offer(setup, features=features, codes=codes, reward_r=reward_r)
    assert getattr(pa.grade_candidate(ctx, item)[0], name) == value


def test_shadow_weight_and_off_window_continuation_are_never_taken() -> None:
    shadow = offer(ENG, features={RVOL: 1.5}, codes=("LEVEL_CONFLUENCE", "SHADOW_WEIGHT"))
    retest, ctx = offer(RET, features={EXT: 1.2}), context(UP)
    after_london = replace(ctx, session=replace(ctx.session, continuation_allowed=False))
    cases = ((ctx, shadow), (context(UP, session_at=MID), retest), (after_london, retest),
             (ctx, retest))
    items = [price_action_view(c, (i,)).ranked[0] for c, i in cases]
    assert [item.verdict for item in items] == ["SKIP", "SKIP", "SKIP", "TAKE"]
    assert "NO_EDGE" in items[0].reason_codes and items[0].note.endswith("shadow weight")
    assert "SESSION_TIMING_POOR" in items[2].reason_codes


def test_unknown_setup_has_no_edge_and_ranks_last() -> None:
    odd = offer(variant="z")
    odd = replace(odd, candidate=replace(odd.candidate, setup="sweep"))
    signals, codes = pa.grade_candidate(context(UP), odd)
    view = price_action_view(context(UP), (odd, offer("engulfing", features={})))
    assert signals.quality == 0 and "NO_EDGE" in codes
    assert view.ranked[-1].candidate_id == odd.candidate.candidate_id


@pytest.mark.parametrize(("features", "session_at", "codes", "verdict", "conviction"), [
    ({}, MID, (), "TAKE", 0.6), ({}, LATE, (), "SKIP", 0.55),
    ({**UP, "er_m15": 0.1}, None, (), "TAKE", 0.7),
    ({**UP, "friction_atr_m5": 0.1}, None, (), "SKIP", 0.65),
    (DOWN, None, ("LEVEL_CONFLUENCE",), "SKIP", 0.6),
])
def test_take_threshold_and_disqualifiers(features: dict[str, float], session_at: int | None,
                                          codes: tuple[str, ...], verdict: str, conviction: float
                                          ) -> None:
    item = price_action_view(context(features, session_at=session_at), (offer(codes=codes),))
    assert (item.ranked[0].verdict, item.ranked[0].conviction) == (verdict, conviction)


def test_ranking_take_first_then_conviction_then_setup_then_order() -> None:
    items = (offer(ENG, features={RVOL: 1.0}, variant="a"),
             offer("retest", features={"extension": 1.2}, variant="b"),
             offer("displacement", variant="c"), offer("displacement", variant="d"))
    ranked = price_action_view(context(UP), items).ranked
    tie = price_action_view(context(UP), (items[3], items[2])).ranked
    assert [r.candidate_id for r in ranked] == [items[i].candidate.candidate_id for i in (2, 1, 0)]
    assert [r.candidate_id for r in tie] == [items[i].candidate.candidate_id for i in (3, 2)]


def test_price_action_view_passes_validation() -> None:
    items = (offer(variant="a"), offer(ORB, "sell", {WIDTH: 0.7}, variant="b"))
    view = price_action_view(context(UP), items)
    offered = frozenset(item.candidate.candidate_id for item in items)
    assert validate_view("price_action", view.model_dump(mode="json"), offered) == view


def test_bad_ranking_inputs_are_refused() -> None:
    with pytest.raises(ValueError):
        pa.grade_candidate(context(), replace(offer(), exit_plan=None))
    for value in (2, -2, True):
        with pytest.raises(ValueError):
            pa.PaSignals(quality=value)


def test_conviction_is_monotone_and_code_priorities_are_complete() -> None:
    names = [item.name for item in fields(pa.PaSignals)]
    for combo in product((-1, 0, 1), repeat=len(names)):
        signals = pa.PaSignals(**dict(zip(names, combo)))
        score = pa.conviction_from(signals)
        assert 0.0 <= score <= 1.0
        for name, value in zip(names, combo):
            if value < 1:
                assert pa.conviction_from(replace(signals, **{name: value + 1})) >= score
    assert set(pa.CODE_PRIORITY) == set(get_args(PriceActionReason))
    assert set(news.CODE_PRIORITY) == set(get_args(NewsReason))
    assert len(pa.ordered_codes(get_args(PriceActionReason))) == MAX_REASON_CODES


# --- news risk ------------------------------------------------------------------
@pytest.mark.parametrize(("overrides", "features", "stance", "regime", "codes"), [
    ({}, RV, "CLEAR", "QUIET", ("NO_EVENTS",)),
    ({}, {}, "CLEAR", "UNCLEAR", ("DATA_MISSING", "NO_EVENTS")),
    ({"blackout": True, "codes": (CAL_PRE_EVENT,)}, RV, "BLOCK", "EVENT_RISK",
     ("EVENT_IMMINENT", "NO_EVENTS")),
    ({"blackout": True}, RV, "BLOCK", "EVENT_RISK", ("NO_EVENTS",)),
    ({"blackout": True, "stale": True, "codes": (CAL_STALE,)}, RV, "BLOCK", "UNCLEAR",
     ("CALENDAR_STALE",)),
    ({"stale": True}, RV, "BLOCK", "UNCLEAR", ("CALENDAR_STALE",)),
    ({"next_event_minutes": 45.0, "events": (event(45 * 60),)}, RV, "CAUTION", "EVENT_RISK",
     ("EVENT_IMMINENT", "HIGH_IMPACT_USD")),
    ({"last_event_minutes_ago": 30.0, "events": (event(-30 * 60),)}, RV, "CAUTION",
     "EVENT_RISK", ("EVENT_RECENT", "HIGH_IMPACT_USD")),
    ({"next_event_minutes": 105.0, "events": (event(105 * 60),)}, RV, "CLEAR", "QUIET",
     ("HIGH_IMPACT_USD",)),
    ({"codes": (CAL_US_DATA_BAR,)}, RV, "CAUTION", "EVENT_RISK", ("HIGH_IMPACT_USD", "NO_EVENTS")),
    ({"as_of_epoch": AS_OF - 2 * M15}, RV, "CAUTION", "QUIET", ("CALENDAR_STALE", "NO_EVENTS")),
    ({}, {"rv_ratio": 2.5}, "CAUTION", "UNCLEAR", ("VOL_ELEVATED", "NO_EVENTS")),
    ({"last_event_minutes_ago": 120.0, "events": (event(-2 * HOUR),)}, {"rv_ratio": 2.5},
     "CAUTION", "USD_DRIVEN", ("HIGH_IMPACT_USD", "VOL_ELEVATED")),
    ({"next_event_minutes": float("nan"), "last_event_minutes_ago": -5.0}, RV, "CLEAR", "QUIET",
     ("NO_EVENTS",)),
])
def test_news_risk_table(overrides: dict[str, Any], features: dict[str, float], stance: str,
                         regime: str, codes: tuple[str, ...]) -> None:
    calendar = cal(**overrides)
    view = news_risk_view(context(features, calendar=calendar))
    assert (view.stance, view.regime, view.reason_codes) == (stance, regime, codes)
    assert view.size_multiplier == {"CLEAR": 1.0, "CAUTION": 0.5, "BLOCK": 0.0}[stance]
    assert validate_view("news_risk", view.model_dump(mode="json"), frozenset(),
                         calendar.event_ids) == view


def test_event_ids_are_nearest_first_well_formed_unique_and_capped() -> None:
    events = tuple(event(i * 600 + 60, f"mt5:{i}") for i in range(12)) + (
        event(0, "bad id!"), event(30, "mt5:0"))
    view = news_risk_view(context(RV, calendar=cal(events=events)))
    assert view.event_ids == tuple(f"mt5:{i}" for i in range(10))


# --- liquidity ------------------------------------------------------------------
DOM = ("SPREAD_NORMAL", "DOM_SYNTHETIC")
WIDE = ("SPREAD_WIDE", "DOM_SYNTHETIC")


@pytest.mark.parametrize(("features", "kwargs", "stance", "codes"), [
    (CALM, {}, "OK", DOM), ({**CALM, "spread_points": 36.0}, {}, "NO_TRADE", WIDE),
    ({**CALM, "spread_points": 25.0}, {"max_spread_points": 20}, "NO_TRADE", WIDE),
    ({**CALM, "friction_atr_m5": 0.08}, {}, "NO_TRADE", ("FRICTION_HIGH", *DOM)),
    ({**CALM, "friction_atr_m5": 0.065}, {}, "CAUTION", ("FRICTION_HIGH", *DOM)),
    ({**CALM, "max_quote_gap_ms": 60_000.0}, {}, "NO_TRADE", ("QUOTE_GAP", *DOM)),
    ({**CALM, "max_quote_gap_ms": 15_000.0}, {}, "CAUTION", ("QUOTE_GAP", *DOM)),
    ({**CALM, "spread_pctl_hour": 0.9}, {}, "CAUTION", WIDE),
    ({"spread_points": 20.0, "friction_atr_m5": 0.03}, {}, "CAUTION", ("DATA_MISSING", *DOM)),
    ({**CALM, "tick_volume_z": -1.5}, {}, "CAUTION", ("ACTIVITY_LOW", *DOM)),
    ({**CALM, "tick_volume_z": 3.0}, {}, "CAUTION", ("ACTIVITY_HIGH", *DOM)),
    ({**CALM, "dom_synthetic": 0.0}, {}, "OK", DOM[:1]),
    ({**CALM, "dom_synthetic": 1.0}, {}, "OK", DOM),
])
def test_liquidity_table(features: dict[str, float], kwargs: dict[str, int], stance: str,
                         codes: tuple[str, ...]) -> None:
    view = liquidity_view(context(features), **kwargs)
    assert (view.stance, view.reason_codes, view.order_style) == (stance, codes, "LIMIT")
    assert view.size_multiplier == {"OK": 1.0, "CAUTION": 0.5, "NO_TRADE": 0.0}[stance]
    assert validate_view("liquidity", view.model_dump(mode="json"), frozenset()) == view


@pytest.mark.parametrize(("ticks", "bar_open", "session_at", "stance", "code"), [
    ({"quote_count": 0}, BAR_OPEN, None, "NO_TRADE", "QUOTES_THIN"),
    ({"quote_count": 100}, BAR_OPEN, None, "CAUTION", "QUOTES_THIN"),
    ({"window_s": 0, "quote_count": 0}, BAR_OPEN, None, "OK", None),
    ({"max_gap_ms": 70_000}, BAR_OPEN, None, "NO_TRADE", "QUOTE_GAP"),
    (None, BAR_OPEN, BAR_OPEN + int(9.5 * HOUR), "NO_TRADE", "ROLLOVER_NEAR"),
    (None, BAR_OPEN + 8 * HOUR + M15, None, "CAUTION", "ROLLOVER_NEAR"),
])
def test_liquidity_tick_and_rollover_checks(ticks: dict[str, int] | None, bar_open: int,
                                            session_at: int | None, stance: str, code: str | None
                                            ) -> None:
    view = liquidity_view(context(CALM, ticks=ticks, bar_open=bar_open, session_at=session_at))
    assert view.stance == stance
    assert code is None or code in view.reason_codes


def test_liquidity_falls_back_to_snapshot_values_and_probe() -> None:
    ctx = context({"friction_atr_m5": 0.03, "spread_pctl_hour": 0.5},
                  probe=ProbeBlock(**{**PROBE, "dom_synthetic": False}))
    assert liq.spread_points(ctx) == 20.0 and liq.quote_gap_ms(ctx) == 800.0
    assert not liq.dom_is_synthetic(ctx)
    assert liq.dom_is_synthetic(replace(ctx, probe=ProbeBlock(**PROBE)))
    assert liquidity_view(ctx).reason_codes == ("SPREAD_NORMAL",)


def test_liquidity_caps_codes_and_rejects_a_bad_spread_ceiling() -> None:
    features = {**CALM, "spread_points": 40.0, "friction_atr_m5": 0.2, "spread_pctl_hour": 0.95,
                "max_quote_gap_ms": 90_000.0, "tick_volume_z": 4.0}
    view = liquidity_view(context(features, ticks={"quote_count": 0}))
    assert view.stance == "NO_TRADE" and len(view.reason_codes) == MAX_REASON_CODES
    for ceiling in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            liquidity_view(context(CALM), max_spread_points=ceiling)  # type: ignore[arg-type]


# --- structure ------------------------------------------------------------------
TREND = {"er_m15": 0.5, "vr_m5": 1.2}
CHOP = {"er_m15": 0.1, "adx_h1": 15.0}
UP_CODES = ("HH_HL_SEQUENCE", "EFFICIENT_TREND", "MOMENTUM")


@pytest.mark.parametrize(("features", "regime", "multiplier", "codes"), [
    ({**UP, **TREND}, "TREND_UP", 1.0, UP_CODES),
    ({**DOWN, "adx_h1": 30.0, "ac1_m5": 0.1}, "TREND_DOWN", 1.0,
     ("LH_LL_SEQUENCE", "MOMENTUM", "ADX_STRONG")),
    (UP, "TRANSITION", 0.75, ("HH_HL_SEQUENCE",)),
    ({**SWING, **CHOP}, "RANGE", 1.0, ("RANGE_BOUND", "INEFFICIENT_CHOP", "ADX_WEAK")),
    ({**SWING, **TREND}, "TRANSITION", 0.75, ("RANGE_BOUND", *UP_CODES[1:])),
    ({**CHOP, "ac1_m5": -0.1, "vr_m5": 0.8}, "RANGE", 1.0,
     ("DATA_MISSING", "INEFFICIENT_CHOP", "MEAN_REVERTING", "ADX_WEAK")),
    ({"atr_ratio_m15_slot": 0.5, "er_m15": True}, "UNCLEAR", 0.75,
     ("DATA_MISSING", "ATR_CONTRACTING")),
    ({**UP, **TREND, "atr_ratio_m15_slot": 2.0}, "VOLATILE", 0.5, (*UP_CODES, "ATR_EXPANDING")),
    ({**UP, **TREND, "atr_ratio_m15_slot": 1.6}, "TREND_UP", 1.0, (*UP_CODES, "ATR_EXPANDING")),
])
def test_structure_regime_table(features: dict[str, float], regime: str, multiplier: float,
                                codes: tuple[str, ...]) -> None:
    view = structure_view(context(features))
    assert (view.regime, view.size_multiplier, view.counter_structure_veto) == (
        regime, multiplier, False)
    assert tuple(c for c in view.reason_codes if c != "NEAR_ROUND_NUMBER") == codes
    assert validate_view("structure", view.model_dump(mode="json"), frozenset()) == view


@pytest.mark.parametrize(("features", "sides", "veto", "flagged"), [
    (UP, ("sell",), True, True), (UP, ("sell", "buy"), False, True),
    (UP, ("buy",), False, False), (DOWN, ("buy", "buy"), True, True),
    (SWING, ("sell",), False, False), (UP, (), False, False),
])
def test_counter_structure(features: dict[str, float], sides: tuple[str, ...], veto: bool,
                           flagged: bool) -> None:
    items = tuple(offer(side=side, variant=f"v{i}") for i, side in enumerate(sides))
    view = structure_view(context(features), items)
    assert view.counter_structure_veto is veto
    assert (view.reason_codes[0] == "COUNTER_STRUCTURE") is flagged


@pytest.mark.parametrize(("kwargs", "code"), [
    ({}, "HH_HL_SEQUENCE"), ({"direction": "down"}, "LH_LL_SEQUENCE"), (None, "RANGE_BOUND")])
def test_swing_comes_from_bars_without_the_feature(kwargs: dict[str, str] | None,
                                                   code: str) -> None:
    start = AS_OF - 40 * M15
    bars = (range_bars(40, start_t=start) if kwargs is None
            else trend_bars(40, start_t=start, **kwargs))
    assert structure_view(context(bars={"M15": bars})).reason_codes[0] == code


def test_pivots_are_only_those_confirmed_by_the_bar_close() -> None:
    bars = random_walk_bars(80, seed=7, start_t=AS_OF - 80 * M15)
    full = confirmed_pivots(bars, st.PIVOT_STRENGTH, M15)
    for cut in range(10, 81, 7):
        as_of = bars[cut - 1].t + M15
        ctx = context(bar_open=as_of - M15, bars={"M15": bars[:cut]})
        assert st.m15_pivots(ctx) == tuple(p for p in full if p.confirmed_at <= as_of)


@pytest.mark.parametrize(("features", "near"), [
    ({"round_distance": 0.9, "atr_m15": 2.0}, True), ({"round_distance": 20.0}, False),
    ({"round_distance": -1.1, "atr_m15": 2.0}, False), ({}, True),
])
def test_near_round_number(features: dict[str, float], near: bool) -> None:
    assert ("NEAR_ROUND_NUMBER" in structure_view(context(features)).reason_codes) is near


@pytest.mark.parametrize(("highs", "lows", "expected"), [
    ((10, 10.1), (5, 3), ("DOUBLE_TOP",)), ((10, 12), (5, 5.2), ("DOUBLE_BOTTOM",)),
    ((10, 10), (5, 5), ("RECTANGLE",)), ((12, 10), (3, 5), ("TRIANGLE",)),
    ((10, 14, 10.2), (3, 2, 1), ("HEAD_SHOULDERS",)),
    ((1, 2, 3), (5, 1, 5.1), ("INV_HEAD_SHOULDERS",)), ((10,), (5,), ()),
])
def test_named_patterns_carry_no_weight(highs: tuple[float, ...], lows: tuple[float, ...],
                                        expected: tuple[str, ...]) -> None:
    def pivots_of(kind: str, prices: tuple[float, ...]) -> tuple[Pivot, ...]:
        return tuple(Pivot(kind, i, BAR_OPEN + i * M15, price, AS_OF)  # type: ignore[arg-type]
                     for i, price in enumerate(prices))

    pivots = pivots_of("high", highs) + pivots_of("low", lows)
    assert st.named_patterns(pivots, 2.0) == expected
    assert st.named_patterns(pivots, None) == () == st.named_patterns(pivots, 0.0)


@pytest.mark.parametrize(("value", "expected"), [
    (1, 1.0), (True, None), ("x", None), (float("nan"), None), (float("inf"), None)])
def test_finite_feature(value: object, expected: float | None) -> None:
    assert st.finite_feature({"k": value}, "k") == expected  # type: ignore[dict-item]
