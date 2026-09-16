# V5 High-Frequency Scalping Burst — Quickstart

V5 = endpoint `/v5/burst` + EA `QlipV5_XAUUSD.mq5`. **Tick-driven**, micro-burst layering, basket equity-managed exit. Inspired by the bulk-scalper video patterns: lots of small positions in microsecond bursts, collective basket close on small profit.

## Filosofi

| Aspek | V5 |
|---|---|
| Trigger | **OnTick** dengan throttle 250ms (bukan M1 close — terlalu lambat) |
| Signal | ATR sweet spot + VSA + DOM imbalance + 3-tick momentum + BB M1 + spread gate |
| Per burst | **3 market orders** lot 0.01 (broker minimum), no per-order TP/SL |
| Basket capacity | Up to **3 bursts** = 9 positions, averaging-in jika direction konsisten |
| Basket TP | **$5 USD** ATAU **0.05% equity** (mana duluan) |
| Basket SL | **$30 USD** ATAU **0.30% equity** (mana duluan) |
| Max lifetime | **2 menit** force close |
| Cooldown | **30 detik** setelah basket close |
| Magic | 250560..250569 (10 slot terpisah dari V1-V4) |

## Mengapa per-order TP/SL absent

Sesuai brief dari brainstorm Anda: per-position SL pada scalper biasanya **dimakan spread** sebelum berkontribusi. V5 hanya mengandalkan **basket-level exit**:
- Begitu PnL kolektif ≥ $5 (atau 0.05%) → close ALL
- Begitu PnL kolektif ≤ -$30 (atau -0.30%) → close ALL
- Begitu 2 menit lewat → force close ALL

## Signal layers (3 lapis analisis)

### Lapis 1 — Micro-volatility sweet spot (ATR)

Bot hanya aktif saat ATR M1 berada di band yang bisa di-scalp:

| Kondisi | ATR percentile | Aksi |
|---|---|---|
| Terlalu sepi | `< 0.25` | **Veto** `ATR_TOO_QUIET` — tidak ada range untuk di-scalp |
| **Sweet spot** | `0.25 – 0.90` | ✅ Lanjut evaluasi |
| Terlalu liar | `> 0.90` | **Veto** `ATR_TOO_WILD` — spike news, spread/slippage makan edge |

### Lapis 2 — Volume Spread Analysis (VSA)

Membandingkan *range* bar M1 (high−low) vs *volume*-nya untuk baca siapa yang pegang kendali:

| Pola VSA | Kondisi | Interpretasi | Efek |
|---|---|---|---|
| `VSA_CLIMAX` | vol_z ≥ 2.0 **dan** range_z ≥ 1.0 | Blow-off bar, exhaustion | **VETO** — jangan kejar |
| `VSA_ABSORPTION` | vol_z ≥ 1.5 **dan** range_z ≤ −0.5 | Stopping volume, reversal dekat | +0.05 score |
| `VSA_NO_DEMAND` | vol_z ≤ −0.5 **dan** range_z ≥ 1.0 | Move tanpa partisipasi = lemah | +0.05 score |
| `VSA_EFFORT_CONFIRMED` | vol_z ≥ 1.5 **dan** range_z ≥ 0 | Volume nyata backing move | +0.10 score |

### Lapis 3 — Order Book Imbalance (OBI)

EA coba `MarketBookAdd()` saat init. Kalau broker kasih DOM:
- `dom_imbalance` = (bid_vol − ask_vol) / total, top 5 level
- Searah posisi (≥ +0.20) → **+0.10 score**
- Melawan posisi (≤ −0.20) → **−0.15 score**

Kalau broker tidak support DOM (umum di retail FX/CFD), nilainya 0.0 dan diabaikan — fallback ke tick volume z-score sebagai proxy.

## Hard gates (sebelum burst)

