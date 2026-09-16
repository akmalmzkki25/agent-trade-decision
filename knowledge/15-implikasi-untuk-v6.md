# Implikasi untuk V6

Apa yang seharusnya berbeda di V6, diturunkan dari file 00–14. Ini **bukan desain V6** —
spesifikasi V6 akan datang dari pengguna. Ini adalah batasan dan prinsip yang harus
dihormati desain apa pun, supaya V6 tidak mengulang masalah yang sudah terdokumentasi.

---

## Prinsip

### 1. Ukur prosesnya dulu, baru pilih geometrinya

Pertanyaan tunggal yang paling menentukan desain V6 — dan yang belum pernah dijawab untuk
V1–V5:

> **Pada horizon trading V6, di jendela sesi V6, apakah XAUUSD trending, mean-reverting,
> atau random walk?**

| Jawaban | Geometri | Trailing | Layering |
|---|---|---|---|
| Momentum (ρ > 0, VR > 1) | Stop rapat, target lebar | Ya, tidak terlalu rapat | Pyramiding pada kekuatan |
| Mean-reverting (ρ < 0, VR < 1) | **Stop lebar, target sempit** | **Tidak** | Ladder limit dengan stop basket nyata |
| Random walk (ρ ≈ 0) | Tidak ada geometri yang lebih baik | — | **Tidak ada alasan untuk layering** |

Cara mengukur: autokorelasi return per jendela sesi, dan **variance ratio bertanda** (bukan
`|1 − VR|`). Ambang Kaminski & Lo: stop menambah nilai di bawah momentum ketika
`ρ/(1−ρ) > SR`. → [12](12-metodologi-dan-statistik.md) §9

**Kalau jawabannya random walk, tuning TP/SL adalah overfitting, titik.**

### 2. Biaya adalah desain, bukan parameter

Keputusan timeframe, jumlah order per basket, dan jenis order semuanya adalah keputusan biaya.

- **M5 untuk sinyal, M15/H1 untuk level, M1 hanya untuk penghalusan eksekusi.**
- **Perdagangkan hanya jam di mana `friksi / ATR(M5) < 0,08`.**
- **Stop ≥ 600 points** sebagai lantai desain.
- **Limit order di mana pun bisa.** Setiap market order adalah biaya struktural.
- **Jumlah order per basket adalah pengali biaya.** 9 order × 30 points = 270 points.

→ [01](01-biaya-dan-kelayakan-timeframe.md)

### 3. Volatilitas per jam, bukan ATR(14)

- Ganti ATR(14) M15 dengan **true range slot-jam-yang-sama, 10 sesi terakhir.**
- Nyatakan semua ambang sebagai **kelipatan volatilitas atau persen harga**, tidak pernah
  dolar atau pip tetap.
- Perlakukan **bar data AS (12:30/13:30 UTC)** sebagai rezim terpisah.
- Rekalibrasi kuartalan — rezim 2026 adalah outlier 2× dengan paruh waktu 1,6 bulan.

→ [02](02-volatilitas-dan-atr.md)

### 4. Sentimen masuk ke lapisan risiko, tidak pernah ke lapisan sinyal

Tidak ada sentimen yang memprediksi arah emas secara intraday. Yang sah:
- **Veto kalender** di sekitar rilis terjadwal (dukungan terkuat)
- **Sizing sadar-volatilitas** (dukungan baik)
- **Flag rezim mingguan** dari COT/makro — terpisah tegas dari sinyal

→ [10](10-sentimen-dan-posisi.md)

### 5. Jangan membangun di atas data yang tidak ada

- **Tidak ada order flow di spot XAUUSD.** Kalau V6 butuh order flow, pilihannya adalah
  ganti instrumen (futures GC, keluar dari MT5) atau impor GC sebagai **konteks**, bukan
  sinyal.
- **Tick volume bukan volume.** Hanya sebagai z-score relatif terhadap feed sendiri.

