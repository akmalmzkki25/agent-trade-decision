# Pola candlestick

---

## 1. Putusan per pola

| Pola | M15 | M5 | M1 | Dasarnya |
|---|---|---|---|---|
| **Displacement / marubozu** | Pakai | Pakai | Marginal | Ekspansi volatilitas adalah variabel keadaan yang terukur dan persisten — bentuk candle bukan |
| **Opening range break** | Pakai | Pakai | Buang | **(a)** 6.142 hari ES/NQ: kontinuasi 70,7% dengan konfirmasi close 5 menit |
| **Break & retest** | Bersyarat | Bersyarat | Buang | **(a)** ~3.000 hari: lanjut 52–60% hanya di ekstensi 1,0–1,35× |
| **Engulfing di level** | Marginal | Marginal | Noise | **(a)** Satu-satunya pola reversal dengan sinyal peer-reviewed — tapi terukur vs Open/High/Low, **bukan** Close |
| **Pin bar / rejection wick** | Filter lokasi | Filter lokasi | Noise | **(b)** Rasio 66,7% adalah konvensi tanpa backtest. Di M1 emas, wick didominasi bid-ask |
| **Inside bar breakout** | Marginal | Marginal | Buang | **(c)** Angka beredar 37,33% vs "60–65%" — tidak sebanding, keduanya tak berguna |
| **Fair value gap** | **Buang** | **Buang** | **Buang** | **(a)** ~40.000 FVG di futures emas: **M15 justru paling buruk**; edge persis dimakan biaya |
| **Order block / SMC** | **Buang** | **Buang** | **Buang** | **(a)** 648 backtest, **nol** mengalahkan buy-and-hold |
| **Doji** | Buang sbg sinyal | Buang | Buang | Nol informasi arah. Di M1 emas, doji sering hanya bar tanpa tick |
| **Hammer / shooting star** | Subset pin bar | Subset pin bar | Buang | Angka "akurasi 75–85%" tidak bersumber |
| **Morning/evening star, 3 soldiers** | Terlalu jarang | Terlalu lambat | Buang | Butuh 3 bar = 45 menit di M15; pergerakannya sudah selesai |
| **3-bar reversal** | Filter lokasi saja | — | Buang | Terlalu sering muncul; filter lokasi yang mengerjakan hampir semuanya |
| **Liquidity sweep** | Filter konteks | Filter konteks | Buang | **(a)** SPY: edge 5 hari +0,119%, **t = 0,94**, n = 547 — tidak signifikan |

## 2. Bukti utama

### Duvinage, Mazza & Petitjean (2013) — studi paling tepat sasaran **(a)**

Sudah diuraikan di [01](01-biaya-dan-kelayakan-timeframe.md). Ringkasnya: 83 aturan
candlestick pada bar 5 menit, 30 saham DJIA, 20.550 observasi per saham, koreksi
Bonferroni dan Hansen SSPA. Setelah friksi 0,05%: **5 dari 83 bullish dan 3 dari 83
bearish bertahan signifikan. Nol mengalahkan buy-and-hold.**

### Heinz, Jamaloodeen, Saxena & Pollacia (2021) — engulfing, dan nuansanya penting **(a)**

*Bullish and Bearish Engulfing Japanese Candlestick patterns: A statistical analysis on
the S&P 500 index.* *Quarterly Review of Economics and Finance* 79, 221–244.

Temuan yang langsung bisa dikodekan: **bearish engulfing punya daya prediksi jangka
pendek yang kuat diukur terhadap kriteria Open dan High, tapi TIDAK terhadap Close.**
Bullish engulfing bekerja terhadap Open dan Low, **tapi tidak Close**.

**Kalau kita mengodekan "engulfing memprediksi close berikutnya", kita sedang mengodekan
kaki yang justru dikatakan studi ini tidak bekerja.**

### Marshall, Young & Rose (2006) **(a)**

Komponen DJIA 1992–2002, metodologi bootstrap yang membangkitkan deret OHLC acak.
**Tidak ada excess return yang signifikan secara statistik** dari strategi candlestick.

