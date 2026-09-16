# Layering dan basket

Kapan layering dibenarkan, dan kapan itu martingale yang menyamar.

---

## 0. Jawaban singkat

Layering dibenarkan dalam **tepat satu konfigurasi** dan merupakan martingale menyamar
dalam semua yang lain.

**Dibenarkan:** menambah pada eksursi *menguntungkan*, dengan **satu stop bersama yang
bergerak naik**, dengan **setiap tambahan lebih kecil dari sebelumnya**, dengan **batas
keras pada total notional**, dan dengan **kondisi penambahan diturunkan ulang dari data
live** — bukan dari fakta bahwa harga bergerak.

**Martingale menyamar:** apa pun yang menambah ukuran saat posisi rugi, di mana "basket
pulih di break-even" adalah tesis exit-nya.

### Aritmetika inti yang harus dihayati

Untuk ladder *k* lot sama dengan jarak *d*, kerugian terbuka saat seluruh ladder di bawah
air adalah:

```
loss = L × contract × d × k(k−1)/2
```

**Kuadratik** terhadap jumlah layer, karena *k* tumbuh linear dengan jarak yang ditempuh.

Ladder 10 layer lot sama pada ekstensi penuh rugi **45×** apa yang dirisikokan layer
pertama saja atas satu langkah jarak. **Tidak ada yang butuh multiplier untuk meledak.
Lot sama sudah terasa eksponensial.** Multiplier 2,0× hanya membuatnya *benar-benar*
eksponensial: di 10 layer kerugiannya $35.455 vs $1.575 lot rata — **22,5× lebih buruk**
pada geometri identik.

---

## 1. Tiga teknik yang bukan varian dari satu hal

### (a) Scaling INTO pemenang — pyramiding

**Struktur:** tambah pada eksursi menguntungkan; stop bergerak naik ke/melewati
break-even; entry rata-rata memburuk; risiko dibiayai oleh profit terbuka.

**Bukti untuk ekspektasi positif — penilaian jujur: tidak ada eksperimen terkontrol yang
menunjukkan pyramiding mengalahkan satu entry ukuran penuh.** Yang ada:

- **(a)** Time-series momentum itu nyata dan mencakup emas. Moskowitz, Ooi & Pedersen
  (2012) mendokumentasikan persistensi return atas 1–12 bulan di 58 futures termasuk
  komoditas, sebagian berbalik sesudahnya. **Ini prasyarat perlu agar pyramiding bekerja**
  — kalau return IID, menambah saat kuat punya edge nol. Tapi itu tidak menetapkan bahwa
  pyramiding adalah cara optimal memanen persistensi itu. Dan ia beroperasi pada **horizon
  bulanan, bukan intraday M1** — peringatan tunggal terbesar untuk kasus kita.
- **(b)** Aturan Turtle adalah satu-satunya sistem pyramiding yang dispesifikasikan
  penuh dan terdokumentasi publik. Doktrin praktisi, tidak pernah divalidasi sebagai uji
  A/B terkontrol melawan sizing rata.
- **(c)** Halaman TurtleTrader sendiri "average up, never down" memuat **nol** backtest,
  dataset, atau track record.

**Koreksi penting terhadap cerita rakyat — pyramiding tidak gratis.** Di bawah aturan
Turtle kanonik (tambah tiap 0,5N, semua stop pindah ke 2N di bawah entry terbaru, 1 unit
× 1N = 1% ekuitas):

| Unit | Stop bersama | Total risiko | Profit terbuka | Pengembalian dari puncak |
|---|---|---|---|---|
| 1 | P0 − 2,0N | 2,00% | 0,00% | 2,00% |
| 2 | P0 − 1,5N | 3,50% | 0,50% | 4,00% |
| 3 | P0 − 1,0N | 4,50% | 1,50% | 6,00% |
| 4 | P0 − 0,5N | **5,00%** | 3,00% | **8,00%** |

Pyramid Turtle 4 unit penuh merisikokan **5% ekuitas** dan **pengembalian 8% dari puncak
profit terbuka**. Ia mengubah taruhan 1% jadi taruhan 5%. **Itu trade yang sebenarnya
dibuat, dan sebagian besar deskripsi pyramiding menyembunyikannya.**

**Profil risiko:** skew kanan. Banyak pengembalian kecil, sesekali tangkapan besar. Ekor
kiri **dibatasi** stop yang bergerak naik. Ekspektasi positif **jika dan hanya jika** aset
dasarnya punya persistensi tren di horizon kita.

### (b) Scaling INTO pecundang — averaging down / grid / martingale

**Struktur:** tambah pada eksursi merugikan; tesis exit adalah "harga kembali ke rata-rata
yang membaik"; risiko tumbuh tanpa batas kecuali ada stop keras.

**Bukti — di sinilah data nyata berada, dan seragam buruk:**

- **(a)** **Chen, Chen & Jang (2025)** membuktikan **grid trading tradisional bernilai
  harapan nol** di bawah asumsi standar, dan **bernilai harapan negatif saat harga
  bergerak linear** (yaitu tren). arXiv:2506.11921.
- **(a)** **Odean (1998)**, 10.000 akun: investor **1,5–2× lebih mungkin menjual pemenang
  daripada pecundang**, dan menahan pecundang **tidak** dibenarkan oleh kinerja berikutnya.
  **EA grid/averaging-down adalah disposition effect yang dikompilasi ke MQL5.**
- **(a)** **FXCM 43 juta trade:** trader EUR/USD menang **61%** waktu tapi rata-rata
  **48 pip menang vs 83 pip kalah (1,73×)**. Win rate tinggi + pecundang besar = rugi
  bersih. **Itu tanda tangan statistik persis yang dihasilkan grid.**
