# Order flow dan data

Apa itu analisis order flow, bukti apa yang mendukungnya, dan — yang paling penting — apa
yang **tersedia dan tidak tersedia** untuk spot XAUUSD di MetaTrader 5 dibanding futures
emas COMEX (GC).

---

## 0. Jawaban di depan

**Bisakah EA spot-XAUUSD ritel melakukan analisis order flow yang bermakna? Tidak.**

Bukan "buruk", bukan "dengan keterbatasan". **Inputnya tidak ada.** MetaQuotes sendiri
mendokumentasikan bahwa instrumen OTC diperdagangkan dengan "Bid dan Ask stream quotes…
**tanpa data transaksi yang benar-benar dieksekusi**" dan bahwa "**tidak ada harga Last
dalam mode ini**". Analisis order flow adalah studi tentang transaksi bertanda yang
tereksekusi melawan likuiditas yang menunggu. Di spot XAUUSD kita tidak punya transaksinya
maupun likuiditasnya — hanya bid dan ask broker, dan hitungan seberapa sering broker itu
mengubahnya.

**Dan bahkan dengan data sempurna, edge-nya tidak akan bertahan terhadap struktur biaya
kita.** → §6

---

## 1. Masalah ketersediaan data

### 1.1 Kata platform sendiri tentang instrumen OTC **(a)**

Dari MT5 Help, *Price Data*, verbatim:

> *"Hanya Bid dan Ask stream quotes yang dipakai dalam perdagangan pasar OTC, **tanpa data
> tentang transaksi yang benar-benar dieksekusi**. Chart didasarkan pada harga Bid."*

> *"Bursa tidak berpartisipasi dalam perdagangan dan tidak menyimpan catatan trade yang
> dilakukan, karena itu **tidak ada harga Last yang tersedia dalam mode ini**."*

> *"Tick volume, yang menunjukkan **jumlah tick yang diterima selama pembentukan bar**"*
> *"Volume, yaitu volume riil transaksi yang dilakukan selama pembentukan bar (**mungkin
> tidak tersedia untuk pasar OTC**)"*

> *"Depth of Market bursa terdiri dari limit order peserta pasar, sementara **Depth of Market
> OTC dibentuk berdasarkan kuotasi broker.**"*

**Spot XAUUSD di broker ritel secara definisi adalah OTC.** Setiap konsekuensi mengikuti dari
empat kalimat itu.

### 1.2 Jebakan DOM sintetis — temuan praktis terpenting **(a)**

Dari MT5 Help, *Depth of Market*, verbatim:

> *"Jika sebuah instrumen diperdagangkan di pasar over-the-counter (OTC), Depth of Market
> bisa dibentuk berdasarkan kuotasi broker, yang mungkin memberi harga berbeda tergantung
> volume beli atau jual. **Jika broker tidak menyediakan volume, jendela DOM berfungsi sebagai
> alat scalping**, yang memungkinkan penempatan market dan pending order dengan satu klik.
> **Dalam kasus ini, Depth of Market menampilkan level harga yang dihitung berdasarkan harga
> Bid dan Ask memakai price change step.**"*

> *"Jumlah bid dan offer yang ditampilkan di DOM ditentukan oleh parameter simbol yang
> ditetapkan broker."*

**Baca hati-hati. Ini bukan biner "DOM bekerja / DOM absen".** Ada kasus tengah di mana
`MarketBookAdd()` berhasil, `MarketBookGet()` mengembalikan array berisi, dan **isinya adalah
tangga harga yang dibangkitkan secara aritmetika dari Bid/Ask dan tick size** — memuat tepat
nol informasi tentang order siapa pun yang menunggu.

Karena broker yang menetapkan parameter kedalaman, `SYMBOL_TICKS_BOOKDEPTH` **bisa bernilai
bukan-nol pada tangga sintetis.**

**EA kita sudah bertahan dengan benar dari ini.** `TrackDomVariability()` di
`ea/QlipV5_XAUUSD.mq5` menyatakan feed sintetis setelah `InpDomStaticSamples` pembacaan
identik, dan `DomImbalance()` lalu mengembalikan 0,0. Dua catatan:
- Tangga sintetis yang diturunkan dari Bid/Ask **akan** berubah saat Bid/Ask bergerak, jadi
  cek variabilitas saja tidak menangkap setiap kasus. **Book yang volume per-level-nya
  konstan atau simetris sempurna sementara harga bergerak adalah tanda pengenalnya.**
- Catat `SYMBOL_TICKS_BOOKDEPTH` dan volume per-level mentah sekali saat init.

Kasus ini juga muncul di feed berbayar: satu working paper menemukan arsip data praktisi
vendornya **gagal uji autentikasi pada lapisan kedalaman** — kedalaman menunggu vendor tetap
invarian melalui kemacetan Treasury Maret 2020, di mana data berlisensi menunjukkan kedalaman
runtuh. **Vendor komersial mengirim kedalaman hasil rekonstruksi seolah-olah terekam.**

### 1.3 Permukaan API MQL5, per fungsi

| API | Perilaku terdokumentasi | Di spot XAUUSD ritel |
|---|---|---|
| `MarketBookAdd()` | Membuka DOM dan berlangganan notifikasi perubahan | Bisa mengembalikan `true` melawan tangga sintetis |
| `MarketBookGet()` | Mengisi `MqlBookInfo[]`. Dokumen: **"DOM hanya tersedia untuk sebagian simbol."** | Kosong, atau sintetis |
| `OnBookEvent()` | Menyala saat DOM berubah; broadcast chart-wide | Menyala juga pada update tangga sintetis |
| `SYMBOL_TICKS_BOOKDEPTH` | **"Jumlah maksimal request yang ditampilkan di DOM. Untuk simbol tanpa antrean request, nilainya nol."** | Ditetapkan broker; **nol itu konklusif, bukan-nol tidak** |
| `COPY_TICKS_INFO` | "tick dengan perubahan harga Bid dan/atau Ask dikembalikan" | **Hanya ini yang kita dapat** |
| `COPY_TICKS_TRADE` | "tick dengan perubahan harga Last dan volume dikembalikan" | Kosong — OTC tidak punya Last |
| `TICK_FLAG_BUY` / `TICK_FLAG_SELL` | "tick adalah hasil transaksi beli/jual" | Tidak pernah diset — butuh transaksi tereksekusi |
| `CopyTickVolume()` | "data historis **tick volume**" | Bekerja. Menghitung *update kuotasi broker kita* |
| `CopyRealVolume()` | "data historis **trade volume**" | Tidak ada yang dikembalikan |

