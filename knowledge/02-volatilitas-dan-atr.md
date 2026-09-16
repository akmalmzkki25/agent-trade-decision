# Volatilitas dan ATR

Sebagian besar isi file ini adalah **pengukuran langsung**, bukan kutipan literatur —
karena tidak ada tabel ATR XAUUSD per jam yang kredibel pernah dipublikasikan di mana
pun. Diukur pada **4.500 bar M15** (8 Jul – 16 Sep 2026) dan **2.513 bar harian**
(Sep 2016 – Sep 2026) futures emas COMEX (`GC=F`).

> **Catatan penting:** ini futures, bukan feed spot CFD broker. Bentuknya bisa
> dipindahkan (spot diarbitrase terhadap COMEX), tapi jam dan spread-nya tidak. Dan
> sampel M15-nya hanya satu rezim 70 hari bervolatilitas tinggi. Ukur ulang di feed
> sendiri.

---

## 1. Temuan utama: ATR(14) di M15 adalah estimator yang salah

Rentang M15 sebenarnya membentang **2,91×** sepanjang hari. Pembacaan ATR(14) hanya
membentang **1,62×**, karena lookback 14 bar di M15 adalah 3,5 jam — ia mengaburkan
setiap batas sesi.

Biasnya **sistematis** dan arahnya **persis terbalik**:

| UTC | ATR(14) baca | True range aktual | Rasio | Akibatnya |
|---|---|---|---|---|
| 00:00 | $7,28 | $10,06 | **0,72** | Terlalu rapat |
| 01:00 | $8,21 | $11,82 | **0,69** | Terlalu rapat (pagi Shanghai) |
| 04:00 | $8,02 | $5,55 | **1,44** | Terlalu lebar |
| 08:00 | $7,99 | $8,06 | 0,99 | Kebetulan pas |
| **12:00** | **$7,94** | **$14,04** | **0,57** | **Paling rapat saat pasar paling bergerak** |
| **13:00** | $9,70 | $14,80 | **0,66** | Terlalu rapat di open NY |
| 16:00 | $11,03 | $8,42 | 1,31 | Terlalu lebar |
| 17:00 | $10,24 | $7,23 | 1,42 | Terlalu lebar |
| **20:00** | $8,28 | $5,08 | **1,63** | **Paling lebar saat pasar paling sepi** |

**Sebaran kalibrasi antar jam: 2,85×.**

### Perbaikannya

Ganti ATR(14) dengan rata-rata true range **slot 15 menit yang sama** selama ~10 sesi
terakhir.

| Estimator | Sebaran kalibrasi antar jam |
|---|---|
| ATR(14) standar di M15 | **2,85×** |
| ATR waktu-hari (slot yang sama, 10 sesi) | **1,08×** |

Error absolut rata-rata per bar hanya membaik **9,5%** — sebagian besar varians rentang
M15 adalah noise harian, bukan musiman. Tapi **itu justru intinya**: noise saling
meniadakan antar trade; bias menumpuk di setiap trade yang diambil pada jam tertentu.

Dukungan independen untuk desain ini: makalah momentum intraday Zarattini/Barbon/Aziz
membangun batas entry-nya dari **profil volatilitas per-menit empiris atas lookback 14
hari**, secara eksplisit alih-alih memakai scaling √t.

## 2. Nyatakan ambang sebagai persen harga, bukan dolar

ATR harian emas sebagai % harga, Wilder ATR(14) pada bar harian COMEX GC:

| Tahun | Vol tahunan c-t-c | **ATR harian sbg % harga** | ATR/σ_harian | Harga rata-rata |
|---|---|---|---|---|
| 2016 | 14,8% | 1,08% | 1,16 | $1.231 |
| 2017 | 10,4% | 0,85% | 1,30 | $1.258 |
| 2018 | 10,5% | **0,75%** | 1,14 | $1.268 |
| 2019 | 11,5% | 0,88% | 1,21 | $1.393 |
| 2020 | 21,6% | 1,80% | 1,32 | $1.777 |
| 2021 | 15,0% | 1,42% | 1,50 | $1.799 |
| 2022 | 15,7% | 1,55% | 1,57 | $1.805 |
| 2023 | 13,4% | 1,31% | 1,55 | $1.955 |
| 2024 | 15,3% | 1,48% | 1,53 | $2.405 |
| 2025 | 20,9% | 1,92% | 1,46 | $3.466 |
| **2026** | **30,9%** | **2,98%** | 1,53 | $4.580 |

