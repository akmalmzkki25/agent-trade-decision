# V6 Manajemen Dinamis M1: Desain

Tanggal 2026-09-17, cabang `layering`. Keempat bagian desain disetujui pengguna di chat
pada hari yang sama. Rencana implementasi tahap A ada di
`docs/superpowers/plans/2026-09-17-v6-phase-a-plan-management.md`.

## Status dan penyimpangan saat implementasi (2026-09-18)

Tahap A sudah dikerjakan (adapter dan EA 6.2.0). Tahap B belum: snapshot menit (§3.1),
`MinuteInbox` (§4.2), paket `m1` (§2.5, §2.6) dan `v6_minute_cycles` (§4.6). Desain di
bawah tetap berlaku, kecuali hal berikut.

- **Tenggat paket M15** (§4.2, §4.5): tidak ada `V6_M15_DEADLINE_S`;
  `V6_OPERATOR_DEADLINE_S` tetap dipakai, dengan default baru 180 detik.
- **Nama kunci jendela rencana** (§4.5): `V6_TIME_LIMIT_MIN_MINUTES`,
  `V6_TIME_LIMIT_MAX_MINUTES`, `V6_PENDING_EXPIRY_MIN_MINUTES` dan
  `V6_PENDING_EXPIRY_MAX_MINUTES`. Keempatnya hanya boleh mempersempit 60–240 dan 15–60
  menit.
- **Laporan EA** (§3.6): laporan aksi (APPLIED, REJECTED, FAILED) dan langkah SL+
  (`PLAN_STEP`) dikirim ke rute baru `POST /v6/action` (`v6.action.1`), bukan ke
  `/v6/execution`, supaya `ExecutionReport` tetap satu bentuk. Outbox dan kirim ulangnya
  sama.
- **Sesi 24 jam** (§4.7): sesi harian tetap ditutup di blok rollover, sehingga ringkasan
  tetap per hari. Begitu blok selesai, runtime membuka dan meng-arm sesi hari berikutnya
  (`V6_SESSION_AUTO_RENEW=true`; `session_renewal_due` di status). Selama pembaruan
  ditunggu, `wait` keluar dengan kode 3, bukan 4.
- **Ledger** (§4.6): `v6_outcomes` tidak diubah. MAE dan MFE sudah ada di snapshot dan di
  record EA. Setiap langkah SL+ dicatat di tabel baru `v6_plan_steps` (kunci tiket), yang
  digabung dengan hasil basket untuk mengukur dampak SL+.
- **Jam sesi EA:** `InTradeSession` sudah memakai jam trading simbol dari broker, jadi
  tidak diubah.
- **ENTER v3** (§2.1) mewajibkan view Price Action TAKE `limits.agent_entry_id` dengan
  conviction ≥ 0,60 (`DECISION_VIEW`). Tanpa aturan ini, keputusan diterima lalu menjadi
  HOLD di protokol.
- **MODIFY posisi** (§2.3) memeriksa tangga yang tersisa: SL, TP yang langkahnya belum
  tereksekusi, dan TP3 harus maju searah trade. Langkah SL+ yang sudah tereksekusi tidak
  lagi membatasi SL, jadi stop boleh digeser lagi setelah TP2. Posisi tanpa TP butuh
  `tp3` (`LADDER_MISSING`).
- **EA** (§3.3) menganggap `TRADE_RETCODE_NO_CHANGES` sebagai selesai. SL dan TP posisi
  hanya diperiksa (`SL_WIDER`, `TOO_CLOSE`) bila nilainya berubah. Penolakan broker karena
  pasar tutup dilaporkan `REJECTED`/`MARKET_CLOSED`.
- **Snapshot posisi** membawa `plan_step` dan `time_limit_epoch` dari EA. Paket memakai
  langkah terjauh dari EA atau dari laporan.
- **Nama modul** (§4.3):
  - model v3 ada di `schemas/operator_plan.py`, bukan `operator_v3.py`;
  - aturan rencana ada di `deliberation/plan_rules.py` dan `agent_entry.py`;
  - bagian bersama keputusan ada di `deliberation/decision_parts.py`, aturan v3 di
    `decision_v3.py`;
  - `pending_review.py` dihapus.
- **Belum selesai** (§9):
  - drill 1–8 (runbook §6) menunggu izin pengguna;
  - jam kuotasi Monex (§10) belum diisi. `V6_BROKER_QUOTE_GAP_UTC` masih memakai nilai
    MetaQuotes sampai pengguna mengisinya dari hasil pengukuran.

## Konteks

