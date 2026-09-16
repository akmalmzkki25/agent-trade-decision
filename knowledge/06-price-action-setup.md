# Price action: setup yang bisa dikodekan

Empat setup. Level `L` selalu datang dari timeframe lebih tinggi (swing M15/H1, high-low
hari sebelumnya, ekstrem sesi) — tidak pernah dari chart tempat kita entry. Semua
evaluasi hanya pada **bar yang sudah close**. Arah dibalik untuk sell.

Konstanta friksi `c = $0,22` (raw). Stop minimum `R_min = $2,20`, lantai desain `$6,00`.

---

## Prioritas

1. **Displacement bar di level M15** — nilai harapan tertinggi
2. **Session opening range break** — bukti terbaik
3. **Retest continuation** — hanya di jendela sempit
4. **Engulfing di level** — dengan filter ukuran dan lokasi

Semua yang lain adalah filter konteks atau kondisi veto, **bukan entry**.

---

## 1. Displacement / momentum bar **(b)**

Yang akan saya prioritaskan. Ini pada dasarnya taruhan "dikondisikan pada pergeseran
rezim volatilitas, momentum berlanjut" — dan itu satu-satunya hal yang didukung
literatur ORB.

**Mengapa yang ini:** ekspansi volatilitas adalah variabel keadaan yang terukur dan
persisten. *Bentuk* candle bukan.

| Elemen | Aturan |
|---|---|
| **Konteks** | M15 searah; harga menembus `L` |
| **Trigger** (close bar `i` di M5) | `body[i] ≥ 0,70 × range[i]`; `range[i] ≥ 2,0 × ATR(14, M5)`; `close[i]` melewati `L`; z-score tick-volume bar `i` ≥ **+1,5** terhadap jendela bergulir 100 bar di feed sendiri |
| **Entry** | Limit di retracement **50% badan bar `i`**, berlaku 3 bar |
| **Stop** | Melewati ekstrem asal bar `i`, atau `1,0 × ATR(14, M5)` — ambil yang lebih lebar |
| **Target** | Parsial pertama di `1,0 × range[i]` diukur dari `L`; runner ke level M15 berikutnya |
| **Batal** | Close kembali ke dalam `L`; limit tak terisi setelah 3 bar; bar ≥1,5×ATR close melawan |

**Kenapa limit, bukan market:** masuk at-market pada close bar 2×ATR berarti membayar
seluruh pergerakannya. Dan temuan dari catatan perdagangan lengkap Taiwan 1992–2006
**(a)**: *"praktis seluruh kerugian investor individu dapat ditelusuri ke order agresif
mereka."*

## 2. Opening range break **(a)** — keluarga dengan bukti terbaik

Dataset dengan metodologi terbuka terkuat yang ditemukan: futures ES dan NQ, bar 1 menit,
2 Jan 2014 – 26 Jan 2026, **6.142+ hari perdagangan**, RTH saja.

| Konfigurasi | Kontinuasi (wick) | (close 1 menit) | (close 5 menit) |
|---|---|---|---|
| ES 5-menit OR | 58,6% | 60,2% | 63,3% |
| ES 30-menit OR | 64,6% | 68,4% | **70,7%** |
| NQ 30-menit OR | 67,0% | 68,7% | **71,5%** |

**Temuan yang bisa dipindahkan langsung:**

- **Metode konfirmasi penting dan bernilai 4–6 poin persentase.** Sentuhan wick <
  close 1 menit < close 5 menit. **Kodekan konfirmasi berbasis close.**
- **Lebar range adalah filter tunggal terbaik.** ES 30-menit OR: sempit (<0,3×ATR) →
  kontinuasi 62,9% dan double-break 53,4%; **lebar (>0,6×ATR) → kontinuasi 77,5% dan
  double-break hanya 20,8%.** OR lebar hanya 4,3% hari.
  **Ini membalik klaim populer "range ketat = breakout eksplosif".**
- **Tangga ekstensi (ES 5-menit OR, by close):** 0,5× range tercapai 76,2% naik / 73,7%
  turun; 1,0× tercapai 64,3% / 62,9%; 2,0× tercapai 43,4% / 43,7%; 3,0× tercapai 27,4% /
  30,5%. **Pakai ini untuk menentukan ukuran target alih-alih menebak kelipatan R.**
