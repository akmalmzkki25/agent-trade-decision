from app.scenarios.liquidity_zone import LiquidityZoneEntry

from .fixtures_v4 import make_v4_request


def test_buy_zone_detected_when_price_above_h1_swing_low():
    # last=4170, swing_low_h1=4155 → zone_top=4156, zone_bottom=4126
    # Wait, ZONE_WIDTH=30 pip, pip_size=0.10 so zone_w=3.0
    # top = 4155 + 1.0 = 4156, bottom = 4156 - 3.0 = 4153
    # last (4170) - top (4156) = 14 = 140 pip → within 5-200 pip range ✓
    req = make_v4_request(
        last_close=4170.00,
        swing_low_h1=4155.00,
        close_m15=4170.00,
        m5_direction=-1.0,  # pullback
    )
    r = LiquidityZoneEntry().evaluate(req)
    assert r.side == "buy"
    assert r.score >= 0.55
    assert r.key_levels["zone_top"] == 4156.0
    assert r.key_levels["zone_bottom"] == 4153.0
    # SL = zone_bottom (4153) - 2.0 (20 pip) = 4151
    assert r.key_levels["sl_price"] == 4151.0


def test_sell_zone_detected_when_price_below_h1_swing_high():
    # swing_high_h1=4200, last=4170
    # zone_bottom = 4200 - 1.0 = 4199, zone_top = 4199 + 3.0 = 4202
    # last (4170) is below zone_bottom (4199) by 29 pts = 290 pip → out of range!
    # Need to bring last closer.
    req = make_v4_request(
        last_close=4196.00,
        swing_high_h1=4200.00,
        close_m15=4196.00,
        m5_direction=1.0,  # bullish small rally back up to zone
    )
    r = LiquidityZoneEntry().evaluate(req)
    assert r.side == "sell"
    assert r.score >= 0.55


def test_no_zone_when_price_already_inside():
    # If price is too close to zone (< MIN_DISTANCE_TO_ZONE_PIPS = 5 pip), reject.
    req = make_v4_request(
        last_close=4156.20,   # ~2 pip above buy zone top
        swing_low_h1=4155.00,
        close_m15=4156.20,
    )
    r = LiquidityZoneEntry().evaluate(req)
    assert r.side == "none"


def test_no_zone_when_price_too_far():
    # > MAX_DISTANCE_TO_ZONE_PIPS = 200 pip from BOTH swing zones → reject.
    req = make_v4_request(
        last_close=4180.00,
        swing_low_h1=4100.00,    # buy zone top=4101, dist 79 pts = 790 pip ✗
        swing_high_h1=4260.00,   # sell zone bottom=4259, dist 79 pts ✗
        close_m15=4180.00,
    )
    r = LiquidityZoneEntry().evaluate(req)
    assert r.side == "none"


def test_m5_pullback_bonus_applied():
    # Bullish M5 candle (wrong direction for BUY pullback) → no bonus
    req_no = make_v4_request(
        last_close=4170.00, swing_low_h1=4155.00, close_m15=4170.00,
        m5_direction=1.0,    # bullish — NOT a pullback for BUY scenario
        m5_body_atr=0.5,
    )
    # Bearish M5 candle (proper pullback into BUY zone) → bonus
    req_yes = make_v4_request(
        last_close=4170.00, swing_low_h1=4155.00, close_m15=4170.00,
        m5_direction=-1.0,
        m5_body_atr=0.5,
    )
    r_no = LiquidityZoneEntry().evaluate(req_no)
    r_yes = LiquidityZoneEntry().evaluate(req_yes)
    assert r_yes.score > r_no.score


def test_missing_features_returns_none():
    req = make_v4_request(
        last_close=4170.00,
        swing_low_h1=0.0,    # missing
        swing_high_h1=0.0,
    )
    r = LiquidityZoneEntry().evaluate(req)
    assert r.side == "none"
    assert "FEATURES_MISSING" in r.reason_codes
