# Biaya dan kelayakan timeframe

File ini menentukan bagaimana semua file lain dibaca. Kalau lantai biayanya tidak
dipahami, sisa dokumen ini mudah disalahartikan sebagai daftar setup yang tinggal
dipasang.

---

## 1. Satuan — selesaikan ini dulu

MetaTrader **tidak punya konsep "pip"**. Referensi properti simbol MQL5 hanya
mendefinisikan `SYMBOL_POINT`, `SYMBOL_DIGITS`, `SYMBOL_TRADE_TICK_SIZE`,
`SYMBOL_TRADE_TICK_VALUE`, dan `SYMBOL_TRADE_CONTRACT_SIZE`. Kata "pip" tidak muncul
sama sekali. **(a)**

Pip adalah kata marketing yang ditempelkan broker di atas platform — itulah sebabnya
mereka tidak sepakat.

| Satuan | Pergerakan harga | P&L per 1,00 lot |
|---|---|---|
| 1 point (`SYMBOL_POINT`) | $0,01/oz | $1,00 |
| "1 pip" konvensi A | $0,01/oz | $1,00 |
| "1 pip" konvensi B | $0,10/oz | $10,00 |
| $1,00 | $1,00/oz | $100,00 |

**Dua jebakan spesifik untuk kode:**

1. `SYMBOL_POINT` dan `SYMBOL_TRADE_TICK_SIZE` adalah properti terpisah dan **bisa
   berbeda**. Broker yang mengutip XAUUSD ke 3 digit punya point = 0,001 sementara tick
   size mungkin tetap 0,01. Kode yang menghitung nilai dari `Point` saja akan salah 10×
   di broker itu.
2. Baca `SYMBOL_TRADE_TICK_VALUE` saat runtime, jangan menurunkan dolar dari contract
   size. Itu satu-satunya nilai yang dijamin platform cocok dengan P&L kita.

Aritmetika 100 oz/lot × $0,01/oz = $1,00 per point per standard lot **benar**, tapi itu
*default*, bukan spesifikasi. Baca dari simbol.

## 2. Model biaya

| Jenis akun | Spread | Komisi | Slippage | Total bolak-balik per 1,00 lot |
|---|---|---|---|---|
| Raw/ECN, London–NY | 10–15 pts | $3,50/sisi = $7 | ~2 pts | **$19–24** (≈$0,19–0,24/oz) |
| Standard (tanpa komisi) | 25–40 pts | $0 | 2–4 pts | **$27–44** |
| Raw, sesi Asia | 15–25 pts | $7 | 2–4 pts | **$24–36** |
| Apa pun, detik pertama NFP/CPI | 30–60 pts, melonjak 100+ | — | 15+ pts | **$50–100+** |

**Konstanta kerja: $0,22/oz untuk akun raw di London/NY. $0,40 untuk akun standard.**

Ledger live kita sendiri mencatat `avg_spread_points` **26–35** pada basket V5 yang
sudah berjalan — konsisten dengan kolom "standard".

> **Belum terverifikasi.** Setiap domain broker diblokir ISP dari mesin riset ini
> (semuanya menunjuk `202.169.44.80`), jadi tidak ada satu pun lembar spesifikasi
> kontrak atau angka spread 2025–26 yang bisa diverifikasi langsung. Angka di atas
> berasal dari perbandingan pihak ketiga dan dari ledger kita sendiri. Cek sendiri
> lewat MT5 → klik kanan simbol → Specification.

## 3. Rumus target minimum

Win rate impas, dengan T = target kotor, S = stop kotor, c = friksi:

```
p* = (S + c) / (T + S)
```

Pada T = S = R ini menjadi `p* = 0,5 + c/(2R)`.

| Stop R | Friksi $0,22 (raw) | Friksi $0,40 (standard) |
|---|---|---|
| $0,50 (50 pts) | 72,0% | 90,0% |
| $1,00 (100 pts) | 61,0% | 70,0% |
| $1,50 (150 pts) | 57,3% | 63,3% |
| $2,50 (250 pts) | 54,4% | 58,0% |
| $5,00 (500 pts) | 52,2% | 54,0% |

**Aturan yang bisa dikodekan:** tolak setiap setup di mana
`friksi / jarak_stop > 0,10`. Di akun raw itu berarti `jarak_stop ≥ 220 points`;
di akun standard, `≥ 400 points`.