### Marshall, Young & Cahan (2007) — pasar Jepang **(a)**

Uji terpisah di pasar asal candlestick. https://doi.org/10.1007/s11156-007-0068-1

### Quantified Strategies — hasil positif, tapi bukan untuk timeframe kita **(b)**

SPY bar harian 1993–sekarang, 515 trade, win rate 74%, rata-rata gain 0,5%, profit
factor 2,40, 8,3% per tahun. **Tapi: bar harian, tahan 1–10 hari, biaya 0,03%.**
Kesimpulan mereka sendiri di tempat lain adalah bahwa interval 15 menit dan lebih pendek
tidak efisien karena noise. **Tidak bisa dipindahkan ke M1/M5.**

### Bulkowski — tidak ada di catatan ilmiah **(b)**

Bulkowski memeringkat 103 pola candle berdasarkan reversal rate, frekuensi, dan kinerja
10 hari, dengan sampel besar (mis. Two Black Gapping: 68% kontinuasi pada 18.264 kejadian).

**Tapi semuanya bar harian ekuitas, dan tidak ada di catatan peer-review:**

- Pencarian penulis Crossref: Thomas N. Bulkowski muncul **tepat sekali**, sebagai
  monografi Wiley (*Chart Patterns*, DOI 10.1002/9781119274933). Setiap "Bulkowski" lain
  adalah orang yang tidak terkait (kimia, biologi perikanan, teknik sipil).
- ***Encyclopedia of Chart Patterns* sendiri sama sekali tidak punya DOI Crossref.**
- OpenAlex pada satu DOI buku itu: **`cited_by_count`: 0**.
- Semantic Scholar: 29 catatan penulis "Bulkowski", **tidak satu pun Thomas N.**

**Putusan: Thomas N. Bulkowski tidak punya publikasi jurnal peer-review di Crossref,
OpenAlex, maupun Semantic Scholar. Karyanya hanya ada sebagai buku dagang Wiley dan
situsnya sendiri. Tidak ada artikel jurnal yang mereplikasi, menguji, atau memvalidasi
statistiknya.**

Dan yang menarik: Velay & Daniel mengutip Bulkowski di pendahuluan mereka sendiri sebagai
menunjukkan korelasi pola-return *"antara 50 dan 60%, di mana 50% tidak lebih baik dari
acak"*, lalu menyimpulkan *"Sendirian, pola-pola itu tidak cukup untuk memprediksi
tren."*

### Fair value gap — studi paling relevan, dan hasilnya negatif **(a)**

MPM Markets, pra-registrasi. **CME ES, NQ, GC (emas), SI. 20 Apr 2019 – 24 Mei 2026.
~2,5 juta bar 1 menit per instrumen, ~40.000 FVG. Diuji di 5 menit, 15 menit, dan
1 jam.** Exit diselesaikan pada data 1 menit, *"menghitung sentuhan stop atau target
hanya dari menit setelah entry benar-benar terisi."*

- Tingkat reaksi FVG melampaui acak di 34 dari 36 sel, median edge **~5 poin persentase**
  — nyata tapi tidak berarti secara ekonomi.
- *"Edge-nya paling banter persis dimakan biaya trading."* Tidak ada dari lima konstruksi
  yang bertahan.
- **Edge membaik saat timeframe memanjang. 5 dan 15 menit paling buruk.**
- Verbatim: *"Win rate turun dari kira-kira 73% ke kira-kira 50%… Seluruh edge yang
  tampak adalah look-ahead intrabar"* — profit factor **2,4 → 1,0**.

**Angka terakhir itu punya pemakaian kedua yang penting**: ia mengukur seberapa sering
harga menyentuh stop *dan* target di dalam bar yang sama. Sekitar **23 poin persentase**
kasus — dan backtest OHLC diam-diam memilih yang menguntungkan. → [12](12-metodologi-dan-statistik.md)

### SMC / ICT — nol dari 648 **(a)**

