# V3 Aggressive Layering — Design

## Context

V1 (M15 single-decision) dan V2 (M1, 3-scenario, single-basket 5 layers) sudah jalan stabil. User minta V3 yang lebih agresif: tetap M1, tetap analisis TA + fundamental, tapi (a) sizing & frequency lebih tinggi, (b) tipe order lebih kaya (1 market + 2 limit + 2 stop per plan), (c) multi-basket parallel boleh dengan beda sisi, (d) fundamental layer aktif (DXY/VIX proxy + calendar stub).

V3 hidup paralel dengan V1+V2, tidak menggantikan apapun. Magic terpisah, endpoint baru, EA terpisah.

## Goals

- Endpoint `POST /v3/plan` mengembalikan `V3Plan` dengan 5-layer mixed-order ladder.
- EA `QlipV3_XAUUSD.mq5` evaluasi per M1 close, pasang ladder, kelola hingga 2 basket parallel beda sisi.
- 4 scenario detector aktif (RANGE_REVERT, TREND_BREAKOUT, CONTINUATION_PULLBACK, MOMENTUM_M1).
- Threshold scenario lebih rendah (0.40 vs V2 0.55) — HOLD hanya saat market benar-benar flat.
- Fundamental bias modifier dari DXY/VIX (max ±0.10 score impact).
- Risk 1% per basket, RR 1.2, max 2 basket parallel = 2% worst-case exposure.
- Dashboard render plans V3 dengan badge slot A/B.

## Non-Goals

- Mengubah V1/V2.
- Ganti dummy ke Claude (`deciders/claude.py` tetap stub).
- News fetcher real (calendar tetap stub).
- TradingView ideas scraper.
- Replay tape backtest.

## Arsitektur

```
adapter/app/
├── models.py                            # +V3PlanRequest/Response, V3Plan, V3Layer, ActiveBasket
├── main.py                              # +POST /v3/plan, +dashboard render V3
├── scenarios/
│   ├── momentum_m1.py                   # NEW
│   └── (range/breakout/pullback dari V2 reused)
├── layering/
│   ├── planner_v3.py                    # NEW: mixed ladder, weighted sizing
│   └── sizing.py                        # reused
├── news/
│   ├── economic_calendar.py             # existing stub
│   └── market_context.py                # NEW: DXY/VIX bias modifier
├── ledger.py                            # reuse plans + plan_layers (add column 'version')
├── dashboard_db.py                      # extend: filter by version
└── templates/dashboard.html             # +slot badge

ea/QlipV3_XAUUSD.mq5                     # NEW. Magic 250530..250544.
```

Magic allocation:
- V3 Slot A: `250530..250534` (5 layer)
- V3 Slot B: `250540..250544` (5 layer)

## Components

### 1. MOMENTUM_M1 Scenario (`scenarios/momentum_m1.py`)

Triggers:
- `m1_atr_pct >= 0.70` (ATR M1 di percentile ≥70 — volatility spike).
- 3 candle M1 berturut-turut searah (close vs open).
- |RSI_M1 centered| < 0.40 (tidak extreme — masih ada ruang).

Side:
- Buy jika 3 last close > open AND price > EMA20_M5.
- Sell mirror.

Score:
- Base 0.40 saat trigger.
- +0.20 bila ADX_M15 ≥ 0.20.
- +0.15 bila DI balance sealign side.
- +0.15 bila spread_to_atr_m1 < 0.10 (likuiditas OK).
- Max 1.0.

Invalidation: EMA50 M5 di sisi lawan.

### 2. Market Context (`news/market_context.py`)

```python
def get_bias_modifier(symbol: str, dxy_features: dict, vix_features: dict) -> dict:
    """
    Returns:
      dxy_slope: float (z-score 60-bar M5)
      vix_zscore: float
      gold_bias: float (-1..+1)
      reason_codes: list[str]
    """
```

