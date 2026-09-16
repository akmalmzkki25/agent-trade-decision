# Basis pengetahuan XAUUSD — bahan untuk V6

Riset ini dikumpulkan pada September 2026 sebagai bahan desain V6. Isinya adalah
tinjauan bukti: apa yang benar-benar punya dukungan data, apa yang hanya kebiasaan
komunitas, dan apa yang harus dibuang.

Versi bacanya (HTML, ringkas): https://claude.ai/artifact/EmX3Gcpeq3LynLLYdzkygS

## Cara membaca

Baca **00** dan **01** lebih dulu. Keduanya menentukan bagaimana semua file lain
harus dibaca — kalau lantai biayanya tidak dipahami, sisa dokumen ini mudah
disalahartikan sebagai daftar setup yang tinggal dipasang.

| File | Isi |
|---|---|
| [00-ringkasan-eksekutif.md](00-ringkasan-eksekutif.md) | Sepuluh temuan yang menentukan desain V6 |
| [01-biaya-dan-kelayakan-timeframe.md](01-biaya-dan-kelayakan-timeframe.md) | Model biaya, satuan, dan mengapa M1 tidak layak |
| [02-volatilitas-dan-atr.md](02-volatilitas-dan-atr.md) | ATR terukur, bias waktu-hari, rezim 2026 |
| [03-sesi-dan-waktu.md](03-sesi-dan-waktu.md) | Jam perdagangan, jebakan DST, jendela berita |
| [04-pola-candlestick.md](04-pola-candlestick.md) | Pola candle: mana yang bertahan, mana noise |
| [05-pola-chart-klasik.md](05-pola-chart-klasik.md) | Head & shoulders, segitiga, flag, dan sebagainya |
| [06-price-action-setup.md](06-price-action-setup.md) | Empat setup yang bisa dikodekan |
| [07-stop-loss.md](07-stop-loss.md) | Penempatan dan ukuran stop |
| [08-take-profit-dan-exit.md](08-take-profit-dan-exit.md) | Target, break-even, trailing, partial |
| [09-layering-dan-basket.md](09-layering-dan-basket.md) | Matematika risiko layering |
| [10-sentimen-dan-posisi.md](10-sentimen-dan-posisi.md) | COT, makro, sentimen ritel |
| [11-order-flow-dan-data.md](11-order-flow-dan-data.md) | Order flow: apa yang benar-benar tersedia |
| [12-metodologi-dan-statistik.md](12-metodologi-dan-statistik.md) | Ukuran sampel, overfitting, higiene backtest |
| [13-daftar-buangan.md](13-daftar-buangan.md) | Klaim yang harus dibuang, dan alasannya |
| [14-audit-ea-v1-v5.md](14-audit-ea-v1-v5.md) | Audit EA kita terhadap bukti di atas |
| [15-implikasi-untuk-v6.md](15-implikasi-untuk-v6.md) | Apa yang seharusnya berbeda di V6 |

Salinan makalah sumber ada di [`../pdf/`](../pdf/), dengan indeks di
[`../pdf/README.md`](../pdf/README.md).

## Konvensi

**Satuan.** Semua angka dalam **MT5 points** (`$0,01/oz`) atau **dolar per ounce**.
Kata "pip" tidak dipakai. Di emas, satu pip bisa berarti $0,01 atau $0,10 tergantung
broker, dan mayoritas angka pip yang beredar ditulis saat emas $1.800–2.400 lalu
disalin maju tanpa diubah. Jangan pernah mengimpor angka pip dari artikel.

**Tingkat bukti.** Setiap klaim diberi nilai:

- **(a)** Kuantitatif, metodologi terbuka, ukuran sampel dan periode dinyatakan
- **(b)** Konsensus praktisi, konsisten secara internal, tapi tidak pernah diuji
- **(c)** Marketing atau sampah — dicantumkan hanya agar bisa dikenali

**Konteks harga.** Emas ~$4.300/oz pada September 2026, setelah puncak sepanjang masa
mendekati $5.600 pada Januari 2026 dan titik terendah $3.959 pada Juni 2026.
Volatilitas terealisasi sempat melampaui 50% melawan rata-rata 20 tahun 17%.
Setiap sumber yang mengutip emas di $1.800–2.400 sudah basi untuk angka absolutnya —
rasio dan persentasenya mungkin masih berlaku.

## Keterbatasan riset ini

Dinyatakan di depan supaya tidak menyesatkan:

- Budget WebSearch sesi ini habis di tengah jalan (200/200). Sebagian riset dikerjakan
  lewat API Crossref, OpenAlex, Semantic Scholar, arXiv, dan fetch langsung. Penemuan
  lewat pencarian web umum karenanya tidak menyeluruh.
- Hampir semua domain broker (ig.com, myfxbook.com, oanda.com, pepperstone.com,
  icmarkets.com, cmegroup.com) diblokir ISP dari mesin ini — semuanya menunjuk ke
  `202.169.44.80`. Spesifikasi kontrak dan spread broker **tidak terverifikasi**; harus
  dicek sendiri lewat MT5 → klik kanan simbol → Specification.
- `web.archive.org` diblokir, jadi sumber yang sudah hilang tidak bisa diambil.
- Beberapa penerbit (ScienceDirect, Wiley, SSRN, Taylor & Francis) menolak akses
  otomatis. Di tempat yang hanya abstrak yang terbaca, itu ditandai.