**Bahwa MQL5 menyediakan `CopyTickVolume` dan `CopyRealVolume` sebagai dua fungsi terpisah
itu sendiri adalah platform yang memberi tahu bahwa keduanya objek berbeda.**

Detail korroborasi dari dokumen CopyTicks: terminal meng-cache "4.096 tick terakhir untuk
setiap instrumen (**65.536 tick untuk simbol dengan Market Depth yang berjalan**)". Cache
besar itu disediakan untuk simbol bursa.

> **Catatan yang saya utangi:** MQL5 tidak pernah menyatakan secara harfiah "COPY_TICKS_TRADE
> mengembalikan 0 di Forex". Baris itu adalah konsekuensi logis dari halaman *Price Data*,
> bukan kalimat yang dikutip. **Ini mudah difalsifikasi di terminal kita sendiri dalam
> sekitar sepuluh baris** — dan layak dilakukan sekali, karena mengubah deduksi jadi
> pengukuran. → §7

### 1.4 Mengapa spot emas tidak punya book untuk diekspos

- **LBMA:** loco London adalah **pasar OTC bilateral**. Tidak ada central limit order book.
- **BIS Quarterly Review, Des 2019, Schrimpf & Sushko (a):** "Peserta pasar yang ingin
  memperdagangkan FX punya **lebih dari 75 venue FX berbeda**." Internalisasi dealer berarti
  "pangsa aktivitas trading yang 'terlihat' oleh pasar yang lebih luas **menurun**."

**Tidak ada consolidated tape. Tidak ada venue tempat order emas institusional bisa terlihat
oleh kita.**

Dan hasil mikrostruktur yang mapan adalah bahwa order institusional besar **dipecah menjadi
deret panjang child order kecil** untuk meminimalkan dampak (mekanisme di balik
square-root law). **Satu candle 15 menit tidak mungkin menjadi jejak institusional, karena
institusi sengaja menghindari meninggalkannya.** Itu masalah struktural bagi premis "order
block", terlepas dari hasil backtest-nya.

### 1.5 Kontrasnya: COMEX GC

CME Globex MDP 3.0 benar-benar menyediakan **MBO (market-by-order)** — "Tick-by-tick dengan
kedalaman order book penuh / Semua order beli dan jual di setiap level harga", dengan skema
`mbo`, `mbp-10`, `tbbo`, dan `trades`. **Volume riil, time-and-sales riil, kedalaman riil,
tingkat-order.**

**GC juga instrumen large-tick, yang sangat penting di §2.** Kuotasi yang diamati: bid
4374,60 / ask 4374,80, setiap harga pada kenaikan $0,10, spread 2 tick. Perlakukan "tick GC =
$0,10/oz" sebagai sangat terindikasi oleh harga yang diamati, **bukan kutipan CME yang
terverifikasi** (cmegroup.com tidak terjangkau dari mesin ini).

---

## 2. Order Book Imbalance — apa yang sebenarnya dikatakan literatur

Ada bukti terbitan berkualitas tinggi di sini. **Ia mengatakan sesuatu yang lebih sempit
daripada yang disiratkan industri edukasi.**

### 2.1 Angka R² utama itu KONTEMPORER, bukan prediktif

**Cont, Kukanov & Stoikov (2014)**, *Journal of Financial Econometrics* 12(1), 47–88 **(a)**.
Teks lengkap dibaca, bukan hanya abstrak:

> *"Kami menemukan variabel agregat ini **menjelaskan** perubahan mid-price pada skala waktu
> pendek secara linear, untuk sampel saham yang besar, dengan **R² rata-rata 65%**."*

Setiap regresi di makalah itu berbentuk `ΔP_k = β·OFI_k + ε_k` di mana — kata mereka —
"ΔP_k adalah **perubahan mid-price 10 detik** dan OFI_k adalah order flow imbalance
**kontemporer**." **Tidak ada spesifikasi tertinggal atau prediktif di mana pun di makalah
itu.**

Penulisnya tahu ini masalah dan mengujinya:

> *"Untuk menguji bahwa R² tinggi dalam regresi kami bukan karena **tautologi ini**, kami
> mengestimasi (4) pada subsampel saham, **mengecualikan event yang mengubah harga** dari
> OFI_k. Dengan perubahan ini R² turun, tapi tetap di wilayah 35%–60%."*

Sampel: **satu bulan kalender — April 2010, 21 hari perdagangan**, 50 saham S&P 500.

Dan baris yang paling penting untuk §3: "R² rata-rata untuk order flow imbalance adalah
**65%** dibanding **32%** untuk **trade imbalance**."

Tiga makalah lagi mengonfirmasi polanya struktural **(a)**:
- **Xu, Gould & Howison (2019):** "memasang hubungan linear sederhana antara MLOFI dan
  perubahan mid-price **kontemporer**."
- **Su et al. (2021):** R² log-GOFI 83,57%/85,37%/86,01% pada 30d/1m/5m — dibingkai
  sepanjang makalah sebagai "kemampuan **menjelaskan** perubahan harga saham."
- **Cont, Cucuringu & Zhang (2023)**, *Quantitative Finance*: "model multi-aset dengan
  cross-impact tidak memberi daya penjelas tambahan untuk dampak **kontemporer**… Di sisi
  lain, kami menunjukkan bahwa OFI lintas-aset **tertinggal memang memperbaiki peramalan**
  return masa depan… cross-impact tertinggal ini terutama bermanifestasi pada **horizon
  jangka pendek dan meluruh cepat**."

Makalah terakhir itu uji terbersih di literatur karena menjalankan kedua spesifikasi pada
data yang sama. Pembacaan teks lengkap (didelegasikan) melaporkan R² out-of-sample
kontemporer **83,83%** dan setiap R² out-of-sample *prediktif* satu menit **negatif**
(−0,37 sampai −0,09, lebih buruk daripada meramal nol), dengan P&L kotor tahunan 0,21–0,43%
sebelum pengakuan makalah sendiri bahwa "strategi mengabaikan biaya trading."

**Kita tidak bisa memperdagangkan hubungan kontemporer. Saat OFI terukur, harga sudah
bergerak.** Perbedaan tunggal itu membatalkan sebagian besar yang dijual sebagai "edge
order flow".

### 2.2 Di mana ia MEMANG prediktif, rezim tick size menentukan segalanya

