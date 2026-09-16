# Stop loss

---

## 1. Teori yang seharusnya mengarahkan keputusan

Dua hasil menyelesaikan lebih banyak daripada aturan ATR mana pun.

### Kaminski & Lo (2014), *Journal of Financial Markets* **(a)**

Kutipan langsung dari teks:

> *"Kami menunjukkan bahwa di bawah proses pembangkit return yang paling umum, Hipotesis
> Random Walk, **stopping premium selalu negatif**."*

> *"jika return portofolio dicirikan oleh 'momentum' atau korelasi serial positif, kami
> menunjukkan bahwa stopping premium bisa positif dan **berbanding lurus dengan besarnya
> persistensi return**."*

> *"untuk strategi mean-reversion, ρ < 0; karenanya, **kebijakan stop-loss merugikan
> expected return**."*

**Syarat cukup agar stop menambah nilai di bawah momentum-AR(1):**

```
ρ/(1−ρ) > SR
```

Autokorelasi harus melampaui Sharpe ratio, **dikalibrasi pada frekuensi sampling yang
sama dengan ρ**. Makalahnya mencatat Sharpe tahunan 1,00 berarti Sharpe bulanan 0,29,
jadi rintangannya lebih rendah daripada yang terlihat.

**Dan temuan yang tidak pernah dikutip konten ritel — verbatim:**

> *"kebijakan stop-loss jangka lebih pendek, frekuensi lebih rendah punya **stopping
> premium negatif di rentang parameter yang luas**. Stop-loss jangka lebih panjang pada
> frekuensi di atas satu bulan berkinerja lebih baik dan bisa mencapai stopping premium
> positif."*

Horizon tempat stop mereka *bekerja* adalah bulanan dan kuartalan. **Jendela 3, 5, dan 10
hari — analog terdekat dengan intraday — menghasilkan stopping premium NEGATIF di
rentang parameter yang luas.**

**Makalah yang paling sering dikutip untuk membenarkan stop-loss tidak mendukungnya di
timeframe kita.**

Dua detail berguna lagi: **ambang exit mendominasi ambang re-entry** dalam menjelaskan
variasi hasil; dan **biaya transaksi diset nol sepanjang makalah** (τ = 0).

Peringatan yang harus dinyatakan: "stop-loss" mereka adalah aturan de-risking kerugian
kumulatif tingkat portofolio, bukan stop order per trade. Pemindahan ke stop per-trade
M15 terbatas. Tapi arahnya — **stop hanya membayar di bawah momentum, dan tidak membayar
di horizon pendek** — adalah sinyal yang relevan.

### Carr & López de Prado (2014) — dan ini membalik dogma ritel **(a)**

*Determining Optimal Trading Rules Without Backtesting.* Monte Carlo atas mesh 21×21
pasangan (profit-taking, stop-loss) pada proses Ornstein-Uhlenbeck diskret, **100.000
jalur**, dengan periode penahanan maksimum:

> *"aturan trading optimal adalah menahan inventori cukup lama sampai muncul profit
> kecil, **bahkan dengan risiko mengalami kerugian 5 atau 7 kali lipat**. Sharpe ratio
> tinggi, mencapai tingkat sekitar 3,2… **Aturan trading terburuk yang mungkin dalam
> setting ini adalah menggabungkan stop-loss pendek dengan ambang profit-taking besar.**"*

Dan, kritis, saat half-life tumbuh menuju random walk: *"tidak ada area yang bisa dikenali
di mana kinerja bisa dimaksimalkan… **Mengkalibrasi aturan trading pada random walk lewat
simulasi historis akan menghasilkan backtest overfitting.**"*

### Sintesis — aturan keputusan untuk V6

| Rezim di horizon kita | Geometri yang benar | Trailing? |
|---|---|---|
| Momentum / tren | Stop rapat, target lebar | Ya |
| Mean-reverting | **Stop lebar, target rapat** | Tidak |
| Random walk | Semua geometri identik; setiap tuning adalah overfitting | — |

**Bentuk SL/TP optimal bukan konstanta universal. Ia adalah fungsi dari proses pembangkit
return yang bisa kita tunjukkan ada di horizon kita.** Kalau kita tidak bisa menetapkan
bahwa emas M15 bersifat momentum atau mean-reverting di jendela sesi kita, maka
mengoptimalkan SL/TP di backtest adalah memasang noise — dan temuan Jin yang disesuaikan
snooping bilang jawaban jujur untuk emas intraday mungkin memang "random walk."

