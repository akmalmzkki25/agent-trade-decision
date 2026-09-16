# Sentimen dan posisi

Indikator sentimen dan positioning untuk emas — mana yang punya daya prediksi terhadap
arah harga, pada horizon berapa, dan mana yang noise.

---

## 0. Jawaban di depan

1. **Tidak ada bukti peer-review bahwa posisi COT memprediksi return emas.** Bukan bukti
   lemah — praktis *tidak ada* bukti spesifik emas sama sekali. Dua makalah yang secara
   universal dikutip untuk "sentimen COT memprediksi return" (Wang 2001, 2003) memuat
   **nol logam mulia**. Uji dengan identifikasi terbaik di komoditas (Sanders/Irwin/Merrin)
   tegas negatif.
2. **Sentimen ritel sebagai sinyal kontrarian tidak didukung, dan tandanya kemungkinan
   terbalik.** Bukti modern terbaik menemukan order imbalance ritel *positif* memprediksi
   return, dan makalah JFQA 2023 menunjukkan long/short beraroma kontrarian rugi 14,8%
   per tahun di tempat perdagangan ritel paling padat.
3. **Sentimen berita menggerakkan emas secara kontemporer, bukan prediktif.** Kata Smales
   sendiri adalah "contemporaneous."
4. **Hubungan emas/suku bunga riil jauh lebih lemah daripada cerita rakyat, dan memang
   selalu lebih lemah.** Angka terkenal −0,82 adalah jendela 15 tahun hanya AS; di luar
   sampel ia −0,31. Model WGC sendiri sekarang mengatribusikan **3%** variabilitas harga
   H1 2026 ke suku bunga melawan **30% tak terjelaskan**.
5. **Pada pertanyaan kunci — tidak.** Tidak ada ukuran sentimen dengan daya prediksi
   **arah** yang terbukti untuk emas pada skala menit-sampai-jam. Yang *bekerja*
   intraday adalah hal yang berbeda: data sentimen dan atensi memprediksi **volatilitas
   dan probabilitas lompatan**, bukan arah. Itu input risiko, bukan sinyal.

---

## 1. CFTC Commitments of Traders

### 1.1 Isi laporannya

Definisi CFTC verbatim dari *Disaggregated Explanatory Notes* **(a)**:

| Kategori | Definisi CFTC |
|---|---|
| **Producer/Merchant/Processor/User** | "Entitas yang terutama terlibat dalam produksi, pemrosesan, pengemasan, atau penanganan komoditas fisik dan memakai pasar futures untuk mengelola atau melindungi risiko yang terkait dengan aktivitas itu." |
| **Swap Dealer** | "Entitas yang terutama berurusan dalam swap untuk komoditas dan memakai pasar futures untuk mengelola atau melindungi risiko yang terkait dengan transaksi swap itu." |
| **Managed Money** | "Commodity trading advisor (CTA) terdaftar; commodity pool operator (CPO) terdaftar; atau dana tak terdaftar yang diidentifikasi CFTC." |
| **Other Reportables** | "Setiap trader reportable lain yang tidak ditempatkan di tiga kategori lain." |

**Peringatan CFTC sendiri yang jarang dikutip blog trading:**

> *"penempatan aktual seorang trader dalam klasifikasi tertentu berdasarkan aktivitas bisnis
> utamanya mungkin melibatkan sejumlah penilaian."*

CFTC mengklasifikasikan **trader, bukan aktivitas trading**. Bank bullion yang menjalankan
buku komersial dan buku swap sekaligus dimasukkan **sekali**, menurut aktivitas utama.
**Kategorinya tidak bersih.**

### 1.2 Jadwal rilis dan lag pelaporan

Verbatim dari jadwal rilis CFTC **(a)**: laporan "dirilis pukul 3:30 sore waktu Timur,"
"biasanya dirilis hari Jumat," dan "Rilis biasanya memuat data dari hari Selasa
sebelumnya."

**Aritmetika kebasian lebih penting daripada apa pun di bagian ini:**

| Ukuran | Nilai |
|---|---|
| Umur data saat rilis | **3 hari** |
| Umur tepat sebelum rilis berikutnya | **10 hari** |
| Rata-rata umur selama jadi cetakan hidup | **~6,5 hari** |
| Update per tahun | **~52** |

### 1.3 Posisi emas live (dari API publik CFTC)

COMEX Gold, disaggregated futures-only, per snapshot Selasa **8 Sep 2026** (cetakan
tersegar yang tersedia, umur 8 hari):

| Per (Sel) | Open interest | Managed money net | Producer/merchant net | Swap dealer net |
|---|---|---|---|---|
| 2026-09-08 | 411.227 | +134.972 | −30.961 | −239.313 |
| 2026-09-01 | 415.196 | +136.771 | −31.289 | −233.429 |
| 2026-08-25 | 427.957 | +144.747 | −34.558 | −245.027 |
| 2026-08-18 | 406.260 | +141.648 | −29.761 | −228.657 |
| 2026-08-11 | 400.309 | +137.662 | −27.935 | −224.705 |
| 2026-08-04 | 371.551 | +130.766 | −18.856 | −207.635 |
| 2026-07-28 | 384.603 | +119.795 | −20.549 | −191.760 |
| 2026-07-07 | 371.776 | +116.161 | −20.986 | −201.296 |

Pada $4.300/oz dan 100 oz/kontrak, total OI emas COMEX ≈ **$177 miliar** notional;
managed money net long ≈ **$58 miliar**; swap dealer net short ≈ **−$103 miliar**.

**Perhatikan strukturnya.** Di emas, short dominan adalah kategori **swap dealer**, bukan
producer/merchant. **Struktur COT emas tidak menyerupai pasar pertanian yang menjadi dasar
seluruh literatur akademis.** Intuisi apa pun yang diimpor dari jagung atau gandum
mengimpor mikrostruktur pasar yang salah.

### 1.4 Yang TIDAK dicakup COT — menentukan bagi trader XAUUSD

Laporan CFTC mencakup **"futures dan options di designated contract market AS"**. Artinya:

- **Hanya futures emas COMEX.**
- **Bukan** spot OTC London, **bukan** LBMA, **bukan** buku XAUUSD broker kita.

XAUUSD yang diperdagangkan EA ritel adalah instrumen spot OTC. COT memberi tahu tentang
venue yang terkait tapi berbeda. Ada kaitan ekonomi yang asli — Sehgal, Sobti & Diesting
(2021) menemukan futures emas memimpin price discovery intraday secara global — **tapi data
posisinya tidak mendeskripsikan instrumen yang kita perdagangkan.**

### 1.5 Bukti akademis — apa yang sebenarnya dikatakan

#### Wang (2001) — makalah yang dikutip semua orang **(a)**

