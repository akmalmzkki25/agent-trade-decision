from app.scenarios.aggressive_bias import AggressiveBias

from .fixtures_v3 import make_v3_request


def test_flat_market_returns_none():
    req = make_v3_request(
        last_close=2400.00,
        ema20_m5=2400.00,
        ema50_m5=2400.00,
        ema50_m15=2400.00,
        di_balance=0.0,
        rsi_m1=0.0,
        last3_dir=0.0,
    )
    r = AggressiveBias().evaluate(req)
    assert r.side == "none"
    assert r.score < 0.40


def test_strong_buy_bias_fires():
    req = make_v3_request(
        last_close=2410.00,        # above all EMAs
        ema20_m5=2400.00,
        ema50_m5=2400.00,
        ema50_m15=2400.00,
        di_balance=0.30,           # DI bull
        rsi_m1=0.30,               # RSI bull
        last3_dir=1.0,             # 3 candles up
    )
    r = AggressiveBias().evaluate(req)
    assert r.side == "buy"
    assert r.score >= 0.65


def test_strong_sell_bias_fires():
    req = make_v3_request(
        last_close=2390.00,
        ema20_m5=2400.00,
        ema50_m5=2400.00,
        ema50_m15=2400.00,
        di_balance=-0.30,
        rsi_m1=-0.30,
        last3_dir=-1.0,
    )
    r = AggressiveBias().evaluate(req)
    assert r.side == "sell"
    assert r.score >= 0.65


def test_mild_bias_still_fires_above_v3_threshold():
    # Only price-vs-EMA20 votes; rest neutral. Total votes = 1.0 → score 0.55.
    req = make_v3_request(
        last_close=2402.00,
        ema20_m5=2400.00,
        ema50_m5=2402.00,    # equal price → no vote
        ema50_m15=2402.00,
        di_balance=0.0,
        rsi_m1=0.0,
        last3_dir=0.0,
    )
    r = AggressiveBias().evaluate(req)
    assert r.side == "buy"
    assert r.score >= 0.40


def test_invalidation_set_to_ema50_m15():
    req = make_v3_request(
        last_close=2410.00,
        ema20_m5=2400.00,
        ema50_m5=2400.00,
        ema50_m15=2395.00,
        di_balance=0.30,
    )
    r = AggressiveBias().evaluate(req)
    assert r.invalidation_price == 2395.00


def test_reason_codes_include_vote_count():
    req = make_v3_request(
        last_close=2410.00,
        ema20_m5=2400.00,
        ema50_m5=2400.00,
        ema50_m15=2400.00,
        di_balance=0.30,
        rsi_m1=0.30,
        last3_dir=1.0,
    )
    r = AggressiveBias().evaluate(req)
    assert "AGG_BIAS_BUY" in r.reason_codes
    assert any(code.startswith("VOTES_") for code in r.reason_codes)
