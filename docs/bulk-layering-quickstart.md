# Bulk Layering EA — Quickstart

Phase 2 EA (`QlipV2_XAUUSD.mq5`) menggunakan endpoint baru `/v2/plan` di adapter yang sama. Tidak ada perubahan setup adapter — instal-nya sama seperti Phase 1.

## 1. Adapter

Pastikan adapter sudah jalan (sama persis dengan Phase 1):

```powershell
cd "D:\Data Science\Qlip\ai-agent-trading\adapter"
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Test endpoint baru:

```powershell
curl http://127.0.0.1:8765/v1/healthz
```

## 2. Deploy EA Bulk Layering

1. Copy `ea\QlipV2_XAUUSD.mq5` ke folder data MT5 → `MQL5\Experts\`.
2. MetaEditor (F4) → buka file → Compile (F7). Harus 0 error.
3. Di MT5, buka chart **XAUUSD M1**.
4. Drag `QlipV2_XAUUSD` dari Navigator → chart M1.
5. Tab **Common** → centang Allow Algo Trading.
6. Tab **Inputs**: biarkan default; opsi yang relevan:
   - `InpEnableTrading = true` (false untuk dry-run tanpa kirim order)
   - `InpTotalRiskPctOverride = 0.0` (= pakai default server 1%)
   - `InpBaseMagic = 250519` (magic per layer = 250520..250524)
   - `InpCooldownMinutes = 5` (jeda setelah basket selesai)
7. **PENTING**: di Tools → Options → Expert Advisors, allowlist URL berikut (jika belum):
   - `http://127.0.0.1:8765`
   - `http://localhost:8765`

Kedua EA (Phase 1 dan Phase 2) **bisa jalan bersamaan di simbol sama** — magic terpisah (250518 vs 250519+).

## 3. Apa yang dilakukan EA

Setiap **M1 bar tertutup**:

1. Hitung fitur multi-TF (H1 + M15 + M5 + M1).
2. POST ke `/v2/plan`. Adapter:
   - Cek halt / spread guard / news blackout (stub).
   - Evaluasi 3 skenario: RANGE_REVERT, TREND_BREAKOUT, CONTINUATION_PULLBACK.
   - Skor tertinggi (≥0.55) menang; build plan 3–5 layer.
   - Atau return `scenario=NONE` jika tidak ada yang lolos.
3. Jika plan valid: EA `OrderSend` semua layer (limit/stop/market sesuai skenario), dengan `magic` per-layer dan expiration 30 menit.
4. Setelah basket aktif:
   - **Basket TP**: ketika total floating PnL ≥ `basket_tp_pct_equity` (default 1.5% equity) → close all + cancel pendings.
   - **Invalidation**: ketika harga lewat `scenario_invalidation_price` → close all + cancel.
   - Setelah basket selesai → cooldown 5 menit, tidak request plan baru.

## 4. Verifikasi

**Tab Experts MT5**, contoh log normal:

```
QlipV2_XAUUSD initialized. ACCOUNT_MARGIN_MODE=2
[Plan] status=ok scenario=RANGE_REVERT side=buy conf=0.72 basket_tp_pct=1.500 inv=2388.45
Layer placed magic=250520 type=buy_limit @2389.30 lots=0.04 sl=2386.80 exp=2026.05.18 17:05
Layer placed magic=250521 type=buy_limit @2388.20 lots=0.04 sl=2385.70 exp=2026.05.18 17:05
...
Basket activated: 4 layers placed, side=buy, inv=2388.45, tp_pct=1.500
```

**Chart**: harus muncul pending order arrows (4–5 buah) di harga ladder.

**SQLite ledger** (di `adapter/trade_ledger.db`):

```powershell
sqlite3 trade_ledger.db "SELECT scenario, side, confidence, layers_count, status FROM plans ORDER BY created_at DESC LIMIT 5;"
sqlite3 trade_ledger.db "SELECT layer_id, order_type, price, lots, sl FROM plan_layers WHERE request_id='<paste>' ORDER BY layer_id;"
```

## 5. Skenario yang dihasilkan adapter

| Skenario | Trigger ringkas | Order types |
|---|---|---|
| RANGE_REVERT | ADX H1 < 0.20, BB M5 di ujung, RSI extreme | buy_limit/sell_limit di S/R |
| TREND_BREAKOUT | ADX H1 ≥ 0.25, DI balance kuat, BB M15 squeeze, harga dekat range high/low | buy_stop/sell_stop di breakout level |
| CONTINUATION_PULLBACK | ADX M15 ≥ 0.22, trend M15 jelas, pullback ke EMA20 M5 | buy_limit/sell_limit di EMA20/EMA50 M5 |

## 6. Troubleshooting

| Gejala | Solusi |
|---|---|
| `WebRequest failed err=4014` | URL belum allowlist. Tambah `http://127.0.0.1:8765` di Options → Expert Advisors |
| `[Plan] status=ok scenario=NONE` terus | Pasar flat — tidak ada skenario lolos. Normal. Tunggu volatility / setup terbentuk |
| `OrderCheck fail` retcode 10016 (invalid stops) | SL terlalu dekat ke harga. Adapter sudah pakai `max(1.5*ATR, dist_to_invalidation)`; jika tetap kena, broker mungkin punya `STOPS_LEVEL` tinggi |
| Basket tidak menutup walau profit | Cek `g_basket_start_eq` di log; pastikan global var tersimpan |
| EA Phase 1 dan Phase 2 saling tutup | Tidak terjadi — magic terpisah. Pastikan tidak ada manual position dengan magic 250518–250524 |

## 7. Mengubah skenario / threshold

- **Threshold scenario**: edit `adapter/app/scenarios/*.py`, ubah konstanta (mis. `MIN_ADX_STRENGTH`).
- **MIN_SCENARIO_SCORE**: edit `adapter/app/scenarios/__init__.py` (default 0.55).
- **Total risk & basket RR**: edit `adapter/app/layering/planner.py` (`DEFAULT_TOTAL_RISK_PCT`, `BASKET_RR`).
- **Jumlah layer**: edit fungsi `_layer_count()` di planner.

Restart adapter setelah edit (`Ctrl+C` lalu `uvicorn ...` lagi). EA tidak perlu di-recompile — kontrak JSON sama.

## 8. Roadmap

- **News fetcher real** (Investing.com / FXStreet) menggantikan stub `economic_calendar.py`.
- **LLM reasoning layer** (Claude Opus 4.7) sebagai *verifier* sebelum plan kembali ke EA.
- **TradingView ideas scraper** sebagai input sentimen.
- **Replay tape** untuk backtest `/v2/plan` di MT5 Strategy Tester.