*Investor Sentiment and Return Predictability in Agricultural Futures Markets.* *Journal of
Futures Markets* 21(10), 929–952.

Indeks sentimennya persis "COT Index" trader:

```
SI = (S − min(S)) / (max(S) − min(S))
```

di mana S adalah posisi agregat (long OI dikurangi short OI) yang di-detrend dengan total
open interest, dan max/min diambil atas **tiga tahun sebelumnya**.

**Pasar: jagung, kedelai, bungkil kedelai, gandum, kapas, gula dunia. Sampel: Januari 1993 –
Maret 2000.** Saya mencari di teks lengkapnya: kata *gold*, *silver*, *copper*, dan
*metal* **tidak muncul sekali pun.**

Temuan utama, verbatim dari abstrak:

> *"sentimen large speculator memprediksi kelanjutan harga. Sebaliknya, sentimen large
> hedger memprediksi pembalikan harga. Sentimen small trader nyaris tidak memprediksi
> pergerakan pasar masa depan... tampaknya large speculator di pasar futures **tidak
> memiliki kemampuan prediksi superior apa pun**."*

Dua hal yang perlu ditekankan:

1. **Arahnya kebalikan dari versi blog trading.** Blog bilang "ikuti commercial, lawan
   spekulator." Wang menemukan sentimen spekulator adalah sinyal *kelanjutan* dan sentimen
   hedger adalah yang *berlawanan*. Ia menyatakannya eksplisit: *"Berlawanan dengan
   keyakinan populer, sentimen large hedger adalah indikator kontrarian."*
2. **Wang mengatribusikan hasilnya ke risk premium, bukan alpha** — teori tekanan hedging,
   hedger membayar spekulator untuk menanggung risiko. **Itu kompensasi karena menanggung
   risiko, bukan ramalan.**

**Tabel II Wang adalah tabel tunggal terpenting untuk tujuan kita.** t-statistik, horizon
tidak tumpang tindih:

| Portofolio pertanian | 2 mgg | 4 mgg | 6 mgg | 8 mgg | 12 mgg |
|---|---|---|---|---|---|
| Large speculator | (0,10) | (3,63) | **(4,28)** | (2,71) | (1,03) |
| Large hedger | (0,93) | (3,66) | **(3,86)** | (2,69) | (1,46) |
| Small trader | (0,77) | (1,15) | (0,86) | (1,20) | (0,07) |

**Pada horizon 2 minggu — terpendek yang diuji Wang — tidak satu pun dari 21 sel
pasar/trader individual mencapai signifikansi.** t-statistik terbesar di mana pun pada 2
minggu adalah 1,52. Prediktabilitas muncul di 4–6 minggu, memuncak di 6, dan sudah runtuh
di 12. **Kaki small-trader (proksi ritel) mati di setiap horizon.**

#### Wang (2003) — set pasar lebih luas, tetap tanpa emas **(a)**

*The behavior and performance of major types of futures traders.* *JFM* 23(1), 1–31.

15 pasar, verbatim: *"tiga finansial (S&P 500, T-bill, T-bond), empat pertanian (jagung,
kedelai, gandum, gula dunia), empat komoditas (kakao, kopi, minyak mentah, heating oil),
dan empat mata uang asing (pound Inggris, Deutsche mark, yen Jepang, franc Swiss)."*

**Tanpa logam mulia.** Nol penyebutan emas di teks lengkap.

Kesimpulan Wang lagi-lagi menyangkal keterampilan: strategi positioning *"secara signifikan
menguntungkan di sebagian besar pasar futures pertanian, komoditas, dan mata uang. Namun,
**kinerja superiornya tampaknya berasal dari efek tekanan hedging alih-alih kemampuan timing
superior** yang dimiliki spekulator."*

#### Sanders, Irwin & Merrin (2009) — uji negatif langsung **(a)**

*Smart Money: The Forecasting Ability of CFTC Large Traders in Agricultural Futures
Markets.* *Journal of Agricultural and Resource Economics* 34(2).

Abstrak verbatim:

> *"Uji kausalitas Granger bivariat menunjukkan **sangat sedikit bukti bahwa posisi trader
> berguna dalam memprediksi (mendahului) return** di 10 pasar futures pertanian. Namun,
> ada **bukti substansial bahwa trader merespons perubahan harga**. Khususnya, trader
> noncommercial menunjukkan kecenderungan trend following... Hasilnya **secara umum tidak
> mendukung penggunaan data COT dalam memprediksi pergerakan harga**."*

**Ini pernyataan masalah yang paling bersih. Kausalitas berjalan dari harga ke posisi,
bukan sebaliknya. COT adalah catatan tertinggal dari apa yang sudah dilakukan
trend-follower.**

#### Sanders, Irwin & Leuthold (1997) — indeks sentimen vendor gagal **(a)**

> *"Pertama, **tidak ada bukti bahwa sentimen noise trader menciptakan bias sistematis**
> dalam harga futures. Kedua, **return pasar yang bisa diprediksi memakai sentimen noise
> trader bukan karakteristik pasar futures** secara umum... Dalam kasus di mana ada bukti
> efek noise trader, itu **paling banter terbatas pada pasar terisolasi dan spesifikasi
> tertentu**."*

Klausa terakhir itu deskripsi buku teks tentang data mining, ditulis oleh penulisnya
sendiri.

#### Uji frekuensi harian — dan hasilnya ~2 basis poin **(a)**

Aulerich, Irwin & Garcia (2013), NBER WP 19065. Memakai data posisi harian **non-publik** —
jauh lebih baik daripada apa pun yang bisa kita akses. Hasil:

> *"Hipotesis nol tidak ada dampak posisi CIT agregat terhadap return harian **ditolak
> hanya di 3 dari 12 pasar**. Estimasi titik dampak kumulatif kenaikan satu standar deviasi
> posisi CIT terhadap return harian **negatif dan sangat kecil, rata-rata hanya sekitar dua
> basis poin**."*

**Dua basis poin, bertanda salah, dengan data yang lebih baik daripada yang akan pernah kita
punya.** Kalau efek hariannya 2bp, efek 5 menitnya tidak bisa diperdagangkan.

#### Satu makalah spesifik emas — yang tidak bisa dibaca

Chen, Yu-Lun & Mo, Wan-Shin (2023), *Determinants and dynamic interactions of trader
positions in the gold futures market*, *Journal of Commodity Markets* 31, 100343.
**Abstrak dan temuannya tidak bisa diambil** (Crossref tanpa abstrak; ScienceDirect 403).
Perhatikan judulnya: *determinan dari* posisi — yaitu posisi sebagai variabel dependen,
yang konsisten dengan cerita reaktif, tapi **saya tidak menyatakan itu.** Target tindak
lanjut teratas dari jaringan tak terblokir.

### 1.6 Putusan COT