Pembacaan saat ini, 16 Sep 2026: **ATR(14) harian = $108,28** pada close $4.373,40 =
**2,48% harga**. 60 hari perdagangan terakhir menahunkan ke 22,7%, jadi volatilitas
sedang mendingin dari lonjakan Juni.

**Dua konstanta yang tahan lama jatuh dari tabel ini:**

1. **`ATR_harian ÷ σ_harian ≈ 1,15–1,57, terpusat ~1,4.`** Stabil selama 11 tahun dan
   perubahan harga 3,7×. Teori memberi √(8/π) ≈ 1,60 untuk GBM tanpa drift yang dipantau
   kontinu; kekurangan empirisnya adalah efek Jensen dari volatility clustering. **Ini
   konversi dari angka volatilitas tahunan mana pun ke ATR dolar.**
2. **ATR harian rezim normal adalah 0,75–1,9% harga. 2026 di ~2,5–3,0% adalah outlier
   ~2×.** Setiap stop dolar yang dikalibrasi hari ini akan kira-kira dua kali lipat
   terlalu lebar setelah reversi — dan dengan paruh waktu 1,6 bulan, reversi itu soal
   bulan, bukan tahun.

## 3. ATR M15 terukur

Wilder ATR(14) pada M15, 4.485 pembacaan, Jul–Sep 2026 di ~$4.311:

| | $ | % harga |
|---|---|---|
| Persentil 10 | $6,24 | 0,145% |
| Persentil 25 | $6,99 | 0,162% |
| **Median** | **$8,12** | **0,188%** |
| Persentil 75 | $9,62 | 0,223% |
| Persentil 90 | $11,84 | 0,275% |
| Persentil 99 | $15,77 | 0,366% |
| Min / Maks | $4,41 / $22,67 | |

**Jadi: ~$8, atau ~800 points, atau ~0,19% harga.** Pakai persentasenya; itu bagian yang
bertahan saat harga atau rezim volatilitas berubah.

## 4. Scaling √t: benar rata-rata, salah di setiap jam

√t naif dari σ harian 30%-tahunan atas 96 bar memberi σ_15m ≈ $8,3 → ATR_15m ≈ $10–13.
Median terukur $8,12, rata-rata $8,61.

**Jadi √t kira-kira benar secara rata-rata dan sepenuhnya salah di setiap jam
individual**, karena ia mengasumsikan profil datar melawan kurva-U terukur 2,91×.

## 5. Bentuk volatilitas intraday emas

### Terukur (2026)

Rentang M15 rata-rata per jam, 4.500 bar:

| UTC | Rentang M15 rata-rata | vs keseluruhan |
|---|---|---|
| 00:00 | $10,02 | 1,17× |
| **01:00** | **$11,81** | **1,37×** |
| 03:00 | $6,55 | 0,76× |
| **04:00** | **$5,53** | **0,64×** |
| 08:00 | $8,05 | 0,94× |
| 10:00 | $6,50 | 0,76× |
| **12:00** | **$14,04** | **1,63×** |
| **13:00** | **$14,79** | **1,72×** |
| 14:00 | $13,56 | 1,58× |
| 16:00 | $8,42 | 0,98× |
| **20:00** | **$5,07** | **0,59×** |
| 23:00 | $5,98 | 0,70× |

**Puncak ke lembah 2,91×.** Pada resolusi slot 15 menit sebarannya melebar ke **3,7×**
(median 13:30 UTC $14,90 vs 20:15/20:30 UTC $4,00). Bertahan setelah membuang 1% bar
teratas (sebaran turun hanya ke 2,75×), jadi bukan artefak lompatan.

### Dipublikasikan (2014–15) — dan bentuknya sama

Iwatsubo, Watkins & Xu **(a)**, data mid-quote 1 menit COMEX + TOCOM, 128 hari
perdagangan Sep 2014 – Mar 2015. Membaca Figure 2.2 untuk emas COMEX:

| Interval | GMT (musim dingin) | Vol | vs rata-rata |
|---|---|---|---|
| TI1 open Tokyo | 00:00–02:04 | 1,20 | 1,30× |
| TI2 | 02:05–04:09 | **0,50** | **0,54×** |
| TI4 open London | 07:30–09:24 | 0,96 | 1,04× |
| TI6 | 11:20–13:14 | 1,05 | 1,14× |
| **TI7 pagi AS** | **13:15–15:09** | **1,33** | **1,44×** |
| TI9 | 17:05–18:59 | 0,81 | 0,88× |

**Puncak ke lembah 2,7× dalam volatilitas, ~7× dalam varians.** Pangsa varians: Tokyo
26%, London 31%, New York 43%.