Sampai commit `fbee77a`, operator V6 (Claude Code, Codex, Antigravity) hanya bisa
memutuskan sekali per bar M15, dan hanya saat V6 flat atau saat order pending sedang
menunggu (paket review KEEP/CANCEL). Begitu order terisi, posisi dibiarkan sampai SL,
TP, atau batas waktu 2 jam. Pada 17 September operator membuka empat entry di Monex-Demo
dan tiga di MetaQuotes-Demo. Tiga dari empat entry Monex sudah tertutup, dua di antaranya
lewat SL: stop $7 tersapu bar M15 yang rentangnya $12–21 setelah rilis Philly Fed.

Pengguna meminta cara kerja yang lebih mirip trader manusia. Operator menganalisis M1
terus-menerus dengan prioritas tetap di M15, menetapkan TP1, TP2, TP3, SL dan SL+,
boleh memotong order atau posisi yang sudah tidak sehat, boleh entry MARKET, LIMIT
atau STOP, memilih lot 0,01–0,03, dan bekerja 24 jam.

## Keputusan pengguna

| Topik | Keputusan |
|---|---|
| Skema TP | Satu posisi. TP3 dipasang di broker; TP1 dan TP2 memicu pergeseran SL (SL+) yang dijalankan EA. Tidak ada tutup sebagian. |
| Jam entry | 24 jam, disaring biaya. Jendela London–NY dihapus; blok rollover, jeda kuotasi broker, jeda LBMA, bar data AS dan akhir pekan tetap memblokir entry. |
| Batas waktu posisi | Dipilih operator 60–240 menit saat entry, boleh diperpanjang sampai total 240 menit. |
| Ritme | Pendekatan B: satu paket per bar M1, dengan paket M15 sebagai prioritas. |
| Batas entry harian | Naik dari 4 menjadi 8. |
| Lot | 0,01–0,03, dipilih operator, boleh diturunkan sizer. |

## Dasar bukti dan penyimpangan yang disengaja

`knowledge/01-biaya-dan-kelayakan-timeframe.md` (rekomendasi 6) menyebut M1 hanya layak
untuk penghalusan eksekusi, dan friksi $0,22 sama dengan 28–49% rentang bar M1 di sesi
Asia. Karena itu paket M1 dipakai untuk timing dan manajemen; setiap entry tetap harus
lolos gate spread dan friksi/ATR(M5) ≤ 0,15, yang secara alami menolak sebagian besar jam
Asia.

`knowledge/08-take-profit-dan-exit.md` §7 menunjukkan scale-out menurunkan ekspektasi, dan
§8 menolak pemindahan ke break-even pada pemicu R tetap; yang dianjurkan adalah menggeser
stop ke level struktur. Pengguna tetap memilih SL+ bertahap. Operator memilih level
struktur (higher low baru untuk buy, lower high baru untuk sell) sebagai `sl_after_tp1`
dan `sl_after_tp2` bila ada, dan memakai entry + biaya hanya bila struktur belum
terbentuk. Setiap trade menyimpan rencana, langkah yang tereksekusi, MAE dan MFE supaya
dampak SL+ bisa diukur, bukan diasumsikan.

§6 dari file yang sama menyebut barrier waktu sebagai exit yang paling banyak bekerja; batas
60–240 menit tetap ditegakkan EA.

## Tujuan

- Operator menerima satu paket per bar M1 selama 24 jam (kecuali blok rollover) dan satu
  paket M15 lengkap setiap 15 menit.
- Rencana entry memuat SL, TP1–TP3, rencana SL+, batas waktu dan lot.
- EA menjalankan langkah SL+ secara lokal dan menerima perintah manajemen bertanda tangan:
  tutup posisi, ubah posisi, ubah pending.
- Order BUY_STOP dan SELL_STOP tersedia.
- Keterlambatan operator tidak pernah membuat posisi tanpa perlindungan.

## Bukan tujuan

- Tutup sebagian. Di Monex (ekuitas sekitar $920, risiko 1%) lot praktis selalu 0,01;
  ditunda sampai akun demo punya ekuitas sekitar $3.000 atau lebih.
- Trailing stop otomatis berbasis ATR.
- Lebih dari satu posisi V6, hedging, atau layering.
- Akun REAL dan CONTEST (tetap ditolak di semua lapisan).
- Backend keputusan selain `operator`.

## Arsitektur

