# Metodologi dan statistik

Bagaimana tidak menipu diri sendiri. File ini adalah alasan mengapa sebagian besar angka
"win rate 80%" yang beredar tidak berarti apa-apa — dan checklist agar backtest V6 tidak
bernasib sama.

---

## 1. Ukuran sampel: berapa trade yang benar-benar dibutuhkan

Trade yang dibutuhkan untuk mencapai t = 2, di mana δ adalah edge win-rate di atas baseline
dan R adalah rasio reward:risk:

```
N = 4R / (δ²(R+1)²)
```

| δ | R=1 | R=2 | R=3 | R=5 |
|---|---|---|---|---|
| 2pp | **2500** | 2222 | 1875 | 1389 |
| 3pp | 1111 | 988 | 833 | 617 |
| 5pp | 400 | 356 | 300 | 222 |

**Ambang "<1000 basket = terlalu awal untuk dipercaya" di dashboard kita kira-kira benar
untuk edge 3pp dan jauh terlalu longgar untuk edge 2pp.**

### Contoh nyata: 1.000 trade tidak cukup untuk sistem 1:3

Pada R=3 dengan win rate sejati 25%, N=1000 memberi SE = 1,37pp, jadi CI 95% adalah
**22,3%–27,7%**, dengan titik impas di **tepat tengah**.

**1.000 trade tidak bisa membedakan sistem 1:3 yang menguntungkan dari yang merugi.** Pada
2–3 trade/hari di emas M15, itu ~18 bulan data live yang tidak memberi tahu apa pun secara
konklusif.

### Rentetan kalah yang harus diharapkan

Ekspektasi rentetan kalah terpanjang ≈ `ln(N)/ln(1/q)`:

| R | Win rate | Rentetan terpanjang, N=1000 | DD pada risiko 1% |
|---|---|---|---|
| 1 | 50% | ~10 | ~9,6% |
| 2 | 33,3% | ~17 | ~15,7% |
| 3 | 25% | ~24 | ~21,4% |
| 5 | 16,7% | ~38 | ~31,7% |

## 2. Batas atas probabilitas bencana: rule of three

Nol ledakan dalam *n* observasi ⟹ batas atas 95% probabilitas ledakan per basket ≈ **3/n**
(Hanley & Lippman-Hand).

| Basket diamati tanpa ledakan | Batas atas 95% p | P(hancur dalam 1.000 basket berikutnya) |
|---|---|---|
| 100 | 3,0% | 100% |
| 200 | 1,5% | 100% |
| 500 | 0,60% | **99,8%** |
| 1.000 | 0,30% | 95,0% |
| 2.000 | 0,15% | 77,7% |
| 5.000 | 0,060% | 45,1% |

**Backtest 500 basket dengan kurva ekuitas sempurna secara statistik konsisten dengan
kehancuran yang nyaris pasti.** Kurva ekuitas apa pun yang lebih pendek dari ~5.000 basket —
**termasuk backtest kita sendiri** — nyaris tidak membawa informasi tentang ekornya.

## 3. Data snooping: koreksinya adalah seluruh ceritanya

### Lintasan Park & Irwin **(a)**

- **2007:** dari **95 studi modern, 56** positif, 20 negatif, 19 campuran.
- **2010:** 17 pasar futures AS, 1985–2004, dengan White's Bootstrap Reality Check dan
  Hansen's SPA: **hanya 2 dari 17** signifikan setelah koreksi data snooping.

**Penulis yang sama. Koreksi data-snooping adalah seluruh ceritanya.**

### Rintangan t yang benar

**Harvey, Liu & Zhu (2016)**, *RFS* **(a)**:

> *"faktor yang baru ditemukan perlu melewati rintangan jauh lebih tinggi, dengan **t-ratio
> lebih besar dari 3,0**... kami berargumen bahwa **sebagian besar temuan riset yang diklaim
> dalam ekonomi keuangan kemungkinan salah**."*

