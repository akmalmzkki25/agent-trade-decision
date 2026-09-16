# Daftar buangan

Klaim yang harus dibuang begitu terlihat, beserta alasannya. Ini bukan daftar "topik yang
tidak disukai" — setiap entri punya alasan yang bisa diperiksa.

---

## Cara mengenali sampah dengan cepat

1. **Win rate di atas ~70% tanpa ukuran sampel, rentang tanggal, instrumen, atau asumsi
   biaya.** Semua angka "65–75%", "75–85%", "80%+" di ruang ini bermuara ke ketiadaan.
2. **Angka "pips" untuk emas tanpa definisi pip.** Satu pip bisa $0,01 atau $0,10. Ambigu
   10×, dan sebagian besar ditulis saat emas $1.800–2.400.
3. **Aritmetika satuan yang tidak konsisten secara internal.** Kalau satuannya tidak cocok
   satu sama lain, angkanya dibangkitkan, bukan diukur.
4. **Kurva ekuitas mulus lebih pendek dari ~5.000 basket.** Tidak membawa informasi tentang
   ekor. → [12](12-metodologi-dan-statistik.md) §2
5. **Akurasi klasifikasi yang dijual sebagai akurasi prediksi.** Model yang mengenali
   head-and-shoulders 95% tidak menunjukkan apa pun tentang apakah H&S memprediksi harga.
6. **Penerbit yang juga penjual.** Sumber yang menjual EA, sinyal, mentorship, atau yang
   berpenghasilan dari afiliasi broker.
7. **Bentuk waktu depan di abstrak** ("metode ini *akan* dinilai... membuatnya andal").

**Perbedaan penting:** *blog penjual* MQL5 adalah marketing — *review pembeli
terverifikasi* dan *thread forum* MQL5 adalah kelas sumber yang jauh lebih baik. MQL5
memverifikasi pembelian, dan penjual bisa membalas tapi **tidak bisa menghapus** review.

---

## Klaim yang tampak seperti data tapi bukan

| Klaim | Status | Rujukan |
|---|---|---|
| "Studi FXCM atas 10.000 trade: win rate 62% dengan konfirmasi multi-timeframe vs 45% single-frame" | **Tampaknya fabrikasi.** Tidak ada publikasi FXCM yang cocok. Studi FXCM yang nyata adalah 43 juta trade tentang risk:reward, leverage, dan waktu hari — bukan MTF, dan ukuran sampelnya tidak cocok | [06](06-price-action-setup.md) §8 |
| "Varian terkait: 60–75% vs 45%", "win rate 8–12% lebih tinggi dengan MTF" | Tidak ada sumber yang bisa diatribusikan sama sekali | [06](06-price-action-setup.md) §8 |
| "Exit lebih penting daripada entry — sistem entry acak dengan trailing stop 3 ATR untung 100% dari waktu" | **Satu-satunya replikasi serius: 100 dari 100 run merugi.** Data primer tidak pernah dipublikasikan. Trailing stop 3-ATR itu sendiri adalah aturan trend-following yang menguntungkan di 1973–1988 | [08](08-take-profit-dan-exit.md) §11 |
| "18.000 trade, 22 tahun, rata-rata trade menghasilkan 15,2%" (retelling Tharp) | Hanya muncul di retelling tersier; tidak bisa ditelusuri ke sumber primer mana pun | [08](08-take-profit-dan-exit.md) §11 |
| "53% akun dengan R:R ≥1:1 untung vs 17% di bawahnya — jadi pakai 1:1 dan kamu 3× lebih mungkin untung" | **Nyaris identitas akuntansi.** R:R diukur ex post dari hasil, bukan dari order yang ditempatkan. Tidak membawa informasi tentang mengubah target | [08](08-take-profit-dan-exit.md) §5 |
| "74–89% ritel rugi, jadi lawan posisi ritel" | Statistik itu invarian terhadap leverage — ia mengukur struktur biaya, bukan arah. Bukti terbaik menemukan order imbalance ritel memprediksi return **secara positif** | [10](10-sentimen-dan-posisi.md) §3 |
| "Ikuti commercial, lawan spekulator" (COT) | **Arahnya terbalik.** Wang (2001) menemukan sentimen spekulator adalah sinyal kelanjutan, hedger yang kontrarian — dan mengatribusikannya ke risk premium, bukan keterampilan. **Dan tidak ada makalahnya yang menguji emas** | [10](10-sentimen-dan-posisi.md) §1 |
| "Korelasi emas vs yield riil −0,82" | Jendela 15 tahun, satu negara. Sampel lebih panjang: −0,31. Tren waktu polos cocok lebih baik (−0,90). WGC sekarang mengatribusikan **3%** ke suku bunga | [10](10-sentimen-dan-posisi.md) §2 |
| "Bank sentral beli 1.000t+ per tahun" | Patah di 2025 (863t). Q1 2026 direvisi −77% (244t → 57t). 57% dari 2025 tak dilaporkan | [10](10-sentimen-dan-posisi.md) §2 |

