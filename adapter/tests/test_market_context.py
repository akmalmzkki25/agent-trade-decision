from app.news.market_context import apply_bias_to_score, get_bias_modifier


def test_non_xau_symbol_no_bias():
    r = get_bias_modifier("EURUSD", {"slope_z": 2.0}, {"z_score": 2.0})
    assert r.gold_bias == 0.0
    assert r.reason_codes == []


def test_dxy_bullish_gives_negative_gold_bias():
    r = get_bias_modifier("XAUUSD", {"slope_z": 1.5}, {})
    assert r.gold_bias < 0
    assert "DXY_BULLISH_USD" in r.reason_codes


def test_dxy_bearish_gives_positive_gold_bias():
    r = get_bias_modifier("XAUUSD", {"slope_z": -1.5}, {})
    assert r.gold_bias > 0
    assert "DXY_BEARISH_USD" in r.reason_codes


def test_vix_riskoff_adds_positive_bias():
    r = get_bias_modifier("XAUUSD", {"slope_z": 0.0}, {"z_score": 2.0})
    assert r.gold_bias > 0
    assert "VIX_RISK_OFF" in r.reason_codes


def test_bias_clamped():
    r = get_bias_modifier("XAUUSD", {"slope_z": -10}, {"z_score": 10})
    assert -1.0 <= r.gold_bias <= 1.0


def test_apply_bias_buy_with_positive_bias_raises_score():
    base = 0.50
    new = apply_bias_to_score(base, "buy", 1.0)
    assert new > base


def test_apply_bias_sell_with_positive_bias_lowers_score():
    base = 0.50
    new = apply_bias_to_score(base, "sell", 1.0)
    assert new < base


def test_apply_bias_clamped_0_1():
    assert apply_bias_to_score(0.95, "buy", 1.0) <= 1.0
    assert apply_bias_to_score(0.05, "sell", 1.0) >= 0.0