- **(a)** **Heimer & Simsek (2019, *JFE*)**: sebelum batas leverage CFTC 2010, **trader FX
  ritel leverage tinggi rata-rata rugi 44% per bulan**. Batas itu memotong kerugian
  ~**40%**, memotong volume 23%, dan — kritis — **mengurangi disposition effect** dengan
  menaikkan biaya peluang menunda realisasi kerugian. **Bukti kausal bahwa membatasi
  eksposur menghentikan orang menahan pecundang.**
- **(a)** **ESMA (2018): 74–89%** akun CFD ritel rugi; rata-rata **€1.600–€29.000** per
  klien.
- **(a)** **AMF Prancis (2014)**, 14.799 investor 2009–2012: **89% rugi**, agregat
  **−€175 juta**. AMF secara spesifik menemukan **kinerja menurun seiring leverage dan
  seiring jumlah trade**.

**Profil risiko:** skew kiri dengan ekor kiri panjang dan gemuk. **Win rate tinggi dijamin
secara struktural dan bukan bukti edge.**

**Win rate impas** — grid yang rugi *X* kali dari yang dimenangkannya butuh:

| Rasio kalah/menang | Win rate dibutuhkan | Satu kekalahan menghapus |
|---|---|---|
| 5× | 83,33% | 5 kemenangan |
| 10× | 90,91% | 10 kemenangan |
| 20× | **95,24%** | 20 kemenangan |
| 50× | 98,04% | 50 kemenangan |
| 100× | 99,01% | 100 kemenangan |

**EA grid "win rate 95%" yang berjalan di rasio 20:1 kalah/menang tepat impas sebelum
biaya, karenanya negatif setelah biaya. Angka marketing-nya dan hukuman matinya adalah
angka yang sama.**

### (c) Limit order bertangga serentak di sekitar zona

**Ini yang sebenarnya kita bangun, dan ini TIDAK sama dengan (b)** — tapi hanya kalau
stopnya nyata.

**Struktur:** semua order ditempatkan sekaligus; sebagian terisi, sebagian tidak; total
risiko dibatasi stop yang berada di bawah seluruh ladder.

**Yang membedakannya dari grid:** sifat penentu grid adalah ia *tidak punya stop* —
mekanisme pemulihan menggantikan stop. Entry bertangga dengan stop basket keras hanyalah
**teknik eksekusi pasif**: mencoba mendapat harga fill rata-rata lebih baik daripada satu
market order.

**Yang membuatnya tetap berbahaya — adverse selection.** Buy-limit di `anchor − i·d`
hanya terisi kalau harga turun `i·d`. Jadi **kedalaman fill berkorelasi sempurna dengan
tesis yang salah.** Dengan pendekatan prinsip refleksi pada random walk tanpa drift dengan
σ harian emas ≈ $70:

| Jarak | P(1 layer tersentuh, 1 hari) | P(3 layer) | P(5 layer) |
|---|---|---|---|
| $10 | 88,6% | 66,8% | 47,5% |
| $20 | 77,5% | 39,1% | 15,3% |
| $35 | 61,7% | 13,4% | 1,2% |
| $50 | 47,5% | 3,2% | 0,04% |

Jarak rapat berarti hampir selalu dapat kedalaman penuh — yang berarti **ladder-nya
kosmetik** dan kita hanya mengambil posisi ukuran penuh di rata-rata yang sedikit lebih
baik. Jarak lebar berarti fill dalam jarang — tapi saat terjadi, itu terjadi **dalam
tren**, persis ketika entry rata-rata kita paling buruk dan pergerakannya berlanjut.

### Putusan ekspektasi

| Teknik | Ekspektasi | Ekor |
|---|---|---|
| (a) Pyramiding saat kuat | Positif **jika** ada persistensi tren di horizon kita; belum terbukti di M1 | Ekor kanan; ekor kiri dibatasi stop naik |
| (b) Averaging down / grid / martingale | **Nol paling banter, negatif setelah biaya** — terbukti | Ekor kiri panjang; tanpa batas tanpa stop keras |
| (c) Limit bertangga dengan stop basket nyata | **Netral eksekusi.** Fill rata-rata sedikit lebih baik, diimbangi *k* spread dan adverse selection | Dibatasi stop — *asalkan stop disizing untuk fill penuh* |

**Kategori (c) tidak menciptakan edge.** Ia sedikit memperbaiki atau sedikit memperburuk
edge yang sudah kita punya. **Kalau detektor skenario kita tidak punya edge, laddering
tidak akan memproduksinya.**

---

## 2. Bagaimana EA grid dan martingale benar-benar mati

### Kasus paling terdokumentasi **(a)**

Review pembeli MQL5 Market adalah kelas sumber terkuat di sini — MQL5 memverifikasi
pembelian, dan penjual bisa membalas tapi **tidak bisa menghapus** review.

Sebuah EA grid populer, klaim vendornya: **70+ bulan profit beruntun, +8.000% sampai
+12.000% sejak 2018.**

Lini masa: ~2018 peluncuran → **~5 tahun kurva ekuitas bersih** → **Juni 2023 gelombang
pertama** → **Maret 2024 gelombang kedua** → laporan akun meledak sampai Juni 2024.

Laporan pembeli terverifikasi: akun risiko tinggi "tersapu bersih"; **drawdown 52%** pada
risiko rendah, **72%** pada moderat; "sekitar **40–50% DD** memakai risiko menengah";
"**drawdown historis 50% telah tercapai** di akun developer"; "meledakkan akun saya setelah
menahan beberapa posisi SELL di NZDCAD **selama lebih dari sebulan**."

**Temuan tunggal terpenting di seluruh file ini: pembunuhnya BUKAN black swan.** Itu tren
berkelanjutan yang biasa saja di AUDCAD/NZDCAD/AUDNZD. Pemulihan drawdown Maret 2024
dilaporkan butuh 80+ hari untuk menutup posisi.