**Replikasi independen 11 tahun kemudian, di 3,5× harga, dengan vendor data berbeda:
2,7× versus 2,91×.** Ini fakta struktural paling tahan lama di seluruh riset ini.

Studi PLoS ONE Batten et al. **(a)** (emas 5 menit, 2000–2015, ~1,08 juta observasi)
sampai pada bentuk yang sama: volatilitas *"cukup konstan sampai 12:00 GMT, lalu naik
sedikit sampai 14:00 GMT. Setelah itu menurun dan mendatar sampai akhir hari."*

**Ini bertentangan langsung dengan klaim blog broker bahwa "60–70% rentang harian
terbentuk selama overlap London–NY."** Emas **tidak** punya kurva-U dramatis ala saham.
Ia punya puncak tengah hari yang landai terpusat di 12:00–14:00 GMT — yaitu jendela
data AS dan open COMEX.

## 6. Bar 12:30 UTC butuh rezimnya sendiri

Bukan parameter — rezim.

| Statistik | Nilai |
|---|---|
| Median | $13,40 |
| **Rata-rata** | **$21,89** |
| Maksimum | **$104,50** |
| vs median keseluruhan | 1,86× di median, **14,5× di ekstrem** |
| Proporsi melampaui $25 | **20%** |

Rata-rata 63% di atas median: skew kanan ekstrem. Bar 14:00 UTC (10:00 ET / 15:00
London) pernah mencapai $93,40.

**Dan perhatikan jamnya:** 08:30 ET adalah 13:30 GMT hanya di musim dingin (EST); di
musim panas (EDT) itu **12:30 GMT**. Delapan dari dua belas bar M15 terbesar di sampel
musim panas jatuh persis di **12:30 UTC**. EA yang meng-hardcode 13:30 salah waktu dari
akhir Maret sampai akhir Oktober. → [03](03-sesi-dan-waktu.md)

## 7. Asia tidak seragam sepi

Ini koreksi terhadap kebiasaan umum mendefinisikan "range Asia" sebagai 00:00–07:00 UTC.

- **00:00–02:00 UTC: 1,17–1,37×** rata-rata (pagi Shanghai) — blok aktif
- **03:00–05:00 UTC: 0,64–0,76×** — jeda yang sesungguhnya

Definisi 00:00–07:00 UTC **mencampur blok aktif dengan blok mati**.

Rentang terukur, 47 hari Jul–Sep 2026:

| | Median | Rata-rata |
|---|---|---|
| Rentang Asia (00:00–07:00 UTC) | **$47,70 = 1,12% harga** | $50,81 |
| London+NY (07:00–20:00 UTC) | $65,90 | $76,74 |
| Seluruh 00:00–20:00 UTC | $90,30 | $99,05 |

**Rasio Asia / London-NY: median 0,699** (kuartil 0,52 / 0,70 / 0,94). Rentang Asia
adalah **54%** dari rentang seluruh hari. Efek hari-dalam-minggu remeh dibandingkan itu
(Senin $8,34 sampai Jumat $9,02 rata-rata rentang M15, sebaran ~8%).

## 8. Rezim volatilitas 2026

World Gold Council **(a)**, ukuran: volatilitas terealisasi 30 hari dari return harian,
ditahunkan:

- **Rata-rata 20 tahun: 17%**; historis "umumnya antara 10% dan 18%"
- 2026 memuncak **di atas 50%**, sekarang **di bawah 30%**, di **persentil 5 teratas
  sejak 1971**
- **Paruh waktu volatilitas ≈ 1,6 bulan**, mean-reverting; WGC secara eksplisit
  menyatakan tidak ada pergeseran struktural
- Jalur harga 2026: puncak intraday **$5.595,47 pada 29 Jan**, terendah intraday
  **$3.959,33 pada 24 Jun**, 12 rekor tertinggi sepanjang masa di H1

**Kita memperdagangkan emas dalam rezim yang berjalan kira-kira 1,5–3× volatilitas
jangka panjangnya.** Setiap sumber yang mengutip emas di $1.800–2.400 berasal dari
2020–2024 dan angka *dolar absolutnya* salah sekitar 2× pada harga, lebih dari itu pada
volatilitas. Konversikan semuanya ke % harga.

## 9. Spread emas rendah saat volatilitas tinggi

Berlawanan dengan saham. Iwatsubo, Watkins & Xu **(a)** menemukan dua hal:

1. Emas di Tokyo didominasi perdagangan **tak terinformasi**; New York menunjukkan
   keduanya.