Aturan (untuk XAUUSD):
- `dxy_slope > +1.0` → gold_bias −0.5, code `DXY_BULLISH_USD`
- `dxy_slope < −1.0` → gold_bias +0.5, code `DXY_BEARISH_USD`
- `vix_zscore > +1.5` → gold_bias +0.3, code `VIX_RISK_OFF`
- Default → gold_bias 0.0, no codes.

Modifier diterapkan di ranker:
```python
aligned = (+gold_bias) if scenario.side == "buy" else (-gold_bias)
final_score = base_score + 0.10 * max(-1, min(1, aligned))
```

Bila symbol bukan XAUUSD-class atau DXY tidak tersedia: skip (`gold_bias=0`).

### 3. Planner V3 (`layering/planner_v3.py`)

Input: winning scenario + V3PlanRequest + assigned slot.

Output: `V3Plan` dengan 5 LayerEntry mixed-type.

Layer recipe (untuk buy plan; sell mirror):

| # | Type | Price offset (× ATR_M1) | Risk weight |
|---|---|---|---|
| L1 | `buy_market` | 0 (anchor at ask) | 0.30 |
| L2 | `buy_limit` | −0.4 | 0.20 |
| L3 | `buy_limit` | −0.8 | 0.15 |
| L4 | `buy_stop` | +0.4 | 0.20 |
| L5 | `buy_stop` | +0.8 | 0.15 |

Weights total = 1.00.

Sizing per layer:
- `risk_per_layer = equity * total_risk_pct * weight`
- `sl_distance = max(1.0 × ATR_M5, dist_to_invalidation_for_this_layer)`
- `lots = risk_per_layer / (sl_dist_ticks × tick_value)`
- **Hard cap**: `lots = min(lots, max_symbol_exposure_lots × weight)` (anti tick_value bug)
- Round down to `volume_step`.

SL per layer:
- BUY: `sl = price − sl_distance`
- SELL: mirror.

TP per layer = `None` (basket TP di EA).

Magic per layer:
- Slot A: `250530 + (i+1)` for i=0..4
- Slot B: `250540 + (i+1)` for i=0..4

Plan fields:
- `basket_slot`: "A" or "B" (assigned by adapter)
- `basket_tp_pct_equity`: 1.20 (RR 1.2 × total_risk_pct)
- `max_lifetime_seconds`: 600
- `scenario_invalidation_price`: from scenario

### 4. Endpoint `/v3/plan` Concurrency Logic

Request includes `active_baskets: list[ActiveBasket]` from EA (slot, side, opened_at_utc).

Adapter logic:
```
1. veto if risk_state.trading_halted OR openclaw.halt
2. veto if spread guard fail (spread_to_atr_m1 > 0.30)
3. veto if news_blackout >= 0.7
4. winner = select_best_v3(req)   # threshold 0.40, includes MOMENTUM_M1
5. apply gold_bias modifier
6. assign_slot:
   - if both slots empty -> slot A
   - if A active and side != A.side -> slot B
   - if A active and side == A.side -> VETO same-side
   - if A active and B active -> VETO capacity
7. plan = build_plan_v3(winner, req, slot)
8. persist + return
```

### 5. EA `QlipV3_XAUUSD.mq5` Outline

Inputs:
- `InpPlanUrl = "http://127.0.0.1:8765/v3/plan"`
- `InpEventUrl = "http://127.0.0.1:8765/v1/events/trade-transaction"`
- `InpEnableTrading = true`
- `InpBaseMagicA = 250530`
- `InpBaseMagicB = 250540`
- `InpMaxBasketLifetimeMinutes = 10`
- `InpCooldownMinutes = 2` (per slot)
- `InpMaxPendingMinutes = 10`
- `InpDxySymbol = ""` (optional, e.g., "USDX" or broker-specific)
- `InpVixSymbol = ""` (optional, e.g., "VIX")

OnTimer (1s):
- Cleanup stale pendings (both slots).
- Detect new M1 bar.
- Determine slot availability. Send `active_baskets` array.
- Build features 4 TF + optional DXY/VIX features.
- POST `/v3/plan`. Fail-closed.
- On `status=ok` & layers → bulk OrderSend per layer:
  - For `buy_market`/`sell_market`: `TRADE_ACTION_DEAL`
  - For `buy_limit`/`sell_limit`/`buy_stop`/`sell_stop`: `TRADE_ACTION_PENDING` with `ORDER_TIME_GTC`