Cara lain melihatnya — biaya sebagai persentase R hanya bergantung pada jarak stop,
sama sekali tidak pada rasio R:R:

| Jarak stop | Points | Biaya sbg % R (friksi 26 pts) |
|---|---|---|
| $2,00 | 200 | **13,0%** |
| $3,00 | 300 | 8,7% |
| $6,00 | 600 | **4,3%** ← lantai desain |
| $9,00 | 900 | 2,9% |
| $12,00 | 1200 | 2,2% |

Edge intraday terbaik yang pernah dipublikasikan adalah **0,13–0,18R kotor**. Stop yang
memakan 0,13R dalam friksi **sudah menghabiskan seluruh edge realistis sebelum trade
dimulai.**

## 4. Temuan inti: friksi sebagai persen rentang bar

Ini angka yang memisahkan timeframe yang layak dari yang tidak.

**Turunan** dari rentang harian $45–70 yang diskalakan per timeframe dan sesi dengan
scaling √t, lalu disesuaikan karena varians terkonsentrasi di London/NY (kira-kira
2–3× varians per menit Asia, jadi rentang berskala ~1,4–1,7×):

| Timeframe | Sesi | Rentang bar tipikal | Friksi $0,22 sbg % rentang |
|---|---|---|---|
| M1 | Asia (00–06 UTC) | $0,45–0,80 | **28–49%** |
| M1 | London/NY (07–17 UTC) | $1,50–2,20 | **10–15%** |
| M5 | Asia | $1,10–1,80 | 12–20% |
| M5 | London/NY | $3,50–5,00 | **4,4–6,3%** |
| M15 | London/NY | $6,00–8,50 | **2,6–3,7%** |

**Sekarang bandingkan dengan studi yang ada:**

- Sampel saham Duvinage: friksi 0,05% melawan rentang bar 5 menit ~0,23% ⇒ friksi ≈
  **22% rentang bar**. Mereka tidak menemukan edge bersih.
- MNQ Mesfin: friksi 2 poin melawan bar 5 menit ~34 poin ⇒ **≈6% rentang bar**. Ia tetap
  tidak menemukan satu pun keluarga dengan edge kotor melebihi friksi.
- **Emas M5 di London/NY: ≈5–6%.** Setara struktural dengan setup MNQ Mesfin, jauh lebih
  baik daripada sampel saham Duvinage.
- **Emas M1 di London/NY: ≈10–15%.** **Emas M1 di Asia: 28–49%.**

**Artinya secara konkret:**

1. Emas M5 selama London/NY *tidak* otomatis tersingkir oleh hasil akademis — struktur
   biayanya 3–4× lebih ramah daripada sampel saham. Tapi hasil MNQ Mesfin pada rasio
   serupa adalah peringatan bahwa rasio biaya yang menguntungkan itu **perlu, bukan
   cukup**.
2. **Emas M1 di London/NY berada di rezim friksi yang sama di mana edge terbukti
   menghilang.** M1 di Asia mustahil secara aritmetika — kita membayar sepertiga sampai
   separuh rentang seluruh candle per bolak-balik.
3. Ini dasar kuantitatif di balik konsensus praktisi bahwa M5 mengalahkan M1 di emas.
   Praktisinya benar, tapi karena alasan yang umumnya tidak mereka sebutkan.

## 5. Bukti akademis

### Duvinage, Mazza & Petitjean (2013) — studi paling tepat sasaran **(a)**

*The Intraday Performance of Market Timing Strategies and Trading Systems Based on
Japanese Candlesticks.*

- **Data:** 30 konstituen DJIA, **bar 5 menit**, 1 Apr 2010 – 13 Apr 2011, 20.550
  observasi per saham.
- **Diuji:** 83 aturan candlestick, plus 2.047 sistem otomatis komposit; rata-rata
  ~24.232 aturan dan sistem per saham.
- **Metode:** koreksi Bonferroni dan Hansen SSPA untuk data snooping. Friksi 0,05%.
- **Hasil kotor:** 56% trade pada pola bullish individual menguntungkan; 22% pada
  pola bearish.
- **Setelah koreksi snooping, kotor:** 26 dari 83 bullish dan 27 dari 83 bearish masih
  signifikan.