## 2. Kelipatan ATR — apa yang sebenarnya ditetapkan

**Tidak ada studi spesifik emas yang menetapkan 1,0×, 1,5×, atau 2,0× ATR(14) di M15.**
Saya mencari. Kelipatan konvensional turun dari karya Wilder asli dan praktik
trend-following umum, dan itu **(b)** paling banter.

Yang **ditetapkan** adalah **lantai biaya**, yang menghasilkan kelipatan ATR dari prinsip
pertama alih-alih dari cerita rakyat.

Biaya sebagai fraksi R bergantung **hanya pada jarak stop**, bukan pada R:R:

| Jarak stop | Points | Biaya sbg % R (26 pts bolak-balik) |
|---|---|---|
| $2,00 | 200 | **13,0%** |
| $3,00 | 300 | 8,7% |
| $6,00 | 600 | **4,3%** |
| $9,00 | 900 | 2,9% |
| $12,00 | 1200 | 2,2% |

Win rate impas terkoreksi adalah `W* = (1+c)/(1+R)`. Pada R=2, drag biaya 13% memindahkan
impas dari 33,3% ke **37,7%**; pada 2,9% ia pindah ke 34,3%.

Mengingat edge intraday terbaik yang dipublikasikan adalah **0,13–0,18R kotor**, **stop
yang memakan 0,13R dalam friksi sudah menghabiskan seluruh edge realistis sebelum trade
dimulai.**

**Lantai desain: stop tidak boleh lebih rapat dari ~600 points ($6,00)**, yang pada ATR
M15 emas saat ini kira-kira **0,8–1,2× ATR**. Itu justifikasi independen berbasis biaya
untuk mengapa ~1 ATR adalah lantainya — bukan rule of thumb.

Satu jangkar kalibrasi **(a)** dengan metodologi terbuka yang layak dicatat: ORB
stocks-in-play Zarattini/Barbon/Aziz memakai **stop di 10% dari ATR 14-hari**. Untuk emas
dengan ATR harian mendekati $100, itu ~$10 — mendarat di zona yang sama.

## 3. Struktural vs jarak tetap

Pakai `max(invalidasi struktural, lantai ATR)`.

- **Level struktur** memberi tahu *di mana ide kita salah*.
- **Lantai ATR** memberi tahu *di mana trade berhenti ekonomis*.

**Ketika stop struktural lebih rapat daripada lantai ATR/biaya, tindakan yang benar adalah
melewati trade atau mengurangi ukuran — bukan merapatkan stop.**

## 4. Buffer di atas level struktural

### Mekanik MT5 yang hampir semua orang lewatkan

**Chart menampilkan bid.** SL long terpicu saat **Bid ≤ SL**, jadi stop long di swing low
chart terpicu persis di tempat chart menunjukkannya. SL short terpicu saat **Ask ≥ SL** —
dan Ask = Bid + spread. **Short yang stopnya ditaruh persis di swing high chart akan
menyala saat bid masih satu spread penuh di bawah high itu.**

**Karena itu short butuh setidaknya +spread (19–30 points, $0,19–0,30) buffer murni untuk
mencapai paritas dengan long. Kalau EA kita tidak melakukan ini, ia punya kebocoran sisi
short yang asimetris dan tidak terdiagnosis.**

### Buffer stop-hunt — dan buktinya bilang sesuatu yang berbeda dari cerita rakyat

Osler, *Stop-Loss Orders and Price Cascades in Currency Markets* **(a)**. Order book
lengkap **Royal Bank of Scotland, 1 Agu 1999 – 11 Apr 2000, 9.655 order, >$55 miliar nilai
nominal**, USD/JPY, GBP/USD, EUR/USD; plus kuotasi menit-per-menit Jan 1996 – Apr 1998,
metodologi bootstrap. Temuan persis:

- Stop-loss order = **43% dari semua order berdasarkan volume, 45% berdasarkan nilai**
- **~10%** order ditempatkan di rate berakhiran **"00"**; ~3% di setiap rate berakhiran
  "0" lainnya; ~2% di setiap yang berakhiran "5"
- Stop-loss **buy** yang tereksekusi mengelompok **tepat di atas** angka bulat: **14,3%**
  di rentang [01,10] vs hanya **6,9%** di [90,99]. Stop-loss sell mengelompok tepat di
  bawah.