→ [11](11-order-flow-dan-data.md)

### 6. Setup yang punya bukti, bukan yang populer

Prioritas: **displacement bar di level M15 → opening range break sesi → retest
continuation di jendela sempit → engulfing di level.** Sisanya filter atau veto.

**Jangan bangun:** deteksi pola chart klasik, FVG, order block, SMC/ICT, sinyal COT, sinyal
sentimen ritel kontrarian, sinyal OBI di spot.

→ [04](04-pola-candlestick.md), [05](05-pola-chart-klasik.md), [06](06-price-action-setup.md)

### 7. Time barrier sebagai exit primer

Bukti terbaik dari semua literatur exit: **barrier waktu yang melakukan pekerjaan
sesungguhnya.** Target jauh yang jarang mengikat + stop struktural + time barrier —
konstruksi triple-barrier.

- **Jangan** geser ke BE pada trigger R tetap.
- **Jangan** trailing kecuali momentum terbukti, dan jangan terlalu rapat.
- **Jangan** scaling out kecuali sudah terukur bahwa drift meluruh dengan umur trade.

→ [08](08-take-profit-dan-exit.md)

### 8. Kalau layering, hanya kategori (c) atau (a)

- **(c)** ladder limit serentak dengan stop basket nyata yang disizing untuk fill penuh, atau
- **(a)** pyramiding pada eksursi menguntungkan dengan tambahan yang mengecil.
- **Tidak pernah (b)** averaging-down.

Batas: 3 layer (keras 4), jarak `max(0,75 × ATR(H1), 20 × spread)`, lot rata atau menurun,
TP dihitung ulang dari risiko hidup di setiap fill, notional ≤ 10:1.

→ [09](09-layering-dan-basket.md)

---

## Pengukuran sebelum menulis kode strategi

Instrumentasi ini harus ada **sebelum** V6 diperdagangkan dengan parameter apa pun yang
di-tuning:

### Per trade / per basket
- [ ] MAE dan MFE dalam **satuan ATR**
- [ ] Waktu sampai MAE, waktu sampai MFE
- [ ] Jam UTC dan sesi entry
- [ ] Spread saat entry dan exit
- [ ] Slippage terealisasi per order
- [ ] Latensi keputusan
- [ ] Alasan exit (TP / SL / waktu / veto / eksternal)
- [ ] Skor skenario saat entry (untuk Gerbang 2 layering)

### Per jam
- [ ] ATR(14) M1 dan M5
- [ ] True range per slot 15 menit
- [ ] Distribusi spread (mean, median, p95)
- [ ] Rasio `friksi / ATR(M5)`

### Sekali saat init (uji falsifikasi)
- [ ] `SYMBOL_TICKS_BOOKDEPTH`
- [ ] Jumlah return `CopyTicks(..., COPY_TICKS_TRADE, ...)`
- [ ] Jumlah return `CopyRealVolume()`
- [ ] `SYMBOL_TRADE_TICK_VALUE`, `SYMBOL_TRADE_TICK_SIZE`, `SYMBOL_POINT`
- [ ] Offset server−GMT, dan apakah DST aktif

### Analisis offline
- [ ] Autokorelasi return per jendela sesi, pada horizon V6
- [ ] Variance ratio bertanda per jendela sesi
- [ ] Half-life OU kalau ada mean reversion

---

## Gerbang yang harus ada sejak hari pertama

Dirangkum dari file-file di atas. Semua dievaluasi **sebelum** logika sinyal.