- **Setelah friksi 0,05%:** hanya **5 dari 83** bullish dan **3 dari 83** bearish
  bertahan.
- **Melawan buy-and-hold, bersih:** *tidak ada aturan yang mengalahkan buy-and-hold di
  30 saham itu.* Tidak satu pun dari 2.047 sistem otomatis juga.

Kesimpulan mereka sendiri: candlestick bisa sedikit memprediksi return intraday, tapi
daya prediksinya tidak berguna untuk manajemen aktif.

### Mesfin (2026) — replikasi modern di futures 5 menit **(a)**

*Structural Limits of OHLCV-Based Intraday Signals in MNQ Futures: A Systematic
Falsification Study.*

- **Data:** futures Micro E-mini Nasdaq, **bar 5 menit**, 947 hari perdagangan,
  2021–2025.
- **Diuji:** 14 keluarga sinyal, divalidasi walk-forward.
- **Kriteria deployment:** out-of-sample, t ≥ 2,0, N ≥ 30 trade, positif bersih setelah
  friksi 2 poin, konsisten antar tahun.
- **Hasil:** return kotor berkisar **0,07 sampai 1,50 poin per trade**, melawan asumsi
  friksi 2 poin. **Tidak ada satu pun keluarga sinyal yang lolos semua kriteria.**
- Kontrol positif memang menyala (RTH Confluence t = 5,83, N = 538; London Session
  Signal B t = 5,15, N = 289), jadi ujinya punya daya — keluarga OHLCV-nya saja yang
  memang tidak ada.

Bacaan pentingnya **bukan** "biaya membunuhnya". Melainkan bahwa edge *kotor* per trade
dari sinyal OHLCV 5 menit naif berada di orde **0,2–4% dari rentang satu bar**. Itu
angka yang harus dilawan.

### Jin (2022) — emas, dengan koreksi snooping **(a)**

Futures emas SHFE, **bar 5 menit**, 12 Mar 2018 – 10 Mar 2021, dengan koreksi bias
data-snooping dan biaya transaksi. Kesimpulan verbatim: *"daya prediksi trading
teknikal intraday di pasar emas China bersifat ilusif."*

### Marshall, Young & Rose (2006) **(a)**

Komponen DJIA 1992–2002, metodologi bootstrap yang membangkitkan deret OHLC acak.
Tidak ada excess return yang signifikan secara statistik.

### Park & Irwin — lintasan yang menceritakan seluruhnya **(a)**

- **2007**, *Journal of Economic Surveys*: dari **95 studi modern, 56** menemukan hasil
  positif, **20** negatif, **19** campuran. Tapi peringatannya ada di abstrak mereka
  sendiri: *"sebagian besar studi empiris mengalami berbagai masalah dalam prosedur
  pengujian, misalnya data snooping, seleksi ex post atas aturan trading, dan kesulitan
  estimasi risiko dan biaya transaksi."*
- **2010**, *Journal of Futures Markets*: 17 pasar futures AS, 1985–2004, dengan
  White's Bootstrap Reality Check dan Hansen SPA. *"aturan terbaik menghasilkan profit
  ekonomi yang signifikan secara statistik hanya untuk **dua dari 17** pasar futures
  setelah koreksi bias data snooping."*

**Penulis yang sama.** Koreksi data-snooping adalah seluruh ceritanya.

---

## Apa yang harus dikodekan

1. Nyatakan setiap ambang dalam **points atau dolar per ounce**, tidak pernah pip.
2. Baca `SYMBOL_TRADE_TICK_VALUE` dan `SYMBOL_TRADE_TICK_SIZE` saat init, jangan
   asumsikan.
3. `spread_points ≤ 20` (raw) / `≤ 35` (standard) sebagai gerbang keras.
4. `jarak_stop ≥ 600 points ($6,00)` sebagai lantai desain, `≥ 220 points` sebagai
   minimum absolut di akun raw.
5. `jarak_stop ≥ 10 × spread saat ini`.
6. **M5 untuk sinyal, M15/H1 untuk level, M1 hanya untuk penghalusan eksekusi —
   tidak pernah untuk pembangkitan sinyal.**
7. Ukur `friksi / ATR(M5)` per jam pada feed sendiri dan perdagangkan hanya jam di
   bawah 0,08.

---

## Referensi