**Aturan V6: t > 3,0, bukan t > 2,0.** Terapkan batas itu pada Tabel II Wang dan hanya hasil
portofolio 4–6 minggu yang bertahan. Terapkan pada literatur ML-sentimen dan praktis tidak
ada yang bertahan.

### Alat koreksi

| Alat | Kapan dipakai |
|---|---|
| **White's Reality Check** (2000) | Menguji apakah aturan terbaik dari semesta aturan benar-benar mengalahkan benchmark |
| **Hansen's SPA** | Versi yang lebih bertenaga dari Reality Check |
| **Romano–Wolf stepdown** | Mengidentifikasi *aturan mana* yang signifikan, bukan hanya apakah ada |
| **Deflated Sharpe Ratio** (Bailey & López de Prado 2014) | Mengoreksi Sharpe terhadap jumlah percobaan |
| **Probability of Backtest Overfitting** (Bailey et al. 2016) | Mengukur probabilitas konfigurasi terbaik in-sample berkinerja di bawah median out-of-sample |
| **Purged / embargoed CV** (López de Prado) | Cross-validation untuk deret waktu tanpa kebocoran |

**Tidak satu pun makalah ML pola chart yang ditemukan menerapkan salah satu dari ini pada
hasilnya sendiri.** → [05](05-pola-chart-klasik.md)

## 4. Look-ahead intrabar: bug yang mengubah 73% jadi 50%

**MPM Markets (a)** — futures CME termasuk **GC (emas)**, ~2,5 juta bar 1 menit per
instrumen, ~40.000 FVG, pra-registrasi:

> *"Win rate turun dari kira-kira 73% ke kira-kira 50%… Seluruh edge yang tampak adalah
> look-ahead intrabar"* — profit factor **2,4 → 1,0**.

**Mekanismenya:** ketika stop dan target sama-sama berada di dalam rentang satu bar M15,
backtest OHLC tidak tahu mana yang tersentuh duluan — dan sering diam-diam memilih yang
menguntungkan. Itu terjadi di sekitar **23 poin persentase** kasus pada instrumen dan
timeframe kita.

**Aturan V6:**
1. **Selesaikan setiap stop/target pada data M1 atau tick**, bukan pada bar sinyal.
2. **Hitung sentuhan hanya mulai dari menit SETELAH entry benar-benar terisi.**
3. **Saat urutan tidak bisa ditentukan, asumsikan stop duluan** (tie-break konservatif).

**Sampai ini selesai, tidak ada angka backtest yang berarti.**

## 5. Look-ahead pivot

Pivot N-bar tidak terkonfirmasi sampai N bar kanan close. Di M15, N=5 berarti **lag 75
menit**; N=50 berarti **12,5 jam**. **Backtest apa pun yang memakai `pivothigh(N)` tanpa
offset N bar mengandung bias look-ahead, akan terlihat luar biasa, dan akan mati live.**

## 6. Look-ahead multi-timeframe

**Sinyal HTF harus dihitung hanya pada bar HTF yang sudah close sepenuhnya.** Memakai bar H4
yang sedang terbentuk membocorkan informasi masa depan ke setiap keputusan M15 di dalamnya.
**Penyebab paling umum backtest multi-timeframe terlihat bagus lalu mati live.**

## 7. Kebocoran cross-validation pada jendela geser

Satu makalah melaporkan **F1 > 0,99** pada klasifikasi pola chart lewat stratified 5-fold CV.
Pada jendela geser yang tumpang tindih, **jendela *t* dan *t+1* berbagi ~99% bar yang sama.**
Stratified k-fold membocorkan informasi dari test ke train.

**Perlakukan F1 > 0,99 sebagai bukti kebocoran, bukan keterampilan.** Pakai purged/embargoed
CV.

## 8. Bootstrap jalur alternatif, jangan optimalkan satu sejarah

Lei & Li **(a)**:

> *"strategi ini tidak mengurangi maupun menambah kerugian investor relatif terhadap strategi
> buy-and-hold **begitu kami memperluas return sekuritas dari realisasi masa lalu ke jalur
> masa depan yang mungkin**."*