| Pertanyaan | Jawaban |
|---|---|
| Bukti peer-review COT memprediksi return **emas**? | **Tidak ditemukan.** Makalah kanonik mengecualikan logam sepenuhnya |
| Bukti peer-review COT memprediksi return **komoditas**? | Lemah, horizon 4–8 minggu, dan penulis mengatribusikannya ke **risk premium**, bukan keterampilan. Sanders/Irwin menemukan praktis nol |
| Arah kausalitas? | **Harga → posisi.** Bukti substansial bahwa trader *merespons* harga |
| Bisa dipakai di EA intraday? | **Tidak.** ~52 update/tahun, lag 3–10 hari, venue berbeda, dan horizon terpendek yang pernah diuji (2 minggu) tidak menunjukkan apa pun |

---

## 2. Penggerak makro

### 2.1 Suku bunga riil — fondasi paling lemah dari semuanya

**Erb & Harvey, *The Golden Dilemma*, *Financial Analysts Journal* 69(4), 2013 (a).**

| Korelasi | Nilai | Sampel |
|---|---|---|
| Yield riil TIPS 10y vs harga emas riil, **AS** | **−0,82** | 1997–2012, akhir bulan |
| Yield riil vs harga emas riil, **UK** (sampel lebih panjang) | **−0,31** | awal 1980-an–2012 |
| Yield riil vs **tren waktu polos** | ≈ **−0,90** | 1997–2012 |
| Harga emas riil vs **tren waktu polos** | ≈ **+0,87** | 1997–2012 |

Putusan Erb & Harvey sendiri, verbatim: **"Yield riil rendah, misalnya pada TIPS, tidak
secara mekanis menyebabkan harga riil emas tinggi."** Mereka mencatat tren waktu polos
*lebih cocok* daripada cerita yield riil, dan membandingkan −0,82 dengan korelasi palsu
produksi-mentega-di-Bangladesh milik Leinweber.

**Ini membingkai ulang seluruh narasi "keruntuhan 2022–2026".** −0,82 adalah jendela
15 tahun, satu negara. Perpanjang sampelnya dan hubungannya jatuh ke −0,31 — sekitar 9%
varians. **Yang terlihat seperti patahan rezim mungkin sebagian besar adalah reversi ke
hubungan jangka panjang yang sebenarnya, yang lebih lemah.**

**Model WGC sendiri sekarang setuju.** Gold Return Attribution Model (GRAM), pangsa
variabilitas harga H1 2026:

| Faktor | Pangsa |
|---|---|
| Momentum | **24%** |
| Risiko & ketidakpastian | 17% |
| Biaya peluang — FX | 14% |
| Ekspansi ekonomi | 12% |
| **Biaya peluang — Suku bunga** | **3%** |
| **Lainnya / tak terjelaskan** | **30%** |
| *Total terjelaskan* | *70%* |

**Suku bunga: 3%. Tak terjelaskan: 30%.** Dan faktor teridentifikasi terbesar adalah
*momentum* — temuan refleksivitas, bukan makro.

Metodologi GRAM: "periode estimasi lima tahun memakai data bulanan." **Tidak ada spesifikasi
regresi, daftar variabel, koefisien, R², atau standard error yang dipublikasikan.**
**Nilai (b) — output kuantitatif, metode tak diungkap.**

Sensitivitas suku bunga yang dipublikasikan WGC: **"penurunan 25bp pada yield 10y AS setara
kenaikan 1,75%."**

### 2.2 Dolar AS — tidak ada beta yang dipublikasikan

Tidak ada satu pun elastisitas emas/DXY yang dipublikasikan dari sumber yang terjangkau.
Tabel sensitivitas WGC punya baris untuk suku bunga, inflasi, bank sentral, risiko
geopolitik, dan bea impor India — dan **tidak ada baris dolar**, meski FX adalah 14% dari
atribusi GRAM.

Satu jangkar peer-review: **Herley, Orlowski & Ritter (2024)**, *Economies* 12(9), 229 (a).
Hubungannya "konsisten berkebalikan" tapi **bergantung keadaan**: "lebih lemah di zona suku
bunga rendah, lebih kokoh di zona menengah, dan sangat kentara di zona tinggi."

**Kalau butuh beta dolar, kita harus mengestimasinya sendiri — dan mengestimasi ulang per
rezim.**

### 2.3 Pembelian bank sentral

| Tahun | Pembelian bersih |
|---|---|
| 2022 | 1.136t |
| 2023 | 1.050,8t |
| 2024 | 1.044,6t → direvisi **1.092,4t** |
| 2025 | **863,3t** (−21% y/y) |
| H1 2026 | **345t** (Q1 57t + Q2 289t) |

**Nilai (a) untuk tonase, dengan tiga peringatan kualitas data serius:**

1. **Cerita "1.000t+ per tahun" patah di 2025.** 863t di bawah 1.000t, dan 345t H1 2026
   adalah "terendah untuk paruh pertama sejak 2022" menurut WGC.
2. **Revisinya sangat besar.** Q1 2026 direvisi **dari 244t turun ke 57t — revisi −77%.**
   Jangan percaya angka kuartal terbaru sampai sudah berumur dua atau tiga kuartal.
3. **57% dari total 2025 adalah pembelian *tak dilaporkan*** (estimasi WGC). Deretnya
   sebagian besar diinferensikan model, bukan diamati.

Sensitivitas WGC: **"20t–30t setara kenaikan/penurunan 1%"** harga.

### 2.4 Aliran ETF — koinsiden, bukan prediktif

Saat ini: total kepemilikan global **4.189t (rekor), AUM $615 miliar**, inflow Agustus 2026
**$18 miliar/+121t**, YTD **+$29 miliar/+160t**.

**Arah kausalitas.** Bahasa WGC sendiri secara konsisten berjalan makro → harga → aliran.
Dari *Gold Demand Trends* Q2 2026, verbatim: *"Kemunduran harga adalah pemicu utama, sementara
sinyal hawkish dari Ketua Fed baru… yield riil yang naik, dan dolar yang lebih kuat, semuanya
menaikkan biaya peluang memegang emas."* GRAM memasukkan aliran ETF ke faktor **momentum** —
secara konstruksi label trend-following.

**Perbedaan kritis:** literatur akademis yang bisa diambil menguji **price discovery** ETF,
bukan **aliran**. Sehgal, Sobti & Diesting (2021) menemukan **"futures emas adalah pemimpin
global dalam price discovery dan volatility spillover."** **ETF tertinggal.**

**Apakah *aliran* ETF emas memprediksi return tampaknya benar-benar belum terjawab di
literatur yang dipublikasikan.** Celah itu sendiri adalah temuan.

### 2.5 Ekspektasi inflasi — **(a)**

Erb & Harvey lagi:

- Tentang inflasi tak terduga: **"Secara efektif tidak ada korelasi di sini. Hubungan
  positif apa pun yang teramati digerakkan oleh satu tahun saja, 1980."**