**Tingkat (a) — kuantitatif, metodologi terbuka**

- Duvinage, M., Mazza, P. & Petitjean, M. (2013). *The Intraday Performance of Market
  Timing Strategies and Trading Systems Based on Japanese Candlesticks.*
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2125889 ·
  https://ideas.repec.org/p/ajf/louvlr/2013001.html ·
  ringkasan: https://www.cxoadvisory.com/technical-trading/testing-japanese-candlesticks-intraday-on-liquid-stocks/
- Mesfin, M. (2026). *Structural Limits of OHLCV-Based Intraday Signals in MNQ Futures.*
  arXiv:2605.04004 — https://arxiv.org/abs/2605.04004 ·
  salinan lokal: [`../pdf/mesfin-2026-structural-limits-of-ohlcv-intraday-signals-mnq.pdf`](../pdf/mesfin-2026-structural-limits-of-ohlcv-intraday-signals-mnq.pdf)
- Jin, X. (2022). *Journal of International Financial Markets, Institutions and Money*
  76. https://ideas.repec.org/a/eee/intfin/v76y2022ics1042443121001876.html
- Marshall, B., Young, M. & Rose, L. (2006). *Candlestick technical trading strategies:
  Can they create value for investors?* *Journal of Banking & Finance* 30(8), 2303–2323.
  https://ideas.repec.org/a/eee/jbfina/v30y2006i8p2303-2323.html
- Park, C.-H. & Irwin, S. (2007). *What Do We Know About the Profitability of Technical
  Analysis?* *Journal of Economic Surveys* 21(4).
  https://doi.org/10.1111/j.1467-6419.2007.00519.x — tidak akses terbuka; working paper
  AgMAS 2004 (https://doi.org/10.2139/ssrn.603481) juga diblokir saat riset
- Park, C.-H. & Irwin, S. (2010). *A reality check on technical trading rule profits in
  the U.S. futures markets.* *Journal of Futures Markets* 30(7), 633–659.
  https://doi.org/10.1002/fut.20435
- Sullivan, R., Timmermann, A. & White, H. (1999). *Data-Snooping, Technical Trading
  Rule Performance, and the Bootstrap.* *Journal of Finance* 54(5).
  https://doi.org/10.1111/0022-1082.00163 ·
  salinan lokal: [`../pdf/sullivan-timmermann-white-1999-data-snooping-technical-trading-bootstrap.pdf`](../pdf/sullivan-timmermann-white-1999-data-snooping-technical-trading-bootstrap.pdf)
- Schulmeister, S. (2008). *Profitability of Technical Stock Trading: Has it Moved from
  Daily to Intraday Data?* WIFO Working Paper 323.
  [`../pdf/schulmeister-2008-profitability-of-technical-stock-trading-daily-to-intraday.pdf`](../pdf/schulmeister-2008-profitability-of-technical-stock-trading-daily-to-intraday.pdf)

**Spesifikasi platform (a)**

- MQL5, *Symbol Properties* —
  https://www.mql5.com/en/docs/constants/environment_state/marketinfoconstants
- MetaTrader 5 Help, *Price Data* —
  https://www.metatrader5.com/en/terminal/help/trading_advanced/price_data

**Biaya dan spesifikasi (b — pihak ketiga, tidak terverifikasi langsung)**

- Traders Mastermind, perbandingan spread emas sepuluh broker (screenshot simultan,
  25 Agu 2026) — https://tradersmastermind.com/gold-spread-comparison/
- Golden Viper, spread broker untuk emas —
  https://goldenviperea.com/blog/tools-brokers/broker-spreads-gold/
- comofx, kalkulator nilai pip XAUUSD —
  https://www.comofx.com/knowledge-hub/blog/xauusd-pip-value-calculator
- FXNX, spesifikasi kontrak XAUUSD —
  https://fxnx.com/en/blog/xauusd-contract-specs-tick-value-lot-size-margin
- Pepperstone Ltd, *Costs and Charges Information*, v5.0, Feb 2025 —
  https://docs.pepperstone.com/legal/uk/Pepperstone_Limited_Costs_and_Charges_Information.pdf
- NIFM Academy, matematika spread untuk scalping —
  https://www.nifmacademy.com/blog/post/333/forex-trading/forex-scalping-strategy-the-spread-math-that-decides-it