**Gould & Bonart (2016)**, *Market Microstructure and Liquidity* **(a)**. Yang ini benar-benar
meramal — arah pergerakan mid-price berikutnya, Nasdaq 2014, data LOBSTER, 10 saham:

> *"regresi logistik kami memperbaiki kinerja out-of-sample klasifikasi biner sekitar
> **50–60% untuk saham large tick** dan sekitar **10–30% untuk saham small-tick**."*
> *"hasil kami untuk prediksi probabilistik sedikit lebih lemah, tapi tetap memperbaiki
> kinerja prediktif out-of-sample sekitar **20–30% untuk saham large-tick** dan sekitar
> **2–6% untuk saham small-tick**."*

Definisi mereka: saham large-tick disebut begitu karena "**harganya yang rendah membuat
tick size relatifnya besar**" (MSFT, INTC, MU, CSCO, ORCL — spread rata-rata $0,012–0,015,
yaitu satu tick). Small-tick: GOOG, AMZN, TSLA, PCLN, NFLX — $0,195–1,111.

**Sekarang tempatkan XAUUSD di sumbu itu.** Harga $4.374. Point = $0,01. Gerbang kita
mengizinkan spread sampai 30 points. **Spread 20–35× kenaikan minimum** menempatkan spot emas
ritel di **ujung small-tick paling jauh** — jauh melampaui AMZN. Menurut hasil makalah ini
sendiri, itu rezim **2–6%**, bukan rezim 20–30%.

**Jebakannya struktural: sinyalnya paling kuat justru di tempat tick terlalu kasar untuk
dimonetisasi.** Pergerakan mid-price satu-tick-ke-depan pada saham spread satu sen adalah
setengah sen melawan bolak-balik satu sen. Di mana tick cukup halus sehingga pergerakan yang
diprediksi layak ditangkap, imbalance nyaris tidak memprediksi.

### 2.3 Umur sinyalnya kurang dari satu detik

**Takahashi (2025)**, *Returns and Order Flow Imbalances* **(a)**. E-mini S&P 500 CME, VAR
struktural pada frekuensi satu detik. Verbatim:

> *"dampak harga maupun aliran signifikan pada horizon satu detik… **Impulse response
> menunjukkan guncangan menghilang hampir sepenuhnya dalam satu detik.**"*

Korroborasi:
- **Kolm, Turiel & Westray (2023)**, *Mathematical Finance* **(a)**: "**horizon efektif**
  ramalan spesifik saham adalah **kira-kira dua perubahan harga rata-rata**." R²
  out-of-sample rata-rata dilaporkan **0,181 persen** (tangan kedua).
- **Hu & Zhang (2025)**, futures indeks CSI 300 **(a)**: korelasi dengan perubahan harga
  "**kontemporer**"; profit out-of-sample "kira-kira seperempat atau sedikit kurang dari
  profit in-sample".
- **Lucchese, Pakkanen & Veraart (2023)** **(a)**: "pada frekuensi tinggi prediktabilitas
  return mid-price bukan hanya ada, tapi **ada di mana-mana**" — pada horizon 50–300 update
  book, milidetik sampai sekitar setengah detik.

**Framing jujurnya bukan "apakah ada sinyal". Ada. Melainkan: apakah sinyalnya lebih besar
daripada spread, dan bisakah kita bertindak di dalam umurnya.**

### 2.4 Bisa dicapai taker ritel? Tidak — empat garis independen

**(i) Makalah kanonik "apakah OBI membantu" hanya memodelkan market maker.** Cartea,
Donnelly & Jaimungal (2018), *Applied Mathematical Finance* **(a)**. Kontrol agen adalah
indikator biner post/tidak-post untuk limit order di touch; agen **menerima** half-spread
sebagai pendapatan; market order hanya muncul di likuidasi terminal, dan penulisnya sengaja
menetralkan bahkan itu "untuk menghilangkan efek menyeberangi spread terhadap kinerja
strategi." Horizon: 10 milidetik, dengan asumsi "latensi nol". Sharpe utama 25–34 **runtuh ke
kira-kira 0–5, negatif untuk beberapa nama, begitu probabilitas fill realistis diterapkan** —
sebelum fee apa pun. **Makalah itu tidak pernah menanyakan pertanyaan taker.**

**(ii) Peringkat latensi, bukan besaran latensi, yang membagi profit.** Byrd, Palaparthi,
Hybinette & Balch (2020) **(a, disimulasikan)**. Verbatim: "latensi berbanding terbalik dengan
profit bagi trader OBI, tapi lebih menarik lagi menunjukkan **peringkat latensi, alih-alih
besaran absolut, adalah faktor kunci** dalam membagi return di antara agen yang mengejar
strategi serupa." Peringkat 1 = **+$2.681/hari**, peringkat 2 = **−$3.297/hari**, peringkat 10
= **−$24.474/hari**. **Pemenang mengambil semua. EA ritel di belakang hop HTTP dan broker
bridge tidak berada di tengah antrean itu.**

**(iii) Saat seseorang memperdagangkannya dengan uang nyata, mengikuti imbalance adalah tanda
yang salah.** Albers, Cucuringu, Howison & Shestopaloff (2025) **(a)**. Verbatim: "Memakai
data dari **eksperimen trading live** pada Binance Bitcoin perpetual… korelasi negatif antara
kemungkinan fill maker dan return pasca-fill. Ini mengharuskan **strategi maker yang layak
sering butuh pendekatan kontrarian, melawan order book imbalance yang berlaku**. Dinamika ini
membuat **strategi yang lazim dikutip sangat tidak menguntungkan**." Maker berbasis imbalance
**−0,47 bp/trade**, taker berbasis imbalance **−1,96 bp/trade**, keduanya setelah fee tier
terbaik, **dengan latensi diasumsikan dapat diabaikan**. 232.897 order live.

**(iv) Plafonnya rendah bahkan dengan pandangan sempurna.** Kearns, Kulesza & Nevmyvaka (2010)
**(a)**. Mereka mensimulasikan "trader frekuensi tinggi '**mahatahu**' yang bisa melihat masa
depan" dan "sampai pada angka yang **mengejutkan sederhana**." Lintas 19 saham Nasdaq selama
setahun penuh, trader berpandangan sempurna pada periode penahanan 10ms menghasilkan
**$62.000**.

**(v) Dan pintu keluar pasif juga tertutup.** DeLise (2024), futures Treasury 10 tahun: fill
limit order "disebabkan oleh dan bertepatan dengan pergerakan harga yang merugikan, yang
menciptakan hambatan pada P&L market maker." **Jangan menyeberangi spread dan kita malah
kena adverse selection.**