```
MT5 QlipV6 EA                                  Adapter (127.0.0.1:8765)
 OnTick/OnTimer
 ├ Plan.mqh: langkah SL+ (lokal, tiap tick)
 ├ close M1  → POST /v6/minute ───────────►    MinuteInbox (terbaru menang)
 ├ close M15 → POST /v6/snapshot ─────────►    SnapshotInbox → siklus M15 (prioritas)
 ├ poll 2 s  ◄── intent v2 | perintah ─────    minute worker → paket M1
 └ laporan   → POST /v6/execution ────────►    OperatorQueue (M15 180 s, M1 50 s)
                                               ▲ wait / submit
                                     Claude Code | Codex | Antigravity (DEMO saja)
```

Siklus M15 tetap jalan lewat engine yang ada. Worker menit baru memakai konteks yang lebih
ringan (bar M1 dari BarStore, quote, eksposur, bias M15 terakhir) dan gate yang sama untuk
halt, kebijakan akun, kesegaran data, breaker, berita, spread, friksi dan rollover.

## 1. Ritme dan jenis paket

Setiap bar M1 tutup, EA mengirim snapshot menit; setiap bar M15 tutup, EA mengirim
snapshot lengkap seperti sekarang. Adapter membuka paling banyak satu paket operator pada
satu waktu.

| Jenis | Kapan | Isi | Tenggat | Bila tak terjawab |
|---|---|---|---|---|
| `m15` | setiap close M15 | blok `bars` (M1 60 bar, M5, M15, H1, D1), `levels`, kalender, biaya, gate, `limits`, keadaan (`flat`, `pending`, `position`) | 180 s | flat: HOLD; pending/posisi: KEEP |
| `m1` | close M1 lainnya | 60 bar M1, `m1_state` (rentang/ATR M1, arah dan kekuatan 5 dan 15 bar terakhir, laju tick), bias M15 terakhir, `limits`, keadaan, jarak harga ke entry/SL/TP1–TP3, hasil aksi terakhir | 50 s | sama |

Aturan antrean:
- Paket `m15` menarik paket `m1` yang masih terbuka, dan selama `m15` terbuka tidak ada
  paket `m1` baru.
- Paket `m1` yang belum dijawab diganti paket `m1` berikutnya; yang lama ditutup sebagai
  "tidak ada perubahan".
- Paket `m1` dijeda selama blok rollover dan saat sesi tidak armed.

Keadaan menentukan jawaban yang sah:
- `flat`: HOLD atau ENTER.
- `pending`: `manage` dengan KEEP, CANCEL atau MODIFY.
- `position`: `manage` dengan KEEP, CLOSE atau MODIFY.

## 2. Keputusan v3

### 2.1 Bentuk

```json
{
  "schema_version": "v6.operator.decision.3",
  "packet_kind": "m1",
  "cycle_id": "m-5f0c2a91d3b4e6aa",
  "packet_hash": "…",
  "agent": null,
  "action": "ENTER",
  "entry_plan": {
    "side": "buy", "order_type": "LIMIT", "entry": 4360.5, "sl": 4353.5,
    "tp1": 4366.0, "tp2": 4371.0, "tp3": 4378.0,
    "sl_after_tp1": 4361.0, "sl_after_tp2": 4366.0,
    "time_limit_min": 150, "pending_expiry_min": 30, "lots": 0.01,
    "thesis": "Floor 4359-4360 diuji tiga kali, M1 membentuk higher low."
  },
  "manage": null,
  "m15_bias": null,
  "views": null,
  "note": ""
}
```

`action` bernilai `HOLD`, `ENTER` atau `MANAGE`. Pada paket `m15`, `views` (keempat desk)
dan `m15_bias` wajib diisi, sedangkan pada paket `m1` keduanya boleh `null`. Objek
`chief` dari v2 dihapus: `action` dan `entry_plan` menggantikannya. Keputusan v2 masih
diterima untuk paket `m15` berkeadaan `flat` selama tahap A, lalu dihapus di tahap B.

### 2.2 `entry_plan`

Aturan ditulis untuk buy; sell adalah cerminannya. `R` adalah jarak dari harga entry
efektif ke `sl` (ask untuk MARKET buy). `d_min` adalah
`max(stops_level, freeze_level) × point + spread saat ini + $0,10`.