- Return emas nominal 10 tahun bergerak **−6% sampai +20% p.a.** sementara inflasi 10 tahun
  hanya bergerak **+2,3% sampai +7,3% p.a.** Tahu sempurna tentang inflasi tidak
  memprediksi return emas.
- **Return emas riil 10 tahun negatif dari 1988 sampai 2005** — kegagalan 17 tahun.
- Bonus safe-haven: **17% observasi bulanan saham/emas AS jatuh di kuadran sama-sama
  negatif.**

Angka tandingan WGC lebih lemah daripada kedengarannya: **"kenaikan CPI 1% berimplikasi
kenaikan 0,5%"** — beta inflasi 0,5, yaitu *separuh* lindung nilai.

### 2.6 Ringkasan makro

| Penggerak | Nilai | Temuan |
|---|---|---|
| Suku bunga riil / TIPS | (a) skeptis; (b) vendor | Nyata tapi jauh lebih lemah dari cerita rakyat. −0,82 rapuh; −0,31 out-of-sample. WGC mengatribusikan **3%** |
| Dolar AS | (b) saja | **Tidak ada beta yang dipublikasikan di mana pun.** Berkebalikan tapi bergantung keadaan |
| Bank sentral | (a) tonase; (b) survei | Data terbaik di sini, tapi revisi besar dan 57% tak dilaporkan. Era 1.000t **berakhir di 2025** |
| Aliran ETF | (b) | Koinsiden dan reaktif. Pertanyaan prediktif belum terjawab |
| Inflasi | (a) | Bukan lindung nilai yang andal pada horizon praktis |

---

## 3. Sentimen ritel sebagai indikator kontrarian

### 3.1 Sumber primernya tidak terjangkau — dikonfirmasi langsung

| Domain | Hasil |
|---|---|
| `www.ig.com` | **Timeout koneksi**, DNS → 202.169.44.80 |
| `www.myfxbook.com` | **Timeout koneksi**, DNS → 202.169.44.80 |
| `www.oanda.com` | **Timeout koneksi**, DNS → 202.169.44.80 |
| `www.dailyfx.com` | **HTTP 403**; WebFetch 301 ke ig.com yang diblokir |

Ketiga domain broker menunjuk ke **alamat sinkhole ISP yang sama** — pemblokiran tingkat
DNS. **Karena itu saya tidak bisa memberi tahu apakah IG Client Sentiment menghitung akun
atau notional, atau apakah IG menerbitkan backtest apa pun.** Saya tidak mengisinya dari
ingatan.

**Satu hal yang saya verifikasi, dan itu penting untuk penilaian:** iggroup.com menyatakan
IG Group mengoperasikan "platform termasuk IG.com, Spectrum, tastylive, tastytrade, **dan
DailyFX**." **DailyFX bukan pihak ketiga independen yang menerbitkan data IG — itu properti
marketing IG Group sendiri.** Itu membatasi klaim kontrarian IGCS mana pun pada **nilai (c)**
tanpa backtest yang diungkap dan bisa direproduksi.

Untuk Myfxbook: pencarian lintas OpenAlex, Crossref, dan Semantic Scholar mengembalikan
**nol makalah akademis** yang memakai data Myfxbook. Tidak ada validasi peer-review atas
dataset itu.

### 3.2 Bukti akademis menunjuk ke arah SEBALIKNYA

**Barber, Lin & Odean (2023)**, *Resolving a Paradox: Retail Trades Positively Predict
Returns but Are Not Profitable*, *JFQA* **(a)**. Verbatim:

> *"Order imbalance ritel **secara positif memprediksi return**, tapi rata-rata trade
> investor ritel merugi. Mengapa?... Strategi long–short berdasarkan kuintil ekstrem order
> imbalance ritel menghasilkan return tahunan menyedihkan sebesar **−14,8% di saham dengan
> perdagangan ritel berat** tapi menghasilkan 6,6% di saham lain."*

**Makalah ini ada justru karena dua klaim itu ditemukan menunjuk ke arah berlawanan.** Dan
perhatikan tandanya: di mana perdagangan ritel *paling berat* — rezim yang paling analog
dengan feed sentimen broker — strategi berbasis posisi ritel ekstrem **rugi 14,8% per
tahun**.

Korroborasi, semuanya **(a)**:

- **Kelley & Tetlock (2013)**, *JoF*: pembelian bersih ritel "**secara positif memprediksi**
  return saham bulanan perusahaan **tanpa bukti pembalikan return**." Tindakan ritel
  "berkontribusi pada efisiensi pasar."
- **Kelley & Tetlock (2017)**, *RFS*: short selling ritel **memprediksi return negatif**,
  portofolio peniru menghasilkan **9% tahunan**. Short ritel terinformasi.
- **Boehmer, Jones, Zhang & Zhang (2021)**, *JoF*: pembelian bersih ritel **mengungguli
  ~10bps selama minggu berikutnya**, dan efeknya "tidak bisa dijelaskan oleh trading
  kontrarian."
- **Kaniel, Saar & Titman (2008)**, *JoF*: "excess return **positif** di bulan setelah
  pembelian intens oleh individu."

**Bahkan pengukurannya rapuh.** Barber, Huang, Jorion, Odean & Schwarz (2024), *JoF*,
menempatkan 85.000 trade nyata untuk memvalidasi algoritma identifikasi-ritel akademis
standar: ia "**salah menandai 28%** trade yang teridentifikasi, dan menghasilkan **ukuran
order imbalance yang tidak informatif untuk 30% saham**." Widget sentimen broker tidak punya
validasi yang sebanding sama sekali.

**Satu hasil kontrarian asli dan mengapa ia tidak bisa dipindahkan.** Berger (2022),
*Review of Accounting and Finance*: "Periode sentimen ritel tinggi mendahului return pasar
yang buruk," dengan "dampak terkuat... di dalam **perusahaan yang sulit dinilai atau sulit
diarbitrase**." Mekanismenya adalah *limits to arbitrage*. **XAUUSD adalah salah satu
instrumen paling likuid dan paling banyak diarbitrase di dunia. Kondisi yang membuat
sentimen ritel kontrarian di ekuitas small-cap absen di emas.**

**Di futures secara spesifik** — analog struktural terdekat — **kaki small-trader Wang
(2001) tidak memprediksi apa pun di horizon mana pun.** Dan Röthig & Chiarella (2010) pada
futures mata uang: small trader "adalah **positive feedback trader**... spekulator
**mendahului** small trader di tiga dari empat pasar futures mata uang... small trader
adalah spekulator kecil yang mengikuti spekulator besar, menunjukkan mereka **kurang
terinformasi**." **Terlambat dan tak terinformasi ≠ andal terbalik.**