## Klaim tentang pola dan setup

| Klaim | Status | Rujukan |
|---|---|---|
| "Semakin sempit range Asia, semakin eksplosif breakout London" | **Terbalik.** Satu-satunya uji sampel besar: range sempit → 62,9% kontinuasi, lebar → 77,5% | [06](06-price-action-setup.md) §2 |
| "Hammer akurat 75–85% di support/resistance" | Tanpa sampel, tanpa tanggal, tanpa asumsi biaya | [04](04-pola-candlestick.md) |
| "Liquidity sweep 65–75% standalone, 80%+ dengan konfluensi" | Tanpa metodologi. Uji nyata: t = 0,94 pada n = 547 | [04](04-pola-candlestick.md), [06](06-price-action-setup.md) |
| "FVG terisi ~70% waktu" | Tidak terverifikasi; dibantah data futures emas MPM | [04](04-pola-candlestick.md) |
| "Backtest 1.000 trade: FVG 64,8%, sweep 58,2%, OB 43,1%" | Tanpa metodologi; dibantah karya tingkat (a) | [04](04-pola-candlestick.md) |
| "London menembus high Asia lebih dulu 53–56% hari" | Tanpa ukuran sampel, tanpa tanggal; sumbernya menjual EA emas | [06](06-price-action-setup.md) |
| "Jam 08:00 GMT rata-rata +34,9R" | N=41; **bertentangan dengan filternya sendiri** | [06](06-price-action-setup.md) |
| "Breakout Asia lari 300–600 pip, pakai stop 200 pip" | Pip tak terdefinisi; bertentangan dengan sumber lain ~10× | [06](06-price-action-setup.md) |
| "Range Asia optimal 8–25 pip" | Tanpa sumber yang bisa dilacak | [02](02-volatilitas-dan-atr.md) |
| "Range Asia 20 pip melepaskan pergerakan London 60–100 pip" | Tanpa sumber yang bisa dilacak | [02](02-volatilitas-dan-atr.md) |
| "XAUUSD fakeout ~62% intraday" | Tanpa sumber yang diungkap | [04](04-pola-candlestick.md) |
| "62% win rate lintas akun prop firm" | Tanpa sumber yang bisa dilacak | — |
| "Head-and-shoulders terbukti bekerja" (mengutip Savin et al. 2007) | **Salah kutip.** Makalahnya menemukan "sedikit atau tidak ada dukungan untuk profitabilitas strategi berdiri sendiri" | [05](05-pola-chart-klasik.md) §4 |
| "AI/CNN mengenali pola chart 95% akurat, jadi pola chart bekerja" | **Pengenalan ≠ prediksi.** Satu model mencetak ~92% pada random walk (GBM) — bukti bahwa angka itu bukan keterampilan prediktif | [05](05-pola-chart-klasik.md) §9 |
| "Jiang, Kelly & Xiu membuktikan pola chart bekerja" | **Salah baca.** JKX mempelajari bentuk arbitrer yang "berbeda signifikan" dari pola klasik | [05](05-pola-chart-klasik.md) §9 |
| "60–70% rentang harian emas terbentuk selama overlap London–NY" | Dibantah data akademis: emas tidak punya kurva-U dramatis; puncaknya landai di 12:00–14:00 GMT | [02](02-volatilitas-dan-atr.md) §5 |
| "Open London adalah jam paling bervolatilitas untuk emas" | Tidak didukung data 2000–2015; puncaknya ~14:00 GMT | [03](03-sesi-dan-waktu.md) |
| "Lelang LBMA sore adalah katalis intraday utama" | Data lompatan 2010–2018: jam 10:00 ET **tidak** menunjukkan klaster lompatan | [03](03-sesi-dan-waktu.md) |
| "Sesi Asia emas cenderung trending / cenderung mean-reverting" (mengutip Iwatsubo et al.) | **Salah baca.** Makalah itu memakai `\|1 − VR\|` — nilai absolut — jadi tidak memberi tahu arah | [03](03-sesi-dan-waktu.md) |