| Field | Aturan |
|---|---|
| `side` | `buy` atau `sell` |
| `order_type` | `MARKET` (`entry` null), `LIMIT` (`entry ≤ limits.buy_limit_max`), `STOP` (`entry ≥ limits.buy_stop_min`); jarak entry ke harga ≤ `limits.max_entry_distance` |
| `sl` | `limits.stop_floor ≤ R ≤ limits.max_stop_distance`; aturan angka bulat $50 dari `risk/exits.py` tetap berlaku |
| `tp1` | `tp1 − entry ≥ 0,5 R` |
| `tp2` | `tp2 > tp1` |
| `tp3` | `tp3 > tp2`, `1 R ≤ tp3 − entry ≤ 5 R`; kode boleh memangkasnya sebelum angka bulat, dan rencana ditolak bila hasil pangkasan ≤ `tp2` |
| `sl_after_tp1` | null, atau `sl < sl_after_tp1 ≤ tp1 − d_min` |
| `sl_after_tp2` | null, atau `max(sl, sl_after_tp1) ≤ sl_after_tp2 ≤ tp2 − d_min` |
| `time_limit_min` | bilangan bulat 60–240 |
| `pending_expiry_min` | 15–60 untuk LIMIT/STOP, null untuk MARKET; order tidak boleh menunggu sampai ke jeda kuotasi broker |
| `lots` | `volume_min ≤ lots ≤ max_lots` pada grid `lots_step`; sizer boleh menurunkan, tidak pernah menaikkan; risiko dihitung dari `sl` |
| `thesis` | ≤ 300 karakter, tidak pernah dieksekusi |

`limits` mendapat dua field baru: `buy_stop_min` (ask + `d_min`) dan `sell_stop_max`
(bid − `d_min`).

### 2.3 `manage`

| Field | Isi |
|---|---|
| `target` | `position` atau `pending`, harus sama dengan keadaan paket |
| `ticket` | harus sama dengan tiket di paket |
| `op` | posisi: `KEEP`, `CLOSE`, `MODIFY`; pending: `KEEP`, `CANCEL`, `MODIFY` |
| `sl`, `tp1`, `tp2`, `tp3`, `sl_after_tp1`, `sl_after_tp2`, `time_limit_min` | hanya untuk `MODIFY`; null berarti tidak berubah |
| `entry`, `pending_expiry_min` | hanya untuk `MODIFY` pending |
| `reason` | ≤ 200 karakter |

`MODIFY` posisi (buy):
- `sl` hanya boleh naik (`sl_baru ≥ sl_sekarang`) dan harus `≤ bid − d_min`.
- `tp3` harus `≥ bid + d_min` dan `(tp3 − open) ≤ 5 R_awal`.
- `tp1`, `tp2` dan langkah SL+ hanya boleh diubah untuk langkah yang belum tereksekusi,
  dengan aturan urutan §2.2.
- `time_limit_min` adalah total sejak posisi dibuka: `≥ menit berjalan + 5` dan `≤ 240`.
- Minimal satu field harus terisi.

`MODIFY` pending memvalidasi ulang seluruh rencana seperti entry baru dengan `side` dan
`order_type` yang sama. MT5 tidak bisa mengubah volume order pending, jadi modifikasi
ditolak (`MANAGE_SIZE`) bila sizer akan menurunkan lot di bawah volume order.

`CLOSE`, `CANCEL` dan `KEEP` tidak memerlukan field lain dan selalu sah untuk keadaannya.

### 2.4 `m15_bias`

Wajib pada paket `m15`:
- `direction`: `up`, `down`, `range` atau `unclear`;
- `levels`: sampai 6 harga;
- `invalidation`: harga atau null;
- `scenario`: ≤ 240 karakter.

Bias berlaku sampai paket `m15` berikutnya dijawab, dan ikut di setiap paket `m1` sebagai
"bias M15 terakhir".

### 2.5 View desk pada paket M1

Paket `m1` tidak memuat view desk. Entry dari paket `m1` memakai view (stance dan pengali)
dari keputusan `m15` terakhir yang diterima; bila paket `m15` itu tidak terjawab, view
baseline siklus tersebut yang berlaku. Veto di view M15 memblokir entry `m1` sampai paket
`m15` berikutnya. Gate kode (berita, spread, friksi, rollover, breaker) tetap dihitung
setiap menit.

### 2.6 Jalur cepat

`OP submit --agent <AGENT> --quick` mengirim "tidak ada perubahan" tanpa menulis file:
HOLD untuk `flat`, `manage.op = KEEP` untuk `pending` dan `position`. Pada paket `m15`,
jalur cepat membawa bias M15 terakhir dengan tanda `carried: true` (atau `unclear` bila
belum ada) dan view baseline. Jalur cepat tidak bisa gagal validasi selama paket masih
terbuka.

### 2.7 Kode penolakan

Kode yang sudah ada tetap dipakai: `DECISION_ENTRY_PLAN` (detail menyebut aturan yang
dilanggar) dan `DECISION_LOTS`. Kode baru:
- `DECISION_MANAGE`: `manage` salah untuk keadaannya, atau melanggar §2.3.
- `DECISION_BIAS`: `m15_bias` hilang atau salah.
- `DECISION_KIND`: `packet_kind` tidak cocok dengan paket.

