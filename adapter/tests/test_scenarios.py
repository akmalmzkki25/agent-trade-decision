from app.scenarios import select_best
from app.scenarios.continuation_pullback import ContinuationPullback
from app.scenarios.range_revert import RangeRevert
from app.scenarios.trend_breakout_stop import TrendBreakoutStop

from .fixtures_v2 import make_plan_request


def test_range_revert_buy_setup():
    # Default fixture: low ADX, BB lower, RSI oversold M5 → buy.
    req = make_plan_request()
    r = RangeRevert().evaluate(req)
    assert r.side == "buy"
    assert r.score >= 0.55
    assert "BB_LOWER_M5" in r.reason_codes


def test_range_revert_sell_setup():
    req = make_plan_request(bb_pos_m5=0.92, rsi_m5=0.50, bb_pos_m1=0.70, rsi_m1=0.30)
    r = RangeRevert().evaluate(req)
    assert r.side == "sell"
    assert r.score >= 0.55


def test_range_revert_no_setup_when_trending():
    req = make_plan_request(adx_h1=0.40, bb_pos_m5=0.0, rsi_m5=0.0)
    r = RangeRevert().evaluate(req)
    assert r.side == "none"


def test_trend_breakout_buy_setup():
    req = make_plan_request(
        adx_h1=0.30,
        di_balance=0.35,
        bb_width_pct=0.20,
        last_close=2401.90,  # close to breakout_high_m15=2402
        bb_pos_m5=0.0,
        rsi_m5=0.0,
        breakout_high_m15=2402.00,
        breakout_low_m15=2395.00,
    )
    r = TrendBreakoutStop().evaluate(req)
    assert r.side == "buy"
    assert r.score >= 0.55


def test_trend_breakout_sell_setup():
    req = make_plan_request(
        adx_h1=0.30,
        di_balance=-0.35,
        bb_width_pct=0.20,
        last_close=2395.10,
        bb_pos_m5=0.0,
        rsi_m5=0.0,
        breakout_high_m15=2402.00,
        breakout_low_m15=2395.00,
    )
    r = TrendBreakoutStop().evaluate(req)
    assert r.side == "sell"


def test_continuation_pullback_buy_setup():
    req = make_plan_request(
        adx_h1=0.28,
        adx_m15=0.26,
        di_balance=0.20,
        last_close=2400.15,  # above ema50_m15=2399.50, close to ema20_m5=2400.10
        bb_pos_m5=0.0,
        rsi_m5=-0.10,
        ema50_m15=2399.50,
        ema20_m5=2400.10,
        ema50_m5=2399.80,
    )
    r = ContinuationPullback().evaluate(req)
    assert r.side == "buy"
    assert r.score >= 0.55


def test_select_best_picks_highest_score():
    # Default fixture leans range_revert; verify selector picks it.
    req = make_plan_request()
    winner = select_best(req)
    assert winner is not None
    assert winner.name == "RANGE_REVERT"


def test_select_best_returns_none_when_flat():
    req = make_plan_request(
        adx_h1=0.10,
        bb_pos_m5=0.0,
        rsi_m5=0.0,
        bb_pos_m1=0.0,
        rsi_m1=0.0,
        di_balance=0.0,
        adx_m15=0.10,
        bb_width_pct=0.80,
    )
    winner = select_best(req)
    assert winner is None