| Gate | Threshold | Effect |
|---|---|---|
| Spread | `spread_points > 30` | Skip burst |
| **ATR sweet spot** | `< 0.25` atau `> 0.90` percentile | Skip burst |
| **VSA climax** | vol_z ≥ 2.0 + range_z ≥ 1.0 | Skip burst |
| Margin level | `< 300%` | Skip burst — proteksi margin call |
| Rate limit | `< 250ms` since last burst | Skip burst — anti-spam |
| Basket capacity | `≥ 3 bursts active` | Skip burst — basket full |
| RSI M1 extreme | `abs(centered) ≥ 0.40` | Skip burst — exhaustion |
| BB M1 extreme opposite | `bb_pos ≥ 0.85` untuk buy (mirror sell) | Skip burst |
| Cooldown | `< 30 sec` after last basket | Skip burst |

## Metrik performa (Section 3 brief)

Setiap basket yang close di-POST ke `/v1/events/basket-result` lalu diagregasi. Buka `/dashboard` untuk lihat card **Strategy performance**:

| Metrik | Formula | Target brief |
|---|---|---|
| **Profit Factor** | gross_profit / abs(gross_loss) | **> 1.3 – 1.5** |
| **Win Rate** | wins / (wins + losses) | 75–90% khas scalper |
| **Expected Value** | `(WR × avg_win) − (LR × abs(avg_loss))` | **harus positif** |
| **Max Floating DD** | floating PnL terburuk dalam basket | pantau vs margin |
| **p95 Latency** | percentile-95 decision latency | **< 20 ms** |
| **Avg Slippage** | abs(fill_price − requested_price) dalam points | sekecil mungkin |
| **Avg Spread** | spread saat kirim order | < expected profit |
| **Sample size** | jumlah basket close | **≥ 1000** sebelum dipercaya |

Dashboard menampilkan badge peringatan **"too early to trust"** selama sample < 1000 — sesuai brief, jangan percaya edge scalper di bawah 1000 trade.

API akses langsung:
```bash
curl "http://127.0.0.1:8765/api/dashboard/metrics?version=v5"
curl "http://127.0.0.1:8765/api/dashboard/equity-curve?version=v5&limit=200"
curl "http://127.0.0.1:8765/api/dashboard/basket-results?version=v5&limit=25"
```

Contoh output metrics:
```json
{
  "version": "v5",
  "sample_size": 412,
  "is_significant": false,
  "min_significant_sample": 1000,
  "profit_factor": 1.42,
  "meets_profit_factor_target": true,
  "win_rate_pct": 81.3,
  "avg_win": 4.85,
  "avg_loss": -18.20,
  "expected_value": 0.5395,
  "max_floating_drawdown": -27.40,
  "p95_latency_ms": 14.0,
  "avg_slippage_points": 2.1,
  "avg_spread_points": 11.4
}
```

Perhatikan pola khas scalper di contoh itu: win rate 81% tapi avg_loss hampir 4× avg_win. EV tetap positif (+0.54/basket) — tapi itulah kenapa brief menekankan hitung EV, bukan cuma lihat win rate.

## Cara kerja end-to-end (satu basket cycle)

```
T=0:00.000  OnTick fires
T=0:00.000  Spread=12 OK, margin=900% OK, last_burst=999s ago OK
T=0:00.000  Tick momentum: 3 last bids up = +1
T=0:00.000  Tick volume z=1.5 (spike)
T=0:00.000  RSI M1 = 0.10 (neutral), BB pos = 0.20 (room above)
T=0:00.000  POST /v5/burst → status=ok, side=buy, 3 layers
T=0:00.050  SendMarket buy 0.01 magic=250560 → filled
T=0:00.100  SendMarket buy 0.01 magic=250561 → filled
T=0:00.150  SendMarket buy 0.01 magic=250562 → filled
T=0:00.150  Burst#1 placed. Basket opened. start_eq=$10000.

T=0:15.000  Price up by 5 pip. Basket PnL = 3 × 0.01 × 100oz × $0.50 = $1.50
T=0:30.000  Price up 10 pip. PnL = $3.00
T=0:42.000  Price up 12 pip. PnL = $5.20 ≥ TP $5
T=0:42.001  CloseBasket("TP $5.20"). All 3 positions closed.

T=0:42.001  Cooldown 30 sec. No new bursts.
T=1:12.001  Cooldown done. Ready for next setup.
```

