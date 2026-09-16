# Audit EA V1–V5 terhadap bukti

Setiap temuan di bawah diverifikasi terhadap kode di repo per 16 September 2026, bukan dari
ingatan. Nomor baris bisa bergeser; nama fungsi dan konstanta yang dikutip adalah
jangkarnya.

**Tingkat keparahan:**
- **KRITIS** — geometri atau asumsi yang membuat ekspektasi negatif secara struktural
- **TINGGI** — bug yang secara diam-diam mengubah risiko atau hasil
- **SEDANG** — keputusan desain yang tidak didukung bukti dan sebaiknya diukur dulu
- **RENDAH** — penamaan atau dokumentasi yang menyesatkan

---

## Masalah lintas versi

### [KRITIS] Kalender berita adalah stub yang selalu bilang "aman"

`adapter/app/news/economic_calendar.py` — `get_blackout()` selalu mengembalikan
`BlackoutDecision(blackout=False, severity=0.0, event="")`. Docstring-nya sendiri: *"This
module returns 'no blackout' for all calls during the bulk-layering Phase A."*

**Mengapa kritis:** rilis makro AS terjadwal menyebabkan **18–25%** lompatan intraday emas
([03](03-sesi-dan-waktu.md)); spread melonjak ke 30–60+ poin dan slippage 15+ poin, membuat
friksi $0,22 kita jadi $0,60–1,00 — membalik ekspektasi setup mana pun jadi negatif. Bar
M15 12:30 UTC punya rata-rata $21,89 dan maksimum $104,50; 20% melampaui $25
([02](02-volatilitas-dan-atr.md)).

**Dan stub yang selalu bilang "aman" lebih buruk daripada tidak ada pemeriksaan**, karena
terbaca sebagai kontrol saat code review. Setiap versi (V2–V5) memanggilnya dan
mempercayainya.

**Perbaikan:** hardcode tabel UTC statis untuk NFP, CPI, FOMC, PPI, retail sales, jobless
claims — dengan penyesuaian DST — sampai integrasi kalender nyata tersedia.

### [KRITIS] Tidak ada jendela sesi di V5

V5 berdagang jam berapa pun, termasuk Asia di mana friksi adalah **28–49% dari rentang bar
M1** ([01](01-biaya-dan-kelayakan-timeframe.md)). Ledger live kita mencatat
`avg_spread_points` 26–35 — di atas gerbang `InpMaxSpreadPoints = 30` di sebagian basket.

**Kemungkinan perbaikan tunggal terbesar yang tersedia.**

### [TINGGI] Jam sesi yang di-hardcode akan salah waktu 7 bulan per tahun

08:30 ET adalah 13:30 GMT hanya di musim dingin; di musim panas **12:30 UTC**. Delapan dari
dua belas bar M15 terbesar di sampel musim panas jatuh persis di 12:30 UTC. Dan
`TimeGMT()` di Strategy Tester sama dengan waktu server — **offset DST jadi 0 di backtest**.
([03](03-sesi-dan-waktu.md) §2)

### [TINGGI] ATR(14) M15 dipakai sebagai estimator volatilitas

V2 dan V3 memakai `ATR_M15` untuk jarak layer dan stop. Bias terukur: **0,57×** (terlalu
rapat) di open NY, **1,63×** (terlalu lebar) di jeda malam. **Stop kita paling rapat justru
di jam paling berbahaya.** ([02](02-volatilitas-dan-atr.md) §1)

### [TINGGI] Konstanta dolar yang menyusut diam-diam

Setiap ambang yang dinyatakan dalam dolar tetap atau pip tetap telah menyusut separuh dalam
istilah relatif sejak emas $2.000. Di $2.000, $3 adalah 0,15% harga; di $4.300 tinggal
0,07%. **Nyatakan sebagai kelipatan ATR atau persen harga.**

### [SEDANG] Market order di mana-mana

