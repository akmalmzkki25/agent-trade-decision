# Sesi dan waktu

---

## 1. Peta UTC

Waktu bertanda ± bergeser satu jam dengan DST. Musim dingin (Nov–Mar, EST/GMT) memakai
waktu yang lebih akhir; musim panas (Mar–Nov, EDT/BST) yang lebih awal.

| UTC | Peristiwa | Karakter emas | Tindakan EA |
|---|---|---|---|
| 21:00–23:00 | Jeda harian / rollover (maintenance CME 21:00–22:00 UTC musim dingin) | Mati. Spread melebar ke 40–80 pts | **Blokir keras** |
| 23:00–06:00 | Asia | Volatilitas terendah, spread relatif terlebar, cenderung mean-revert | **Blokir M1 sepenuhnya.** M5 hanya fade range dengan target ≥ $2,50 |
| 00:00–02:00 | Pagi Shanghai | **1,17–1,37× rata-rata** — bukan bagian yang sepi | Jangan masukkan ke definisi "range Asia" |
| 03:00–05:00 | Jeda Asia sesungguhnya | 0,64–0,76× | Jendela paling mati untuk emas |
| 06:00–07:00 | Frankfurt / pra-London | Ekspansi pertama mulai | Tandai high/low range Asia di sini |
| 07:00± / 08:00± | **Open London** | Break range Asia, percobaan arah pertama | Aktifkan ORB. Harapkan banyak fakeout |
| 09:30± / 10:30± | Lelang LBMA pagi (10:30 London) | Peristiwa likuiditas singkat | Jeda entry 2 menit |
| 11:00–17:00 | **Puncak jumlah transaksi** | Jendela inti yang layak | **Jendela perdagangan utama** |
| 12:30± / 13:30± | **Data AS (08:30 ET)** | Keras; spread 30–50+ pts, slippage 15+ pts | **Blokir keras ±15 menit** |
| 13:30± / 14:30± | **Open NY / COMEX (09:30 ET)** | Puncak volatilitas menurut data akademis | Aktifkan ORB kedua |
| 14:00± / 15:00± | **Lelang LBMA sore (15:00 London)** | Fixing fisik, sering jadi titik balik | Jeda entry 2 menit; pakai harga fix sebagai level |
| 17:00–21:00 | Pasca-London | Likuiditas turun, spread melebar, retest lebih sering gagal | Matikan setup kontinuasi; fade atau diam |
| 18:30± / 19:30± | Settlement harian COMEX (13:30 ET) | Ramai sebentar lalu tenang | Kurangi ukuran |

## 2. Jebakan DST — ini akan diam-diam merusak filter sesi

Dari dokumentasi MQL5 **(a)**:

- Broker menyelaraskan close candle harian ke 17:00 New York. **Musim dingin (EST):
  server = GMT+2. Musim panas (EDT): server = GMT+3.**
- **AS dan Eropa mengubah jam pada hari Minggu yang berbeda** — AS bergerak lebih dulu
  di Maret dan kembali lebih akhir di musim gugur. Selama **1–3 minggu per tahun** kotak
  sesi kita meleset satu jam.
- Deteksi saat runtime: `(TimeCurrent() - TimeGMT()) / 3600`.
- **Peringatan kritis: di Strategy Tester, `TimeGMT()` sama dengan `TimeTradeServer()`,
  jadi ini mengembalikan 0 di backtest.** Offset harus disuplai terpisah untuk
  pengujian, atau backtest akan memperdagangkan jam yang berbeda dari live.
- Konvensi tanda adalah bug yang umum: sebagian input berarti server−GMT, sebagian
  GMT−server.

**Kodekan sesi terikat ke waktu lokal bursa (Europe/London, America/New_York), dikonversi
saat runtime — bukan sebagai UTC tetap atau jam server tetap.**

Bukti nyata dari data kita sendiri: **delapan dari dua belas bar M15 terbesar** di sampel
musim panas jatuh persis di **12:30 UTC**, bukan 13:30. EA yang meng-hardcode 13:30
salah waktu dari akhir Maret sampai akhir Oktober.