- **Break yang gagal berkaskade.** **79,6–99,5% break ORB yang gagal berlanjut menembus
  batas sebaliknya.** Stop-and-reverse didukung secara statistik; "tunggu sampai kembali"
  tidak.
- **Profil risikonya lebih buruk daripada kelihatannya.** ES 30-menit OR dengan
  konfirmasi close 5 menit: **median MFE 5,0 poin vs median MAE 14,0 poin — MFE:MAE =
  0,36.** Win rate-nya tinggi dan profil eksursinya jelek. **Penempatan stop, bukan entry,
  yang menentukan hasil strategi ini.**
- **Kecepatan break:** ES 5-menit OR, 88,3% break dalam 5 menit; ES 30-menit OR, 52,7%
  dalam 5 menit, 74,0% dalam 15 menit. **Kodekan time-out pada pending order.**

**Korroborasi independen di sisi kegagalan:** 240.102 trade ORB di 600+ simbol ekuitas —
**65,9% breakout kena stop pada setelan default** (5 menit 54,8% gagal, 15 menit 68,8%,
30 menit 75,8%).

Perhatikan kontradiksi yang tampak dengan angka kontinuasi di atas: **kontinuasi-ke-close
dan bertahan-dari-stop adalah pertanyaan berbeda. Keduanya benar.** Ini pelajaran praktis
paling penting: **tingkat kena arah yang tinggi hidup berdampingan dengan tingkat
stop-out yang tinggi, dan selisihnya sepenuhnya soal di mana stop ditaruh.**

### Adaptasi untuk emas

Tidak ada yang pernah menerbitkan statistik ORB pada XAUUSD. Jangkar relevan untuk emas
adalah **open London dan open NY/COMEX**. Kodekan jendela OR pada open *sesi*,
kondisikan pada lebar OR vs ATR, tuntut konfirmasi close 5 menit, dan harapkan persentase
ES/NQ menurun — emas bukan indeks ekuitas dengan lelang pembukaan keras di 09:30.

### Peringatan keras tentang literatur ORB

Hasil terkenal Zarattini/Barbon/Aziz **tidak** bereplikasi bersih:

- Klaim asli: QQQ 2016–2023, 675% total, 31% tahunan, Sharpe 1,12, alpha 33%. **1.795
  trade, 51% long / 49% short, win rate 24%, +0,13R rata-rata.**
- Replikasi independen QQQ 2010–2026: **full-sample Sharpe −0,06**, in-sample 0,16,
  out-of-sample (11 bulan pasca-publikasi) **−0,84**.
- Makalah QQQ mengasumsikan **slippage nol** melawan stop $0,08.
- Di teks lengkapnya: kata **"drawdown" muncul nol kali**, **"out-of-sample" nol kali**,
  **"robust" nol kali**. Tidak ada max drawdown dilaporkan untuk varian mana pun.
- Hasil "stop = 5% dari ATR 14-hari" adalah **sapuan sensitivitas**, bukan strategi
  utamanya — dan itu solusi pojok dari sweep parameter 2-D in-sample. Contoh mereka
  sendiri: ATR 14-hari TQQQ $1,60 pada saham $25 → stop **$0,08**. Mereka sendiri
  mengakui itu "bisa dianggap tidak realistis karena model kami mengasumsikan tidak ada
  slippage."
- Untuk replikasi stocks-in-play: net PnL turun dari $138.639 (slippage nol) ke **$4.860
  pada $0,02/saham**, impas di **~2,2¢/saham** melawan spread ~1¢, dan **76% profit
  strategi tersaring datang dari 2022 saja**.

**Baca makalah ORB sebagai hipotesis, bukan sistem tervalidasi.**

Dan dua makalah SSRN 2026 yang tampaknya menyelesaikan pertanyaan ini terblokir dan
layak diambil manual:
- Fetna, *Opening-Range Breakout Does Not Survive Trading Costs: A Pre-Registered
  225-Cell Study on Sixteen Years of Futures Data*, DOI 10.2139/ssrn.7428398