**Konsekuensi desain: sizing ladder kita melawan tren satu arah yang membosankan selama
empat minggu, bukan melawan de-peg. Tren adalah kasus umum; gap adalah yang langka.**

### Emas secara spesifik **(b)**

EA grid emas dari developer yang sama — deskripsinya secara terbuka menyatakan "sistem
grid trading mean-reversion" dengan multiplier grid. Laporan pembeli: "Ratusan pengguna
juga telah meledakkan akun mereka dalam beberapa kesempatan di 2023"; **akun $75.000 pada
0,50 lot** "terbakar oleh pergerakan hari itu." Satu reviewer menuduh akun sinyal penulis
yang meledak "telah disembunyikan." **Penghapusan sinyal itu tidak bisa diverifikasi
secara independen.**

### Peristiwa gap — untuk kalibrasi, bukan sebagai risiko utama **(a)**

- **15 Jan 2015, de-peg CHF.** Data per detik FXCM sendiri: pada 04:31:08 hanya **satu**
  LP yang mengutip, 1.000 pip di bawah floor; pada 04:42:28 EBS melompat 0,9550 → 0,5000 —
  **4.500 pip dalam satu detik**. FXCM: *"kemampuan kecil untuk mengeksekusi stop order
  klien atau margin call karena nyaris nol kuotasi yang efektif bisa dieksekusi"* dan
  *"bagi siapa pun yang posisinya di atas 8X saldo mereka, hasilnya kehilangan total."*
  Kerugian: **FXCM $225 juta** saldo negatif klien; **Alpari UK bangkrut**; **IG sampai
  £30 juta**.
- **2–3 Jan 2019, flash crash JPY.** Broker Jepang: **¥943 juta / $8,6 juta**, **6.389
  pelanggan ritel** dengan saldo negatif.

**Stop kita bukan stop selama peristiwa ini.** Kodekan sesuai itu: stop adalah *niat*, dan
satu-satunya kontrol eksposur yang bertahan dari gap adalah **total notional**.

### Waktu bertahan tipikal, dan mengapa backtest berbohong

Rule of three (Hanley & Lippman-Hand): nol ledakan dalam *n* observasi ⟹ batas atas 95%
probabilitas ledakan per basket ≈ **3/n**.

| Basket diamati tanpa ledakan | Batas atas 95% p | P(hancur dalam 1.000 basket berikutnya) |
|---|---|---|
| 100 | 3,0% | 100% |
| 200 | 1,5% | 100% |
| 500 | 0,60% | **99,8%** |
| 1.000 | 0,30% | 95,0% |
| 2.000 | 0,15% | 77,7% |
| 5.000 | 0,060% | 45,1% |

**Backtest 500 basket dengan kurva ekuitas sempurna secara statistik konsisten dengan
kehancuran yang nyaris pasti.** Lima tahun bersih EA grid itu bukan bukti; itu sampel yang
belum bertemu ekornya. **Kurva vendor apa pun yang lebih pendek dari ~5.000 basket nyaris
tidak memberi tahu apa pun tentang ekornya.**

### Progresi lot: angka persisnya

Total lot setelah *k* layer, basis 0,01:

| k | rata | 1,3× | 1,5× | 2,0× |
|---|---|---|---|---|
| 5 | 0,050 | 0,090 | 0,132 | 0,310 |
| 8 | 0,080 | 0,239 | 0,493 | 2,550 |
| 10 | 0,100 | 0,426 | 1,133 | **10,230** |
| 12 | 0,120 | 0,743 | 2,575 | **40,950** |

Kerugian terbuka pada jarak $35, basis 0,01 lot:

| k | jarak | rata | 1,3× | 1,5× | 2,0× |
|---|---|---|---|---|---|
| 5 | $140 | $350 | $472 | $573 | $910 |
| 8 | $245 | $980 | $1.850 | $2.888 | $8.645 |
| 10 | $315 | $1.575 | $3.806 | $7.233 | **$35.455** |
| 12 | $385 | $2.310 | $7.271 | $17.184 | **$142.905** |

**Batasan yang mengikat pertama bukan modal kita — itu batas max-lot broker.** Catatan
praktisi di MQL5: *"Sebagian besar broker hanya mengizinkan 50 lot sebelum dipotong."*
Pada 2× dari 0,01 kita menabrak 50 lot sekitar langkah ke-13.

### Matematika margin call — dan mengapa itu hal yang salah untuk dikhawatirkan

`Margin Level % = Ekuitas / Margin Terpakai × 100`. Stop-out tipikal 50%.

Basket *k* layer 0,01 lot sama, jarak $35, leverage 1:500, ekuitas $10.000, emas $4.300:

| k | Margin terpakai | Rugi terbuka | Ekuitas | Margin level |
|---|---|---|---|---|
| 10 | $86 | $1.575 | $8.425 | 9.797% |
| 15 | $129 | $3.675 | $6.325 | 4.903% |
| 20 | $172 | $6.650 | $3.350 | 1.948% |

**Pada leverage tinggi, stop-out tidak pernah menyelamatkan kita.** Akun sudah hancur 67%
sementara margin level masih membaca 1.948%. **Perlindungan margin call adalah mekanisme
solvabilitas broker, bukan kontrol risiko. Kontrol risiko kita harus trigger drawdown
ekuitas, bukan trigger margin level.**

---

## 3. Aturan risiko kalau tetap layering

### 3.1 Jumlah maksimum layer — **3, batas keras 4**

- **(b)** Satu-satunya sistem terdokumentasi dengan batas eksplisit adalah Turtle: **4 unit
  per pasar, 6 berkorelasi erat, 10 berkorelasi longgar, 12 satu arah**.
- Kerugian terburuk berskala `k(k−1)/2`. Dari 3 ke 6 layer melipatgandakan eksposur
  terburuk **5×** pada lot per layer yang sama.