## Averaging-in scenario (multi-burst basket)

```
T=0:00  Burst#1 buy at 4170.50 (3 layers)
T=0:30  Price drops to 4170.20 (PnL ≈ -$0.90). Tick momentum reverses up.
T=0:30  Adapter: side=buy ok, basket has 1 burst, allow burst#2.
T=0:30  Burst#2 placed at 4170.20 (3 more layers). Average entry now 4170.35.
T=0:45  Price recovers to 4170.45. PnL = 6 × 0.01 × 100oz × $0.10 = $0.60
T=1:00  Price 4170.70. PnL = $5.10 → TP hit → close all 6 positions.
```

## Setup

1. **Restart adapter** untuk muat endpoint `/v5/burst`:
   ```powershell
   cd "D:\Data Science\Qlip\ai-agent-trading\adapter"
   .\.venv\Scripts\Activate.ps1
   uvicorn app.main:app --host 127.0.0.1 --port 8765
   ```

2. **Copy EA**:
   ```powershell
   $mt5 = (Get-ChildItem "$env:APPDATA\MetaQuotes\Terminal\" -Directory | Where-Object Name -ne "Common" | Select-Object -First 1).FullName
   Copy-Item "D:\Data Science\Qlip\ai-agent-trading\ea\QlipV5_XAUUSD.mq5" "$mt5\MQL5\Experts\" -Force
   ```

3. **Compile**: MetaEditor F4 → F7. Harus 0 error.

4. **MT5 Navigator refresh**, drag `QlipV5_XAUUSD` ke chart **XAUUSD M1**.

5. **Inputs penting**:
   - `InpEnableTrading = true`
   - `InpMinBurstIntervalMs = 250`
   - `InpBasketTpUsd = 5.0`
   - `InpBasketSlUsd = 30.0`
   - `InpMaxBurstsPerBasket = 3`
   - `InpMaxSpreadPoints = 30`
   - `InpMinMarginLevelPct = 300.0`

## Expected log

```
V5 EA initialized. magic=250560..250569 basket_open= bursts=0 start_eq=0.00 cooldown=
V5 burst#1 placed: side=buy layers=3 (basket pnl now=0.05)
V5 closing basket: TP $5.23 (0.052%) (pnl=5.23)
```

Atau saat averaging:
```
V5 burst#1 placed: side=buy layers=3 (basket pnl now=0.05)
V5 burst#2 placed: side=buy layers=3 (basket pnl now=-0.40)
V5 closing basket: TP $5.10 (0.051%) (pnl=5.10)
```

## Keamanan adapter

Adapter dengar di `127.0.0.1` dan dipanggil EA via `WebRequest()`. **Localhost saja bukan batas keamanan** — halaman web apa pun yang Anda buka bisa POST ke `127.0.0.1:8765`. Guard yang terpasang:

| Guard | Efek |
|---|---|
| Wajib `Content-Type: application/json` | Menolak *simple request* lintas-origin (form/text) yang browser kirim tanpa preflight → **HTTP 415** |
| Tolak `Sec-Fetch-Site: cross-site` / `Origin` asing | Request yang jelas dimulai browser dari situs lain → **HTTP 403** |
| Batas ukuran body (`MAX_REQUEST_BYTES`, default 256 KB) | Cegah memory exhaustion → **HTTP 413** |
| HMAC opsional (`HMAC_REQUIRED`) | Tanda tangan HMAC-SHA256, perbandingan timing-safe |
| **Startup guard** | Adapter **menolak start** kalau `ADAPTER_HOST` bukan loopback sementara `HMAC_REQUIRED=false`, atau kalau HMAC aktif tapi kunci masih placeholder |
| Security headers | `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, CSP dengan `frame-ancestors 'none'` |
| `/docs` mati by default | Set `ENABLE_DOCS=true` hanya saat development |

MT5 `WebRequest()` tidak mengirim `Origin` maupun `Sec-Fetch-Site`, jadi traffic EA yang sah lolos tanpa perubahan konfigurasi apa pun.

Kalau Anda ingin membuka dashboard dari HP/mesin lain di LAN, jangan cuma ubah `ADAPTER_HOST` — adapter akan menolak start. Set juga:

```env
ADAPTER_HOST=0.0.0.0
HMAC_REQUIRED=true
INTERNAL_HMAC_KEY=<secret acak yang kuat>
```

## Risiko & batasan

⚠️ **Strategi ini berisiko tinggi.** Beberapa catatan dari brief:

1. **Broker restriction**: Banyak broker (B-Book) reject scalping <1 min atau spamming order. Pakai broker ECN/A-Book.
2. **Spread**: XAUUSD spread bisa melebar mendadak. `InpMaxSpreadPoints=30` adalah guard, tapi review broker spec.
3. **Slippage**: Market order pada volatility tinggi bisa fill di harga jauh dari quote. `InpDeviationPoints=30` toleransi.
4. **Margin call**: Layering 9 positions consume margin signifikan. `InpMinMarginLevelPct=300%` guard ini.
5. **Statistical significance**: Brief Anda benar — jangan percaya performa <1000 trades. Jalankan demo lama untuk validasi.
6. **API rate limit**: Adapter local, tidak ada rate limit. Tapi broker server bisa reject jika EA spam.

## Tuning

Edit [adapter/app/models.py](adapter/app/models.py) class `V5ExitRules`:

```python
basket_tp_usd: float = 5.0              # naikkan untuk target lebih besar (mengurangi win rate)
basket_tp_pct_equity: float = 0.05      # sama, dalam persen
basket_sl_usd: float = 30.0             # turunkan untuk loss lebih kecil per basket
max_lifetime_seconds: int = 120         # 2 min
max_bursts_per_basket: int = 3          # turunkan ke 1 = no averaging, lebih aman
min_burst_interval_ms: int = 250        # naikkan untuk less frequent
cooldown_seconds: int = 30
```

Edit [adapter/app/scenarios/scalp_micro.py](adapter/app/scenarios/scalp_micro.py):
- `MAX_SPREAD_POINTS = 30.0` — turunkan untuk lebih ketat
- `MIN_TICK_VOL_Z = 0.5` — naikkan untuk butuh confirmation lebih kuat
- `RSI_EXTREME = 0.40` — turunkan untuk lebih hati-hati di exhaustion zone

EA `InpMaxSpreadPoints` & `InpMinMarginLevelPct` juga gates di MT5 side (selain server-side veto).

Restart adapter setelah edit. EA tidak perlu recompile (values terkirim per burst).

## Dashboard

Buka [http://127.0.0.1:8765/dashboard](http://127.0.0.1:8765/dashboard). Plans V5 akan muncul dengan **badge pink `v5`**. Recent layer plans card menampilkan scenario SCALP_MICRO, side, confidence, dan layers count per burst.

## Multi-EA coexistence

Boleh attach **5 EA bersamaan**:
- V1 (TrendBreakout) — magic 250518, M15
- V2 (BulkLayer) — magic 250519..250524, M1
- V3 (Aggressive) — magic 250530..250544, M1
- V4 (LiquidityZone) — magic 250551..250552, M1
- V5 (Scalping) — magic 250560..250569, M1

Semua magic terpisah, tidak akan saling tutup posisi.

## Roadmap V5

- ⏳ Real OBI/DOM integration (broker permitting via `MarketBookGet`)
- ⏳ VSA (Volume Spread Analysis) signal
- ⏳ Adaptive `MAX_SPREAD_POINTS` berdasarkan session (London/NY tighter, Asian wider)
- ⏳ Per-burst latency tracking via dashboard metric card
- ⏳ Expected Value (EV) calculator dari ledger history