- Take-profit order mengelompok **di** angka bulat dan membangkitkan umpan balik *negatif*
- Hasilnya signifikan secara statistik **selama berjam-jam, bukan berhari-hari**

**Baca ini hati-hati, karena narasi ritel membalikkannya.** Bukti Osler adalah bahwa
klaster stop **MEMPERPANJANG TREN** — harga berakselerasi *menembusnya*. Itu bukan bukti
untuk "stop hunt" spike-lalu-berbalik. Dan itu bukan bukti aktor jahat; kaskadenya
mekanis.

**Dua aturan desain yang mengikuti:**

1. **Stop:** jangan tempatkan di, atau tepat melewati, angka bulat ke arah yang jelas.
   Di situlah klasternya, dan kaskade berarti kita dapat fill buruk saat menembusnya.
2. **Target:** tempatkan **tepat sebelum** angka bulat. Di situlah klaster take-profit
   dan tekanan mean-reverting-nya berada.

Osler (2003), order book bank dealing FX besar, **~9.700 stop-loss dan take-profit order**,
1 Sep 1999 – 11 Apr 2000, memberi mekanismenya:

> *"**9,3 persen** take-profit order tereksekusi persis di 00, sementara persentase yang
> sesuai untuk stop-loss order hanya **4,4**."*

Take-profit mengelompok lebih keras di angka bulat → mereka membalikkan tren di level itu.
Stop-loss terdistribusi berbeda → mereka mengintensifkan tren begitu level ditembus.

### Angka bulat emas — dan mengapa mereka berhenti penting

O'Connor & Lucey, *Mind The Gap: Psychological Barriers in Gold and Silver Prices*,
*Finance Research Letters* 17(C) 2016, 135–140, data intraday **1975–2015** **(a)**:
barrier signifikan secara statistik di **emas** pada harga berakhiran **0 dan 00**; tidak
ada bukti signifikan untuk perak.

**Tapi terapkan penskalaan harga.** Pada $450, kenaikan $10 adalah **2,2% harga** — gerakan
multi-hari. Pada **$4.285, $10 hanya 0,23% harga**, dan level $10 lewat kira-kira dua kali
per bar M15. Efek digitnya mungkin bertahan; signifikansi ekonominya tidak.

**Pada harga sekarang hanya kelipatan $50 dan $100 yang masih berguna sebagai landmark.**

## 5. "Berapa persen stop kena hunt lalu harga lanjut?"

**Tidak ada studi MAE sampel besar untuk emas atau FX intraday yang dipublikasikan.** Saya
mencari; agen-agen mencari. Metodologi Maximum Adverse Excursion Sweeney (*Campaign
Trading*) meresepkan pengukurannya tapi tidak menerbitkan distribusi emas. **Setiap artikel
yang mengutip persentase di sini tidak punya sumber primer.**

Kuantifikasi tidak langsung terbaik adalah hasil futures emas MPM: menyelesaikan urutan
sentuhan secara jujur pada data 1 menit menurunkan win rate tercatat dari **~73% ke ~50%**
dan profit factor dari **2,4 ke 1,0**. Itu menyiratkan di sekitar **23 poin persentase**
kasus, harga menyentuh stop *dan* target di dalam bar yang sama, dan backtest OHLC M15
naif diam-diam memilih yang menguntungkan.

Itu bukan "% stop yang di-hunt lalu berbalik", tapi itu pengukuran nyata atas besaran
fenomena ini di instrumen dan timeframe persis kita — **dan besar.**

**Yang bisa dilakukan:** ini tidak bisa dijawab dari literatur. **Instrumentasi EA untuk
mencatat MAE dan MFE per trade dalam satuan ATR, dan bangun distribusi sendiri.** Itu
beberapa minggu data dan nilainya melebihi setiap artikel tentang subjek ini.

## 6. Bukti terkuat bahwa stop BISA menambah nilai — dan mengapa ia tidak berlaku bagi kita

Han, Zhou & Zhu, *Taming Momentum Crashes: A Simple Stop-Loss Strategy* **(a)**. Saham
AS, **Januari 1926 – Desember 2013**. Stop 15% pada saham individual di dalam portofolio
momentum.

| Metrik | Momentum | + stop 10% |
|---|---|---|
| Bulan terburuk, equal-weighted | −49,79% | **−11,36%** |
| Bulan terburuk, value-weighted | −64,97% | **−23,28%** |
| Return bulanan rata-rata | 0,99% | **1,69%** |
| Std dev bulanan | 6,01% | **4,58%** |
| **Sharpe** | **0,165** | **0,369** |

