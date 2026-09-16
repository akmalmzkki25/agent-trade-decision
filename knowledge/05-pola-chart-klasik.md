# Pola chart klasik

Head and shoulders, double/triple top dan bottom, segitiga, flag dan pennant, wedge,
rectangle, cup-and-handle.

---

## 1. Putusan

**Jangan bangun ini untuk V6.** Alasannya bukan selera — bukti terbaik yang tersedia
bersifat negatif, dan sebagian besar pola yang kita kenal tidak pernah diuji sama
sekali.

Lima fakta yang menentukan:

1. **Lima dari tujuh keluarga pola tidak pernah diuji dalam literatur peer-review sama
   sekali.** Seluruh catatan algoritmik mencakup head-and-shoulders, double/triple
   top-bottom, segitiga, rectangle, dan broadening formation — plus flag, dalam satu
   garis keturunan terisolasi. **Pennant, wedge, dan cup-and-handle praktisnya nol
   pengujian empiris peer-review di pasar mana pun, di timeframe mana pun.**
2. **Tidak ada studi pola chart klasik pada emas, di timeframe mana pun.** Tidak harian,
   tidak intraday.
3. **Uji head-and-shoulders yang paling baik desainnya menyimpulkan ia merugi** — dan
   merugi *lebih banyak* daripada algoritma identik yang dijalankan pada return yang
   diacak, di seluruh 14 spesifikasi robustness.
4. **Target measured move sama sekali tidak punya bukti pendukung.** Satu-satunya studi
   akademis yang mengujinya langsung merusak hasilnya sendiri dengan mengambil nilai
   absolut return.
5. **Pola chart butuh pemilihan jangkar yang subjektif**, dan pivot tidak terkonfirmasi
   sampai N bar kanan close — yang berarti setiap chart BOS/CHoCH yang rapi diberi label
   secara retroaktif.

## 2. Bukti negatif terkuat: Osler (1998) **(a)**

*Identifying Noise Traders: The Head-and-Shoulders Pattern in U.S. Equities.*
Federal Reserve Bank of New York Staff Report 42, Februari 1998.

**Sampel:** harga dan volume untuk **100 perusahaan yang dipilih acak** dari CRSP.
Dimulai dari semua perusahaan dengan data harga kontinu 2 Jul 1962 → 31 Des 1993 =
**31,5 tahun, 8.220 observasi harian**; setelah membuang redundansi merger, **528
perusahaan** tersisa, dari situ **100 diambil acak**.

Algoritma menemukan **~27 pola H&S terkonfirmasi per perusahaan** — sedikit kurang dari
satu per tahun, yang ia catat cocok dengan frekuensi yang dijelaskan manual TA.

**Hasil volume (trader H&S memang ada):** total perdagangan yang dibangkitkan pola H&S
tertentu ≈ **seperempat volume perdagangan satu hari**; rata-rata volume residual ≈ **11
persen**; dari 100 p-value tingkat perusahaan, **21 di bawah 0,05 dan 32 di bawah 0,10**.

**Hasil profitabilitas, verbatim:**

> *"Profit rata-rata per posisi sebenarnya **negatif, di −0,24 persen** (pada posisi yang
> ditahan rata-rata **10 hari bisnis**). Profit rata-rata di data simulasi adalah
> **−0,03 persen**... Menurut Uji Satu, **perbedaan ini tidak signifikan secara
> statistik**."*

Uji kedua: 100 p-value tingkat perusahaan **tidak** terkonsentrasi di nilai rendah;
statistik **Anderson-Darling 2,6** dengan *"kecenderungan yang sangat sederhana bagi
profit untuk terkonsentrasi di tingkat **tinggi**"* — artinya kalau ada, H&S sedikit
lebih **buruk** daripada acak.

**Robustness:** sepuluh analisis sensitivitas, termasuk null AR(1) dan AR(1)+GARCH(1,1).

**Dampak harga:** penjualan H&S mendorong harga **turun** dan sebaliknya; efek inkremental
hari-berikutnya ≈ **0,06 persen**; efeknya *"mulai menghilang hampir seketika, dan hilang
sepenuhnya dalam dua minggu."* Ia eksplisit mencatat bahwa dengan biaya transaksi,
*"orang tidak bisa berdagang secara menguntungkan atas informasi bahwa trader
head-and-shoulders aktif di hari berikutnya."*

**Intinya, dengan kata-katanya sendiri:** trader H&S memenuhi syarat sebagai **noise
trader** justru *karena* strateginya tidak menguntungkan.

## 3. Head-and-shoulders di FX: menguntungkan tapi tidak efisien **(a)**

