"""The MarketContext feature map: exact values on flat bars, gaps and no look-ahead."""

from __future__ import annotations

import json
import math
from dataclasses import replace

import pytest

from app.v6.cycle_codes import FEATURE_KEYS
from app.v6.market import feature_map as fm
from app.v6.market.feature_map import FeatureInputs, adx_wilder, build_feature_map
from app.v6.schemas.snapshot import ProbeBlock, TickStatsBlock
from app.v6.types import Bar

from . import engine_fixtures_v6 as ef
from .fixtures_v6 import random_walk_bars, trend_bars

TICKS = TickStatsBlock.model_validate_json(json.dumps({
    "window_s": 900, "quote_count": 900, "max_gap_ms": 800, "spread_p50_points": 20.0,
    "spread_p95_points": 25.0, "mid_rv": 0.1}))
PROBE = ProbeBlock.model_validate_json(json.dumps({
    "book_depth": 10, "trade_ticks_count": 0, "real_volume_count": 0, "dom_synthetic": True,
    "gmt_offset_s": 10800, "dst_active": True, "calendar_events_seen": 4}))


def inputs(bars: dict[str, tuple[Bar, ...]] | None = None, **changes: object) -> FeatureInputs:
    base = FeatureInputs(
        as_of_epoch=ef.AS_OF, bars=ef.history() if bars is None else bars, point=0.01,
        spread_points=20, spread_price=0.2, friction_price=0.4, mid=4300.1, ticks=TICKS)
    return replace(base, **changes)  # type: ignore[arg-type]


def test_flat_bars_give_exact_features() -> None:
    features = build_feature_map(inputs())
    assert set(features) <= FEATURE_KEYS
    assert features["atr_m5"] == pytest.approx(8.0)
    assert features["atr_m5_points"] == pytest.approx(800.0)
    assert features["atr_m15"] == pytest.approx(10.0)
    assert features["atr_h1"] == pytest.approx(12.0)
    assert features["slot_tr_m15"] == pytest.approx(10.0)
    assert features["atr_ratio_m15_slot"] == pytest.approx(1.0)
    assert features["rv_ratio"] == pytest.approx(1.0)
    assert features["friction_price"] == 0.4
    assert features["friction_atr_m5"] == pytest.approx(0.05)
    assert features["spread_points"] == 20.0
    assert features["spread_pctl_hour"] == 0.5
    assert features["max_quote_gap_ms"] == 800.0
    assert features["er_m15"] == 0.0
    assert features["adx_h1"] == 0.0
    assert features["round_distance"] == pytest.approx(0.1)
    # Constant series have no variance ratio, autocorrelation, activity z or swing.
    for missing in ("vr_m5", "ac1_m5", "tick_volume_z", "structure_m15", "dom_synthetic"):
        assert missing not in features


def test_without_bars_only_snapshot_features_remain() -> None:
    features = build_feature_map(inputs({}, ticks=TICKS.model_copy(update={"window_s": 0})))
    assert set(features) == {"friction_price", "spread_points", "round_distance"}


def test_a_live_spread_above_the_constant_is_the_friction() -> None:
    features = build_feature_map(inputs(spread_price=0.55))
    assert features["friction_price"] == 0.55
    assert features["friction_atr_m5"] == pytest.approx(0.55 / 8.0)


def test_probe_reports_the_dom() -> None:
    assert build_feature_map(inputs(probe=PROBE))["dom_synthetic"] == 1.0
    real = PROBE.model_copy(update={"dom_synthetic": False})
    assert build_feature_map(inputs(probe=real))["dom_synthetic"] == 0.0


def test_moving_bars_give_structure_efficiency_and_activity() -> None:
    history = ef.history()
    m15 = trend_bars(200, tf="M15", start_t=ef.AS_OF - 200 * ef.M15)
    m5 = random_walk_bars(300, seed=7, tf="M5", start_t=ef.AS_OF - 300 * ef.M5)
    features = build_feature_map(inputs({**history, "M15": m15, "M5": m5}))
    assert features["structure_m15"] == 1.0
    assert 0.0 < features["er_m15"] <= 1.0
    assert "vr_m5" in features and -1.0 <= features["ac1_m5"] <= 1.0
    assert all(math.isfinite(value) for value in features.values())


def test_a_trigger_bar_missing_drops_trigger_features() -> None:
    history = ef.history()
    trimmed = {**history, "M15": history["M15"][:-1]}
    features = build_feature_map(inputs(trimmed))
    assert "rv_ratio" not in features and "tick_volume_z" not in features


def test_spread_percentile_needs_enough_same_hour_samples() -> None:
    history = ef.history()
    short = {**history, "M5": history["M5"][-11:]}
    assert "spread_pctl_hour" not in build_feature_map(inputs(short))


def test_adx_on_a_steady_trend_is_strong() -> None:
    bars = trend_bars(80, tf="H1", start_t=ef.AS_OF - 80 * ef.H1, leg=8, pullback=1)
    assert adx_wilder(bars) > 25.0


def test_adx_needs_history_and_range() -> None:
    assert adx_wilder(trend_bars(28, tf="H1")) is None
    flat = tuple(Bar(t=i * ef.H1, o=1.0, h=1.0, l=1.0, c=1.0) for i in range(40))
    assert adx_wilder(flat) is None
    with pytest.raises(ValueError):
        adx_wilder(flat, period=0)


def test_helpers_are_defensive() -> None:
    assert fm._ratio(1.0, 0.0) is None
    assert fm._ratio(None, 1.0) is None
    assert fm._finite(math.inf) is None
    assert fm._dx(1.0, 1.0, 0.0) is None
    assert fm._dx(0.0, 0.0, 1.0) == 0.0
    assert fm.effective_friction(0.4, 0.3) == 0.4