- Untuk menjaga total risiko konstan, lot per layer harus menyusut ~`2/(k(k+1))`. **Di 10
  layer setiap lot tinggal 1,8%** dari yang dibawa trade posisi tunggal. Risiko sama,
  profit harapan sama, tapi bayar 10 spread. **Tidak ada alasan melakukan ini.**

### 3.2 Jarak — `d = max(0,75 × ATR(H1), 20 × spread_saat_ini)`

**Lantai biaya tidak bisa ditawar:**

| Jarak | Biaya per langkah @ spread $0,25 | @ $0,60 (berita) |
|---|---|---|
| $1 | 25,0% | 60,0% |
| $2 | 12,5% | 30,0% |
| $5 | 5,0% | 12,0% |
| $10 | 2,5% | 6,0% |
| $20 | 1,2% | 3,0% |
| $35 | 0,7% | 1,7% |

Jarak harus **≥20× spread live** untuk menjaga biaya di bawah 5% per langkah. Di emas itu
berarti **≥$5 minimum absolut, ≥$12 untuk nyaman. Ladder M1 berjarak rapat mati karena
biaya jauh sebelum mati karena tren.**

**Tetap vs ATR vs adaptif volatilitas: pakai ATR.** Jarak titik tetap adalah **mekanisme
yang membuat EA grid meledak** — saat volatilitas berlipat, grid tetap terisi dua kali
lebih cepat pada ukuran lot yang sama, melipatgandakan eksposur persis saat pasar paling
berbahaya. **Baca ATR sekali saat plan dan bekukan untuk umur basket**, supaya geometrinya
tidak bergeser di bawah kita.

### 3.3 Progresi lot — **rata atau menurun. Tidak pernah menaik. Tidak bisa ditawar.**

Pada 6 layer, jarak $35, basis 0,01:

| Progresi | Total lot | Offset entry rata-rata | Rugi terbuka | Jarak impas |
|---|---|---|---|---|
| menurun 1/(i+1) | 0,025 | −$50,7 | −$304 | $124,3 |
| menurun 0,8^i | 0,037 | −$65,4 | −$404 | $109,6 |
| **rata** | 0,060 | −$87,5 | −$525 | $87,5 |
| menaik 1,3^i | 0,128 | −$113,2 | −$788 | $61,8 |
| menaik 1,5^i | 0,208 | −$125,2 | −$1.035 | $49,8 |
| martingale 2,0^i | 0,630 | −$143,3 | **−$1.995** | $31,7 |

Baca dua kolom terakhir bersama. Lot menaik *memang* mengurangi jarak impas — dari $87,5
ke $31,7. **Itulah seluruh godaan martingale, dan itu nyata.** Yang dibayar untuknya
adalah **rugi terbuka 3,8× lebih besar** pada harga yang sama. Kita menukar probabilitas
lebih tinggi untuk kemenangan kecil dengan kerugian jauh lebih besar saat pemulihan tidak
datang. **Trade itu berekspektasi negatif setiap kali kelipatan kerugiannya melebihi
`(1−p)/p`.**

**Untuk pyramiding secara spesifik** — satu-satunya aturan yang menjaga total risiko di
1R dengan trailing stop bersama di 2N di bawah entry terbaru:

```
kemajuan_dibutuhkan_dalam_N = 2 × (ukuran_tambahan / ukuran_dasar)
```

| Ukuran tambahan | Kemajuan menguntungkan dibutuhkan sebelum menambah |
|---|---|
| 1,00× dasar | 2,00N (1R penuh) |
| 0,50× dasar | 1,00N |
| 0,33× dasar | 0,66N |
| 0,25× dasar | 0,50N |

**Ukuran tambahan yang mengecil adalah yang membuat pyramid netral-risiko mungkin sama
sekali. Rumus ini hasil paling langsung bisa dikodekan di seluruh file ini.**

### 3.4 Batas eksposur total basket — **dua batas, keduanya ditegakkan, mana yang mengikat duluan**

**Batas 1 — risiko ekuitas:** total risiko basket ≤ **0,5–1,0% ekuitas**, diukur sebagai
fill ladder penuh **plus satu langkah jarak tambahan** melewati layer terakhir.

```
loss_ladder_penuh = L × 100 × [ k × S + d × k(k−1)/2 ]
```

di mana `S` = lari merugikan melewati layer terakhir. Selesaikan untuk `L`. **Ini rumus
sizing yang benar; men-sizing setiap layer independen seolah trade berdiri sendiri
meremehkan risiko basket sebesar `k(k−1)/2 × d / S`.**

**Batas 2 — notional, dijangkarkan ke regulasi.** ESMA menetapkan leverage CFD emas pada
**20:1** (margin awal 5%) memakai simulasi yang dikalibrasi agar penahanan 1–5 hari punya
**probabilitas ~5% close-out margin 50%**. ESMA eksplisit mencatat leverage tersirat emas
*kira-kira dua kali minyak*, itu sebabnya emas dapat pita 20:1 sendiri sementara komoditas
lain dapat 10:1.

Diterjemahkan ke batas keras total lot semua layer pada emas ≈ $4.300:

| Ekuitas | 20:1 (plafon) | **10:1 (disarankan)** |
|---|---|---|
| $1.000 | 0,047 | 0,023 |
| $2.000 | 0,093 | 0,047 |
| $5.000 | 0,233 | 0,116 |
| $10.000 | 0,465 | 0,233 |
| $25.000 | 1,163 | 0,581 |

**Rekomendasi 10:1 sebagai batas kerja** karena kalibrasi ESMA memakai data emas pra-2018,
dan volatilitas terealisasi emas 2025–26 jauh lebih tinggi. *Risiko sama, separuh
leverage.*

**Dinding lot minimum adalah stop keras bagi akun kecil:**

