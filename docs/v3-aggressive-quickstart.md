# V3 Aggressive Mixed-Layering — Quickstart

V3 = endpoint `/v3/plan` + EA `QlipV3_XAUUSD.mq5`. Hidup paralel dengan V1+V2+V4 (magic terpisah).

## Apa yang baru di V3

- **Cadence M1, 5 skenario**: RANGE_REVERT / TREND_BREAKOUT / CONTINUATION_PULLBACK / MOMENTUM_M1 / **AGGRESSIVE_BIAS** (fallback always-on).
- **Threshold 0.35** — sangat agresif, AGGRESSIVE_BIAS memastikan hampir tidak pernah HOLD selama ada bias arah.
- **Mixed-order ladder per plan** (5 layers):
  - L1: market (anchor, 30% risk)
  - L2-3: limit pullback (20% + 15%)
  - L4-5: stop momentum (20% + 15%)
- **Max 2 basket parallel** beda sisi (hedge OK, same-side dilarang).
- **Risk 1% per basket**.
- **Staged exit (NEW)** — replaced basket TP 1.2% all-at-once. 3 stage progressive trailing (lihat section di bawah).
- **Cooldown 2 menit per slot**.
- **Max basket lifetime 10 menit** (force close).
- **Fundamental bias modifier**: DXY/VIX → adjust skor maksimum ±0.10.

## Exit logic (RR 1:1 + Staged Exit fallback)

V3 sekarang punya **2 lapis exit** yang bekerja sinergis:

### Lapis 1: Per-layer broker TP (RR 1:1, default)

Setiap layer dikirim ke broker dengan **TP eksplisit** = entry ± SL distance × `rr_ratio`. Default `rr_ratio = 1.0` artinya:
- Layer entry 4540.50, SL 4538.50 (−$2.00 = −20 pip) → **TP 4542.50** (+$2.00 = +20 pip)
- Setiap layer close otomatis di broker pada SL atau TP-nya
- **Tidak ada lagi error "order limit" / "invalid order"** karena TP sekarang selalu di-set

### Lapis 2: Staged Exit (EA-managed, fallback safety net)

Jalan **hanya jika** SL distance per layer > 30 pip (mis. saat invalidation jauh). Saat tipikal (SL ~10–25 pip), broker TP fires duluan dan staged exit tidak relevan.

| Stage | Trigger | Aksi | SL akhir |
|---|---|---|---|
| **1** | profit ≥ **+30 pip** & ≥3 layer open | Close **3 layer** (oldest first), cancel semua pending, trail SL sisa | entry **−20 pip** |
| **2** | profit ≥ **+50 pip** | Trail SL semua sisa ke entry (BEP) | entry |
| **3** | profit ≥ **+60 pip** | Close 1 layer lagi, sisakan 1 runner | unchanged |
| **Cap** | profit ≥ **+100 pip** (per-posisi) | Close posisi itu | — |

### Mengganti RR ratio

Edit di [adapter/app/models.py](adapter/app/models.py) `V3ExitRules`:

| `rr_ratio` | Behavior |
|---|---|
| `1.0` (default) | TP = SL distance. Win/loss balanced. |
| `1.5` | TP 50% lebih jauh dari SL. Butuh win rate ≥40%. |
| `2.0` | TP 2× SL. Butuh win rate ≥33%. |
| `0.0` | **Disable per-layer TP** → kembali ke pure staged exit (legacy mode) |

Restart adapter setelah edit; EA tidak perlu re-compile (parse dari response).

### Outcome matrix

| Kondisi | Hasil per layer |
|---|---|
| Stuck < +30 pip → SL kena | Loss penuh original SL |
| Stage 1 hit, lalu reverse | 3 layer profit terkunci, 2 layer worst-case −20 pip |
| Stage 2 hit, lalu reverse | 3 layer profit + 2 layer BEP (zero loss) |
| Stage 3 hit, lalu reverse | 4 layer profit + 1 layer BEP |
| Cap +100 pip | Maximum 5× profit terkunci |

Konfigurasi di [adapter/app/models.py](adapter/app/models.py) → `V3ExitRules` (default langsung match spec di atas). EA parse dari response per plan, jadi tuning di-server tanpa recompile EA.

## Magic Allocation

| EA | Magic |
|---|---|
| V1 (TrendBreakout) | 250518 |
| V2 (BulkLayer) | 250519..250524 |
| V3 Slot A | 250530..250534 |
| V3 Slot B | 250540..250544 |

Tidak akan saling bentrok.

## Setup (asumsi adapter sudah jalan)

1. Adapter restart untuk muat endpoint baru:
   ```powershell
   cd "D:\Data Science\Qlip\ai-agent-trading\adapter"
   .\.venv\Scripts\Activate.ps1
   uvicorn app.main:app --host 127.0.0.1 --port 8765
   ```
   Verify: `curl http://127.0.0.1:8765/v1/healthz`.

2. Copy EA V3 ke MT5:
   ```powershell
   $mt5Data = (Get-ChildItem "$env:APPDATA\MetaQuotes\Terminal\" -Directory | Where-Object Name -ne "Common" | Select-Object -First 1).FullName
   Copy-Item "D:\Data Science\Qlip\ai-agent-trading\ea\QlipV3_XAUUSD.mq5" "$mt5Data\MQL5\Experts\" -Force
   ```