V5 memakai market order eksklusif, 9 per basket. Catatan perdagangan lengkap Taiwan
1992–2006: *"praktis seluruh kerugian trading individu dapat ditelusuri ke order agresif
mereka."* ([08](08-take-profit-dan-exit.md) §5)

---

## V1 — `ea/QlipV1_XAUUSD.mq5` (493 baris, magic 250518)

Breakout M15 sederhana, `InpBreakoutLookback = 20`, satu posisi.

**Relatif paling sehat** secara struktur: satu posisi, M15 (friksi 2,6–3,7% rentang bar di
London/NY), tanpa layering. Masalahnya adalah yang lintas versi di atas.

| Temuan | Tingkat |
|---|---|
| Breakout lookback 20 bar di M15 = 5 jam — mencampur sesi Asia dengan London. Bukti ORB mendukung jendela yang dijangkarkan ke **open sesi**, bukan lookback bergulir | SEDANG |
| Tidak ada filter lebar range. Bukti: range lebar (>0,6×ATR) → kontinuasi 77,5%; sempit → 62,9% dengan double-break 53,4% | SEDANG |
| Tidak ada konfirmasi close — bukti bilang close 5 menit bernilai 4–6 poin persentase di atas sentuhan wick | SEDANG |

---

## V2 — `ea/QlipV2_XAUUSD.mq5` (809 baris, magic 250519–250524)

Bulk layering berbasis skenario. Planner: `adapter/app/layering/planner.py`.

### [KRITIS] Ladder lebih dangkal daripada stop satu layer — ia bukan ladder

Spesifikasi di docstring planner:
```
step_i = max(0.5*ATR_M15, base_step) * (1 + 0.4*i)
SL distance per layer = max(1.5 * ATR_M15, distance_to_invalidation)
```

| ATR(M15) | N | Kedalaman ladder | Jarak SL | kedalaman/SL |
|---|---|---|---|---|
| $8 | 3 | $7,20 | $12,00 | 0,60 |
| $8 | 5 | $10,40 | $12,00 | 0,87 |
| $12 | 5 | $15,60 | $18,00 | 0,87 |

Seluruh ladder terisi dalam **1,3 × ATR(M15)**, yang **kurang dari jarak stop satu layer**,
di setiap tingkat ATR. **Dalam pergerakan merugikan apa pun, semua layer terisi sebelum stop
pertama terpicu.** Kita mengambil satu posisi dalam N irisan dalam ~1,3 ATR dan membayar N
spread untuk itu.

**Perbaikan:** lebarkan jarak ke `max(0,75 × ATR(H1), 20 × spread)` atau turun ke entry
tunggal. ([09](09-layering-dan-basket.md) §3.2)

### [KRITIS] TP basket tetap tidak terjangkau pada fill parsial

`BASKET_RR = 1.5`; `basket_tp_pct_equity = total_risk_pct * BASKET_RR * 100.0` — dihitung dari
risiko **terencana penuh**, bukan risiko **terisi**.

| Layer terisi (dari 5) | Risiko hidup | Target TP | Kelipatan R dibutuhkan |
|---|---|---|---|
| 1 | 0,20% | 1,5% | **7,5R** |
| 2 | 0,40% | 1,5% | 3,8R |
| 3 | 0,60% | 1,5% | 2,5R |
| 5 | 1,00% | 1,5% | 1,5R |

**Distribusi terealisasinya jadi "menang hanya ketika seluruh ladder terisi" — yaitu menang
hanya ketika tesis awal SALAH dan harga menembus seluruh zona kita. Itu profil payoff grid
yang tercapai secara tidak sengaja.**

**Perbaikan:** hitung ulang di setiap fill:
```
risiko_hidup$ = Σ layer TERISI (lot_i × 100 × |harga_i − SL_basket|)
TP_basket$    = RR × risiko_hidup$
```

### [KRITIS] `RANGE_REVERT` adalah averaging-down ke level yang gagal