2. **Bid-ask spread RENDAH saat volatilitas TINGGI** — kebalikan dari ekuitas.

Poin kedua yang operasional: untuk emas, **biaya per satuan volatilitas paling baik
selama jendela NY aktif dan paling buruk selama jam Asia yang sepi.** Digabung dengan
lantai biaya di [01](01-biaya-dan-kelayakan-timeframe.md), itu argumen berbasis bukti
untuk membatasi EA emas ke kira-kira **12:00–17:00 GMT** — bukan karena volatilitasnya
lebih tinggi (itu memotong dua arah) tapi karena **hambatan biaya sebagai fraksi R
paling rendah di sana**, dan di situlah perdagangan terinformasi hidup.

---

## Apa yang harus dikodekan

1. **Ganti ATR(14) M15** dengan true range slot-jam-yang-sama, 10 sesi terakhir.
2. **Nyatakan setiap ambang sebagai % harga**, konversi ke dolar saat runtime memakai
   `ATR_harian ≈ 1,4 × σ_harian`.
3. **Perlakukan 12:30 UTC (musim panas) / 13:30 UTC (musim dingin) sebagai rezim
   terpisah**, bukan sebagai parameter.
4. **Jangan definisikan range Asia sebagai 00:00–07:00 UTC.** Kalau butuh jendela sepi,
   pakai 03:00–05:00 UTC.
5. **Rekalibrasi ulang setiap kuartal.** Rezim 2026 adalah outlier 2× dengan paruh waktu
   1,6 bulan.

---

## Referensi

**Pengukuran langsung**

Futures emas COMEX (`GC=F`, kontrak Des-2026) via API chart publik Yahoo. 2.513 bar
harian Sep 2016 – Sep 2026; 4.500 bar M15 8 Jul – 16 Sep 2026. Semua perhitungan ATR
memakai smoothing Wilder, periode 14.

**Tingkat (a) — kuantitatif, metodologi terbuka**

- Iwatsubo, K., Watkins, C. & Xu, T. (2018). *Intraday seasonality in efficiency,
  liquidity, volatility and volume: Platinum and gold futures in Tokyo and New York.*
  *Journal of Commodity Markets* 11, 59–71. https://doi.org/10.1016/j.jcomm.2018.05.001 ·
  preprint OA (Kobe DP 1722): https://da.lib.kobe-u.ac.jp/da/kernel/81009847/81009847.pdf ·
  salinan lokal: [`../pdf/iwatsubo-watkins-xu-2018-intraday-seasonality-gold-tokyo-new-york.pdf`](../pdf/iwatsubo-watkins-xu-2018-intraday-seasonality-gold-tokyo-new-york.pdf)
- Batten, J., Lucey, B., McGroarty, F., Peat, M. & Urquhart, A. (2017). *Stylized facts
  of intraday precious metals.* *PLoS ONE* 12(4): e0174232.
  https://pmc.ncbi.nlm.nih.gov/articles/PMC5407636/
- World Gold Council. *Gold Mid-Year Outlook 2026.*
  https://www.gold.org/goldhub/research/gold-mid-year-outlook-2026
- World Gold Council (2026). *You asked, we answered: has gold's performance
  structurally changed?*
  https://www.gold.org/goldhub/gold-focus/2026/04/you-asked-we-answered-has-golds-performance-structurally-changed
- Zarattini, C., Barbon, A. & Aziz, A. *Beat the Market: An Effective Intraday Momentum
  Strategy for S&P500 ETF (SPY).* SSRN 4824172 — https://ssrn.com/abstract=4824172 ·
  SFI WP 24-97: https://www.sfi.ch/en/publications/n-24-97-beat-the-market-an-effective-intraday-momentum-strategy-for-s-p500-etf-spy

**Spesifikasi platform (a)**

- MQL5, *Server time and DST* — https://www.mql5.com/en/blogs/post/774934

**Yang harus dibuang (c)**

- Setiap tabel "ATR emas" yang mengutip "ATR M15 $12 sementara harian $45" —
  mustahil secara aritmetika; 96 bar 15 menit masing-masing $12 tidak bisa menghasilkan
  rentang harian $45.
- Pro-Scalper, *ATR for gold trading* — https://www.pro-scalper.com/indicators/atr-gold-trading
  menyatakan "$10 per pip untuk 0,01 lot XAUUSD", salah faktor 100. Saat aritmetika
  satuannya tidak konsisten secara internal, angkanya dibangkitkan, bukan diukur.