### 2.5 Satu studi yang menguji PERSIS setup ini pada XAUUSD di broker ritel **(a)**

**Jaddu & Bilokon (2023)**, *Combining Deep Learning on Order Books with Reinforcement Learning
for Profitable Trading*, arXiv:2311.02088.

**Makalah paling relevan di seluruh tinjauan ini.** Lima instrumen — GBPUSD, EURUSD, DE40,
FTSE100, dan **XAUUSD** — memakai order flow imbalance dari **order book broker ritel
(cTrader/FXPro)**, di-backtest lalu di-forward-test live di platform ritel itu. Putusan
abstraknya sendiri:

> *"Hasilnya membuktikan potensi tapi **butuh modifikasi minimal lebih lanjut agar trading
> konsisten menguntungkan untuk sepenuhnya menangani biaya trading ritel, slippage, dan
> fluktuasi spread.**"*

Dari pembacaan teks lengkap (didelegasikan): XAUUSD punya ~13,4 juta observasi atas 10 minggu
di 2023 dan **paling buruk dari kelima instrumen**, R² out-of-sample rata-rata **0,045%** (vs
0,231% untuk DE40). Dengan biaya ritel diterapkan, profit harian rata-rata pada XAUUSD
**negatif** di ketiga algoritma RL. **XAUUSD tidak pernah lolos ke uji forward live**; dua
instrumen yang lolos (GBPUSD, EURUSD) sama-sama rugi live, yang penulisnya atribusikan ke
latensi — "horizon pertama hampir selalu sudah lewat saat trade baru dieksekusi."

**Perbandingan apel-ke-apel yang jujur bukan "R² 65%". Melainkan 0,181% (ekuitas Nasdaq, data
institusional) versus 0,045% (XAUUSD, feed broker ritel).**

### 2.6 Hasil terbitan yang langsung membantah intuisi ritel **(a)**

**Patzelt & Bouchaud (2018)**, *Physical Review E* 97, 012304. Peer-reviewed. Verbatim:

> *"Kami lebih lanjut menunjukkan bahwa **order flow imbalance ekstrem tidak diasosiasikan
> dengan return besar**. Sebaliknya, ia teramati saat harga '**dipatok**' pada level
> tertentu. Harga bergerak hanya ketika ada **keseimbangan yang cukup** dalam order flow
> lokal. Faktanya, probabilitas sebuah trade mengubah mid-price **jatuh ke nol** dengan
> meningkatnya bias tanda order (absolut)… Temuan kami **menantang asumsi luas tentang dampak
> agregat linear**."*

**"Imbalance besar berarti pergerakan besar" bukan penyederhanaan literatur. Itu KEBALIKAN
dari apa yang ditemukan literatur.**

### 2.7 Cakupan untuk FX dan emas secara spesifik

Hampir tidak ada:
- arXiv `abs:"order book" AND abs:gold` → **0 hasil.**
- arXiv `abs:"limit order book" AND abs:"foreign exchange"` → **1 hasil** (tentang
  probabilitas fill, bukan prediksi OBI).
- arXiv `abs:"tick volume"` → **0 hasil.**

---

## 3. Footprint, delta, dan CVD

### 3.1 Langkah penandaan adalah mata rantai lemah, dan errornya sistematis

Delta dan CVD adalah jumlah berjalan volume trade bertanda. Setiap footprint chart bertumpu
pada algoritma yang menebak sisi mana yang agresor.

| Studi | Pasar | Lee-Ready | Tick rule | Quote rule |
|---|---|---|---|---|
| **Ellis, Michaely & O'Hara (2000)**, *JFQA* | Ekuitas Nasdaq | **81,05%** | 77,66% | 76,4% |
| **Odders-White (2000)**, *JFM* | NYSE (TORQ) | **85%** | — | — |
| **Jurkatis (2022)**, *JFM* | Nasdaq ITCH, 134 juta+ trade | ~90% volume | — | — |
| **Grauer, Schuster & Uhrig-Homburg (2023)** | **Options** ISE/CBOE | **62,0–62,5%** | **~acak** | — |
| **Bilz (2023)**, tesis KIT, 49,2 juta trade ISE | **Options** ISE | 62,56% | **49,67%** | 62,67% |

Dua hal lebih penting daripada persentase utamanya.

**Pertama, errornya tidak acak.** Temuan sentral Odders-White adalah bahwa misklasifikasi
terkonsentrasi di **trade titik tengah, trade kecil, dan saham besar atau yang sering
diperdagangkan**. Tingkat error 15% yang berkorelasi dengan persis karakteristik trade yang
akan kita kondisikan tidak saling meniadakan — ia membias.

**Kedua, artefak penandaan bisa memproduksi sinyal yang tampak prediktif.** Ini kisah VPIN dan
itu peringatan bagi siapa pun yang membangun indikator delta. Easley, López de Prado & O'Hara
memperkenalkan VPIN pada bulk volume classification. **Andersen & Bondarenko (2014)**, *Review
of Finance*, membangun benchmark klasifikasi akurat untuk futures E-mini S&P 500 dan
menyimpulkan bahwa **VPIN memprediksi volatilitas semata-mata karena volatilitas yang naik
menginduksi error klasifikasi sistematis dalam BVC**. Makalah pendampingnya menemukan VPIN
**memuncak SETELAH flash crash, bukan sebelumnya**. Chakrabarty, Pascual & Shkilko (2015)
secara independen menemukan tick rule polos dan Lee-Ready mengalahkan BVC untuk saham semua
ukuran.

**Tiga kelompok independen dengan data ground-truth menemukan BVC kalah dari tick rule polos,
dan satu dari mereka menunjukkan metrik hasilnya adalah artefak dari metode penandaannya
sendiri. Divergensi delta bisa jadi artefak klasifikasi alih-alih peristiwa pasar, dan tidak
ada apa pun dalam kurikulum ritel yang membedakan keduanya.**

### 3.2 Trade imbalance adalah sinyal yang lebih lemah — dan satu-satunya yang bisa didekati

Dari teks lengkap Cont/Kukanov/Stoikov: order flow imbalance (event book) menjelaskan
**65%**; trade imbalance — yang persis diukur delta dan CVD — menjelaskan **32%**. Kata mereka:
"hubungan antara perubahan harga dan volume trade ditemukan **berisik dan kurang kokoh**."

**Jadi delta adalah konstruk yang lebih lemah, diukur lewat classifier berisik — dan di spot
XAUUSD kita sama sekali tidak bisa menghitungnya, karena tidak ada cetakan trade untuk
ditandai.**