### 3.3 "Ritel rugi" ≠ "posisi ritel memprediksi arah"

**Statistik kerugian regulator nyata dan terdokumentasi baik** (keduanya **(a)**):
- ESMA: "74-89% akun ritel biasanya rugi," kerugian rata-rata €1.600–€29.000.
- FCA CP18/38: "diperkirakan **78% akun klien ritel aktif merugi**," £268,4 juta hilang
  dalam tiga bulan, diproyeksikan £1,07 miliar/tahun.

**Tapi dua garis bukti independen menunjukkan statistik ini tidak membawa informasi arah:**

1. **Heimer & Simsek (2019)**, *JFE* **(a)**. Batas leverage FX ritel AS 2010 — dengan arah
   pasar dan posisi tetap — "**memperbaiki return portofolio trader leverage tinggi sebesar
   18 poin persentase per bulan (sehingga mengurangi kerugian mereka sebesar 40 persen)**...
   Namun kebijakan itu **tidak memengaruhi harga bid-ask relatif** yang dikenakan brokerage."
   *Empat puluh persen kerugian menguap hanya dengan mengubah leverage.*
2. **FCA PS19/18** mencatat (merangkum pengajuan industri) bahwa setelah batas leverage
   ESMA, "persentase akun merugi **sama**... [tapi] secara agregat konsumen ritel **merugi
   lebih sedikit**."

**Statistik yang invarian terhadap leverage tapi responsif terhadap sizing posisi sedang
mengukur struktur biaya dan geometri risk-of-ruin trading berleverage — bukan keterampilan
arah.** Kalau ritel secara sistematis *salah arah*, *tingkat* kerugiannya akan jadi properti
posisi mereka dan de-leverage tidak akan membiarkannya tak berubah.

Dan FCA CP16/40 footnote 31 menyatakannya langsung — regulator menjelaskan mengapa ia
memilih pengungkapan tingkat akun alih-alih tingkat trade:

> *"Karena ada **kecenderungan perilaku klien untuk membiarkan kerugian berjalan** (meski
> mungkin ditutup paksa dalam beberapa kasus) **sambil mengambil profit lebih kecil**, angka
> per trade mungkin tidak mengungkap pengalaman klien secara keseluruhan."*

Itu regulator yang menyatakan bahwa jurang antara win rate trade dan hasil akun **adalah**
disposition effect.

### 3.4 Apa sebenarnya yang diukur angka "74–89%"

1. **Itu hitungan akun, tidak ditimbang uang.** Akun turun €1 dihitung identik dengan akun
   turun €50.000.
2. **Itu rolling 12 bulan, disegarkan kuartalan** — bukan "per kuartal."
3. **Itu per-akun, bukan per-orang.** FCA: "angka itu mencakup akun dormant dan potensi
   beberapa akun yang dimiliki investor yang sama."
4. **Akun yang trading sekali pun ikut dihitung.** Hanya akun *tanpa* posisi terbuka di
   jendela yang dikecualikan — yang secara mekanis menaikkan persentase yang dilaporkan.
5. **P&L belum terealisasi di batas periode ikut dihitung**, jadi drawdown terbuka dihitung
   sebagai rugi meski kemudian pulih.
6. **Efek seleksi tidak dikontrol.** Tidak ada pelacakan kohort.
7. **Regulatornya sendiri bilang metrik ini tidak sensitif terhadap intervensi yang ia
   benarkan.** FCA CP18/38: "**Kami tidak mengharapkan batas leverage akan selalu menaikkan
   persentase akun yang untung**." Dan: "net profit atau loss konsumen ritel sebagai
   persentase total eksposur mereka **tetap nyaris atau sepenuhnya tidak berubah** setelah
   firma menurunkan batas leverage, tapi menghasilkan pengurangan volume trading 28%."
8. **Itu tidak mengatakan apa pun tentang metodologi take-profit optimal.** **Jangan pakai
   74–89% untuk berargumen mendukung atau menentang skema TP/SL mana pun.**

**Anchor per-NCA di dalam rentang (a):**
- **AMF Prancis (2014):** "89% konsumen rugi... rata-rata €10.887 dan **median €1.843**."
  **Jurang mean/median ~6× adalah fakta tunggal paling informatif di sini** — kerugian
  sangat ber-skew kanan dan "rata-rata kerugian" adalah artefak ekor, bukan klien tipikal.
- **FCA CP16/40 Annex 2 ¶41 — temuan distribusi yang hampir tak ada yang mengutip:**
  "rata-rata kerugian per klien ritel **tidak berpengalaman** adalah **£400** dan rata-rata
  kerugian per klien ritel **berpengalaman** adalah **£3.500**. Ini didasarkan pada klien
  yang melakukan **kurang dari 100 trade** dan klien yang melakukan **100 trade atau
  lebih**." **Kerugian berskala ~9× dengan jumlah trade.**
- **AFM Belanda (2019):** "Diperkirakan rata-rata klien ritel rugi **€1.201**. Data
  menunjukkan korelasi antara hasil terealisasi dan leverage CFD. **Hasil rata-rata memburuk
  seiring naiknya leverage.**"

### 3.5 Putusan sentimen ritel

**Marketing, bukan bukti.** Tidak ada sumber yang bisa dijangkau yang menetapkan bahwa posisi
ritel agregat memprediksi pembalikan di emas atau di mana pun. Bukti dengan identifikasi
terbaik menemukan tandanya *positif*; satu hasil kontrarian bergantung pada limits-to-
arbitrage yang tidak dimiliki emas; dan di futures secara spesifik, kaki small-trader tidak
memprediksi apa pun. **Nilai (c) untuk produk broker, (a) untuk literatur akademis yang
membantahnya.**

---

## 4. Sentimen berita dan teks

### 4.1 Sentimen berita — kontemporer, bukan prediktif

**Smales (2014)**, *News sentiment in the gold futures market*, *Journal of Banking &
Finance* 49, 275–286 **(a)**. Thomson Reuters News Analytics, futures emas, **2003–2012**.
Verbatim:

> *"Ada respons asimetris terhadap rilis berita dengan sentimen berita negatif memicu respons
> **kontemporer** yang lebih besar dalam return futures emas daripada berita positif."*

**Kata operatifnya adalah "kontemporer."** Makalahnya mendokumentasikan sebuah *respons*,
bukan ramalan. Asimetrinya berbalik selama resesi 2007–2009.

Dan kecepatannya, dari makalah Smales di pasar lain: "penyesuaian terhadap informasi baru
terjadi cepat dengan **bagian signifikan dari reaksi selesai dalam 30 detik**."

### 4.2 Google Trends — memprediksi volatilitas dan volume, bukan arah

**Ayala, Gonzálvez-Gallego & Arteaga-Sánchez (2024)**, tinjauan sistematis, *Financial
Innovation* **(a)**. 56 studi, 2010–2021:

