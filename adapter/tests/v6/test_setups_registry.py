"""
SetupInput validation, the shared setup helpers and the registry (order, dedupe, cap,
failure isolation, MarketContext entry point).
"""

from __future__ import annotations

import logging
import math
from dataclasses import FrozenInstanceError, replace

import pytest

from app.v6.cycle_codes import MAX_OFFERED_CANDIDATES, candidate_id_for
from app.v6.market.sessions import MAX_SUPPORTED_EPOCH
from app.v6.setups import (
    DETECTORS, MAX_CANDIDATES, SetupInput, as_setup_input, detect_all, detect_candidates,
)
from app.v6.setups import base
from app.v6.setups.base import Level
from app.v6.types import Candidate

from .cycle_fixtures_v6 import market_context
from .setup_fixtures_v6 import (
    DAY, HOUR, M15, NOON, TUE, WED, bar, day_bar, displacement_up, engulfing_input, flat,
    h1_swing_high, setup_input,
)

DOUBLE_SETUP_PREV = (4300.4, 4300.6, 4298.8, 4299.0)
DOUBLE_SETUP_TRIGGER = (4299.0, 4304.6, 4299.0, 4304.4)


# --- SetupInput -------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    {"as_of_epoch": NOON + 1}, {"as_of_epoch": True}, {"as_of_epoch": float(NOON)},
    {"as_of_epoch": M15}, {"as_of_epoch": (MAX_SUPPORTED_EPOCH // M15 + 1) * M15},
    {"as_of_epoch": NOON * 1000}, {"spread_price": math.nan}, {"spread_price": -0.1},
    {"spread_price": True}, {"point": 0.0}, {"point": math.inf}, {"digits": 9},
    {"digits": True}, {"digits": 2.0},
])
def test_setup_input_rejects_bad_scalars(kwargs: dict[str, object]) -> None:
    good = {"as_of_epoch": NOON, "bars": {}, "spread_price": 0.2, "point": 0.01, "digits": 2}
    with pytest.raises(ValueError):
        SetupInput(**{**good, **kwargs})  # type: ignore[arg-type]


def test_setup_input_rejects_unknown_timeframes_and_unordered_bars() -> None:
    rows = flat(WED, WED + 3 * M15, price=4300.0)
    with pytest.raises(ValueError, match="unknown timeframe"):
        setup_input(NOON, M30=rows)
    with pytest.raises(ValueError, match="oldest first"):
        setup_input(NOON, M15=rows[::-1])
    with pytest.raises(ValueError, match="oldest first"):
        setup_input(NOON, M15=rows + rows[-1:])


def test_setup_input_keeps_only_closed_bars_and_is_immutable() -> None:
    m15 = flat(NOON - M15, NOON + 2 * M15, price=4300.0)
    h1 = flat(NOON - HOUR, NOON + HOUR, price=4300.0, step=HOUR)
    d1 = (day_bar(TUE, 4310.0, 4290.0), day_bar(WED, 4310.0, 4290.0))
    inp = setup_input(NOON + M15, M15=m15, H1=h1, D1=d1)
    assert [b.t for b in inp.series("M15")] == [NOON - M15, NOON]
    assert [b.t for b in inp.series("H1")] == [NOON - HOUR]
    assert [b.t for b in inp.series("D1")] == [TUE]
    assert inp.series("M5") == ()
    assert inp.trigger == m15[1] and inp.previous == m15[0]
    assert inp.known_before_trigger("M15") == m15[:1]
    with pytest.raises(FrozenInstanceError):
        inp.spread_price = 1.0  # type: ignore[misc]
    with pytest.raises(TypeError):
        inp.bars["M15"] = ()  # type: ignore[index]


def test_setup_input_trigger_and_buffers() -> None:
    inp = setup_input(NOON + M15, spread=0.0, M15=flat(NOON - M15, NOON, price=4300.0))
    assert inp.trigger is None and inp.previous is None
    assert inp.close_buffer == pytest.approx(0.03)       # never below 3 points
    assert base.stop_buffer(inp, None) == pytest.approx(0.03)
    assert base.stop_buffer(inp, 2.0) == pytest.approx(0.3)


def test_as_setup_input_accepts_context_and_rejects_other_types() -> None:
    inp = displacement_up()
    assert as_setup_input(inp) is inp
    with pytest.raises(TypeError):
        as_setup_input({"bars": {}})  # type: ignore[arg-type]


# --- shared helpers ---------------------------------------------------------------------