3. MetaEditor (F4) → compile `QlipV3_XAUUSD.mq5` → F7. Harus 0 error, 0 warning.

4. MT5: Navigator → klik kanan Expert Advisors → Refresh. Drag `QlipV3_XAUUSD` ke chart XAUUSD M1.

5. Inputs penting (di dialog properties):
   - `InpEnableTrading = true`
   - `InpTotalRiskPctOverride = 0.0` (= server default 1%)
   - `InpBaseMagicA = 250530`, `InpBaseMagicB = 250540`
   - `InpMaxBasketLifetimeMin = 10`
   - `InpCooldownMinutes = 2`
   - `InpDxySymbol = ""` (kosongkan kalau broker tidak punya DXY; isi mis. `"USDX"` kalau ada)
   - `InpVixSymbol = ""` (sama, isi kalau ada)

## Expected Log Pattern

Saat berjalan normal:

```
V3 EA initialized. SlotA active=false side= | SlotB active=false side=
[V3 Plan] status=ok scen=MOMENTUM_M1 side=buy slot=A conf=0.72 tp_pct=1.200 inv=4538.50
V3 layer placed magic=250531 type=buy_market @4540.50 lots=0.06 sl=4538.50
V3 layer placed magic=250532 type=buy_limit @4540.30 lots=0.04 sl=4538.30
V3 layer placed magic=250533 type=buy_limit @4540.10 lots=0.03 sl=4538.10
V3 layer placed magic=250534 type=buy_stop @4540.70 lots=0.04 sl=4538.70
V3 layer placed magic=250535 type=buy_stop @4540.90 lots=0.03 sl=4538.90
V3 basket slot A activated: side=buy layers=5 inv=4538.50 tp_pct=1.200
```

Beberapa M1 kemudian, kalau market berbalik dan ada sinyal SELL:

```
[V3 Plan] status=ok scen=TREND_BREAKOUT side=sell slot=B conf=0.65 ...
V3 layer placed magic=250541 type=sell_market ...
...
V3 basket slot B activated: side=sell ...
```

→ Dua basket aktif parallel (hedge buy + sell).

Saat staged exit fire:

```
V3 slot A STAGE1 done: closed=3/cancel_pendings=yes/trail_SL=entry-20.0pip
V3 slot A STAGE2 done: trail_SL=entry+0.0pip (BEP-style)
V3 slot A STAGE3 done: closed=1 (keep 1 runner)
V3 ticket=12345 closed at runner cap +100.0pip
```

## Dashboard

Buka `http://127.0.0.1:8765/dashboard`. Pada section **Recent layer plans**:
- Plans V2 → badge biru `v2`
- Plans V3 → badge ungu `v3 · A` atau `v3 · B`

## Bisa attach 3 EA bersamaan?

Ya. Magic semuanya terpisah:
- V1 di chart XAUUSD M15
- V2 di chart XAUUSD M1 (atau chart M1 berbeda)
- V3 di chart XAUUSD M1 (atau chart M1 ketiga)

Mereka tidak akan saling tutup posisi.

## Troubleshooting

| Gejala | Solusi |
|---|---|
| `[V3 Plan] status=veto code=APP-CONC-409` | Sudah ada basket sisi sama. Tunggu basket selesai atau cooldown. |
| `[V3 Plan] status=veto code=APP-CAPACITY-409` | Dua slot full. Tidak normal kalau sides berbeda (concurrency rule). Cek state. |
| `OrderSend fail retcode=10022` | Sudah di-fix dengan `ORDER_TIME_GTC`. Kalau muncul lagi, broker bermasalah. |
| `V3 anchor (market) failed; aborting basket.` | Layer L1 (market) reject (no money / invalid stops). Basket tidak diaktifkan. Cek freemargin. |
| Slot tidak pernah cooldown | Cek `g_cooldown_until` GlobalVariable: `QlipV3_A_COOLDOWN` |

## Tuning

Edit konstanta di Python (restart adapter setelah edit):

| Apa | File | Default |
|---|---|---|
| Min score threshold | [adapter/app/scenarios/__init__.py](adapter/app/scenarios/__init__.py) — `MIN_SCENARIO_SCORE_V3` | 0.40 |
| Total risk per basket | [adapter/app/layering/planner_v3.py](adapter/app/layering/planner_v3.py) — `DEFAULT_TOTAL_RISK_PCT` | 0.01 (1%) |
| Basket RR | `BASKET_RR` | 1.2 |
| Max lifetime | `MAX_LIFETIME_SECONDS` | 600 (10 min) |
| Layer weights | `_RECIPE_BUY/SELL` | 30/20/15/20/15 |
| Spacing factor | offset_atr di `_RECIPE_BUY/SELL` | ±0.4, ±0.8 × ATR_M1 |
| Fundamental bias weight | [adapter/app/news/market_context.py](adapter/app/news/market_context.py) — `apply_bias_to_score(weight=)` | 0.10 |

## Roadmap

- Calendar fetcher real (Investing.com) → ganti stub `economic_calendar.py`.
- LLM verifier sebelum return plan (Claude Opus 4.7).
- TradingView ideas integration.
- Replay tape untuk V3 backtest.