Biaya transaksi impas: **4,00%** (return rata-rata sama), **4,82%** (Sharpe sama) — ambang
yang absurd tingginya, jadi hasilnya bertahan terhadap biaya realistis.

**Ini tidak bertentangan dengan Kaminski & Lo — ia mengonfirmasi Proposisi 2.** Momentum
justru kasus korelasi serial positif. Stop-nya bekerja karena saham momentum yang kalah
terus kalah: dependensi jalur yang asli.

**Tapi ini strategi ekuitas cross-sectional yang di-rebalance bulanan dengan pemeriksaan
stop harian. Mekanismenya adalah memotong ekor kiri strategi ber-skew negatif.
Mekanismenya bisa digeneralisasi; angkanya tidak bisa dipindahkan ke instrumen tunggal
M15.** Hasil ini rutin disalahkutip di konten ritel sebagai bukti bahwa stop intraday
per-trade bekerja. Bukan.

Dan mereka membuktikan padanan teoretisnya: *"harga pasar berbeda dari harga dalam kasus
tanpa trader stop-loss sebesar **sebuah barrier option**."* **Itu pernyataan formal bahwa
exit stop-loss adalah transformasi payoff opsi barrier — pembentukan ulang distribusi,
bukan sumber alpha.**

## 7. Bukti tandingan terkuat: Clare, Seaton, Smith & Thomas (2013) **(a)**

S&P500 harian, **Juli 1988 – Juni 2011**, **biaya transaksi 0,2% diterapkan**.

Sapuan kerapatan trailing stop persentase pada strategi breakout 200 hari:

| Trailing stop | 3% | 5% | 7% | 10% | 12% | 15% |
|---|---|---|---|---|---|---|
| Return tahunan | 3,02% | 4,47% | 6,82% | 9,52% | 10,13% | 9,61% |
| Volatilitas tahunan | 6,83% | 8,71% | 9,77% | 11,08% | 11,70% | 11,91% |
| **Sharpe** | **−0,11** | **0,08** | **0,31** | **0,52** | **0,54** | **0,49** |

**Degradasi monoton saat stop dirapatkan.** Dan stop terbaik (12%, Sharpe 0,54) tetap
**tidak mengalahkan aturan tren tanpa stop**: strategi MA 12 bulan end-of-month berjalan
di >11,00% return, **Sharpe 0,58** atas 1952–2011.

Kesimpulan verbatim: **"tidak ada nilai dalam aturan stop loss"** dan **"perubahan tren
itu sendiri adalah aturan stop-loss terbaik."**

Ini tabel paling relevan untuk keputusan yang saya temukan, karena ia adalah sapuan
kerapatan trailing-stop yang bersih dengan biaya disertakan, dan ia bilang **makin rapat
makin buruk.**

---

## Apa yang harus dikodekan

1. **`SL = max(invalidasi_struktural, lantai_ATR, lantai_biaya)`.** Kalau struktur lebih
   rapat dari lantai, **lewati trade atau kecilkan lot — jangan rapatkan stop.**
2. **`jarak_stop ≥ 600 points`** sebagai lantai desain; `≥ 220 points` minimum absolut.
3. **`jarak_stop ≥ 10 × spread saat ini`.**
4. **Short dapat buffer +spread tambahan.** Tanpa ini ada kebocoran sisi short sistematis.
5. **Jangan taruh stop di atau tepat melewati kelipatan $50/$100.** Taruh target tepat
   sebelumnya.
6. **Catat MAE/MFE dalam satuan ATR di setiap trade.** Satu-satunya cara menjawab
   pertanyaan stop-hunt, dan satu-satunya cara menemukan di mana drift meluruh.
7. **Ukur ρ (autokorelasi) per jendela sesi pada data kita sendiri** sebelum memutuskan
   geometri stop. Ambangnya: stop menambah nilai di bawah momentum ketika `ρ/(1−ρ) > SR`.

---

## Referensi

**Tingkat (a) — kuantitatif, metodologi terbuka**

- Kaminski, K. & Lo, A. (2014). *When do stop-loss rules stop losses?*
  *Journal of Financial Markets* 18, 234–254.
  https://doi.org/10.1016/j.finmar.2013.07.001 · preprint teks lengkap:
  https://www.smallake.kr/wp-content/uploads/2017/02/When_Do_Stop-Loss_Rules_Stop_Losses.pdf ·
  ekstrak lokal: [`../pdf/extract-kaminski_lo.txt`](../pdf/extract-kaminski_lo.txt)