def test_since_and_between_windows() -> None:
    rows = flat(WED, WED + 4 * M15, price=4300.0)
    assert base.since(rows, WED + M15) == rows[1:]
    assert base.between(rows, WED + M15, WED + 3 * M15) == rows[1:3]
    assert base.between(rows, WED + 9 * M15, WED + 10 * M15) == ()


def test_crossed_levels_and_pick_level() -> None:
    pdh, pivot = Level("pdh", 4310.0, WED), Level("h1_high", 4309.0, WED)
    assert base.crossed_levels((pdh, pivot), "buy", 4310.0, 4310.6, 0.6) == (pdh,)
    assert base.crossed_levels((pdh, pivot), "buy", 4308.0, 4310.5, 0.6) == (pivot,)
    assert base.crossed_levels((pdh, pivot), "sell", 4311.0, 4309.0, 0.6) == (pdh,)
    assert base.pick_level((pivot, pdh), 4309.0) == pdh
    near, far = Level("h1_low", 4301.0, WED), Level("h1_high", 4299.0, WED)
    assert base.pick_level((far, near), 4300.5) == near
    assert base.pick_level((Level("h1_low", 4301.0, WED), far), 4300.0) == far
    with pytest.raises(ValueError):
        base.pick_level((), 4300.0)
    assert base.level_codes(pdh, (pdh,)) == ("LEVEL_PDH",)
    assert base.level_codes(pdh, (pdh, pivot)) == ("LEVEL_PDH", "LEVEL_CONFLUENCE")


@pytest.mark.parametrize(("structure", "side", "codes"), [
    ("up", "buy", ("HTF_ALIGNED",)), ("down", "sell", ("HTF_ALIGNED",)),
    ("down", "buy", ("HTF_OPPOSED",)), ("up", "sell", ("HTF_OPPOSED",)),
    ("range", "buy", ()), ("unknown", "sell", ()),
])
def test_bias_codes(structure: str, side: str, codes: tuple[str, ...]) -> None:
    assert base.bias_codes(structure, side) == codes  # type: ignore[arg-type]


def test_reference_levels_respect_lookback_and_knowledge_time() -> None:
    recent = h1_swing_high(WED, 4320.0)
    old = h1_swing_high(WED - 3 * DAY, 4330.0)
    inp = setup_input(NOON + M15, H1=old + recent, D1=(day_bar(TUE, 4310.0, 4290.0),))
    levels = base.reference_levels(inp)
    assert [(lv.kind, lv.price) for lv in levels] == [
        ("pdh", 4310.0), ("pdl", 4290.0), ("h1_high", 4320.0)]
    assert all(lv.known_at <= inp.bar_open_epoch for lv in levels)
    assert levels[2].known_at == WED + 5 * HOUR and levels[2].code == "LEVEL_H1_PIVOT_HIGH"


def test_volatility_units() -> None:
    assert base.vol_unit_m15(displacement_up()) == (1.0, "VOL_ATR_FALLBACK")
    assert base.vol_unit_m15(displacement_up(history_start=WED - 6 * DAY)) == (1.0, "VOL_SLOT_TR")
    dead = setup_input(NOON + M15, M15=flat(WED, NOON + M15, price=4300.0, half=0.0))
    assert base.vol_unit_m15(dead) is None
    assert base.atr_m5(dead) is None and base.atr_d1(dead) is None


def test_build_candidate_rounds_and_refuses_bad_geometry() -> None:
    inp = displacement_up()
    kwargs = {"setup": "displacement", "side": "buy", "features": {}, "reason_codes": ()}
    assert base.build_candidate(inp, entry=4300.001, invalidation=4299.996, **kwargs) is None
    assert base.build_candidate(inp, entry=4300.0, invalidation=4301.0, **kwargs) is None
    assert base.build_candidate(inp, entry=math.inf, invalidation=4290.0, **kwargs) is None
    cand = base.build_candidate(
        inp, entry=4300.004, invalidation=4290.0, setup="orb", side="buy", variant="lon",
        features={"a": 1.23456789, "b": None, "c": math.nan},
        reason_codes=("X", "Y", "X"))
    assert cand is not None
    assert cand.candidate_id == candidate_id_for("orb", "buy", NOON, "lon")
    assert (cand.entry, cand.invalidation) == (4300.0, 4290.0)
    assert dict(cand.features) == {"a": 1.234568} and cand.reason_codes == ("X", "Y")
    with pytest.raises(TypeError):
        cand.features["a"] = 2.0  # type: ignore[index]