| Ekuitas | Batas 10:1 | Maks layer 0,01 lot |
|---|---|---|
| $1.000 | 0,023 lot | **2** |
| $2.000 | 0,047 lot | **4** |
| $5.000 | 0,116 lot | 11 |

**Akun $1.000 tidak bisa menjalankan ladder emas 3 layer dalam batas eksposur yang bisa
dipertahankan. Itu aritmetika, bukan opini. Gerbangkan strategi pada ekuitas minimum di
kode dan tolak berdagang di bawahnya.**

### 3.5 Ukuran stop bersama — **satu stop basket, di struktur, disizing agar fill penuh + stop = anggaran risiko**

Stop *nyata* ladder adalah `(k−1)·d + S` — total kedalamannya. Bandingkan dengan ATR harian
sebelum menerimanya. Dengan anggaran risiko $100 pada $10.000:

| k | d | lot/layer | total lot | Lari melewati layer terakhir | Total kedalaman ladder |
|---|---|---|---|---|---|
| 1 | — | 0,0286 | 0,0286 | $35,0 | $35 (0,5 × dATR) |
| 3 | $35 | 0,0048 | 0,0144 | $34,4 | $104 (1,5 × dATR) |
| 5 | $35 | 0,0019 | 0,0095 | $35,3 | $175 (2,5 × dATR) |

**Stop yang lebih dalam dari ~2× ATR harian pada strategi intraday bukan strategi
intraday.** Jaga total kedalaman ladder di bawah **1,5 × ATR harian**.

**Stop per-layer salah karena tiga alasan:**
1. Mereka menghentikan kaki berharga terbaik lebih dulu dalam whipsaw, meninggalkan entry
   terburuk hidup.
2. Mereka membuat total risiko bergantung pada urutan fill, yang tidak bisa kita kontrol.
3. Mereka mengundang logika "satu kaki kena stop, tambahkan pengganti" yang mengubah
   ladder jadi grid.

---

## 4. Logika exit basket

### 4.1 TP bersama vs per-posisi — **pakai TP bersama, tapi skalakan ke risiko HIDUP**

**Bug konkret yang sering terjadi:** spesifikasi 5 layer, total risiko 1%, TP basket tetap
1,5% ekuitas.

| Layer terisi | Risiko hidup | Target TP | Kelipatan R dibutuhkan |
|---|---|---|---|
| 1 | 0,20% | 1,5% | **7,5R** |
| 2 | 0,40% | 1,5% | 3,8R |
| 3 | 0,60% | 1,5% | 2,5R |
| 5 | 1,00% | 1,5% | 1,5R |

Basket yang terisi sebagian harus menempuh **7,5R** untuk mencapai target yang disizing
untuk fill penuh. Ia akan kena stopnya duluan, nyaris selalu. **Ini membuat distribusi
terealisasi strategi jadi "menang hanya ketika seluruh ladder terisi" — yaitu menang hanya
ketika tesis awal SALAH dan harga menembus seluruh zona kita. Itu profil payoff grid yang
tercapai secara tidak sengaja.**

**Perbaikan, hitung ulang di setiap fill:**

```
risiko_hidup$ = Σ atas layer TERISI dari lot_i × 100 × |harga_i − SL_basket|
TP_basket$    = RR × risiko_hidup$
```

### 4.2 Tutup di break-even + biaya

**Sah sebagai aturan de-risking, berbahaya sebagai tesis exit.**

- **Sah:** "kalau basket kembali ke break-even+biaya setelah `T` menit tanpa mencapai
  target, tutup flat." Ini time stop berpakaian lain dan itu baik-baik saja.
- **Berbahaya:** "tahan sampai break-even+biaya tercapai." **Itu mekanisme pemulihan
  grid. Itu yang membunuh akun.** Kalau muncul di mana pun di kode kita tanpa stop keras
  yang mendominasinya, kita sudah membangun martingale.

Biaya harus mencakup *k* spread, *k* komisi, dan swap.

### 4.3 Menutup sebagian basket — kaki paling untung vs kaki tertua

**Ini pertanyaan dengan jawaban matematis paling jelas dan bukti publik paling sedikit.**

Ladder buy terisi di 4300 / 4265 / 4230 / 4195, harga sekarang 4210 (entry rata-rata
4247,5, rugi terbuka −$150, jarak impas +$37,5):

| Kebijakan | Terealisasi | Entry rata-rata baru | PnL sisa | Jarak impas baru | Eksposur |
|---|---|---|---|---|---|
| Tutup kaki **paling untung** (terbaru, entry terendah) | +$15 | 4265,0 | −$165 | **+$55,0** | 0,03 |
| Tutup kaki **paling rugi** (tertua, entry tertinggi) | −$90 | 4230,0 | −$60 | **+$20,0** | 0,03 |
| Close-by (pasangkan terbaik + terburuk) | −$75 | 4247,5 | −$75 | +$37,5 | 0,02 |
| Proporsional (paruh setiap kaki) | −$75 | 4247,5 | −$75 | +$37,5 | 0,02 |

**Menutup kaki paling untung lebih dulu MENAIKKAN entry rata-rata basket sisa dan membuat
pemulihan lebih sulit.** Itu disposition effect yang diimplementasikan sebagai fitur: ia
membukukan profit dan membiarkan entry terburuk hidup. Terkonfirmasi di ladder 8 kaki:
menutup 2 kaki paling untung meninggalkan kebutuhan impas $227,5 vs $157,5 kalau menutup 2
yang paling rugi.

**Peringkat:**
1. **Pemangkasan proporsional** — satu-satunya pengurangan netral-eksposur; jarak impas
   tidak berubah. Pakai ini kalau harus de-risk.
2. **Tutup kaki paling rugi (tertua)** — secara matematis memperbaiki basket sisa, tapi
   membukukan kerugian terealisasi terbesar. Hanya benar kalau kita keluar dari tesis.