- Mahadzva, *Session Microstructure and the Momentum Mirage*, DOI 10.2139/ssrn.7435081

## 3. Break and retest **(a)** — setup yang sama membalik tanda

Data terbaik: studi Initial Balance retest, NQ dan ES, bar 1 menit, Feb 2014 – Mei 2026,
~3.000 hari-break per instrumen. IB = 09:30–10:30 ET; break = candle 1 menit pertama
setelah 10:30 yang wick-nya menembus IB high/low.

- **IB menembus 96–98% waktu.** "Apakah ia menembus" tidak membawa informasi apa pun.
- **Probabilitas harga kembali ke level yang ditembus, menurut ekstensi yang dicapai:**

| Ekstensi | NQ | ES |
|---|---|---|
| 1,1× | 87% | 90% |
| 1,2× | 75% | 81% |
| 1,3× | 63% | 70% |
| 1,4× | 53% | 61% |
| 1,5× | 45% | 53% |

- **Tiga hasil pada ekstensi 1,2× (NQ / ES):** lari 26% / —; **retest lalu lanjut 52% /
  60%**; retest lalu berbalik 22% / —.
- **Titik silang di mana "berbalik" melampaui "lanjut": ≈1,40× di NQ, ≈1,50× di ES.**
  **Melewati ekstensi itu, memudarkan retest adalah sisi yang lebih baik.**
- **Waktu hari mendominasi (NQ pada 1,2×):** 10:30–12:00 → 58% lanjut / 20% berbalik.
  12:00–14:00 → 44% / 26%. **14:00–16:00 → 23% lanjut / 32% berbalik, 45% tidak pernah
  retest.** **Setup yang sama membalik tanda sepanjang sesi.**
- **Pengondisian kecepatan:** ekstensi cepat lanjut **62%**; grind lambat **42%**.
- **Ukuran kaki kedua:** median ekstensi tambahan 0,45R (NQ) / 0,56R (ES); ~20%
  menghasilkan satu range penuh lagi.
- **Waktu retest:** median 5 menit pada ekstensi 1,1×, 55 menit pada 1,5×.

**Yang bisa dikodekan:** ambil entry retest-continuation **hanya** ketika ekstensi antara
**~1,0× dan ~1,35×**, pergerakan ke ekstensi itu **cepat**, dan kita berada di
**sepertiga pertama sesi**. Di luar itu, ambil sisi sebaliknya atau diam.

| Elemen | Aturan |
|---|---|
| **Entry (agresif)** | Close bar rejection yang wick-nya masuk ke level yang di-retest lalu close searah |
| **Entry (konservatif)** | Bar berikutnya menembus ekstrem bar rejection — satu bar tunda |
| **Stop** | Melewati ekstrem retest, atau **1×ATR(14)** melewati ekstrem zona |
| **Batal** | Bar retest **close** menembus kembali level |

**Tidak ada yang menetapkan batas waktu** — berapa bar retest boleh berlangsung sebelum
setup kedaluwarsa. Itu parameter bebas dan permukaan overfitting klasik. **Tetapkan, dan
tetapkan sebelum melihat hasilnya.**

## 4. Engulfing di level **(b)**, dengan benang tipis **(a)**

| Elemen | Aturan |
|---|---|
| **Konteks** | Harga masuk ke `L` dari luar; `L` adalah level M15 ke atas |
| **Trigger** (close bar `i`) | `body[i] > body[i-1]`; `close[i]` melewati `open[i-1]` searah sinyal; `range[i] ≥ 1,3 × ATR(14, M5)`; `close[i]` di sisi yang benar dari `L`; low bar `i` (untuk long) dalam `0,5 × ATR` dari `L` |
| **Entry** | Stop order 1 tick melewati `high[i]` (long), berlaku 3 bar saja |
| **Stop** | `min(low[i], low[i-1]) − max(3 × spread, 0,15 × ATR(14, M5))` |
| **Gerbang ukuran** | Kalau jarak stop < $2,20, **lewati trade** — jangan perkecil risiko agar muat |
| **Batal** | Close M5 mana pun kembali melewati `open[i]` melawan kita; order entry tak terisi dalam 3 bar; `L` ditembus ke arah berlawanan |