StatOasis. SPY (1993+), QQQ (1999+), DIA (1998+), IWM (2000+), **bar harian**, 648
backtest: Order Block (18 varian), FVG (18), Liquidity Sweep (3), OTE (6), plus kontrol
RSI/SMA/inside-bar, dibandingkan dengan buy-and-hold dan baseline 50-seed coin-flip.

- SPY Order Block **t = +1,22** (5 hari); SPY FVG **t = −0,08** (5 hari)
- Di semua pasar, **tepat satu dari 32 uji** melampaui t = 2 — persis yang diberikan
  keberuntungan
- ***"Nol dari 648 backtest mengalahkan sekadar memegang indeks pada laba bersih."***
- Putusan penulis: *"ICT mekanis pada bar harian adalah sistem entry biasa-biasa saja
  yang mengenakan cerita luar biasa."*

Peringatan yang adil: bar harian, ETF ekuitas, bukan emas, bukan intraday. Tapi
digabungkan dengan hasil MPM di futures emas intraday, arahnya konsisten.

## 3. Konfirmasi yang benar-benar dituntut praktisi

Ada konsensus luas, konsisten, dan **tidak terdokumentasi** pada empat konfirmasi. Layak
dikodekan semuanya karena murah dan semuanya mengurangi jumlah trade ke arah yang benar:

1. **Bar tertutup, bukan bar yang sedang terbentuk.** *"Candle engulfing yang belum close
   hanyalah wick dengan potensi."* Evaluasi hanya di `rates[1]`.
2. **Close melewati level, bukan wick menembusnya.** Kodekan: `close[1]` tegas melewati
   level ± buffer, dan tuntut `|close − level| ≥ k × spread` dengan k ≥ 3.
3. **Retest yang bertahan.** Data IB di [06](06-price-action-setup.md) mengkuantifikasi
   ini dengan benar.
4. **Konfirmasi volume — dengan tanda bintang besar.** Konten vendor secara universal
   merekomendasikan 1,5–2× rata-rata volume 20 bar. **Di XAUUSD MT5 ini tick volume,
   bukan volume traded.** Kodekan sebagai z-score relatif terhadap jendela bergulir di
   feed sendiri; jangan pernah sebagai ambang absolut, dan jangan pernah berasumsi ia
   bisa dipindahkan antar broker. → [11](11-order-flow-dan-data.md)

## 4. Aturan spesifik emas yang harus ditambahkan

**`wick_length ≥ 8 × spread`** untuk setiap sinyal berbasis wick, di atas rasio 66,7%.

Di M1 emas, wick $0,10 dengan spread $0,12 bukan rejection — itu bid-ask. **Filter
tunggal ini membunuh hampir semua pin bar M1, dan itu hasil yang benar.**

Dan tuntut wick itu menembus level yang sudah diidentifikasi sebelumnya lalu close
kembali di dalam. **Wick tanpa jangkar bukan setup** — emas menghasilkan wick yang
"terlihat seperti error di chart."

## 5. Rasio pin bar — dari mana angkanya

**Aturan standar:** rejection wick ≥ **66,7%** dari total rentang high-low bar; badan
≤ 33%; badan berada di sepertiga terjauh. Versi ketat yang sering dikutip adalah rasio
wick-ke-badan **3:1**.

Semua **tingkat (b)**: tidak ada backtest yang dipublikasikan di balik ambang 66,7%. Itu
konvensi, bukan optimum terukur.

---

## Apa yang harus dikodekan

1. **Displacement bar sebagai prioritas utama**, bukan pola reversal. → [06](06-price-action-setup.md)
2. Engulfing hanya dengan filter lokasi dan ukuran, dan **jangan uji terhadap Close**.
3. `wick_length ≥ 8 × spread` untuk semua sinyal berbasis wick.
4. Evaluasi hanya `rates[1]`; tembus level butuh close, bukan wick.
5. Volume hanya sebagai z-score relatif feed sendiri.
6. **Buang FVG, order block, doji, hammer, star, dan three soldiers sebagai sinyal.**
   Kalau dipakai sama sekali, hanya sebagai filter konteks atau veto.

