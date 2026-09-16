# Ringkasan eksekutif

Sepuluh temuan yang menentukan bagaimana V6 harus dibangun. Masing-masing diperluas
di file bernomor yang disebut.

---

## 1. Biaya adalah suku pertama, bukan pembulatan

Friksi dibagi rentang rata-rata satu bar menentukan apakah sebuah timeframe layak
diperdagangkan sama sekali.

| Timeframe | Sesi | Friksi sbg % rentang bar |
|---|---|---|
| M1 | Asia | 28–49% |
| M1 | London/NY | 10–15% |
| M5 | Asia | 12–20% |
| M5 | London/NY | 4,4–6,3% |
| M15 | London/NY | 2,6–3,7% |

Duvinage (2013) menguji 83 aturan candlestick pada bar 5 menit di mana rasio ini ≈22%:
setelah biaya, 5 bertahan dan **nol** mengalahkan buy-and-hold. Mesfin (2026) menguji
14 keluarga sinyal pada MNQ 5 menit di mana rasionya ≈6%: edge kotor 0,07–1,50 poin
melawan friksi 2,0 poin — **nol** lolos. → [01](01-biaya-dan-kelayakan-timeframe.md)

**Untuk V6:** perdagangkan hanya jam di mana `friksi / ATR(M5) < 0,08`.

## 2. ATR(14) di M15 adalah estimator yang salah untuk emas

Diukur langsung pada 4.500 bar M15 dan 2.513 bar harian emas COMEX. Rentang M15
sebenarnya membentang **2,91×** sepanjang hari; pembacaan ATR(14) hanya **1,62×**,
karena lookback 14 bar di M15 adalah 3,5 jam dan mengaburkan batas sesi. Biasnya
sistematis dan **terbalik**: 0,57× (terlalu rapat) di open NY, 1,63× (terlalu lebar)
di jeda malam.

Mengganti dengan rata-rata true range **slot 15 menit yang sama** selama 10 sesi
terakhir menurunkan sebaran kalibrasi antar jam dari **2,85× ke 1,08×**.
→ [02](02-volatilitas-dan-atr.md)

## 3. Exit tidak menciptakan edge

Untuk `X_t = μt + σW_t`, untuk aturan keluar apa pun: **`E[P&L] = μ · E[τ] − biaya`**.
Ekspektasi dari aturan exit mana pun sama dengan drift dikali lama waktu di pasar,
dikurangi biaya. Exit hanya menentukan berapa lama kita terpapar drift yang sudah ada.

Konsekuensinya: "pakai 1:3" bukan nasihat, itu perubahan satuan. Untuk random walk
tanpa drift, peluang kena TP duluan adalah `1/(1+R)` — persis sama dengan win rate
impas untuk payoff R. Ekspektasi nol di setiap R. → [08](08-take-profit-dan-exit.md)

## 4. Geometri TP/SL harus mengikuti proses, bukan dipilih

Monte Carlo 100.000 jalur atas mesh 21×21 pasangan (TP, SL):

- **Mean-reverting** → stop lebar, target sempit. Sharpe sampai 3,2.
- **Momentum** → kebalikannya.
- **Random walk** → semua geometri identik, dan setiap tuning adalah overfitting.

V5 saat ini bertaruh pada yang pertama tanpa pernah mengukur apakah premisnya benar.
→ [08](08-take-profit-dan-exit.md), [14](14-audit-ea-v1-v5.md)

## 5. Kerugian layering tumbuh kuadratik

Untuk *k* lot sama dengan jarak `d`: `loss = L × contract × d × k(k−1)/2`. Naik dari
3 ke 6 layer melipatgandakan eksposur terburuk **5×**. Ladder 10 layer rugi **45×** apa
yang dirisikokan layer pertama. Lot rata sudah terasa eksponensial; multiplier 2,0×
membuatnya benar-benar eksponensial.

Dan grid trading terbukti bernilai harapan **nol** sebelum biaya, negatif sesudahnya.
Cara grid EA mati bukan black swan — kasus paling terdokumentasi mati karena **tren
satu arah yang biasa saja** selama empat minggu. → [09](09-layering-dan-basket.md)

## 6. Order flow tidak tersedia di spot XAUUSD. Titik.

Dokumentasi MetaQuotes sendiri: instrumen OTC diperdagangkan dengan "Bid dan Ask
stream quotes, **tanpa data transaksi yang benar-benar dieksekusi**" dan "**tidak ada
harga Last**". Order flow adalah studi tentang transaksi bertanda melawan likuiditas
yang menunggu. Di spot XAUUSD kita tidak punya keduanya.

Lebih buruk: kalau broker mengembalikan DOM, MetaQuotes menyatakan itu bisa berupa
"level harga yang **dihitung dari Bid dan Ask** memakai price change step" — tangga
sintetis tanpa informasi apa pun. → [11](11-order-flow-dan-data.md)

Satu studi menguji persis konfigurasi kita — order flow imbalance dari order book
broker ritel, lima instrumen termasuk XAUUSD. **XAUUSD paling buruk dari kelima**,
R² out-of-sample **0,045%**, dan tidak pernah lolos ke uji live.