### 3.3 Validasi terbitan untuk pola footprint? Praktis tidak ada

Pencarian Crossref untuk "volume delta divergence footprint chart trading" dan "market profile
Steidlmayer value area trading" hanya mengembalikan bab buku dagang Wiley — bukan studi
empiris. Sembilan query lanjutan lintas OpenAlex, Crossref, dan arXiv untuk "stacked
imbalance", "unfinished auction", "absorption", "cumulative volume delta" **tidak menemukan
validasi empiris peer-review untuk satu pun.**

Satu upaya serius tidak di-review dan hasilnya sendiri negatif: **Tolusic (2026)**, *Initiative
and Responsive: Auction Market Theory at the Signed Tape* (working paper, abstrak saja). 624
sesi futures terekam, 8 instrumen CME/CBOT, 11,5 juta pesan book:
- **"cerita rakyat footprint bahwa absorption adalah properti peristiwa bernilai nol tanpa
  syarat."** Yang bertahan adalah kondisional *lokasional* yang lemah.
- Arsip data praktisinya **gagal uji autentikasinya sendiri pada lapisan kedalaman** — vendor
  mengirim kedalaman hasil rekonstruksi seolah terekam. **Itu masalah DOM sintetis §1.2 yang
  muncul di feed berbayar kelas institusional.**

Satu makalah mengklaim return besar dari fitur divergensi CVD — **Moustafa, Neagu & Kalita
(2026)**, arXiv:2609.13825, +163,6% pada TSLA dan +116,5% pada NVDA. **Nilai (c).** Dua megacap
pilihan, periode tak dinyatakan, tanpa pengungkapan biaya, tanpa benchmark buy-and-hold saat
kedua nama itu melonjak, tidak di-review, dan fitur CVD adalah satu dari dua puluh dimensi di
dalam agen RL — tidak bisa diatribusikan. **Jangan kutip.**

### 3.4 Market Profile / TPO / Volume Profile

**Tidak ada validasi akademis yang ditemukan.** Korpusnya adalah buku praktisi. **Nilai (b)**,
bergeser ke **(c)** saat dikemas sebagai kurikulum berbayar.

Satu hasil tetangga yang nyata layak diketahui karena memberi *mekanisme* untuk metode berbasis
level: **Kavajecz & Odders-White (2004)**, *Technical Analysis and Liquidity Provision*, *RFS*
**(a)** — level support dan resistance **bertepatan dengan puncak kedalaman limit order book**.
Itu temuan jurnal top-3 yang nyata tentang level dan kedalaman. **Itu bukan validasi distribusi
TPO, value area, atau point of control.**

---

## 4. VSA dan Wyckoff

### 4.1 Validasi langsung: tidak ditemukan

Pencarian bibliografis Crossref untuk "Wyckoff method accumulation distribution trading
profitability" dan "volume spread analysis trading strategy empirical test" mengembalikan
hasil yang sama sekali tidak terkait. arXiv tidak punya apa-apa.

**Tidak ada validasi terbitan untuk klasifikasi bar spesifik — climax, absorption,
no-demand, effort-without-result.** Konsisten dengan kesimpulan kita tentang SMC/ICT.

### 4.2 Konteks yang memang ada **(a)**

- **Park & Irwin (2007)** — survei standar. Temuan positif sangat terkompromi oleh data
  snooping, seleksi aturan ex-post, dan estimasi biaya; profitabilitas menurun dari waktu ke
  waktu.
- **Lo, Mamaysky & Wang (2000)** — satu hasil simpatik, dan perhatikan betapa hati-hatinya:
  indikator teknikal tertentu "memberi **informasi inkremental** dan **mungkin punya sejumlah
  nilai praktis**." Kandungan statistik pada data ekuitas harian. **Bukan temuan
  profitabilitas, dan tidak ada apa pun tentang pola volume.**
- **Karpoff (1987)**, *JFQA*, dan **Jones, Kaul & Lipson (1994)**, *RFS* — keteraturan
  volume-harga asli yang disinggung VSA. **Hasil terkenal Jones/Kaul/Lipson adalah bahwa
  JUMLAH transaksi, bukan ukurannya, yang menggerakkan volatilitas** — justifikasi teoretis
  terbaik yang tersedia untuk memakai hitungan trade sebagai proksi aktivitas.

### 4.3 Apakah memakai tick volume alih-alih real volume membatalkan lapisan VSA kita?

Tidak persis — tapi itu mengubah apa yang bisa diklaim lapisan itu, dan mengungkap satu error
spesifik di kode kita.

**Bagian yang bisa dipertahankan.** Jones/Kaul/Lipson mendukung *hitungan* trade sebagai
proksi aktivitas, dan MetaQuotes mendefinisikan tick volume sebagai "jumlah tick yang diterima
selama pembentukan bar" — hitungan update kuotasi, sepupu dari hitungan trade. Implementasi
kita memakai **z-score terhadap 50 bar M1 sebelumnya dari simbol yang sama** (`VsaZScores`).
**Itu konstruksi yang benar**: ia tidak pernah membandingkan antar broker atau mengklaim
tingkat volume absolut, jadi ia mengukur "apakah bar ini luar biasa aktif untuk *feed ini*".

**Bagian yang tidak bertahan.** Tick count **spesifik broker**. Ia bergantung pada berapa
banyak liquidity provider yang diagregasi broker, throttling kuotasi mereka, dan filtering
mereka. Dua broker yang mengamati pasar emas yang sama akan melaporkan tick volume berbeda
untuk menit yang sama. **Tidak ada kalibrasi lintas broker yang mungkin, dan tidak ada ambang
dari artikel atau indikator mana pun yang bisa dipindahkan ke feed kita.**

**Error konkret yang harus diperbaiki.** `_classify_vsa` di
`adapter/app/scenarios/scalp_micro.py` mendefinisikan `VSA_NO_DEMAND` sebagai
`volume_z <= -0.5 AND range_z >= 1.0` — volume rendah dengan range **lebar**. **"No demand"
VSA kanonik adalah bar naik ber-spread SEMPIT, volume rendah, close dekat high-nya.** Kondisi
kita mendeskripsikan pergerakan tipis tanpa dukungan, yang ide yang bisa dipertahankan, tapi
itu bukan pola yang dirujuk namanya. Ganti namanya (`VSA_THIN_MOVE`) atau implementasikan
bentuk kanonik.