## 3. Bukti akademis tentang bentuk intraday emas

### Batten, Lucey, McGroarty, Peat & Urquhart (2017), PLoS ONE **(a)**

Thomson Reuters Tick History, **frekuensi 5 menit, 1 Mei 2000 – 30 Apr 2015**, emas /
perak / platinum / palladium. ~1,08 juta observasi emas.

Untuk emas secara khusus:

- **Jumlah transaksi:** kurva-U terbalik, memuncak **11:00–17:00 GMT** (Eropa 09:00–17:00
  bertumpang tindih dengan Amerika Utara 15:00–20:00). Jumlah transaksi per bar 5 menit
  naik dari 2,28 (2000–05) ke 47,06 (2010–15).
- **Volatilitas:** *"cukup konstan sampai 12:00 GMT, lalu naik sedikit sampai 14:00 GMT.
  Setelah titik ini, volatilitas menurun dan mendatar sampai akhir hari."*
- **Bid-ask spread:** cukup konstan sepanjang hari, dengan kenaikan kecil sekitar
  **22:00 GMT**, bertepatan dengan penutupan harian 21:00–22:00 GMT. Rata-rata BAS turun
  dari 0,00185 (2000–05) ke 0,000600 (2010–15).

**Perhatikan apa yang dikatakan dan tidak dikatakan ini: puncak volatilitas ada di
sekitar 14:00 GMT, bukan di open London.** Klaim bahwa open London adalah jam paling
bervolatilitas untuk emas **tidak didukung** dataset ini.

### Iwatsubo, Watkins & Xu (2018) **(a)**

Data 1 menit, TOCOM + COMEX, 128 hari perdagangan Sep 2014 – Mar 2015. Dua temuan:

1. **TI1 (open Tokyo) adalah "periode paling tidak efisien secara informasional dari
   seluruh jam perdagangan global"** untuk emas di kedua bursa. Puncak inefisiensi kedua
   di **TI4 — open London**. Open New York (TI7) **relatif efisien**.
2. **Berbeda dari saham, bid-ask spread RENDAH saat volatilitas TINGGI.**

**Peringatan metodologis penting.** Metrik efisiensinya adalah
`VR = |1 − Var[r(5min)] / (5 · Var[r(1min)])|` — **nilai absolut** penyimpangan dari
variance ratio 1. Penulisnya eksplisit memakai nilai absolut "karena kami tertarik pada
penyimpangan dari random walk **ke arah mana pun**."

**Artinya makalah ini mengukur BESARAN perilaku non-random-walk, bukan ARAHNYA.** Ia
**tidak** menetapkan apakah emas tren atau mean-revert secara intraday. Siapa pun yang
mengutipnya untuk membenarkan breakout sesi Asia *atau* fading sesi Asia salah baca.

Yang paling mendekati arah adalah analisis korelasinya: selama **sesi siang New York**,
efisiensi berkorelasi **positif dan signifikan** dengan volatilitas dan volume — konsisten
dengan perdagangan *terinformasi*. Selama **sesi siang Tokyo**, efisiensi berkorelasi
**negatif** dengan volatilitas — konsisten dengan perdagangan *tak terinformasi* yang
membangkitkan volatilitas.

### Sobti — waktu lompatan harga emas **(a)**

COMEX emas + GLD, 5 menit, **1 Jan 2010 – 31 Mar 2018**, dibatasi 07:30–16:00 ET.
Deteksi lompatan Andersen/Bollerslev dengan koreksi periodisitas Boudt et al., plus
robustness Lee-Mykland.

- **1.101 lompatan COMEX; P(hari dengan lompatan) = 32,15%**
- **562 negatif vs 539 positif — emas jatuh lebih sering daripada melonjak**
- Berita makro AS terjadwal menyebabkan **18–25%** lompatan COMEX; FOMC adalah
  peristiwa paling dominan menurut LASSO
- Distribusi per jam: **17% lompatan di 08:00–09:00 ET**, puncak kedua **16% di
  14:00–15:00 ET**, palung ~9% di 13:00 ET