3. **Tutup kaki paling untung** — **tidak pernah.** Tegas lebih buruk di setiap metrik
   kecuali screenshot P/L.

### 4.4 Time exit — **ya, pakai**

`basket_max_age = 4 jam` untuk emas intraday, plus paksa flat sebelum rollover harian dan
sebelum rilis berdampak tinggi terjadwal. → [08](08-take-profit-dan-exit.md) §10

---

## 5. Kondisi yang bisa dikodekan untuk "apakah tambahan ini dibenarkan?"

Setiap gerbang harus bernilai `true` pada **bar tertutup**, dievaluasi pada saat
penambahan, **tidak diwarisi dari saat plan**.

**Gerbang 1 — Arah tambahan.**
```
tambahan_pada_eksursi_menguntungkan == true
```
Kalau false, kita sedang averaging down. Semua di bawah tidak relevan. Satu-satunya
pengecualian adalah entry bertangga *yang sudah direncanakan* (kategori c) di mana semua
order ditempatkan serentak dengan stop basket sudah hidup — dan bahkan saat itu, tidak ada
order yang boleh *ditambahkan* setelahnya.

**Gerbang 2 — Tesis awal masih benar.** Jalankan ulang detektor skenario yang sama yang
membuka basket:
```
skenario_sekarang.nama  == basket.skenario
skenario_sekarang.sisi  == basket.sisi
skenario_sekarang.skor  >= basket.skor_entry    // bukan sekadar >= MIN_SCORE
```
Menuntut skor **setidaknya sekuat saat entry** adalah proksi objektif murah untuk "tesis
belum meluruh."

**Gerbang 3 — Struktur utuh.** Untuk long:
```
tidak ada bar M15 tertutup yang close di bawah basket.swing_low_saat_entry
DAN close(H1) > EMA50(H1)
```
**Close** menembus struktur, bukan wick — wick adalah noise, close adalah informasi.

**Gerbang 4 — Pullback dalam tren** (untuk continuation-pullback).
```
ADX(H1) >= 22
persen_retracement_dari_impuls_terakhir <= 0,618
jarak(harga, EMA20_M5) <= 0,5 * ATR(M5)
```
Membatasi retracement yang membedakan "pullback" dari "reversal sedang berlangsung" di kode.

**Gerbang 5 — Rezim volatilitas tak berubah.** Gerbang yang akan menyelamatkan pengguna
EA grid tadi.
```
atr_ratio = ATR(14, M15) / ATR(100, M15)
atr_ratio <= 1,8                          // ekspansi vol → bekukan tambahan
atr_ratio >= 0,5                          // vol kolaps → tesis tak lagi valid
ATR(M15) <= 1,5 * ATR_saat_plan           // geometri dibekukan saat plan
```

**Gerbang 6 — Kill switch tren-melawan.** Tindakan langsung terhadap mode kegagalan yang
terdokumentasi (tren berkelanjutan yang membosankan, bukan gap). Kaufman Efficiency Ratio
atas *n* bar:
```
ER = |close[0] - close[n]| / Σ|close[i] - close[i-1]|
jika ER > 0,60 DAN sign(close[0] - close[n]) != basket.sisi:
      blokir semua tambahan DAN tutup basket segera
```
**Jangan tunggu stop basket.** Pergerakan ER tinggi melawan kita adalah pasar yang bilang
premis mean-reversion sudah mati. **Ambang 0,60 adalah saran, bukan nilai tervalidasi —
kalibrasi pada data emas sendiri.**

**Gerbang 7 — Biaya dan likuiditas.**
```
spread <= 0,35 * ATR(M1)
spread * 20 <= jarak_layer
tidak dalam jendela blackout berita
```

**Gerbang 8 — Eksposur.**
```
total_lot_setelah_tambahan <= 10 * ekuitas / (harga_emas * 100)
risiko_basket_hidup_setelah_tambahan <= anggaran_risiko
```

**Gerbang 9 — Anggaran.**
```
rugi_terealisasi_harian_pct < stop_harian
basket_kalah_berturut < maks_rentetan
```

---

## 6. Matematika sizing posisi

### 6.1 Kelly untuk basket

**Kesalahan sentral yang harus dihindari: semua kaki basket searah pada satu instrumen
berkorelasi sempurna. Itu SATU taruhan, bukan *k* taruhan.** Menerapkan Kelly per kaki
melebih-lebihkan kapasitas kira-kira *k*×.

`f* = p − q/b`, di mana `b` = payoff pada **basket**:

| Skenario | p | b | Full Kelly f* | Quarter Kelly |
|---|---|---|---|---|
| Basket tren, menang 40%, 2,5:1 | 0,40 | 2,50 | 16,0% | 4,0% |
| Seimbang, menang 55%, 1,2:1 | 0,55 | 1,20 | 17,5% | 4,4% |
| Mean-revert, menang 70%, 0,5:1 | 0,70 | 0,50 | 10,0% | 2,5% |
| **Grid, menang 90%, rugi 10× kemenangan** | 0,90 | 0,10 | **−10,0%** | — |
| **Grid, menang 95%, rugi 20× kemenangan** | 0,95 | 0,05 | **−5,0%** | — |

**`f*` negatif berarti TIDAK ADA ukuran taruhan yang aman — alokasi yang benar adalah
nol.**

**Jangan trading full Kelly.** Full Kelly menghasilkan drawdown 50%+; simulasi yang
dilaporkan menempatkan rata-rata max drawdown full-Kelly sekitar 60% dengan persentil 95
sekitar 82%, versus half-Kelly kira-kira separuhnya untuk kekayaan akhir ~8% lebih rendah.
Lebih menentukan lagi: `p` dan `b` adalah *estimasi*, dan Kelly sangat sensitif terhadap
error estimasi. **Pakai quarter Kelly paling banyak, dan batasi pada anggaran
fixed-fractional kita (0,5–1%), ambil yang lebih kecil.**