> *"GSVI **berhubungan positif dengan volatilitas dan volume trading** terlepas dari kata
> kunci, wilayah pasar, atau frekuensi yang dipakai."*

**Atensi memprediksi SEBERAPA BANYAK pasar bergerak, bukan KE ARAH MANA.**

### 4.3 Media sosial dan sentimen LLM — nilai (c), dan inilah sebabnya

Pencarian **tidak menemukan literatur peer-review yang kredibel** tentang sentimen media
sosial yang memprediksi emas. Yang ada menunjukkan patologi klasik. Contoh: studi sentimen
Bitcoin *yang sama* dipublikasikan dua kali di dua venue dalam tiga bulan, melaporkan
**akurasi arah 0,90** tanpa biaya transaksi, tanpa validasi walk-forward, dan tanpa koreksi
multiple testing. **Publikasi ganda itu sendiri adalah bendera merah kualitas.**

Karya sentimen emas berbasis LLM yang baru ada, tapi temuannya tidak bisa diambil: Sun, Xu,
Zhang & Simkins (2024), *Investor Sentiment in Gold and Gold Futures Market: Evidence from
ChatGPT-Generated Sentiment Index*, SSRN 4877214. **Tak dinilai — hanya judul.** **Perlakukan
backtest sentimen LLM apa pun dengan prasangka ekstrem sampai ia menunjukkan hasil
out-of-sample setelah biaya.**

### 4.4 Tolok ukur overfitting yang harus diterapkan

- **Harvey, Liu & Zhu (2016)**, *RFS* **(a)**: "faktor yang baru ditemukan perlu melewati
  rintangan jauh lebih tinggi, dengan **t-ratio lebih besar dari 3,0**... kami berargumen
  bahwa **sebagian besar temuan riset yang diklaim dalam ekonomi keuangan kemungkinan
  salah**."

Terapkan batas t > 3,0 pada Tabel II Wang dan hanya hasil portofolio 4–6 minggu yang
bertahan. Terapkan pada literatur ML-sentimen dan praktis tidak ada yang bertahan.

---

## 5. Perilaku safe-haven

**Baur & Lucey (2010)**, *Is Gold a Hedge or a Safe Haven?*, *Financial Review* 45,
217–229 **(a)**. Verbatim:

> *"(i) Emas adalah lindung nilai terhadap saham, (ii) Emas adalah safe haven dalam kondisi
> pasar saham ekstrem dan (iii) **Emas adalah safe haven bagi saham hanya selama 15 hari
> perdagangan setelah guncangan ekstrem terjadi**."*

**Kondisi pemicunya presisi:** regressor memakai return saham "di kuantil bawah ke-q seperti
**kuantil 5%, 2,5%, dan 1%**." **Data harian.** Koefisien lindung nilai: **−0,0568 (AS),
−0,1988 (UK), −0,0497 (Jerman)**, semuanya sangat signifikan. **Emas bukan lindung nilai
maupun safe haven untuk obligasi.**

**Baur & McDermott (2010)**, *JBF* **(a)**, 1979–2009: emas adalah lindung nilai dan safe
haven untuk **pasar Eropa mayor dan AS**, tapi **tidak untuk Australia, Kanada, Jepang, atau
BRIC**.

**Tandingannya** (Erb & Harvey): **17% observasi bulanan saham/emas AS jatuh di kuadran
sama-sama negatif.**

**Apakah ia beroperasi pada skala waktu intraday? Tidak.** Pemicunya adalah return harian di
1–5% bawah distribusinya; efeknya meluruh atas **~15 hari perdagangan**. **Ini fenomena tiga
minggu yang dikondisikan pada peristiwa harian langka. EA tidak bisa memperdagangkannya,
tapi ia mendeskripsikan sebuah *rezim* yang mungkin ingin dikenali EA.**

---

## 6. Pertanyaan kunci: adakah yang bekerja di horizon intraday?

### 6.1 Jawaban jujur: tidak ada sinyal sentimen arah

| Indikator | Horizon terpendek dengan bukti | Frekuensi update | Arah? |
|---|---|---|---|
| Posisi COT | **4–8 minggu** (2 mgg: nol) | Mingguan, lag 3–10 hari | Lemah, risk premium |
| Posisi CIT harian (non-publik) | Harian | Harian | **~2bp, tanda salah** |
| Suku bunga riil / makro | Bulanan | Nyaris harian | Bergantung rezim |
| Aliran ETF | Bulanan | Harian/mingguan | Reaktif |
| Safe-haven | ~15 hari perdagangan | Pemicu harian | Kondisional |
| Posisi ritel | — | Real-time | **Tanpa bukti valid; tanda kemungkinan positif** |
| Sentimen berita | **Kontemporer** | Real-time | Respons, bukan ramalan |
| Google Trends | Mingguan | Mingguan | **Volatilitas/volume saja** |

**Semua yang punya bukti berada di skala hari sampai bulan.** Dua hal yang *memang*
real-time (posisi ritel, sentimen berita) masing-masing tidak didukung dan kontemporer.

Ada juga alasan struktural untuk mengharapkan ini: berita makro terserap dalam ~30 detik.
**Saat feed sentimen diparsing, diskor, dan dikirim, pergerakannya sudah terjadi. Kita
tidak balapan dengan EA ritel lain; kita balapan dengan sistem colocated yang membaca kabel
yang sama.**

### 6.2 Yang BEKERJA intraday — dan itu kuantitas yang berbeda

Studi paling relevan adalah **Sobti**, *What causes intraday price jumps and co-jumps in
Gold*. Memakai **Thomson Reuters MarketPsych Indices** — skor atensi, sentimen, dan emosi
menit-per-menit — dengan sampling 5 menit, **1 Jan 2010 – 31 Mar 2018**, futures emas CME
dan SPDR GLD **(a metodologi; conference paper, tanpa uji out-of-sample)**.

- **"Kejutan berita makroekonomi AS adalah prediktor dominan lompatan harga"** — makro AS
  terjadwal menyebabkan **18–25%** lompatan intraday di futures COMEX.
- **"aktivitas trading, biaya trading, Amihud illiquidity, dan volatilitas berada pada
  tingkat tinggi 10–15 menit sebelum lompatan positif maupun negatif."**
- Atensi berita menaikkan prediktabilitas lompatan **negatif**; atensi media sosial
  menaikkan prediktabilitas lompatan **positif**.

**Baca itu hati-hati. Ia memprediksi *terjadinya* lompatan — peristiwa volatilitas — lewat
regresi logistik terpenalti. Itu BUKAN ramalan return arah setelah biaya.** Ringkasan
jujurnya: **data sentimen dan atensi intraday memberi tahu KAPAN pasar akan bergerak, bukan
KE ARAH MANA.**

