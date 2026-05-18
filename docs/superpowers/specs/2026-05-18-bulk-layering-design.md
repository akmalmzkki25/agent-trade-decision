# Bulk Layering EA Design — XAUUSD M1

## Context

Phase 1 MVP (`QlipTrendBreakout_XAUUSD.mq5` + dummy decider) sudah berjalan di Exness/MetaQuotes-Demo dengan jalur EA → adapter → SQLite ledger terverifikasi. User minta lapis berikutnya — EA terpisah untuk **multi-order bulk layering** pada M1 dengan rule TA murni (LLM ditunda; modul news disiapkan sebagai stub).

Pemicu desain: M1 timeframe sangat noisy → keputusan harus dikuatkan multi-timeframe (M5+M15+H1), dan order tunggal kurang ekspresif untuk skenario range/breakout/pullback yang berbeda. Bulk layering ladder memungkinkan EA mengeksekusi "rencana" — multi order limit/stop simultan — dengan total risiko terkapsul.

Phase 1 tetap berdiri (backward compat), endpoint `/v1/decision` tidak diubah.

## Goals

- EA baru `QlipBulkLayer_XAUUSD.mq5` (magic terpisah) menempatkan **3–5 layer order** sekaligus per skenario menang.
- Tipe order menyesuaikan skenario: `buy_limit`, `sell_limit`, `buy_stop`, `sell_stop`, atau `market` (opsional untuk anchor).
- TA mendeteksi salah satu dari **tiga skenario**: RANGE_REVERT, TREND_BREAKOUT, CONTINUATION_PULLBACK; skor tertinggi menang.
- Risk total **1% equity per skenario**, dibagi rata ke layer.
- **Hybrid TP/SL**: SL per-layer (proteksi individual), basket TP di EA (semua close saat target net profit tercapai atau invalidation level kena).
- Sumber berita: stub `economic_calendar.py` (no-op return `{blackout:False}`); arsitektur siap diisi fetcher Investing.com nanti.
- LLM reasoning **belum diintegrasi** — adapter return JSON `plan` deterministik. `decider` Claude tetap stub.

## Non-Goals

- TradingView ideas scraper (skip — bisa ditambah nanti).
- Backtest replay tape (skip — Phase 3 nanti).
- Strategi >3 skenario; martingale; pyramiding murni.
- Mengubah Phase 1 EA/endpoint.
- LLM call di hot path.

## Arsitektur

```
adapter/app/
├── models.py                          # +LayerPlan, +LayerEntry, +LayerPlanRequest, +LayerPlanResponse
├── main.py                            # +POST /v2/plan
├── scenarios/                         # NEW
│   ├── __init__.py                    # ranker: pilih scenario skor tertinggi
│   ├── base.py                        # Protocol Scenario
│   ├── range_revert.py
│   ├── trend_breakout_stop.py
│   └── continuation_pullback.py
├── layering/                          # NEW
│   ├── __init__.py
│   ├── planner.py                     # build LayerPlan dari scenario
│   └── sizing.py                      # split risk → lots per layer
├── news/                              # NEW
│   ├── __init__.py
│   └── economic_calendar.py           # stub get_blackout()
└── ledger.py                          # +tabel plans, +tabel plan_layers

ea/QlipBulkLayer_XAUUSD.mq5            # NEW (M1 trigger)
```

## Komponen

### 1. Scenario detectors (`adapter/app/scenarios/`)

Setiap scenario implement `Scenario` protocol:
```python
class Scenario(Protocol):
    name: str
    def evaluate(self, req: LayerPlanRequest) -> ScenarioResult: ...
```

`ScenarioResult` berisi: `score: float (0..1)`, `side: "buy"|"sell"|"both"|"none"`, `confidence: float`, `key_levels: dict` (support, resistance, breakout_high, breakout_low, ema20_m5, dst), `reason_codes: list[str]`, `invalidation_price: float`.

**RANGE_REVERT** (`range_revert.py`):
- Trigger: H1 ADX < 20, H1 ATR percentile rendah (sideways).
- Side bias: M5 BB% lower → buy; upper → sell. Kedua sisi (both) jika BB squeeze + RSI extreme.
- Key levels: H1 swing high/low (lookback 20) sebagai S/R.
- Invalidation: break swing H1 ±0.5×ATR_H1.
- Order types yang akan dipakai planner: `buy_limit` (turun) dan/atau `sell_limit` (naik).

**TREND_BREAKOUT** (`trend_breakout_stop.py`):
- Trigger: H1 ADX ≥ 25, |DI balance| ≥ 0.20, BB width M15 di percentile bawah (kompresi), harga dekat range high/low M15 (≤0.3×ATR_M15).
- Side: arah DI balance.
- Key levels: 20-bar high/low M15 sebagai breakout level.
- Invalidation: harga balik melewati EMA50 M15 di sisi lawan.
- Order types: `buy_stop` di atas high (atau `sell_stop` di bawah low).