- Save basket state to GlobalVariable per slot.

OnTick (light):
- Evaluate each active slot:
  - Floating PnL pct vs basket_start_equity → if ≥ `basket_tp_pct_equity` → close & cancel slot.
  - Price vs `invalidation_price` → close & cancel slot.
  - Time vs `opened_at + max_lifetime` → close & cancel slot.
- Start cooldown 2 min for closed slot.

OnTradeTransaction: POST to `/v1/events/trade-transaction` (reuse V1 endpoint, no schema change).

### 6. Dashboard Updates

- Recent plans card: show badge "V3 · A" / "V3 · B" next to scenario.
- Filter chip (future): "V2" / "V3" / "all".
- Stats card: split "Active baskets" into V2 vs V3.

### 7. Storage (Ledger)

Reuse existing `plans` + `plan_layers` tables. Add column `version TEXT DEFAULT 'v2'` via migration on first run. V3 writes `version='v3'`, V2 writes `version='v2'`.

## Data Flow

```
M1 close → EA features (4 TF + optional DXY/VIX) → POST /v3/plan
                                                    ├─ veto checks (halt, spread, news)
                                                    ├─ scenarios.select_best_v3
                                                    ├─ market_context.get_bias_modifier
                                                    ├─ assign_slot (A/B/veto)
                                                    ├─ planner_v3.build_plan
                                                    └─ ledger.write_plan (version='v3')
EA → bulk OrderSend (1 market + 2 limit + 2 stop, magic per layer)
On tick → per-slot basket evaluation (TP/invalidation/lifetime)
```

## Error Handling

| Layer | Case | Action |
|---|---|---|
| Adapter | scenario all < 0.30 | status=ok, scenario=NONE, layers=[] |
| Adapter | news blackout ≥ 0.7 | status=veto |
| Adapter | same-side parallel basket | status=veto, code APP-CONC-409 |
| Adapter | both slots busy | status=veto, code APP-CAPACITY-409 |
| Adapter | planner exception | status=degraded |
| EA | WebRequest fail | retry once (reuse V2 retry helper), then HOLD |
| EA | OrderSend retcode≠OK on L1 (market) | abort plan, cancel any placed pendings |
| EA | OrderSend fail on L2..L5 | log + skip layer, continue |
| EA | Basket max lifetime reached | force close all + cancel pendings + cooldown |

## Verification

1. `pytest adapter/tests/` — semua existing 41 lulus + ~15 baru.
2. EA compile 0 error 0 warning.
3. Smoke test demo:
   - Verify L1 market fills immediately.
   - Verify 2 limit + 2 stop appear di chart sebagai arrows.
   - Setelah basket selesai (TP/invalidation/lifetime): semua tertutup + slot cooldown.
   - Coba paksa scenario kedua arah berbeda → verifikasi Slot B assigned & 2 basket bisa hidup parallel.
4. Concurrency tests:
   - Plan ke-2 same-side ditolak (APP-CONC-409).
   - Plan ke-3 saat 2 slot full ditolak (APP-CAPACITY-409).

## Risk & Mitigations

- **Hard cap per layer** = `max_symbol_exposure_lots × weight` → anti tick_value bug.
- **Max 2 basket parallel** dengan beda sisi → hedge boleh, double-down dilarang.
- **Daily DD kill-switch**: floating + realized DD ≥ 5% equity → halt V3 (set GlobalVariable; reset via operator).
- **L1 market order failure → abort basket**: kalau anchor gagal, sisanya tidak place.
- **Cooldown per slot 2 menit**: cegah re-entry langsung setelah basket loss.

## Roadmap Pasca V3

- Calendar fetcher real (Investing.com).
- LLM verifier sebelum return plan.
- TradingView ideas integration.
- Replay tape backtest untuk V3.
