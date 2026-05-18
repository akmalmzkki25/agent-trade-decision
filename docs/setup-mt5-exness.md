# Setup MT5 + Exness Demo untuk Qlip MVP

Panduan ringkas untuk menjalankan **Phase 1 MVP** (EA `QlipTrendBreakout_XAUUSD.mq5` + adapter FastAPI dummy decider) di MetaTrader 5 dengan akun **demo Exness**.

## 1. Install MetaTrader 5

1. Buka https://www.exness.com → "Open Account" → pilih **Demo**.
2. Unduh installer MT5 dari halaman Exness (Windows-only).
3. Install. Saat pertama dijalankan, login dengan kredensial demo yang dikirim Exness (server biasanya `Exness-MT5Trial` / `Exness-MT5Trial7` dst.).
4. Pastikan simbol **XAUUSD** muncul di Market Watch. Jika tidak, klik kanan Market Watch → Symbols → cari XAUUSD → Show.

## 2. Allowlist URL untuk WebRequest

EA memanggil adapter di `http://127.0.0.1:8765`. MT5 wajib di-allowlist:

1. **Tools → Options → Expert Advisors**.
2. Centang **Allow algorithmic trading**.
3. Centang **Allow WebRequest for listed URL**.
4. Tambahkan dua entry:
   - `http://127.0.0.1:8765`
   - `http://localhost:8765`
5. OK. (Catatan: URL ini **tidak bisa ditambahkan secara programatis**.)

## 3. Setup adapter Python

Prasyarat: Python 3.11+.

```powershell
cd "D:\Data Science\Qlip\ai-agent-trading\adapter"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env
```

Jalankan adapter:

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Verifikasi hidup:

```powershell
curl http://127.0.0.1:8765/v1/healthz
```

Harus return `{"ok":true,"version":"0.1.0","decider":"dummy_trend_breakout",...}`.

## 4. Compile & deploy EA

1. Copy `ea\QlipTrendBreakout_XAUUSD.mq5` ke folder data MT5:
   - Buka MT5 → **File → Open Data Folder** → masuk ke `MQL5\Experts\`.
   - Paste file `.mq5` di sana.
2. Buka **MetaEditor** (F4 di MT5) → buka file → **Compile** (F7). Harus 0 error.
3. Di MT5, drag EA dari Navigator → chart **XAUUSD M15**.
4. Di dialog properties:
   - Tab **Common**: centang "Allow Algo Trading".
   - Tab **Inputs**: biarkan default, atau ubah `InpEnableTrading` ke `false` untuk dry-run.
5. Pastikan icon AutoTrading di toolbar MT5 **hijau**.

## 5. Smoke Test

Biarkan jalan minimal sampai 1-2 bar M15 baru terjadi:

- **Tab "Experts" di MT5**: harus muncul log `[Decision] status=ok action=... ...` setiap bar M15 baru.
- **Tab "Journal" di MT5**: tidak boleh ada `WebRequest failed err=...`.
- **Ledger SQLite** (di folder adapter):

  ```powershell
  sqlite3 .\trade_ledger.db "SELECT request_id, action, side, lots, latency_ms FROM decisions ORDER BY created_at DESC LIMIT 10;"
  ```

- Jika ada keputusan `open`, cek tabel `trade_events` setelah fill:

  ```powershell
  sqlite3 .\trade_ledger.db "SELECT trans_type, retcode, deal_ticket FROM trade_events ORDER BY created_at DESC LIMIT 10;"
  ```

## 6. Fail-Closed Test

1. Stop adapter (`Ctrl+C` di terminal uvicorn).
2. Tunggu bar M15 baru.
3. EA harus log `WebRequest failed ...` dan **tidak** mengirim order.
4. Jalankan adapter lagi → operasi normal kembali.

## 7. Troubleshooting

| Gejala | Kemungkinan | Solusi |
|---|---|---|
| `WebRequest failed err=4060` | URL belum di-allowlist | Step 2 di atas |
| `WebRequest failed err=5203` | Bad URL / firewall lokal | Cek URL, allow uvicorn di Windows Firewall |
| Adapter 401 | `HMAC_REQUIRED=true` tapi EA tidak kirim signature | Set `HMAC_REQUIRED=false` di `.env` (dev mode) |
| `feature build failed (warming up?)` | Bar history belum cukup | Tunggu MT5 download history; ~hours of M15 + H1 |
| EA tidak send order, log `Already have position` | Sudah ada posisi XAUUSD dengan magic 250518 | Tutup posisi manual atau ganti `InpMagic` |
| Strategy Tester crash / no decisions | `WebRequest` tidak tersedia di tester | Expected — Phase 1 hanya untuk live demo. Replay tape menyusul di Phase 3 |

## 8. Catatan

- `OrderSend()` yang sukses **tidak** menjamin order benar-benar tereksekusi — selalu cek retcode dan event `OnTradeTransaction` (sudah di-handle EA).
- Mode akun (`netting` vs `hedging`) di-log saat `OnInit`. EA Phase 1 mengasumsikan satu posisi per simbol (guard di `TryExecute`).
- Risk per trade default **0.5%** equity (parameter `InpMaxRiskPerTradePct`). Naikkan hanya setelah validasi statistik out-of-sample.
