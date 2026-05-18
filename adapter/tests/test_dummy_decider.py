from app.deciders.dummy_trend_breakout import DummyTrendBreakoutDecider

from .fixtures import make_request


def test_strong_trend_breakout_opens_buy():
    d = DummyTrendBreakoutDecider()
    resp = d.decide(make_request())
    assert resp.status == "ok"
    assert resp.decision.action == "open"
    assert resp.decision.side == "buy"
    assert resp.decision.lots > 0
    assert resp.decision.sl is not None and resp.decision.tp is not None
    assert resp.decision.sl < resp.decision.tp


def test_strong_downside_opens_sell():
    d = DummyTrendBreakoutDecider()
    req = make_request(di_balance=-0.30, price_vs_ma=-0.25, brk_up=0.0, brk_dn=1.0)
    resp = d.decide(req)
    assert resp.decision.action == "open"
    assert resp.decision.side == "sell"
    assert resp.decision.sl > resp.decision.tp


def test_weak_adx_holds():
    d = DummyTrendBreakoutDecider()
    resp = d.decide(make_request(adx_strength=0.10))
    assert resp.decision.action == "hold"
    assert "ADX_WEAK" in resp.decision.reason_codes


def test_no_breakout_holds():
    d = DummyTrendBreakoutDecider()
    resp = d.decide(make_request(brk_up=0.0, brk_dn=0.0))
    assert resp.decision.action == "hold"
    assert "NO_BREAKOUT" in resp.decision.reason_codes


def test_wide_spread_holds():
    d = DummyTrendBreakoutDecider()
    resp = d.decide(make_request(spread_to_atr=0.50))
    assert resp.decision.action == "hold"
    assert "SPREAD_GUARD" in resp.decision.reason_codes


def test_lots_capped_by_exposure():
    d = DummyTrendBreakoutDecider()
    req = make_request()
    # very wide ATR -> tiny lots; verify still respects min
    resp = d.decide(req)
    assert resp.decision.lots <= req.risk_state.max_symbol_exposure_lots