`DECISION_REVIEW` pensiun bersama `pending_review.py`.

## 3. Kontrak EA

### 3.1 Snapshot menit `v6.minute.1` ke `POST /v6/minute`

Dikirim sekali per close M1, tanpa antrean ulang, dengan timeout 800 ms dan ukuran di
bawah 4 KB. Isinya:
- identitas: `snapshot_id` (`Q6M-<login>-<epoch bar>`), `login`, `trade_mode`, `server`,
  `sent_at_epoch`;
- `bar`: `t`, `o`, `h`, `l`, `c`, `tick_volume`, `spread_max`;
- `quote`: bid, ask, `spread_points`;
- `ticks`: jumlah tick dan jeda maksimum dalam ms;
- `account`: `equity`, `balance`, `free_margin`;
- `local_halt`;
- `exposure`: untuk setiap posisi V6, `ticket`, `intent_id`, `side`, `volume`, `open_price`,
  `open_epoch`, `sl`, `tp`, `profit`, `plan_step`, `time_limit_epoch`; untuk setiap pending
  V6, `ticket`, `intent_id`, `order_type`, `price`, `sl`, `tp`, `volume`,
  `expiration_epoch`.

Bar M1 dari snapshot ini ditambahkan ke BarStore, sehingga backfill M1 tidak perlu
diulang.

### 3.2 Intent `v6.intent.2`

Menambah beberapa field ke `v6.intent.1`:
- `order_type` sekarang juga bisa `BUY_STOP` atau `SELL_STOP`;
- `tp` (= TP3), `tp1`, `tp2`, `sl_after_tp1`, `sl_after_tp2` (0 berarti tidak ada);
- `time_barrier_s` (3600–14400);
- `expiration_epoch` untuk pending.

Semua field baru masuk ke string kanonik HMAC dengan urutan tetap, dan vektor self-test
EA diperbarui. EA memeriksa ulang urutan TP, arah order STOP/LIMIT terhadap harga, jarak
`d_min`, dan batas lot/risiko lokal (`InpMaxLots` 0,03, `InpMaxRiskUsd` 50).

### 3.3 Perintah di balasan poll

`command` bertambah `CLOSE_POSITION`, `MODIFY_POSITION` dan `MODIFY_PENDING`, di samping
`NONE`, `FLATTEN` dan `CANCEL_PENDING`. Perintah baru membawa objek `action`:
- identitas: `action_id` (12 karakter base32), `ticket`, `issued_at_epoch`;
- level: `sl`, `tp`, `tp1`, `tp2`, `sl_after_tp1`, `sl_after_tp2`;
- waktu: `time_barrier_s`, dan untuk pending `price` serta `expiration_epoch`.

Satu balasan membawa satu perintah atau satu intent, tidak pernah keduanya, dan seluruh
balasan ditandatangani. EA menolak perintah bila salah satu kondisi ini terjadi:
- tiket bukan milik magic V6 atau tidak dilacak;
- `action_id` sudah pernah diterapkan;
- umur perintah lebih dari 30 detik;
- SL melebar;
- jarak ke harga kurang dari `d_min`;
- total batas waktu lebih dari 4 jam;
- akun bukan DEMO.

### 3.4 Langkah SL+ lokal (`Plan.mqh`)

Rencana setiap posisi (TP1, TP2, dua target SL, langkah terakhir, batas waktu) disimpan
di GlobalVariable per tiket dengan prefix `QlipV6_`, mengikuti pola `Track.mqh`.

Setiap tick, dan setiap detik lewat OnTimer, EA memeriksa pemicu. Untuk buy pemicunya
`bid ≥ tp1` (langkah 1) atau `bid ≥ tp2` (langkah 2); untuk sell, `ask ≤ tp1`/`tp2`. Saat
pemicu aktif, EA memanggil `PositionModify` dengan target SL langkah itu, asalkan target
lebih aman dari SL sekarang dan masih `≤ bid − d_min`. Kalau belum valid, EA mencoba lagi
pada tick berikutnya. Langkah 2 menggantikan langkah 1 yang belum sempat tereksekusi.
Status langkah disimpan sebelum laporan dikirim.

### 3.5 Batas waktu dan rollover

EA menutup posisi pada `open_epoch + time_barrier_s`, dan `MODIFY_POSITION` bisa
memperpanjangnya. Flatten harian (`InpFlattenServerTime`, 22:55 waktu server) tetap
berlaku. `InTradeSession` di `Schedule.mqh` hanya memblokir jendela flatten dan jeda
kuotasi, bukan jam London–NY.

### 3.6 Laporan ke `/v6/execution`