- Pasca-lompatan: varians terealisasi normal kembali **10–15 menit** sesudahnya;
  illiquidity Amihud **15–20 menit**, tapi **30–35 menit setelah lompatan negatif**;
  effective spread melebar 15–25 menit **sebelum** lompatan
- **Hasil negatif yang perlu dicatat:** jam 10:00 ET, yang memuat lelang LBMA sore,
  **tidak** menunjukkan klaster lompatan (~105 lompatan, sejajar dengan tetangganya).
  Itu **bertentangan** dengan klaim praktisi bahwa PM fix adalah katalis intraday utama.

Data kita sendiri sejalan secara arah — 14:00 UTC memang elevated (1,58×) tapi di bawah
12:00–13:00 UTC.

## 4. Jendela berita

**Blokir keras ±15 menit di sekitar rilis AS berdampak tinggi.** Ini konsensus praktisi
universal, dan mekanismenya tidak ambigu meski jendela persisnya arbitrer: spread 30–50
pts dan slippage 15+ pts membuat konstanta friksi $0,22 kita menjadi $0,60–1,00, yang
membalik ekspektasi setup M5 mana pun menjadi negatif terlepas dari arahnya.

Set peristiwa yang diidentifikasi Sobti: **NFP, CPI, FOMC, PPI, retail sales, consumer
confidence, PMI, jobless claims.**

Dan penyerapannya cepat: Smales menemukan di pasar lain bahwa *"penyesuaian terhadap
informasi baru terjadi cepat dengan **bagian signifikan dari reaksi selesai dalam 30
detik**."* Saat feed sentimen kita diparsing, diskor, dan dikirim, pergerakannya sudah
terjadi. Kita tidak balapan dengan EA ritel lain; kita balapan dengan sistem colocated
yang membaca kabel yang sama.

**Untuk kode:** `economic_calendar.py` kita saat ini selalu mengembalikan
`blackout: False`. Sampai jadi nyata, **hardcode tabel UTC statis**. Stub yang selalu
bilang "aman" lebih buruk daripada tidak ada pemeriksaan sama sekali, karena terbaca
sebagai kontrol saat code review.

## 5. Waktu penahanan posisi — data regulator

Dari analisis intervensi produk ESMA, Annex C. Data CySEC dikumpulkan dari **11 firma**,
2 Jan – 15 Mei 2017, waktu penahanan dalam **jam**:

| Kelas aset | Kuartil 1 | Median | Kuartil 3 | Rata-rata |
|---|---|---|---|---|
| Mata uang mayor | 0–1 | 1–2 | 4–8 | 14,82 |
| **Komoditas** | **0–1** | **2–4** | **8–24** | **19,36** |
| Indeks mayor | 0–1 | 1–2 | 8–24 | 17 |
| Saham individual | 0–1 | 4–8 | 48–96 | 46,65 |

**Rata-rata jauh melebihi median di setiap kelas** karena distribusinya di-skew oleh ekor
posisi yang ditahan terlalu lama. **Ekor itu adalah populasi yang menolak merealisasi
kerugian.** Time stop kita ada untuk menjaga kita keluar dari ekor itu.

Proporsi posisi yang masih terbuka setelah waktu tertentu, dari satu penyedia UK besar:

| Kelas aset | 5 menit | 1 jam | 1 hari | 1 minggu |
|---|---|---|---|---|
| Komoditas | 91% | **64%** | 23% | 8% |
| FX | 84% | **50%** | 13% | 4% |

**Separuh posisi FX ritel ditutup dalam satu jam.** Snapshot "% klien net long" karenanya
mengukur populasi yang berputar cepat secara intraday.

**Rekomendasi konkret:** `basket_max_age = 4 jam` untuk emas intraday, plus paksa flat
sebelum rollover harian dan sebelum rilis berdampak tinggi terjadwal.

---

## Apa yang harus dikodekan