**CONTINUATION_PULLBACK** (`continuation_pullback.py`):
- Trigger: M15 ADX ≥ 22, harga di atas EMA50 M15 (bullish) atau di bawah (bearish), pullback ke EMA20 M5 (jarak ≤ 0.5×ATR_M5).
- Side: arah trend M15.
- Key levels: EMA20_M5, EMA50_M5.
- Invalidation: close M15 menembus EMA50 M15 sisi lawan.
- Order types: `buy_limit` (atau `sell_limit`) di sekitar EMA20/EMA50 M5.

**Ranker (`scenarios/__init__.py`)**:
```python
def select_best(req) -> Optional[ScenarioResult]:
    results = [s.evaluate(req) for s in [RangeRevert(), TrendBreakoutStop(), ContinuationPullback()]]
    candidates = [r for r in results if r.score >= MIN_SCENARIO_SCORE]
    if not candidates: return None
    return max(candidates, key=lambda r: r.score)
```

`MIN_SCENARIO_SCORE = 0.55` (tunable di settings).

### 2. Layering planner (`adapter/app/layering/`)

`planner.build_plan(scenario_result, req) -> LayerPlan`:

- **N layers** ditentukan dari skor: `N = 3 if score < 0.65 else (4 if score < 0.78 else 5)`.
- **Spacing**: `step = max(0.5 * atr_m15, base_step)`; tiap layer `i` (0-indexed) jaraknya `step * (1 + 0.4 * i)` dari anchor.
- **Anchor & arah penempatan**:
  - RANGE_REVERT buy: anchor = `support`; layer_i_price = `support - step * (1 + 0.4*i)`; type = `buy_limit`.
  - RANGE_REVERT sell: anchor = `resistance`; layer_i_price = `resistance + step * (1 + 0.4*i)`; type = `sell_limit`.
  - TREND_BREAKOUT buy: anchor = `breakout_high`; layer_i_price = `breakout_high + step * (1 + 0.4*i)`; type = `buy_stop`.
  - TREND_BREAKOUT sell: mirror dengan `sell_stop`.
  - CONTINUATION buy: anchor = `ema20_m5`; layer_i_price = `ema20_m5 - step * (1 + 0.4*i)`; type = `buy_limit`.
  - CONTINUATION sell: mirror.
- **SL per layer**: BUY → `sl = layer_price - max(1.5*atr_m15, dist_to_invalidation)`. SELL mirror.
- **Lots per layer** (`sizing.compute_lots`):
  ```
  risk_amount_total = equity * total_risk_pct  # 1% default
  risk_per_layer = risk_amount_total / N
  sl_dist = abs(layer_price - sl)
  raw_lots = risk_per_layer / (sl_dist / tick_size * tick_value)
  lots = floor_to_step(raw_lots, volume_step, min=volume_min)
  ```
- **TP per layer = null** (basket TP di EA).
- **Basket TP**: `basket_tp_pct_equity = total_risk_pct * BASKET_RR` (default RR=1.5 → 1.5% target).
- **Expiration**: `now_utc() + 30 menit` per layer (`ORDER_TIME_SPECIFIED`).
- **scenario_invalidation_price**: dari `ScenarioResult.invalidation_price`.

### 3. News stub (`adapter/app/news/economic_calendar.py`)

```python
class CalendarEvent(BaseModel):
    title: str
    impact: Literal["low", "medium", "high"]
    minutes_to: int   # negative if past, positive if future

class BlackoutDecision(BaseModel):
    blackout: bool
    severity: float  # 0..1
    event: str

def get_blackout(symbol: str, now_utc: datetime) -> BlackoutDecision:
    # Phase A stub: always returns no blackout.
    return BlackoutDecision(blackout=False, severity=0.0, event="")
```

Planner & ranker memanggil `get_blackout(...)` di awal; jika `severity >= 0.7` → return `status="veto"`, `plan.scenario="NONE"`.

### 4. API endpoint `/v2/plan`

Request schema `LayerPlanRequest` = `DecisionRequest` + tambahan:
- `features.confirm_tf` (M5 features) — wajib.
- `features.execution_tf` ditambah `volume_min`, `volume_step`, `volume_max`.

Response `LayerPlanResponse` (lihat brainstorm message untuk contoh full).

Status:
- `ok` — plan valid dengan ≥1 layer.
- `degraded` — adapter error; plan kosong; EA harus skip.
- `veto` — pre-check (halt / news blackout / spread); plan kosong; EA harus skip + log.

### 5. Ledger ekstensi

Tabel baru `plans` (request_id, symbol, scenario, side, confidence, basket_tp_pct, invalidation_price, layers_json, status, created_at). Tabel `plan_layers` (id, request_id, layer_id, order_type, price, lots, sl, tp, expiration_utc, mt5_order_ticket nullable). Reuse `trade_events` untuk OnTradeTransaction.