## Klaim tentang volatilitas dan satuan

| Klaim | Status | Rujukan |
|---|---|---|
| "ATR M15 $12 sementara ATR harian $45" | **Mustahil secara aritmetika.** 96 bar × $12 tidak bisa menghasilkan rentang harian $45 | [02](02-volatilitas-dan-atr.md) |
| "$10 per pip untuk 0,01 lot XAUUSD" (Pro-Scalper) | **Salah faktor 100.** 0,01 lot = 1 oz, jadi pergerakan $0,10 = $0,10, bukan $10 | [02](02-volatilitas-dan-atr.md) |
| Tabel rentang sesi tradingonmt5.com (Asia 60–150 "cent", London 150–300, NY 200–500) | Meleset ~10× untuk emas di $4.300. **Rasio antar sesinya informatif; nilai absolutnya tidak** | [03](03-sesi-dan-waktu.md) |
| "Sesi Asia range 20–40 pip" dan "London 80–150 pip" | Keduanya tidak mungkin benar di $4.300 dengan konvensi mana pun | [01](01-biaya-dan-kelayakan-timeframe.md) |
| Angka dolar emas mana pun bersumber 2020–2024 | Basi ~2× pada harga, lebih dari itu pada volatilitas | [02](02-volatilitas-dan-atr.md) |

## Klaim tentang layering dan EA

| Klaim | Status | Rujukan |
|---|---|---|
| "EA grid dengan 70+ bulan profit beruntun" | Kasus terdokumentasi: 5 tahun bersih, lalu drawdown 40–72% di 2023–2024 karena **tren biasa** | [09](09-layering-dan-basket.md) §2 |
| "Win rate grid 95% membuktikan edge" | Pada rasio kalah/menang 20:1, 95% tepat impas sebelum biaya | [09](09-layering-dan-basket.md) §1 |
| "Average up, never down" (TurtleTrader) | Nol backtest, nol dataset, nol track record | [09](09-layering-dan-basket.md) |
| "Sebagian besar sistem berhenti bekerja dalam 12–18 bulan" | Bermuara ke situs tanpa dataset yang dikutip | [09](09-layering-dan-basket.md) |
| "Margin level yang tinggi berarti akun aman" | Basket 20 layer pada 1:500 bisa menghancurkan 67% akun saat margin level masih 1.948% | [09](09-layering-dan-basket.md) §2 |
| EA scalper emas MQL5 Market dengan kurva ekuitas mulus | Petunjuk ada di disclosure mereka sendiri: backtest dengan `MaxSpread = 100` poin, rekomendasi 30 untuk live | [01](01-biaya-dan-kelayakan-timeframe.md) |
| "FTMO melarang martingale dan grid" | FTMO tidak melarangnya secara eksplisit; batas rugi 5%/10% adalah larangan efektifnya | [09](09-layering-dan-basket.md) |

## Klaim tentang order flow

| Klaim | Status | Rujukan |
|---|---|---|
| "Order flow imbalance menjelaskan 65% pergerakan harga" | **Kontemporer, bukan prediktif.** Tidak ada spesifikasi tertinggal di makalah itu | [11](11-order-flow-dan-data.md) §2 |
| "Imbalance besar berarti pergerakan besar" | **Kebalikan dari temuan literatur.** Imbalance ekstrem menyertai harga yang *dipatok* | [11](11-order-flow-dan-data.md) §2 |
| "Order block adalah jejak institusional" | Spot XAUUSD tidak punya order book; institusi sengaja memecah order agar tidak meninggalkan jejak | [11](11-order-flow-dan-data.md) §1 |
| "Absorption adalah properti peristiwa" | Satu-satunya uji serius: nol tanpa syarat | [11](11-order-flow-dan-data.md) §3 |
| "Divergensi CVD menghasilkan +163% pada TSLA" | Dua megacap pilihan, tanpa biaya, tanpa benchmark, tidak di-review | [11](11-order-flow-dan-data.md) §3 |
| "Konfirmasi dengan volume 1,5× rata-rata 20 bar" (di XAUUSD MT5) | Volume di MT5 spot adalah tick count broker, bukan volume. Tidak bisa diterapkan secara bermakna | [11](11-order-flow-dan-data.md) §5 |

## Sumber yang tercatat sebagai (c)