Buy-limit ditempatkan di `support − step × (1 + 0.4i)` — **eksposur makin besar saat support
patah.** Dikombinasikan dengan invalidasi di `swing ± 0.5 × ATR_H1`, invalidasi itu mungkin
berada **di dalam** ladder tergantung rasio ATR.

**Perbaikan:** tegaskan saat plan bahwa `invalidation_price` berada tegas melewati layer
terdalam plus satu langkah jarak; tolak plan kalau tidak.

### [TINGGI] Pembulatan lot diam-diam mengubah risiko −46% sampai +50%

Dengan lot min 0,01 / step 0,01 di emas:

| ATR(M15) | N | Lot/layer eksak | Dibulatkan | Risiko aktual vs anggaran |
|---|---|---|---|---|
| $8 | 5 | 0,0167 | 0,01 | −40% |
| $12 | 3 | 0,0185 | 0,01 | −46% |
| $20 | 5 | 0,0067 | 0,01 | **+50%** |

Ada juga baris `lots = max(volume_min, ...)` yang **membulatkan naik** ke lot minimum —
itulah jalur +50%.

**Perbaikan:** hitung risiko basket aktual pasca-pembulatan dan **tolak plan** kalau
melampaui anggaran lebih dari toleransi kecil. Jangan pernah membulatkan naik diam-diam.

### [TINGGI] Trigger M1 + cooldown 5 menit tanpa batas harian

| Umur basket | Maks basket/hari | Ekuitas berisiko/hari pada 1% masing-masing |
|---|---|---|
| 5 menit | 138 | 138% |
| 30 menit | 39 | 39% |
| 60 menit | 21 | 21% |

**Aturan 1%-per-basket tanpa batas harian adalah aturan 20–140%-per-hari.** Pemutus
harian/mingguan di [09](09-layering-dan-basket.md) §6.4 tidak opsional.

---

## V3 — `ea/QlipV3_XAUUSD.mq5` (1.112 baris, magic 250530–250544)

"Agresif": semua tipe order, M1, dua slot paralel, exit bertahap. Model:
`V3ExitRules` di `adapter/app/models.py`.

**Catatan:** file ini 1.112 baris — melanggar aturan maksimum 800 baris.

### [KRITIS] Semua masalah V2 diwarisi

`planner_v3.py` memakai `BASKET_RR` yang sama dan rumus TP basket tetap yang sama.

### [TINGGI] Stop ke BEP di +50 pip

`stage2_trigger_pips: 50.0`, `stage2_sl_offset_pips: 0.0` (BEP).

Dari +k R dengan target T, memindahkan stop ke BE mengubah P(capai target) dari
`(1+k)/(1+T)` ke `k/T`. Pada contoh +0,5R dengan target 3R: **dari 37,5% ke 16,7%.**
Dengan biaya, BE tegas lebih buruk. Dengan drift positif — premis trade momentum — tegas
lebih buruk. ([08](08-take-profit-dan-exit.md) §8)

**Dan stage 1 memindahkan SL ke entry − 20 pip setelah +30 pip** — stop yang tetap berada di
dalam pita noise.

**Perbaikan:** geser stop ke level struktural (higher low baru), bukan ke angka pip tetap.

### [TINGGI] TP per-layer RR 1:1 di dalam logika basket

`rr_ratio: float = 1.0` — broker-side TP di `entry + (sl_distance × rr_ratio)` untuk setiap
layer.

Dua masalah:
1. TP per-layer di dalam basket berarti layer yang berharga terbaik keluar duluan pada
   pergerakan menguntungkan, meninggalkan layer terburuk. Itu **kebalikan dari urutan partial
   yang benar** (§4.3 [09](09-layering-dan-basket.md)).
2. Dengan stop per-layer 1,5×ATR dan TP per-layer 1:1, setiap layer adalah trade 1:1 yang
   independen — **dan di bawah random walk tanpa drift itu ekspektasi tepat nol sebelum
   biaya** ([08](08-take-profit-dan-exit.md) §2).