### 6.2 Risk of ruin

Pendekatan difusi: **`RoR = exp(−2μb/σ²)`** — μ = ekspektasi $ P/L per basket, σ² = varians
per basket, b = dolar tunjangan drawdown sampai ambang kehancuran. Diturunkan dari gerak
Brown di Chen & Ankenman, *The Mathematics of Poker* (2006), Bab 22.

Akun $10.000, kehancuran didefinisikan −30% (b = $3.000), edge +0,1R:

| Risiko per basket | RoR |
|---|---|
| 1% | **0,25%** |
| 2% | 4,98% |
| 5% | **30,12%** |
| berapa pun %, **edge nol** | **100%** |

**Baris terakhir itu seluruh argumennya. Dengan edge nol, risk of ruin 100% terlepas dari
sizing.** Grid trading terbukti berekspektasi nol sebelum biaya dan karenanya negatif
sesudahnya — jadi **tidak ada aturan sizing yang membuatnya bisa bertahan.**

Perhatikan betapa kerasnya RoR merespons risiko per basket: 1% → 0,25%, 5% → 30%. **Kenaikan
ukuran posisi 5× adalah kenaikan probabilitas kehancuran 120×.** Itu kasus kuantitatif
untuk pita 0,5–1%.

### 6.3 Mengapa max drawdown berskala dengan jumlah layer

`kerugian_terburuk ∝ k(k−1)/2` pada lot per layer konstan:

| k | 1 | 2 | 3 | 4 | 5 | 6 | 8 | 10 |
|---|---|---|---|---|---|---|---|---|
| Pengali | 0 | 1× | 3× | 6× | 10× | 15× | 28× | 45× |

Mekanismenya: saat harga menempuh jarak *X*, jumlah layer terisi tumbuh sebagai `X/d`, dan
setiap layer terisi menyumbang kerugian sebanding jaraknya sendiri. Kerugian adalah
**integral** dari posisi yang tumbuh linear — maka kuadratik.

**Basket berlapis bukan "satu trade dengan beberapa entry." Ia posisi yang ukurannya adalah
fungsi naik dari seberapa salah kita. Itu definisi konveksitas negatif.**

### 6.4 Stop drawdown ekuitas keras

Kekalahan risiko-penuh berturut untuk mencapai drawdown: `n = ln(1−DD) / ln(1−r)`

| Risiko/basket | −5% | −10% | −20% | −30% |
|---|---|---|---|---|
| 0,5% | 10 | 21 | 45 | 71 |
| 1,0% | 5 | 10 | 22 | 35 |
| 2,0% | 3 | 5 | 11 | 18 |
| 5,0% | 1 | 2 | 4 | 7 |

Probabilitas rentetan kalah pada win rate basket *w*: pada w=50%, P(5 berturut) = 3,1%,
P(8) = 0,39%, P(12) = 0,024%. Pada 1% per basket kita akan menabrak −5% kira-kira bulanan
pada win rate 50%. **Itu tidak boleh memicu shutdown, atau kita akan shutdown terus.**

**Pemutus bertingkat yang disarankan (akun $10.000):**

| Tingkat | Ambang | = basket risiko-penuh berturut |
|---|---|---|
| Stop keras per basket | 1,0% ($100) | 1 |
| **Stop rugi harian** | 3,0% ($300) | 3 |
| **Stop rugi mingguan** | 6,0% ($600) | 6 |
| **Halt bulanan / strategi** | 10,0% ($1.000) | 10 |
| Review akun / berhenti trading | 20,0% ($2.000) | 20 |

Logika sizing: harian = 2–4× per basket, mingguan = 2× harian, bulanan = 1,5–2× mingguan.
Lebih longgar dari itu dan pemutusnya cuma hiasan. **Setiap pemutus harus terealisasi +
mengambang, dievaluasi setiap tick, dan harus membatalkan pending order selain menutup
posisi** — pemutus yang membiarkan pending order hidup bukan pemutus.

**Matematika pemulihan:** −20% butuh +25% untuk pulih; −30% butuh +43%; −50% butuh +100%;
−70% butuh +233%. Drawdown 40–72% yang dilaporkan pengguna EA grid tadi, dalam praktiknya,
adalah terminal.

---

## 7. Ringkasan satu paragraf

Bangun kategori (c), jangan pernah (b). Tiga layer, maksimum empat. Jarak
`max(0,75 × ATR(H1), 20 × spread)`, dibekukan saat plan. Lot rata atau menurun — tidak
pernah menaik. Satu stop basket di struktur, disizing agar fill ladder penuh plus satu
langkah ekstra = 0,5–1% ekuitas, dengan total kedalaman ladder di bawah 1,5 × ATR harian.
Total notional dibatasi 10:1 (0,233 lot per $10.000 pada emas $4.300), menolak berdagang di
bawah ekuitas minimum yang diizinkan aritmetika lot-step. TP basket dihitung ulang dari
risiko *hidup* di setiap fill. Time stop 4 jam, kill switch tren-melawan Kaufman-ER,
pembekuan rezim rasio ATR, dan pemutus bertingkat 3% harian / 6% mingguan / 10% bulanan
yang menutup posisi **dan** membatalkan pending. Tambahan pasca-fill hanya pada eksursi
menguntungkan, disizing agar `kemajuan_dibutuhkan_dalam_N = 2 × (ukuran_tambahan /
ukuran_dasar)`. **Dan perlakukan kurva ekuitas apa pun yang lebih pendek dari ~5.000
basket — termasuk backtest kita sendiri — sebagai tidak membawa informasi tentang ekornya.**

---

## Referensi

**Tingkat (a) — bukti kuantitatif keras**