Jenis laporan baru:
- `PLAN_STEP`: langkah, SL lama, SL baru, harga, waktu;
- `ACTION_APPLIED` dan `ACTION_REJECTED`: `action_id` dan kode alasan;
- `ACTION_FAILED`: retcode broker.

Laporan diantrekan lewat outbox yang sudah ada dan dikirim ulang sampai diterima.

## 4. Adapter

### 4.1 Rute

- `POST /v6/minute`: HMAC, dedupe per `snapshot_id`, balasan 202, validasi seperti
  snapshot M15 (timestamp naik, OHLC konsisten, nilai finite, panjang dibatasi).
- `POST /v6/intent/poll`: balasan bisa membawa perintah manajemen dari `CommandBoard`
  yang diperluas dengan antrean aksi (satu aksi aktif per tiket).
- `/v6/operator/wait` dan `/decision`: menangani `packet_kind`.

### 4.2 Runtime

- `MinuteInbox` (maxsize 1) dan worker menit di `runtime/service.py`. ID siklus menit
  memakai prefix `m-`.
- Saat snapshot M15 masuk, runtime menarik paket menit yang terbuka lewat
  `OperatorQueue.withdraw`, lalu menjalankan siklus M15.
- `OperatorQueue` menyimpan tenggat per paket: `V6_M15_DEADLINE_S` dan
  `V6_M1_DEADLINE_S`.
- Watchdog menandai aksi yang tidak dilaporkan EA dalam 30 detik sebagai `EXPIRED`.

### 4.3 Paket dan keputusan

- `deliberation/operator_packet.py` membuat paket `m15` untuk ketiga keadaan.
- `deliberation/minute_packet.py` (baru) membuat paket `m1` dan `m1_state`, hanya dari
  bar yang sudah tutup.
- `deliberation/management.py` (baru) memvalidasi `manage` dan membangun perintah;
  modul ini menggantikan `pending_review.py`.
- `schemas/operator_parts.py` dan `schemas/operator.py` mendapat model v3; bila file
  melewati 400 baris, model v3 dipindah ke `schemas/operator_v3.py`.
- `deliberation/agent_entry.py` memvalidasi rencana §2.2, termasuk order STOP, urutan TP
  dan langkah SL+.

### 4.4 Risk dan gate

- `market/sessions.py` mendapat `entries_allowed_all_day`, yang memblokir akhir pekan,
  rollover (17:00–19:00 New York), jeda LBMA dan bar data AS. `_session_gate` memakainya
  bila `V6_ENTRY_HOURS=all_day`. Cek jeda kuotasi broker (`settings.quote_gap`) tetap
  berlaku.
- `risk/intent_builder.py` membangun intent v2; `risk/exits.py` memvalidasi dan
  memangkas TP3.
- `risk/sizing.py` tidak berubah.

### 4.5 Konfigurasi baru

| Kunci | Default | Batas |
|---|---|---|
| `V6_ENTRY_HOURS` | `all_day` | `all_day` atau `london_ny` |
| `V6_MAX_TRADES_PER_DAY` | 8 | 1–20 |
| `V6_MINUTE_PACKETS` | `false` di tahap A, `true` di tahap B | |
| `V6_M1_DEADLINE_S` | 50 | 20–55 |
| `V6_M15_DEADLINE_S` | 180 | 60–600, menggantikan `V6_OPERATOR_DEADLINE_S` |
| `V6_MINUTE_STALE_S` | 10 | 3–30 |
| `V6_TIME_LIMIT_MIN` / `V6_TIME_LIMIT_MAX` | 60 / 240 menit | plafon kode `MAX_TIME_BARRIER_S` 14400 |
| `V6_PENDING_EXPIRY_MIN` / `V6_PENDING_EXPIRY_MAX` | 15 / 60 menit | |

Validator konfigurasi menjaga agar tenggat M15 + TTL intent tetap lebih pendek dari masa
berlaku pending terpendek.

### 4.6 Ledger dan dashboard

- `v6_actions`: `action_id`, `cycle_id`, `packet_kind`, `ticket`, `op`, payload JSON,
  status (`PUBLISHED`, `APPLIED`, `REJECTED`, `FAILED`, `EXPIRED`), detail, waktu.
- `v6_minute_cycles`: satu baris ringkas per paket menit (keadaan, aksi, latensi
  operator), dipangkas setelah 3 hari.
- `v6_intents` mendapat kolom rencana (TP1, TP2, target SL+, batas waktu) dan langkah yang
  tereksekusi; `v6_outcomes` mendapat MAE, MFE dan langkah terakhir.