Catatan: fitur ini ditambahkan untuk menghindari "order limit error" (batas order broker),
yang merupakan alasan operasional sah — tapi solusinya menambah biaya struktural.

### [SEDANG] Stage 1 menutup "oldest first"

`// Collect open position tickets in the slot, sorted by open time ASC (oldest first).`

Apakah ini benar atau salah **bergantung arah fill**:
- Ladder buy-limit yang terisi **ke bawah**: tertua = entry tertinggi = kaki **paling rugi** →
  menutupnya secara matematis memperbaiki basket sisa. Benar.
- Buy-stop yang terisi **ke atas** (pyramid): tertua = entry terendah = kaki **paling untung**
  → menutupnya menaikkan entry rata-rata sisa. **Salah.**

V3 memakai keduanya ("semua tipe order"). **Perbaikan:** urutkan berdasarkan harga entry
relatif terhadap arah, bukan waktu — atau pakai pemangkasan proporsional.

### [SEDANG] Mode "fast bulk layering" di M1

M1 berada di rezim friksi 10–15% (London/NY) sampai 28–49% (Asia) dari rentang bar.
**Ladder M1 berjarak rapat mati karena biaya jauh sebelum mati karena tren.**
([09](09-layering-dan-basket.md) §3.2)

---

## V4 — `ea/QlipV4_XAUUSD.mq5` (614 baris, magic 250550–250552)

Liquidity zone H1→M15→M5→M1, partial TP, SL ke BE.

### [KRITIS] Tiga pemotongan distribusi berturut-turut

Dari `ManagePosition` di EA (dengan `pip = point × 10 = $0,10`):

| Aturan | Nilai | Dalam dolar | vs ATR M15 (~$8) |
|---|---|---|---|
| Partial 50% | `profit_pips >= 30.0` | **+$3,00/oz = 300 points** | ~0,35–0,45 ATR |
| SL ke BE | segera setelah partial | — | — |
| Runner cap | `profit_pips >= 100.0` | **+$10,00/oz = 1000 points** | ~1,1–1,5 ATR |

Tiga masalah:
1. **Partial $3 menyala di ~0,35–0,45 ATR** — di dalam pita noise, dan hanya ~11×
   biaya bolak-balik.
2. **SL ke BE segera setelah partial** memotong peluang bersyarat mencapai target kira-kira
   separuh.
3. **Runner cap di $10** memotong persis ekor kanan yang membayar semuanya.

**Kombinasi — partial dini, BE segera, runner dibatasi — adalah pemotongan tiga kali atas
satu-satunya bagian distribusi yang berekspektasi positif.**

Dan kalau entry-nya benar-benar mean-reverting (zona likuiditas), bukti Carr & López de Prado
bilang target *sempit* memang benar — tapi saat itu **stop harus lebar** dan trailing/BE harus
**dimatikan**. V4 mencampur geometri dari dua rezim.

**Perbaikan:** nyatakan ketiganya sebagai kelipatan ATR; pindahkan stop hanya pada peristiwa
struktural; ganti runner cap keras dengan trail berskala volatilitas plus **time barrier**.

### [TINGGI] SL maksimum 50 pip = $5 = ~0,6 ATR M15

Spesifikasi pengguna: zona 30 pip, SL maks 50 pip. Pada `pip = $0,10`, 50 pip = **$5,00 =
500 points** — di bawah lantai desain 600 points dan ~0,6 ATR M15 saat ini.

Biaya sebagai % R pada stop $5: ~5,2%. **Di bawah lantai; setup yang butuh stop lebih rapat
dari ini harus dilewati, bukan dipaksa.**

### [SEDANG] Rantai H1→M15→M5→M1 tanpa aturan tiebreak

Tidak ada sumber yang menawarkan tiebreak saat H1 dan M15 bertentangan. Pastikan setiap fitur
HTF hanya dari **bar tertutup**. ([06](06-price-action-setup.md) §8)