### 6.3 Yang sah dilakukan sentimen dalam sistem intraday

Tiga peran, urut dari dukungan bukti terkuat:

**1. Veto jendela peristiwa (dukungan terkuat).** Rilis makro AS terjadwal menyebabkan
18–25% lompatan emas intraday, dan penyesuaian selesai dalam ~30 detik. Likuiditas memburuk
10–15 menit sebelumnya. **Mundur di sekitar rilis terjadwal didukung bukti dan tidak
mengorbankan apa pun yang bisa kita tangkap dengan andal.** Ini hanya butuh kalender
ekonomi — tidak butuh vendor sentimen.

**2. Penskalaan rezim volatilitas (dukungan baik, secara analogi).** Moreira & Muir,
*Volatility Managed Portfolios*, *JoF* **(a)**: "Portofolio terkelola yang mengambil risiko
lebih kecil saat volatilitas tinggi menghasilkan alpha besar... **Timing volatilitas
menaikkan Sharpe ratio karena perubahan volatilitas faktor tidak diimbangi perubahan
proporsional expected return.**" Menskalakan ukuran posisi dengan kebalikan varians
terealisasi baru-baru ini adalah perbaikan yang terdokumentasi dan kokoh — dan hanya memakai
data harga. Catatan: terdokumentasi pada frekuensi bulanan di faktor ekuitas, jadi
memindahkannya ke emas intraday adalah asumsi, bukan temuan.

**3. Konteks rezim lambat (dukungan lemah, biaya rendah).** Ekstrem posisi COT, rezim suku
bunga riil, aliran bank sentral, dan keadaan safe-haven bisa secara wajar mengondisikan
*strategi mana* yang diaktifkan atau seberapa agresif sizing — pada kadensi review
mingguan. **Ini bukan sinyal dan tidak boleh pernah disambungkan ke entry.**

### 6.4 Rekomendasi konkret untuk EA

**Jangan bangun:**
- Entry atau exit apa pun yang dipicu posisi COT. Venue salah, frekuensi salah (52/tahun),
  lag salah (3–10 hari), horizon salah (4–8 minggu), dan tanpa bukti spesifik emas.
- Aturan kontrarian apa pun yang dikunci ke sentimen ritel/broker. Buktinya menunjuk ke
  arah lain, datasetnya tak terjangkau dan tak tervalidasi, dan satu penerbit yang bisa
  diidentifikasi memasarkan bukunya sendiri.
- Sinyal entry sentimen berita atau LLM apa pun. Hubungan yang terdokumentasi bersifat
  kontemporer dan terserap dalam ~30 detik.

**Bangun:**
- **Veto berbasis kalender** di sekitar rilis makro AS terjadwal (NFP, CPI, FOMC, PPI,
  retail sales, consumer confidence, PMI, jobless claims).
- **Sizing posisi sadar-volatilitas**, menurunkan ukuran saat volatilitas terealisasi
  baru-baru ini tinggi.
- **Flag rezim berkadensi mingguan** kalau memang ingin konteks makro — dan pisahkan
  tegas dari pembangkitan sinyal agar kita bisa mengukur apakah ia menambah apa pun.

**Versi satu kalimat: dalam sistem emas intraday, sentimen masuk ke LAPISAN RISIKO, tidak
pernah ke LAPISAN SINYAL.**

---

## Referensi

**Tingkat (a) — kuantitatif, metodologi terbuka**

- CFTC, *Disaggregated Explanatory Notes* —
  https://www.cftc.gov/MarketReports/CommitmentsofTraders/DisaggregatedExplanatoryNotes/index.htm
- CFTC, *Explanatory Notes* (legacy) —
  https://www.cftc.gov/MarketReports/CommitmentsofTraders/ExplanatoryNotes/index.htm
- CFTC, *Release Schedule* —
  https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm
- CFTC Public Reporting Environment — https://publicreporting.cftc.gov/resource/72hh-3qpy.json
- Wang, C. (2001). *Investor Sentiment and Return Predictability in Agricultural Futures
  Markets.* *JFM* 21(10), 929–952. https://doi.org/10.1002/fut.2003 · OA:
  https://mpra.ub.uni-muenchen.de/36425/1/MPRA_paper_36425.pdf ·
  [`../pdf/wang-2001-investor-sentiment-return-predictability-agricultural-futures.pdf`](../pdf/wang-2001-investor-sentiment-return-predictability-agricultural-futures.pdf)
- Wang, C. (2003). *The behavior and performance of major types of futures traders.*
  *JFM* 23(1), 1–31. https://doi.org/10.1002/fut.10056 · OA:
  https://mpra.ub.uni-muenchen.de/36426/1/MPRA_paper_36426.pdf ·
  [`../pdf/wang-2003-behavior-and-performance-of-major-types-of-futures-traders.pdf`](../pdf/wang-2003-behavior-and-performance-of-major-types-of-futures-traders.pdf)
- Sanders, D., Irwin, S. & Merrin, R. (2009). *Smart Money.* *JARE* 34(2).
  https://doi.org/10.22004/ag.econ.54547 · http://ageconsearch.umn.edu/record/54547
- Sanders, D., Irwin, S. & Leuthold, R. (1997). *Noise Traders, Market Sentiment, and
  Futures Price Behavior.* https://doi.org/10.2139/ssrn.39932
- Aulerich, N., Irwin, S. & Garcia, P. (2013). *Bubbles, Food Prices, and Speculation.*
  NBER WP 19065. https://doi.org/10.3386/w19065
- Bahloul, W. (2018). *Short-term contrarian and sentiment by traders' types on futures
  markets.* *Review of Behavioral Finance* 10. https://doi.org/10.1108/rbf-07-2017-0063
- Chen, Y.-L. & Mo, W.-S. (2023). *Journal of Commodity Markets* 31, 100343.
  https://doi.org/10.1016/j.jcomm.2023.100343 — **tidak bisa dibaca**
- Erb, C. & Harvey, C. (2013). *The Golden Dilemma.* *Financial Analysts Journal* 69(4).
  https://doi.org/10.2469/faj.v69.n4.1 · NBER WP 18706:
  https://www.nber.org/system/files/working_papers/w18706/w18706.pdf ·
  [`../pdf/erb-harvey-2013-the-golden-dilemma.pdf`](../pdf/erb-harvey-2013-the-golden-dilemma.pdf)
- Erb, C. & Harvey, C. (2024). *Is There Still a Golden Dilemma?*
  https://doi.org/10.2139/ssrn.4807895 — target tindak lanjut
- Herley, Orlowski & Ritter (2024). *Economies* 12(9), 229.
  https://doi.org/10.3390/economies12090229
- World Gold Council. *Gold Mid-Year Outlook 2026.*
  https://www.gold.org/goldhub/research/gold-mid-year-outlook-2026