## 7. Tidak ada sentimen yang memprediksi arah emas secara intraday

Semua yang punya bukti beroperasi pada horizon **hari sampai bulan**: COT 4–8 minggu
(dan tidak ada satu pun makalah COT yang menguji emas), makro bulanan, safe-haven
~15 hari perdagangan. Dua hal yang real-time — posisi ritel dan sentimen berita —
masing-masing tidak terdukung dan bersifat **kontemporer**, bukan prediktif.

Yang *bisa* dilakukan sentimen intraday berbeda jenisnya: memprediksi **kapan** pasar
akan bergerak (probabilitas lompatan), bukan **ke arah mana**. Itu input risiko, bukan
sinyal. → [10](10-sentimen-dan-posisi.md)

## 8. Posisi ritel bukan indikator kontrarian

Ini kebalikan dari cerita marketing. Bukti terbaik menemukan order imbalance ritel
**secara positif** memprediksi return, dan strategi long-short berdasarkan posisi
ritel ekstrem **rugi 14,8% per tahun** justru di saham yang perdagangan ritelnya
paling padat.

Dan "74–89% ritel rugi" tidak mengandung informasi arah: ketika CFTC membatasi
leverage pada 2010, kerugian trader leverage tinggi turun **40%** sementara tingkat
akun merugi tidak berubah. Statistik itu mengukur struktur biaya dan geometri risk
of ruin, bukan keterampilan arah. → [10](10-sentimen-dan-posisi.md)

## 9. Pola chart klasik: bukti terbaiknya negatif

Uji sampel besar head-and-shoulders di ekuitas AS — 100 perusahaan CRSP acak, 31,5
tahun, bootstrap, sepuluh analisis sensitivitas: **profit rata-rata −0,24% per posisi
versus −0,03% di bawah null acak, tidak signifikan secara statistik**. Penulisnya
menyimpulkan trader H&S memenuhi syarat sebagai *noise trader* justru karena
strateginya tidak menguntungkan.

Dan perhatikan lintasan Park & Irwin: "56 dari 95 studi modern positif" (2007) →
**"2 dari 17 pasar futures signifikan setelah koreksi data-snooping" (2010)**. Penulis
yang sama. Koreksinya adalah seluruh ceritanya. → [05](05-pola-chart-klasik.md)

## 10. Ukuran sampel kita jauh dari cukup

`N = 4R / (δ²(R+1)²)` trade untuk mencapai t = 2. Pada edge 3 poin persentase itu
~833–1.111 trade; pada edge 2 poin persentase, **~1.875–2.500**.

Dan lebih tajam lagi: dengan nol ledakan dalam *n* basket, batas atas 95% probabilitas
ledakan per basket ≈ `3/n`. **Backtest 500 basket dengan kurva ekuitas sempurna masih
konsisten dengan kehancuran yang nyaris pasti dalam 1.000 basket berikutnya.**
→ [12](12-metodologi-dan-statistik.md)

---

## Yang memang tidak ada buktinya

Dinyatakan terang-terangan, karena ketiadaan bukti lebih berguna daripada bukti palsu:

- Tidak ada studi yang menetapkan kelipatan ATR yang benar untuk emas M15
- Tidak ada studi MAE/stop-hunt sampel besar untuk emas atau FX intraday
- Tidak ada uji head-to-head yang ketat antara fixed-R, struktur, ATR, dan trailing
- Tidak ada uji kuantitatif scaling out pada emas intraday
- Tidak ada studi peer-reviewed tentang SMC/ICT, BOS, CHoCH, atau order block
- Tidak ada uji terkontrol yang membandingkan pyramiding dengan sizing rata
- Tidak ada studi pola chart klasik pada emas, di timeframe mana pun
- Tidak ada uji publik yang ketat untuk stop break-even, di pasar mana pun

Seluruh korpus praktisi merinci **entry** dan stop awal dengan detail, lalu praktis
tidak mengatakan apa pun yang bisa diuji tentang **exit**. Logika exit V6 harus
dibenarkan dengan data kita sendiri. → [13](13-daftar-buangan.md)

---

## Empat pengukuran sebelum tuning apa pun

Lebih bernilai daripada seluruh literatur di folder ini:

1. **ATR(14) M1 dan M5 per jam UTC**, minimal 6 bulan. Tidak ada tabel kredibel untuk
   ini yang pernah dipublikasikan di mana pun.
2. **Distribusi spread per jam** dari tick live — bukan dari Strategy Tester, yang
   memodelkan spread dengan buruk.
3. **Distribusi slippage riil** pada market order, per jam.
4. **Rasio `friksi / ATR(M5)` per jam.** Perdagangkan hanya jam di bawah 0,08.

Dan satu eksperimen yang nilainya melebihi semua kutipan di sini: jalankan
**variance ratio bertanda** pada data XAUUSD kita sendiri, per jendela sesi. Itu yang
menentukan apakah geometri V5 (stop lebar, target sempit) benar atau terbalik.
