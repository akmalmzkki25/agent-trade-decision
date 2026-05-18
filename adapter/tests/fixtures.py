from __future__ import annotations

from app.models import (
    AccountSnapshot,
    DecisionRequest,
    FeatureBundle,
    MarketSnapshot,
    OpenClawContext,
    PositionSnapshot,
    RiskState,
)


def make_request(
    *,
    halted: bool = False,
    spread_points: float = 8.0,
    adx_strength: float = 0.35,
    di_balance: float = 0.30,
    price_vs_ma: float = 0.25,
    brk_up: float = 1.0,
    brk_dn: float = 0.0,
    atr_abs: float = 2.50,
    spread_to_atr: float = 0.05,
    request_id: str = "TEST-XAUUSD-M15-2026-05-18T09:00:00Z",
) -> DecisionRequest:
    return DecisionRequest(
        schema_version="trade-decision-request.v1",
        request_id=request_id,
        mode="paper",
        timestamp_utc="2026-05-18T09:00:00+00:00",
        symbol="XAUUSD",
        timeframe="M15",
        bar_index=1,
        market=MarketSnapshot(
            bid=2400.10,
            ask=2400.40,
            last_close=2400.20,
            spread_points=spread_points,
            digits=2,
            stops_level_points=50,
            freeze_level_points=10,
            tick_size=0.01,
            tick_value=1.0,
        ),
        account=AccountSnapshot(
            login="demo-1",
            balance=10000.0,
            equity=10000.0,
            free_margin=9500.0,
            margin_level=900.0,
            currency="USD",
            leverage=200,
        ),
        position=PositionSnapshot(
            net_position=0.0,
            avg_price=0.0,
            floating_pnl=0.0,
            open_positions_count=0,
            pending_orders_count=0,
            side="flat",
        ),
        risk_state=RiskState(
            max_risk_per_trade_pct=0.5,
            max_symbol_exposure_lots=0.30,
            daily_drawdown_pct=0.0,
            consecutive_losses=0,
            cooldown_until_utc=None,
            trading_halted=halted,
            blackout_reason=None,
        ),
        features=FeatureBundle(
            context_tf={
                "adx_strength": adx_strength,
                "di_balance": di_balance,
                "ma_gap_atr": 0.40,
                "atr_pct": 0.0040,
            },
            decision_tf={
                "rsi_centered": 0.18,
                "bb_pos": 0.30,
                "price_vs_ma_fast_atr": price_vs_ma,
                "breakout_up_flag": brk_up,
                "breakout_dn_flag": brk_dn,
            },
            execution_tf={
                "spread_to_atr_ratio": spread_to_atr,
                "last_bar_body_atr": 0.45,
                "atr_abs": atr_abs,
            },
        ),
        openclaw_context=OpenClawContext(mode="normal"),
    )