**Chang & Osler (1999)**, *Methodical Madness: Technical Analysis and the Irrationality
of Exchange-rate Forecasts*, *Economic Journal* 109(458), 636–661.
DOI [10.1111/1468-0297.00466](https://doi.org/10.1111/1468-0297.00466)

> ⚠️ DOI yang sering dikutip (`…00459`) salah — itu makalah jaminan sosial Pemberton.

**Sampel:** nilai tukar dolar harian, **1973–1994**, mata uang mayor vs USD. Algoritma
H&S terkomputerisasi objektif dibangun dari kriteria TA yang dipublikasikan.
**Bootstrap, 10.000 deret simulasi** di bawah null random walk.

**Kesimpulan verbatim:** *"Kami menemukan aturan ini **menguntungkan, tapi tidak efisien,
karena ia didominasi oleh aturan trading yang lebih sederhana**."*

Dua kriteria rasionalitas diterapkan (profitabilitas dan efisiensi); H&S lolos yang
pertama, gagal yang kedua. **Head-and-shoulders tidak menambahkan apa pun di atas filter
rule sederhana.**

Catatan tangan kedua: Lo-Mamaysky-Wang (footnote 9) menyatakan Chang & Osler menemukan
bahwa untuk **dua dari enam mata uang** (yen dan Deutsche mark) strategi H&S bisa
menghasilkan profit signifikan. Saya tidak bisa memverifikasi ini dari teks Chang & Osler
sendiri — PDF FRBNY-nya adalah **citra hasil scan tanpa lapisan teks** (pdftotext
menghasilkan 66 byte, dan tidak ada OCR di mesin ini).

## 4. Savin, Weller & Zvingelis (2007) — dan ini rutin disalahkutip **(a)**

*The Predictive Power of "Head-and-Shoulders" Price Patterns in the U.S. Stock Market.*
*Journal of Financial Econometrics* 5(2), 243–265.
DOI [10.1093/jjfinec/nbl012](https://doi.org/10.1093/jjfinec/nbl012)

> ⚠️ DOI yang sering dikutip (`…nbl011`) salah — itu makalah estimasi kovarians Voev & Lunde.

**Metode:** algoritma kernel regression Lo-Mamaysky-Wang **dengan modifikasi** — filter
tambahan "berdasarkan pola harga tipikal yang diidentifikasi **seorang analis teknikal**."
**Sampel:** S&P 500 dan Russell 2000, **1990–1999**.

**Verbatim:** *"kami menemukan **sedikit atau tidak ada dukungan untuk profitabilitas
strategi trading berdiri sendiri**. Tapi kami menemukan bukti kuat bahwa pola ini punya
daya untuk memprediksi excess return. **Excess return yang disesuaikan risiko untuk
strategi yang dikondisikan pada pola 'head-and-shoulders' adalah 5–7% per tahun.**
Menggabungkan strategi dengan portofolio pasar menghasilkan peningkatan signifikan..."*

**Bacaan jujurnya:** strategi pola *berdiri sendiri* **tidak** bekerja; 5–7% itu menuntut
melapisi pola di atas portofolio pasar. Biaya transaksi tidak disebutkan di abstrak dan
tidak bisa diverifikasi (berbayar).

## 5. Ha & Moon (2017) — satu-satunya uji forward-return langsung atas pola buku teks **(a)**

*The Evolution of Neural Network-Based Chart Patterns*, GECCO '17.
DOI [10.1145/3071178.3071192](https://doi.org/10.1145/3071178.3071192) ·
arXiv https://arxiv.org/abs/1706.05283

Membandingkan **26 pola buku teks yang dirancang manual** — H&S top/bottom, segitiga
ascending/descending/simetris, double top/bottom, triple top/bottom, broadening
formation, wedge, rectangle, diamond — pada **expected k-hari forward log return**,
k ∈ {20, 50, 100}.

**Data:** semua saham yang pernah tercatat di Korea, **Jan 2012 – Des 2016**, harian;
latih 2012–14 (1.018.045 sampel), validasi 2015, uji 2016 (292.141).

**Hasil out-of-sample (2016):**

- **Head-and-shoulders top: negatif di 20, 50, dan 100 hari.**
- **Descending triangle: negatif di ketiga horizon.**
- Sebagian besar pola tidak bisa dibedakan dari nol, dengan **tanda yang berbalik antar
  horizon.**
- **Double head-and-shoulders bottom/top dan head-and-double-shoulders bottom: nol
  kecocokan dalam lebih dari satu juta chart.** Polanya tidak pernah menyala.
- **"Double top" — pola reversal bearish kanonik — menunjukkan forward return positif
  TERBESAR dari semua pola klasik.** Kebalikan dari klaim buku teks.

**Peringatan yang harus dinyatakan:** ini nilai fitness yang dipenalti (disesuaikan
kelangkaan), jadi besarannya tidak langsung terbaca sebagai return. **Tandanya adalah
klaim yang aman.** Juga: pola hasil evolusi runtuh out-of-sample (+1,33 validasi →
**−0,29 uji**), pencariannya 1.000 individu × 200 generasi **tanpa koreksi
data-snooping**, dan biaya transaksi absen. Penulis menyebutnya sendiri *"preliminary."*

## 6. Lo, Mamaysky & Wang (2000) — makalah fondasi, dan apa yang TIDAK ia tetapkan **(a)**

*Foundations of Technical Analysis.* *Journal of Finance* 55(4).
Teks lengkap: [`../pdf/lo-mamaysky-wang-2000-foundations-of-technical-analysis.pdf`](../pdf/lo-mamaysky-wang-2000-foundations-of-technical-analysis.pdf)

**Metode:** smoothing **kernel regression** nonparametrik untuk mengekstrak ekstrem
lokal, lalu definisi berbasis aturan untuk **10 pola**. Bandwidth 0,3×h, dipilih dengan
memeriksa kurva hasil fit **bersama beberapa analis teknikal profesional**, **sebelum**
analisis statistik.

**Sampel:** CRSP **NYSE/AMEX dan Nasdaq**, harian, **1962–1996**, dibagi jadi **tujuh
subperiode lima tahun**; di setiap subperiode **10 saham diambil acak dari masing-masing
5 kuintil kapitalisasi = 50 saham per subperiode**; pengambilan diulang dua kali.

**Jumlah pola:** NYSE/AMEX seluruh sampel — double top/bottom **>2.000 kejadian
masing-masing**; H&S dan inverted H&S **>1.600 masing-masing**.

**Kelemahan yang mereka akui sendiri, verbatim:** *"statistik goodness-of-fit dan
Kolmogorov-Smirnov diturunkan di bawah asumsi bahwa return bersifat independen dan
terdistribusi identik, **yang tidak masuk akal untuk data keuangan**... normalisasi...
**tidak menghilangkan dependensi atau heterogenitas**."*

**KESIMPULAN SEBENARNYA, verbatim — dan ini rutin disalahkutip:**

> *"kami menemukan bahwa pola teknikal tertentu memang memberi informasi inkremental,
> terutama untuk saham Nasdaq. **Meskipun ini tidak serta-merta berarti analisis teknikal
> bisa dipakai menghasilkan profit 'berlebih'**, ia membuka kemungkinan bahwa analisis
> teknikal bisa menambah nilai pada proses investasi."*

Dan: *"pola tradisional seperti head-and-shoulders dan rectangle, **meski kadang efektif,
belum tentu optimal**."*

**Tidak ada biaya transaksi. Tidak ada strategi trading. Tidak ada uji profitabilitas.**
Ini uji distribusi kondisional vs tidak kondisional, titik.

## 7. Klaster null Marshall — bukti terkuat melawan profitabilitas aturan **(a)**

- **Marshall, Cahan & Cahan (2008)**, *Technical Analysis Around the World*,
  SSRN [10.2139/ssrn.1181367](https://doi.org/10.2139/ssrn.1181367): *"Lebih dari 5.000
  aturan trading teknikal populer **tidak konsisten menguntungkan di 49 indeks negara**
  yang membentuk MSCI **setelah bias data snooping diperhitungkan**."*
- **Marshall, Cahan & Cahan (2008)**, *Can commodity futures be profitably traded with
  quantitative market timing strategies?*, *JBF*,
  DOI [10.1016/j.jbankfin.2007.12.011](https://doi.org/10.1016/j.jbankfin.2007.12.011):
  lebih dari **7.000 aturan pada 15 futures komoditas mayor**, dua metodologi bootstrap,
  disesuaikan data-snooping — *"kami menunjukkan secara konklusif bahwa mereka tidak
  menguntungkan."*
  **Ini yang paling mendekati bukti langsung tentang emas.**
- **Bajgrowicz & Scaillet (2012)**, *Technical trading revisited: False discoveries,
  persistence tests, and transaction costs*, *JFE*,
  DOI [10.1016/j.jfineco.2012.06.001](https://doi.org/10.1016/j.jfineco.2012.06.001).
- **Marshall, Young & Rose (2008)** menguji **7.846 aturan pada data ekuitas AS 5 menit**
  — **tidak satu pun menguntungkan** setelah penyesuaian data-snooping.
- **Yamamoto (2012)** menguji **5.081 strategi pada 207 saham Nikkei 225** intraday
  memakai White's Reality Check *dan* Hansen's SPA — lebih dari separuh menunjukkan
  prediktabilitas jangka pendek, **tidak satu pun mengalahkan buy-and-hold.**

## 8. Tsinaslanidis & Guijarro (2020/21) — makalah paling lengkap metodologinya **(a)**

*What makes trading strategies based on chart pattern recognition profitable?*
*Expert Systems* 38(5). DOI [10.1111/exsy.12596](https://doi.org/10.1111/exsy.12596) ·
[`../pdf/tsinaslanidis-guijarro-2021-what-makes-chart-pattern-strategies-profitable.pdf`](../pdf/tsinaslanidis-guijarro-2021-what-makes-chart-pattern-strategies-profitable.pdf)

Sengaja **meninggalkan pola bernama**. Memakai **pengenalan pola generik** — bentuk apa
pun yang menguntungkan secara historis — dicocokkan lewat subsequence Dynamic Time
Warping (UCR Suite).

**Data:** 1.920 saham NYSE dari Bloomberg, 3 Jan 2006 – 31 Des 2015, disaring ke
**N = 560 saham, T = 2.517 bar OHLC HARIAN**. 1.687.840 trade potensial.
**Biaya: 0,05% per satu arah.** **Koreksi data-snooping: White's Reality Check** dengan
stationary bootstrap Politis-Romano.

**Angka yang harus dikutip:**

> Tanpa biaya, **1.025 dari 1.126 konfigurasi (91,03%)** menghasilkan return harian
> rata-rata positif. Setelah fee 0,05% satu arah, **hanya 552 dari 1.126 (49%)** yang
> tetap positif.

Sebuah lemparan koin. Return rata-rata per trade turun dari 0,12% kotor ke **0,02%**
bersih.

**Peringatan yang dikubur abstraknya.** Judul "92,5% eksperimen menguntungkan" muncul
hanya setelah membatasi ke 429 dari 1.126 konfigurasi, dipilih *ex post* sebagai "sejalan
dengan prinsip TA". Dan lebih dalam lagi: **Reality Check diterapkan pada konfigurasi
terbaik dari semesta penuh pada return KOTOR; analisis biaya datang sesudahnya.**
Signifikansi yang disesuaikan data-snooping karenanya ditetapkan pada return kotor.

**Penulisnya sendiri menyatakan metodenya belum diuji pada FX, komoditas, dan intraday.**

## 9. Literatur machine learning: bertanya hal yang salah selama satu dekade

Ada **dua literatur berbeda** di sini, dan menyamakannya adalah kesalahan paling umum.

### Literatur A — pengenalan (skor tinggi, nol kandungan prediktif)

"Apakah gambar ini memuat head-and-shoulders?" Label ground-truth dibangkitkan oleh
aturan yang ditulis penulisnya sendiri, atau digambar tangan dengan hindsight. Skor yang
dilaporkan: 90–99%. **Nilai buktinya soal prediksi: nol.** Makalah-makalah ini mengukur
seberapa baik neural net mengimplementasikan ulang sebuah pernyataan `if`.

Empat bukti langsung, kutipan dari makalahnya sendiri:

**Velay & Daniel (2018)**, arXiv:1808.00418:
> *"Kami pertama-tama mengimplementasikan recognizer hard-coded... **Kami memakainya
> untuk membangun training set kami.**"*

Model pemenang: LSTM, recall 96,8%, **keunggulan generalisasi atas aturannya: 0,3%**.
Kesimpulan mereka: *"model CNN tidak memberi tingkat deteksi yang lebih baik daripada
algoritma hard-coded."* Tidak ada simulasi trading.

**Cohen, Balch & Veloso (2020)**, DOI 10.1145/3383455.3422544 (J.P. Morgan AI Research):
> *"...melabeli sampel mengikuti **tiga strategi trading biner yang didefinisikan secara
> aljabar**... algoritma secara efisien **memulihkan aturan pembangkit-label yang rumit
> dan multiskala** saat data direpresentasikan secara visual."*

Dan di pendahuluan: *"Makalah ini ditulis di bawah asumsi bahwa, mengingat pengetahuan
publik, pasar efisien... **pergerakan pasar masa depan nyaris tidak punya
prediktabilitas.**"* Angka 95% itu adalah memulihkan perpotongan Bollinger Band dan RSI
dari gambar perpotongan itu sendiri.

**Chen & Tsai (GAF-CNN)**, DOI 10.1186/s40854-020-00187-0:
Data latihnya **disimulasikan dari Geometric Brownian Motion** — random walk yang, secara
konstruksi, nol struktur yang bisa diprediksi. Modelnya mencetak **~92% pada random
walk** dan 90,7% pada data EUR/USD 1 menit nyata. Dan saat aturan buku teks menghasilkan
terlalu sedikit positif: *"kami **melonggarkan aturannya** untuk mendapat data yang
cukup."*

**~92% pada noise ITULAH buktinya bahwa angka akurasi ini tidak mungkin keterampilan
prediktif.**

**Birogul, Temür & Köse (2020)**, DOI 10.1109/ACCESS.2020.2994282:
Manusia melihat **chart tahunan yang sudah selesai** dan mengotaki bagian rendahnya
sebagai Buy dan puncaknya sebagai Sell. Labelnya tidak bisa diketahui secara real time,
dan gambar inputnya memuat seluruh tahun. Makalah lanjutannya (DOI
10.1109/ACCESS.2024.3411991) melaporkan *"keberhasilan prediksi 100%"* — yang merupakan
diagnosis evaluasi yang rusak, bukan hasil.

### Literatur B — prediksi (48–59%, di atau sedikit di atas lemparan koin)

**Jiang, Kelly & Xiu (2023)**, *(Re-)Imag(in)ing Price Trends*, *Journal of Finance*
78(6). DOI [10.1111/jofi.13268](https://doi.org/10.1111/jofi.13268)

Target genuinely prediktif — forward return, bukan pengenalan. **Tapi makalah ini bukan
tentang pola chart klasik.** CNN-nya mempelajari bentuk arbitrer; penulisnya eksplisit
mencatat pola yang dipelajari *"berbeda signifikan dari sinyal tren yang lazim
dianalisis."* **Siapa pun yang mengutip JKX sebagai pembenaran head-and-shoulders salah
baca.**

Dua hal paling berguna tentang JKX justru skeptis:

- **Bøjstrup, Veliyev & Wulff (2026)**, SSRN 10.2139/ssrn.7119420 (working paper):
  *"Model gambar yang dilatih pada chart saham memprediksi return out-of-sample,
  **meski chart tidak menambahkan informasi apa pun di luar data OHLCV yang ia
  render.**... Kira-kira **60% dari selisih yang tampak terkait dengan belajar dari
  return realisasi yang berisik alih-alih konten spesifik chart.**"*
- Replikasi pihak ketiga pada ~5.000 saham A China melaporkan decile-spread
  **Sharpe 2,76 kotor → 1,07 bersih hanya pada 10 bps/sisi**, dan akurasi arah CNN pada
  test set **51,5% vs base rate 48,3%**. (Replikasi GitHub tanpa review — bukan bukti
  atas angka JKX sendiri, tapi bentuknya instruktif: haircut Sharpe ~61% dari asumsi
  biaya sederhana.)

**Tsai, Chen & Wang (2018)**, arXiv:1801.03018 — lab Taiwan yang sama dengan GAF-CNN,
tapi dengan label forward return nyata pada JPY:
> *"**Tidak satu pun eksperimen menghasilkan kinerja yang baik.** Selain itu, setiap
> model tidak stabil karena over-fitting."*

Mereka meninggalkan data FX nyata dan mundur ke simulasi GBM. **Lab yang sama: 90,7% pada
pengenalan, kegagalan total pada prediksi.** Itu titik data paling bersih di seluruh
audit ini.

### Dan tidak satu pun menerapkan koreksi multiple testing

**Tidak satu pun makalah yang ditemukan di seluruh literatur ML pola chart menerapkan
White's Reality Check, Hansen's SPA, Romano–Wolf, atau deflated Sharpe ratio pada
hasilnya sendiri.** Biaya transaksi dimodelkan di minoritas kecil, dan dua makalah secara
eksplisit menyatakan mereka mengecualikan komisi sambil mengutip rentang yang mereka
tolak terapkan.

## 10. Intraday: satu-satunya studi besar, dan ia merusak asumsi fraktalitas

**Cervelló-Royo, Guijarro & Michniuk (2015)**, *ESWA* 42(14), 5963–5975,
DOI [10.1016/j.eswa.2015.03.017](https://doi.org/10.1016/j.eswa.2015.03.017), dan
disertasi Michniuk (2017), DOI [10.4995/thesis/10251/78837](https://doi.org/10.4995/thesis/10251/78837)

Benar-benar besar dan benar-benar intraday: **DAX 15 menit, 127.480 bar, 2000–2013;
futures DJIA 15 menit, 91.307 bar; IBEX-35 15 menit plus per jam.** Template bull/bear
flag dengan stop-loss dan take-profit. Judul: DJIA sampai 180,2% total return; DAX 346,7%
vs buy-and-hold 9,9%.

**Tiga masalah yang mendiskualifikasi, semuanya diakui di teksnya sendiri:**

1. **Tidak ada biaya transaksi.** Verbatim: *"biaya transaksi keuangan sangat kecil,
   terutama saat bekerja dengan pasar futures; **karena itu kesimpulan ini tidak akan
   berubah oleh inklusi biaya transaksi**."* Itu asersi, bukan perhitungan — dengan
   1.402–3.053 round trip pada bar 15 menit.
2. **Tidak ada uji signifikansi sama sekali**, dan **436 konfigurasi parameter dicari**
   (220 DAX + 96 DJIA + 120 IBEX) dengan yang terbaik dilaporkan. Alasan yang dinyatakan:
   *"Non-normalitas return aturan trading menghalangi penerapan statistik t."*
   Non-normalitas adalah alasan untuk bootstrap, bukan untuk meninggalkan inferensi.
   Tinjauan literaturnya sendiri menjelaskan Reality Check, SPA, dan Romano–Wolf panjang
   lebar — lalu tidak menerapkan satu pun.
3. **Hasilnya sendiri merusak fraktalitas.** Pola yang sama, indeks yang sama, periode
   yang sama: menguntungkan di semua 120 konfigurasi pada 15 menit, menguntungkan hanya
   di **40%** pada per jam. Kata penulisnya sendiri: *"**Ini mempertanyakan salah satu
   asumsi dasar analisis teknikal: fraktalitas.**"*

**Ben Omrane & Van Oppens (2006)**, *Empirical Economics* 30(4), 947–971,
DOI [10.1007/s00181-005-0007-8](https://doi.org/10.1007/s00181-005-0007-8) — intra-harian
EUR/USD, dua belas jenis pola chart, dua metode deteksi ekstrem, signifikansi Monte Carlo:

> *"Lebih dari separuh chart yang terdeteksi menunjukkan prediktabilitas signifikan.
> Namun demikian, **hanya dua pola chart yang berimplikasi profitabilitas signifikan yang
> bagaimanapun terlalu kecil untuk menutup biaya transaksi.**"*

Perhatikan bahwa 12 pola × 2 metode = 24 uji, dan tepat 2 keluar menguntungkan — kira-kira
yang diberikan kebetulan.

## 11. Masalah identifikasi: pivot butuh offset

Satu-satunya definisi presisi yang bisa dikodekan adalah **pivot N-bar**: pivot high
berkekuatan N adalah bar yang high-nya melampaui high dari N bar di kiri **dan** N bar
di kanan. Fractal Williams adalah kasus N=2. Implementasi SMC open-source yang paling
banyak dipakai default ke **50 bar untuk struktur swing, 5 untuk struktur internal**.

**Ini jebakan look-ahead terbesar di seluruh keluarga ini.** Pivot tidak bisa
dikonfirmasi sampai setiap bar sisi kanan close. Di M15, N=5 berarti **lag 75 menit**;
N=50 berarti **12,5 jam**. Setiap chart BOS/CHoCH rapi yang pernah kita lihat diberi label
retroaktif. **Backtest apa pun yang memakai `pivothigh(N)` tanpa offset N bar mengandung
bias look-ahead, akan terlihat luar biasa, dan akan mati live.**

### Dan analis profesional pun tidak sepakat di mana levelnya

Osler (2000) **(a)** — enam firma FX profesional menerbitkan level support/resistance
untuk tiga pasangan mata uang yang sama pada hari yang sama:

| Ukuran | Nilai |
|---|---|
| **Kesepakatan berpasangan antar firma** (toleransi longgar 5 poin) | **~30% rata-rata; rentang 13%–38%** |
| Level per hari, per firma | Firma 1: 10,0 · Firma 2: 6,0 · Firma 3: 17,8 · Firma 5: 4,2 · Firma 6: 2,5 |
| Jarak level terluar dari spot | Firma 6: ~100 poin · Firma 3: >700 poin |

Verbatim: *"**Firma tidak sepakat secara luas satu sama lain tentang sinyal yang
relevan.**"*

Perhatikan asimetri yang Osler tarik sendiri: analis hampir sepenuhnya sepakat pada
**definisi** support/resistance, namun hanya sepakat ~30% pada **di mana levelnya
sebenarnya berada.** Itu bukti paling bersih bahwa masalah subjektivitasnya ada di
penerapan, bukan kosakata.

## 12. Measured move: bucket kosong

**Saya mencari lintas Crossref (tiga query terpisah), arXiv, dan Semantic Scholar. Nol
studi peer-review yang mengukur pencapaian target measured move atau follow-through
breakout sebagai statistik.**

Yang muncul di pencarian hanyalah **bab buku dagang Wiley yang diberi DOI, yang bukan
riset:**

- *"The Measured Move"* — https://doi.org/10.1002/9781119197935.ch23
- *"Failed Breakouts, Breakout Pullbacks, and Breakout Tests"* —
  https://doi.org/10.1002/9781119202608.ch5

Yang paling mendekati perlakuan peer-review adalah parameterisasi TP/SL di dalam makalah
kelompok Guijarro — dan di situ targetnya adalah kelipatan arbitrer dari rentang pola
yang dipilih grid search, **bukan** aturan measured move klasik yang diuji sebagai
hipotesis.

**Implikasinya: setiap angka measured move dan pencapaian target yang beredar — termasuk
semua milik Bulkowski — diterbitkan sendiri dan tidak pernah direplikasi independen di
jurnal peer-review. Tidak ada literatur akademis untuk mengeceknya. Ketiadaan itu bukan
celah pencarian saya; itu keadaan bidangnya.**

Hal yang sama berlaku untuk **false breakout / bull trap sebagai statistik terukur** —
tidak ada literatur peer-review yang mengukurnya. Satu-satunya klaim kuantitatif yang
beredar berasal dari buku dan situs Bulkowski sendiri.

---

## Apa artinya untuk V6

**Jangan bangun deteksi pola chart klasik.** Lebih spesifik:

1. Head-and-shoulders: bukti terbaiknya negatif (Osler 1998) atau "didominasi aturan
   lebih sederhana" (Chang & Osler 1999) atau "tidak ada dukungan untuk strategi berdiri
   sendiri" (Savin et al. 2007).
2. Pola buku teks yang diberi target forward return menghasilkan tanda yang salah atau
   nol (Ha & Moon 2017).
3. Kalau tetap ingin mengeksplorasi pengenalan pola, **kerangka generik DTW**
   (Tsinaslanidis & Guijarro) punya metodologi terbaik — dan hasilnya: 91% konfigurasi
   menguntungkan kotor, **49% bersih** pada biaya institusional 0,05%/sisi, yang jauh
   di bawah biaya ritel kita.
4. Kalau memakai struktur swing apa pun, **offset setiap pivot sejumlah lag
   konfirmasinya** dan pakai hanya bar HTF yang sudah close.

---

## Referensi

**Tingkat (a) — kuantitatif, metodologi terbuka**

- Osler, C. (1998). *Identifying Noise Traders: The Head-and-Shoulders Pattern in U.S.
  Equities.* FRBNY Staff Report 42.
  https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr42.pdf ·
  [`../pdf/osler-1998-head-and-shoulders-us-equities-frbny-sr42.pdf`](../pdf/osler-1998-head-and-shoulders-us-equities-frbny-sr42.pdf)
- Chang, P.H.K. & Osler, C. (1999). *Methodical Madness.* *Economic Journal* 109(458),
  636–661. https://doi.org/10.1111/1468-0297.00466 · FRBNY SR4 (hasil scan):
  [`../pdf/chang-osler-1995-head-and-shoulders-not-just-a-flaky-pattern-frbny-sr4.pdf`](../pdf/chang-osler-1995-head-and-shoulders-not-just-a-flaky-pattern-frbny-sr4.pdf)
- Savin, G., Weller, P. & Zvingelis, J. (2007). *The Predictive Power of
  "Head-and-Shoulders" Price Patterns in the U.S. Stock Market.* *JFEc* 5(2), 243–265.
  https://doi.org/10.1093/jjfinec/nbl012
- Lo, A., Mamaysky, H. & Wang, J. (2000). *Foundations of Technical Analysis.* *JoF*
  55(4). https://doi.org/10.1111/0022-1082.00265 ·
  [`../pdf/lo-mamaysky-wang-2000-foundations-of-technical-analysis.pdf`](../pdf/lo-mamaysky-wang-2000-foundations-of-technical-analysis.pdf)
- Ha, S. & Moon, B.-R. (2017). *The Evolution of Neural Network-Based Chart Patterns.*
  GECCO '17. https://doi.org/10.1145/3071178.3071192 · https://arxiv.org/abs/1706.05283
- Marshall, B., Cahan, R. & Cahan, J. (2008). *Technical Analysis Around the World.*
  https://doi.org/10.2139/ssrn.1181367
- Marshall, B., Cahan, R. & Cahan, J. (2008). *Can commodity futures be profitably traded
  with quantitative market timing strategies?* *JBF*.
  https://doi.org/10.1016/j.jbankfin.2007.12.011
- Bajgrowicz, P. & Scaillet, O. (2012). *Technical trading revisited.* *JFE* 106(3).
  https://doi.org/10.1016/j.jfineco.2012.06.001
- Tsinaslanidis, P. & Guijarro, F. (2021). *What makes trading strategies based on chart
  pattern recognition profitable?* *Expert Systems* 38(5).
  https://doi.org/10.1111/exsy.12596 ·
  [`../pdf/tsinaslanidis-guijarro-2021-what-makes-chart-pattern-strategies-profitable.pdf`](../pdf/tsinaslanidis-guijarro-2021-what-makes-chart-pattern-strategies-profitable.pdf)
- Cervelló-Royo, R., Guijarro, F. & Michniuk, K. (2015). *Stock market trading rule based
  on pattern recognition and technical analysis.* *ESWA* 42(14).
  https://doi.org/10.1016/j.eswa.2015.03.017 — teks lengkap diblokir (RiuNet HTTP 401);
  isinya tercakup di disertasi Michniuk di bawah
- Michniuk, K. (2017). Disertasi, UPV. https://doi.org/10.4995/thesis/10251/78837 ·
  [`../pdf/michniuk-2017-pattern-recognition-applied-to-chart-analysis-thesis.pdf`](../pdf/michniuk-2017-pattern-recognition-applied-to-chart-analysis-thesis.pdf)
- Ben Omrane, W. & Van Oppens, H. (2006). *Empirical Economics* 30(4), 947–971.
  https://doi.org/10.1007/s00181-005-0007-8
- Osler, C. (2000). *Support for Resistance: Technical Analysis and Intraday Exchange
  Rates.* FRBNY *Economic Policy Review*, Juli 2000, 53–68.
  https://www.newyorkfed.org/medialibrary/media/research/epr/00v06n2/0007osle.pdf ·
  [`../pdf/osler-2000-support-for-resistance-intraday-exchange-rates-frbny.pdf`](../pdf/osler-2000-support-for-resistance-intraday-exchange-rates-frbny.pdf)
- Jiang, J., Kelly, B. & Xiu, D. (2023). *(Re-)Imag(in)ing Price Trends.* *JoF* 78(6).
  https://doi.org/10.1111/jofi.13268
- Cohen, N., Balch, T. & Veloso, M. (2020). *Trading via Image Classification.* ICAIF '20.
  https://doi.org/10.1145/3383455.3422544 · https://arxiv.org/abs/1907.10046 ·
  [`../pdf/cohen-2020-trading-via-image-classification.pdf`](../pdf/cohen-2020-trading-via-image-classification.pdf)
- Chen, J.-H. & Tsai, Y.-C. (2020). *Encoding candlesticks as images for pattern
  classification using CNN.* *Financial Innovation*.
  https://doi.org/10.1186/s40854-020-00187-0 · https://arxiv.org/abs/1901.05237
- Velay, M. & Daniel, F. (2018). *Stock Chart Pattern recognition with Deep Learning.*
  https://arxiv.org/abs/1808.00418 (**preprint, tidak pernah peer-review**)
- Tsai, Y.-C., Chen, J.-H. & Wang, C.-C. (2018). https://arxiv.org/abs/1801.03018
- Arévalo, R., García, J., Guijarro, F. & Peris, A. (2017). *A dynamic trading rule based
  on filtered flag pattern recognition.* *ESWA* 81, 177–192.
  https://doi.org/10.1016/j.eswa.2017.03.028 — **abstrak dan teks lengkap tidak bisa
  diambil**; temuannya tidak terverifikasi
- Psaradellis, I., Laws, J., Pantelous, A. & Sermpinis, G. (2023). *International Journal
  of Forecasting* 39(1), 178–191. https://doi.org/10.1016/j.ijforecast.2021.10.002

**Tingkat (b) — konsensus praktisi tanpa data**

- Bulkowski, T. *Encyclopedia of Chart Patterns.* https://thepatternsite.com/top10.html —
  tidak ada di catatan peer-review; lihat §4 di [04](04-pola-candlestick.md).
- LuxAlgo, *Order Block Anatomy and Refinement* —
  https://www.luxalgo.com/library/concept/order-block-anatomy-and-refinement/
- LuxAlgo, *Pivot Strength* — https://www.luxalgo.com/library/concept/pivot-strength/
- Al Brooks, *Trading Price Action Trading Ranges* (bab Wiley ber-DOI, bukan riset)

**Yang harus dibuang (c)**

- DOI 10.1109/C2I666499.2025.11366955 — abstraknya ditulis **dalam bentuk waktu depan**
  dan menyatakan kesimpulannya sebelum eksperimennya: *"metode yang diusulkan **akan**
  dinilai... membuat metode yang diusulkan andal dan akurat."* Tidak ada dataset, tidak
  ada metrik.
- DOI 10.5753/eniac.2025.12471 — melaporkan metrik **pada training set** menurut caption
  tabelnya sendiri, dan bibliografinya memuat **setidaknya dua referensi fabrikasi** —
  tanda daftar referensi yang dibangkitkan LLM.
- DOI 10.36774/sisiti.v14i2.1744 — satu-satunya studi pola chart XAUUSD yang ditemukan;
  prosiding seminar lokal, "28,57%" hampir pasti 2 dari 7, tanpa biaya, tanpa spread,
  tanpa uji signifikansi.
- DOI 10.1109/BTS-I2C67944.2025.11399400 — melaporkan **F1 > 0,99** lewat stratified
  5-fold CV pada jendela geser yang tumpang tindih. Jendela *t* dan *t+1* berbagi ~99%
  bar yang sama. **F1 >0,99 adalah bukti kebocoran, bukan keterampilan.**
- DOI 10.14357/19922264220304 — Crossref tidak mencatat penulis sama sekali.