---

## Referensi

**Tingkat (a) — kuantitatif, metodologi terbuka**

- Duvinage, M., Mazza, P. & Petitjean, M. (2013). *The Intraday Performance of Market
  Timing Strategies and Trading Systems Based on Japanese Candlesticks.*
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2125889
- Heinz, A., Jamaloodeen, M., Saxena, A. & Pollacia, L. (2021). *Bullish and Bearish
  Engulfing Japanese Candlestick patterns: A statistical analysis on the S&P 500 index.*
  *Quarterly Review of Economics and Finance* 79, 221–244.
  https://ideas.repec.org/a/eee/quaeco/v79y2021icp221-244.html
- Marshall, B., Young, M. & Rose, L. (2006). *Candlestick technical trading strategies:
  Can they create value for investors?* *Journal of Banking & Finance* 30(8), 2303–2323.
  https://doi.org/10.1016/j.jbankfin.2005.08.001
- Marshall, B., Young, M. & Cahan, R. (2007). *Are candlestick technical trading
  strategies profitable in the Japanese equity market?*
  https://doi.org/10.1007/s11156-007-0068-1
- MPM Markets. *Does the Fair Value Gap strategy work?* (pra-registrasi; CME ES/NQ/GC/SI,
  ~2,5 juta bar 1 menit per instrumen, ~40.000 FVG)
  https://mpmmarkets.com/research/does-the-fair-value-gap-strategy-work
- StatOasis. *ICT backtest: what survives* (648 backtest, 4 ETF indeks)
  https://statoasis.com/overfit/research/ict-backtest-what-survives
- Lo, A., Mamaysky, H. & Wang, J. (2000). *Foundations of Technical Analysis.*
  *Journal of Finance* 55(4). https://doi.org/10.1111/0022-1082.00265 ·
  NBER w7613: [`../pdf/lo-mamaysky-wang-2000-foundations-of-technical-analysis.pdf`](../pdf/lo-mamaysky-wang-2000-foundations-of-technical-analysis.pdf)

**Tingkat (b) — konsensus praktisi tanpa data**

- Bulkowski, T. *Encyclopedia of Chart Patterns* / *Encyclopedia of Candlestick Charts.*
  https://thepatternsite.com/top10.html ·
  http://traders.com/Documentation/FEEDbk_docs/2011/11/Bulkowski.html ·
  DOI buku Wiley: https://doi.org/10.1002/9781119274933 ·
  https://doi.org/10.1002/9781119202288
  **Tidak ada validasi peer-review yang ditemukan di Crossref, OpenAlex, atau
  Semantic Scholar.**
- Quantified Strategies, *Candlestick patterns that actually work* (SPY harian)
  https://quantifiedstrategies.substack.com/p/candlestick-patterns-that-actually
- Daily Price Action, aturan pin bar —
  https://dailypriceaction.com/blog/forex-pin-bar-trading-strategy/
- QuantVPS, formasi candle untuk scalper —
  https://www.quantvps.com/blog/candle-formations-every-scalper-needs-to-master
- Strike.money, pin bar — https://www.strike.money/technical-analysis/pin-bar
- StrategyQuant, inside bar breakout —
  https://strategyquant.com/blog/inside-bar-breakout-strategy-price-action-trading/

**Yang harus dibuang (c)**

- Klaim "hammer akurat 75–85% di support/resistance" (QuantVPS) — tanpa sampel, tanpa
  periode, tanpa asumsi biaya.
- Klaim "liquidity sweep 65–75% standalone, 80%+ dengan konfluensi"
  (https://www.quantum-algo.com/blog/guides/liquidity-sweep-trading-complete-guide/) —
  tanpa metodologi yang diungkap. Bandingkan dengan hasil StatOasis yang sebenarnya:
  t = 0,94 pada n = 547.
- "62% XAUUSD fakeout intraday" (fortraders.com) — tanpa sumber.
