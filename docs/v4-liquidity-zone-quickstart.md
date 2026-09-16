# V4 Liquidity Zone Entry — Quickstart

V4 = endpoint `/v4/plan` + EA `QlipV4_XAUUSD.mq5`. Berbeda total dari V3 (spam) — V4 fokus **quality entry** dari zona likuiditas H1.

## Filosofi

| Aspek | V3 Aggressive | V4 Liquidity Zone |
|---|---|---|
| Frequency | Spam tiap M1 close | Selective — banyak HOLD, hanya entry saat zona valid |
| Layer count | 5 (1 market + 4 pending) | **2** (semua limit di dalam zone) |
| Risk per basket | 1% equity | 1% equity (sama) |
| Take profit | Basket TP 1.2% all-at-once | **Partial 50% @ +30 pip → SL ke BE → runner cap +100 pip** |
| Stop loss | Per-layer SL ATR | **Pip-based, max 50 pip dari zone bottom** |
| Lifetime | 10 menit | 30 menit (kasih ruang pullback) |
| Concurrency | 2 basket parallel beda sisi | **1 basket only** |
| Cooldown | 2 menit per slot | 3 menit |

## Cara kerja end-to-end

### 1. Identifikasi zona H1
- Cari swing low/high H1 (lookback 20 bar)
- BUY zone: top = swing_low + 10 pip, bottom = top − 30 pip
- SELL zone mirror dari swing high

**Contoh** (sesuai spec Anda):
- H1 swing low = 4155
- BUY zone top = 4156, bottom = 4153 (lebar 30 pip)
- SL = 4151 (bottom − 20 pip buffer = total 50 pip dari top)

### 2. Konfirmasi MTF (H1 → M15 → M5 → M1)
- **H1**: zona valid (swing terdeteksi)
- **M15**: harga di **luar** zona, approaching
- **M5**: arah candle terakhir (pullback bonus)
- **M1**: trigger (request plan tiap M1 close)

### 3. Place 2 layer limit di zona
| Layer | Type | Price | SL |
|---|---|---|---|
| L1 | buy_limit | 4156 (top) | 4151 |
| L2 | buy_limit | 4153 (bottom) | 4151 |

Lots split 50/50, di-cap oleh `max_symbol_exposure × 0.5`.

### 4. Exit logic (per-posisi, di-handle EA tiap tick)

Setelah posisi fill (mis. L1 fill di 4156):

```
Profit (pip) ──┬─< +30  → Tunggu (SL = 4151 = ~50 pip SL)
              ├─ +30   → Close 50% volume + SL geser ke entry (4156) ← BE
              ├─ +30..+100 → Runner aktif (downside locked di entry)
              └─ +100  → Close sisa (cap)

Saat balik ke 4156 (BE) → SL trigger, close di nol (BE protection bekerja)
```

### 5. Basket lifecycle
- Max 30 menit dari plan dibuka → force close all
- Single basket only — adapter veto plan baru selama ada posisi/pending dengan magic kita

## Setup

1. **Adapter restart** untuk muat endpoint baru:
   ```powershell
   cd "D:\Data Science\Qlip\ai-agent-trading\adapter"
   .\.venv\Scripts\Activate.ps1
   uvicorn app.main:app --host 127.0.0.1 --port 8765
   ```

2. **Copy EA**:
   ```powershell
   $mt5 = (Get-ChildItem "$env:APPDATA\MetaQuotes\Terminal\" -Directory | Where-Object Name -ne "Common" | Select-Object -First 1).FullName
   Copy-Item "D:\Data Science\Qlip\ai-agent-trading\ea\QlipV4_XAUUSD.mq5" "$mt5\MQL5\Experts\" -Force
   ```

3. **Compile**: MetaEditor F4 → buka `QlipV4_XAUUSD.mq5` → F7. Harus 0 error.

4. **Refresh Navigator MT5**, drag `QlipV4_XAUUSD` ke chart **XAUUSD M1**.

5. **Inputs penting** (di properties dialog):
   - `InpEnableTrading = true`
   - `InpTotalRiskPctOverride = 0.0` (= 1% default)
   - `InpBaseMagic = 250550`
   - `InpMaxBasketLifetimeMin = 30`
   - `InpCooldownMinutes = 3`
   - `InpSwingLookbackH1 = 20`

## Expected log

Saat plan fire:
```
V4 EA initialized. basket_opened=...
[V4 Plan] status=ok scen=LIQUIDITY_ZONE_ENTRY side=buy conf=0.70
V4 layer placed magic=250551 type=buy_limit @4156.00 lots=0.04 sl=4151.00
V4 layer placed magic=250552 type=buy_limit @4153.00 lots=0.10 sl=4151.00
V4 basket opened: 2 layers placed at 2026.05.21 09:01
```

