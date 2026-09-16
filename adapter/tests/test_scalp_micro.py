from app.scenarios.scalp_micro import ScalpMicro

from .fixtures_v5 import make_v5_request


def test_buy_setup_triggers():
    req = make_v5_request(
        tick_momentum_signed=1.0, bb_pos=0.20, rsi_centered=0.10,
        tick_volume_z=1.0, spread_points=8.0,
    )
    r = ScalpMicro().evaluate(req)
    assert r.side == "buy"
    assert r.score >= 0.55


def test_sell_setup_triggers():
    req = make_v5_request(
        tick_momentum_signed=-1.0, bb_pos=-0.20, rsi_centered=-0.10,
        tick_volume_z=1.0, spread_points=8.0,
    )
    r = ScalpMicro().evaluate(req)
    assert r.side == "sell"


def test_spread_too_wide_vetoed():
    req = make_v5_request(spread_points=50.0)
    r = ScalpMicro().evaluate(req)
    assert r.side == "none"
    assert "SPREAD_TOO_WIDE" in r.reason_codes


def test_rsi_extreme_vetoed():
    req = make_v5_request(rsi_centered=0.50)
    r = ScalpMicro().evaluate(req)
    assert r.side == "none"
    assert "RSI_EXTREME_M1" in r.reason_codes


def test_no_tick_momentum_vetoed():
    req = make_v5_request(tick_momentum_signed=0.0)
    r = ScalpMicro().evaluate(req)
    assert r.side == "none"


def test_bb_extreme_blocks_entry():
    # Want to buy (mom up) but BB already at upper extreme (0.90) — block
    req = make_v5_request(tick_momentum_signed=1.0, bb_pos=0.90)
    r = ScalpMicro().evaluate(req)
    assert r.side == "none"
    assert "BB_EXTREME_BLOCK" in r.reason_codes


def test_high_atr_percentile_alone_does_not_block():
    """
    Percentile is recorded but must not gate: a smoothed series sits near its
    own extremes most of the time, so ranking it blocked 97% of live readings.
    """
    req = make_v5_request(atr_m1_percentile=0.99, atr_m1_ratio=1.1)
    r = ScalpMicro().evaluate(req)
    assert r.side == "buy"


def test_volume_spike_boosts_score():
    req_no = make_v5_request(tick_volume_z=0.1)
    req_yes = make_v5_request(tick_volume_z=2.0)
    s_no = ScalpMicro().evaluate(req_no).score
    s_yes = ScalpMicro().evaluate(req_yes).score
    assert s_yes > s_no


# --- Micro-volatility sweet spot (brief section 1.2) -----------------------


def test_atr_too_quiet_vetoed():
    """Ratio well under 1.0 = volatility collapsed vs its own baseline."""
    req = make_v5_request(atr_m1_ratio=0.30)
    r = ScalpMicro().evaluate(req)
    assert r.side == "none"
    assert "ATR_TOO_QUIET" in r.reason_codes


def test_atr_too_wild_vetoed():
    """News spike volatility — spread/slippage would eat the scalp edge."""
    req = make_v5_request(atr_m1_ratio=3.2)
    r = ScalpMicro().evaluate(req)
    assert r.side == "none"
    assert "ATR_TOO_WILD" in r.reason_codes


def test_atr_sweet_spot_allows_entry():
    req = make_v5_request(atr_m1_ratio=1.1)
    r = ScalpMicro().evaluate(req)
    assert r.side == "buy"
    assert "ATR_SWEET_SPOT" in r.reason_codes


# --- Volume Spread Analysis (brief section 1.1) ---------------------------


def test_vsa_climax_vetoes_entry():
    """Blow-off bar: ultra volume + wide range. Never chase it."""
    req = make_v5_request(vsa_volume_z=2.5, vsa_range_z=1.8)
    r = ScalpMicro().evaluate(req)
    assert r.side == "none"
    assert "VSA_CLIMAX_VETO" in r.reason_codes


def test_vsa_absorption_flagged():
    """High volume, narrow range = stopping volume."""
    req = make_v5_request(vsa_volume_z=1.8, vsa_range_z=-0.8)
    r = ScalpMicro().evaluate(req)
    assert r.side == "buy"
    assert "VSA_ABSORPTION" in r.reason_codes


def test_vsa_no_demand_flagged():
    """Low volume, wide range = move lacks participation."""
    req = make_v5_request(vsa_volume_z=-1.0, vsa_range_z=1.5)
    r = ScalpMicro().evaluate(req)
    assert "VSA_NO_DEMAND" in r.reason_codes


def test_vsa_effort_confirmed_boosts_score():
    req_neutral = make_v5_request(vsa_volume_z=0.0, vsa_range_z=0.0)
    req_effort = make_v5_request(vsa_volume_z=1.8, vsa_range_z=0.5)
    s_neutral = ScalpMicro().evaluate(req_neutral).score
    s_effort = ScalpMicro().evaluate(req_effort).score
    assert s_effort > s_neutral
    assert "VSA_EFFORT_CONFIRMED" in ScalpMicro().evaluate(req_effort).reason_codes


# --- Order book imbalance (brief section 1.1, when DOM available) ---------


def test_dom_imbalance_aligned_boosts_score():
    req_none = make_v5_request(dom_imbalance=0.0)
    req_aligned = make_v5_request(dom_imbalance=0.5)   # bid-heavy, we buy
    s_none = ScalpMicro().evaluate(req_none).score
    s_aligned = ScalpMicro().evaluate(req_aligned).score
    assert s_aligned > s_none
    assert "DOM_IMBALANCE_ALIGNED" in ScalpMicro().evaluate(req_aligned).reason_codes


def test_dom_imbalance_against_penalises_score():
    req_none = make_v5_request(dom_imbalance=0.0)
    req_against = make_v5_request(dom_imbalance=-0.5)   # ask-heavy, we buy
    s_none = ScalpMicro().evaluate(req_none).score
    s_against = ScalpMicro().evaluate(req_against).score
    assert s_against < s_none
    assert "DOM_IMBALANCE_AGAINST" in ScalpMicro().evaluate(req_against).reason_codes