**Menguji level stop pada satu jalur historis adalah overfitting terhadap urutan spesifik
jalur itu.** Metodologi mereka — mem-bootstrap jalur masa depan alternatif — adalah hal
terpenting untuk ditiru.

Carr & López de Prado **(a)** menambahkan peringatan tegas: pada proses yang mendekati random
walk, *"tidak ada area yang bisa dikenali di mana kinerja bisa dimaksimalkan…
**Mengkalibrasi aturan trading pada random walk lewat simulasi historis akan menghasilkan
backtest overfitting.**"*

## 9. Karakterisasi proses sebelum tuning aturan

**Ini langkah yang paling sering dilewati, dan yang paling menentukan.**

Sebelum men-tuning parameter SL/TP apa pun, ukur:

1. **Autokorelasi return (ρ)** per jendela sesi, pada horizon trading kita.
2. **Half-life Ornstein-Uhlenbeck**, kalau ada mean reversion.
3. **Variance ratio BERTANDA** — bukan `|1 − VR|`. VR > 1 berarti tren; VR < 1 berarti
   mean-reverting. Studi efisiensi emas yang ada memakai nilai absolut, jadi **tidak
   memberi tahu arahnya.** → [03](03-sesi-dan-waktu.md)

Lalu terapkan ambang Kaminski & Lo: **stop menambah nilai di bawah momentum ketika
`ρ/(1−ρ) > SR`**, dikalibrasi pada frekuensi sampling yang sama.

| Hasil pengukuran | Geometri yang benar |
|---|---|
| ρ > 0 signifikan (momentum) | Stop rapat, target lebar, trailing |
| ρ < 0 signifikan (mean-reverting) | Stop lebar, target sempit, **tanpa** trailing |
| ρ ≈ 0 (random walk) | **Semua geometri identik. Jangan tuning — itu overfitting.** |

Satu data yang sudah ada: estimasi Hurst langsung untuk emas (uji cross-instrument MGC
Mesfin) menempatkan emas **lebih dekat ke 0,5** daripada MNQ (0,59) — lebih dekat ke random
walk. Mean reversion OU pada MGC 5 menit **gagal di setiap ambang** (t terbaik = −4,49, win
rate 32%), dan half-life 60 menit berarti ~8 jam — lebih lama dari satu sesi, **secara
struktural tidak kompatibel dengan eksekusi intraday.**

## 10. Pengukuran yang harus dicatat EA

**Setiap trade:**
- MAE dan MFE dalam **satuan ATR**
- Waktu sampai MAE, waktu sampai MFE
- Jam UTC entry, sesi
- Spread saat entry dan exit
- Slippage terealisasi
- Latensi keputusan
- Alasan exit (TP / SL / waktu / eksternal)

**Setiap jam:**
- ATR(14) M1 dan M5
- Distribusi spread (mean, median, persentil 95)
- Rasio `friksi / ATR(M5)`

**Sekali, saat init** (uji falsifikasi):
- `SYMBOL_TICKS_BOOKDEPTH`
- Jumlah return `CopyTicks(..., COPY_TICKS_TRADE, ...)`
- Jumlah return `CopyRealVolume()`
- `SYMBOL_TRADE_TICK_VALUE`, `SYMBOL_TRADE_TICK_SIZE`, `SYMBOL_POINT`

## 11. Checklist sebelum mempercayai backtest mana pun

- [ ] Stop/target diselesaikan pada M1 atau tick, dengan tie-break stop-duluan
- [ ] Pivot di-offset sejumlah lag konfirmasinya
- [ ] Fitur HTF hanya dari bar tertutup
- [ ] Offset DST disuplai terpisah untuk Strategy Tester
- [ ] Spread dimodelkan per jam dari data live, bukan konstanta
- [ ] Biaya mencakup spread, komisi, slippage, dan swap
- [ ] Jumlah konfigurasi yang dicoba dicatat, dan koreksi multiple testing diterapkan
- [ ] Rintangan t > 3,0, bukan t > 2,0
- [ ] Hasil out-of-sample atau walk-forward, bukan hanya in-sample
- [ ] Jumlah trade mencapai N dari tabel §1 untuk edge yang diklaim
- [ ] Proses sudah dikarakterisasi (ρ, VR bertanda) sebelum tuning geometri
- [ ] Profit tidak terkonsentrasi di segelintir hari (cek: buang 3 hari terbaik, ulang uji t)
- [ ] Parameter "sejalan dengan prinsip TA" tidak dipilih setelah melihat hasil