---

## V5 — `ea/QlipV5_XAUUSD.mq5` (1.010 baris, magic 250560–250569)

Scalping basket bertick. Signal: `adapter/app/scenarios/scalp_micro.py`.

**Catatan:** file ini 1.010 baris — melanggar aturan maksimum 800 baris.

### [KRITIS] Geometri 1:6 terbalik adalah taruhan yang tidak pernah diukur

`InpBasketTpUsd = 5.0` vs `InpBasketSlUsd = 30.0` → **geometri 1:6 terbalik.**
Win rate impas tanpa drift = 30/35 = **85,7%**. Ekspektasi tepat nol di situ sebelum biaya,
negatif sesudahnya.

**Ini tidak otomatis salah.** Menurut Carr & López de Prado, **TP kecil / SL besar adalah
geometri optimal untuk proses yang kuat mean-reverting (OU half-life pendek)** — Sharpe
sampai 3,2 di simulasi mereka — dan itu yang dilakukan market maker. Tapi taruhannya jadi
eksplisit:

> **V5 bertaruh bahwa XAUUSD kuat mean-reverting pada horizon ~2 menit.**

| Kalau prosesnya… | Maka geometri V5… |
|---|---|
| Kuat mean-reverting | **Tepat.** Pekerjaannya adalah menurunkan biaya per burst |
| Random walk | Tidak relevan. **Spread yang membunuh** |
| Trending | **Geometri terburuk yang mungkin** |

**Dan satu-satunya estimasi Hurst emas yang ada menempatkan emas lebih dekat ke 0,5**
(random walk), dengan mean reversion OU pada MGC 5 menit **gagal di setiap ambang** dan
half-life ~8 jam — lebih lama dari satu sesi. ([12](12-metodologi-dan-statistik.md) §9)

**Perbaikan:** ukur autokorelasi dan variance ratio bertanda pada horizon 2 menit **sebelum**
tuning parameter apa pun.

### [KRITIS] Hasil live sejauh ini konsisten dengan masalah di atas

13 basket per 16 Sep 2026: win rate **38,46%**, profit factor **0,45** (target >1,3),
ekspektasi **−$3,12/basket**, rata-rata menang **$4,73** vs rata-rata kalah **−$8,02**.

Sebagian besar basket berakhir di **"Max lifetime"** — target $5 belum kena dalam 120 detik,
kerugian $30 juga belum, jadi waktu habis duluan. **Time barrier-lah yang melakukan
pekerjaan exit sesungguhnya** — yang konsisten dengan literatur ORB, tapi berarti TP/SL-nya
hampir tidak pernah mengikat.

13 sampel jelas tidak signifikan (butuh ~1.000–2.500). Ini arah, bukan vonis.

### [KRITIS] Spread memakan ~54% target

Pada gerbang `InpMaxSpreadPoints = 30`, basket 9 market order lot sama membayar
**9 × 30 = 270 points** spread entry. TP $5 atas 9 posisi 0,01 lot (9 oz) butuh ~55,6 points
pergerakan menguntungkan rata-rata, atau ~500 points agregat. **Spread saja memakan ~54%
target**, sebelum slippage, invarian terhadap ukuran lot. ([11](11-order-flow-dan-data.md) §6)

### [TINGGI] Lapisan OBI tidak bisa bekerja di spot XAUUSD

`DomImbalance()` dan `TrackDomVariability()` — guard DOM sintetisnya **benar dan hati-hati**,
tapi **input OBI nyata tidak ada** di spot XAUUSD OTC. Satu-satunya uji XAUUSD book ritel:
R²ₒₛ **0,045%**, tidak pernah lolos ke live. ([11](11-order-flow-dan-data.md))

**Perbaikan:**
- Tutup item roadmap "Real OBI/DOM integration" sebagai tidak bisa dicapai.
- Tambahkan cek volume per-level (volume konstan/simetris saat harga bergerak = sintetis).
- Catat `SYMBOL_TICKS_BOOKDEPTH` sekali saat init.