**Penjual EA / sinyal / mentorship:**
pro-scalper.com · breakoutalerts.io · goldenalgostrategy.com · quantum-algo.com ·
fortraders.com · theinnercircletraders.com · innercircletrader.net · priceactionninja.com

**Broker dan afiliasi (definisi mungkin bisa dipakai, angka tidak):**
fxopen.com · tradezella.com · fxglory.com · forexgdp.com · edgeful.com · investortipster.com

**Situs ulasan/penjual EA:**
fxroboteasy.com · forexrobotlab · eatested · myfxbots · bestforexeas · algotradingspace ·
forexstore · cheaperforex · fxprosystems · forexcracked · 4xpip · botfxpro ·
newyorkcityservers (vendor VPS dengan "ulasan")

**Makalah yang bermasalah:**
- DOI 10.1109/C2I666499.2025.11366955 — abstrak dalam bentuk waktu depan, tanpa dataset atau
  metrik
- DOI 10.5753/eniac.2025.12471 — metrik pada training set; setidaknya dua referensi
  fabrikasi (tanda daftar referensi buatan LLM)
- DOI 10.36774/sisiti.v14i2.1744 — studi pola XAUUSD, n≈7 untuk satu hasil
- DOI 10.1109/BTS-I2C67944.2025.11399400 — F1 > 0,99 dari kebocoran CV
- DOI 10.14357/19922264220304 — Crossref tidak mencatat penulis
- DOI 10.1109/ACCESS.2024.3411991 — "keberhasilan prediksi 100%", komisi dikecualikan
- DOI 10.5121/csit.2023.131001 dan 10.5121/mlaij.2023.10301 — studi yang sama
  dipublikasikan dua kali
- arXiv:2609.13825 — divergensi CVD, dua megacap pilihan
- Sönnert (2015), tesis ORB emas — backtest strategi intraday bergantung-jalur pada data
  OHLC harian saja (di 78% hari kedua ambang tersentuh dan hasilnya diasumsikan);
  "rata-rata 0,58%" adalah standar deviasi yang salah label (sebenarnya 0,041%/hari); nol
  biaya transaksi. [`../pdf/sonnert-2015-intraday-momentum-gold-futures-opening-range-breakouts.pdf`](../pdf/sonnert-2015-intraday-momentum-gold-futures-opening-range-breakouts.pdf)

---

## Tidak dibuang — tapi sering salah dikutip

Sumber-sumber ini **valid**, tapi kutipannya di konten ritel sering salah:

| Sumber | Yang sering diklaim | Yang sebenarnya dikatakan |
|---|---|---|
| Lo, Mamaysky & Wang (2000) | "Analisis teknikal terbukti menguntungkan" | Pola memberi "informasi inkremental" — "**tidak serta-merta berarti** analisis teknikal bisa menghasilkan profit berlebih." Tanpa biaya, tanpa strategi trading |
| Han, Zhou & Zhu | "Stop loss terbukti menggandakan Sharpe" | Stop 15% **bulanan tingkat portofolio** pada momentum ekuitas cross-sectional. Bukan stop per-trade intraday |
| Kaminski & Lo (2014) | "Stop loss menambah nilai" | Hanya di bawah momentum, dan **negatif di horizon pendek**. Sering ditempeli angka "50–100bp" dari working paper 2008 yang datanya berbeda |
| Zarattini & Aziz (2023) | "Stop 5% ATR menghasilkan 9.350%" | Sapuan sensitivitas in-sample, **tanpa slippage**, "drawdown" muncul nol kali di teks |
| Cont, Kukanov & Stoikov (2014) | "OFI memprediksi harga" | OFI **menjelaskan** perubahan harga **kontemporer** |
| Wang (2001) | "COT memprediksi harga emas" | **Tidak menguji emas sama sekali.** Enam pasar pertanian |
| Iwatsubo, Watkins & Xu (2018) | "Sesi Asia emas trending" | Mengukur **besaran** penyimpangan dari random walk, **bukan arahnya** |
| Tsinaslanidis & Guijarro (2021) | "92,5% eksperimen pola chart menguntungkan" | Setelah membatasi ke subset ex-post. Semesta penuh: **49% bersih** setelah biaya institusional |
| Michniuk (2017) | "Pola flag menguntungkan di semua 96 konfigurasi" | Tanpa biaya transaksi, tanpa uji signifikansi, 436 konfigurasi dicari, dan **hasilnya sendiri mematahkan fraktalitas** |