### 6. EA `QlipBulkLayer_XAUUSD.mq5`

- Magic base = `250519`. Magic per layer = `250519 + layer_id` (1..5).
- `OnInit`: indicator handles H1+M15+M5+M1 (ADX, EMA20/50, RSI, ATR, BBands). Timer 1s.
- `OnTimer`:
  - Detect new M1 closed bar.
  - Skip jika sudah ada basket aktif (PositionsTotal w/ base magic > 0 atau pending orders dengan magic 250519..250524 > 0).
  - Build payload 4 TF, POST `/v2/plan` (timeout 2000ms, fail-closed).
  - Jika `status=ok`, iterasi `plan.layers` → `OrderCheck` → `OrderSend` per layer (`ORDER_TYPE_*_LIMIT/STOP`, `ORDER_TIME_SPECIFIED`+expiration). Magic per layer.
- `OnTick` (light):
  - Hitung total floating PnL semua position dengan magic 250519..250524 + total realized closes today untuk basket aktif.
  - Jika `floating_pnl_pct_equity >= basket_tp_pct_equity` → close all positions + cancel all pendings (basket TP).
  - Jika `bid/ask` melewati `invalidation_price` (saved di global var) → close all + cancel (basket SL/invalidation).
- `OnTradeTransaction`: POST `/v1/events/trade-transaction` (reuse endpoint Phase 1).

EA menyimpan `invalidation_price` & `basket_tp_pct` di global var (`GlobalVariableSet`) supaya survive restart.

## Data Flow

```
M1 close → EA features (4 TF) → POST /v2/plan
                                  ├─ news blackout check → veto?
                                  ├─ scenarios.select_best
                                  ├─ planner.build_plan
                                  └─ persist plans + plan_layers
EA receives plan → OrderCheck/Send per layer → magic per layer
On tick → check basket TP / invalidation → close all + cancel all
OnTradeTransaction → POST /v1/events/trade-transaction
```

## Error Handling

| Lapisan | Kasus | Tindakan |
|---|---|---|
| Adapter | scenario tidak ada yang lolos threshold | `status=ok`, `scenario=NONE`, layers=[] |
| Adapter | news blackout severity≥0.7 | `status=veto`, layers=[] |
| Adapter | exception di planner | `status=degraded`, layers=[], error code `APP-PLAN-500` |
| EA | WebRequest gagal | HOLD, no orders, log |
| EA | OrderCheck fail untuk satu layer | skip layer itu, lanjut layer berikut, log retcode |
| EA | OrderSend fail untuk layer 1 | abort basket: cancel semua layer yang sudah berhasil placed |
| EA | basket invalidation tercapai | close all positions w/ magic basis, cancel all pendings, set cooldown 5 menit |

## Verifikasi

1. `pytest adapter/tests/` — unit test untuk: 3 scenario detector (happy + edge), planner (N layers correct, spacing correct, lots distribusi rata, basket TP), endpoint `/v2/plan` (ok / veto / degraded), news stub.
2. EA compile di MetaEditor → 0 error, 0 warning.
3. Smoke test demo: jalankan adapter + EA → tunggu sampai muncul plan dengan ≥3 layer placed di chart (visible sebagai pending order arrows). Cek tabel `plans` & `plan_layers` di SQLite.
4. Basket TP test: place limit order manual yang akan trigger TP cepat; pastikan EA close all + cancel pending saat target.
5. Invalidation test: paksa harga lewat invalidation level di tester atau demo; pastikan EA close all + cancel.
6. Fail-closed: matikan adapter → EA tidak crash, tidak place order.

## Risk & Mitigasi

- **Over-trading di M1**: cooldown 5 menit setelah basket selesai/invalid.
- **Pending menumpuk**: expiration 30 menit + cleanup periodik di EA (`OrderDelete` untuk pending dengan magic basis tapi tidak ada di plan aktif).
- **Spread spike M1**: spread guard di adapter (skip plan jika `spread_to_atr_m1 > 0.35`).
- **Konflik dengan EA Phase 1 di simbol sama**: magic terpisah (Phase 1 = 250518, Phase 2 = 250519..250524). User boleh attach kedua EA simultan; mereka tidak akan saling tutup posisi karena guard magic.
- **News blackout false positive (stub return False selalu)**: dokumentasi jelas bahwa stub no-op; user tahu harus isi sebelum live trading nyata.

## Roadmap pasca-MVP bulk-layering

1. Isi `economic_calendar.py` fetcher (Investing.com / FXStreet).
2. Tambah TradingView ideas scraper opsional.
3. LLM reasoning di `/v2/plan` (Claude verifikasi/override deterministic scenario output sebelum dikirim).
4. Replay tape harness yang juga handle `LayerPlan`.