**Peringatan penting:** studi yang mendukung engulfing mengukur terhadap **Open, High,
dan Low** — **bukan Close**. Kalau kita mengodekan uji berbasis close, kita mengodekan
kaki yang justru tidak bekerja. Lebih suka uji exit berbasis high/low.

## 5. Pin bar / rejection wick **(b)** — filter lokasi, bukan entry

**Aturan rasio standar:** rejection wick ≥ **66,7%** dari total rentang high-low; badan
≤ 33%; badan di sepertiga terjauh. Versi ketat: rasio wick-ke-badan **3:1**.

Semua **(b)**: tidak ada backtest di balik ambang 66,7%. Itu konvensi.

**Tambahan spesifik emas yang wajib:** `wick_length ≥ 8 × spread`. Di M1 emas, wick $0,10
dengan spread $0,12 bukan rejection, itu bid-ask. **Filter ini membunuh sebagian besar
pin bar M1 — dan itu hasil yang benar.**

**Mode kegagalan yang harus dikodekan-lawan:** emas menghasilkan wick yang "terlihat
seperti error di chart." Tuntut wick itu menembus level yang sudah diidentifikasi
sebelumnya dan close kembali di dalam. **Wick tanpa jangkar bukan setup.**

## 6. Liquidity sweep **(a)** — filter veto, bukan entry

| Elemen | Aturan |
|---|---|
| **Trigger** | Bar `i` menembus ekstrem swing atau ekstrem sesi sebelumnya sebesar ≥ `3 × spread` **dan close kembali di dalam**, dengan `wick_beyond_level ≥ 0,5 × range[i]` |
| **Entry** | Pada close bar **berikutnya** (`i+1`) yang mengonfirmasi arah — entry kedua, bukan pada bar sweep-nya |
| **Stop** | Melewati ekstrem sweep sebesar `max(3 × spread, 0,15 × ATR)` |
| **Batal** | Close melewati ekstrem sweep |

**Tingkat bukti:** konsepnya punya dukungan positif lemah tapi **tidak signifikan secara
statistik** — SPY +0,119% atas 5 hari, **t = 0,94**, n = 547; DIA/QQQ/IWM serupa; **31
dari 32 skor ICT gagal mencapai t = 2,0.** Klaim vendor "65–75% standalone, 80%+ dengan
konfluensi" tidak punya metodologi yang diungkap.

**Pakai sebagai filter veto** — "jangan long tepat setelah buy-side liquidity baru saja
diambil" — **bukan sebagai entry.**

## 7. Konfirmasi yang wajib untuk semua setup

1. **Bar tertutup.** Evaluasi hanya `rates[1]`.
2. **Close melewati level, bukan wick.** `|close − L| ≥ 3 × spread`.
3. **Wick ≥ 8 × spread** untuk setiap sinyal berbasis wick.
4. **Pivot butuh offset.** Swing point N-bar tidak terkonfirmasi sampai N bar kanan
   close. Di M15, N=5 berarti **lag 75 menit**.
5. **HTF hanya bar tertutup.** Memakai bar H4 yang sedang terbentuk membocorkan
   informasi masa depan ke setiap keputusan M15 di dalamnya — penyebab paling umum
   backtest multi-timeframe terlihat bagus lalu mati live.
6. **Gerbang ukuran, bukan penyesuaian stop.** Kalau stop struktural lebih rapat dari
   lantai biaya, **lewati trade**, jangan rapatkan stop.

## 8. Alur multi-timeframe

Alur standar (diulang universal, **tidak bersumber di mana pun**): H4/H1 bias → struktur
M15 → trigger M5/M1.

Bias HTF didefinisikan bermacam-macam: (i) struktur swing H4 HH/HL vs LH/LL, (ii) **EMA
50 di atas/bawah EMA 200 di H4**, (iii) premium/discount relatif terhadap dealing range
HTF terakhir. **Tidak ada sumber yang menawarkan tiebreak saat H4 dan H1 bertentangan
satu sama lain** — yang justru kasus yang benar-benar terjadi.