def test_side_of() -> None:
    assert base.side_of(bar(NOON, 1.0, 2.0, 0.5, 1.5)) == "buy"
    assert base.side_of(bar(NOON, 1.5, 2.0, 0.5, 1.0)) == "sell"
    assert base.side_of(bar(NOON, 1.0, 2.0, 0.5, 1.0)) is None


# --- registry ---------------------------------------------------------------------------

def _fake(setup: str, side: str = "buy", entry: float = 4300.0, stop: float = 4290.0,
          variant: str = "") -> Candidate:
    candidate_id = candidate_id_for(setup, side, NOON, variant)  # type: ignore[arg-type]
    return Candidate(candidate_id=candidate_id, setup=setup, side=side,  # type: ignore[arg-type]
                     entry=entry, invalidation=stop, bar_t=NOON)


def test_registry_lists_every_detector_in_evidence_order() -> None:
    assert [name for name, _ in DETECTORS] == ["displacement", "orb", "retest", "engulfing"]
    assert MAX_CANDIDATES == MAX_OFFERED_CANDIDATES == 3


def test_detect_all_orders_by_priority_and_keeps_distinct_trades() -> None:
    inp = engulfing_input(DOUBLE_SETUP_PREV, DOUBLE_SETUP_TRIGGER, price=4301.0, half=1.0)
    found = detect_all(inp)
    assert [c.setup for c in found] == ["displacement", "engulfing"]
    assert found[0].entry == found[1].entry and found[0].invalidation != found[1].invalidation


def test_detect_all_dedupes_ids_and_identical_trades() -> None:
    detectors = (
        ("engulfing", lambda inp: (_fake("engulfing"),)),
        ("orb", lambda inp: (_fake("orb", variant="lon"), _fake("orb", variant="lon"),
                             _fake("orb", entry=4301.0, variant="ny"),
                             _fake("orb", entry=4302.0, variant="ny"))),     # same id, new trade
        ("displacement", lambda inp: (_fake("displacement"),)),
    )
    found = detect_all(displacement_up(), detectors)  # type: ignore[arg-type]
    assert [c.candidate_id for c in found] == [
        candidate_id_for("displacement", "buy", NOON),
        candidate_id_for("orb", "buy", NOON, "ny"),
    ]


def test_detect_candidates_caps_the_offer() -> None:
    many = tuple(_fake("orb", entry=4300.0 + i, variant=f"v{i}") for i in range(5))
    detectors = (("orb", lambda inp: many), ("retest", lambda inp: (_fake("retest"),)))
    capped = detect_candidates(displacement_up(), detectors=detectors)  # type: ignore[arg-type]
    assert len(capped) == MAX_CANDIDATES
    assert [c.candidate_id for c in capped] == [c.candidate_id for c in many[:3]]
    assert len(detect_candidates(displacement_up(), 1, detectors)) == 1  # type: ignore[arg-type]
    for bad in (0, MAX_CANDIDATES + 1, True, 1.5):
        with pytest.raises(ValueError):
            detect_candidates(displacement_up(), bad)  # type: ignore[arg-type]


def test_failing_detector_is_logged_and_skipped(caplog: pytest.LogCaptureFixture) -> None:
    def broken(inp: SetupInput) -> tuple[Candidate, ...]:
        raise ZeroDivisionError("boom")

    detectors = (("orb", broken), *DETECTORS[:1])
    with caplog.at_level(logging.WARNING, logger="app.v6.setups.registry"):
        found = detect_all(displacement_up(), detectors)  # type: ignore[arg-type]
    assert [c.setup for c in found] == ["displacement"]
    assert "orb" in caplog.text and "ZeroDivisionError" in caplog.text


def test_unexpected_detector_errors_propagate() -> None:
    def buggy(inp: SetupInput) -> tuple[Candidate, ...]:
        raise KeyError("bug")

    with pytest.raises(KeyError):
        detect_all(displacement_up(), (("orb", buggy),))  # type: ignore[arg-type]


def test_detectors_accept_a_market_context() -> None:
    reference = displacement_up()
    ctx = market_context(bars=dict(reference.bars))
    from_ctx = SetupInput.from_context(ctx)
    assert from_ctx.as_of_epoch == reference.as_of_epoch == NOON + M15
    assert from_ctx.point == 0.01 and from_ctx.digits == 2
    assert from_ctx.spread_price == pytest.approx(0.2)
    assert detect_all(ctx) == detect_all(replace(reference, spread_price=ctx.spread_price))
    assert [c.setup for c in detect_candidates(ctx)] == ["displacement"]