### Contoh kegagalan checklist dari riset ini

Uji breakout range Asia pada 46 hari data emas terukur: **setiap satu dari 20 sel parameter
menguntungkan.** Itu *bukan* hasil — itu wujud sampel rezim tunggal yang terlalu pendek. Sel
terbaik memberi **t = 2,17 pada n = 46** — dan itu yang terbaik dari 20 sel, jadi
signifikansi yang disesuaikan multiple testing adalah nol. **Tiga hari dari 46 menyumbang 54%
dari seluruh profit**; membuangnya menurunkan t ke **1,33**.

---

## Referensi

**Tingkat (a)**

- Harvey, C., Liu, Y. & Zhu, H. (2016). *…and the Cross-Section of Expected Returns.* *RFS*.
  https://doi.org/10.1093/rfs/hhv059
- Bailey, D., Borwein, J., López de Prado, M. & Zhu, Q. (2016). *The Probability of Backtest
  Overfitting.* *Journal of Computational Finance*. https://doi.org/10.21314/jcf.2016.322 ·
  [`../pdf/bailey-lopezdeprado-probability-of-backtest-overfitting.txt`](../pdf/bailey-lopezdeprado-probability-of-backtest-overfitting.txt)
- Bailey, D. & López de Prado, M. (2014). *The Deflated Sharpe Ratio.* *Journal of Portfolio
  Management*. https://doi.org/10.3905/jpm.2014.40.5.094
- Sullivan, R., Timmermann, A. & White, H. (1999). *Data-Snooping, Technical Trading Rule
  Performance, and the Bootstrap.* *JoF* 54(5). https://doi.org/10.1111/0022-1082.00163 ·
  [`../pdf/sullivan-timmermann-white-1999-data-snooping-technical-trading-bootstrap.pdf`](../pdf/sullivan-timmermann-white-1999-data-snooping-technical-trading-bootstrap.pdf)
- Park, C.-H. & Irwin, S. (2007, 2010). https://doi.org/10.1111/j.1467-6419.2007.00519.x ·
  https://doi.org/10.1002/fut.20435
- Bajgrowicz, P. & Scaillet, O. (2012). https://doi.org/10.1016/j.jfineco.2012.06.001
- Lei, A. & Li, H. (2009). https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1214737
- Carr, P. & López de Prado, M. (2014).
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2658641
- Kaminski, K. & Lo, A. (2014). https://doi.org/10.1016/j.finmar.2013.07.001
- MPM Markets. *Does the Fair Value Gap strategy work?*
  https://mpmmarkets.com/research/does-the-fair-value-gap-strategy-work
- Mesfin, M. (2026). https://arxiv.org/abs/2605.04004 ·
  [`../pdf/mesfin-2026-structural-limits-of-ohlcv-intraday-signals-mnq.pdf`](../pdf/mesfin-2026-structural-limits-of-ohlcv-intraday-signals-mnq.pdf)
- Psaradellis, I. et al. (2023). *International Journal of Forecasting* 39(1).
  https://doi.org/10.1016/j.ijforecast.2021.10.002 — satu dari sedikit studi dengan kontrol
  false-discovery, uji out-of-sample ekstensif, dan biaya.
- López de Prado, M. (2018). *Advances in Financial Machine Learning.* Wiley — purged CV,
  triple-barrier, meta-labeling.
- Hanley, J. & Lippman-Hand, A. (1983). *If nothing goes wrong, is everything all right?*
  *JAMA* — rule of three.