1. Jendela sesi **terikat waktu lokal bursa**, bukan UTC tetap atau jam server tetap.
2. Deteksi DST saat runtime; **suplai offset terpisah untuk Strategy Tester.**
3. Blokir keras 21:00–23:00 UTC dan ±15 menit sekitar rilis AS berdampak tinggi.
4. Jendela perdagangan utama **11:00–17:00 UTC**, idealnya dipersempit ke 12:00–17:00.
5. Jeda entry 2 menit di lelang LBMA (10:30 dan 15:00 London).
6. Time barrier per basket — 4 jam untuk strategi M15, jauh lebih pendek untuk scalping.
7. Perlakukan jam pasca-London (17:00–21:00) sebagai zona di mana setup kontinuasi harus
   **dimatikan**, bukan sekadar dikurangi ukurannya.

---

## Referensi

**Tingkat (a) — kuantitatif, metodologi terbuka**

- Batten, J., Lucey, B., McGroarty, F., Peat, M. & Urquhart, A. (2017). *Stylized facts
  of intraday precious metals.* *PLoS ONE* 12(4): e0174232.
  https://pmc.ncbi.nlm.nih.gov/articles/PMC5407636/
- Iwatsubo, K., Watkins, C. & Xu, T. (2018). *Intraday seasonality in efficiency,
  liquidity, volatility and volume.* *Journal of Commodity Markets* 11, 59–71.
  https://doi.org/10.1016/j.jcomm.2018.05.001 ·
  [`../pdf/iwatsubo-watkins-xu-2018-intraday-seasonality-gold-tokyo-new-york.pdf`](../pdf/iwatsubo-watkins-xu-2018-intraday-seasonality-gold-tokyo-new-york.pdf)
- Sobti, N. (2025). *What triggers intraday price jumps and co-jumps in gold?*
  *International Review of Financial Analysis*.
  https://doi.org/10.1016/j.irfa.2025.104380 · versi EFMA 2024:
  http://www.efmaefm.org/0EFMAMEETINGS/EFMA%20ANNUAL%20MEETINGS/2024-Lisbon/papers/EFMA_COMPLETEMANU_NSOBTI.pdf ·
  [`../pdf/sobti-2024-what-causes-intraday-price-jumps-in-gold.pdf`](../pdf/sobti-2024-what-causes-intraday-price-jumps-in-gold.pdf)
- ESMA (2018). *Product Intervention Analysis — Measures on Contracts for Differences*,
  ESMA50-162-215, Annex C (tabel waktu penahanan).
  https://www.esma.europa.eu/sites/default/files/library/esma50-162-215_product_intervention_analysis_cfds.pdf ·
  ekstrak lokal: [`../pdf/extract-esma.txt`](../pdf/extract-esma.txt)
- ICE Benchmark Administration, waktu lelang LBMA (10:30 dan 15:00 London) —
  https://www.ice.com/iba/lbma-gold-price ·
  https://www.lbma.org.uk/prices-and-data/lbma-gold-price/lbma-gold-price

**Spesifikasi platform (a)**

- MQL5, *Server time, DST, and the Strategy Tester* —
  https://www.mql5.com/en/blogs/post/774934

**Tingkat (b) — konsensus praktisi tanpa data**

- Traders Mastermind, jam perdagangan emas dan COMEX/London fix —
  https://tradersmastermind.com/gold-trading-hours-comex-london-fix/
- TMGM — https://www.tmgm.com/en/academy/trading-academy/gold-trading-hours
- EBC — https://www.ebc.com/forex/what-are-the-best-xauusd-trading-hours
- Vantage — https://www.vantagemarkets.com/en/academy/best-time-to-trade-gold-xau-usd/
- FXNX, aturan 15 menit untuk berita —
  https://fxnx.com/en/blog/mastering-xauusd-news-15-minute-rule-cpi-nfp

**Yang harus dibuang (c)**

- tradingonmt5.com, *XAUUSD session playbook* —
  https://www.tradingonmt5.com/xauusd-session-playbook — urutan sesinya masuk akal dan
  cocok dengan sumber lain, tapi angka rentangnya meleset ~10× untuk emas di $4.300.
  **Rasio antar sesinya informatif; nilai absolutnya tidak.** Ini pola umum di konten
  emas: struktur relatif sering benar, angka absolut hampir tidak pernah.