**Bukti: praktis nol.** Dan ada satu klaim fabrikasi yang harus ditolak:

> Klaim yang beredar luas *"studi FXCM atas 10.000 trade: win rate 62% dengan konfirmasi
> multi-timeframe vs 45% satu frame"* **tidak berkorespondensi dengan publikasi FXCM mana
> pun.** Studi FXCM yang nyata adalah 43 juta trade tentang **risk:reward, leverage, dan
> waktu hari** — bukan MTF, dan ukuran sampelnya tidak cocok. **Perlakukan sebagai
> fabrikasi.**

**Satu poin MTF yang pasti benar, dan itu aturan implementasi bukan klaim:** sinyal
timeframe lebih tinggi harus dihitung **hanya pada bar HTF yang sudah close sepenuhnya.**

---

## Referensi

**Tingkat (a) — kuantitatif, metodologi terbuka**

- TradingStats. *ORB Breakout Strategy Guide* (ES/NQ, 6.142+ hari, 2014–2026)
  https://tradingstats.net/orb-breakout-strategy-guide/
- TradingStats. *Initial Balance Retest Statistics* (NQ/ES, ~3.000 hari-break)
  https://tradingstats.net/initial-balance-retest-statistics/
- ORB Setups. *How to identify and avoid false breakouts: a data-driven approach*
  (240.102 trade, 600+ simbol)
  https://orbsetups.com/research/how-to-identify-and-avoid-false-breakouts-a-data-driven-approach/
- StatOasis. *ICT backtest: what survives* (648 backtest)
  https://statoasis.com/overfit/research/ict-backtest-what-survives
- Zarattini, C. & Aziz, A. (2023). *Can Day Trading Really Be Profitable?* SSRN 4416622.
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4416622
- Replikasi independen ORB QQQ —
  https://github.com/giovannibrusco/zarattini-2023-orb-qqq ·
  https://paperswithbacktest.com/strategies/orb-trading-strategy ·
  https://danfin.net/opening-range-breakout-research
- Heinz, A. et al. (2021). *Bullish and Bearish Engulfing patterns.* *QREF* 79, 221–244.
  https://ideas.repec.org/a/eee/quaeco/v79y2021icp221-244.html
- Barber, B., Lee, Y.-T., Liu, Y.-J. & Odean, T. (2009). *Just How Much Do Individual
  Investors Lose by Trading?* *RFS* 22(2), 609–632. https://doi.org/10.1093/rfs/hhn046 —
  *"praktis seluruh kerugian trading individu dapat ditelusuri ke order agresif mereka."*
- Fetna (2026). *Opening-Range Breakout Does Not Survive Trading Costs.*
  https://doi.org/10.2139/ssrn.7428398 — **belum dibaca**; SSRN menolak akses otomatis.
  Ambil manual.
- Mahadzva (2026). *Session Microstructure and the Momentum Mirage.*
  https://doi.org/10.2139/ssrn.7435081 — **belum dibaca**; SSRN menolak akses otomatis.
  Ambil manual.

**Tingkat (b) — konsensus praktisi tanpa data**

- Daily Price Action, aturan pin bar —
  https://dailypriceaction.com/blog/forex-pin-bar-trading-strategy/
- Audacity Capital, inside bar dengan filter ADX —
  https://audacity.capital/trading-guides/inside-bar-trading-strategy/
- Al Brooks, *Trading Price Action Trading Ranges* —
  https://www.brookstradingcourse.com/how-to-trade-manual/trading-ranges/

**Yang harus dibuang (c)**

- "Studi FXCM: 62% MTF vs 45% single-frame" — **tampaknya fabrikasi**; tidak ada
  publikasi FXCM yang cocok.
- pro-scalper.com, "53–56% sesi London menembus high Asia lebih dulu" — tanpa sampel,
  tanpa tanggal; menjual EA emas.
- breakoutalerts.io, "jam 08:00 GMT rata-rata +34,9R" — N=41, dan **bertentangan dengan
  filternya sendiri**: filter mereka bilang lewati range di atas 40 pip, sementara data
  mereka sendiri bilang range 40+ pip adalah satu-satunya bucket yang menguntungkan.