- ESMA (2018). *Product Intervention Analysis*, ESMA50-162-215.
  https://www.esma.europa.eu/sites/default/files/library/esma50-162-215_product_intervention_analysis_cfds.pdf ·
  [`../pdf/extract-esma.txt`](../pdf/extract-esma.txt)
- ESMA, siaran pers batas leverage —
  https://www.esma.europa.eu/press-news/esma-news/esma-agrees-prohibit-binary-options-and-restrict-cfds-protect-retail-investors
- Heimer, R. & Simsek, A. (2019). *Should Retail Investors' Leverage Be Limited?*
  *Journal of Financial Economics* 132(3). https://doi.org/10.1016/j.jfineco.2018.10.017 ·
  https://www.nber.org/system/files/working_papers/w24176/w24176.pdf
- Odean, T. (1998). *Are Investors Reluctant to Realize Their Losses?* *JoF* 53(5).
  https://doi.org/10.1111/0022-1082.00072 · [`../pdf/extract-odean.txt`](../pdf/extract-odean.txt)
- Barber, B., Lee, Y.-T., Liu, Y.-J. & Odean, T. *The Cross-Section of Speculator Skill.*
  https://faculty.haas.berkeley.edu/odean/papers/day%20traders/The%20Cross-Section%20of%20Speculator%20Skill.pdf
- AMF Prancis (2014). *Étude des résultats des investisseurs particuliers sur le trading
  de CFD et de Forex en France.*
  https://www.amf-france.org/fr/actualites-publications/publications/rapports-etudes-et-analyses/etude-des-resultats-des-investisseurs-particuliers-sur-le-trading-de-cfd-et-de-forex-en-france
- Moskowitz, T., Ooi, Y.H. & Pedersen, L. (2012). *Time series momentum.* *JFE*.
  https://www.sciencedirect.com/science/article/pii/S0304405X11002613
- Chen, Chen & Jang (2025). *Grid trading has zero expected value.* arXiv:2506.11921.
  https://arxiv.org/pdf/2506.11921
- FXCM, studi 43 juta trade — https://www.moneyshow.com/articles/currency-42927/
- Review pembeli MQL5 Market (EA grid, 2018–2024) —
  https://www.mql5.com/en/market/product/66317 · https://www.mql5.com/en/market/product/70283
- EA grid emas, deskripsi dan review — https://www.mql5.com/en/market/product/89528
- Data tick SNB FXCM —
  https://www.financemagnates.com/forex/brokers/fxcm-publishes-data-of-snb-mishandling-of-the-swiss-franc/
- Broker Jepang, flash crash Jan 2019 —
  https://www.financemagnates.com/forex/brokers/exclusive-japanese-brokers-face-8-6m-loss-due-to-january-flash-crash/
- Enlightened Stock Trading, sapuan partial-profit —
  https://enlightenedstocktrading.substack.com/p/is-taking-partial-profits-in-trend
- CFTC, pengungkapan forex ritel kuartalan (Q2 2026): Schwab 64,0% / IBKR 54,5% / OANDA
  66,0% / tastyfx 65,5% / FOREX.com 67,4% tidak menguntungkan
- Chen, B. & Ankenman, J. (2006). *The Mathematics of Poker*, Bab 22 (risk of ruin).

**Tingkat (b) — konsensus praktisi, tanpa data terkontrol**

- Aturan Turtle asli — https://www.theturtletrader.com/turtle-trading-rules/ · implementasi
  MQL5: https://www.mql5.com/en/articles/23448
- LuxAlgo, *Pyramiding* — https://www.luxalgo.com/library/concept/pyramiding/ — secara
  eksplisit nol backtest.
- Thread blowup forum MQL5 — https://www.mql5.com/en/forum/275681 ·
  https://www.mql5.com/en/forum/305770 · https://www.mql5.com/en/forum/178702/page26
- MQL5, *Risk-of-ruin auditor* — https://www.mql5.com/en/articles/23673
- FTMO, *Forbidden trading practices* — https://ftmo.com/en/forbidden-trading-practices/ —
  **FTMO tidak secara eksplisit melarang martingale atau grid.** Batas rugi 5% harian / 10%
  maksimum-nya adalah larangan efektif, karena strategi yang rutin berjalan di DD 40–72%
  tidak bisa lolos terlepas dari teks aturannya. (Tidak bisa diverifikasi langsung —
  ftmo.com memblokir fetch otomatis.)

**Yang harus dibuang (c)**

- TurtleTrader, *Average up, never down* — https://www.turtletrader.com/average-up/ —
  nol backtest, nol dataset, nol track record.
- fxroboteasy.com, forexrobotlab, eatested, myfxbots, bestforexeas, algotradingspace,
  forexstore, cheaperforex, fxprosystems, forexcracked, 4xpip, botfxpro — semuanya menjual
  sistem pesaing atau berpenghasilan dari afiliasi.
- **Perbedaan penting:** *blog penjual* di MQL5 adalah marketing — *review pembeli
  terverifikasi* dan *thread forum* MQL5 adalah kelas sumber yang berbeda dan jauh lebih
  baik.

## Yang memang tidak ada buktinya

1. Tidak ada studi terkontrol yang membandingkan pyramiding dengan sizing rata.
2. Tidak ada statistik survival agregat untuk populasi sinyal Myfxbook atau MQL5.
3. Tidak ada studi tentang kaki basket mana yang harus ditutup duluan. §4.3 adalah
   aritmetika, yang sudah cukup, tapi tidak ada karya empiris.
4. Tidak ada basis bukti untuk grid/martingale pada emas secara spesifik.
5. Tidak ada regulator yang membahas grid atau martingale EA secara spesifik.
6. **Ambang Kaufman ER 0,60 di Gerbang 6 adalah saran, bukan nilai tervalidasi.**