| Gerbang | Aturan | Sumber |
|---|---|---|
| Spread | `spread_points ≤ 20` (raw) / `≤ 35` (standard) | [01](01-biaya-dan-kelayakan-timeframe.md) |
| Sesi | Jendela dari waktu lokal bursa; blokir 21:00–23:00 UTC | [03](03-sesi-dan-waktu.md) |
| Berita | Blokir ±15 menit sekitar rilis AS berdampak tinggi (tabel statis sampai kalender nyata) | [03](03-sesi-dan-waktu.md) |
| Friksi | `friksi / ATR(M5) < 0,08` untuk jam ini | [01](01-biaya-dan-kelayakan-timeframe.md) |
| Volatilitas minimum | `ATR(14, M5) ≥ 250 points` | [01](01-biaya-dan-kelayakan-timeframe.md) |
| Stop minimum | `jarak_stop ≥ 600 points` dan `≥ 10 × spread` — kalau tidak, **lewati** | [07](07-stop-loss.md) |
| Bar tertutup | Semua sinyal dari `rates[1]`; HTF dari bar HTF tertutup | [06](06-price-action-setup.md) |
| Pivot | Offset sejumlah lag konfirmasi | [05](05-pola-chart-klasik.md) |
| Eksposur | Total lot ≤ 10 × ekuitas / (harga × 100) | [09](09-layering-dan-basket.md) |
| Ekuitas minimum | Tolak trading di bawah ekuitas yang diizinkan aritmetika lot-step | [09](09-layering-dan-basket.md) |
| Pembulatan lot | Tolak plan kalau risiko pasca-pembulatan melampaui anggaran | [14](14-audit-ea-v1-v5.md) |
| Pemutus | 3% harian / 6% mingguan / 10% bulanan, terealisasi + mengambang, **batalkan pending** | [09](09-layering-dan-basket.md) |
| Arah basket | Satu arah per basket; tolak bursts berlawanan dan side kosong | sudah di V5 |
| Persistensi state | Setiap `g_basket_*` dipersisten ke GlobalVariable | sudah di V5 |

---

## Standar evaluasi V6

Sebelum V6 dianggap "bekerja":

1. **Backtest diselesaikan pada data M1/tick** dengan tie-break stop-duluan.
2. **Walk-forward atau out-of-sample**, bukan hanya in-sample.
3. **Koreksi multiple testing** atas semua konfigurasi yang dicoba — catat jumlahnya.
4. **t > 3,0**, bukan t > 2,0.
5. **N trade sesuai tabel** di [12](12-metodologi-dan-statistik.md) §1 untuk edge yang
   diklaim.
6. **Profit tidak terkonsentrasi** — buang 3 hari terbaik dan ulangi uji t.
7. **Bootstrap jalur alternatif** untuk parameter stop, bukan optimasi pada satu sejarah.
8. **Profit factor > 1,3 setelah biaya**, dengan biaya dimodelkan per jam dari data live.
9. **Kurva ekuitas ≥ 5.000 basket** sebelum membuat klaim apa pun tentang risiko ekor.

---

## Pertanyaan terbuka untuk spesifikasi V6

Hal-hal yang harus diputuskan pengguna, karena buktinya tidak memutuskannya:

1. **Horizon trading.** Scalping 2 menit (V5), intraday jam-an (V4), atau swing multi-jam?
   Ini menentukan timeframe mana yang layak secara biaya.
2. **Jenis akun.** Raw/ECN (friksi ~$0,22) atau standard (~$0,40)? Ini menggeser lantai stop
   hampir 2×.
3. **Tetap di spot XAUUSD atau pindah ke futures GC?** Hanya GC yang punya order flow asli.
4. **Apakah layering benar-benar dibutuhkan?** Layering kategori (c) netral eksekusi — ia
   tidak menciptakan edge. Kalau entry tunggal dengan sizing yang sama memberi eksposur
   setara dengan lebih sedikit spread, layering hanya menambah biaya.
5. **Ukuran ekuitas akun live.** Akun $1.000 hanya bisa menjalankan 2 layer 0,01 lot dalam
   batas eksposur 10:1.
6. **Apakah Claude/LLM masuk ke loop keputusan?** Bukti sentimen bilang LLM tidak punya
   keunggulan arah intraday — tapi LLM bisa berguna di lapisan risiko (membaca kalender,
   menilai rezim mingguan).