Saat partial TP hit (+30 pip):
```
V4 ticket=12345 partial closed 0.02 lots at +30.1 pip
V4 ticket=12345 SL moved to BE @4156.00
```

Saat runner hit (+100 pip):
```
V4 ticket=12345 closed at +100.2 pip (runner cap)
```

Saat lifetime habis:
```
V4 closing basket: Max lifetime reached
```

## Outcome matrix (worst-best case)

Mengasumsikan equity $10k, 1% risk = $100 worst-case loss budget:

| Scenario | Outcome |
|---|---|
| Best case: harga tap zone → bounce → +100 pip cap | **+$100 profit** (1% equity) |
| Good: tap zone → +30 partial → ditarik ke BE | **+$15 profit** (partial saja, sisa BE) |
| Neutral: tap zone → +30 partial → runner kembali ke BE | **+$15 profit** (partial bagus, sisa nol) |
| Bad: tap zone → langsung balik break SL | **−$100 loss** (semua SL kena) |
| Skip: harga tidak pernah tap zone dalam 30 menit | **$0** (no fill, basket close) |

Expected value positif kalau win rate ≥ ~40% — karena partial TP + BE lock keuntungan kecil-kecil sambil ada chance runner besar.

## Tuning

Edit konstanta di [adapter/app/scenarios/liquidity_zone.py](adapter/app/scenarios/liquidity_zone.py):
- `ZONE_WIDTH_PIPS = 30.0`
- `ZONE_OFFSET_PIPS = 10.0` (gap dari swing point ke zone)
- `SL_BUFFER_PIPS = 20.0` (gap dari zone bottom ke SL)
- `MIN_DISTANCE_TO_ZONE_PIPS = 5.0`
- `MAX_DISTANCE_TO_ZONE_PIPS = 200.0`

Edit di [adapter/app/layering/planner_v4.py](adapter/app/layering/planner_v4.py):
- `PARTIAL_TP_PIPS = 30.0`
- `PARTIAL_CLOSE_FRACTION = 0.50`
- `RUNNER_CAP_PIPS = 100.0`
- `MAX_LIFETIME_SECONDS = 1800` (30 menit)
- `DEFAULT_TOTAL_RISK_PCT = 0.01`

Restart adapter setelah edit. EA tidak perlu recompile.

## Dashboard

Buka [http://127.0.0.1:8765/dashboard](http://127.0.0.1:8765/dashboard). Plans V4 akan muncul dengan badge **teal `v4`** (V2 = biru, V3 = ungu).

## Multi-EA coexistence

Boleh attach **4 EA bersamaan**:
- V1 (TrendBreakout) — magic 250518, chart M15
- V2 (BulkLayer) — magic 250519..250524, chart M1
- V3 (Aggressive) — magic 250530..250544, chart M1
- V4 (LiquidityZone) — magic 250550..250554, chart M1

Magic sepenuhnya terpisah, tidak akan saling tutup posisi.

## Troubleshooting

| Gejala | Solusi |
|---|---|
| `[V4 Plan] status=ok scen=NONE` terus-menerus | Tidak ada zona valid saat ini — harga ada di tengah swing range, atau terlalu jauh dari swing. Normal kalau market trending kuat tanpa pullback. |
| `[V4 Plan] status=veto code=APP-CONC-409` | Basket aktif. Tunggu basket selesai (max 30 min) atau cooldown 3 min. |
| Partial TP tidak trigger walau profit > 30 pip | Cek `PipSize()` di EA log — pastikan returns 0.10 untuk XAUUSD. Cek GlobalVariable `QlipV4_PARTIAL_<ticket>` apakah sudah 1. |
| `SL-to-BE failed retcode=...` | Broker reject SL modify (umumnya stops_level violation kalau harga sangat dekat entry). EA akan retry tiap tick sampai berhasil. |
| Position tidak fill dalam 30 menit | Harga tidak mencapai zone. Basket akan close dengan "Max lifetime reached". |

## Roadmap

- ✅ V1 (M15 trend breakout)
- ✅ V2 (M1 bulk layer, 3 scenario, basket TP)
- ✅ V3 (M1 aggressive, 5 scenario incl. AGGRESSIVE_BIAS, 2 slots parallel)
- ✅ V4 (M1 liquidity zone, partial TP + BE)
- ⏳ Calendar fetcher real (Investing.com)
- ⏳ Claude reasoning verifier di /v4/plan
- ⏳ Order block / fair value gap detection untuk zone (selain swing-based)