**Yang kedua, lebih besar.** Docstring modul di `adapter/app/scenarios/scalp_micro.py`
menyatakan: *"Order Book Imbalance (OBI) is approximated by tick volume z-score."* **Tidak.**
OBI adalah rasio *bertanda* volume bid menunggu terhadap volume ask; z-score tick-count adalah
ukuran aktivitas *tak bertanda* yang memuat nol informasi arah. **Keduanya objek berbeda, dan
menyebut yang satu aproksimasi dari yang lain akan menyesatkan tuning di masa depan.**

**Satu temuan empiris yang layak dimasukkan ke lapisan VSA.** Chaboud, Chernenko & Wright
(Federal Reserve IFDP 903) **(a)** menemukan pada data interdealer EBS bahwa di sekitar
pengumuman makro "harga melompat segera setelah pengumuman **tanpa banyak volume trading**,
sementara volume trading dan volatilitas lalu **melonjak sekitar 15 detik setelah** rilis
data." **Pada peristiwa yang paling menggerakkan emas, volume tertinggal dari harga.** Logika
"effort versus result" apa pun akan membaca bar-bar itu terbalik.

---

## 5. Alternatif praktis

### 5.1 Tick volume sebagai proksi — kualitasnya tidak diketahui, dan itu temuannya

Tidak ada studi yang ditemukan yang mengukur korelasi antara tick volume MetaTrader dan volume
FX/logam yang sebenarnya diperdagangkan. **Perlakukan ini sebagai celah asli, bukan negatif
yang sudah selesai.**

Alasan celah itu ada itu sendiri informatif. Dari makalah Federal Reserve (IFDP 903),
verbatim:

> *"**Ketiadaan data sampai sekarang telah menghalangi praktis semua riset tentang volume
> trading di pasar valuta asing.**"*

Ekonom Fed butuh dataset EBS proprietary untuk mempelajari volume FX sama sekali. **Tidak ada
benchmark publik untuk memvalidasi tick count broker kita.**

**Posisi praktis:** tetap pakai z-score. Jangan pernah pakai ambang absolut, jangan pernah
pindahkan ambang dari broker atau artikel lain, dan **jangan pernah mendeskripsikannya sebagai
ukuran volume** di kode atau dokumen. Itu ukuran aktivitas ternormalisasi pada satu feed
privat.

### 5.2 COMEX GC sebagai feed eksternal — upgrade terbaik, dan futures emas memimpin

**Sehgal, Sobti & Diesting (2021)**, *JFM* **(a)**. Data intraday 2010–2018 lintas pasar emas
matang dan berkembang. Verbatim: "kami menemukan bahwa **futures emas adalah pemimpin global
dalam price discovery dan volatility spillover**. Namun, selama 2016–2018, ETF berbasis emas
fisik dan spot menantang kepemimpinan futures di New York dan Shanghai."

**Jadi feed COMEX bukan sekadar pengganti data spot yang hilang — futures adalah tempat price
discovery emas terjadi.** Itu arah yang benar untuk mengimpor informasi.

**Biaya.** Dari halaman harga Databento: paket **$199/bulan (Standard, termasuk data live)**,
**$1.750/bulan (Plus)**, **$4.500/bulan (Unlimited)**, dengan fee lisensi CME "diteruskan tanpa
markup" — jumlah fee lisensinya sendiri tidak bisa diekstrak (JS-rendered).

**Di mana ia terpasang.** **Bukan di EA.** Adapter kita sudah punya bentuk untuk ini:
`adapter/app/news/market_context.py` menerima fitur DXY dan VIX dan mengembalikan modifier bias
yang dilipat ranker V3 dengan bobot 0,10. Feed GC adalah pola yang sama, dan `httpx` sudah ada
di `adapter/requirements.txt`. **EA tetap jadi execution plane yang tipis.**

**Apa yang akan dan tidak akan dibelinya.** Ia memberi volume riil, time-and-sales riil,
kedalaman riil pada instrumen yang memimpin price discovery. Ia **tidak** akan memberi sinyal
OBI yang bisa diperdagangkan untuk taker spot XAUUSD, karena alasan §2.3 dan §2.4 — OFI GC
meluruh dalam sekitar satu detik, dan kita akan bertindak di instrumen berbeda, lewat broker
bridge, membayar spread 20–35× kenaikan minimum. **Pakai untuk rezim dan konteks** (profil
volume sesi, z-score volume asli untuk menggantikan proksi tick-count, signifikansi level yang
dikonfirmasi volume, gerbang reaksi berita yang jujur), **bukan untuk timing burst.**

### 5.3 Data LBMA — tidak bisa dipakai untuk ini

Emas/perak/platinum/palladium lintas spot, swap/forward, option, dan loan/lease; **harian
berbasis T+1**; "**Data spesifik klien tidak disertakan, hanya jumlah agregat**"; akses lewat
**langganan Bloomberg atau Refinitiv saja**. **Total agregat T+1 tidak bisa menginformasikan
keputusan intraday.**

---

## 6. Garis bawah yang jujur

**Tidak ada footprint chart yang bisa dibangun.** Tidak ada cetakan trade untuk ditandai.
**Tidak ada delta atau CVD yang bisa dibangun.** Alasan sama. **Tidak ada OBI asli yang bisa
dibangun.** Dan kalau broker kita mengembalikan book, MetaQuotes bilang itu bisa berupa "level
harga **yang dihitung berdasarkan harga Bid dan Ask memakai price change step**". **Tick volume
adalah hitungan aktivitas privat broker**, hanya bisa dipakai sebagai z-score yang merujuk
dirinya sendiri.

**Dan bahkan dengan data sempurna, edge-nya tidak akan bertahan terhadap struktur biaya kita.**

| Bukti | Realitas V5 |
|---|---|
| OBI paling kuat untuk instrumen **large-tick** (Gould & Bonart: 20–30% vs **2–6%**) | Spread XAUUSD **20–35× kenaikan minimum** — ujung small-tick terjauh |
| Guncangan OFI "**menghilang hampir sepenuhnya dalam satu detik**" | Throttle burst 250ms + round trip HTTP + latensi eksekusi broker |
| **Peringkat latensi** menentukan siapa yang untung; peringkat 2 sudah rugi | EA ritel di belakang broker bridge |
| Taker OBI uang nyata: **−1,96 bp/trade** pada latensi nol dan fee terbaik | — |
| Uji XAUUSD book ritel langsung: **R²ₒₛ 0,045%**, dikeluarkan dari uji live | Instrumen sama, kelas feed sama |