- World Gold Council. *Gold Demand Trends* FY2022, FY2024, FY2025, Q2 2026 —
  https://www.gold.org/goldhub/research/gold-demand-trends
- World Gold Council. *Gold ETF Holdings and Flows*, Sep 2026 —
  https://www.gold.org/goldhub/research/gold-etfs-holdings-and-flows/2026/09
- World Gold Council. *Gold Market Commentary*, Agustus 2026 —
  https://www.gold.org/goldhub/research/gold-market-commentary-august-2026
- Sehgal, S., Sobti, N. & Diesting, W. (2021). *Who leads in intraday gold price discovery?*
  *JFM* 41(6). https://doi.org/10.1002/fut.22208
- Barber, B., Lin, S. & Odean, T. (2023). *Resolving a Paradox.* *JFQA*.
  https://doi.org/10.1017/S0022109023000601
- Kelley, E. & Tetlock, P. (2013). *JoF*. https://doi.org/10.1111/jofi.12028
- Kelley, E. & Tetlock, P. (2017). *RFS*. https://doi.org/10.1093/rfs/hhw089
- Boehmer, E., Jones, C., Zhang, X. & Zhang, X. (2021). *JoF*.
  https://doi.org/10.1111/jofi.13033
- Kaniel, R., Saar, G. & Titman, S. (2008). *JoF*.
  https://doi.org/10.1111/j.1540-6261.2008.01316.x
- Barber, B., Huang, X., Jorion, P., Odean, T. & Schwarz, C. (2024). *JoF*.
  https://doi.org/10.1111/jofi.13334
- Berger, D. (2022). *Review of Accounting and Finance*.
  https://doi.org/10.1108/raf-06-2021-0152
- Röthig, A. & Chiarella, C. (2010). https://doi.org/10.2139/ssrn.1631502
- Heimer, R. & Simsek, A. (2019). *JFE*. https://doi.org/10.1016/j.jfineco.2018.10.017
- Barber, B. & Odean, T. (2000). https://doi.org/10.1111/0022-1082.00226
- ESMA (2018), siaran pers dan analisis intervensi produk —
  https://www.esma.europa.eu/press-news/esma-news/esma-agrees-prohibit-binary-options-and-restrict-cfds-protect-retail-investors ·
  https://www.esma.europa.eu/sites/default/files/library/esma71-98-128_press_release_product_intervention.pdf ·
  https://www.esma.europa.eu/sites/default/files/library/esma35-43-1000_additional_information_on_the_agreed_product_intervention_measures_relating_to_contracts_for_differences_and_binary_options.pdf
- FCA CP16/40 — https://www.fca.org.uk/publication/consultation/cp16-40.pdf
- FCA CP18/38 — https://www.fca.org.uk/publication/consultation/cp18-38.pdf ·
  [`../pdf/fca-2018-cp18-38-cfd-products-consultation.pdf`](../pdf/fca-2018-cp18-38-cfd-products-consultation.pdf)
- FCA PS19/18 — https://www.fca.org.uk/publication/policy/ps19-18.pdf ·
  [`../pdf/fca-2019-ps19-18-cfd-products-policy-statement.pdf`](../pdf/fca-2019-ps19-18-cfd-products-policy-statement.pdf)
- AFM Belanda (2019) —
  https://www.afm.nl/~/profmedia/files/onderwerpen/productinterventie/productinterventie-cfd_eng.pdf
- Smales, L. (2014). *News sentiment in the gold futures market.* *JBF* 49, 275–286.
  https://doi.org/10.1016/j.jbankfin.2014.09.006 · WP: https://doi.org/10.2139/ssrn.2309868
- Smales, L. (2015). *Asymmetric volatility response to news sentiment in gold futures.*
  *JIFMIM* 34. https://doi.org/10.1016/j.intfin.2014.11.001
- Ayala, M., Gonzálvez-Gallego, N. & Arteaga-Sánchez, R. (2024). *Financial Innovation*.
  https://doi.org/10.1186/s40854-023-00606-y
- Preis, T., Moat, H. & Stanley, H.E. (2013). https://doi.org/10.1038/srep01684
- Harvey, C., Liu, Y. & Zhu, H. (2016). *RFS*. https://doi.org/10.1093/rfs/hhv059
- Bailey, D., Borwein, J., López de Prado, M. & Zhu, Q. (2016). *The Probability of
  Backtest Overfitting.* *Journal of Computational Finance*.
  https://doi.org/10.21314/jcf.2016.322
- Baur, D. & Lucey, B. (2010). *Is Gold a Hedge or a Safe Haven?* *Financial Review* 45.
  https://doi.org/10.1111/j.1540-6288.2010.00244.x · IIIS DP 198:
  https://www.tcd.ie/triss/assets/PDFs/iiis/iiisdp198.pdf ·
  [`../pdf/baur-lucey-2010-is-gold-a-hedge-or-a-safe-haven.pdf`](../pdf/baur-lucey-2010-is-gold-a-hedge-or-a-safe-haven.pdf)
- Baur, D. & McDermott, T. (2010). *JBF* 34. https://doi.org/10.1016/j.jbankfin.2009.12.008
- Sobti, N. *What causes intraday price jumps and co-jumps in Gold.*
  [`../pdf/sobti-2024-what-causes-intraday-price-jumps-in-gold.pdf`](../pdf/sobti-2024-what-causes-intraday-price-jumps-in-gold.pdf)
- Moreira, A. & Muir, T. *Volatility Managed Portfolios.* NBER WP 22208.
  https://www.nber.org/papers/w22208 · [`../pdf/extract-moreira_muir.txt`](../pdf/extract-moreira_muir.txt)

**Tingkat (b)**

- Sun, Xu, Zhang & Simkins (2024). *Investor Sentiment in Gold... ChatGPT-Generated
  Sentiment Index.* https://doi.org/10.2139/ssrn.4877214 — **hanya judul**
- World Gold Council GRAM — output kuantitatif, metode tak diungkap

**Yang harus dibuang (c)**

- IG Client Sentiment sebagai sinyal kontrarian — DailyFX adalah properti marketing IG
  Group sendiri; tidak ada backtest yang diungkap.
- Myfxbook Community Outlook — nol makalah akademis yang memakai datanya.
- Studi sentimen media sosial dengan "akurasi arah 0,90" —
  https://doi.org/10.5121/csit.2023.131001 dan https://doi.org/10.5121/mlaij.2023.10301 —
  studi yang sama dipublikasikan dua kali.

**Target tindak lanjut dari jaringan tak terblokir:** (1) Kaourma et al. 2025, *JIFMIM*
https://doi.org/10.1016/j.intfin.2025.102146 — order flow ritel FX intraday; (2) Chen & Mo
2023 — satu-satunya makalah COT spesifik emas; (3) Erb & Harvey 2024.
