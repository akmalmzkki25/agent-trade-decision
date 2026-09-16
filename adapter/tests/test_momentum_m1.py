from app.scenarios.momentum_m1 import MomentumM1

from .fixtures_v3 import make_v3_request


def test_no_trigger_when_atr_low():
    req = make_v3_request(m1_atr_pct=0.50, last3_dir=1.0)
    r = MomentumM1().evaluate(req)
    assert r.score == 0.0 and r.side == "none"


def test_no_trigger_when_no_3candle_run():
    req = make_v3_request(m1_atr_pct=0.80, last3_dir=0.0)
    r = MomentumM1().evaluate(req)
    assert r.side == "none"


def test_no_trigger_when_rsi_extreme():
    req = make_v3_request(m1_atr_pct=0.85, last3_dir=1.0, rsi_m1=0.45)
    r = MomentumM1().evaluate(req)
    assert r.side == "none"


def test_buy_trigger_when_aligned():
    req = make_v3_request(
        m1_atr_pct=0.85,
        last3_dir=1.0,
        rsi_m1=0.10,
        last_close=2400.50,   # above ema20_m5=2400.10
        ema20_m5=2400.10,
        ema50_m5=2399.80,
        adx_m15=0.25,
        di_balance=0.25,
    )
    r = MomentumM1().evaluate(req)
    assert r.side == "buy"
    assert r.score >= 0.40


def test_sell_trigger_when_aligned():
    req = make_v3_request(
        m1_atr_pct=0.85,
        last3_dir=-1.0,
        rsi_m1=-0.10,
        last_close=2399.00,   # below ema20_m5
        ema20_m5=2400.10,
        ema50_m5=2400.50,
        adx_m15=0.25,
        di_balance=-0.25,
    )
    r = MomentumM1().evaluate(req)
    assert r.side == "sell"
    assert r.score >= 0.40