Satu aritmetika, dari angka kita sendiri. Pada gerbang `InpMaxSpreadPoints = 30`, basket
sembilan market order lot sama membayar **9 × 30 = 270 points** spread entry. TP basket $5,00
atas sembilan posisi 0,01 lot (9 oz) butuh sekitar **55,6 points** pergerakan menguntungkan
rata-rata, atau ~500 points agregat. **Spread saja memakan kira-kira 54% target** — invarian
skala terhadap ukuran lot, sebelum slippage, sebelum hambatan adverse-selection, dan sebelum
mempertimbangkan bahwa input OBI-nya nol toh.

### Apa upgrade data minimum sebenarnya

**Tidak ada upgrade yang membuat analisis order flow bekerja DI SPOT XAUUSD.** Instrumennya
tidak punya order flow untuk diamati. Dua opsi nyata:

**Opsi A — ganti instrumen.** Perdagangkan **futures GC COMEX** lewat broker futures dengan
data pasar CME Globex MDP 3.0 (MBO). Ini satu-satunya jalan ke order flow asli pada emas:
volume riil, time-and-sales riil, kedalaman order-demi-order, dan venue yang memimpin price
discovery. GC juga instrumen large-tick dengan spread terkunci dekat satu tick — rezim Gould &
Bonart di mana queue imbalance memang memprediksi. **Ini berarti meninggalkan MT5 dan
meninggalkan CFD.** Bahkan begitu, §2.3 dan §2.4 bilang edge taker marginal paling banter;
kita akan memasuki balapan latensi yang tidak siap kita menangkan.

**Opsi B — tetap trading spot, impor GC sebagai konteks.** Berlangganan data CME Globex
(Databento mulai $199/bulan plus fee lisensi), konsumsi di **adapter**, mengikuti pola
DXY/VIX `market_context.py`. **Ini tidak akan memberi *sinyal* order flow.** Ia akan memberi
volume riil menggantikan proksi tick-count, profil volume sesi yang jujur, dan signifikansi
level yang dikonfirmasi volume. **Ini nilai yang lebih baik bagi kita** — perbaikan nyata pada
konteks, cocok dengan arsitektur yang sudah ada, dan tidak butuh berpura-pura sinyal sub-detik
bisa diperdagangkan pada 250ms.

---

## 7. Perubahan konkret untuk codebase

1. **Tutup item roadmap V5 "Real OBI/DOM integration (broker permitting via `MarketBookGet`)"**
   (`docs/v5-scalping-quickstart.md`) sebagai **tidak bisa dicapai di spot XAUUSD**.
   Membiarkannya pending menyiratkan ada broker yang akan memperbaikinya.
2. **Pertahankan dan perkuat guard DOM sintetis.** Tambahkan cek pada *volume* per-level
   (volume konstan atau simetris sempurna saat harga bergerak = sintetis), dan catat
   `SYMBOL_TICKS_BOOKDEPTH` sekali saat init.
3. **Perbaiki docstring** di `adapter/app/scenarios/scalp_micro.py`. Z-score tick-volume
   bukan aproksimasi OBI — ia aktivitas tak bertanda, tanpa kandungan arah.
4. **Ganti nama `VSA_NO_DEMAND`** atau implementasikan bentuk kanonik range-sempit.
5. **Pertimbangkan ulang veto keras `VSA_CLIMAX`.** Itu satu-satunya aturan VSA yang kita
   naikkan jadi gerbang pemblokir, dan ia bertumpu pada klasifikasi yang tak tervalidasi yang
   dihitung dari tick count privat broker. Temuan peer-review Patzelt & Bouchaud bahwa
   imbalance ekstrem menyertai *pematokan* alih-alih pergerakan besar menyiratkan intuisi
   exhaustion setidaknya tidak umum. **Ukur efek aktual veto itu pada hasil basket kita sendiri
   sebelum mempertahankannya sebagai veto alih-alih penalti.**
6. **Jalankan uji falsifikasi.** Sepuluh baris di terminal live: catat
   `SYMBOL_TICKS_BOOKDEPTH`, jumlah return `CopyTicks(..., COPY_TICKS_TRADE, ...)`, dan jumlah
   return `CopyRealVolume()` untuk simbol XAUUSD kita. **Itu mengubah deduksi §1.3 jadi
   pengukuran pada broker *kita*, yang nilainya melebihi kutipan mana pun di sini.**

---

## Referensi

**Tingkat (a) — dokumentasi platform, dikutip verbatim**

- MetaTrader 5 Help, *Price Data* —
  https://www.metatrader5.com/en/terminal/help/trading_advanced/price_data
- MetaTrader 5 Help, *Depth of Market* —
  https://www.metatrader5.com/en/terminal/help/trading/depth_of_market
- MQL5 docs — MarketBookAdd/Get, OnBookEvent, CopyTicks, MqlTick, MqlBookInfo, MqlRates,
  ENUM_BOOK_TYPE, SYMBOL_TICKS_BOOKDEPTH, CopyTickVolume, CopyRealVolume —
  https://www.mql5.com/en/docs
- MQL5, *Tick volume* — https://www.mql5.com/en/blogs/post/774938

**Tingkat (a) — kuantitatif, metodologi terbuka**

- Cont, R., Kukanov, A. & Stoikov, S. (2014). *The Price Impact of Order Book Events.*
  *Journal of Financial Econometrics* 12(1), 47–88. https://doi.org/10.1093/jjfinec/nbt003 ·
  https://arxiv.org/abs/1011.6402
- Gould, M. & Bonart, J. (2016). *Queue Imbalance as a One-Tick-Ahead Price Predictor in a
  Limit Order Book.* https://arxiv.org/abs/1512.03492
- Patzelt, F. & Bouchaud, J.-P. (2018). *Physical Review E* 97, 012304.
  https://doi.org/10.1103/PhysRevE.97.012304 · https://arxiv.org/abs/1706.04163
- Cont, R., Cucuringu, M. & Zhang, C. (2023). *Cross-impact of order flow imbalance in
  equity markets.* *Quantitative Finance* 23(10). https://doi.org/10.1080/14697688.2023.2236159 ·
  https://arxiv.org/abs/2112.13213