- Carr, P. & López de Prado, M. (2014). *Determining Optimal Trading Rules Without
  Backtesting.* https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2658641
- Clare, A., Seaton, J., Smith, P. & Thomas, S. (2013). *Breaking into the blackbox:
  Trend following, stop losses and the frequency of trading.* *Journal of Asset
  Management* 14(3), 182–194. https://doi.org/10.1057/jam.2013.11 · OA:
  https://openaccess.city.ac.uk/id/eprint/17842/8/BLACKBOX%20%20%20SSRN-id2126476.pdf
- Han, Y., Zhou, G. & Zhu, Y. *Taming Momentum Crashes: A Simple Stop-Loss Strategy.*
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2407199 ·
  https://www.cicfconf.org/sites/default/files/paper_811.pdf ·
  ekstrak lokal: [`../pdf/extract-han_zhou_zhu.txt`](../pdf/extract-han_zhou_zhu.txt)
- Lei, A. & Li, H. (2009). *The Value of Stop Loss Strategies.* *Financial Services
  Review* 18(1), 23–51. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1214737 ·
  ekstrak lokal: [`../pdf/extract-lei_li.txt`](../pdf/extract-lei_li.txt)
- Lo, A. & Remorov, A. (2017). *Stop-loss strategies with serial correlation, regime
  switching, and transaction costs.* *JFM* 34, 1–15.
  https://doi.org/10.1016/j.finmar.2017.02.003 — **hanya abstrak yang bisa diakses;
  target pengambilan prioritas tinggi.**
- Dai, M., Marshall, B., Nguyen, N. & Visaltanachoti, N. (2020). *Risk reduction using
  trailing stop-loss rules.* *International Review of Finance* 21(4), 1334–1352.
  https://doi.org/10.1111/irfi.12328
- Osler, C. (2005). *Stop-loss orders and price cascades in currency markets.* *JIMF*
  24(2), 219–241. https://doi.org/10.1016/j.jimonfin.2004.12.002 · FRBNY SR150:
  [`../pdf/osler-2005-stop-loss-orders-and-price-cascades-frbny-sr150.pdf`](../pdf/osler-2005-stop-loss-orders-and-price-cascades-frbny-sr150.pdf)
- Osler, C. (2003). *Currency Orders and Exchange Rate Dynamics.* *Journal of Finance*
  58(5). https://doi.org/10.1111/1540-6261.00588 · FRBNY SR125:
  [`../pdf/osler-2003-currency-orders-and-exchange-rate-dynamics-frbny-sr125.pdf`](../pdf/osler-2003-currency-orders-and-exchange-rate-dynamics-frbny-sr125.pdf)
- O'Connor, F. & Lucey, B. (2016). *Mind The Gap: Psychological Barriers in Gold and
  Silver Prices.* *Finance Research Letters* 17(C), 135–140.
  https://ray.yorksj.ac.uk/id/eprint/1438/
- MPM Markets. *Does the Fair Value Gap strategy work?* — sumber untuk kolaps
  73%→50% win rate dari look-ahead intrabar.
  https://mpmmarkets.com/research/does-the-fair-value-gap-strategy-work

**Tingkat (b)**

- Sweeney, J. *Campaign Trading* — metodologi Maximum Adverse Excursion. Meresepkan
  pengukurannya, tidak menerbitkan distribusi emas.
- StockCharts ChartSchool, *Chandelier Exit* —
  https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/chandelier-exit
  **Catatan provenance:** StockCharts memberi "highest high 22 bar − 3 × ATR(22)" dan
  mengatribusikannya ke Charles LeBeau, tapi **tidak menyatakan di mana LeBeau
  mempublikasikannya semula dan tidak memberi tanggal.** Spesifikasi primernya tidak bisa
  diverifikasi (traderclub.com hanya ada di web.archive.org, yang diblokir). **Perlakukan
  parameter 22/3,0 sebagai konvensi pustaka indikator modern, bukan spesifikasi LeBeau
  yang terverifikasi.**

**Yang harus dibuang (c)**

- Setiap artikel yang mengutip "X% stop kena hunt lalu harga lanjut" — tidak ada sumber
  primer untuk statistik ini di pasar mana pun.
- Setiap kelipatan ATR spesifik untuk emas M15 yang disajikan sebagai hasil terukur.