- Dashboard `/v6` menampilkan rencana posisi, langkah SL+, aksi terakhir, dan persentase
  paket menit yang terjawab dalam tenggat.

### 4.7 Sesi 24 jam

Sesi tidak ditutup otomatis saat rollover. Hari trading tetap berganti pada 17:00 New
York untuk penghitungan harian dan ringkasan; blok rollover menahan entry dan paket menit.
`session start` dari chat baru melanjutkan sesi yang sama.

## 5. Harness operator dan dokumen

- `v6_operator.py`:
  - `wait` mencetak jenis paket dan ringkasan `m1` satu baris;
  - `template` menulis templat v3;
  - `submit --quick` ditambahkan.
- `v6-trading` SKILL (dua salinan identik) dan `AGENTS.md`, tetap di bawah 12.000
  karakter:
  - loop per paket;
  - jawab `m1` dalam 45 detik dan pakai `--quick` bila tidak ada perubahan;
  - laporkan satu baris hanya untuk aksi (entry, modifikasi, cut, pengisian, penutupan)
    dan untuk setiap paket `m15`;
  - mulai chat baru saat konteks berat, tanpa menghentikan sesi.
- `docs/v6-operator.md`: rubrik baru untuk bias M15, timing M1, tangga TP dan SL+ (level
  struktur lebih dulu), kriteria cut, order STOP, batas waktu, dan warisan view.
- `docs/v6-wire-contract.md`: snapshot menit, intent v2, perintah manajemen, laporan baru.
- `docs/v6-runbook.md`: input EA, pengukuran jam kuotasi Monex, drill tahap A.

## 6. Penanganan error

| Kejadian | Perilaku |
|---|---|
| Paket `m1` tak terjawab | "tidak ada perubahan"; langkah SL+ tetap dijalankan EA |
| Paket `m15` tak terjawab | HOLD atau KEEP; bias M15 lama tetap berlaku dengan tanda kedaluwarsa |
| Keputusan tidak valid | 422 dengan kode §2.7; paket tetap terbuka sampai tenggat |
| Adapter mati, chat berhenti, batas pemakaian tercapai | EA menjaga SL/TP broker, langkah SL+, batas waktu, flatten harian dan breaker 3%; tidak ada entry tanpa intent bertanda tangan |
| EA atau MT5 restart | rencana dan langkah dipulihkan dari GlobalVariable, tiket dicocokkan dengan broker; langkah yang terlewat diterapkan hanya bila masih valid, selain itu dilewati dan dilaporkan |
| Modifikasi gagal | EA mencoba ulang sampai 10 detik, lalu `ACTION_FAILED`; paket berikutnya menampilkan hasilnya |
| Aksi ganda atau basi | `action_id` diterapkan sekali; aksi lebih tua dari 30 detik ditolak |
| Snapshot menit hilang atau basi | paket `m1` dilewati; entry MARKET memakai cek drift yang ada |
| Rollover | entry dan paket `m1` dijeda; flatten harian seperti sekarang |
| Halt, breaker, bukan DEMO | FLATTEN dan CANCEL_PENDING, sesi di-disarm |

## 7. Invarian keamanan

Diuji otomatis di adapter dan, bila bisa, di self-test EA:
1. SL tidak pernah melebar, dan setiap posisi V6 selalu punya SL di broker.
2. Risiko di SL awal tidak melebihi budget, lot tidak melebihi 0,03, dan risiko tidak
   melebihi $50 di EA.
3. Paling banyak satu posisi V6; tidak ada entry saat ada posisi atau pending; paling
   banyak 8 entry per hari trading.
4. Batas waktu tidak melebihi 240 menit sejak posisi dibuka.
5. TP1 < TP2 < TP3 searah trade, dan langkah SL+ hanya bergerak ke arah aman.
6. Aksi manajemen hanya menyentuh tiket bermagic V6 dengan intent yang cocok.
7. DEMO saja, di konfigurasi, API operator, intent builder dan EA.

## 8. Pengujian

Adapter (pytest, tabel):
- validasi v3: urutan TP, arah LIMIT/STOP, `d_min`, pemangkasan TP3, langkah SL+;
- `management.py`: setiap op untuk setiap keadaan, SL melebar, tiket salah, batas waktu;
- `minute_packet.py`: tidak memakai bar yang belum tutup; `m1_state` pada data sintetis;
- `OperatorQueue`: dua tenggat, "terbaru menang", penarikan oleh M15;
- siklus menit dengan `FakeClock`: entry, modifikasi, cut, rollover, halt;
- string kanonik intent v2 dan perintah harus identik dengan fixture EA;
- gate `all_day`, batas 8 entry, ledger `v6_actions`;
- integrasi `TestClient`: snapshot menit, paket, keputusan, perintah di poll, laporan
  eksekusi, baris aksi.