- Xu, K., Gould, M. & Howison, S. (2019). https://arxiv.org/abs/1907.06230
- Su et al. (2021). https://arxiv.org/abs/2112.02947
- Takahashi (2025). *Returns and Order Flow Imbalances.* https://arxiv.org/abs/2508.06788
- Hu & Zhang (2025). https://arxiv.org/abs/2505.17388
- Lucchese, L., Pakkanen, M. & Veraart, A. (2023). https://arxiv.org/abs/2211.13777
- Byrd, D., Palaparthi, S., Hybinette, M. & Balch, T. (2020). https://arxiv.org/abs/2006.08682
- Albers, J., Cucuringu, M., Howison, S. & Shestopaloff, A. (2025).
  https://arxiv.org/abs/2502.18625
- Kearns, M., Kulesza, A. & Nevmyvaka, Y. (2010). *Empirical Limitations on High Frequency
  Trading Profitability.* https://arxiv.org/abs/1007.2593
- **Jaddu, K. & Bilokon, P. (2023). *Combining Deep Learning on Order Books with
  Reinforcement Learning for Profitable Trading.* https://arxiv.org/abs/2311.02088 —
  uji XAUUSD** · [`../pdf/extract-jaddu.txt`](../pdf/extract-jaddu.txt)
- Cartea, Á., Donnelly, R. & Jaimungal, S. (2018). *Enhancing trading strategies with order
  book signals.* *Applied Mathematical Finance* 25(1). https://doi.org/10.1080/1350486X.2018.1434009
- Kolm, P., Turiel, J. & Westray, N. (2023). *Mathematical Finance* 33(4).
  https://doi.org/10.1111/mafi.12413
- DeLise, T. (2024). https://arxiv.org/abs/2407.16527
- Sehgal, S., Sobti, N. & Diesting, W. (2021). *JFM*. https://doi.org/10.1002/fut.22208
- Evans, M. & Lyons, R. (2002). *Order Flow and Exchange Rate Dynamics.*
  https://doi.org/10.1086/324391 · [`../pdf/extract-evanslyons.txt`](../pdf/extract-evanslyons.txt)
- BIS, Schrimpf, A. & Sushko, V. (2019). *FX trade execution: complex and highly fragmented.*
  https://www.bis.org/publ/qtrpdf/r_qt1912g.htm
- Chaboud, A., Chernenko, S. & Wright, J. *Trading Activity and Exchange Rates in High-Frequency
  EBS Data.* Federal Reserve IFDP 903.
  https://www.federalreserve.gov/pubs/ifdp/2007/903/default.htm
- LBMA, *Daily trade reporting data* —
  https://www.lbma.org.uk/prices-and-data/lbma-daily-trade-reporting-data ·
  https://www.lbma.org.uk/london-market
- Ellis, K., Michaely, R. & O'Hara, M. (2000). *JFQA* 35(4). https://doi.org/10.2307/2676254
- Odders-White, E. (2000). *JFM* 3(3). https://doi.org/10.1016/S1386-4181(00)00006-9
- Jurkatis, S. (2022). *JFM* 58. https://doi.org/10.1016/j.finmar.2021.100635 ·
  [`../pdf/extract-jurkatis.txt`](../pdf/extract-jurkatis.txt)
- Chakrabarty, B., Pascual, R. & Shkilko, A. (2015). *JFM* 25.
  https://doi.org/10.1016/j.finmar.2015.06.001
- Andersen, T. & Bondarenko, O. (2014). *Review of Finance* 18(6).
  https://doi.org/10.1093/rof/rfu041 · *JFM* 17: https://doi.org/10.1016/j.finmar.2013.05.005
- Easley, D., López de Prado, M. & O'Hara, M. (2012). *RFS*. https://doi.org/10.1093/rfs/hhs053
- Grauer, C., Schuster, P. & Uhrig-Homburg, M. (2023). https://doi.org/10.2139/ssrn.4098475
- Bilz (2023), tesis KIT — https://github.com/KarelZe/tclf
- Kavajecz, K. & Odders-White, E. (2004). *Technical Analysis and Liquidity Provision.*
  *RFS* 17(4). https://doi.org/10.1093/rfs/hhg057
- Park, C.-H. & Irwin, S. (2007). https://doi.org/10.1111/j.1467-6419.2007.00519.x
- Karpoff, J. (1987). *JFQA* 22(1). https://doi.org/10.2307/2330874
- Jones, C., Kaul, G. & Lipson, M. (1994). *RFS* 7(4). https://doi.org/10.1093/rfs/7.4.631
- Menkhoff, L., Sarno, L., Schmeling, M. & Schrimpf, A. *Information Flows in Foreign
  Exchange Markets.* BIS WP 405.
  [`../pdf/menkhoff-sarno-schmeling-schrimpf-bis-wp405-fx-customer-order-flow.pdf`](../pdf/menkhoff-sarno-schmeling-schrimpf-bis-wp405-fx-customer-order-flow.pdf)
- Goodhart, C., Ito, T. & Payne, R. *One Day in June 1993: A Study of the Working of the
  Reuters 2000-2 Electronic Foreign Exchange Trading System.* NBER.
  [`../pdf/goodhart-ito-payne-microstructure-of-fx-markets-nber.pdf`](../pdf/goodhart-ito-payne-microstructure-of-fx-markets-nber.pdf)
- Databento — https://databento.com/pricing · https://databento.com/datasets/GLBX.MDP3

**Tingkat (b)**

- *Markets in Profile* (Wiley) — https://doi.org/10.1002/9781119196709
- Kezeli, dalam *New Frontiers in Technical Analysis* — https://doi.org/10.1002/9781118531525.ch5
- Seluruh korpus Wyckoff/VSA (garis Tom Williams) — tidak ada uji empiris yang ditemukan.
- Tolusic (2026). *Initiative and Responsive.* https://doi.org/10.2139/ssrn.7135258 —
  working paper, abstrak saja.

**Yang harus dibuang (c)**

- Moustafa, Neagu & Kalita (2026), arXiv:2609.13825 — return tiga digit pada dua megacap
  pilihan, tanpa pengungkapan biaya, tanpa benchmark, tidak di-review.
- Produk "aggregated retail tick volume" mana pun yang tidak menerbitkan metodologi dan
  validasi terhadap benchmark volume riil.

## Yang tidak bisa diverifikasi

- **Spesifikasi kontrak dan jadwal fee data pasar CME sendiri** — cmegroup.com tidak
  terjangkau (403/timeout).
- **Studi yang mengukur tick volume MetaTrader terhadap volume FX/logam riil.** Celah terbuka.
- **Perilaku DOM XAUUSD broker ritel spesifik** — sebagian besar domain broker diblokir ISP,
  dan ini memang lebih baik dijawab dengan uji terminal sepuluh baris di §7.