### [RENDAH] Docstring yang menyesatkan

`scalp_micro.py` modul docstring: *"Order Book Imbalance (OBI) is approximated by tick volume
z-score."* **Tidak.** OBI bertanda; z-score tick-count tak bertanda dan tanpa informasi arah.

### [RENDAH] `VSA_NO_DEMAND` salah nama

`_classify_vsa`: `volume_z <= -0.5 AND range_z >= 1.0` = volume rendah, range **lebar**.
"No demand" VSA kanonik adalah range **sempit**. Ganti nama (`VSA_THIN_MOVE`) atau
implementasikan bentuk kanonik.

### [SEDANG] `VSA_CLIMAX` sebagai veto keras

Satu-satunya aturan VSA yang dijadikan gerbang pemblokir, bertumpu pada klasifikasi tak
tervalidasi yang dihitung dari tick count privat broker. Temuan peer-review bahwa imbalance
ekstrem menyertai *pematokan* harga alih-alih pergerakan besar menyiratkan intuisi exhaustion
tidak umum. **Ukur efek aktualnya pada hasil basket sebelum mempertahankannya sebagai veto.**

### [SEDANG] Tick momentum 3-tick sebagai penentu arah

`TickMomentumSigned()`: 3 tick naik berturut = buy, 3 turun = sell. Di M1 emas, tick
didominasi bid-ask bounce. Dan **"NO_TICK_MOMENTUM" adalah penolakan dominan** (222 dari 250
keputusan). Arahnya adalah pengikut momentum mikro — yang bertentangan dengan premis
mean-reversion yang disiratkan geometri 1:6.

**Ada ketidakkonsistenan internal:** entry bertaruh pada **kelanjutan** momentum mikro,
sementara exit bertaruh pada **mean reversion**. Keduanya tidak bisa benar sekaligus pada
horizon yang sama.

### Yang sudah diperbaiki di sesi ini

Untuk kelengkapan — sudah di-commit dan di-deploy:
- **Basket satu arah** (`APP-SIDE-409`) — burst berlawanan ditolak; terbukti bekerja live.
- **State basket bertahan dari restart** — side, id, dan analitik dipersisten ke
  GlobalVariable.
- **Kegagalan WebRequest dilaporkan** di `PostBasketResult`.
- **Gerbang ATR memakai rasio** (bukan percentile rank), dan `ArraySetAsSeries` dipasang.

---

## Ringkasan prioritas

| # | Temuan | Versi | Tingkat |
|---|---|---|---|
| 1 | Kalender berita stub selalu "aman" | V2–V5 | KRITIS |
| 2 | Tidak ada jendela sesi | V5 | KRITIS |
| 3 | Geometri 1:6 tidak pernah diukur prosesnya | V5 | KRITIS |
| 4 | Spread memakan ~54% target | V5 | KRITIS |
| 5 | TP basket tetap tak terjangkau pada fill parsial | V2, V3 | KRITIS |
| 6 | Ladder lebih dangkal dari stop satu layer | V2, V3 | KRITIS |
| 7 | `RANGE_REVERT` averaging-down | V2, V3 | KRITIS |
| 8 | Tiga pemotongan distribusi | V4 | KRITIS |
| 9 | Jam sesi salah waktu 7 bulan/tahun (DST) | Semua | TINGGI |
| 10 | ATR(14) M15 bias terbalik terhadap jam | V2–V4 | TINGGI |
| 11 | Pembulatan lot diam-diam −46% s/d +50% | V2, V3 | TINGGI |
| 12 | Tanpa batas rugi harian/mingguan | V2, V3 | TINGGI |
| 13 | SL ke BEP pada trigger pip tetap | V3, V4 | TINGGI |
| 14 | OBI tidak bisa bekerja di spot | V5 | TINGGI |
| 15 | File >800 baris | V3, V5 | SEDANG |