Target coverage: ≥ 80% keseluruhan, 100% untuk `risk/`, `deliberation/protocol.py` dan
`deliberation/management.py`.

EA:
- kompilasi MetaEditor 0 error dan 0 warning, lalu salin ke folder terminal;
- self-test: vektor HMAC intent v2 dan perintah, parsing JSON perintah baru, penolakan SL
  melebar;
- drill di Monex-Demo:
  1. langkah SL+ di TP1 dan TP2;
  2. `CLOSE_POSITION`;
  3. `MODIFY_POSITION` yang melebarkan SL harus ditolak;
  4. `MODIFY_PENDING`;
  5. entry BUY_STOP dan SELL_STOP;
  6. perpanjangan batas waktu;
  7. restart EA dengan rencana utuh;
  8. adapter dimatikan saat posisi terbuka.

Replay: `scripts/v6_replay.py` memutar ritme menit dengan keputusan skrip untuk mengukur
jumlah paket per hari dan waktu siklus adapter.

Harness: `tests/v6/test_agent_harness_parity.py` diperbarui untuk perintah dan kode baru.

## 9. Tahapan

**Tahap A: rencana dan manajemen lewat paket M15** (`V6_MINUTE_PACKETS=false`)
- Keputusan v3, rencana TP1–TP3 dan SL+, order STOP, paket M15 untuk tiga keadaan,
  perintah manajemen, intent v2, `Plan.mqh`, batas waktu 60–240 menit, entry 24 jam,
  batas 8 entry, ledger aksi.
- Selesai bila:
  - semua tes hijau dengan target coverage;
  - EA terkompilasi dan terpasang;
  - drill 1–8 lulus;
  - jam kuotasi Monex terukur;
  - satu hari London–NY berjalan dengan minimal satu posisi yang dikelola lewat paket M15
    dan satu langkah SL+ yang dieksekusi EA.

**Tahap B: paket per menit** (`V6_MINUTE_PACKETS=true`)
- Snapshot menit, `MinuteInbox` dan worker, paket `m1`, tenggat 50 detik, `--quick`,
  pembaruan skill dan dokumen, pensiun keputusan v2.
- Selesai bila:
  - satu hari penuh berjalan tanpa antrean menumpuk di adapter (p95 siklus menit
    < 300 ms);
  - minimal 90% paket `m1` terjawab dalam tenggat selama dua jam pasar ramai;
  - pemeriksaan invarian ledger bersih.

## 10. Risiko dan hal yang belum terukur

- **Beban operator.** Sekitar 1.300 paket per hari akan menghabiskan token dalam jumlah
  besar; batas pemakaian langganan bisa menghentikan loop, dan pemadatan konteks
  melewatkan beberapa menit. Keduanya aman karena default "tidak ada perubahan", tetapi
  analisis M1 jadi berlubang. Tahap B mengukur persentase paket yang terjawab.
- **Tenggat 50 detik** bisa terlalu ketat. Satu keputusan dengan tulis file dan submit
  selama ini memakan 20–40 detik. Kalau target 90% tidak tercapai, tenggat dinaikkan
  (maksimal 55 detik) atau lebih banyak jawaban lewat `--quick`.
- **Jam kuotasi Monex** belum diukur; `market/broker_hours` masih memakai hasil
  MetaQuotes (tanpa kuotasi 20:00–22:00 UTC). Entry 24 jam baru diaktifkan setelah
  pengukuran.
- **Stops level Monex** 1 point dan freeze level 0 (probe 2026-09-17), jadi `d_min`
  praktis ditentukan spread. Nilai ini dibaca ulang dari snapshot setiap siklus, tidak
  di-hardcode.
- **Overtrading di M1.** Batas 8 entry per hari dan gate biaya menahannya; ledger
  mencatat entry yang lahir dari paket `m1` terpisah dari paket `m15` supaya bisa
  dibandingkan.
- **Bukti.** SL+ bertahap dan keputusan per menit bertentangan dengan temuan
  `knowledge/01` dan `knowledge/08`. Hasil demo harus dinilai dari ledger (R per trade,
  MAE/MFE, langkah SL+ yang terkena) sebelum ada perubahan lain.
- **Akun kecil.** Di Monex lot praktis 0,01, dan stop maksimum sekitar $7,3 lebih kecil
  dari ATR M15 setelah rilis data ($12–13). Stop yang sempit tetap rentan tersapu;
  operator harus lebih sering HOLD di jam seperti itu.
- Hasil demo tidak sama dengan hasil live.
