# V6 Tahap A: Rencana TP1–TP3, SL+ dan Manajemen Posisi — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Operator V6 bisa masuk dengan MARKET, LIMIT atau STOP beserta SL, TP1–TP3, rencana SL+
dan batas waktu 60–240 menit, lalu mengelola order pending dan posisi terbuka lewat paket
M15, sementara EA menjalankan langkah SL+ sendiri; entry boleh 24 jam (disaring biaya).

**Architecture:** Keputusan operator naik ke schema v3 (`action`, `entry_plan` v2, `manage`,
`m15_bias`). Adapter memvalidasi rencana terhadap `limits` paket, membangun intent v2
(order STOP, level tangga, batas waktu) dan perintah manajemen bertanda tangan
(`CLOSE_POSITION`, `MODIFY_POSITION`, `MODIFY_PENDING`) lewat balasan poll. EA menyimpan
rencana per tiket di GlobalVariable, menggeser SL saat TP1/TP2 tersentuh, dan melaporkan
setiap aksi ke rute baru `/v6/action`. Siklus M15 tetap satu-satunya ritme; paket per menit
adalah tahap B.

**Tech Stack:** Python 3.12, FastAPI, pydantic v2 (strict, frozen), SQLite, pytest + anyio;
MQL5 (MetaEditor build 5xxx), HMAC-SHA256.

**Spec:** `docs/superpowers/specs/2026-09-17-v6-m1-dynamic-management-design.md`
(bagian 2, 3, 4, 6, 7, 8 dan tahap A di bagian 9).

## Global Constraints

- DEMO saja di semua lapisan; `V6_ALLOW_REAL_ACCOUNT` tetap ditolak.
- File ≤ 400 baris, fungsi < 50 baris, type hints di semua fungsi, model pydantic strict/frozen,
  dataclass frozen, `Decimal` untuk uang/lot, epoch UTC integer, SQL berparameter,
  tanpa `print()` di kode app.
- Jangan pernah membuka atau mencetak `adapter/.env`, token operator atau kunci HMAC EA.
- Setiap edit `.mq5`/`.mqh`: salin ke
  `C:\Users\Huawei\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Experts`
  dan kompilasi dengan `"C:\Program Files\MetaTrader 5\MetaEditor64.exe" /compile:<file> /log:<log>`
  pada giliran yang sama. Log berformat UTF-16 dan harus berisi `0 errors, 0 warnings`.
- Tes dijalankan dari `adapter/`:
  `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider <path>`.
- Coverage: ≥ 80% keseluruhan; 100% untuk `app/v6/risk/`, `deliberation/protocol.py`,
  `deliberation/management.py`, `deliberation/plan_rules.py`.
- Commit bergaya conventional commit tanpa baris atribusi, hanya saat tugas selesai dan tes hijau.
- Nilai dari spec: lot 0,01–0,03; TP1 ≥ 0,5R; 1R ≤ TP3 ≤ 5R; batas waktu 60–240 menit;
  pending 15–60 menit; `d_min = max(stops_level, freeze_level) × point + spread + $0,10`;
  aksi lebih tua dari 30 detik ditolak EA; batas entry harian 8; tenggat paket M15 180 detik.

## Penyimpangan dari spec (disengaja, dicatat di dokumen pada Task 18)

- Kunci `V6_M15_DEADLINE_S` tidak dibuat; `V6_OPERATOR_DEADLINE_S` tetap dipakai sebagai
  tenggat paket M15 dengan default baru 180 detik.
- Kunci jendela rencana bernama `V6_TIME_LIMIT_MIN_MINUTES`, `V6_TIME_LIMIT_MAX_MINUTES`,
  `V6_PENDING_EXPIRY_MIN_MINUTES`, `V6_PENDING_EXPIRY_MAX_MINUTES`.
- Laporan aksi dan langkah SL+ dikirim ke rute baru `POST /v6/action` (`v6.action.1`),
  bukan ke `/v6/execution`, supaya `ExecutionReport` tetap satu bentuk.
- Sesi 24 jam diwujudkan sebagai **pembaruan sesi otomatis**: sesi harian tetap ditutup di
  blok rollover (ringkasan harian tetap per hari), lalu runtime membuka dan meng-arm sesi
  hari berikutnya begitu blok selesai. `wait` tidak keluar dengan kode 4 selama pembaruan
  masih ditunggu.
- `InTradeSession` di EA sudah memakai jam trading simbol dari broker (bukan jam
  London–NY), jadi tidak diubah.
- `v6_outcomes` tidak diubah: excursion posisi sudah ada di snapshot (`mae_points`,
  `mfe_points`) dan di record Track EA, sedangkan setiap langkah SL+ dicatat di tabel baru
  `v6_plan_steps` (kunci tiket), yang bisa digabung dengan hasil basket untuk mengukur
  dampak SL+.

## Catatan pelaksanaan gelombang Task 8–11 (2026-09-18)

Dikerjakan dan di-commit bersama, karena tes baru hijau setelah keempatnya selesai. Hal yang
berbeda dari teks tugas di bawah (tugas berikutnya mengikuti catatan ini):

- **`PositionBlock.plan_step` dan `time_limit_epoch` pindah ke Task 15.** Tes kontrak golden
  mewajibkan setiap field snapshot dikirim EA, dan mengompilasi EA sebelum Task 15 akan
  membuat snapshot ditolak adapter yang sedang berjalan. Sampai Task 15, blok `position`
  memakai intent tersimpan: `plan.step` = `v6_intents.plan_step` (diisi laporan PLAN_STEP di
  Task 12), `time_limit_epoch` = `open_epoch + time_barrier_s` intent (tanpa intent:
  `V6_TIME_BARRIER_S`). Task 15 menambah kedua field di snapshot, EA, golden, dan membuat
  `position_block` memakai nilai EA bila ada.
- **Bagian bersama keputusan** ada di `deliberation/decision_parts.py` (envelope v2 dan v3,
  `ValidatedDecision`, parse, pemeriksaan paket dan view); `operator_decision.py` memegang
  aturan v1/v2 dan dispatch, `decision_v3.py` aturan v3. `parse_envelope` sudah mengenali v3,
  karena antrean operator mem-parse sebelum memvalidasi.
- **ENTER v3 mewajibkan Price Action TAKE `limits.agent_entry_id`** dengan conviction
  ≥ `pa_min_conviction` (`DECISION_VIEW`, role `price_action`), supaya agen langsung tahu,
  bukan mendapat HOLD dari protokol setelah diterima.
- **MODIFY posisi memeriksa tangga sisa pada setiap perubahan**: SL, TP yang langkahnya belum
  tereksekusi, dan TP3 harus maju searah trade; langkah SL+ yang sudah tereksekusi tidak
  lagi membatasi SL (trailing setelah TP2 boleh). Posisi tanpa TP butuh `tp3`
  (`LADDER_MISSING`); `barrier_s` default = batas waktu yang sedang berjalan (wire mewajibkan
  > 0). Pending tanpa TP/SL butuh level itu (`LADDER_MISSING`).
- **Intent dicari lewat komentar, lalu lewat tiket** (`PlanReader.by_ticket`), karena broker
  bisa menulis ulang komentar; blok paket memakai `intent_id` tersimpan bila komentar rusak.
- **`note` keputusan v3** dibersihkan dari karakter kontrol (`DecisionNote`).
- **Sebagian Task 17 dan 18 sudah dikerjakan** agar suite hijau: CLI `decisions.py`
  (templat v3, `manage_example`, peringatan v3; peringatan bias `unclear` hanya untuk ENTER),
  `packet_view.py` (baris posisi, pending, bias, aksi terakhir, limits STOP/TP1/waktu),
  `waiting.py` (`state` dan bloknya); `docs/v6-operator.md` bagian 6 (contoh paket dan
  keputusan v3 diregenerasi dan divalidasi, tabel field dan penolakan) dan 5.9 (manajemen,
  menggantikan review). Sisa Task 17 (tes `operator_cli_fixtures_v6`) dan Task 18 (rubrik
  5.6/5.8 ke v3, bagian baru 5.10–5.13, ringkasan Indonesia, skill, `AGENTS.md`, runbook,
  wire contract) tetap di tugasnya.
- `IntentPublisher.manage` belum ada sampai Task 12: jangan jalankan adapter baru untuk
  trading sebelum Task 12–15 selesai.

## Catatan pelaksanaan Task 12 (2026-09-18)

- Kedaluwarsa aksi dijalankan di `ExecutionDesk.supervise` (langkah watchdog `supervise`
  yang sudah ada, di samping kedaluwarsa intent), bukan langkah watchdog baru: papan aksi
  milik desk (`DeskDeps.actions`), `RuntimeParts.actions` menunjuk ke sana.
- `ActionBoard.queue(action)` mengembalikan aksi yang digantikan; publisher menandainya
  `EXPIRED` ("superseded by ...") supaya tidak ada baris yang tertinggal PUBLISHED.
- `publisher.manage` memvalidasi aksi terhadap aturan wire (`action_response`) sebelum
  menyimpannya; aksi yang melanggar ditolak dengan `ACTION_INVALID` dan tidak disimpan.
- MODIFY_PENDING yang APPLIED juga memperbarui `entry`, `sl`, `tp` intent
  (`IntentStore.update_levels`), supaya risiko awal posisi yang terisi kemudian diukur dari
  level order yang benar.
- `/v6/action` masuk daftar rute di tes keamanan, rute EA dan batas body; paritas path EA
  (`test_ea_safety_parity.py`) menunggu Task 15, saat EA mulai mengirim ke rute ini.

---

## Peta file

| File | Status | Tanggung jawab |
|---|---|---|
| `adapter/app/v6/risk/limits.py` | ubah | konstanta tahap A |
| `adapter/app/v6/config.py` | ubah | `entry_hours`, batas entry 8, tenggat 180, jendela rencana, `session_auto_renew` |
| `adapter/app/v6/market/sessions.py` | ubah | `entry_blocks` untuk jam entry 24 jam |
| `adapter/app/v6/risk/gates.py` | ubah | gate SESSION memakai `entry_blocks` |
| `adapter/app/v6/schemas/operator_plan.py` | baru | `EntryPlanV2`, `ManageRequest`, `M15Bias`, blok paket posisi/rencana/aksi |
| `adapter/app/v6/deliberation/agent_entry.py` | ubah | `stop_problems`/`target_problems` publik, `modify_distance`, batas STOP |
| `adapter/app/v6/deliberation/plan_rules.py` | baru | validasi rencana v2 terhadap `limits`, kandidat dan `TradePlan` |
| `adapter/app/v6/types.py` | ubah | `TradePlan` |
| `adapter/app/v6/schemas/intent.py` | ubah | intent v2: order STOP, level tangga, perintah manajemen, `ActionReport` |
| `adapter/app/v6/wire.py` | ubah | string kanonik v2 |
| `adapter/app/v6/ledger_intents.py` | ubah | kolom rencana di `v6_intents` |
| `adapter/app/v6/ledger_actions.py` | baru | tabel `v6_actions` dan `ActionStore` |
| `adapter/app/v6/risk/order_choice.py` | baru | pemilihan jenis order (dipindah dari `intent_builder.py`) + STOP |
| `adapter/app/v6/risk/intent_builder.py` | ubah | rencana ke intent v2 |
| `adapter/app/v6/deliberation/publication.py` | ubah | `PublishRequest.plan`, `ManagementAction`, `ManageDispatch`, port `manage` |
| `adapter/app/v6/deliberation/management.py` | baru | validasi `manage` dan pembuatan aksi |
| `adapter/app/v6/deliberation/trade_state.py` | baru (menggantikan `pending_review.py`) | keadaan flat/pending/position dan kapan manajemen ditawarkan |
| `adapter/app/v6/schemas/operator.py` | ubah | paket v3, templat v3, kode penolakan baru |
| `adapter/app/v6/schemas/operator_parts.py` | ubah | `PacketLimits` baru, `PacketPendingOrder.plan` |
| `adapter/app/v6/deliberation/packet_extras.py` | ubah | blok posisi, pending dan limits v3 |
| `adapter/app/v6/deliberation/operator_packet.py` | ubah | `PacketRequest.state/plans/last_action/last_bias` |
| `adapter/app/v6/deliberation/decision_v3.py` | baru | envelope dan validasi keputusan v3 |
| `adapter/app/v6/deliberation/operator_decision.py` | ubah | dispatch v3, field baru `ValidatedDecision` |
| `adapter/app/v6/deliberation/operator_flow.py` | baru | jalur operator engine (dipindah dari `engine.py`) + ENTER berencana + MANAGE |
| `adapter/app/v6/deliberation/engine.py` | ubah | routing keadaan |
| `adapter/app/v6/cycle_codes.py` | ubah | `HoldReason.MANAGE_*` |
| `adapter/app/v6/runtime/actions.py` | baru | `ActionBoard` |
| `adapter/app/v6/runtime/publisher.py` | ubah | `manage()` |
| `adapter/app/v6/runtime/poll_reply.py` | ubah | balasan poll membawa aksi |
| `adapter/app/v6/runtime/action_desk.py` | baru | menerapkan `v6.action.1` ke ledger dan papan aksi |
| `adapter/app/routes/v6_ea.py` | ubah | `POST /v6/action` |
| `adapter/app/v6/schemas/snapshot.py` | ubah | `PositionBlock.plan_step`, `time_limit_epoch` |
| `adapter/app/v6/runtime/sessions.py`, `watchdog.py` | ubah | pembaruan sesi otomatis |
| `adapter/app/routes/v6_status_view.py` | ubah | `session_renewal_due` di `/v6/status` |
| `adapter/scripts/v6ops/*.py` | ubah | templat v3, ringkasan posisi/rencana, `session_gone` |
| `ea/QlipV6/Intent.mqh`, `SelfTest.mqh` | ubah | intent v2 dan vektor HMAC |
| `ea/QlipV6/Checks.mqh`, `Execute.mqh` | ubah | order STOP, rencana ke Track |
| `ea/QlipV6/Track.mqh`, `Snapshot.mqh` | ubah | field rencana tersimpan dan dilaporkan |
| `ea/QlipV6/Plan.mqh` | baru | langkah SL+ lokal |
| `ea/QlipV6/Actions.mqh` | baru | CLOSE/MODIFY posisi dan pending |
| `ea/QlipV6/Report.mqh`, `Outbox.mqh`, `Poll.mqh`, `Manage.mqh`, `Orders.mqh` | ubah | laporan aksi, jalur `/v6/action`, perintah baru, modifikasi order |
| `ea/QlipV6_XAUUSD.mq5` | ubah | versi 6.2.0, langkah SL+ di OnTick |
| `docs/v6-operator.md`, `docs/v6-wire-contract.md`, `docs/v6-runbook.md`, skill, `AGENTS.md`, `adapter/.env.example` | ubah | dokumentasi tahap A |
| `adapter/app/v6/dashboard_queries.py`, `app/templates/v6.html`, `app/static/v6_render.js` | ubah | rencana dan aksi di dashboard |

## Task 1: Konstanta dan pengaturan tahap A

**Files:**
- Modify: `adapter/app/v6/risk/limits.py` (tambah konstanta di akhir file)
- Modify: `adapter/app/v6/config.py:93-123` (field) dan validator `_check_intent_timing`
- Modify: `adapter/.env.example` (bagian V6)
- Test: `adapter/tests/v6/test_config_phase_a.py` (baru); sesuaikan `tests/v6/test_v6_config.py`
  yang masih mengharapkan default lama (`operator_deadline_s == 300`, `max_trades_per_day == 4`)

**Interfaces:**
- Produces: `limits.MIN_TIME_LIMIT_S`, `limits.MIN_PENDING_EXPIRY_S`,
  `limits.MAX_PENDING_EXPIRY_S`, `limits.MIN_TP1_R`, `limits.MODIFY_BUFFER_PRICE`,
  `limits.ACTION_MAX_AGE_S`, `limits.MAX_TRADES_PER_DAY_CEILING`;
  `V6Settings.entry_hours: Literal["all_day", "london_ny"]`,
  `V6Settings.time_limit_bounds_s -> tuple[int, int]`,
  `V6Settings.pending_expiry_bounds_s -> tuple[int, int]`,
  `V6Settings.session_auto_renew: bool`.

- [ ] **Step 1: Tulis tes yang gagal**

```python
"""Phase A settings: entry hours, trade cap, deadlines, plan windows, session renewal."""

from __future__ import annotations

from typing import Any

import pytest

from app.v6.config import V6Settings
from app.v6.risk import limits


def settings(**overrides: Any) -> V6Settings:
    return V6Settings(_env_file=None, **overrides)


def test_phase_a_defaults() -> None:
    s = settings()
    assert s.entry_hours == "all_day" and s.session_auto_renew is True
    assert (s.max_trades_per_day, s.operator_deadline_s) == (8, 180)
    assert s.time_limit_bounds_s == (3600, 14400)
    assert s.pending_expiry_bounds_s == (900, 3600)


@pytest.mark.parametrize("overrides", [
    {"time_limit_min_minutes": 59},
    {"time_limit_max_minutes": 241},
    {"time_limit_min_minutes": 120, "time_limit_max_minutes": 90},
    {"pending_expiry_min_minutes": 14},
    {"pending_expiry_max_minutes": 61},
    {"pending_expiry_min_minutes": 30, "pending_expiry_max_minutes": 20},
    {"max_trades_per_day": 21},
    {"entry_hours": "asia_only"},
])
def test_plan_windows_may_only_tighten(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        settings(**overrides)


def test_tightened_windows_are_accepted() -> None:
    s = settings(time_limit_min_minutes=90, time_limit_max_minutes=180,
                 pending_expiry_min_minutes=20, pending_expiry_max_minutes=45,
                 entry_hours="london_ny")
    assert s.time_limit_bounds_s == (5400, 10800)
    assert s.pending_expiry_bounds_s == (1200, 2700)


def test_the_deadline_leaves_the_shortest_pending_order_time_to_live() -> None:
    with pytest.raises(ValueError, match="pending expiry"):
        settings(operator_deadline_s=700, intent_ttl_s=300)


def test_phase_a_limits() -> None:
    assert (limits.MIN_TIME_LIMIT_S, limits.MAX_TIME_BARRIER_S) == (3600, 14400)
    assert (limits.MIN_PENDING_EXPIRY_S, limits.MAX_PENDING_EXPIRY_S) == (900, 3600)
    assert (limits.MIN_TP1_R, limits.MODIFY_BUFFER_PRICE) == (0.5, 0.10)
    assert (limits.ACTION_MAX_AGE_S, limits.MAX_TRADES_PER_DAY_CEILING) == (30, 20)
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_config_phase_a.py`
Expected: FAIL (`AttributeError: ... 'MIN_TIME_LIMIT_S'`).

- [ ] **Step 3: Implementasi**

Tambahkan di akhir `limits.py`:

```python
# --- Phase A of the M1 dynamic-management design (user decisions 2026-09-17) ---------------
# An agent plan holds between 60 min and MAX_TIME_BARRIER_S (4 h).
MIN_TIME_LIMIT_S: Final[int] = 3600
# A LIMIT or STOP rests 15-60 min.
MIN_PENDING_EXPIRY_S: Final[int] = 900
MAX_PENDING_EXPIRY_S: Final[int] = 3600
# TP1 sits at least half the initial risk beyond the entry.
MIN_TP1_R: Final[float] = 0.5
# d_min = max(stops_level, freeze_level) x point + spread + this, in price units.
MODIFY_BUFFER_PRICE: Final[float] = 0.10
# The EA refuses a management action issued longer ago than this.
ACTION_MAX_AGE_S: Final[int] = 30
MAX_TRADES_PER_DAY_CEILING: Final[int] = 20
```

Di `config.py`, di bawah alias tipe yang ada:

```python
EntryHours = Literal["all_day", "london_ny"]
SECONDS_PER_MINUTE: Final[int] = 60
```

Ubah dan tambah field:

```python
    operator_deadline_s: int = Field(default=180, ge=30, le=840)
    ...
    max_trades_per_day: int = Field(default=8, ge=1, le=limits.MAX_TRADES_PER_DAY_CEILING)
    ...
    # --- agent plans (Phase A, user decisions 2026-09-17) ----------------------------------
    entry_hours: EntryHours = "all_day"
    session_auto_renew: bool = True
    time_limit_min_minutes: int = Field(default=60, ge=1)
    time_limit_max_minutes: int = Field(default=240, ge=1)
    pending_expiry_min_minutes: int = Field(default=15, ge=1)
    pending_expiry_max_minutes: int = Field(default=60, ge=1)
```

Properti baru di bagian "derived views":

```python
    @property
    def time_limit_bounds_s(self) -> tuple[int, int]:
        """(shortest, longest) holding time an agent plan may ask for, in seconds."""
        return (self.time_limit_min_minutes * SECONDS_PER_MINUTE,
                self.time_limit_max_minutes * SECONDS_PER_MINUTE)

    @property
    def pending_expiry_bounds_s(self) -> tuple[int, int]:
        """(shortest, longest) resting time of an agent LIMIT or STOP, in seconds."""
        return (self.pending_expiry_min_minutes * SECONDS_PER_MINUTE,
                self.pending_expiry_max_minutes * SECONDS_PER_MINUTE)
```

Validator baru, dan `_check_intent_timing` diganti:

```python
    @model_validator(mode="after")
    def _check_plan_windows(self) -> "V6Settings":
        low, high = self.time_limit_bounds_s
        if not limits.MIN_TIME_LIMIT_S <= low <= high <= limits.MAX_TIME_BARRIER_S:
            raise ValueError("V6_TIME_LIMIT_MIN_MINUTES and V6_TIME_LIMIT_MAX_MINUTES may only "
                             "tighten 60-240 min, with min <= max.")
        low, high = self.pending_expiry_bounds_s
        if not limits.MIN_PENDING_EXPIRY_S <= low <= high <= limits.MAX_PENDING_EXPIRY_S:
            raise ValueError("V6_PENDING_EXPIRY_MIN_MINUTES and V6_PENDING_EXPIRY_MAX_MINUTES "
                             "may only tighten 15-60 min, with min <= max.")
        return self

    @model_validator(mode="after")
    def _check_intent_timing(self) -> "V6Settings":
        """A decision may arrive V6_OPERATOR_DEADLINE_S after the close and its intent stays
        valid for V6_INTENT_TTL_S; the shortest pending order must still have time to live."""
        shortest = min(self.pending_expiry_s, self.pending_expiry_bounds_s[0])
        latest_valid_until = self.operator_deadline_s + self.intent_ttl_s + MIN_PENDING_LIFETIME_S
        if latest_valid_until > shortest:
            raise ValueError(
                f"V6_OPERATOR_DEADLINE_S + V6_INTENT_TTL_S + {MIN_PENDING_LIFETIME_S} s must not "
                f"exceed the shortest pending expiry ({shortest} s).")
        return self
```

Di `adapter/.env.example`, bagian V6, tambahkan (satu komentar pendek di atas tiap
kelompok):

```text
# Entry hours: all_day (the cost gates decide) or london_ny.
V6_ENTRY_HOURS=all_day
# Reopen and re-arm the session once the daily rollover block ends.
V6_SESSION_AUTO_RENEW=true
V6_MAX_TRADES_PER_DAY=8
# Deadline of an M15 packet, seconds after the bar close.
V6_OPERATOR_DEADLINE_S=180
V6_TIME_LIMIT_MIN_MINUTES=60
V6_TIME_LIMIT_MAX_MINUTES=240
V6_PENDING_EXPIRY_MIN_MINUTES=15
V6_PENDING_EXPIRY_MAX_MINUTES=60
```

- [ ] **Step 4: Jalankan tes baru dan tes config lama**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_config_phase_a.py tests/v6/test_v6_config.py`
Expected: PASS setelah default lama di `test_v6_config.py` diganti (300 → 180, 4 → 8).

- [ ] **Step 5: Jalankan seluruh `tests/v6`**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6 -x`
Expected: PASS. Tes engine yang bergantung pada tenggat 300 detik diberi
`ef.settings(operator_deadline_s=300)`.

- [ ] **Step 6: Commit**

```bash
git add adapter/app/v6/risk/limits.py adapter/app/v6/config.py adapter/.env.example adapter/tests/v6/test_config_phase_a.py adapter/tests/v6/test_v6_config.py
git commit -m "feat: add V6 phase A limits and settings"
```

---

## Task 2: Entry 24 jam lewat gate SESSION

**Files:**
- Modify: `adapter/app/v6/market/sessions.py` (tambah `ALL_DAY_ENTRY_BLOCKS`, `entry_blocks`)
- Modify: `adapter/app/v6/risk/gates.py` (`_session_gate`)
- Modify: `adapter/app/v6/deliberation/operator_packet.py` (`_session` memakai `entry_blocks`)
- Test: `adapter/tests/v6/test_entry_hours.py` (baru)

**Interfaces:**
- Consumes: `V6Settings.entry_hours`, `V6Settings.pending_expiry_bounds_s` (Task 1)
- Produces: `sessions.entry_blocks(state: SessionState, entry_hours: str) -> tuple[str, ...]`

- [ ] **Step 1: Tulis tes yang gagal**

```python
"""Entry hours: all_day drops only the London-NY window; hard blocks stay."""

from __future__ import annotations

import pytest

from app.v6.config import V6Settings
from app.v6.market.sessions import (
    BLOCK_OUTSIDE_TRADING_HOURS, BLOCK_ROLLOVER, entry_blocks, session_state,
)
from app.v6.risk import gates

ASIA_01_UTC = 1_789_606_800        # Thu 2026-09-17 01:00 UTC
OVERLAP_1245_UTC = 1_789_649_100   # Thu 2026-09-17 12:45 UTC
ROLLOVER_2130_UTC = 1_789_681_800  # Thu 2026-09-17 21:30 UTC (17:30 New York)


@pytest.mark.parametrize(("epoch", "all_day", "london_ny"), [
    (ASIA_01_UTC, (), (BLOCK_OUTSIDE_TRADING_HOURS,)),
    (OVERLAP_1245_UTC, (), ()),
    (ROLLOVER_2130_UTC, (BLOCK_ROLLOVER,), (BLOCK_ROLLOVER, BLOCK_OUTSIDE_TRADING_HOURS)),
])
def test_entry_blocks(epoch: int, all_day: tuple[str, ...], london_ny: tuple[str, ...]) -> None:
    state = session_state(epoch)
    assert entry_blocks(state, "all_day") == all_day
    assert entry_blocks(state, "london_ny") == london_ny


class _Context:
    """The two attributes `_session_gate` reads."""

    def __init__(self, epoch: int) -> None:
        self.session = session_state(epoch)
        self.as_of_epoch = epoch


@pytest.mark.parametrize(("hours", "passed"), [("all_day", True), ("london_ny", False)])
def test_the_session_gate_follows_entry_hours(hours: str, passed: bool) -> None:
    settings = V6Settings(_env_file=None, entry_hours=hours, broker_quote_gap_utc="")
    gate = gates._session_gate(_Context(ASIA_01_UTC), settings)  # noqa: SLF001
    assert gate.passed is passed
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_entry_hours.py`
Expected: FAIL (`ImportError: cannot import name 'entry_blocks'`).

- [ ] **Step 3: Implementasi**

Di `sessions.py`, di bawah konstanta `BLOCK_*` dan setelah kelas `SessionState`:

```python
# With entry_hours=all_day (user decision 2026-09-17) the cost gates decide when to trade;
# these blocks still stop every entry.
ALL_DAY_ENTRY_BLOCKS: Final[frozenset[str]] = frozenset({
    BLOCK_WEEKEND, BLOCK_ROLLOVER, BLOCK_LBMA_PAUSE, BLOCK_US_DATA_BAR})


def entry_blocks(state: SessionState, entry_hours: str) -> tuple[str, ...]:
    """The session blocks that stop an entry: all of them for london_ny, all but
    OUTSIDE_TRADING_HOURS for all_day."""
    if entry_hours == "london_ny":
        return state.block_reasons
    return tuple(code for code in state.block_reasons if code in ALL_DAY_ENTRY_BLOCKS)
```

`_session_gate` di `gates.py`:

```python
def _session_gate(context: MarketContext, settings: V6Settings) -> GateResult:
    """Session blocks for the configured entry hours, plus the broker quote gap: an order
    placed now must not be able to rest into the gap (nobody could manage its fill)."""
    session = context.session
    lifetime_s = max(settings.pending_expiry_s, settings.pending_expiry_bounds_s[1])
    reasons = entry_blocks(session, settings.entry_hours) + entry_block(
        context.as_of_epoch, settings.quote_gap, lifetime_s)
    return GateResult(code=GATE_SESSION, passed=not reasons,
                      value=",".join(reasons) or VALUE_OK,
                      detail=f"phase={session.phase} quality={session.quality} "
                             f"third={session.main_window_third} hours={settings.entry_hours}")
```

`_session` di `operator_packet.py` menerima `settings` dan memakai blok efektif:

```python
def _session(context: MarketContext, armed: bool, settings: V6Settings) -> Document:
    state = context.session
    blocks = entry_blocks(state, settings.entry_hours)
    return {"phase": state.phase, "main_window_third": state.main_window_third,
            "entries_allowed": not blocks,
            "continuation_allowed": state.continuation_allowed,
            "block_reasons": _codes(blocks), "armed": bool(armed),
            "quality": state.quality, "in_main_window": bool(state.in_main_window)}
```

dengan pemanggil `"session": _session(context, request.armed, settings),` di `_body`.

- [ ] **Step 4: Jalankan tes**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_entry_hours.py tests/v6`
Expected: PASS. Tes gate lama yang mengharapkan SESSION gagal di luar jam London–NY diberi
`entry_hours="london_ny"`.

- [ ] **Step 5: Commit**

```bash
git add adapter/app/v6/market/sessions.py adapter/app/v6/risk/gates.py adapter/app/v6/deliberation/operator_packet.py adapter/tests/v6
git commit -m "feat: allow V6 entries all day behind the cost gates"
```

---

## Task 3: Model rencana, permintaan manajemen dan bias M15

**Files:**
- Create: `adapter/app/v6/schemas/operator_plan.py`
- Test: `adapter/tests/v6/test_operator_plan_models.py`

**Interfaces:**
- Produces (semua di `app.v6.schemas.operator_plan`):
  - `PlanOrderType = Literal["MARKET", "LIMIT", "STOP"]`, `ManageTarget`, `ManageOp`,
    `BiasDirection`, `DecisionAction = Literal["HOLD", "ENTER", "MANAGE"]`,
    `PacketKind = Literal["m15"]`, `PacketState = Literal["flat", "pending", "position"]`,
    `ActionStatus`
  - `EntryPlanV2(side, order_type, entry, sl, tp1, tp2, tp3, sl_after_tp1, sl_after_tp2,
    time_limit_min, pending_expiry_min, lots, thesis)`
  - `ManageRequest(target, ticket, op, sl, tp1, tp2, tp3, sl_after_tp1, sl_after_tp2,
    time_limit_min, entry, pending_expiry_min, reason)` dengan properti
    `changes -> dict[str, float | int]`
  - `M15Bias(direction, levels, invalidation, scenario)`
  - `PacketPlan(tp1, tp2, sl_after_tp1, sl_after_tp2, step, time_limit_min)`
  - `PacketPosition(ticket, intent_id, side, lots, open_price, open_epoch, sl, tp,
    initial_sl, profit, r_now, mae_points, mfe_points, minutes_open, time_limit_epoch, plan)`
  - `PacketAction(action_id, op, ticket, status, detail, at_epoch)`
  - `ladder_problems(side, entry, sl, tp1, tp2, tp3, sl_after_tp1, sl_after_tp2) -> list[str]`
    (setiap entri berbentuk `"KODE: pesan"`)
  - `OPS_BY_TARGET`, `MODIFY_FIELDS`, `PENDING_ONLY_FIELDS`

Model hanya memeriksa tipe dan rentang. Urutan level dan kecocokan dengan pasar diperiksa
di `plan_rules` dan `management`, supaya penolakan bisa menyebut aturan yang dilanggar
tanpa menggemakan teks kiriman.

- [ ] **Step 1: Tulis tes yang gagal**

```python
"""Decision v3 building blocks: types and ranges only; ordering lives in ladder_problems."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.v6.schemas.operator_plan import (
    EntryPlanV2, M15Bias, ManageRequest, PacketPlan, ladder_problems,
)


def plan(**changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "side": "buy", "order_type": "LIMIT", "entry": 4360.5, "sl": 4353.5,
        "tp1": 4366.0, "tp2": 4371.0, "tp3": 4378.0, "sl_after_tp1": 4361.0,
        "sl_after_tp2": 4366.0, "time_limit_min": 150, "pending_expiry_min": 30,
        "lots": 0.01, "thesis": "floor 4359-4360 held three times"}
    return {**document, **changes}


def test_a_plan_parses() -> None:
    parsed = EntryPlanV2.model_validate(plan())
    assert (parsed.order_type, parsed.tp3, parsed.lots) == ("LIMIT", 4378.0, 0.01)


@pytest.mark.parametrize("changes", [
    {"order_type": "BUY_LIMIT"}, {"lots": 0}, {"time_limit_min": 241},
    {"pending_expiry_min": 61}, {"sl": -1.0}, {"thesis": "x" * 301}, {"extra": 1},
    {"tp1": float("nan")},
])
def test_types_and_ranges_are_strict(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        EntryPlanV2.model_validate(plan(**changes))


@pytest.mark.parametrize(("side", "levels", "ok"), [
    ("buy", (4360.5, 4353.5, 4366.0, 4371.0, 4378.0, 4361.0, 4366.0), True),
    ("buy", (None, 4353.5, 4366.0, 4371.0, 4378.0, None, None), True),
    ("buy", (4360.5, 4353.5, 4371.0, 4366.0, 4378.0, None, None), False),
    ("buy", (4360.5, 4361.0, 4366.0, 4371.0, 4378.0, None, None), False),
    ("buy", (4360.5, 4353.5, 4366.0, 4371.0, 4378.0, 4367.0, None), False),
    ("buy", (4360.5, 4353.5, 4366.0, 4371.0, 4378.0, 4362.0, 4361.0), False),
    ("sell", (4358.5, 4365.5, 4353.0, 4349.0, 4345.0, 4357.0, 4353.0), True),
    ("sell", (4358.5, 4365.5, 4353.0, 4349.0, 4345.0, 4366.0, None), False),
])
def test_ladder_problems(side: str, levels: tuple[Any, ...], ok: bool) -> None:
    entry, sl, tp1, tp2, tp3, s1, s2 = levels
    problems = ladder_problems(side, entry, sl, tp1, tp2, tp3, s1, s2)
    assert (problems == []) is ok
    assert all(":" in text for text in problems)


def test_manage_request_lists_its_changes() -> None:
    request = ManageRequest.model_validate(
        {"target": "position", "ticket": 91, "op": "MODIFY", "sl": 4361.0,
         "time_limit_min": 200, "reason": "higher low at 4361"})
    assert request.changes == {"sl": 4361.0, "time_limit_min": 200}
    keep = ManageRequest.model_validate({"target": "pending", "ticket": 7, "op": "KEEP"})
    assert keep.changes == {}


@pytest.mark.parametrize("document", [
    {"target": "position", "ticket": 0, "op": "KEEP"},
    {"target": "order", "ticket": 1, "op": "KEEP"},
    {"target": "position", "ticket": 1, "op": "HOLD"},
    {"target": "position", "ticket": 1, "op": "MODIFY", "time_limit_min": 0},
])
def test_manage_request_types(document: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ManageRequest.model_validate(document)


def test_bias_and_plan_blocks() -> None:
    bias = M15Bias.model_validate({"direction": "range", "levels": [4355.0, 4381.5],
                                   "invalidation": None, "scenario": "fade the edges"})
    assert bias.levels == (4355.0, 4381.5)
    with pytest.raises(ValidationError):
        M15Bias.model_validate({"direction": "up", "levels": [1.0] * 7})
    assert PacketPlan(tp1=0.0, tp2=0.0, sl_after_tp1=0.0, sl_after_tp2=0.0, step=0,
                      time_limit_min=0).step == 0
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_operator_plan_models.py`
Expected: FAIL (`ModuleNotFoundError: app.v6.schemas.operator_plan`).

- [ ] **Step 3: Implementasi `operator_plan.py`**

```python
"""
Decision v3 building blocks (docs/superpowers/specs/2026-09-17-v6-m1-dynamic-management-design.md).

An agent plan carries the initial stop, a three-level target ladder and optional SL+ steps;
a manage request changes a resting order or an open position; the M15 bias is the agent's
own reading, echoed in later packets. The models check types and ranges only:
`deliberation.plan_rules` and `deliberation.management` judge the numbers against the
packet, so their refusals can name the broken rule without echoing submitted text.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Final, Literal

from pydantic import AfterValidator, Field, StringConstraints

from ..types import Side
from .agents import _printable as printable
from .operator_parts import Epoch, Frozen, Price

PlanOrderType = Literal["MARKET", "LIMIT", "STOP"]
ManageTarget = Literal["position", "pending"]
ManageOp = Literal["KEEP", "CLOSE", "CANCEL", "MODIFY"]
BiasDirection = Literal["up", "down", "range", "unclear"]
DecisionAction = Literal["HOLD", "ENTER", "MANAGE"]
PacketKind = Literal["m15"]
PacketState = Literal["flat", "pending", "position"]
ActionStatus = Literal["PUBLISHED", "APPLIED", "REJECTED", "FAILED", "EXPIRED"]

MAX_BIAS_LEVELS: Final[int] = 6
MAX_SCENARIO_CHARS: Final[int] = 240
MAX_REASON_CHARS: Final[int] = 200
MAX_THESIS_CHARS: Final[int] = 300
MAX_ACTION_DETAIL_CHARS: Final[int] = 120
MAX_PLAN_MINUTES: Final[int] = 240
MAX_EXPIRY_MINUTES: Final[int] = 60
MAX_PLAN_LOTS: Final[float] = 1.0
OPS_BY_TARGET: Final[Mapping[str, frozenset[str]]] = MappingProxyType({
    "position": frozenset({"KEEP", "CLOSE", "MODIFY"}),
    "pending": frozenset({"KEEP", "CANCEL", "MODIFY"}),
})
MODIFY_FIELDS: Final[tuple[str, ...]] = (
    "sl", "tp1", "tp2", "tp3", "sl_after_tp1", "sl_after_tp2", "time_limit_min",
    "entry", "pending_expiry_min")
PENDING_ONLY_FIELDS: Final[tuple[str, ...]] = ("entry", "pending_expiry_min")

Thesis = Annotated[str, StringConstraints(max_length=MAX_THESIS_CHARS),
                   AfterValidator(printable)]
Reason = Annotated[str, StringConstraints(max_length=MAX_REASON_CHARS),
                   AfterValidator(printable)]
Scenario = Annotated[str, StringConstraints(max_length=MAX_SCENARIO_CHARS),
                     AfterValidator(printable)]
ActionDetail = Annotated[str, StringConstraints(max_length=MAX_ACTION_DETAIL_CHARS),
                         AfterValidator(printable)]
PlanMinutes = Annotated[int, Field(ge=1, le=MAX_PLAN_MINUTES)]
ExpiryMinutes = Annotated[int, Field(ge=1, le=MAX_EXPIRY_MINUTES)]


class EntryPlanV2(Frozen):
    """An entry the agent designed: initial stop, TP ladder, SL+ steps, holding time, size."""

    side: Side
    order_type: PlanOrderType
    entry: Price | None = None
    sl: Price
    tp1: Price
    tp2: Price
    tp3: Price
    sl_after_tp1: Price | None = None
    sl_after_tp2: Price | None = None
    time_limit_min: PlanMinutes
    pending_expiry_min: ExpiryMinutes | None = None
    lots: float = Field(gt=0, le=MAX_PLAN_LOTS)
    thesis: Thesis = ""


class ManageRequest(Frozen):
    """What to do with the resting order or the open position of the packet."""

    target: ManageTarget
    ticket: int = Field(gt=0)
    op: ManageOp
    sl: Price | None = None
    tp1: Price | None = None
    tp2: Price | None = None
    tp3: Price | None = None
    sl_after_tp1: Price | None = None
    sl_after_tp2: Price | None = None
    time_limit_min: PlanMinutes | None = None
    entry: Price | None = None
    pending_expiry_min: ExpiryMinutes | None = None
    reason: Reason = ""

    @property
    def changes(self) -> dict[str, float | int]:
        """The fields a MODIFY sets (None means unchanged)."""
        return {name: getattr(self, name) for name in MODIFY_FIELDS
                if getattr(self, name) is not None}


class M15Bias(Frozen):
    direction: BiasDirection
    levels: tuple[Price, ...] = Field(default=(), max_length=MAX_BIAS_LEVELS)
    invalidation: Price | None = None
    scenario: Scenario = ""


class PacketPlan(Frozen):
    """The ladder of a resting order or an open position (0 = no level)."""

    tp1: float = Field(ge=0)
    tp2: float = Field(ge=0)
    sl_after_tp1: float = Field(ge=0)
    sl_after_tp2: float = Field(ge=0)
    step: int = Field(ge=0, le=2)
    time_limit_min: int = Field(ge=0, le=MAX_PLAN_MINUTES)


class PacketPosition(Frozen):
    """The open V6 position a management packet asks about."""

    ticket: int = Field(gt=0)
    intent_id: Annotated[str, StringConstraints(max_length=16)]
    side: Side
    lots: Price
    open_price: Price
    open_epoch: Epoch
    sl: float = Field(ge=0)
    tp: float = Field(ge=0)
    initial_sl: float = Field(ge=0)
    profit: float
    r_now: float
    mae_points: float = Field(ge=0)
    mfe_points: float = Field(ge=0)
    minutes_open: float = Field(ge=0)
    time_limit_epoch: Epoch
    plan: PacketPlan


class PacketAction(Frozen):
    """The newest management action of this session and what became of it."""

    action_id: Annotated[str, StringConstraints(pattern=r"^[a-z2-7]{12}$")]
    op: Annotated[str, StringConstraints(pattern=r"^[A-Z_]{1,24}$")]
    ticket: int = Field(ge=0)
    status: ActionStatus
    detail: ActionDetail
    at_epoch: Epoch


def _advancing(sign: int, levels: list[float]) -> bool:
    return all(sign * (later - earlier) > 0 for earlier, later in zip(levels, levels[1:]))


def ladder_problems(side: str, entry: float | None, sl: float, tp1: float, tp2: float,
                    tp3: float, sl_after_tp1: float | None,
                    sl_after_tp2: float | None) -> list[str]:
    """The ordering every plan keeps whatever the market does: sl, entry, tp1, tp2, tp3 in
    the trade's direction, and each SL+ step between the stop before it and its trigger."""
    sign = 1 if side == "buy" else -1
    levels = [sl] + ([] if entry is None else [entry]) + [tp1, tp2, tp3]
    problems = [] if _advancing(sign, levels) else [
        "LADDER_ORDER: levels must advance in the trade direction: sl, entry, tp1, tp2, tp3"]
    if sl_after_tp1 is not None and not _advancing(sign, [sl, sl_after_tp1, tp1]):
        problems.append("SL_STEP_INVALID: sl_after_tp1 must lie between sl and tp1")
    if sl_after_tp2 is not None:
        floor = sl if sl_after_tp1 is None else sl_after_tp1
        if not (sign * (sl_after_tp2 - floor) >= 0 and _advancing(sign, [sl, sl_after_tp2, tp2])):
            problems.append("SL_STEP_INVALID: sl_after_tp2 must lie between the stop before "
                            "it and tp2")
    return problems
```

- [ ] **Step 4: Jalankan tes**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_operator_plan_models.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add adapter/app/v6/schemas/operator_plan.py adapter/tests/v6/test_operator_plan_models.py
git commit -m "feat: add V6 decision v3 plan, manage and bias models"
```

---

## Task 4: Aturan rencana terhadap `limits` paket

**Files:**
- Modify: `adapter/app/v6/deliberation/agent_entry.py` (`_stop_problems` → `stop_problems`,
  `_target_problems` → `target_problems`; tambah `modify_distance`,
  `EntryLimits.modify_distance`, `buy_stop_min`, `sell_stop_max`)
- Modify: `adapter/app/v6/types.py` (tambah `TradePlan`)
- Create: `adapter/app/v6/deliberation/plan_rules.py`
- Test: `adapter/tests/v6/test_plan_rules.py`

**Interfaces:**
- Consumes: `EntryPlanV2`, `ladder_problems` (Task 3); `limits.MIN_TP1_R`,
  `limits.MODIFY_BUFFER_PRICE`, `V6Settings.time_limit_bounds_s`,
  `V6Settings.pending_expiry_bounds_s` (Task 1)
- Produces:
  - `agent_entry.modify_distance(spec: SymbolSpec, spread_price: float) -> float`
  - `EntryLimits.modify_distance: float = 0.0`, `EntryLimits.buy_stop_min`,
    `EntryLimits.sell_stop_max`
  - `types.TradePlan(order_type, tp1, tp2, sl_after_tp1, sl_after_tp2, time_limit_s,
    pending_expiry_s)` (0 berarti tidak ada)
  - `plan_rules.PlanBounds(entry, min_tp1_r, time_limit_min, time_limit_max,
    pending_expiry_min, pending_expiry_max, volume_min, lots_step, max_lots)`
  - `plan_rules.plan_bounds(context, settings, remaining_loss_usd) -> PlanBounds`
  - `plan_rules.plan_problems_v2(plan, bounds) -> tuple[EntryProblem, ...]`
  - `plan_rules.lots_problem(lots, bounds) -> str | None`
  - `plan_rules.plan_candidate(plan, bounds, bar_t) -> Candidate`
  - `plan_rules.trade_plan(plan) -> TradePlan`
  - `plan_rules.ladder_after_exit(plan, exit_tp) -> str | None`
  - Kode: `PROBLEM_SHAPE = "PLAN_SHAPE"`, `PROBLEM_STOP_NOT_BEYOND = "STOP_NOT_BEYOND"`,
    `PROBLEM_TP1_TOO_CLOSE = "TP1_TOO_CLOSE"`, `PROBLEM_STEP_TOO_CLOSE = "SL_STEP_TOO_CLOSE"`,
    `PROBLEM_TIME_LIMIT = "TIME_LIMIT_RANGE"`, `PROBLEM_PENDING_EXPIRY = "PENDING_EXPIRY_RANGE"`,
    `PROBLEM_TP3_TRIMMED = "TP3_TRIMMED_BELOW_TP2"`

- [ ] **Step 1: Tulis tes yang gagal**

```python
"""Agent plan v2 against the packet limits."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from app.v6.deliberation.agent_entry import EntryLimits, modify_distance
from app.v6.deliberation.plan_rules import (
    PROBLEM_PENDING_EXPIRY, PROBLEM_STEP_TOO_CLOSE, PROBLEM_STOP_NOT_BEYOND,
    PROBLEM_TIME_LIMIT, PROBLEM_TP1_TOO_CLOSE, PROBLEM_TP3_TRIMMED, PlanBounds,
    ladder_after_exit, lots_problem, plan_candidate, plan_problems_v2, trade_plan,
)
from app.v6.schemas.operator_plan import EntryPlanV2
from app.v6.types import SymbolSpec

BID, ASK = 4366.61, 4366.89
LIMITS = EntryLimits(
    agent_entry_id="agent-1789650900", tick_size=0.01, digits=2, bid=BID, ask=ASK,
    max_entry_distance=18.44, stop_floor=6.0, max_stop_distance=7.16, min_reward_r=1.0,
    max_reward_r=5.0, default_reward_r=2.0, risk_budget_usd=9.12, modify_distance=0.38)
BOUNDS = PlanBounds(entry=LIMITS, min_tp1_r=0.5, time_limit_min=60, time_limit_max=240,
                    pending_expiry_min=15, pending_expiry_max=60, volume_min=0.01,
                    lots_step=0.01, max_lots=0.03)


def plan(**changes: Any) -> EntryPlanV2:
    document: dict[str, Any] = {
        "side": "buy", "order_type": "LIMIT", "entry": 4360.5, "sl": 4353.5,
        "tp1": 4366.0, "tp2": 4371.0, "tp3": 4374.5, "sl_after_tp1": 4361.0,
        "sl_after_tp2": 4366.0, "time_limit_min": 150, "pending_expiry_min": 30,
        "lots": 0.01}
    return EntryPlanV2.model_validate({**document, **changes})


def codes(found) -> list[str]:
    return [item.code for item in found]


def test_a_valid_limit_plan() -> None:
    assert plan_problems_v2(plan(), BOUNDS) == ()


def test_stop_orders_must_sit_beyond_the_quote() -> None:
    assert LIMITS.buy_stop_min == round(ASK + 0.38, 2)
    assert LIMITS.sell_stop_max == round(BID - 0.38, 2)
    good = plan(order_type="STOP", entry=4370.0, sl=4363.5, tp1=4374.0, tp2=4378.0,
                tp3=4384.0, sl_after_tp1=4370.5, sl_after_tp2=4374.0)
    assert plan_problems_v2(good, BOUNDS) == ()
    near = plan(order_type="STOP", entry=4367.0, sl=4360.5, tp1=4371.0, tp2=4375.0,
                tp3=4381.0, sl_after_tp1=None, sl_after_tp2=None)
    assert PROBLEM_STOP_NOT_BEYOND in codes(plan_problems_v2(near, BOUNDS))


def test_a_market_plan_is_judged_from_the_quote() -> None:
    market = plan(order_type="MARKET", entry=None, pending_expiry_min=None, sl=4359.9,
                  tp1=4370.5, tp2=4374.0, tp3=4380.0, sl_after_tp1=None, sl_after_tp2=None)
    assert plan_problems_v2(market, BOUNDS) == ()


@pytest.mark.parametrize(("changes", "code"), [
    ({"order_type": "MARKET"}, "PLAN_SHAPE"),
    ({"pending_expiry_min": None}, "PLAN_SHAPE"),
    ({"tp1": 4363.0}, PROBLEM_TP1_TOO_CLOSE),
    ({"sl_after_tp1": 4365.8}, PROBLEM_STEP_TOO_CLOSE),
    ({"sl_after_tp2": 4370.8}, PROBLEM_STEP_TOO_CLOSE),
    ({"time_limit_min": 45}, PROBLEM_TIME_LIMIT),
    ({"pending_expiry_min": 10}, PROBLEM_PENDING_EXPIRY),
    ({"tp2": 4365.0}, "LADDER_ORDER"),
    ({"sl": 4345.0}, "STOP_TOO_WIDE"),
    ({"sl": 4356.5}, "STOP_TOO_TIGHT"),
    ({"tp3": 4400.0}, "REWARD_TOO_LARGE"),
    ({"entry": 4367.0}, "LIMIT_NOT_PASSIVE"),
])
def test_plan_problems(changes: dict[str, Any], code: str) -> None:
    assert code in codes(plan_problems_v2(plan(**changes), BOUNDS))


@pytest.mark.parametrize(("lots", "ok"), [(0.01, True), (0.03, True), (0.04, False),
                                          (0.015, False), (0.005, False)])
def test_lots(lots: float, ok: bool) -> None:
    assert (lots_problem(lots, BOUNDS) is None) is ok


def test_candidate_and_trade_plan() -> None:
    chosen = plan()
    candidate = plan_candidate(chosen, BOUNDS, bar_t=1_789_650_000)
    assert (candidate.entry, candidate.invalidation) == (4360.5, 4353.5)
    assert candidate.features["reward_r"] == pytest.approx(2.0)
    assert candidate.features["stop_order"] == 0.0
    traded = trade_plan(chosen)
    assert (traded.order_type, traded.tp1, traded.sl_after_tp2) == ("LIMIT", 4366.0, 4366.0)
    assert (traded.time_limit_s, traded.pending_expiry_s) == (9000, 1800)
    assert trade_plan(chosen.model_copy(update={"sl_after_tp1": None})).sl_after_tp1 == 0.0


def test_a_trimmed_target_must_stay_beyond_tp2() -> None:
    assert ladder_after_exit(plan(), 4374.4) is None
    refusal = ladder_after_exit(plan(), 4370.9)
    assert refusal is not None and refusal.startswith(PROBLEM_TP3_TRIMMED)


def test_modify_distance() -> None:
    spec = SymbolSpec(digits=2, point=0.01, tick_size=0.01, tick_value=1.0,
                      tick_value_loss=1.0, contract_size=100.0, volume_min=0.01,
                      volume_step=0.01, volume_max=50.0, stops_level=1, freeze_level=0)
    assert modify_distance(spec, 0.28) == pytest.approx(0.39)


def test_limits_keep_their_old_shape() -> None:
    legacy = replace(LIMITS, modify_distance=0.0)
    assert legacy.buy_stop_min == round(ASK, 2)
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_plan_rules.py`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Ubah `agent_entry.py` dan `types.py`**

- Ganti nama `_stop_problems` → `stop_problems` dan `_target_problems` → `target_problems`,
  termasuk pemanggilan di `plan_problems`.
- Tambah field terakhir di `EntryLimits`: `modify_distance: float = 0.0`, dan:

```python
    @property
    def buy_stop_min(self) -> float:
        """The lowest BUY STOP price: the ask plus the modify distance, rounded up a tick."""
        return round(math.ceil((self.ask + self.modify_distance) / self.tick_size - 1e-9)
                     * self.tick_size, self.digits)

    @property
    def sell_stop_max(self) -> float:
        """The highest SELL STOP price: the bid minus the modify distance, rounded down."""
        return _floor_to_tick(self.bid - self.modify_distance, self.tick_size, self.digits)
```

- Fungsi baru di bawah `fundable_stop`:

```python
def modify_distance(spec: SymbolSpec, spread_price: float) -> float:
    """d_min: how far a stop, a target or a STOP entry stays from the price
    (max(stops_level, freeze_level) x point + spread + MODIFY_BUFFER_PRICE)."""
    levels = max(spec.stops_level, spec.freeze_level) * spec.point
    return round(levels + spread_price + limits.MODIFY_BUFFER_PRICE, spec.digits + 2)
```

- `entry_limits(...)` mengisi `modify_distance=modify_distance(spec, spread)`.
- `limits_from_packet(packet)` mengisi `modify_distance=block.modify_distance`; field paket
  itu baru ada di Task 8, jadi di Task 4 tulis
  `modify_distance=getattr(block, "modify_distance", 0.0)` dan ganti di Task 8.

Di `types.py`:

```python
@dataclass(frozen=True)
class TradePlan:
    """What an agent plan adds to an exit plan (0 = no level / no expiry)."""

    order_type: str            # MARKET | LIMIT | STOP
    tp1: float
    tp2: float
    sl_after_tp1: float
    sl_after_tp2: float
    time_limit_s: int
    pending_expiry_s: int
```

- [ ] **Step 4: Tulis `plan_rules.py`**

```python
"""
An agent plan v2 judged against the packet limits (spec section 2.2).

Checked in the order an agent can fix it: shape, entry (LIMIT passive, STOP beyond the
quote by d_min, both within the entry distance), stop (floor, funding), target (1-5R),
ladder (ordering, TP1 >= 0.5R, SL+ steps d_min before their trigger), holding time and
pending expiry. A plan that passes becomes a Candidate (setup "agent") and a TradePlan;
the exit plan, the sizer and the intent builder keep the last word.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from ..config import V6Settings
from ..cycle_codes import AGENT_SETUP
from ..cycle_types import MarketContext
from ..risk import limits
from ..schemas.operator_parts import AgentEntryPlan
from ..schemas.operator_plan import EntryPlanV2, ladder_problems
from ..types import Candidate, TradePlan
from .agent_entry import (
    AGENT_REASON_CODE, EntryLimits, EntryProblem, entry_limits, resolved_entry, snap,
    stop_problems, target_problems,
)

PROBLEM_SHAPE: Final[str] = "PLAN_SHAPE"
PROBLEM_STOP_NOT_BEYOND: Final[str] = "STOP_NOT_BEYOND"
PROBLEM_TP1_TOO_CLOSE: Final[str] = "TP1_TOO_CLOSE"
PROBLEM_STEP_TOO_CLOSE: Final[str] = "SL_STEP_TOO_CLOSE"
PROBLEM_TIME_LIMIT: Final[str] = "TIME_LIMIT_RANGE"
PROBLEM_PENDING_EXPIRY: Final[str] = "PENDING_EXPIRY_RANGE"
PROBLEM_TP3_TRIMMED: Final[str] = "TP3_TRIMMED_BELOW_TP2"
PROBLEM_LIMIT_NOT_PASSIVE: Final[str] = "LIMIT_NOT_PASSIVE"
PROBLEM_ENTRY_TOO_FAR: Final[str] = "ENTRY_TOO_FAR"
SECONDS_PER_MINUTE: Final[int] = 60
LOTS_EPSILON: Final[float] = 1e-9
PRICE_EPSILON: Final[float] = 1e-9


@dataclass(frozen=True)
class PlanBounds:
    entry: EntryLimits
    min_tp1_r: float
    time_limit_min: int
    time_limit_max: int
    pending_expiry_min: int
    pending_expiry_max: int
    volume_min: float
    lots_step: float
    max_lots: float


def plan_bounds(context: MarketContext, settings: V6Settings,
                remaining_loss_usd: float) -> PlanBounds:
    low, high = settings.time_limit_bounds_s
    expiry_low, expiry_high = settings.pending_expiry_bounds_s
    return PlanBounds(
        entry=entry_limits(context, settings, remaining_loss_usd),
        min_tp1_r=limits.MIN_TP1_R, time_limit_min=low // SECONDS_PER_MINUTE,
        time_limit_max=high // SECONDS_PER_MINUTE,
        pending_expiry_min=expiry_low // SECONDS_PER_MINUTE,
        pending_expiry_max=expiry_high // SECONDS_PER_MINUTE,
        volume_min=context.spec.volume_min, lots_step=context.spec.volume_step,
        max_lots=min(settings.max_lots, limits.MAX_EXECUTE_LOTS))


def _legacy(plan: EntryPlanV2) -> AgentEntryPlan:
    """The plan in the shape agent_entry's stop and target checks read."""
    return AgentEntryPlan(side=plan.side,
                          order_type="MARKET" if plan.entry is None else "LIMIT",
                          entry=plan.entry, stop=plan.sl, target=plan.tp3)


def _shape_problems(plan: EntryPlanV2) -> list[EntryProblem]:
    market = plan.order_type == "MARKET"
    problems = []
    if market != (plan.entry is None):
        problems.append(EntryProblem(PROBLEM_SHAPE,
                                     "MARKET leaves entry null; LIMIT and STOP set it"))
    if market != (plan.pending_expiry_min is None):
        problems.append(EntryProblem(PROBLEM_SHAPE,
                                     "pending_expiry_min is set exactly for LIMIT and STOP"))
    return problems


def _entry_problems(plan: EntryPlanV2, bounds: EntryLimits) -> list[EntryProblem]:
    if plan.entry is None:
        return []
    entry = snap(plan.entry, bounds.tick_size, bounds.digits)
    buy = plan.side == "buy"
    reference = bounds.ask if buy else bounds.bid
    problems = []
    if plan.order_type == "LIMIT" and not (entry <= bounds.buy_limit_max if buy
                                           else entry >= bounds.sell_limit_min):
        edge = bounds.buy_limit_max if buy else bounds.sell_limit_min
        problems.append(EntryProblem(PROBLEM_LIMIT_NOT_PASSIVE,
                                     f"a {plan.side} LIMIT must rest beyond {edge}"))
    if plan.order_type == "STOP" and not (entry >= bounds.buy_stop_min if buy
                                          else entry <= bounds.sell_stop_max):
        edge = bounds.buy_stop_min if buy else bounds.sell_stop_max
        problems.append(EntryProblem(PROBLEM_STOP_NOT_BEYOND,
                                     f"a {plan.side} STOP must sit beyond {edge}"))
    if abs(entry - reference) > bounds.max_entry_distance + PRICE_EPSILON:
        problems.append(EntryProblem(PROBLEM_ENTRY_TOO_FAR,
                                     f"entry is more than {bounds.max_entry_distance} "
                                     f"from the quote {reference}"))
    return problems


def _step_problems(plan: EntryPlanV2, sign: int, distance: float) -> list[EntryProblem]:
    steps = ((plan.sl_after_tp1, plan.tp1, "sl_after_tp1"),
             (plan.sl_after_tp2, plan.tp2, "sl_after_tp2"))
    return [EntryProblem(PROBLEM_STEP_TOO_CLOSE,
                         f"{name} must stay {distance} before its trigger")
            for level, trigger, name in steps
            if level is not None and sign * (trigger - level) + PRICE_EPSILON < distance]


def _ladder_problems(plan: EntryPlanV2, bounds: PlanBounds) -> list[EntryProblem]:
    limits_ = bounds.entry
    entry = resolved_entry(_legacy(plan), limits_)
    found = ladder_problems(plan.side, entry, plan.sl, plan.tp1, plan.tp2, plan.tp3,
                            plan.sl_after_tp1, plan.sl_after_tp2)
    if found:
        return [EntryProblem(*text.split(": ", 1)) for text in found]
    sign = 1 if plan.side == "buy" else -1
    problems = _step_problems(plan, sign, limits_.modify_distance)
    if sign * (plan.tp1 - entry) + PRICE_EPSILON < bounds.min_tp1_r * sign * (entry - plan.sl):
        problems.insert(0, EntryProblem(PROBLEM_TP1_TOO_CLOSE,
                                        f"tp1 must be at least {bounds.min_tp1_r}R from entry"))
    return problems


def _time_problems(plan: EntryPlanV2, bounds: PlanBounds) -> list[EntryProblem]:
    problems = []
    if not bounds.time_limit_min <= plan.time_limit_min <= bounds.time_limit_max:
        problems.append(EntryProblem(PROBLEM_TIME_LIMIT,
                                     f"time_limit_min must be {bounds.time_limit_min}-"
                                     f"{bounds.time_limit_max}"))
    expiry = plan.pending_expiry_min
    if expiry is not None and not bounds.pending_expiry_min <= expiry <= bounds.pending_expiry_max:
        problems.append(EntryProblem(PROBLEM_PENDING_EXPIRY,
                                     f"pending_expiry_min must be {bounds.pending_expiry_min}-"
                                     f"{bounds.pending_expiry_max}"))
    return problems


def plan_problems_v2(plan: EntryPlanV2, bounds: PlanBounds) -> tuple[EntryProblem, ...]:
    """Every reason the plan cannot be sent as it stands (empty when it fits)."""
    shape = _shape_problems(plan)
    if shape:
        return tuple(shape)
    legacy = _legacy(plan)
    stop = stop_problems(legacy, bounds.entry)
    target = [] if stop else target_problems(legacy, bounds.entry)
    ladder = [] if stop or target else _ladder_problems(plan, bounds)
    return tuple(_entry_problems(plan, bounds.entry) + stop + target + ladder
                 + _time_problems(plan, bounds))


def lots_problem(lots: float, bounds: PlanBounds) -> str | None:
    steps = round(lots / bounds.lots_step)
    on_step = abs(steps * bounds.lots_step - lots) <= LOTS_EPSILON
    within = bounds.volume_min - LOTS_EPSILON <= lots <= bounds.max_lots + LOTS_EPSILON
    if on_step and within:
        return None
    return (f"lots must be a multiple of {bounds.lots_step} between {bounds.volume_min} "
            f"and {bounds.max_lots}")


def _reward_r(plan: EntryPlanV2, bounds: EntryLimits) -> float:
    entry = resolved_entry(_legacy(plan), bounds)
    risk = abs(entry - plan.sl)
    return 0.0 if risk <= 0 else abs(plan.tp3 - entry) / risk


def plan_candidate(plan: EntryPlanV2, bounds: PlanBounds, bar_t: int) -> Candidate:
    """The plan as a detector-shaped candidate (only after `plan_problems_v2` is empty)."""
    limits_ = bounds.entry
    return Candidate(
        candidate_id=limits_.agent_entry_id, setup=AGENT_SETUP, side=plan.side,
        entry=resolved_entry(_legacy(plan), limits_),
        invalidation=snap(plan.sl, limits_.tick_size, limits_.digits), bar_t=bar_t,
        features={"reward_r": round(_reward_r(plan, limits_), 4),
                  "market": 1.0 if plan.order_type == "MARKET" else 0.0,
                  "stop_order": 1.0 if plan.order_type == "STOP" else 0.0},
        reason_codes=(AGENT_REASON_CODE,))


def trade_plan(plan: EntryPlanV2) -> TradePlan:
    expiry = plan.pending_expiry_min or 0
    return TradePlan(
        order_type=plan.order_type, tp1=plan.tp1, tp2=plan.tp2,
        sl_after_tp1=plan.sl_after_tp1 or 0.0, sl_after_tp2=plan.sl_after_tp2 or 0.0,
        time_limit_s=plan.time_limit_min * SECONDS_PER_MINUTE,
        pending_expiry_s=expiry * SECONDS_PER_MINUTE)


def ladder_after_exit(plan: EntryPlanV2, exit_tp: float) -> str | None:
    """The exit plan may pull TP3 in front of a round level; it must stay beyond TP2."""
    sign = Decimal(1 if plan.side == "buy" else -1)
    if sign * (Decimal(repr(exit_tp)) - Decimal(repr(plan.tp2))) > 0:
        return None
    return f"{PROBLEM_TP3_TRIMMED}: the exit plan pulled tp3 to {exit_tp}, not beyond tp2"
```

- [ ] **Step 5: Jalankan tes plan dan tes agent entry lama**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_plan_rules.py tests/v6 -k "plan or agent_entry or entry_plan"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add adapter/app/v6/deliberation/agent_entry.py adapter/app/v6/deliberation/plan_rules.py adapter/app/v6/types.py adapter/tests/v6/test_plan_rules.py
git commit -m "feat: judge V6 agent plans with a TP ladder and SL+ steps"
```

---

## Task 5: Kontrak intent v2 dan laporan aksi

**Files:**
- Modify: `adapter/app/v6/schemas/intent.py`
- Modify: `adapter/app/v6/wire.py` (`CANONICAL_FIELDS`, `intent_canonical`)
- Test: `adapter/tests/v6/test_intent_v2_wire.py` (baru); perbarui tes yang menuliskan
  `"v6.intent.1"` secara harfiah (`grep -rn "v6.intent.1" tests/`)

**Interfaces:**
- Produces:
  - `INTENT_SCHEMA = "v6.intent.2"`, `ACTION_SCHEMA = "v6.action.1"`
  - `Command` bertambah `"CLOSE_POSITION"`, `"MODIFY_POSITION"`, `"MODIFY_PENDING"`;
    `ACTION_COMMANDS: frozenset[str]`
  - `OrderType` bertambah `"BUY_STOP"`, `"SELL_STOP"`; `STOP_ORDER_TYPES`,
    `PENDING_ORDER_TYPES`
  - `PollResponse` field baru (default nol): `tp1`, `tp2`, `sl_after_tp1`, `sl_after_tp2`,
    `action_id`, `action_ticket`, `action_sl`, `action_tp`, `action_tp1`, `action_tp2`,
    `action_sl1`, `action_sl2`, `action_price`, `action_expiry_epoch`, `action_barrier_s`,
    `action_issued_epoch`
  - `order_problems(..., tp1=0.0, tp2=0.0, sl_after_tp1=0.0, sl_after_tp2=0.0)`
  - `ActionReport` (`schema_version="v6.action.1"`, `kind`, `action_id`, `command`,
    `intent_id`, `ticket`, `reason_code`, `retcode`, `step`, `old_sl`, `new_sl`, `price`,
    `sent_at_epoch`), `ActionKind`, `ActionReason`
  - `wire.CANONICAL_FIELDS` dengan 16 field tambahan di akhir

Arti field aksi di balasan poll:
- `CLOSE_POSITION`: hanya `action_id`, `action_ticket`, `action_issued_epoch`.
- `MODIFY_POSITION`: nilai lengkap setelah perubahan: `action_sl` dan `action_tp` (> 0),
  `action_tp1`/`action_tp2`/`action_sl1`/`action_sl2` (0 = tidak ada level),
  `action_barrier_s` (total batas waktu sejak posisi dibuka, > 0).
- `MODIFY_PENDING`: nilai lengkap order baru: `action_price`, `action_sl`, `action_tp`,
  `action_expiry_epoch`, `action_barrier_s` (> 0) dan level tangga.

- [ ] **Step 1: Tulis tes yang gagal**

```python
"""Intent v2: STOP orders, the TP ladder and signed management commands."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from app.v6 import wire
from app.v6.schemas.intent import (
    ACTION_COMMANDS, ActionReport, PollResponse, order_problems,
)

KEY = SecretStr("ea-hmac-key-" + "e" * 40)
NOW = 1_789_650_950


def intent(**changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = dict(
        server_time_epoch=NOW, command="NONE", has_intent=True, intent_id="k7w2m4pq3xza",
        source="operator", side="buy", order_type="BUY_STOP", entry=4370.0, sl=4363.5,
        tp=4384.0, lots=0.01, ref_price=4366.89, max_drift_points=130,
        max_spread_points=50, valid_until_epoch=NOW + 100, pending_expiry_epoch=NOW + 1800,
        time_barrier_s=9000, magic=250570, tp1=4374.0, tp2=4378.0, sl_after_tp1=4370.5,
        sl_after_tp2=4374.0)
    return {**document, **changes}


def action(**changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = dict(
        server_time_epoch=NOW, command="MODIFY_POSITION", action_id="m3a7q2z5k6pw",
        action_ticket=91, action_sl=4361.0, action_tp=4378.0, action_tp1=0.0,
        action_tp2=4371.0, action_sl1=0.0, action_sl2=4366.0, action_barrier_s=10800,
        action_issued_epoch=NOW)
    return {**document, **changes}


def test_a_stop_intent_with_a_ladder_is_valid() -> None:
    response = PollResponse(**intent())
    assert response.schema_version == "v6.intent.2"
    assert response.tp1 == 4374.0


@pytest.mark.parametrize("changes", [
    {"tp1": 4385.0}, {"tp2": 4373.0}, {"sl_after_tp1": 4375.0},
    {"sl_after_tp2": 4369.0}, {"tp1": 0.0}, {"pending_expiry_epoch": 0},
    {"order_type": "SELL_STOP"},
])
def test_ladder_and_pending_rules(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PollResponse(**intent(**changes))


def test_a_ladder_is_optional() -> None:
    plain = intent(tp1=0.0, tp2=0.0, sl_after_tp1=0.0, sl_after_tp2=0.0,
                   order_type="BUY_LIMIT", entry=4360.5, sl=4353.5, tp=4374.5)
    assert PollResponse(**plain).tp1 == 0.0


def test_order_problems_accept_the_ladder_keywords() -> None:
    assert order_problems(side="sell", order_type="SELL_STOP", entry=4350.0, sl=4357.0,
                          tp=4336.0, lots=0.01, valid_until_epoch=100,
                          pending_expiry_epoch=1000, time_barrier_s=3600, tp1=4346.0,
                          tp2=4341.0, sl_after_tp1=4350.5, sl_after_tp2=0.0) == []


def test_management_commands() -> None:
    assert ACTION_COMMANDS == {"CLOSE_POSITION", "MODIFY_POSITION", "MODIFY_PENDING"}
    assert PollResponse(**action()).action_ticket == 91
    close = dict(server_time_epoch=NOW, command="CLOSE_POSITION",
                 action_id="m3a7q2z5k6pw", action_ticket=91, action_issued_epoch=NOW)
    assert PollResponse(**close).command == "CLOSE_POSITION"
    pending = action(command="MODIFY_PENDING", action_price=4359.0,
                     action_expiry_epoch=NOW + 900)
    assert PollResponse(**pending).action_price == 4359.0


@pytest.mark.parametrize("document", [
    action(action_id=""),
    action(action_ticket=0),
    action(action_sl=0.0),
    action(action_barrier_s=0),
    action(action_price=4359.0),
    action(command="MODIFY_PENDING", action_price=0.0, action_expiry_epoch=NOW + 900),
    dict(server_time_epoch=NOW, command="CLOSE_POSITION", action_id="m3a7q2z5k6pw",
         action_ticket=91, action_issued_epoch=NOW, action_sl=4361.0),
    dict(server_time_epoch=NOW, command="NONE", action_id="m3a7q2z5k6pw"),
    {**intent(), "command": "MODIFY_POSITION", "action_id": "m3a7q2z5k6pw",
     "action_ticket": 91, "action_issued_epoch": NOW},
])
def test_bad_management_commands(document: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PollResponse(**document)


def test_the_canonical_string_covers_the_new_fields() -> None:
    response = PollResponse(**intent())
    text = wire.intent_canonical(response, 0.01)
    assert text.startswith("v6.intent.2|")
    assert text.endswith("|437400|437800|437050|437400||0|0|0|0|0|0|0|0|0|0|0")
    assert len(text.split("|")) == len(wire.CANONICAL_FIELDS) == 36
    signed = wire.sign_intent(KEY, response, 0.01)
    assert wire.verify_intent(KEY, signed, 0.01)
    moved = signed.model_copy(update={"tp1": 4375.0})
    assert not wire.verify_intent(KEY, moved, 0.01)
    command = wire.sign_intent(KEY, PollResponse(**action()), 0.01)
    assert wire.intent_canonical(command, 0.01).endswith(
        "|m3a7q2z5k6pw|91|436100|437800|0|437100|0|436600|0|0|10800|1789650950")
    assert wire.verify_intent(KEY, command, 0.01)


def report(**changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = dict(
        schema_version="v6.action.1", kind="APPLIED", action_id="m3a7q2z5k6pw",
        command="MODIFY_POSITION", intent_id="k7w2m4pq3xza", ticket=91, reason_code="NONE",
        retcode=10009, step=0, old_sl=4353.5, new_sl=4361.0, price=4366.2,
        sent_at_epoch=NOW)
    return {**document, **changes}


def test_action_reports() -> None:
    assert ActionReport(**report()).kind == "APPLIED"
    step = report(kind="PLAN_STEP", action_id="", command="NONE", step=1)
    assert ActionReport(**step).step == 1
    rejected = report(kind="REJECTED", reason_code="SL_WIDER", retcode=0)
    assert ActionReport(**rejected).reason_code == "SL_WIDER"


@pytest.mark.parametrize("changes", [
    {"kind": "APPLIED", "reason_code": "STALE"},
    {"kind": "REJECTED", "reason_code": "NONE"},
    {"kind": "PLAN_STEP", "action_id": "", "command": "NONE", "step": 0},
    {"kind": "PLAN_STEP", "step": 1},
    {"kind": "APPLIED", "action_id": ""},
    {"command": "FLATTEN"},
])
def test_bad_action_reports(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ActionReport(**report(**changes))
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_intent_v2_wire.py`
Expected: FAIL (`ImportError: cannot import name 'ACTION_COMMANDS'`).

- [ ] **Step 3: Implementasi di `schemas/intent.py`**

Konstanta dan tipe:

```python
INTENT_SCHEMA: Final[str] = "v6.intent.2"
ACTION_SCHEMA: Final[str] = "v6.action.1"
ACTION_ID_PATTERN: Final[str] = INTENT_ID_PATTERN
OptionalActionId = Annotated[str, StringConstraints(pattern=r"^([a-z2-7]{12})?$")]
Command = Literal["NONE", "FLATTEN", "CANCEL_PENDING", "CLOSE_POSITION", "MODIFY_POSITION",
                  "MODIFY_PENDING"]
ACTION_COMMANDS: Final[frozenset[str]] = frozenset(
    {"CLOSE_POSITION", "MODIFY_POSITION", "MODIFY_PENDING"})
OrderType = Literal["BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP", "BUY", "SELL", "NONE"]
LIMIT_ORDER_TYPES: Final[frozenset[str]] = frozenset({"BUY_LIMIT", "SELL_LIMIT"})
STOP_ORDER_TYPES: Final[frozenset[str]] = frozenset({"BUY_STOP", "SELL_STOP"})
PENDING_ORDER_TYPES: Final[frozenset[str]] = LIMIT_ORDER_TYPES | STOP_ORDER_TYPES
ORDER_TYPES_BY_SIDE: Final[Mapping[str, frozenset[str]]] = MappingProxyType({
    "buy": frozenset({"BUY_LIMIT", "BUY_STOP", "BUY"}),
    "sell": frozenset({"SELL_LIMIT", "SELL_STOP", "SELL"})})
INTENT_FIELDS: Final[tuple[str, ...]] = (
    "intent_id", "source", "side", "order_type", "entry", "sl", "tp", "lots", "ref_price",
    "max_drift_points", "max_spread_points", "valid_until_epoch", "pending_expiry_epoch",
    "time_barrier_s", "magic", "tp1", "tp2", "sl_after_tp1", "sl_after_tp2",
)
ACTION_FIELDS: Final[tuple[str, ...]] = (
    "action_id", "action_ticket", "action_sl", "action_tp", "action_tp1", "action_tp2",
    "action_sl1", "action_sl2", "action_price", "action_expiry_epoch", "action_barrier_s",
    "action_issued_epoch",
)
_ACTION_PRICE_FIELDS: Final[tuple[str, ...]] = (
    "action_sl", "action_tp", "action_tp1", "action_tp2", "action_sl1", "action_sl2",
    "action_price")
```

Field baru di `PollResponse` (setelah `magic`, sebelum `sig`), dan nilai default
`schema_version` menjadi `"v6.intent.2"` dengan `Literal["v6.intent.2"]`:

```python
    tp1: float = Field(default=0.0, ge=0.0)
    tp2: float = Field(default=0.0, ge=0.0)
    sl_after_tp1: float = Field(default=0.0, ge=0.0)
    sl_after_tp2: float = Field(default=0.0, ge=0.0)
    action_id: OptionalActionId = ""
    action_ticket: int = Field(default=0, ge=0)
    action_sl: float = Field(default=0.0, ge=0.0)
    action_tp: float = Field(default=0.0, ge=0.0)
    action_tp1: float = Field(default=0.0, ge=0.0)
    action_tp2: float = Field(default=0.0, ge=0.0)
    action_sl1: float = Field(default=0.0, ge=0.0)
    action_sl2: float = Field(default=0.0, ge=0.0)
    action_price: float = Field(default=0.0, ge=0.0)
    action_expiry_epoch: int = Field(default=0, ge=0)
    action_barrier_s: int = Field(default=0, ge=0, le=limits.MAX_TIME_BARRIER_S)
    action_issued_epoch: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> "PollResponse":
        problems = (_intent_problems(self) if self.has_intent
                    else _leftover_fields(self, INTENT_FIELDS, "has_intent=false"))
        problems += _action_problems(self)
        if problems:
            raise ValueError("; ".join(problems))
        return self
```

Helper baru dan perubahan helper lama:

```python
def _leftover_fields(response: PollResponse, names: tuple[str, ...],
                     rule: str) -> list[str]:
    defaults = PollResponse.model_fields
    leftover = [name for name in names if getattr(response, name) != defaults[name].default]
    return [f"{rule} must leave {', '.join(leftover)} empty"] if leftover else []


def _action_problems(r: PollResponse) -> list[str]:
    if r.command not in ACTION_COMMANDS:
        return _leftover_fields(r, ACTION_FIELDS, f"command {r.command}")
    positive = {name: getattr(r, name) > 0 for name in ACTION_FIELDS if name != "action_id"}
    wanted = {
        "CLOSE_POSITION": (),
        "MODIFY_POSITION": ("action_sl", "action_tp", "action_barrier_s"),
        "MODIFY_PENDING": ("action_sl", "action_tp", "action_barrier_s", "action_price",
                           "action_expiry_epoch"),
    }[r.command]
    allowed_zero_or_set = ("action_tp1", "action_tp2", "action_sl1", "action_sl2")
    checks = [
        (not r.has_intent, "a management command never travels with an intent"),
        (bool(r.action_id) and r.action_ticket > 0 and r.action_issued_epoch > 0,
         "a management command needs action_id, action_ticket and action_issued_epoch"),
        (all(positive[name] for name in wanted), f"{r.command} needs {', '.join(wanted)}"),
    ]
    if r.command == "CLOSE_POSITION":
        extra = [name for name in _ACTION_PRICE_FIELDS + ("action_expiry_epoch",
                                                          "action_barrier_s") if positive[name]]
        checks.append((not extra, "CLOSE_POSITION carries no levels"))
    if r.command == "MODIFY_POSITION":
        checks.append((not positive["action_price"] and not positive["action_expiry_epoch"],
                       "MODIFY_POSITION carries no order price or expiry"))
    del allowed_zero_or_set
    return [message for ok, message in checks if not ok]


def _ladder_checks(sign: int, entry: float, sl: float, tp: float, tp1: float, tp2: float,
                   s1: float, s2: float) -> list[tuple[bool, str]]:
    if tp1 == 0 and tp2 == 0 and s1 == 0 and s2 == 0:
        return []
    floor = s1 if s1 > 0 else sl
    return [
        (tp1 > 0 and tp2 > 0 and sign * (tp1 - entry) > 0 and sign * (tp2 - tp1) > 0
         and sign * (tp - tp2) > 0, "the TP ladder must advance: entry, tp1, tp2, tp"),
        (s1 == 0 or (sign * (s1 - sl) > 0 and sign * (tp1 - s1) > 0),
         "sl_after_tp1 must sit between sl and tp1"),
        (s2 == 0 or (sign * (s2 - floor) >= 0 and sign * (tp2 - s2) > 0
                     and sign * (s2 - sl) > 0),
         "sl_after_tp2 must sit between the stop before it and tp2"),
    ]
```

Catatan: hapus variabel `allowed_zero_or_set` bila linter menandainya; ia hanya
mendokumentasikan bahwa empat level tangga boleh nol.

`order_problems` menerima kata kunci baru dan order STOP:

```python
def order_problems(*, side: str, order_type: str, entry: float, sl: float, tp: float,
                   lots: float, valid_until_epoch: int, pending_expiry_epoch: int,
                   time_barrier_s: int, tp1: float = 0.0, tp2: float = 0.0,
                   sl_after_tp1: float = 0.0, sl_after_tp2: float = 0.0) -> list[str]:
    """Geometry every live intent obeys (poll response and v6_intents row alike)."""
    sign = _SIDE_SIGN.get(side, 0)
    pending = order_type in PENDING_ORDER_TYPES
    checks = [
        (order_type in ORDER_TYPES_BY_SIDE.get(side, ()), "order_type does not match side"),
        (_finite_positive(entry, sl, tp, lots), "prices and lots must be finite and positive"),
        (sign * (entry - sl) > 0 and sign * (tp - entry) > 0,
         "sl and tp must lie on their own side of entry"),
        (_within_lot_cap(lots), f"lots above the {limits.MAX_EXECUTE_LOTS} execution cap"),
        (0 < time_barrier_s <= limits.MAX_TIME_BARRIER_S, "time barrier out of range"),
        (not pending or pending_expiry_epoch >= valid_until_epoch + MIN_PENDING_LIFETIME_S,
         f"a pending order must expire at least {MIN_PENDING_LIFETIME_S} s after valid_until"),
        (pending or pending_expiry_epoch == 0, "a market order has no pending expiry"),
    ]
    checks += _ladder_checks(sign, entry, sl, tp, tp1, tp2, sl_after_tp1, sl_after_tp2)
    return [message for ok, message in checks if not ok]
```

dan `_intent_problems` meneruskan `tp1=r.tp1, tp2=r.tp2, sl_after_tp1=r.sl_after_tp1,
sl_after_tp2=r.sl_after_tp2`.

Laporan aksi (di akhir file):

```python
ActionKind = Literal["APPLIED", "REJECTED", "FAILED", "PLAN_STEP"]
ActionReason = Literal[
    "NONE", "UNKNOWN_TICKET", "STALE", "DUPLICATE", "SL_WIDER", "TOO_CLOSE", "BARRIER",
    "DEMO_REQUIRED", "HALTED", "MARKET_CLOSED", "BROKER_ERROR", "BAD_ACTION", "STEP_DONE",
]


class ActionReport(_Strict):
    """What the EA did with a management command, or a local SL+ step (`PLAN_STEP`)."""

    schema_version: Literal["v6.action.1"]
    kind: ActionKind
    action_id: OptionalActionId
    command: Command
    intent_id: OptionalIntentId
    ticket: int = Field(ge=0)
    reason_code: ActionReason
    retcode: int = Field(ge=0)
    step: int = Field(ge=0, le=2)
    old_sl: float = Field(ge=0)
    new_sl: float = Field(ge=0)
    price: float = Field(ge=0)
    sent_at_epoch: int = Field(ge=0)

    @model_validator(mode="after")
    def _kind_matches(self) -> "ActionReport":
        step = self.kind == "PLAN_STEP"
        checks = (
            (step == (self.action_id == ""), "a PLAN_STEP has no action_id; an action has one"),
            (step == (self.command == "NONE"),
             "a PLAN_STEP has command NONE; an action names its command"),
            (not step or self.step in (1, 2), "a PLAN_STEP is step 1 or 2"),
            (step or self.command in ACTION_COMMANDS, "unknown management command"),
            ((self.kind == "APPLIED" or step) == (self.reason_code == "NONE"),
             "APPLIED and PLAN_STEP carry reason NONE; REJECTED and FAILED carry a reason"),
        )
        problems = [message for ok, message in checks if not ok]
        if problems:
            raise ValueError("; ".join(problems))
        return self
```

- [ ] **Step 4: String kanonik di `wire.py`**

```python
CANONICAL_FIELDS: Final[tuple[str, ...]] = (
    "schema_version", "server_time_epoch", "command", "has_intent", "intent_id", "source",
    "require_demo", "side", "order_type", "entry_points", "sl_points", "tp_points",
    "lots_hundredths", "ref_points", "max_drift_points", "max_spread_points",
    "valid_until_epoch", "pending_expiry_epoch", "time_barrier_s", "magic",
    "tp1_points", "tp2_points", "sl_after_tp1_points", "sl_after_tp2_points",
    "action_id", "action_ticket", "action_sl_points", "action_tp_points",
    "action_tp1_points", "action_tp2_points", "action_sl1_points", "action_sl2_points",
    "action_price_points", "action_expiry_epoch", "action_barrier_s", "action_issued_epoch",
)


def intent_canonical(response: PollResponse, point: float) -> str:
    """The signed text of a poll response, fields in CANONICAL_FIELDS order."""
    r = response

    def points(value: float) -> int:
        return price_to_points(value, point)

    values = (
        r.schema_version, r.server_time_epoch, r.command, 1 if r.has_intent else 0,
        r.intent_id, r.source, r.require_demo, r.side, r.order_type,
        points(r.entry), points(r.sl), points(r.tp), lots_to_hundredths(r.lots),
        points(r.ref_price), r.max_drift_points, r.max_spread_points,
        r.valid_until_epoch, r.pending_expiry_epoch, r.time_barrier_s, r.magic,
        points(r.tp1), points(r.tp2), points(r.sl_after_tp1), points(r.sl_after_tp2),
        r.action_id, r.action_ticket, points(r.action_sl), points(r.action_tp),
        points(r.action_tp1), points(r.action_tp2), points(r.action_sl1),
        points(r.action_sl2), points(r.action_price), r.action_expiry_epoch,
        r.action_barrier_s, r.action_issued_epoch,
    )
    return CANONICAL_SEPARATOR.join(str(value) for value in values)
```

Perbarui docstring modul `wire.py` (field baru ikut ditandatangani).

- [ ] **Step 5: Jalankan tes dan perbaiki tes lama**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_intent_v2_wire.py tests/v6 -k "intent or wire or poll"`
Expected: PASS setelah literal `"v6.intent.1"` di tes lama diganti `"v6.intent.2"` dan vektor
kanonik lama diperbarui (tambahkan 16 nilai nol di akhir string yang diharapkan).

- [ ] **Step 6: Commit**

```bash
git add adapter/app/v6/schemas/intent.py adapter/app/v6/wire.py adapter/tests/v6
git commit -m "feat: sign V6 intent v2 with STOP orders, the TP ladder and management commands"
```

---

## Task 6: Kolom rencana di `v6_intents` dan tabel `v6_actions`

**Files:**
- Modify: `adapter/app/v6/ledger_intents.py` (kolom rencana, `NewIntent`/`IntentRecord`,
  `INTENT_COLUMN_MIGRATIONS`, `update_plan`, `set_plan_step`)
- Create: `adapter/app/v6/ledger_actions.py`
- Modify: `adapter/app/v6/ledger_cycles.py:144-150` (skema dan `self.actions`)
- Test: `adapter/tests/v6/test_ledger_plan_actions.py`

**Interfaces:**
- Consumes: `order_problems(..., tp1, tp2, sl_after_tp1, sl_after_tp2)` (Task 5)
- Produces:
  - `NewIntent` dan `IntentRecord` field baru (default 0): `tp1`, `tp2`, `sl_after_tp1`,
    `sl_after_tp2`; `IntentRecord.plan_step: int = 0`
  - `IntentStore.update_plan(intent_id: str, *, tp1: float, tp2: float,
    sl_after_tp1: float, sl_after_tp2: float, time_barrier_s: int) -> bool`
  - `IntentStore.set_plan_step(intent_id: str, step: int) -> bool` (hanya naik)
  - `ledger_actions.ActionRow`, `ActionStore.insert(row) -> None`,
    `ActionStore.mark(action_id, status, detail, at) -> bool`,
    `ActionStore.get(action_id) -> ActionRow | None`,
    `ActionStore.latest(session_id: str) -> ActionRow | None`,
    `ActionStore.recent(limit: int) -> tuple[ActionRow, ...]`,
    `ActionStore.record_step(intent_id, ticket, step, old_sl, new_sl, price, at) -> bool`
  - `LedgerCycles.actions: ActionStore`

- [ ] **Step 1: Tulis tes yang gagal**

```python
"""v6_intents plan columns and the v6_actions ledger."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.v6.ledger_actions import ActionRow
from app.v6.ledger_cycles import LedgerCycles
from app.v6.ledger_intents import NewIntent

T0 = 1_789_650_900.0


def new_intent(**changes) -> NewIntent:
    fields = dict(intent_id="k7w2m4pq3xza", cycle_id="c-0123456789abcdef",
                  session_id="a1b2c3d4e5f6", agent="claude_code", source="operator",
                  side="buy", order_type="BUY_LIMIT", entry=4360.5, sl=4353.5, tp=4374.5,
                  lots=0.01, risk_usd=7.4, valid_until_epoch=int(T0) + 120,
                  pending_expiry_epoch=int(T0) + 1800, time_barrier_s=9000, created_at=T0,
                  tp1=4366.0, tp2=4371.0, sl_after_tp1=4361.0, sl_after_tp2=4366.0)
    return NewIntent(**{**fields, **changes})


@pytest.fixture
def ledger(tmp_path: Path):
    store = LedgerCycles(tmp_path / "v6.sqlite")
    yield store
    store.close()


def test_plan_columns_round_trip(ledger: LedgerCycles) -> None:
    record = ledger.intents.insert(new_intent())
    assert (record.tp1, record.tp2, record.sl_after_tp1, record.sl_after_tp2,
            record.plan_step) == (4366.0, 4371.0, 4361.0, 4366.0, 0)
    assert ledger.intents.update_plan("k7w2m4pq3xza", tp1=4367.0, tp2=4372.0,
                                      sl_after_tp1=4362.0, sl_after_tp2=0.0,
                                      time_barrier_s=10800)
    assert ledger.intents.set_plan_step("k7w2m4pq3xza", 1)
    assert not ledger.intents.set_plan_step("k7w2m4pq3xza", 1)
    stored = ledger.intents.get("k7w2m4pq3xza")
    assert (stored.tp1, stored.sl_after_tp2, stored.time_barrier_s, stored.plan_step) == (
        4367.0, 0.0, 10800, 1)
    assert not ledger.intents.update_plan("zzzzzzzzzzzz", tp1=1.0, tp2=2.0,
                                          sl_after_tp1=0.0, sl_after_tp2=0.0,
                                          time_barrier_s=3600)


def test_a_broken_ladder_is_refused_at_insert() -> None:
    with pytest.raises(ValueError, match="ladder"):
        new_intent(tp1=4380.0)


def test_an_old_database_gains_the_columns(tmp_path: Path) -> None:
    import sqlite3
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE v6_intents (
        intent_id TEXT NOT NULL PRIMARY KEY, cycle_id TEXT NOT NULL,
        session_id TEXT NOT NULL, agent TEXT NOT NULL DEFAULT '', source TEXT NOT NULL,
        status TEXT NOT NULL, side TEXT NOT NULL, order_type TEXT NOT NULL,
        entry REAL NOT NULL, sl REAL NOT NULL, tp REAL NOT NULL, lots REAL NOT NULL,
        risk_usd REAL NOT NULL, valid_until_epoch INTEGER NOT NULL,
        pending_expiry_epoch INTEGER NOT NULL, time_barrier_s INTEGER NOT NULL,
        created_at REAL NOT NULL, delivered_at REAL, reported_at REAL, closed_at REAL,
        report_status TEXT, report_reason TEXT, ticket INTEGER, fill_price REAL,
        outcome_pnl REAL, basket_id TEXT)""")
    conn.commit()
    conn.close()
    store = LedgerCycles(path)
    try:
        assert store.intents.insert(new_intent()).tp1 == 4366.0
    finally:
        store.close()


def action_row(**changes) -> ActionRow:
    fields = dict(action_id="m3a7q2z5k6pw", cycle_id="c-0123456789abcdef",
                  session_id="a1b2c3d4e5f6", agent="claude_code", command="MODIFY_POSITION",
                  ticket=91, intent_id="k7w2m4pq3xza",
                  payload={"sl": 4361.0, "tp": 4378.0}, status="PUBLISHED", detail="",
                  created_at=T0, updated_at=T0)
    return ActionRow(**{**fields, **changes})


def test_actions(ledger: LedgerCycles) -> None:
    ledger.actions.insert(action_row())
    assert ledger.actions.mark("m3a7q2z5k6pw", "APPLIED", "retcode 10009", T0 + 3)
    assert not ledger.actions.mark("m3a7q2z5k6pw", "FAILED", "late", T0 + 4)
    row = ledger.actions.get("m3a7q2z5k6pw")
    assert (row.status, row.detail, row.payload["sl"]) == ("APPLIED", "retcode 10009", 4361.0)
    assert ledger.actions.latest("a1b2c3d4e5f6").action_id == "m3a7q2z5k6pw"
    assert ledger.actions.latest("ffffffffffff") is None
    assert len(ledger.actions.recent(10)) == 1
    assert ledger.actions.record_step("k7w2m4pq3xza", 91, 1, 4353.5, 4361.0, 4366.1, T0 + 9)
    assert not ledger.actions.record_step("k7w2m4pq3xza", 91, 1, 4353.5, 4361.0, 4366.1, T0)


@pytest.mark.parametrize("changes", [
    {"status": "DONE"}, {"command": "FLATTEN"}, {"ticket": 0}, {"action_id": "x"},
])
def test_bad_action_rows(changes) -> None:
    with pytest.raises(ValueError):
        action_row(**changes)
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_ledger_plan_actions.py`
Expected: FAIL (`ModuleNotFoundError: app.v6.ledger_actions`).

- [ ] **Step 3: Implementasi `ledger_intents.py`**

- Tambah di `NewIntent` (field terakhir): `tp1: float = 0.0`, `tp2: float = 0.0`,
  `sl_after_tp1: float = 0.0`, `sl_after_tp2: float = 0.0`. Di `_new_intent_problems`,
  masukkan keempatnya ke daftar angka dan teruskan ke `order_problems(...)`.
- Tambah di `IntentRecord` (field terakhir, urutan = urutan kolom setelah migrasi):
  `tp1: float = 0.0`, `tp2: float = 0.0`, `sl_after_tp1: float = 0.0`,
  `sl_after_tp2: float = 0.0`, `plan_step: int = 0`.
- Migrasi dan SQL baru:

```python
# ALTER TABLE ... ADD COLUMN appends, so these follow basket_id in IntentRecord.
INTENT_COLUMN_MIGRATIONS: Final[tuple[str, ...]] = (
    "ALTER TABLE v6_intents ADD COLUMN tp1 REAL NOT NULL DEFAULT 0",
    "ALTER TABLE v6_intents ADD COLUMN tp2 REAL NOT NULL DEFAULT 0",
    "ALTER TABLE v6_intents ADD COLUMN sl_after_tp1 REAL NOT NULL DEFAULT 0",
    "ALTER TABLE v6_intents ADD COLUMN sl_after_tp2 REAL NOT NULL DEFAULT 0",
    "ALTER TABLE v6_intents ADD COLUMN plan_step INTEGER NOT NULL DEFAULT 0",
)
_PLAN_SQL: Final[str] = (
    "UPDATE v6_intents SET tp1 = ?, tp2 = ?, sl_after_tp1 = ?, sl_after_tp2 = ?,"
    " time_barrier_s = ? WHERE intent_id = ?")
_STEP_SQL: Final[str] = (
    "UPDATE v6_intents SET plan_step = ? WHERE intent_id = ? AND plan_step < ?")
```

  `CREATE TABLE` di `INTENT_SCHEMA_DDL` tetap tanpa kolom baru; migrasi menambahkannya
  baik untuk database baru maupun lama.

- Metode baru `IntentStore`:

```python
    def update_plan(self, intent_id: str, *, tp1: float, tp2: float, sl_after_tp1: float,
                    sl_after_tp2: float, time_barrier_s: int) -> bool:
        """Store the ladder a management action changed (the EA has applied it)."""
        with self._write() as conn:
            changed = conn.execute(_PLAN_SQL, (tp1, tp2, sl_after_tp1, sl_after_tp2,
                                               time_barrier_s, intent_id)).rowcount
        return changed == 1

    def set_plan_step(self, intent_id: str, step: int) -> bool:
        """Record an SL+ step the EA executed; a step never goes back."""
        with self._write() as conn:
            changed = conn.execute(_STEP_SQL, (step, intent_id, step)).rowcount
        return changed == 1
```

  (Nama atribut tulis di `IntentStore.__init__` adalah `write`; sesuaikan `self._write`
  dengan nama sebenarnya saat mengimplementasikan.)

- Di `ledger_cycles.py`:

```python
            apply_schema(self._conn, (*CYCLE_SCHEMA_DDL, *INTENT_SCHEMA_DDL, *ACTION_SCHEMA_DDL),
                         (*CYCLE_COLUMN_MIGRATIONS, *INTENT_COLUMN_MIGRATIONS))
        ...
        self.intents = IntentStore(self._write, self._fetchall)
        self.actions = ActionStore(self._write, self._fetchall)
```

- [ ] **Step 4: Tulis `ledger_actions.py`**

```python
"""
v6_actions and v6_plan_steps: management actions sent to the EA and the SL+ steps it took.

`ActionStore` shares the connection and lock of `LedgerCycles` (`ledger_cycles.actions`).
An action is PUBLISHED when queued for the EA and moves once to APPLIED, REJECTED, FAILED
or EXPIRED. A plan step is recorded once per (ticket, step).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

from .schemas.intent import ACTION_COMMANDS, INTENT_ID_PATTERN

ACTION_STATUSES: Final[tuple[str, ...]] = ("PUBLISHED", "APPLIED", "REJECTED", "FAILED",
                                           "EXPIRED")
FINAL_ACTION_STATUSES: Final[frozenset[str]] = frozenset(ACTION_STATUSES[1:])
MAX_TEXT_CHARS: Final[int] = 120
MAX_PAYLOAD_BYTES: Final[int] = 2048
MAX_LIST_LIMIT: Final[int] = 500
_ID_RE: Final[re.Pattern[str]] = re.compile(INTENT_ID_PATTERN)
_STATUS_LIST: Final[str] = ", ".join(f"'{status}'" for status in ACTION_STATUSES)

ACTION_SCHEMA_DDL: Final[tuple[str, ...]] = (
    f"""CREATE TABLE IF NOT EXISTS v6_actions (
        action_id TEXT NOT NULL PRIMARY KEY, cycle_id TEXT NOT NULL,
        session_id TEXT NOT NULL, agent TEXT NOT NULL, command TEXT NOT NULL,
        ticket INTEGER NOT NULL CHECK (ticket > 0), intent_id TEXT NOT NULL DEFAULT '',
        payload TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ({_STATUS_LIST})),
        detail TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_v6_actions_session ON v6_actions(session_id, created_at)",
    """CREATE TABLE IF NOT EXISTS v6_plan_steps (
        ticket INTEGER NOT NULL, step INTEGER NOT NULL CHECK (step IN (1, 2)),
        intent_id TEXT NOT NULL, old_sl REAL NOT NULL, new_sl REAL NOT NULL,
        price REAL NOT NULL, at REAL NOT NULL, PRIMARY KEY (ticket, step))""",
)
_COLUMNS: Final[str] = ("action_id, cycle_id, session_id, agent, command, ticket, intent_id,"
                        " payload, status, detail, created_at, updated_at")
_INSERT_SQL: Final[str] = f"INSERT INTO v6_actions ({_COLUMNS}) VALUES ({', '.join('?' * 12)})"
_SELECT_SQL: Final[str] = f"SELECT {_COLUMNS} FROM v6_actions"
_BY_ID_SQL: Final[str] = f"{_SELECT_SQL} WHERE action_id = ?"
_LATEST_SQL: Final[str] = (f"{_SELECT_SQL} WHERE session_id = ?"
                           " ORDER BY created_at DESC, action_id LIMIT 1")
_RECENT_SQL: Final[str] = f"{_SELECT_SQL} ORDER BY created_at DESC, action_id LIMIT ?"
_MARK_SQL: Final[str] = ("UPDATE v6_actions SET status = ?, detail = ?, updated_at = ?"
                         " WHERE action_id = ? AND status = 'PUBLISHED'")
_STEP_SQL: Final[str] = ("INSERT OR IGNORE INTO v6_plan_steps"
                         " (ticket, step, intent_id, old_sl, new_sl, price, at)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?)")

WriteTx = Callable[[], AbstractContextManager]
Fetch = Callable[[str, tuple[object, ...]], list[tuple]]


def _finite(*values: object) -> bool:
    return all(isinstance(value, (int, float)) and not isinstance(value, bool)
               and math.isfinite(value) for value in values)


@dataclass(frozen=True)
class ActionRow:
    action_id: str
    cycle_id: str
    session_id: str
    agent: str
    command: str
    ticket: int
    intent_id: str
    payload: Mapping[str, object] = field(default_factory=dict)
    status: str = "PUBLISHED"
    detail: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        checks = (
            (bool(_ID_RE.match(self.action_id)), "action_id must be 12 base32 characters"),
            (self.command in ACTION_COMMANDS, "unknown command"),
            (isinstance(self.ticket, int) and self.ticket > 0, "ticket must be positive"),
            (self.status in ACTION_STATUSES, "unknown status"),
            (len(self.detail) <= MAX_TEXT_CHARS, "detail is too long"),
            (_finite(self.created_at, self.updated_at), "times must be finite"),
            (len(self.payload_json) <= MAX_PAYLOAD_BYTES, "payload is too large"),
        )
        problems = [message for ok, message in checks if not ok]
        if problems:
            raise ValueError("invalid action: " + "; ".join(problems))

    @property
    def payload_json(self) -> str:
        return json.dumps(dict(self.payload), sort_keys=True, allow_nan=False)


def _row(values: tuple) -> ActionRow:
    (action_id, cycle_id, session_id, agent, command, ticket, intent_id, payload, status,
     detail, created_at, updated_at) = values
    return ActionRow(action_id=action_id, cycle_id=cycle_id, session_id=session_id,
                     agent=agent, command=command, ticket=int(ticket), intent_id=intent_id,
                     payload=json.loads(payload), status=status, detail=detail,
                     created_at=float(created_at), updated_at=float(updated_at))


class ActionStore:
    def __init__(self, write: WriteTx, fetchall: Fetch) -> None:
        self._write = write
        self._fetchall = fetchall

    def insert(self, row: ActionRow) -> None:
        values = (row.action_id, row.cycle_id, row.session_id, row.agent, row.command,
                  row.ticket, row.intent_id, row.payload_json, row.status, row.detail,
                  row.created_at, row.updated_at)
        with self._write() as conn:
            conn.execute(_INSERT_SQL, values)

    def mark(self, action_id: str, status: str, detail: str, at: float) -> bool:
        """PUBLISHED -> a final status, once; False when the action is gone or final."""
        if status not in FINAL_ACTION_STATUSES:
            raise ValueError(f"{status} is not a final action status")
        with self._write() as conn:
            changed = conn.execute(_MARK_SQL, (status, detail[:MAX_TEXT_CHARS], at,
                                               action_id)).rowcount
        return changed == 1

    def get(self, action_id: str) -> ActionRow | None:
        rows = self._fetchall(_BY_ID_SQL, (action_id,))
        return _row(rows[0]) if rows else None

    def latest(self, session_id: str) -> ActionRow | None:
        rows = self._fetchall(_LATEST_SQL, (session_id,))
        return _row(rows[0]) if rows else None

    def recent(self, limit: int = 50) -> tuple[ActionRow, ...]:
        bounded = max(1, min(int(limit), MAX_LIST_LIMIT))
        return tuple(_row(values) for values in self._fetchall(_RECENT_SQL, (bounded,)))

    def record_step(self, intent_id: str, ticket: int, step: int, old_sl: float,
                    new_sl: float, price: float, at: float) -> bool:
        with self._write() as conn:
            changed = conn.execute(_STEP_SQL, (ticket, step, intent_id, old_sl, new_sl,
                                               price, at)).rowcount
        return changed == 1
```

- [ ] **Step 5: Jalankan tes ledger**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_ledger_plan_actions.py tests/v6 -k "ledger or intent_store"`
Expected: PASS. Kalau tes menyebut pesan "ladder" tidak ada, sesuaikan pesan pertama
`_ladder_checks` ("the TP ladder must advance ...") yang sudah memuat kata "ladder".

- [ ] **Step 6: Commit**

```bash
git add adapter/app/v6/ledger_intents.py adapter/app/v6/ledger_actions.py adapter/app/v6/ledger_cycles.py adapter/tests/v6/test_ledger_plan_actions.py
git commit -m "feat: store V6 plan levels and management actions"
```

---

## Task 7: Pemilihan order STOP dan intent berencana

**Files:**
- Create: `adapter/app/v6/risk/order_choice.py` (pindahan `ReferenceQuote`, `_Inputs` →
  `OrderInputs`, `_Order` → `ChosenOrder`, `_drift_points`, `_stop_floor`, `_choose_order`,
  `_market_order`, `_dec`, `_stop`, `_loss_usd`, `SIDE_SIGN`, `LIMIT_ORDERS`,
  `MARKET_ORDERS`, `MARKET_STYLES`, `DRIFT_STOP_FRACTION`, `MIN_DRIFT_POINTS`)
- Modify: `adapter/app/v6/risk/intent_builder.py` (impor ulang nama-nama itu, parameter
  `plan`, `_timing`, `_draft`, `_response`)
- Test: `adapter/tests/v6/test_intent_builder_plan.py`

**Interfaces:**
- Consumes: `TradePlan` (Task 4); `STOP_ORDER_TYPES`, field tangga `PollResponse` (Task 5);
  field tangga `NewIntent` (Task 6)
- Produces:
  - `order_choice.OrderInputs` (field `_Inputs` lama + `trade: TradePlan | None = None`)
  - `order_choice.ChosenOrder(order_type, entry, drift_points, loss_usd, pending: bool)`
  - `order_choice.choose_order(i: OrderInputs) -> ChosenOrder | Refusal`
  - `order_choice.STOP_ORDERS`, `STOP_NOT_BEYOND = "STOP_NOT_BEYOND"`,
    `MARKET_MOVED = "MARKET_MOVED"`
  - `intent_builder.build_intent(..., trade: TradePlan | None = None)`
  - `intent_builder.ReferenceQuote` tetap bisa diimpor dari `intent_builder`

- [ ] **Step 1: Tulis tes yang gagal**

Tes memakai helper `tests/v6/test_intent_builder.py` (`build`, `context`, `drafted`,
`refused`, `NOW`, `CLOSE`, `FIXED_ID`, `EA_KEY`, `POINT`). Quote di helper itu
bid 4300.0 / ask 4300.2.

```python
"""An agent plan through the intent builder: STOP, LIMIT and MARKET with a TP ladder."""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr

from app.v6 import wire
from app.v6.risk import order_choice
from app.v6.risk.intent_builder import to_poll_response
from app.v6.types import TradePlan

from .test_intent_builder import (
    CLOSE, EA_KEY, NOW, POINT, build, drafted, plan, refused, sizing,
)


def trade(order_type: str = "LIMIT", **changes: Any) -> TradePlan:
    fields = dict(order_type=order_type, tp1=4302.0, tp2=4306.0, sl_after_tp1=4298.5,
                  sl_after_tp2=4302.0, time_limit_s=9000, pending_expiry_s=1800)
    return TradePlan(**{**fields, **changes})


def test_a_planned_limit_carries_the_ladder_and_its_own_times() -> None:
    draft = drafted(build(trade=trade()))
    row = draft.row
    assert (row.order_type, row.entry, row.sl, row.tp) == ("BUY_LIMIT", 4298.0, 4291.0, 4312.0)
    assert (row.tp1, row.tp2, row.sl_after_tp1, row.sl_after_tp2) == (
        4302.0, 4306.0, 4298.5, 4302.0)
    assert (row.time_barrier_s, row.pending_expiry_epoch) == (9000, CLOSE + 1800)
    response = to_poll_response(draft, int(NOW), SecretStr(EA_KEY), POINT)
    assert (response.schema_version, response.tp1, response.sl_after_tp2) == (
        "v6.intent.2", 4302.0, 4302.0)
    assert wire.verify_intent(SecretStr(EA_KEY), response, POINT)


def test_a_buy_stop_above_the_ask() -> None:
    stop_plan = plan(entry=4305.0)
    ladder = trade("STOP", tp1=4309.0, tp2=4313.0, sl_after_tp1=4305.5, sl_after_tp2=4309.0)
    draft = drafted(build(entry=4305.0, exit_plan=stop_plan, trade=ladder))
    assert (draft.row.order_type, draft.row.entry) == ("BUY_STOP", 4305.0)
    assert draft.row.pending_expiry_epoch == CLOSE + 1800


def test_a_stop_below_the_ask_is_refused() -> None:
    ladder = trade("STOP", tp1=4303.0, tp2=4307.0, sl_after_tp1=None or 0.0,
                   sl_after_tp2=0.0)
    codes = refused(build(entry=4300.1, exit_plan=plan(entry=4300.1), trade=ladder))
    assert codes == (order_choice.STOP_NOT_BEYOND,)


def test_a_planned_limit_that_is_no_longer_passive_is_refused() -> None:
    codes = refused(build(entry=4300.5, exit_plan=plan(entry=4300.5),
                          trade=trade(tp1=4305.0, tp2=4309.0, sl_after_tp1=0.0,
                                      sl_after_tp2=0.0)))
    assert codes == ("LIMIT_NOT_PASSIVE",)


def test_a_planned_market_order_fills_at_the_quote() -> None:
    market = trade("MARKET", tp1=4304.5, tp2=4308.5, sl_after_tp1=0.0, sl_after_tp2=0.0,
                   pending_expiry_s=0)
    draft = drafted(build(entry=4300.2, exit_plan=plan(entry=4300.2), trade=market,
                          sizing=sizing(lots=0.01)))
    assert (draft.row.order_type, draft.row.pending_expiry_epoch) == ("BUY", 0)
    moved = refused(build(entry=4296.0, exit_plan=plan(entry=4296.0), trade=market))
    assert moved == (order_choice.MARKET_MOVED,)


def test_without_a_plan_the_old_rules_hold() -> None:
    draft = drafted(build())
    assert (draft.row.tp1, draft.row.time_barrier_s) == (0.0, 7200)
```

Catatan: `build(...)` di helper lama meneruskan `**overrides` ke `build_intent`, jadi
`trade=` dan `exit_plan=` bisa langsung dipakai; `candidate(...)` di helper memakai
`entry` yang sama.

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_intent_builder_plan.py`
Expected: FAIL (`ImportError: cannot import name 'order_choice'`).

- [ ] **Step 3: Buat `order_choice.py`**

Pindahkan kode yang disebut di **Files** tanpa mengubah perilakunya, dengan nama publik
(`OrderInputs`, `ChosenOrder`, `choose_order`, `dec`, `stop_distance`, `loss_usd`,
`stop_floor`, `drift_points`). Lalu tambahkan jalur rencana:

```python
STOP_ORDERS: Final[Mapping[str, OrderType]] = MappingProxyType(
    {"buy": "BUY_STOP", "sell": "SELL_STOP"})
STOP_NOT_BEYOND: Final[str] = "STOP_NOT_BEYOND"
MARKET_MOVED: Final[str] = "MARKET_MOVED"


@dataclass(frozen=True)
class ChosenOrder:
    order_type: OrderType
    entry: float
    drift_points: int
    loss_usd: Decimal
    pending: bool


def _gap(i: OrderInputs) -> Decimal:
    """EA check 13: a pending price sits a stops level plus one tick from the quote."""
    spec = i.context.spec
    return spec.stops_level * dec(spec.point) + dec(spec.tick_size)


def _planned_order(i: OrderInputs) -> ChosenOrder | Refusal:
    """An agent plan names its order type; the quote only decides whether it still fits."""
    side = i.plan.side
    ref, entry = dec(i.quote.side_price(side)), dec(i.plan.entry)
    drift = drift_points(i)
    kind = i.trade.order_type
    if kind == "LIMIT":
        if i.sign * (ref - entry) >= _gap(i):
            return ChosenOrder(LIMIT_ORDERS[side], i.plan.entry, drift,
                               loss_usd(i, stop_distance(i, entry)), pending=True)
        return Refusal((LIMIT_NOT_PASSIVE,), f"the {side} LIMIT at {entry} is no longer passive")
    if kind == "STOP":
        if i.sign * (entry - ref) >= _gap(i):
            return ChosenOrder(STOP_ORDERS[side], i.plan.entry, drift,
                               loss_usd(i, stop_distance(i, entry)), pending=True)
        return Refusal((STOP_NOT_BEYOND,), f"the {side} STOP at {entry} is not beyond the quote")
    if abs(ref - entry) <= drift * dec(i.context.spec.point):
        return _market_order(i, ref, drift)
    return Refusal((MARKET_MOVED,), f"the quote {ref} moved away from the planned {entry}")


def choose_order(i: OrderInputs) -> ChosenOrder | Refusal:
    if i.trade is not None:
        return _planned_order(i)
    return _suggested_order(i)      # the former _choose_order body, unchanged
```

`_market_order` mengembalikan `ChosenOrder(..., pending=False)`; `LIMIT_NOT_PASSIVE`
tetap didefinisikan di `intent_builder.py` lama, jadi pindahkan konstanta itu ke
`order_choice.py` dan impor ulang di `intent_builder.py` (publisher memakai
`ib.LIMIT_NOT_PASSIVE`).

- [ ] **Step 4: Ubah `intent_builder.py`**

- Impor dari `order_choice` dan re-ekspor: `ReferenceQuote`, `LIMIT_NOT_PASSIVE`,
  `MARKET_STOP_BELOW_FLOOR`, `MARKET_REWARD_BELOW_1R`, `RISK_OVER_BUDGET`,
  `STOP_NOT_BEYOND`, `MARKET_MOVED`.
- `build_intent(..., new_id=new_intent_id, trade: TradePlan | None = None)` meneruskan
  `trade` ke `OrderInputs`.
- `_timing(i, order)`:

```python
def _timing(i: OrderInputs, order: ChosenOrder) -> tuple[int, int] | Refusal:
    """(valid_until_epoch, pending_expiry_epoch), or TOO_LATE."""
    settings, close = i.settings, i.context.as_of_epoch
    lifetime = settings.pending_expiry_s if i.trade is None else i.trade.pending_expiry_s
    valid_until = math.floor(i.now) + settings.intent_ttl_s
    pending_expiry = close + lifetime if order.pending else 0
    if order.pending:
        valid_until = min(valid_until, pending_expiry - MIN_PENDING_LIFETIME_S)
    if i.now - close <= settings.operator_deadline_s and valid_until - i.now >= MIN_VALIDITY_S:
        return valid_until, pending_expiry
    return Refusal((TOO_LATE,), f"a decision {i.now - close:.0f} s after the close is too late")
```

- `_draft(...)`: `time_barrier_s` menjadi
  `i.trade.time_limit_s if i.trade is not None else min(settings.time_barrier_s,
  plan.time_barrier_s, limits.MAX_TIME_BARRIER_S)`, dan `NewIntent` menerima
  `tp1`, `tp2`, `sl_after_tp1`, `sl_after_tp2` dari `i.trade` (0.0 bila `None`).
- `_response(...)` mengisi `tp1=row.tp1, tp2=row.tp2, sl_after_tp1=row.sl_after_tp1,
  sl_after_tp2=row.sl_after_tp2`.
- Ganti semua penyebutan "v6.intent.1" di docstring menjadi "v6.intent.2".

Pastikan `intent_builder.py` ≤ 400 baris setelah pemindahan (target sekitar 300).

- [ ] **Step 5: Jalankan tes**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_intent_builder_plan.py tests/v6/test_intent_builder.py tests/v6/test_intent_publisher.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add adapter/app/v6/risk/order_choice.py adapter/app/v6/risk/intent_builder.py adapter/tests/v6/test_intent_builder_plan.py
git commit -m "feat: build V6 STOP, LIMIT and MARKET intents from agent plans"
```

---

## Task 8: Paket operator v3 (flat, pending, position)

**Files:**
- Modify: `adapter/app/v6/schemas/operator_parts.py` (literal enum baru dipindah ke sini,
  `PacketLimits` baru, `ENUM_CHOICES`, `LIMIT_VALUES`; hapus `PendingAction` dan
  `PacketPendingOrder`)
- Modify: `adapter/app/v6/schemas/operator_plan.py` (impor literal dari `operator_parts`,
  pindahan `PacketPendingOrder` dengan field `plan`)
- Create: `adapter/app/v6/schemas/operator_checks.py` (pindahan pemeriksaan keputusan v2 dari
  `operator.py`: `_parse_decision`, `parse_operator_decision`, `_check_views`,
  `_check_rebuttal`, `entry_plan_problem`, `_check_entry_plan`, `_lots_problem`,
  `decision_extras_problem`)
- Modify: `adapter/app/v6/schemas/operator.py` (paket v3, templat v3, kode penolakan)
- Modify: `adapter/app/v6/deliberation/packet_extras.py` (`limits_block`,
  `pending_order_block`, `position_block`)
- Modify: `adapter/app/v6/deliberation/operator_packet.py` (`PacketRequest`, `_body`)
- Modify: `adapter/app/v6/schemas/snapshot.py` (`PositionBlock.plan_step`,
  `PositionBlock.time_limit_epoch`, default 0)
- Modify: `adapter/scripts/v6ops/waiting.py:47` (`PACKET_SCHEMA = "v6.operator.packet.3"`)
- Modify: `adapter/tests/v6/operator_fixtures_v6.py` (body v3, `position_block()`,
  `pending_block()`, `limits_block()` baru)
- Test: `adapter/tests/v6/test_operator_packet_v3.py`

**Interfaces:**
- Consumes: model Task 3, `plan_bounds` (Task 4), `IntentRecord` field rencana (Task 6),
  `ActionRow` (Task 6)
- Produces:
  - `PACKET_SCHEMA = "v6.operator.packet.3"`, `DECISION_SCHEMA = "v6.operator.decision.3"`,
    `DECISION_SCHEMAS = ("v6.operator.decision.1", "v6.operator.decision.2",
    "v6.operator.decision.3")`
  - `OperatorPacketBody` field baru: `packet_kind`, `state`, `position`, `pending_order`,
    `last_action`, `last_bias`, `last_bias_at_epoch`
  - `DecisionTemplate` (model strict v3) dan `OperatorPacket.decision_template: DecisionTemplate`
  - `decision_template(body, digest) -> DecisionTemplate`
  - Kode: `DECISION_ERR_MANAGE = "DECISION_MANAGE"`, `DECISION_ERR_BIAS = "DECISION_BIAS"`,
    `DECISION_ERR_KIND = "DECISION_KIND"`; `DECISION_ERR_REVIEW` dihapus
  - `PacketRequest(..., state: PacketState = "flat", record: IntentRecord | None = None,
    last_action: ActionRow | None = None, last_bias: M15Bias | None = None,
    last_bias_at: int | None = None)`; field `review` dihapus
  - `packet_extras.limits_block(context, settings, remaining_loss_usd, *, state)`,
    `pending_order_block(context, record)`, `position_block(context, record)`
  - `plan_rules.bounds_from_packet(packet) -> PlanBounds`

Aturan body tambahan (di `_check_body`):
- `state == "position"` tepat saat `position` terisi; `state == "pending"` tepat saat
  `pending_order` terisi; paling banyak satu dari keduanya.
- `state != "flat"` berarti `candidates` kosong dan `limits.agent_entry_possible` false.

Templat keputusan v3:
- `action`: HOLD untuk `flat`, MANAGE untuk `pending`/`position`.
- `manage`: null untuk `flat`; `{"target": state, "ticket": <tiket>, "op": "KEEP"}`
  untuk yang lain.
- `views`: view baseline (atau default hati-hati), seperti templat v2.
- `m15_bias`: `last_bias` bila ada, selain itu `{"direction": "unclear"}`.
- `entry_plan`: null, `note`: "".

- [ ] **Step 1: Perbarui fixture operator**

Di `operator_fixtures_v6.py`:
- `limits_block()` menambah `"buy_stop_min": round(ASK + 0.37, 2)`,
  `"sell_stop_max": round(BID - 0.37, 2)`, `"modify_distance": 0.37`, `"min_tp1_r": 0.5`,
  `"time_limit_min_minutes": 60`, `"time_limit_max_minutes": 240`,
  `"pending_expiry_min_minutes": 15`, `"pending_expiry_max_minutes": 60`.
- `body()` memakai `"schema_version": "v6.operator.packet.3"`, `"packet_kind": "m15"`,
  `"state": "flat"`, `"position": None`, `"pending_order": None`, `"last_action": None`,
  `"last_bias": None`, `"last_bias_at_epoch": None`.
- Helper baru:

```python
def plan_block(**changes: Any) -> dict[str, Any]:
    block = {"tp1": 4539.0, "tp2": 4543.0, "sl_after_tp1": 4535.5, "sl_after_tp2": 4539.0,
             "step": 0, "time_limit_min": 150}
    return {**block, **changes}


def position_block(**changes: Any) -> dict[str, Any]:
    block = {"ticket": 91, "intent_id": "k7w2m4pq3xza", "side": "buy", "lots": 0.01,
             "open_price": 4533.35, "open_epoch": BAR_CLOSE - 600, "sl": 4526.35,
             "tp": 4549.0, "initial_sl": 4526.35, "profit": 1.83, "r_now": 0.26,
             "mae_points": 120.0, "mfe_points": 240.0, "minutes_open": 10.0,
             "time_limit_epoch": BAR_CLOSE - 600 + 9000, "plan": plan_block()}
    return {**block, **changes}


def pending_block(**changes: Any) -> dict[str, Any]:
    block = {"ticket": 77, "intent_id": "k7w2m4pq3xza", "order_type": "BUY_LIMIT",
             "price": 4531.35, "sl": 4524.35, "tp": 4547.0, "lots": 0.01,
             "expiration_epoch": BAR_CLOSE + 900, "distance_from_quote": 4.0,
             "plan": plan_block(tp1=4535.5, tp2=4541.0, sl_after_tp1=4532.0,
                                sl_after_tp2=4535.5)}
    return {**block, **changes}


def managed_packet(state: str, **changes: Any) -> OperatorPacket:
    """A pending or position packet: no suggestions, no agent entry."""
    ids = (AGENT_ID,)
    allowed = allowed_values(operator_agents=("claude_code", "codex"), candidate_ids=ids,
                             event_ids=(EVENT_ID,), pa_min_conviction=0.6)
    block = {"position": position_block()} if state == "position" else {
        "pending_order": pending_block()}
    return packet(state=state, candidates=[], limits=limits_block(agent_entry_possible=False),
                  allowed=allowed.model_dump(mode="json"),
                  baseline_views={**body()["baseline_views"], "price_action": None},
                  **{**block, **changes})


def decision_v3(sealed: OperatorPacket, **changes: Any) -> dict[str, Any]:
    """The sealed template with the agent set (edit it like an agent would)."""
    template = sealed.decision_template.model_dump(mode="json")
    return {**template, "agent": "codex", **copy.deepcopy(changes)}
```

- [ ] **Step 2: Tulis tes yang gagal**

```python
"""Operator packet v3: states, blocks, template and the packet builder."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.v6.deliberation.operator_packet import PacketRequest, build_packet
from app.v6.deliberation.packet_extras import pending_order_block, position_block
from app.v6.deliberation.plan_rules import bounds_from_packet
from app.v6.ledger_intents import IntentRecord

from . import engine_fixtures_v6 as ef
from . import operator_fixtures_v6 as of


def test_a_flat_packet_holds_by_default() -> None:
    sealed = of.packet()
    template = sealed.decision_template
    assert (sealed.schema_version, sealed.packet_kind, sealed.state) == (
        "v6.operator.packet.3", "m15", "flat")
    assert (template.schema_version, template.action, template.manage) == (
        "v6.operator.decision.3", "HOLD", None)
    assert template.m15_bias.direction == "unclear"
    bounds = bounds_from_packet(sealed)
    assert (bounds.entry.modify_distance, bounds.time_limit_max, bounds.max_lots) == (
        0.37, 240, 0.03)


@pytest.mark.parametrize(("state", "ticket"), [("position", 91), ("pending", 77)])
def test_a_managed_packet_keeps_by_default(state: str, ticket: int) -> None:
    sealed = of.managed_packet(state)
    manage = sealed.decision_template.manage
    assert (sealed.decision_template.action, manage.target, manage.ticket, manage.op) == (
        "MANAGE", state, ticket, "KEEP")


def test_the_last_bias_is_echoed_in_the_template() -> None:
    bias = {"direction": "range", "levels": [4526.0, 4541.0], "invalidation": None,
            "scenario": "fade the box"}
    sealed = of.packet(last_bias=bias, last_bias_at_epoch=of.BAR_OPEN)
    assert sealed.decision_template.m15_bias.levels == (4526.0, 4541.0)


@pytest.mark.parametrize("changes", [
    {"state": "position"},
    {"state": "flat", "position": of.position_block()},
    {"state": "pending", "position": of.position_block(), "pending_order": of.pending_block()},
    {"state": "pending", "pending_order": of.pending_block()},  # candidates still offered
    {"packet_kind": "m1"},
    {"schema_version": "v6.operator.packet.2"},
])
def test_inconsistent_bodies_are_refused(changes: dict) -> None:
    with pytest.raises(ValidationError):
        of.packet(**changes)


def record(**changes) -> IntentRecord:
    fields = dict(intent_id="k7w2m4pq3xza", cycle_id="c-00000000000000aa",
                  session_id="sess-1", agent="claude_code", source="operator",
                  status="FILLED", side="buy", order_type="BUY_LIMIT", entry=4296.5,
                  sl=4289.0, tp=4310.0, lots=0.02, risk_usd=15.8, valid_until_epoch=1,
                  pending_expiry_epoch=2, time_barrier_s=9000, created_at=1.0,
                  tp1=4301.0, tp2=4305.0, sl_after_tp1=4297.0, sl_after_tp2=4301.0,
                  plan_step=1)
    return IntentRecord(**{**fields, **changes})


POSITION = {"ticket": 91, "magic": 250570, "side": "buy", "volume": 0.02,
            "price_open": 4296.5, "sl": 4297.0, "tp": 4310.0, "profit": 7.0, "swap": 0.0,
            "open_epoch": ef.AS_OF - 1200, "comment": "Q6:k7w2m4pq3xza",
            "mae_points": 150.0, "mfe_points": 500.0, "plan_step": 1,
            "time_limit_epoch": ef.AS_OF - 1200 + 9000}
RESTING = {"ticket": 77, "magic": 250570, "order_type": "BUY_STOP", "price": 4303.5,
           "sl": 4296.5, "tp": 4317.0, "volume": 0.01, "expiration_epoch": ef.AS_OF + 900,
           "comment": "Q6:k7w2m4pq3xza"}


def context_with(**payload):
    return ef.market_context(ef.engine_snapshot(**payload))


def test_position_block() -> None:
    block = position_block(context_with(positions=[POSITION]), record())
    assert (block["ticket"], block["initial_sl"], block["plan"]["step"]) == (91, 4289.0, 1)
    assert block["minutes_open"] == pytest.approx(20.0)
    assert block["r_now"] == pytest.approx((ef.PRICE - 4296.5) / 7.5, abs=0.01)
    assert block["plan"]["time_limit_min"] == 150


def test_position_block_without_a_record() -> None:
    block = position_block(context_with(positions=[POSITION]), None)
    assert block["initial_sl"] == 4297.0 and block["plan"]["tp1"] == 0.0


def test_pending_block_carries_the_plan() -> None:
    block = pending_order_block(context_with(pending_orders=[RESTING]),
                                record(order_type="BUY_STOP", plan_step=0))
    assert (block["order_type"], block["plan"]["tp1"], block["plan"]["step"]) == (
        "BUY_STOP", 4301.0, 0)
    assert block["distance_from_quote"] == pytest.approx(4303.5 - ef.ask_price())


def test_the_builder_serves_a_position_packet() -> None:
    context = context_with(positions=[POSITION])
    request = PacketRequest(
        context=context, gates=(), offered=(), baseline=ef.rules_views(context),
        remaining_loss_usd=50.0, session_id="sess-1", armed=True, now=ef.RECEIVED,
        state="position", record=record())
    sealed = build_packet(request, ef.settings(backend="operator", mode="execute",
                                               operator_token="t" * 40,
                                               ea_hmac_key="k" * 40))
    assert sealed.state == "position" and sealed.position.ticket == 91
    assert sealed.candidates == () and sealed.limits.agent_entry_possible is False
```

Catatan fixture engine yang perlu ditambahkan bila belum ada (tulis sebelum Step 3):
- `ef.market_context(snapshot) -> MarketContext` (seperti `context()` di
  `test_intent_publisher.py`: `MarketContext.from_snapshot(...)` dengan `bars={}`,
  `session_state(AS_OF)`, `calendar()`, `features={}`).
- `ef.ask_price() -> float` (ask dari `engine_snapshot_payload()`).
- `ef.rules_views(context) -> DeskViews` (view rules dengan `OfflineProvider` atau
  `DeskViews` berisi `None` di semua desk bila builder menerimanya).

- [ ] **Step 3: Jalankan, pastikan gagal**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_operator_packet_v3.py`
Expected: FAIL.

- [ ] **Step 4: Implementasi skema**

`operator_parts.py`:
- Pindahkan literal `PlanOrderType`, `ManageTarget`, `ManageOp`, `BiasDirection`,
  `DecisionAction`, `PacketKind`, `PacketState`, `ActionStatus` ke sini (di bawah
  `AgentOrderType`); `operator_plan.py` mengimpornya dari `operator_parts`.
- Hapus `PendingAction` dan entri `"pending_action"` di `ENUM_CHOICES`; tambahkan:

```python
    "action": get_args(DecisionAction),
    "entry_plan_v2.order_type": get_args(PlanOrderType),
    "manage.target": get_args(ManageTarget),
    "manage.op": get_args(ManageOp),
    "m15_bias.direction": get_args(BiasDirection),
```

- `LIMIT_VALUES` bertambah `"max_bias_levels": 6`, `"max_scenario_chars": 240`,
  `"max_reason_chars": 200`, `"max_decision_note_chars": 300`.
- `PacketLimits` bertambah:

```python
    buy_stop_min: Price
    sell_stop_max: Price
    modify_distance: float = Field(ge=0)
    min_tp1_r: Price
    time_limit_min_minutes: int = Field(ge=1, le=240)
    time_limit_max_minutes: int = Field(ge=1, le=240)
    pending_expiry_min_minutes: int = Field(ge=1, le=60)
    pending_expiry_max_minutes: int = Field(ge=1, le=60)
```

- Hapus `PacketPendingOrder` dari file ini.

`operator_plan.py` menambah (pindahan, dengan `plan`):

```python
class PacketPendingOrder(Frozen):
    """The resting V6 order a management packet asks about."""

    ticket: int = Field(ge=0)
    intent_id: Annotated[str, StringConstraints(max_length=16)]
    order_type: Literal["BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP"]
    price: Price
    sl: float = Field(ge=0)
    tp: float = Field(ge=0)
    lots: Price
    expiration_epoch: Epoch
    distance_from_quote: float
    plan: PacketPlan | None = None
```

`operator.py`:
- Pindahkan pemeriksaan v2 ke `operator_checks.py` (bagian **Files**), hapus bagian review
  dari `decision_extras_problem` (tinggal pemeriksaan `lots`), dan re-ekspor nama-namanya.
- Konstanta skema dan kode:

```python
PACKET_SCHEMA: Final[str] = "v6.operator.packet.3"
DECISION_SCHEMA_V2: Final[str] = "v6.operator.decision.2"
DECISION_SCHEMA: Final[str] = "v6.operator.decision.3"
DECISION_SCHEMAS: Final[tuple[str, ...]] = (
    "v6.operator.decision.1", DECISION_SCHEMA_V2, DECISION_SCHEMA)
DecisionSchema = Literal["v6.operator.decision.1", "v6.operator.decision.2"]
DECISION_ERR_MANAGE: Final[str] = "DECISION_MANAGE"
DECISION_ERR_BIAS: Final[str] = "DECISION_BIAS"
DECISION_ERR_KIND: Final[str] = "DECISION_KIND"
```

- Field body dan pemeriksaan:

```python
    schema_version: Literal["v6.operator.packet.3"]
    packet_kind: PacketKind
    state: PacketState
    ...
    pending_order: PacketPendingOrder | None = None
    position: PacketPosition | None = None
    last_action: PacketAction | None = None
    last_bias: M15Bias | None = None
    last_bias_at_epoch: Epoch | None = None
```

Tambahkan ke `checks` di `_check_body`:

```python
            ((self.state == "position") == (self.position is not None),
             "state position needs exactly a position block"),
            ((self.state == "pending") == (self.pending_order is not None),
             "state pending needs exactly a pending_order block"),
            (self.state == "flat" or (not self.candidates
                                      and not self.limits.agent_entry_possible),
             "a management packet offers no entry"),
```

- Templat v3:

```python
class DecisionTemplate(Frozen):
    """The ready-to-edit v3 decision of a served packet (HOLD, or KEEP when managing)."""

    schema_version: Literal["v6.operator.decision.3"]
    packet_kind: PacketKind
    cycle_id: ItemId
    packet_hash: Hash
    agent: OperatorAgent
    action: DecisionAction
    views: OperatorViews
    entry_plan: None = None
    manage: ManageRequest | None = None
    m15_bias: M15Bias
    note: Annotated[str, StringConstraints(max_length=300)] = ""


def _keep(body: OperatorPacketBody) -> ManageRequest | None:
    if body.state == "position" and body.position is not None:
        return ManageRequest(target="position", ticket=body.position.ticket, op="KEEP")
    if body.state == "pending" and body.pending_order is not None:
        return ManageRequest(target="pending", ticket=body.pending_order.ticket, op="KEEP")
    return None


def decision_template(body: OperatorPacketBody, digest: str) -> DecisionTemplate:
    """The rules views (or cautious defaults), HOLD or KEEP, and the last bias."""
    base = body.baseline_views
    views = OperatorViews(
        price_action=_or_default(base.price_action, ABSTAIN_VIEW),
        news_risk=_or_default(base.news_risk, UNKNOWN_NEWS_VIEW),
        liquidity=_or_default(base.liquidity, UNKNOWN_LIQUIDITY_VIEW),
        structure=_or_default(base.structure, UNKNOWN_STRUCTURE_VIEW))
    manage = _keep(body)
    return DecisionTemplate(
        schema_version=DECISION_SCHEMA, packet_kind=body.packet_kind,
        cycle_id=body.cycle_id, packet_hash=digest, agent=body.allowed.agents[0],
        action="HOLD" if manage is None else "MANAGE", views=views, manage=manage,
        m15_bias=body.last_bias or M15Bias(direction="unclear"))
```

  `OperatorPacket.decision_template` bertipe `DecisionTemplate`; `_check_seal` tetap sama.
  `OperatorDecision` (v2) tetap ada untuk validasi v2 dan kehilangan `pending_action`.

`plan_rules.py` menambah:

```python
def bounds_from_packet(packet: "OperatorPacket") -> PlanBounds:
    """The PlanBounds a served packet advertised."""
    block = packet.limits
    return PlanBounds(
        entry=limits_from_packet(packet), min_tp1_r=block.min_tp1_r,
        time_limit_min=block.time_limit_min_minutes,
        time_limit_max=block.time_limit_max_minutes,
        pending_expiry_min=block.pending_expiry_min_minutes,
        pending_expiry_max=block.pending_expiry_max_minutes,
        volume_min=block.volume_min, lots_step=block.lots_step, max_lots=block.max_lots)
```

  (impor `OperatorPacket` di bawah `TYPE_CHECKING`, `limits_from_packet` dari `agent_entry`;
  ganti `getattr(block, "modify_distance", 0.0)` di `limits_from_packet` menjadi
  `block.modify_distance`).

`snapshot.py` — `PositionBlock` bertambah:

```python
    plan_step: int = Field(default=0, ge=0, le=2)
    time_limit_epoch: int = Field(default=0, ge=0)
```

- [ ] **Step 5: Implementasi blok paket dan builder**

`packet_extras.py`:

```python
def _record_plan(record: IntentRecord | None, step: int) -> Document:
    if record is None:
        return {"tp1": 0.0, "tp2": 0.0, "sl_after_tp1": 0.0, "sl_after_tp2": 0.0,
                "step": step, "time_limit_min": 0}
    return {"tp1": record.tp1, "tp2": record.tp2, "sl_after_tp1": record.sl_after_tp1,
            "sl_after_tp2": record.sl_after_tp2, "step": max(step, record.plan_step),
            "time_limit_min": record.time_barrier_s // SECONDS_PER_MINUTE}


def _intent_of(comment: str) -> str:
    return intent_id_from_comment(comment) or ""


def pending_order_block(context: MarketContext, record: IntentRecord | None) -> Document | None:
    """The first resting V6 order and its plan (None when nothing rests)."""
    if not context.pending_orders:
        return None
    order = context.pending_orders[0]
    buying = order.order_type.startswith("BUY")
    quote = context.quote
    distance = order.price - quote.ask if buying else quote.bid - order.price
    if order.order_type.endswith("LIMIT"):
        distance = -distance
    return {"ticket": order.ticket, "intent_id": _intent_of(order.comment),
            "order_type": order.order_type, "price": order.price, "sl": order.sl,
            "tp": order.tp, "lots": order.volume, "expiration_epoch": order.expiration_epoch,
            "distance_from_quote": round(distance, context.spec.digits),
            "plan": _record_plan(record, 0)}


def position_block(context: MarketContext, record: IntentRecord | None) -> Document | None:
    """The first open V6 position with its initial risk and plan."""
    if not context.positions:
        return None
    position = context.positions[0]
    sign = 1 if position.side == "buy" else -1
    mark = context.quote.bid if sign > 0 else context.quote.ask
    initial_sl = record.sl if record is not None else position.sl
    risk = abs(position.price_open - initial_sl)
    barrier = record.time_barrier_s if record is not None else 0
    limit_epoch = position.time_limit_epoch or (position.open_epoch + barrier)
    return {"ticket": position.ticket, "intent_id": _intent_of(position.comment),
            "side": position.side, "lots": position.volume, "open_price": position.price_open,
            "open_epoch": position.open_epoch, "sl": position.sl, "tp": position.tp,
            "initial_sl": initial_sl, "profit": position.profit,
            "r_now": round(sign * (mark - position.price_open) / risk, 3) if risk > 0 else 0.0,
            "mae_points": position.mae_points, "mfe_points": position.mfe_points,
            "minutes_open": round(max(0, context.as_of_epoch - position.open_epoch)
                                  / SECONDS_PER_MINUTE, 2),
            "time_limit_epoch": limit_epoch,
            "plan": _record_plan(record, position.plan_step)}
```

  `distance_from_quote` bermakna "berapa jauh pasar harus bergerak untuk mengisi order":
  untuk BUY_LIMIT `ask − price`, untuk BUY_STOP `price − ask` (tanda dibalik untuk sell).
  Periksa ulang tanda ini di tes `test_pending_block_carries_the_plan` dan tes review lama.

  `limits_block(context, settings, remaining_loss_usd, *, state="flat")` memakai
  `plan_bounds(...)` dan menambah field baru:

```python
    bounds = plan_bounds(context, settings, remaining_loss_usd)
    entry = bounds.entry
    return {
        ...,  # field lama, dengan entry.* menggantikan bounds.*
        "agent_entry_possible": entry.possible and state == "flat",
        "buy_stop_min": entry.buy_stop_min, "sell_stop_max": entry.sell_stop_max,
        "modify_distance": entry.modify_distance, "min_tp1_r": bounds.min_tp1_r,
        "time_limit_min_minutes": bounds.time_limit_min,
        "time_limit_max_minutes": bounds.time_limit_max,
        "pending_expiry_min_minutes": bounds.pending_expiry_min,
        "pending_expiry_max_minutes": bounds.pending_expiry_max,
    }
```

`operator_packet.py`:
- `PacketRequest`: hapus `review`; tambah `state`, `record`, `last_action`, `last_bias`,
  `last_bias_at` (lihat **Interfaces**).
- `build_packet`: kandidat hanya untuk `state == "flat"`.
- `_body` menambah:

```python
        "packet_kind": "m15", "state": request.state,
        "limits": limits,     # limits_block(..., state=request.state)
        "pending_order": (pending_order_block(context, request.record)
                          if request.state == "pending" else None),
        "position": (position_block(context, request.record)
                     if request.state == "position" else None),
        "last_action": _action(request.last_action),
        "last_bias": None if request.last_bias is None
        else request.last_bias.model_dump(mode="json"),
        "last_bias_at_epoch": request.last_bias_at,
```

  dengan

```python
def _action(row: ActionRow | None) -> Document | None:
    if row is None:
        return None
    return {"action_id": row.action_id, "op": row.command, "ticket": row.ticket,
            "status": row.status, "detail": _text(row.detail, MAX_ACTION_DETAIL_CHARS),
            "at_epoch": int(row.updated_at)}
```

- [ ] **Step 6: Jalankan tes paket dan tes operator lama**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_operator_packet_v3.py tests/v6 -k "operator or packet"`
Expected: tes paket v3 PASS. Tes lama yang masih memakai `pending_action`, `review=True`,
`DECISION_ERR_REVIEW` atau templat v2 akan gagal; tes itu diganti di Task 9 dan Task 11
(tandai dengan `pytest.mark.skip(reason="replaced in Task 9/11")` hanya sampai tugas itu,
lalu hapus). Tidak ada commit dengan tes yang di-skip: Task 8, 9 dan 11 di-commit bersama
bila perlu.

- [ ] **Step 7: Commit (atau gabungkan dengan Task 9)**

```bash
git add adapter/app/v6/schemas adapter/app/v6/deliberation/packet_extras.py adapter/app/v6/deliberation/operator_packet.py adapter/app/v6/deliberation/plan_rules.py adapter/scripts/v6ops/waiting.py adapter/tests/v6
git commit -m "feat: serve V6 operator packets v3 for flat, pending and position states"
```

---

## Task 9: Validasi keputusan v3

**Files:**
- Create: `adapter/app/v6/deliberation/decision_v3.py`
- Modify: `adapter/app/v6/deliberation/operator_decision.py` (field `ValidatedDecision`,
  dispatch v3, hapus `pending_action`)
- Test: `adapter/tests/v6/test_decision_v3.py`; hapus tes review lama di
  `tests/v6/test_operator_entry_plan.py` (bagian "lots and review") dan ganti
  `tests/v6/test_review_packet_parts.py` yang memakai `pending_review`

**Interfaces:**
- Consumes: `EntryPlanV2`, `ManageRequest`, `M15Bias` (Task 3); `plan_problems_v2`,
  `lots_problem`, `bounds_from_packet` (Task 4, 8); `manage_problems` (Task 10 — lihat
  catatan urutan di bawah)
- Produces:
  - `DecisionEnvelopeV3`
  - `validate_v3(packet, raw_or_envelope, settings, *, now) -> ValidatedDecision | DecisionError`
  - `ValidatedDecision` field baru: `schema_version: str = "v6.operator.decision.2"`,
    `action: str = ""`, `plan: EntryPlanV2 | None = None`,
    `manage: ManageRequest | None = None`, `bias: M15Bias | None = None`, `note: str = ""`;
    field `pending_action` dihapus
  - `validate_decision(...)` memilih jalur v3 bila `schema_version` dokumen adalah v3

Urutan: Task 10 (`management.py`) tidak bergantung pada Task 9, jadi kerjakan Task 10 lebih
dulu bila ingin tes Task 9 memanggil `manage_problems` yang asli.

Chief yang diturunkan dari keputusan v3:
- `ENTER`: `ChiefDecision(action="ENTER", candidate_id=packet.limits.agent_entry_id,
  risk_tier="standard", order_style="MARKET" if plan.order_type == "MARKET" else "LIMIT",
  exit_profile="STANDARD", confidence=<conviction PA untuk agent_entry_id>,
  rationale=note[:300], dissent="")`.
- `HOLD`/`MANAGE`: `HOLD_DECISION` dengan `rationale=note[:300]`.

Aturan (urutan pemeriksaan):
1. Ukuran dan envelope strict (`DECISION_TOO_LARGE`, `DECISION_NOT_JSON`, `DECISION_SCHEMA`).
2. Paket yang sama, belum kedaluwarsa, agen diizinkan (seperti v2).
3. `packet_kind` sama dengan paket, selain itu `DECISION_KIND`.
4. `state == "flat"`: `action` HOLD atau ENTER dan `manage` null; selain itu `action` MANAGE,
   `entry_plan` null dan `manage` wajib. Pelanggaran → `DECISION_MANAGE`
   (atau `DECISION_ENTRY_PLAN` bila `entry_plan` diisi di paket non-flat).
5. `views` wajib (paket `m15`): PA dan desk diperiksa seperti v2 (PA gagal → `DECISION_VIEW`,
   desk gagal → flag).
6. `m15_bias` wajib dan valid, selain itu `DECISION_BIAS`.
7. `ENTER`: `entry_plan` wajib → `EntryPlanV2` → `plan_problems_v2` → `lots_problem`
   (`DECISION_LOTS`). HOLD dengan `entry_plan` → `DECISION_ENTRY_PLAN`.
8. `MANAGE`: `ManageRequest` → `manage_problems(request, packet)` → `DECISION_MANAGE`
   dengan detail `"KODE: pesan; ..."`.
9. Keputusan v1/v2 hanya untuk paket `flat`; untuk paket lain → `DECISION_KIND`.

- [ ] **Step 1: Tulis tes yang gagal**

```python
"""Decision v3 against a served packet."""

from __future__ import annotations

from typing import Any

import pytest

from app.v6.config import V6Settings
from app.v6.deliberation.operator_decision import (
    DecisionError, ValidatedDecision, validate_decision,
)
from app.v6.schemas import operator as op

from . import operator_fixtures_v6 as of
from .cycle_fixtures_v6 import pa_payload

NOW = float(of.CREATED + 10)
SETTINGS = V6Settings(_env_file=None)
PLAN = {"side": "buy", "order_type": "LIMIT", "entry": 4533.35, "sl": 4526.35,
        "tp1": 4537.5, "tp2": 4541.0, "tp3": 4547.35, "sl_after_tp1": 4533.8,
        "sl_after_tp2": 4537.5, "time_limit_min": 150, "pending_expiry_min": 30,
        "lots": 0.01, "thesis": "bounce from the M15 pivot low"}


def validate(packet, document: dict[str, Any]):
    return validate_decision(packet, of.raw(document), SETTINGS, now=NOW)


def enter(packet, **changes: Any) -> dict[str, Any]:
    document = of.decision_v3(packet, action="ENTER", entry_plan=PLAN)
    document["views"]["price_action"] = pa_payload(of.AGENT_ID, conviction=0.8)
    return {**document, **changes}


def error(result) -> str:
    assert isinstance(result, DecisionError), result
    return result.code


def test_an_enter_is_accepted_with_a_derived_chief() -> None:
    packet = of.packet()
    result = validate(packet, enter(packet, note="pivot bounce"))
    assert isinstance(result, ValidatedDecision), result
    assert (result.schema_version, result.action) == ("v6.operator.decision.3", "ENTER")
    assert (result.chief.action, result.chief.candidate_id) == ("ENTER", of.AGENT_ID)
    assert (result.chief.order_style, result.chief.rationale) == ("LIMIT", "pivot bounce")
    assert result.plan.tp2 == 4541.0 and result.bias.direction == "unclear"


def test_a_stop_plan_maps_to_a_limit_style_chief() -> None:
    packet = of.packet()
    stop = {**PLAN, "order_type": "STOP", "entry": 4540.0, "sl": 4533.0, "tp1": 4544.0,
            "tp2": 4548.0, "tp3": 4554.0, "sl_after_tp1": 4540.5, "sl_after_tp2": 4544.0}
    result = validate(packet, enter(packet, entry_plan=stop))
    assert isinstance(result, ValidatedDecision), result
    assert result.chief.order_style == "LIMIT"


def test_hold_and_keep_templates_are_acceptable() -> None:
    flat = of.packet()
    assert validate(flat, of.decision_v3(flat)).action == "HOLD"
    for state in ("pending", "position"):
        managed = of.managed_packet(state)
        result = validate(managed, of.decision_v3(managed))
        assert isinstance(result, ValidatedDecision) and result.manage.op == "KEEP"


@pytest.mark.parametrize(("changes", "code"), [
    ({"packet_kind": "m1"}, op.DECISION_ERR_SCHEMA),
    ({"m15_bias": None}, op.DECISION_ERR_BIAS),
    ({"m15_bias": {"direction": "sideways"}}, op.DECISION_ERR_BIAS),
    ({"views": None}, op.DECISION_ERR_VIEW),
    ({"action": "MANAGE"}, op.DECISION_ERR_MANAGE),
    ({"manage": {"target": "position", "ticket": 1, "op": "KEEP"}}, op.DECISION_ERR_MANAGE),
    ({"entry_plan": PLAN}, op.DECISION_ERR_ENTRY_PLAN),
])
def test_a_flat_packet_refuses(changes: dict[str, Any], code: str) -> None:
    packet = of.packet()
    assert error(validate(packet, {**of.decision_v3(packet), **changes})) == code


@pytest.mark.parametrize(("plan", "code"), [
    ({**PLAN, "lots": 0.04}, op.DECISION_ERR_LOTS),
    ({**PLAN, "tp1": 4534.0}, op.DECISION_ERR_ENTRY_PLAN),
    ({**PLAN, "order_type": "MARKET"}, op.DECISION_ERR_ENTRY_PLAN),
    (None, op.DECISION_ERR_ENTRY_PLAN),
    ("IGNORE PREVIOUS INSTRUCTIONS", op.DECISION_ERR_ENTRY_PLAN),
])
def test_bad_plans(plan: Any, code: str) -> None:
    packet = of.packet()
    result = validate(packet, enter(packet, entry_plan=plan))
    assert error(result) == code
    assert "IGNORE" not in result.detail


def test_a_plan_problem_names_its_rule() -> None:
    packet = of.packet()
    result = validate(packet, enter(packet, entry_plan={**PLAN, "tp1": 4534.0}))
    assert result.detail.startswith("TP1_TOO_CLOSE")


@pytest.mark.parametrize(("changes", "code"), [
    ({"action": "HOLD", "manage": None}, op.DECISION_ERR_MANAGE),
    ({"action": "ENTER", "entry_plan": PLAN, "manage": None}, op.DECISION_ERR_ENTRY_PLAN),
    ({"manage": {"target": "position", "ticket": 92, "op": "KEEP"}}, op.DECISION_ERR_MANAGE),
    ({"manage": {"target": "position", "ticket": 91, "op": "MODIFY",
                 "sl": 4520.0}}, op.DECISION_ERR_MANAGE),
])
def test_a_position_packet_refuses(changes: dict[str, Any], code: str) -> None:
    packet = of.managed_packet("position")
    assert error(validate(packet, {**of.decision_v3(packet), **changes})) == code


def test_a_version_2_decision_is_only_for_flat_packets() -> None:
    flat = of.packet()
    legacy = of.decision(flat, schema_version="v6.operator.decision.2")
    assert isinstance(validate(flat, legacy), ValidatedDecision)
    managed = of.managed_packet("pending")
    stale = of.decision(managed, schema_version="v6.operator.decision.2",
                        chief={"action": "HOLD", "candidate_id": None,
                               "risk_tier": "reduced", "order_style": "LIMIT",
                               "exit_profile": "STANDARD", "confidence": 0.0,
                               "rationale": "", "dissent": ""}, rebuttal={})
    assert error(validate(managed, stale)) == op.DECISION_ERR_KIND


def test_a_close_is_accepted() -> None:
    packet = of.managed_packet("position")
    close = {"target": "position", "ticket": 91, "op": "CLOSE", "reason": "structure broke"}
    result = validate(packet, {**of.decision_v3(packet), "manage": close})
    assert isinstance(result, ValidatedDecision) and result.manage.op == "CLOSE"
    assert result.summary()["manage_op"] == "CLOSE"
```

Catatan: `{"packet_kind": "m1"}` gagal di skema karena `PacketKind` hanya `"m15"` di tahap A,
jadi kodenya `DECISION_SCHEMA`; pemeriksaan `DECISION_KIND` untuk `packet_kind` baru relevan
di tahap B.

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_decision_v3.py`
Expected: FAIL.

- [ ] **Step 3: Implementasi `decision_v3.py`**

```python
"""
Decision v3 against the packet it answers (spec section 2).

Checked role by role like v2: the Price Action view refuses, a risk desk view is only
flagged. The v3 action replaces the v2 Chief, which is derived here so that the protocol,
the exit plan, the sizer and the intent builder keep their authority. Error details
name fields, pydantic error types and rule codes only; submitted text is never echoed.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field, JsonValue, StringConstraints, ValidationError
from typing_extensions import Annotated

from ..config import OperatorAgent, V6Settings
from ..schemas.agents import ChiefDecision, ItemId, PriceActionView
from ..schemas.operator import (
    DECISION_ERR_BIAS, DECISION_ERR_ENTRY_PLAN, DECISION_ERR_KIND, DECISION_ERR_LOTS,
    DECISION_ERR_MANAGE, DECISION_ERR_VIEW, HOLD_DECISION, OperatorPacket,
)
from ..schemas.operator_parts import DecisionAction, Frozen, Hash, PacketKind
from ..schemas.operator_plan import EntryPlanV2, M15Bias, ManageRequest
from . import operator_decision as od
from .management import manage_problems
from .plan_rules import bounds_from_packet, lots_problem, plan_problems_v2

DECISION_SCHEMA_V3: Final[str] = "v6.operator.decision.3"
MAX_NOTE_CHARS: Final[int] = 300


class DecisionEnvelopeV3(Frozen):
    schema_version: Literal["v6.operator.decision.3"]
    packet_kind: PacketKind
    cycle_id: ItemId
    packet_hash: Hash
    agent: OperatorAgent
    action: DecisionAction
    views: od.RawViews | None = None
    entry_plan: JsonValue = None
    manage: JsonValue = None
    m15_bias: JsonValue = None
    note: Annotated[str, StringConstraints(max_length=MAX_NOTE_CHARS)] = ""


def _where(exc: ValidationError) -> str:
    errors = exc.errors(include_url=False, include_context=False, include_input=False)
    return ", ".join(f"{od.safe_location(err['loc'])}({err['type']})"
                     for err in errors[:od.MAX_REPORTED_ERRORS])


def _parsed(model, raw: object, code: str, role: str):
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        return od.decision_error(code, f"invalid at: {_where(exc)}", role)


def _state_problem(envelope: DecisionEnvelopeV3,
                   packet: OperatorPacket) -> od.DecisionError | None:
    if envelope.packet_kind != packet.packet_kind:
        return od.decision_error(DECISION_ERR_KIND, "the decision answers another packet kind")
    if packet.state == "flat":
        if envelope.action == "MANAGE" or envelope.manage is not None:
            return od.decision_error(DECISION_ERR_MANAGE, "a flat packet takes HOLD or ENTER",
                                     "manage")
        if envelope.action == "HOLD" and envelope.entry_plan is not None:
            return od.decision_error(DECISION_ERR_ENTRY_PLAN, "a HOLD carries no entry_plan",
                                     "entry_plan")
        return None
    if envelope.entry_plan is not None or envelope.action == "ENTER":
        return od.decision_error(DECISION_ERR_ENTRY_PLAN,
                                 f"a {packet.state} packet takes no entry", "entry_plan")
    if envelope.action != "MANAGE" or envelope.manage is None:
        return od.decision_error(DECISION_ERR_MANAGE,
                                 f"a {packet.state} packet needs action MANAGE and manage",
                                 "manage")
    return None


def _plan(envelope: DecisionEnvelopeV3, packet: OperatorPacket):
    plan = _parsed(EntryPlanV2, envelope.entry_plan, DECISION_ERR_ENTRY_PLAN, "entry_plan")
    if isinstance(plan, od.DecisionError):
        return plan
    bounds = bounds_from_packet(packet)
    problems = plan_problems_v2(plan, bounds)
    if problems:
        detail = "; ".join(f"{item.code}: {item.message}" for item in problems)
        return od.decision_error(DECISION_ERR_ENTRY_PLAN, detail, "entry_plan")
    lots = lots_problem(plan.lots, bounds)
    return plan if lots is None else od.decision_error(DECISION_ERR_LOTS, lots, "entry_plan")


def _manage(envelope: DecisionEnvelopeV3, packet: OperatorPacket):
    request = _parsed(ManageRequest, envelope.manage, DECISION_ERR_MANAGE, "manage")
    if isinstance(request, od.DecisionError):
        return request
    problems = manage_problems(request, packet)
    if problems:
        return od.decision_error(DECISION_ERR_MANAGE, "; ".join(problems), "manage")
    return request


def _chief(action: str, plan: EntryPlanV2 | None, packet: OperatorPacket,
           price_action: PriceActionView, note: str) -> ChiefDecision:
    if action != "ENTER" or plan is None:
        return HOLD_DECISION.model_copy(update={"rationale": note[:MAX_NOTE_CHARS]})
    entry_id = packet.limits.agent_entry_id
    conviction = next((item.conviction for item in price_action.ranked
                       if item.candidate_id == entry_id), 0.0)
    return ChiefDecision(
        action="ENTER", candidate_id=entry_id, risk_tier="standard",
        order_style="MARKET" if plan.order_type == "MARKET" else "LIMIT",
        exit_profile="STANDARD", confidence=conviction, rationale=note[:MAX_NOTE_CHARS],
        dissent="")


def validate_v3(packet: OperatorPacket, envelope: DecisionEnvelopeV3, settings: V6Settings,
                *, now: float) -> od.DecisionOutcome:
    problem = (od.packet_problem(envelope, packet, settings, now)
               or _state_problem(envelope, packet))
    if problem is not None:
        return problem
    if envelope.views is None:
        return od.decision_error(DECISION_ERR_VIEW, "an m15 packet needs the four views",
                                 "views")
    views = od.checked_desks(envelope.views, packet)
    if isinstance(views, od.DecisionError):
        return views
    bias = _parsed(M15Bias, envelope.m15_bias, DECISION_ERR_BIAS, "m15_bias")
    if isinstance(bias, od.DecisionError):
        return bias
    plan = _plan(envelope, packet) if envelope.action == "ENTER" else None
    manage = _manage(envelope, packet) if envelope.action == "MANAGE" else None
    for found in (plan, manage):
        if isinstance(found, od.DecisionError):
            return found
    pa_view, desks, flags = views
    chief = _chief(envelope.action, plan, packet, pa_view, envelope.note)
    return od.ValidatedDecision(
        cycle_id=envelope.cycle_id, packet_hash=envelope.packet_hash, agent=envelope.agent,
        price_action=pa_view, chief=chief, flags=flags, **desks,
        schema_version=DECISION_SCHEMA_V3, action=envelope.action, plan=plan,
        manage=manage, bias=bias, note=envelope.note)
```

`operator_decision.py` (perubahan yang dibutuhkan `decision_v3.py`):
- Ganti nama `_safe_location` → `safe_location`, `_error` → `decision_error`,
  `_packet_problem` → `packet_problem` (perbarui pemanggilan lama).
- Pecah `_checked_views` menjadi `checked_desks(views: RawViews, packet)` yang mengembalikan
  `(pa_view, {"news_risk": ..., "liquidity": ..., "structure": ...}, flags)` atau
  `DecisionError`, dan sisa jalur v2 (Chief, rebuttal, plan v2, lots) yang memakainya.
- `ValidatedDecision`: hapus `pending_action`; tambah field v3 (lihat **Interfaces**);
  `summary()` menambah `"schema": self.schema_version`, `"decision_action": self.action`,
  `"manage_op": None if self.manage is None else self.manage.op`,
  `"plan_order_type": None if self.plan is None else self.plan.order_type`.
- `validate_decision(packet, decision, settings, *, now)`:

```python
def _schema_of(raw: bytes) -> str | None:
    """The schema_version of a JSON object, or None (the strict parse reports why)."""
    try:
        document = json.loads(bytes(raw))
    except (ValueError, UnicodeDecodeError):
        return None
    return document.get("schema_version") if isinstance(document, dict) else None


def validate_decision(packet, decision, settings, *, now):
    if isinstance(decision, (bytes, bytearray)) and len(decision) <= MAX_DECISION_BYTES \
            and _schema_of(decision) == DECISION_SCHEMA_V3:
        from .decision_v3 import DecisionEnvelopeV3, validate_v3   # avoids an import cycle
        envelope = parse_model(DecisionEnvelopeV3, decision)
        if isinstance(envelope, DecisionError):
            return envelope
        return validate_v3(packet, envelope, settings, now=now)
    envelope = decision if isinstance(decision, DecisionEnvelope) else parse_envelope(decision)
    if isinstance(envelope, DecisionError):
        return envelope
    if packet.state != "flat":
        return decision_error(DECISION_ERR_KIND, "a management packet needs decision v3")
    ...  # the v2 path as before
```

  `parse_model(model, raw)` adalah `parse_envelope` yang digeneralisasi (batas ukuran dan
  pesan error yang sama). `DecisionEnvelope` (v2) kehilangan `pending_action`.

- [ ] **Step 4: Jalankan tes**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_decision_v3.py tests/v6/test_operator_decision.py tests/v6/test_operator_entry_plan.py`
Expected: PASS (tes review lama sudah dihapus).

- [ ] **Step 5: Commit**

```bash
git add adapter/app/v6/deliberation/decision_v3.py adapter/app/v6/deliberation/operator_decision.py adapter/tests/v6
git commit -m "feat: validate V6 operator decisions v3"
```

---

## Task 10: Validasi manajemen dan pembuatan aksi

**Files:**
- Create: `adapter/app/v6/deliberation/management.py`
- Modify: `adapter/app/v6/deliberation/publication.py` (tambah `ManagementAction`)
- Test: `adapter/tests/v6/test_management.py`

**Interfaces:**
- Consumes: `ManageRequest`, `PacketPosition`, `PacketPendingOrder`, `EntryPlanV2`,
  `OPS_BY_TARGET`, `PENDING_ONLY_FIELDS` (Task 3, 8); `plan_problems_v2`,
  `bounds_from_packet` (Task 4, 8)
- Produces:
  - `publication.ManagementAction(action_id, command, ticket, intent_id, cycle_id,
    issued_at, sl=0.0, tp=0.0, tp1=0.0, tp2=0.0, sl_after_tp1=0.0, sl_after_tp2=0.0,
    price=0.0, expiry_epoch=0, barrier_s=0)` dengan `payload() -> dict[str, float | int]`
  - `management.manage_problems(request, packet) -> tuple[str, ...]` (setiap entri
    `"KODE: pesan"`)
  - `management.build_action(request, packet, *, now, new_id=new_intent_id)
    -> ManagementAction | None` (None untuk KEEP dan CANCEL)
  - Kode: `MANAGE_SHAPE`, `MANAGE_TICKET`, `SL_WIDER`, `TOO_CLOSE`, `REWARD_TOO_LARGE`,
    `STEP_DONE`, `LADDER_ORDER`, `SL_STEP_INVALID`, `TIME_LIMIT_RANGE`, `MANAGE_SIZE`,
    `LADDER_MISSING`

Aturan (buy; sell cermin, harga acuan = bid untuk buy, ask untuk sell, `d` =
`limits.modify_distance`):
- Bentuk: target sama dengan keadaan paket, tiket sama, op sesuai target, MODIFY punya
  minimal satu field, op lain tanpa field, `entry`/`pending_expiry_min` hanya untuk pending.
- Posisi, `sl` baru: tidak lebih lebar dari SL sekarang dan `≤ harga − d`.
- Posisi, `tp3` baru: `≥ harga + d` dan `tp3 − open ≤ max_reward_r × |open − initial_sl|`.
- Posisi, `tp1`/`sl_after_tp1` hanya bila `plan.step < 1`; `tp2`/`sl_after_tp2` hanya bila
  `plan.step < 2` (`STEP_DONE`). Level tersisa (SL, TP1 bila belum, TP2 bila belum, TP3)
  harus maju searah trade (`LADDER_ORDER`); `sl_after_tp1` di antara SL dan `tp1 − d`,
  `sl_after_tp2` di antara langkah sebelumnya dan `tp2 − d` (`SL_STEP_INVALID`).
- Posisi, `time_limit_min`: `menit berjalan + 5 ≤ nilai ≤ limits.time_limit_max_minutes`.
- Pending, MODIFY: rencana gabungan (nilai baru, sisanya dari order) diperiksa dengan
  `plan_problems_v2`; order tanpa tangga butuh `tp1` dan `tp2` di permintaan
  (`LADDER_MISSING`); lot order > `volume_min` dan jarak stop baru > jarak stop sekarang →
  `MANAGE_SIZE`.

- [ ] **Step 1: Tulis tes yang gagal**

```python
"""Managing a resting order or an open position."""

from __future__ import annotations

from typing import Any

import pytest

from app.v6.deliberation.management import build_action, manage_problems
from app.v6.schemas.operator_plan import ManageRequest

from . import operator_fixtures_v6 as of

NOW = float(of.CREATED + 10)
ACTION_ID = "m3a7q2z5k6pw"


def request(target: str = "position", ticket: int = 91, op: str = "MODIFY",
            **fields: Any) -> ManageRequest:
    return ManageRequest.model_validate({"target": target, "ticket": ticket, "op": op,
                                         **fields})


def codes(found: tuple[str, ...]) -> list[str]:
    return [text.split(":", 1)[0] for text in found]


POSITION = of.managed_packet("position")   # buy 4533.35, SL 4526.35, bid 4535.18
PENDING = of.managed_packet("pending")     # BUY_LIMIT 4531.35, SL 4524.35


@pytest.mark.parametrize("fields", [
    {"sl": 4530.0}, {"sl": 4526.35}, {"tp3": 4545.0}, {"time_limit_min": 200},
    {"tp1": 4538.0, "sl_after_tp1": 4534.0}, {"sl_after_tp2": 4538.0},
])
def test_valid_position_changes(fields: dict[str, Any]) -> None:
    assert manage_problems(request(**fields), POSITION) == ()


@pytest.mark.parametrize(("fields", "code"), [
    ({"sl": 4525.0}, "SL_WIDER"),
    ({"sl": 4535.0}, "TOO_CLOSE"),
    ({"tp3": 4535.3}, "TOO_CLOSE"),
    ({"tp3": 4570.0}, "REWARD_TOO_LARGE"),
    ({"time_limit_min": 12}, "TIME_LIMIT_RANGE"),
    ({"tp1": 4544.0}, "LADDER_ORDER"),
    ({"sl_after_tp1": 4538.8}, "SL_STEP_INVALID"),
    ({"sl_after_tp2": 4534.0}, "SL_STEP_INVALID"),
    ({"entry": 4530.0}, "MANAGE_SHAPE"),
])
def test_refused_position_changes(fields: dict[str, Any], code: str) -> None:
    assert code in codes(manage_problems(request(**fields), POSITION))


def test_an_executed_step_cannot_change() -> None:
    stepped = of.managed_packet("position", position=of.position_block(
        plan=of.plan_block(step=1), sl=4534.0))
    assert "STEP_DONE" in codes(manage_problems(request(tp1=4538.0), stepped))
    assert manage_problems(request(tp2=4544.0), stepped) == ()


@pytest.mark.parametrize(("document", "code"), [
    ({"target": "pending", "ticket": 91, "op": "KEEP"}, "MANAGE_SHAPE"),
    ({"target": "position", "ticket": 92, "op": "KEEP"}, "MANAGE_TICKET"),
    ({"target": "position", "ticket": 91, "op": "CANCEL"}, "MANAGE_SHAPE"),
    ({"target": "position", "ticket": 91, "op": "MODIFY"}, "MANAGE_SHAPE"),
    ({"target": "position", "ticket": 91, "op": "CLOSE", "sl": 4530.0}, "MANAGE_SHAPE"),
])
def test_shape(document: dict[str, Any], code: str) -> None:
    found = manage_problems(ManageRequest.model_validate(document), POSITION)
    assert code in codes(found)


def test_pending_changes() -> None:
    fine = request("pending", 77, entry=4530.35, sl=4523.85, tp1=4534.5, tp2=4540.0,
                   tp3=4546.0, sl_after_tp1=4531.0, sl_after_tp2=4534.5,
                   pending_expiry_min=20)
    assert manage_problems(fine, PENDING) == ()
    passive = request("pending", 77, entry=4536.0)
    assert "LIMIT_NOT_PASSIVE" in codes(manage_problems(passive, PENDING))
    assert manage_problems(request("pending", 77, op="CANCEL"), PENDING) == ()


def test_a_wider_stop_on_a_bigger_order_is_refused() -> None:
    bigger = of.managed_packet("pending", pending_order=of.pending_block(lots=0.02))
    wider = request("pending", 77, sl=4523.35)
    assert "MANAGE_SIZE" in codes(manage_problems(wider, bigger))


def test_a_pending_order_without_a_ladder_needs_one() -> None:
    bare = of.managed_packet("pending", pending_order=of.pending_block(plan=None))
    assert "LADDER_MISSING" in codes(manage_problems(request("pending", 77, sl=4525.0), bare))


def test_actions() -> None:
    new_id = lambda: ACTION_ID  # noqa: E731
    assert build_action(request(op="KEEP"), POSITION, now=NOW, new_id=new_id) is None
    close = build_action(request(op="CLOSE"), POSITION, now=NOW, new_id=new_id)
    assert (close.command, close.ticket, close.intent_id, close.sl) == (
        "CLOSE_POSITION", 91, "k7w2m4pq3xza", 0.0)
    modify = build_action(request(sl=4530.004, time_limit_min=200), POSITION, now=NOW,
                          new_id=new_id)
    assert (modify.command, modify.sl, modify.tp, modify.tp1, modify.barrier_s) == (
        "MODIFY_POSITION", 4530.0, 4549.0, 4539.0, 12000)
    assert modify.issued_at == int(NOW) and modify.cycle_id == POSITION.cycle_id
    pending = build_action(request("pending", 77, pending_expiry_min=20), PENDING, now=NOW,
                           new_id=new_id)
    assert (pending.command, pending.price, pending.sl, pending.tp) == (
        "MODIFY_PENDING", 4531.35, 4524.35, 4547.0)
    assert pending.expiry_epoch == of.BAR_CLOSE + 1200
    assert pending.payload()["price"] == 4531.35
    assert build_action(request("pending", 77, op="CANCEL"), PENDING, now=NOW,
                        new_id=new_id) is None
```

Angka fixture: paket posisi memakai bid 4535.18, ask 4535.35, `modify_distance` 0.37,
`max_reward_r` 5.0, `time_limit_max_minutes` 240, `minutes_open` 10, open 4533.35,
SL 4526.35, TP 4549.0, rencana TP1 4539.0, TP2 4543.0, SL+ 4535.5 / 4539.0, langkah 0.

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_management.py`
Expected: FAIL.

- [ ] **Step 3: Tambah `ManagementAction` di `publication.py`**

```python
ACTION_PAYLOAD_FIELDS: Final[tuple[str, ...]] = (
    "sl", "tp", "tp1", "tp2", "sl_after_tp1", "sl_after_tp2", "price", "expiry_epoch",
    "barrier_s")


@dataclass(frozen=True)
class ManagementAction:
    """A signed-command-to-be: the full values after a CLOSE or MODIFY (0 = none)."""

    action_id: str
    command: str
    ticket: int
    intent_id: str
    cycle_id: str
    issued_at: int
    sl: float = 0.0
    tp: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    sl_after_tp1: float = 0.0
    sl_after_tp2: float = 0.0
    price: float = 0.0
    expiry_epoch: int = 0
    barrier_s: int = 0

    def payload(self) -> dict[str, float | int]:
        return {name: getattr(self, name) for name in ACTION_PAYLOAD_FIELDS}
```

- [ ] **Step 4: Tulis `management.py`**

```python
"""
Managing the resting order or the open position of a packet (spec section 2.3).

`manage_problems` judges a ManageRequest against the packet it answers, with the prices
the agent saw; `build_action` turns an accepted CLOSE or MODIFY into the full
ManagementAction the EA receives. The EA checks the same rules again against its live
quote, so a packet that went stale can at worst be refused there.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from ..schemas.intent import new_intent_id
from ..schemas.operator import OperatorPacket
from ..schemas.operator_plan import (
    OPS_BY_TARGET, PENDING_ONLY_FIELDS, EntryPlanV2, ManageRequest, PacketPendingOrder,
    PacketPosition,
)
from .plan_rules import bounds_from_packet, plan_problems_v2
from .publication import ManagementAction

MANAGE_SHAPE: Final[str] = "MANAGE_SHAPE"
MANAGE_TICKET: Final[str] = "MANAGE_TICKET"
SL_WIDER: Final[str] = "SL_WIDER"
TOO_CLOSE: Final[str] = "TOO_CLOSE"
REWARD_TOO_LARGE: Final[str] = "REWARD_TOO_LARGE"
STEP_DONE: Final[str] = "STEP_DONE"
LADDER_ORDER: Final[str] = "LADDER_ORDER"
SL_STEP_INVALID: Final[str] = "SL_STEP_INVALID"
TIME_LIMIT_RANGE: Final[str] = "TIME_LIMIT_RANGE"
MANAGE_SIZE: Final[str] = "MANAGE_SIZE"
LADDER_MISSING: Final[str] = "LADDER_MISSING"
MIN_EXTENSION_MIN: Final[int] = 5
SECONDS_PER_MINUTE: Final[int] = 60
PRICE_EPSILON: Final[float] = 1e-9
PLAN_TYPES: Final[dict[str, str]] = {"BUY_LIMIT": "LIMIT", "SELL_LIMIT": "LIMIT",
                                     "BUY_STOP": "STOP", "SELL_STOP": "STOP"}


def _problem(code: str, message: str) -> str:
    return f"{code}: {message}"


def _pick(new: float | int | None, old: float | int) -> float | int:
    return old if new is None else new


@dataclass(frozen=True)
class Levels:
    sl: float
    tp3: float
    tp1: float
    tp2: float
    s1: float
    s2: float
    minutes: int


def _position_levels(request: ManageRequest, position: PacketPosition) -> Levels:
    plan = position.plan
    return Levels(sl=_pick(request.sl, position.sl), tp3=_pick(request.tp3, position.tp),
                  tp1=_pick(request.tp1, plan.tp1), tp2=_pick(request.tp2, plan.tp2),
                  s1=_pick(request.sl_after_tp1, plan.sl_after_tp1),
                  s2=_pick(request.sl_after_tp2, plan.sl_after_tp2),
                  minutes=_pick(request.time_limit_min, plan.time_limit_min))


def _ticket(packet: OperatorPacket) -> int:
    if packet.position is not None:
        return packet.position.ticket
    return 0 if packet.pending_order is None else packet.pending_order.ticket


def _shape_problems(request: ManageRequest, packet: OperatorPacket) -> list[str]:
    state = packet.state
    if state == "flat" or request.target != state:
        return [_problem(MANAGE_SHAPE, f"this packet manages the {state}")]
    changes = request.changes
    checks = (
        (request.ticket == _ticket(packet), MANAGE_TICKET,
         f"the packet's ticket is {_ticket(packet)}"),
        (request.op in OPS_BY_TARGET[state], MANAGE_SHAPE,
         f"{request.op} is not an op for a {state}"),
        (request.op != "MODIFY" or bool(changes), MANAGE_SHAPE,
         "MODIFY needs at least one field"),
        (request.op == "MODIFY" or not changes, MANAGE_SHAPE,
         f"{request.op} takes no fields"),
        (state == "pending" or not set(changes) & set(PENDING_ONLY_FIELDS), MANAGE_SHAPE,
         "entry and pending_expiry_min belong to a pending order"),
    )
    return [_problem(code, message) for ok, code, message in checks if not ok]


def _sl_tp_problems(request: ManageRequest, packet: OperatorPacket, sign: int,
                    price: float) -> list[str]:
    position, limits = packet.position, packet.limits
    d = limits.modify_distance
    problems = []
    if request.sl is not None:
        if sign * (request.sl - position.sl) < -PRICE_EPSILON:
            problems.append(_problem(SL_WIDER, "the stop may only move toward safety"))
        if sign * (price - request.sl) + PRICE_EPSILON < d:
            problems.append(_problem(TOO_CLOSE, f"the stop must stay {d} from the price"))
    if request.tp3 is not None:
        risk = abs(position.open_price - position.initial_sl)
        if sign * (request.tp3 - price) + PRICE_EPSILON < d:
            problems.append(_problem(TOO_CLOSE, f"tp3 must stay {d} beyond the price"))
        if sign * (request.tp3 - position.open_price) > limits.max_reward_r * risk + PRICE_EPSILON:
            problems.append(_problem(REWARD_TOO_LARGE,
                                     f"tp3 is beyond {limits.max_reward_r}R of the initial risk"))
    return problems


def _advancing(sign: int, levels: list[float]) -> bool:
    return all(sign * (b - a) > 0 for a, b in zip(levels, levels[1:]))


def _step_problems(request: ManageRequest, position: PacketPosition, new: Levels,
                   sign: int, d: float) -> list[str]:
    step = position.plan.step
    first = request.tp1 is not None or request.sl_after_tp1 is not None
    second = request.tp2 is not None or request.sl_after_tp2 is not None
    if (first and step >= 1) or (second and step >= 2):
        return [_problem(STEP_DONE, "an executed step cannot change")]
    if not (first or second):
        return []
    levels = [new.sl] + ([new.tp1] if step < 1 and new.tp1 > 0 else []) + (
        [new.tp2] if new.tp2 > 0 else []) + [new.tp3]
    problems = [] if _advancing(sign, levels) else [
        _problem(LADDER_ORDER, "sl, tp1, tp2 and tp3 must advance in the trade direction")]
    if step < 1 and new.s1 > 0 and not (sign * (new.s1 - new.sl) > 0
                                        and sign * (new.tp1 - new.s1) + PRICE_EPSILON >= d):
        problems.append(_problem(SL_STEP_INVALID, f"sl_after_tp1 must sit between sl and "
                                                  f"tp1 - {d}"))
    floor = new.s1 if step < 1 and new.s1 > 0 else new.sl
    if new.s2 > 0 and not (sign * (new.s2 - floor) >= 0 and sign * (new.s2 - new.sl) > 0
                           and sign * (new.tp2 - new.s2) + PRICE_EPSILON >= d):
        problems.append(_problem(SL_STEP_INVALID, f"sl_after_tp2 must sit between the "
                                                  f"stop before it and tp2 - {d}"))
    return problems


def _position_problems(request: ManageRequest, packet: OperatorPacket) -> list[str]:
    position, limits = packet.position, packet.limits
    sign = 1 if position.side == "buy" else -1
    price = packet.market.bid if sign > 0 else packet.market.ask
    new = _position_levels(request, position)
    problems = _sl_tp_problems(request, packet, sign, price)
    problems += _step_problems(request, position, new, sign, limits.modify_distance)
    if request.time_limit_min is not None:
        low = math.ceil(position.minutes_open) + MIN_EXTENSION_MIN
        high = limits.time_limit_max_minutes
        if not low <= request.time_limit_min <= high:
            problems.append(_problem(TIME_LIMIT_RANGE, f"time_limit_min must be {low}-{high}"))
    return problems


def _pending_plan(request: ManageRequest, order: PacketPendingOrder,
                  packet: OperatorPacket) -> EntryPlanV2:
    plan = order.plan
    side = "buy" if order.order_type.startswith("BUY") else "sell"
    return EntryPlanV2(
        side=side, order_type=PLAN_TYPES[order.order_type],
        entry=_pick(request.entry, order.price), sl=_pick(request.sl, order.sl),
        tp1=_pick(request.tp1, plan.tp1 if plan else 0.0),
        tp2=_pick(request.tp2, plan.tp2 if plan else 0.0),
        tp3=_pick(request.tp3, order.tp),
        sl_after_tp1=_pick(request.sl_after_tp1, plan.sl_after_tp1 if plan else 0.0) or None,
        sl_after_tp2=_pick(request.sl_after_tp2, plan.sl_after_tp2 if plan else 0.0) or None,
        time_limit_min=_pick(request.time_limit_min,
                             plan.time_limit_min if plan and plan.time_limit_min
                             else packet.limits.time_limit_min_minutes),
        pending_expiry_min=_pick(request.pending_expiry_min,
                                 packet.limits.pending_expiry_min_minutes),
        lots=order.lots)


def _pending_problems(request: ManageRequest, packet: OperatorPacket) -> list[str]:
    order = packet.pending_order
    if request.op != "MODIFY":
        return []
    has_ladder = order.plan is not None and order.plan.tp1 > 0 and order.plan.tp2 > 0
    if not has_ladder and (request.tp1 is None or request.tp2 is None):
        return [_problem(LADDER_MISSING, "this order has no ladder: give tp1 and tp2")]
    merged = _pending_plan(request, order, packet)
    problems = [_problem(item.code, item.message)
                for item in plan_problems_v2(merged, bounds_from_packet(packet))]
    wider = abs(merged.entry - merged.sl) > abs(order.price - order.sl) + PRICE_EPSILON
    if wider and order.lots > packet.limits.volume_min + PRICE_EPSILON:
        problems.append(_problem(MANAGE_SIZE, "a wider stop needs the minimum lot; "
                                              "cancel and enter again instead"))
    return problems


def manage_problems(request: ManageRequest, packet: OperatorPacket) -> tuple[str, ...]:
    """Every reason the request does not fit the packet (empty when it does)."""
    shape = _shape_problems(request, packet)
    if shape or request.op != "MODIFY":
        return tuple(shape)
    if packet.state == "position":
        return tuple(_position_problems(request, packet))
    return tuple(_pending_problems(request, packet))


def _snap(value: float, packet: OperatorPacket) -> float:
    tick, digits = packet.limits.tick_size, packet.limits.digits
    return round(round(value / tick) * tick, digits) if value > 0 else 0.0


def build_action(request: ManageRequest, packet: OperatorPacket, *, now: float,
                 new_id: Callable[[], str] = new_intent_id) -> ManagementAction | None:
    """The EA command for an accepted CLOSE or MODIFY; None for KEEP and CANCEL."""
    if request.op in ("KEEP", "CANCEL"):
        return None
    base = dict(action_id=new_id(), cycle_id=packet.cycle_id, issued_at=int(now))
    if packet.state == "position":
        position = packet.position
        if request.op == "CLOSE":
            return ManagementAction(command="CLOSE_POSITION", ticket=position.ticket,
                                    intent_id=position.intent_id, **base)
        new = _position_levels(request, position)
        return ManagementAction(
            command="MODIFY_POSITION", ticket=position.ticket, intent_id=position.intent_id,
            sl=_snap(new.sl, packet), tp=_snap(new.tp3, packet), tp1=_snap(new.tp1, packet),
            tp2=_snap(new.tp2, packet), sl_after_tp1=_snap(new.s1, packet),
            sl_after_tp2=_snap(new.s2, packet),
            barrier_s=int(new.minutes) * SECONDS_PER_MINUTE, **base)
    order = packet.pending_order
    merged = _pending_plan(request, order, packet)
    expiry = (order.expiration_epoch if request.pending_expiry_min is None
              else packet.bar_close_epoch + request.pending_expiry_min * SECONDS_PER_MINUTE)
    return ManagementAction(
        command="MODIFY_PENDING", ticket=order.ticket, intent_id=order.intent_id,
        price=_snap(merged.entry, packet), sl=_snap(merged.sl, packet),
        tp=_snap(merged.tp3, packet), tp1=_snap(merged.tp1, packet),
        tp2=_snap(merged.tp2, packet), sl_after_tp1=_snap(merged.sl_after_tp1 or 0.0, packet),
        sl_after_tp2=_snap(merged.sl_after_tp2 or 0.0, packet), expiry_epoch=expiry,
        barrier_s=merged.time_limit_min * SECONDS_PER_MINUTE, **base)
```

Catatan untuk implementer:
- `_pending_plan` memakai `or None` hanya untuk langkah SL+ (level 0 berarti tidak ada);
  `EntryPlanV2.tp1`/`tp2` wajib `> 0`, jadi `_pending_problems` memeriksa `LADDER_MISSING`
  sebelum membangun rencana gabungan.
- Bila `management.py` melewati 400 baris atau fungsi melewati 50 baris, pecah bagian
  posisi ke `management_position.py`.

- [ ] **Step 5: Jalankan tes**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_management.py --cov=app/v6/deliberation/management --cov-report=term-missing`
Expected: PASS, coverage 100% (tambah kasus sell dan kasus `sl_after_tp2` tanpa langkah 1
bila baris belum tercakup).

- [ ] **Step 6: Commit**

```bash
git add adapter/app/v6/deliberation/management.py adapter/app/v6/deliberation/publication.py adapter/tests/v6/test_management.py
git commit -m "feat: validate V6 management requests and build EA actions"
```

---

## Task 11: Engine — routing keadaan, ENTER berencana dan MANAGE

**Files:**
- Create: `adapter/app/v6/deliberation/trade_state.py` (menggantikan `pending_review.py`,
  yang dihapus)
- Create: `adapter/app/v6/deliberation/operator_flow.py` (mixin berisi jalur operator yang
  dipindah dari `engine.py`)
- Modify: `adapter/app/v6/deliberation/engine.py` (`EngineDeps.plans`, `EngineDeps.bias`,
  `_decide`, `_resolve`, `_enter`, pewarisan mixin)
- Modify: `adapter/app/v6/deliberation/publication.py` (`PublishRequest.plan`,
  `ManageDispatch`, `ManageOutcome`, `IntentPort.manage`)
- Modify: `adapter/app/v6/cycle_codes.py` (`HoldReason.MANAGE_KEPT`, `MANAGE_SENT`,
  `MANAGE_REFUSED`; hapus `PENDING_KEPT`, `PENDING_CANCELLED`)
- Test: `adapter/tests/v6/test_engine_management.py`; hapus `tests/v6/test_engine_review_lots.py`
  (kasus lot dipindah ke tes baru dalam bentuk v3); perbarui `FakePublisher` di
  `tests/v6/test_engine_operator.py`

**Interfaces:**
- Consumes: Task 3–10
- Produces:
  - `trade_state.trade_state(context) -> PacketState`,
    `trade_state.wants_management(context, gates) -> bool`,
    `trade_state.managed_intent_id(context) -> str | None`,
    `trade_state.BiasMemory` (`remember(bias, at)`, `latest() -> tuple[M15Bias | None, int | None]`),
    `trade_state.PlanReader` (Protocol: `intent(intent_id) -> IntentRecord | None`,
    `last_action(session_id) -> ActionRow | None`)
  - `EngineDeps.plans: PlanReader | None = None`,
    `EngineDeps.bias: BiasMemory = field(default_factory=BiasMemory)`
  - `publication.PublishRequest.plan: TradePlan | None = None`
  - `publication.ManageDispatch(request, action, cycle_id, agent, session_id)`
  - `publication.ManageOutcome(sent: bool, code: str, detail: str)`
  - `IntentPort.manage(dispatch: ManageDispatch) -> ManageOutcome`
  - `HoldReason.MANAGE_KEPT = "APP-V6-MANAGE-KEPT"`,
    `HoldReason.MANAGE_SENT = "APP-V6-MANAGE-SENT"`,
    `HoldReason.MANAGE_REFUSED = "APP-V6-MANAGE-REFUSED"`

Alur baru di engine:
1. Gate gagal, backend operator, `wants_management` → jalur operator dengan
   `state = trade_state(context)`.
2. Gate lolos, backend operator → jalur operator dengan `state="flat"` (seperti sekarang).
3. Sebelum membangun paket: `record` diambil lewat `deps.plans.intent(managed_intent_id)`
   (di thread), `last_action` lewat `deps.plans.last_action(session_id)`, bias lewat
   `deps.bias.latest()`.
4. Keputusan diterima: bias diingat (`deps.bias.remember(decision.bias,
   packet.bar_close_epoch)`).
5. `state != "flat"` → `_manage_result`; `state == "flat"` → `_resolve` seperti sekarang,
   dengan cabang v3: `decision.plan` → kandidat lewat `plan_candidate`, exit plan lewat
   `assess_candidate(tp_r_multiple=reward_r)`, lalu `ladder_after_exit`; lot = `plan.lots`;
   `trade_plan(plan)` ikut ke `_enter` dan `PublishRequest.plan`.

`_manage_result`:
- `manage is None` atau `op == "KEEP"` → HOLD `MANAGE_KEPT`.
- mode bukan execute atau tanpa publisher → HOLD `MANAGE_KEPT` dengan detail
  `"manage: <op> recorded only (shadow)"`.
- selain itu `build_action(...)` lalu `publisher.manage(ManageDispatch(...))` → HOLD
  `MANAGE_SENT` bila `outcome.sent`, selain itu HOLD `MANAGE_REFUSED`.

- [ ] **Step 1: Tulis tes yang gagal**

Tes memakai `rig`, `FakePublisher`, `WAIT_STEPS`, `WAIT_STEP_S` dari
`tests/v6/test_engine_operator.py`. `FakePublisher` diperluas:

```python
class FakePublisher:
    def __init__(self, outcome: PublishOutcome,
                 managed: ManageOutcome = ManageOutcome(True, "QUEUED", "queued")) -> None:
        self.outcome, self.managed = outcome, managed
        self.requests: list[PublishRequest] = []
        self.cancels: list[str] = []
        self.dispatches: list[ManageDispatch] = []

    async def publish(self, request: PublishRequest) -> PublishOutcome:
        self.requests.append(request)
        return self.outcome

    async def cancel_pending(self, reason: str) -> tuple[str, ...]:
        self.cancels.append(reason)
        return ()

    async def manage(self, dispatch: ManageDispatch) -> ManageOutcome:
        self.dispatches.append(dispatch)
        return self.managed
```

```python
"""The engine offers management packets and sends management actions."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest

from app.v6.cycle_codes import HoldReason
from app.v6.deliberation.publication import ManageOutcome, PublishOutcome

from . import engine_fixtures_v6 as ef
from .cycle_fixtures_v6 import pa_payload
from .test_engine_operator import WAIT_STEP_S, WAIT_STEPS, FakePublisher, rig

POSITION = {"ticket": 91, "magic": 250570, "side": "buy", "volume": 0.01,
            "price_open": 4296.5, "sl": 4289.0, "tp": 4310.0, "profit": 3.5, "swap": 0.0,
            "open_epoch": ef.AS_OF - 1200, "comment": "Q6:k7w2m4pq3xza",
            "mae_points": 80.0, "mfe_points": 390.0}
RESTING = {"ticket": 77, "magic": 250570, "order_type": "BUY_LIMIT", "price": 4296.5,
           "sl": 4289.0, "tp": 4310.0, "volume": 0.01, "expiration_epoch": ef.AS_OF + 900,
           "comment": "Q6:k7w2m4pq3xza"}
Edit = Callable[[dict[str, Any]], dict[str, Any]]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def published() -> FakePublisher:
    return FakePublisher(PublishOutcome(intent_id="k7w2m4pq3xza", code="BOOK_PUBLISHED"))


async def answer(setup, edit: Edit, request) -> tuple[Any, dict[str, Any]]:
    seen: dict[str, Any] = {}

    async def agent() -> None:
        for _ in range(WAIT_STEPS):
            if setup.queue.pending is not None:
                break
            await asyncio.sleep(WAIT_STEP_S)
        packet = setup.queue.pending.packet.model_dump(mode="json")
        seen.update(packet)
        decision = edit({**json.loads(json.dumps(packet["decision_template"])),
                         "agent": "claude_code"})
        seen["submit"] = setup.queue.submit(json.dumps(decision).encode("utf-8"),
                                            setup.clock.now_epoch())

    outcome, _ = await asyncio.gather(setup.engine.run(request), agent())
    return outcome.result, seen


def position_request(**payload: Any):
    return ef.request(ef.engine_snapshot(positions=[POSITION], **payload))


def keep(document: dict[str, Any]) -> dict[str, Any]:
    return document


def close(document: dict[str, Any]) -> dict[str, Any]:
    return {**document, "manage": {"target": "position", "ticket": 91, "op": "CLOSE"}}


@pytest.mark.anyio
async def test_an_open_position_is_kept() -> None:
    setup = rig(publisher=published())
    result, packet = await answer(setup, keep, position_request())
    assert (packet["state"], packet["position"]["ticket"]) == ("position", 91)
    assert (result.status, result.hold_reason) == ("HOLD", HoldReason.MANAGE_KEPT)


@pytest.mark.anyio
async def test_a_close_is_sent_to_the_publisher() -> None:
    publisher = published()
    setup = rig(publisher=publisher)
    result, _ = await answer(setup, close, position_request())
    assert (result.status, result.hold_reason) == ("HOLD", HoldReason.MANAGE_SENT)
    dispatch = publisher.dispatches[0]
    assert (dispatch.request.op, dispatch.action.command, dispatch.action.ticket) == (
        "CLOSE", "CLOSE_POSITION", 91)


@pytest.mark.anyio
async def test_a_refused_action_holds_with_its_reason() -> None:
    publisher = FakePublisher(PublishOutcome(), ManageOutcome(False, "SESSION_NOT_ARMED", "no"))
    setup = rig(publisher=publisher)
    result, _ = await answer(setup, close, position_request())
    assert result.hold_reason == HoldReason.MANAGE_REFUSED


@pytest.mark.anyio
async def test_shadow_mode_only_records_the_request() -> None:
    publisher = published()
    setup = rig(publisher=publisher, mode="shadow")
    result, _ = await answer(setup, close, position_request())
    assert result.hold_reason == HoldReason.MANAGE_KEPT and publisher.dispatches == []


@pytest.mark.anyio
async def test_a_pending_cancel_is_dispatched_without_an_action() -> None:
    publisher = published()
    setup = rig(publisher=publisher)
    cancel = lambda d: {**d, "manage": {"target": "pending", "ticket": 77, "op": "CANCEL"}}  # noqa: E731
    result, packet = await answer(setup, cancel,
                                  ef.request(ef.engine_snapshot(pending_orders=[RESTING])))
    assert packet["state"] == "pending"
    assert (publisher.dispatches[0].request.op, publisher.dispatches[0].action) == (
        "CANCEL", None)
    assert result.hold_reason == HoldReason.MANAGE_SENT


@pytest.mark.anyio
async def test_no_management_packet_while_halted() -> None:
    setup = rig(publisher=published())
    request = ef.request(ef.engine_snapshot(positions=[POSITION]), halt_sources=("file",))
    outcome = await setup.engine.run(request)
    assert outcome.result.hold_reason == HoldReason.HALTED
    assert setup.queue.pending is None


def stop_entry(document: dict[str, Any]) -> dict[str, Any]:
    limits = document["_limits"]
    entry = round(limits["buy_stop_min"] + 1.0, 2)
    plan = {"side": "buy", "order_type": "STOP", "entry": entry, "sl": round(entry - 7.0, 2),
            "tp1": round(entry + 4.0, 2), "tp2": round(entry + 8.0, 2),
            "tp3": round(entry + 14.0, 2), "sl_after_tp1": round(entry + 0.5, 2),
            "sl_after_tp2": round(entry + 4.0, 2), "time_limit_min": 120,
            "pending_expiry_min": 30, "lots": 0.01}
    views = {**document["views"], "price_action": pa_payload(document["_entry_id"],
                                                             conviction=0.8)}
    clean = {key: value for key, value in document.items() if not key.startswith("_")}
    return {**clean, "action": "ENTER", "entry_plan": plan, "views": views,
            "m15_bias": {"direction": "up", "levels": [entry], "scenario": "breakout"}}
```

Karena `edit` hanya menerima templat, tambahkan di `answer(...)` dua kunci bantu sebelum
`edit` dipanggil: `"_limits": packet["limits"]`, `"_entry_id": packet["limits"]["agent_entry_id"]`
(dibuang oleh `stop_entry`; `keep`/`close` tidak memakainya, jadi buang juga di sana dengan
pola yang sama sebelum submit).

```python
@pytest.mark.anyio
async def test_a_stop_entry_is_published_with_its_plan_and_the_bias_is_remembered() -> None:
    publisher = published()
    setup = rig(publisher=publisher)
    result, _ = await answer(setup, stop_entry, ef.request())
    assert result.status == "ENTER"
    request = publisher.requests[0]
    assert (request.plan.order_type, request.plan.time_limit_s) == ("STOP", 7200)
    assert request.plan.sl_after_tp2 > request.plan.sl_after_tp1 > 0
    bias, at = setup.engine.deps.bias.latest()
    assert bias.direction == "up" and at == ef.AS_OF


@pytest.mark.anyio
async def test_the_next_packet_echoes_the_bias_and_the_last_action() -> None:
    setup = rig(publisher=published())
    await answer(setup, stop_entry, ef.request())
    later = ef.request(ef.engine_snapshot("snap-eng-0002", bar_open=ef.T_BAR + 900),
                       received_at=ef.RECEIVED + 900)
    setup.clock.advance(900)
    _, packet = await answer(setup, keep, later)
    assert packet["last_bias"]["direction"] == "up"
```

Catatan fixture:
- `rig(...)` di `test_engine_operator.py` menerima `publisher=` dan perlu parameter baru
  `mode="execute"` serta `plans=` (default: reader palsu yang mengembalikan `None`).
- Kalau `FakeClock` belum punya `advance`, pakai setter yang ada (`clock.set(...)`).

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_engine_management.py`
Expected: FAIL.

- [ ] **Step 3: Implementasi `trade_state.py`**

```python
"""
The trade state of a cycle and when the agent manages instead of entering (spec 1, 2.3).

A V6 position or resting order fails the OCCUPANCY gate; if nothing else that makes the
system unsafe failed as well, the agent gets a management packet. A halt, a breaker,
stale data or a failed account policy are handled by the runtime (FLATTEN or
CANCEL_PENDING), never by the agent.
"""

from __future__ import annotations

from typing import Final, Protocol

from ..cycle_codes import (
    GATE_ACCOUNT_POLICY, GATE_BREAKER, GATE_CLOCK_SKEW, GATE_HALTED, GATE_OCCUPANCY,
    GATE_SNAPSHOT_AGE, GATE_SPEC, GATE_WARMUP,
)
from ..cycle_types import MarketContext
from ..ledger_actions import ActionRow
from ..ledger_intents import IntentRecord
from ..risk.gates import failed_codes
from ..schemas.intent import intent_id_from_comment
from ..schemas.operator_plan import M15Bias, PacketState
from ..types import GateResult

MANAGEMENT_BLOCKING_GATES: Final[frozenset[str]] = frozenset({
    GATE_HALTED, GATE_WARMUP, GATE_ACCOUNT_POLICY, GATE_SNAPSHOT_AGE, GATE_CLOCK_SKEW,
    GATE_SPEC, GATE_BREAKER})


def trade_state(context: MarketContext) -> PacketState:
    if context.positions:
        return "position"
    return "pending" if context.pending_orders else "flat"


def wants_management(context: MarketContext, gates: tuple[GateResult, ...]) -> bool:
    failed = frozenset(failed_codes(gates))
    return (trade_state(context) != "flat" and GATE_OCCUPANCY in failed
            and not failed & MANAGEMENT_BLOCKING_GATES)


def managed_intent_id(context: MarketContext) -> str | None:
    if context.positions:
        return intent_id_from_comment(context.positions[0].comment)
    if context.pending_orders:
        return intent_id_from_comment(context.pending_orders[0].comment)
    return None


class PlanReader(Protocol):
    """Blocking reads of what the adapter stored about a trade (run in a thread)."""

    def intent(self, intent_id: str) -> IntentRecord | None: ...

    def last_action(self, session_id: str) -> ActionRow | None: ...


class BiasMemory:
    """The newest M15 bias an agent gave, kept in memory (a restart forgets it)."""

    def __init__(self) -> None:
        self._bias: M15Bias | None = None
        self._at: int | None = None

    def remember(self, bias: M15Bias, at: int) -> None:
        self._bias, self._at = bias, at

    def latest(self) -> tuple[M15Bias | None, int | None]:
        return self._bias, self._at
```

Implementasi `PlanReader` di `app/v6/runtime/wiring.py` (atau `container.py`):

```python
@dataclass(frozen=True)
class LedgerPlans:
    ledger: LedgerCycles

    def intent(self, intent_id: str) -> IntentRecord | None:
        return self.ledger.intents.get(intent_id)

    def last_action(self, session_id: str) -> ActionRow | None:
        return self.ledger.actions.latest(session_id)
```

dan `build_engine(..., plans=LedgerPlans(container.ledger_cycles))`.

- [ ] **Step 4: Implementasi `operator_flow.py` dan perubahan `engine.py`**

Pindahkan `_operator_path`, `_operator_decide`, `_agent_item` ke
`class OperatorFlow` di `operator_flow.py`; `DeliberationEngine(OperatorFlow)` mewarisinya.
Metode mixin memakai `self._deps`, `self._now()`, `self._hold(...)`, `self._timed(...)`,
`self._with_panel(...)`, `self._late(...)`, `self._deadline(...)`, `self._resolve(...)`
dari engine. Isi baru:

```python
class OperatorFlow:
    """The operator backend's part of a cycle (mixed into DeliberationEngine)."""

    async def _operator_path(self, queue: OperatorQueue, draft: CycleDraft, tier0: Tier0,
                             state: PacketState = "flat") -> CycleOutcome:
        request = draft.request
        if request.session_id is None:
            return self._hold(draft, HoldReason.NO_SESSION, DETAIL_NO_SESSION)
        if self._late(request):
            return self._hold(draft, HoldReason.LATE, DETAIL_LATE)
        return await self._operator_decide(queue, draft, tier0, state)

    async def _facts(self, context: MarketContext, session_id: str,
                     state: PacketState) -> tuple[IntentRecord | None, ActionRow | None]:
        plans = self._deps.plans
        if plans is None:
            return None, None
        intent_id = managed_intent_id(context) if state != "flat" else None
        record = None if intent_id is None else await asyncio.to_thread(plans.intent, intent_id)
        return record, await asyncio.to_thread(plans.last_action, session_id)

    async def _operator_decide(self, queue: OperatorQueue, draft: CycleDraft, tier0: Tier0,
                               state: PacketState) -> CycleOutcome:
        deps, request = self._deps, draft.request
        record, last_action = await self._facts(tier0.context, request.session_id, state)
        bias, bias_at = deps.bias.latest()
        packet_request = PacketRequest(
            context=tier0.context, gates=tier0.gates, offered=tier0.pool.offered,
            baseline=tier0.baseline.views, remaining_loss_usd=tier0.breakers.remaining_loss_usd,
            session_id=request.session_id, armed=request.session_armed, now=self._now(),
            deadline_epoch=self._deadline(request), state=state, record=record,
            last_action=last_action, last_bias=bias, last_bias_at=bias_at)
        outcome = await operator_round(queue, packet_request, deps.settings, tier0.baseline,
                                       deps.clock)
        if isinstance(outcome, PacketRefusal):
            return self._hold(self._timed(draft), outcome.hold_reason,
                              f"{outcome.code}: {outcome.detail}")
        draft = self._with_panel(draft, tier0, outcome.panel)
        decision = outcome.decision
        if decision is None:
            return self._hold(draft, HoldReason.OPERATOR_TIMEOUT, outcome.detail)
        if decision.bias is not None:
            deps.bias.remember(decision.bias, outcome.packet.bar_close_epoch)
        if state != "flat":
            return await self._manage_result(draft, outcome)
        return await self._resolve(draft, tier0, outcome.panel, outcome)

    async def _manage_result(self, draft: CycleDraft, rnd: OperatorRound) -> CycleOutcome:
        decision, packet = rnd.decision, rnd.packet
        request = decision.manage
        if request is None or request.op == "KEEP":
            return self._hold(draft, HoldReason.MANAGE_KEPT, f"manage: keep the {packet.state}")
        publisher = self._deps.publisher
        if publisher is None or self._deps.settings.mode != EXECUTE_MODE:
            return self._hold(draft, HoldReason.MANAGE_KEPT,
                              f"manage: {request.op} recorded only (shadow)")
        action = build_action(request, packet, now=self._now())
        outcome = await publisher.manage(ManageDispatch(
            request=request, action=action, cycle_id=packet.cycle_id,
            agent=decision.agent, session_id=draft.request.session_id or ""))
        reason = HoldReason.MANAGE_SENT if outcome.sent else HoldReason.MANAGE_REFUSED
        return self._hold(draft, reason, f"manage: {outcome.detail}")

    def _plan_item(self, tier0: Tier0, packet: OperatorPacket,
                   plan: EntryPlanV2) -> CandidateAssessment:
        settings, context = self._deps.settings, tier0.context
        candidate = plan_candidate(plan, bounds_from_packet(packet), context.bar_open_epoch)
        item = assess_candidate(context, candidate, settings,
                                friction_price=cycle_friction(settings, context),
                                tp_r_multiple=candidate.features["reward_r"])
        if item.exit_plan is None:
            return item
        problem = ladder_after_exit(plan, item.exit_plan.tp)
        if problem is not None:
            return replace(item, exit_plan=None, refusal=Refusal(
                (PROBLEM_TP3_TRIMMED,), problem))
        return replace(item, verdict=VERDICT_CHOSEN)
```

`engine._resolve` memilih cabang v3:

```python
        decision = None if operator is None else operator.decision
        plan_v3 = None if decision is None else decision.plan
        lots = None if decision is None else (
            plan_v3.lots if plan_v3 is not None
            else decision.lots or tier0.context.spec.volume_min)
        trade = None if plan_v3 is None else trade_plan(plan_v3)
        item = tier0.pool.find(protocol.candidate_id)
        if item is None and plan_v3 is not None:
            item = self._plan_item(tier0, operator.packet, plan_v3)
        elif item is None and decision is not None and decision.entry_plan is not None:
            item = self._agent_item(tier0, operator.packet, decision.entry_plan)
        if item is not None and item.candidate.setup == AGENT_SETUP:
            draft = draft.update(candidates=draft.candidates + (item,))
            if item.exit_plan is None:
                codes = "" if item.refusal is None else ",".join(item.refusal.codes)
                return self._hold(draft, HoldReason.EXIT, DETAIL_AGENT_EXIT + codes)
        if item is None:
            raise ValueError("the protocol picked a candidate that was not offered")
        return await self._enter(draft, tier0, panel, protocol, agent, item, lots, trade)
```

dan `_enter(..., lots=None, trade=None)` meneruskan `plan=trade` ke `PublishRequest`.
`_review_result` dihapus. Pastikan `engine.py` dan `operator_flow.py` masing-masing
≤ 400 baris.

`publication.py`:

```python
@dataclass(frozen=True)
class PublishRequest:
    decision: ProtocolDecision
    candidate: Candidate
    exit_plan: ExitPlan
    sizing: SizingResult
    context: MarketContext
    agent: str
    plan: TradePlan | None = None


@dataclass(frozen=True)
class ManageDispatch:
    request: ManageRequest
    action: ManagementAction | None     # None for CANCEL
    cycle_id: str
    agent: str
    session_id: str


@dataclass(frozen=True)
class ManageOutcome:
    sent: bool
    code: str
    detail: str = ""


class IntentPort(Protocol):
    async def publish(self, request: PublishRequest) -> PublishOutcome: ...

    async def cancel_pending(self, reason: str) -> tuple[str, ...]: ...

    async def manage(self, dispatch: ManageDispatch) -> ManageOutcome:
        """Queue a management action (or CANCEL_PENDING) for the armed session."""
        ...
```

`cycle_codes.py`: ganti `PENDING_KEPT`/`PENDING_CANCELLED` dengan tiga kode `MANAGE_*`
(perbarui tabel/tes yang mendaftar semua `HoldReason`).

- [ ] **Step 5: Jalankan tes engine**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_engine_management.py tests/v6 -k "engine"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add adapter/app/v6/deliberation adapter/app/v6/cycle_codes.py adapter/app/v6/runtime/wiring.py adapter/app/v6/container.py adapter/tests/v6
git rm adapter/app/v6/deliberation/pending_review.py adapter/tests/v6/test_engine_review_lots.py adapter/tests/v6/test_review_packet_parts.py
git commit -m "feat: route V6 cycles by trade state and dispatch management decisions"
```

---

## Task 12: Runtime — papan aksi, publisher, balasan poll dan `/v6/action`

**Files:**
- Create: `adapter/app/v6/runtime/actions.py` (`ActionBoard`)
- Create: `adapter/app/v6/runtime/action_desk.py` (`ActionDesk`)
- Modify: `adapter/app/v6/runtime/publisher.py` (`manage`)
- Modify: `adapter/app/v6/runtime/poll_reply.py` (aksi di balasan)
- Modify: `adapter/app/v6/runtime/desk.py` / `wiring.py` / `container.py`
  (`actions`, `action_desk` di deps dan `container.parts`)
- Modify: `adapter/app/v6/runtime/watchdog.py` (langkah `STEP_ACTIONS` yang menandai aksi
  kedaluwarsa)
- Modify: `adapter/app/routes/v6_ea.py` (`POST /v6/action`)
- Test: `adapter/tests/v6/test_action_runtime.py`; tambahkan `/v6/action` ke daftar rute EA
  di tes keamanan (`grep -rn '"/v6/execution"' tests/`)

**Interfaces:**
- Consumes: `ManagementAction`, `ManageDispatch`, `ManageOutcome` (Task 10, 11);
  `ActionStore`, `IntentStore.update_plan/set_plan_step` (Task 6); `ActionReport`,
  field aksi `PollResponse` (Task 5)
- Produces:
  - `ActionBoard.queue(action, now)`, `for_poll(now) -> ManagementAction | None`,
    `settle(action_id) -> bool`, `pop_expired(now) -> ManagementAction | None`
  - `action_response(action, server_time) -> PollResponse`
  - `ActionDesk.apply(report: ActionReport, now: float) -> str` (blocking)
  - `IntentPublisher.manage(dispatch) -> ManageOutcome`
  - `REASON_AGENT_CANCEL = "AGENT_CANCEL"` pindah ke `publisher.py`

- [ ] **Step 1: Tulis tes yang gagal**

```python
"""Management actions: queued, served on polls, settled by EA reports."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.v6.clock import FakeClock
from app.v6.container import V6Container
from app.v6.deliberation.publication import ManageDispatch, ManagementAction
from app.v6.ledger_intents import NewIntent
from app.v6.runtime.action_desk import ActionDesk
from app.v6.runtime.actions import ActionBoard, action_response
from app.v6.runtime.publisher import IntentPublisher
from app.v6.schemas.intent import ActionReport
from app.v6.schemas.operator_plan import ManageRequest

from . import engine_fixtures_v6 as ef
from .desk_fixtures_v6 import demo_poll, desk_container, open_session

T0 = ef.RECEIVED
ACTION = ManagementAction(action_id="m3a7q2z5k6pw", command="MODIFY_POSITION", ticket=91,
                          intent_id="k7w2m4pq3xza", cycle_id="c-00000000000000bb",
                          issued_at=int(T0), sl=4361.0, tp=4378.0, tp2=4371.0,
                          sl_after_tp2=4366.0, barrier_s=10800)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def clock() -> FakeClock:
    return ef.clock_at()


@pytest.fixture
def container(tmp_path: Path, clock: FakeClock) -> Iterator[V6Container]:
    with desk_container(tmp_path, clock) as built:
        yield built


def test_the_board_repeats_an_action_until_settled_or_stale() -> None:
    board = ActionBoard()
    board.queue(ACTION, T0)
    assert board.for_poll(T0 + 1) == ACTION and board.for_poll(T0 + 2) == ACTION
    assert board.settle("m3a7q2z5k6pw") and board.for_poll(T0 + 3) is None
    assert not board.settle("m3a7q2z5k6pw")
    board.queue(ACTION, T0)
    assert board.for_poll(T0 + 31) is None
    assert board.pop_expired(T0 + 31) == ACTION and board.pop_expired(T0 + 32) is None


def test_the_poll_answer_carries_the_action() -> None:
    response = action_response(ACTION, int(T0) + 1)
    assert (response.command, response.action_ticket, response.action_sl2) == (
        "MODIFY_POSITION", 91, 4366.0)
    assert (response.action_barrier_s, response.has_intent) == (10800, False)


def dispatch(op: str = "MODIFY", action: ManagementAction | None = ACTION) -> ManageDispatch:
    request = ManageRequest(target="position", ticket=91, op=op,
                            sl=4361.0 if op == "MODIFY" else None)
    return ManageDispatch(request=request, action=action, cycle_id="c-00000000000000bb",
                          agent="claude_code", session_id="")


@pytest.mark.anyio
async def test_manage_needs_an_armed_session(container: V6Container) -> None:
    demo_poll(container)
    outcome = await IntentPublisher(container.parts.desk).manage(dispatch())
    assert not outcome.sent and container.parts.actions.for_poll(T0) is None


@pytest.mark.anyio
async def test_manage_queues_and_records(container: V6Container) -> None:
    demo_poll(container)
    session = open_session(container, armed=True)
    outcome = await IntentPublisher(container.parts.desk).manage(dispatch())
    assert outcome.sent and outcome.code == "MODIFY_POSITION"
    assert container.parts.actions.for_poll(T0) == ACTION
    row = container.ledger_cycles.actions.get("m3a7q2z5k6pw")
    assert (row.status, row.session_id, row.payload["tp"]) == (
        "PUBLISHED", session.session_id, 4378.0)


@pytest.mark.anyio
async def test_a_cancel_queues_cancel_pending(container: V6Container,
                                              clock: FakeClock) -> None:
    demo_poll(container)
    open_session(container, armed=True)
    outcome = await IntentPublisher(container.parts.desk).manage(dispatch("CANCEL", None))
    assert outcome.sent and outcome.code == "CANCEL_PENDING"
    assert container.parts.control.commands.current(clock.now_epoch()).command == \
        "CANCEL_PENDING"


def report(**changes) -> ActionReport:
    fields = dict(schema_version="v6.action.1", kind="APPLIED", action_id="m3a7q2z5k6pw",
                  command="MODIFY_POSITION", intent_id="k7w2m4pq3xza", ticket=91,
                  reason_code="NONE", retcode=10009, step=0, old_sl=4353.5, new_sl=4361.0,
                  price=4366.2, sent_at_epoch=int(T0))
    return ActionReport(**{**fields, **changes})


def seeded(container: V6Container) -> ActionDesk:
    ledger = container.ledger_cycles
    ledger.intents.insert(NewIntent(
        intent_id="k7w2m4pq3xza", cycle_id="c-00000000000000aa", session_id="sess-1",
        agent="claude_code", source="operator", side="buy", order_type="BUY_LIMIT",
        entry=4360.5, sl=4353.5, tp=4374.5, lots=0.01, risk_usd=7.4,
        valid_until_epoch=int(T0) + 60, pending_expiry_epoch=int(T0) + 1800,
        time_barrier_s=9000, created_at=T0, tp1=4366.0, tp2=4370.0, sl_after_tp1=4361.0,
        sl_after_tp2=4365.0))
    from app.v6.ledger_actions import ActionRow
    ledger.actions.insert(ActionRow(
        action_id="m3a7q2z5k6pw", cycle_id="c-00000000000000bb", session_id="sess-1",
        agent="claude_code", command="MODIFY_POSITION", ticket=91,
        intent_id="k7w2m4pq3xza", payload=ACTION.payload(), created_at=T0, updated_at=T0))
    return ActionDesk(ledger)


def test_an_applied_modify_updates_the_plan(container: V6Container) -> None:
    desk = seeded(container)
    assert desk.apply(report(), T0 + 2) == "APPLIED"
    intent = container.ledger_cycles.intents.get("k7w2m4pq3xza")
    assert (intent.tp2, intent.sl_after_tp2, intent.time_barrier_s) == (4371.0, 4366.0, 10800)
    assert desk.apply(report(), T0 + 3) == "DUPLICATE"


def test_a_rejected_action_keeps_the_plan(container: V6Container) -> None:
    desk = seeded(container)
    assert desk.apply(report(kind="REJECTED", reason_code="SL_WIDER", retcode=0),
                      T0 + 2) == "REJECTED"
    assert container.ledger_cycles.intents.get("k7w2m4pq3xza").tp2 == 4370.0


def test_a_plan_step_is_recorded(container: V6Container) -> None:
    desk = seeded(container)
    step = report(kind="PLAN_STEP", action_id="", command="NONE", step=1, retcode=10009)
    assert desk.apply(step, T0 + 5) == "STEP"
    assert container.ledger_cycles.intents.get("k7w2m4pq3xza").plan_step == 1
    assert desk.apply(step, T0 + 6) == "DUPLICATE"
```

Tambahkan juga tes rute (di file yang sama atau `test_v6_ea_routes.py`) yang mengirim
`ActionReport` bertanda tangan ke `/v6/action` lewat helper klien EA yang sudah ada dan
mengharapkan `{"ok": true}`, serta 401 tanpa tanda tangan.

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_action_runtime.py`
Expected: FAIL.

- [ ] **Step 3: Implementasi `actions.py`**

```python
"""
The management action waiting for the EA (spec section 3.3).

At most one action waits. It is repeated on every poll until the EA reports it or it is
older than ACTION_MAX_AGE_S, after which the EA would refuse it anyway; the watchdog then
marks it EXPIRED. Everything here runs on the event loop thread.
"""

from __future__ import annotations

from ..deliberation.publication import ManagementAction
from ..risk.limits import ACTION_MAX_AGE_S
from ..schemas.intent import PollResponse


class ActionBoard:
    def __init__(self) -> None:
        self._pending: ManagementAction | None = None

    def queue(self, action: ManagementAction, now: float) -> None:
        """A newer action replaces one still waiting (the EA applies each id once)."""
        del now
        self._pending = action

    def _fresh(self, now: float) -> bool:
        return self._pending is not None and now - self._pending.issued_at <= ACTION_MAX_AGE_S

    def for_poll(self, now: float) -> ManagementAction | None:
        return self._pending if self._fresh(now) else None

    def settle(self, action_id: str) -> bool:
        if self._pending is None or self._pending.action_id != action_id:
            return False
        self._pending = None
        return True

    def pop_expired(self, now: float) -> ManagementAction | None:
        if self._pending is None or self._fresh(now):
            return None
        expired, self._pending = self._pending, None
        return expired


def action_response(action: ManagementAction, server_time: int) -> PollResponse:
    """The flat v6.intent.2 answer that carries `action` (unsigned)."""
    return PollResponse(
        server_time_epoch=server_time, command=action.command, action_id=action.action_id,
        action_ticket=action.ticket, action_sl=action.sl, action_tp=action.tp,
        action_tp1=action.tp1, action_tp2=action.tp2, action_sl1=action.sl_after_tp1,
        action_sl2=action.sl_after_tp2, action_price=action.price,
        action_expiry_epoch=action.expiry_epoch, action_barrier_s=action.barrier_s,
        action_issued_epoch=action.issued_at)
```

- [ ] **Step 4: Implementasi `action_desk.py`**

```python
"""
What the EA reported about a management action or an SL+ step (`v6.action.1`).

APPLIED stores the new ladder in v6_intents; REJECTED and FAILED only close the action.
A PLAN_STEP is recorded once per (ticket, step). Blocking: call it in a thread.
"""

from __future__ import annotations

from typing import Final

from ..ledger_cycles import LedgerCycles
from ..schemas.intent import ActionReport

RESULT_DUPLICATE: Final[str] = "DUPLICATE"
RESULT_STEP: Final[str] = "STEP"
MODIFY_COMMANDS: Final[frozenset[str]] = frozenset({"MODIFY_POSITION", "MODIFY_PENDING"})


class ActionDesk:
    def __init__(self, ledger: LedgerCycles) -> None:
        self._ledger = ledger

    def apply(self, report: ActionReport, now: float) -> str:
        if report.kind == "PLAN_STEP":
            return self._step(report, now)
        detail = f"{report.reason_code} retcode {report.retcode}"
        if not self._ledger.actions.mark(report.action_id, report.kind, detail, now):
            return RESULT_DUPLICATE
        if report.kind == "APPLIED":
            self._store_plan(report.action_id)
        return report.kind

    def _intent_id(self, report: ActionReport) -> str:
        if report.intent_id:
            return report.intent_id
        record = self._ledger.intents.by_ticket(report.ticket)
        return "" if record is None else record.intent_id

    def _step(self, report: ActionReport, now: float) -> str:
        intent_id = self._intent_id(report)
        recorded = self._ledger.actions.record_step(
            intent_id, report.ticket, report.step, report.old_sl, report.new_sl,
            report.price, now)
        if intent_id:
            self._ledger.intents.set_plan_step(intent_id, report.step)
        return RESULT_STEP if recorded else RESULT_DUPLICATE

    def _store_plan(self, action_id: str) -> None:
        row = self._ledger.actions.get(action_id)
        if row is None or row.command not in MODIFY_COMMANDS or not row.intent_id:
            return
        levels = row.payload
        self._ledger.intents.update_plan(
            row.intent_id, tp1=float(levels["tp1"]), tp2=float(levels["tp2"]),
            sl_after_tp1=float(levels["sl_after_tp1"]),
            sl_after_tp2=float(levels["sl_after_tp2"]),
            time_barrier_s=int(levels["barrier_s"]))
```

- [ ] **Step 5: `publisher.manage`, balasan poll, rute dan watchdog**

`publisher.py`:

```python
REASON_AGENT_CANCEL: Final[str] = "AGENT_CANCEL"
CODE_CANCEL_PENDING: Final[str] = "CANCEL_PENDING"

    async def manage(self, dispatch: ManageDispatch) -> ManageOutcome:
        """Queue a management action (or CANCEL_PENDING) for the armed session."""
        deps = self._desk.deps
        now = deps.clock.now_epoch()
        session = await asyncio.to_thread(deps.ledger.active_session)
        if session is None or not session.armed:
            return ManageOutcome(False, NOT_ARMED, "no armed session")
        arm = await self._desk.arm_decision(session, now)
        if not arm.armed:
            return ManageOutcome(False, arm.reason, "the session fails its arming checks")
        if dispatch.request.op == "CANCEL" or dispatch.action is None:
            cancelled = await self.cancel_pending(REASON_AGENT_CANCEL)
            return ManageOutcome(True, CODE_CANCEL_PENDING,
                                 f"cancel queued ({len(cancelled)} undelivered intents cancelled)")
        action = dispatch.action
        row = ActionRow(action_id=action.action_id, cycle_id=action.cycle_id,
                        session_id=session.session_id, agent=dispatch.agent,
                        command=action.command, ticket=action.ticket,
                        intent_id=action.intent_id, payload=action.payload(),
                        created_at=now, updated_at=now)
        await asyncio.to_thread(deps.ledger.actions.insert, row)
        deps.actions.queue(action, now)
        return ManageOutcome(True, action.command,
                             f"{action.command} {action.action_id} queued for {action.ticket}")
```

  `publish(...)` meneruskan `trade=request.plan` ke `build_intent`.

`poll_reply.py`, di `reply(...)`:

```python
        idle = PollResponse(server_time_epoch=int(now), command=command)
        response = idle
        action = deps.actions.for_poll(now) if command == NO_COMMAND else None
        if action is not None:
            response = action_response(action, int(now))
        elif command == NO_COMMAND and deps.settings.mode == EXECUTE_MODE:
            response = await self._with_intent(idle, now, facts.point)
```

  (docstring modul: urutan FLATTEN > CANCEL_PENDING > aksi manajemen > intent.)

`v6_ea.py`:

```python
@router.post("/v6/action")
async def v6_action(request: Request, container: ActiveContainer) -> dict[str, bool]:
    report = _parse(ActionReport, await read_ea_body(request, container))
    now = container.clock.now_epoch()
    container.ea_state.touch(now)
    parts = container.parts
    if report.action_id:
        parts.actions.settle(report.action_id)
    try:
        result = await _storage(parts.action_desk.apply, report, now)
    except (sqlite3.Error, ValueError) as exc:
        logger.error("v6 action report %s/%s not applied (%s)", report.kind,
                     report.action_id or report.ticket, type(exc).__name__)
        return {"ok": True}
    logger.info("v6 action report %s %s ticket=%s -> %s", report.kind,
                report.action_id or "-", report.ticket, result)
    return {"ok": True}
```

`watchdog.py`: langkah `STEP_ACTIONS` setelah `STEP_SUPERVISE`:

```python
async def _expire_actions(self, now: float) -> None:
    expired = self._deps.actions.pop_expired(now)
    if expired is not None:
        await asyncio.to_thread(self._deps.ledger.actions.mark, expired.action_id,
                                "EXPIRED", "no EA report within 30 s", now)
```

Wiring: `ActionBoard()` dan `ActionDesk(ledger_cycles)` dibuat sekali di container dan
dimasukkan ke deps desk (`deps.actions`), ke `container.parts.actions`,
`container.parts.action_desk`, dan ke deps watchdog.

- [ ] **Step 6: Jalankan tes**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_action_runtime.py tests/v6 -k "poll or publisher or route or watchdog or security"`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add adapter/app/v6/runtime adapter/app/routes/v6_ea.py adapter/app/v6/container.py adapter/tests
git commit -m "feat: queue V6 management actions on signed polls and record EA reports"
```

---

## Task 13: Pembaruan sesi otomatis setelah rollover

**Files:**
- Modify: `adapter/app/v6/runtime/sessions.py` (`SessionService`)
- Modify: `adapter/app/v6/runtime/watchdog.py` (langkah pembaruan)
- Modify: `adapter/app/routes/v6_status_view.py` (`session_renewal_due`)
- Modify: `adapter/scripts/v6ops/waiting.py` (`session_gone`)
- Modify: `adapter/app/v6/container.py` (argumen baru `SessionService`)
- Test: `adapter/tests/v6/test_session_renewal.py`

**Interfaces:**
- Consumes: `V6Settings.session_auto_renew` (Task 1)
- Produces:
  - `SessionService(..., auto_renew: bool = False, backend: str = "", mode: str = "")`
  - `SessionService.renewal_due: bool` (properti)
  - `SessionService.renew_if_due(now) -> SessionStartOutcome | None`
  - `/v6/status` berisi `"session_renewal_due": bool`
  - `waiting.session_gone(body)` false bila `body["session_renewal_due"]` true

Aturan:
- `auto_close_if_rollover` yang menutup sesi (alasan rollover atau hari berganti) menyalakan
  `renewal_due` bila `auto_renew` aktif.
- `renew_if_due(now)`: tidak melakukan apa pun bila tidak ada pembaruan tertunda atau
  `_start_refusal(now)` masih menolak (rollover, akhir pekan, breaker, bukan DEMO); selain
  itu memanggil `start(backend=..., mode=..., actor=ACTOR_RUNTIME, now=now)` dan mematikan
  `renewal_due` bila sesi terbuka.
- `stop(...)` oleh aktor selain runtime mematikan `renewal_due` ("Sudah cukup hari ini"
  berarti berhenti sungguhan).

- [ ] **Step 1: Tulis tes yang gagal**

Gunakan fixture sesi yang dipakai `tests/v6/test_v6_sessions.py` (service dengan
`FakeClock`, ledger sementara dan `EaState` yang sudah menerima poll DEMO). Tes:

```python
"""A session closed by the rollover reopens and re-arms when the block ends."""

from __future__ import annotations

import pytest

from app.v6.runtime.sessions import ACTOR_RUNTIME

from .test_v6_sessions import ROLLOVER_EPOCH, AFTER_ROLLOVER_EPOCH, service_for

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_rollover_close_then_renewal(tmp_path) -> None:
    service, clock = service_for(tmp_path, auto_renew=True)
    started = await service.start(backend="operator", mode="execute", actor="operator",
                                  now=clock.now_epoch())
    clock.set(ROLLOVER_EPOCH)
    closed = await service.auto_close_if_rollover(ROLLOVER_EPOCH)
    assert closed is not None and service.renewal_due
    assert await service.renew_if_due(ROLLOVER_EPOCH + 60) is None
    renewed = await service.renew_if_due(AFTER_ROLLOVER_EPOCH)
    assert renewed is not None and renewed.session is not None
    assert renewed.session.session_id != started.session.session_id
    assert not service.renewal_due


async def test_a_user_stop_cancels_the_renewal(tmp_path) -> None:
    service, clock = service_for(tmp_path, auto_renew=True)
    await service.start(backend="operator", mode="execute", actor="operator",
                        now=clock.now_epoch())
    await service.auto_close_if_rollover(ROLLOVER_EPOCH)
    await service.stop(actor="operator", reason="sudah_cukup", now=ROLLOVER_EPOCH + 5)
    assert not service.renewal_due
    assert await service.renew_if_due(AFTER_ROLLOVER_EPOCH) is None


async def test_without_auto_renew_nothing_reopens(tmp_path) -> None:
    service, clock = service_for(tmp_path, auto_renew=False)
    await service.start(backend="operator", mode="execute", actor="operator",
                        now=clock.now_epoch())
    await service.auto_close_if_rollover(ROLLOVER_EPOCH)
    assert not service.renewal_due


def test_the_cli_waits_through_a_renewal() -> None:
    from v6ops.waiting import session_gone   # the CLI package is on sys.path in these tests
    assert session_gone({"session": None, "session_renewal_due": False})
    assert not session_gone({"session": None, "session_renewal_due": True})
```

Bila `test_v6_sessions.py` belum punya `service_for`, `ROLLOVER_EPOCH`
(`1_789_681_800`, Kamis 21:30 UTC) dan `AFTER_ROLLOVER_EPOCH` (`1_789_688_400`, Kamis
23:40 UTC), tambahkan helper itu di sana lebih dulu (pola yang sama dengan fixture yang
sudah ada di file tersebut). `ACTOR_RUNTIME` diimpor untuk memastikan konstanta itu ada.

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_session_renewal.py`
Expected: FAIL.

- [ ] **Step 3: Implementasi**

`sessions.py`:

```python
class SessionService:
    def __init__(self, *, ledger: LedgerCycles, control_log: ControlLog, ea_state: EaState,
                 commands: CommandBoard, desk: SessionDesk | None = None,
                 auto_renew: bool = False, backend: str = "", mode: str = "") -> None:
        ...
        self._auto_renew = auto_renew and bool(backend) and bool(mode)
        self._backend, self._mode = backend, mode
        self._renewal_due = False

    @property
    def renewal_due(self) -> bool:
        return self._renewal_due

    async def renew_if_due(self, now: float) -> SessionStartOutcome | None:
        """Reopen (and arm) the session a rollover closed, once the market allows it."""
        if not self._renewal_due or await self._start_refusal(int(now)) is not None:
            return None
        outcome = await self.start(backend=self._backend, mode=self._mode,
                                   actor=ACTOR_RUNTIME, now=now)
        if outcome.session is not None:
            self._renewal_due = False
            logger.info("v6 session renewed after rollover: %s", outcome.session.session_id)
        return outcome
```

  Di `stop(...)`, baris pertama: `if actor != ACTOR_RUNTIME: self._renewal_due = False`.
  Di `auto_close_if_rollover(...)`, setelah `outcome = await self.stop(...)`:
  `self._renewal_due = self._auto_renew` lalu `return outcome`.

`watchdog.py`, setelah langkah sesi:

```python
        await self._step(STEP_SESSIONS, lambda: deps.sessions.renew_if_due(now), None, errors)
```

`container.py`: `SessionService(..., auto_renew=settings.session_auto_renew,
backend=settings.backend, mode=settings.mode)`.

`v6_status_view.py`: tambah `"session_renewal_due": plane.sessions.renewal_due` di badan
`/v6/status`.

`waiting.py`:

```python
def session_gone(body: object) -> bool:
    """The reply says there is no active session and none is about to reopen."""
    if not isinstance(body, Mapping) or body.get("session_renewal_due") is True:
        return False
    session = body.get("session", {})
    return (session is None or get_path(session, "active") is False
            or body.get("session_active") is False)
```

- [ ] **Step 4: Jalankan tes**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_session_renewal.py tests/v6 -k "session or watchdog or status or wait"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add adapter/app/v6/runtime/sessions.py adapter/app/v6/runtime/watchdog.py adapter/app/routes/v6_status_view.py adapter/scripts/v6ops/waiting.py adapter/app/v6/container.py adapter/tests/v6
git commit -m "feat: reopen and re-arm the V6 session after the daily rollover"
```

---

## Task 14: EA — intent v2, order STOP dan vektor HMAC

**Files:**
- Modify: `ea/QlipV6/Intent.mqh` (skema v2, field baru, perintah baru, kanonik, aturan tangga)
- Modify: `ea/QlipV6/Checks.mqh` (`WithinDrift`, `PendingStillValid`, `RiskWithinCap`,
  `SymbolShapeProblem`)
- Modify: `ea/QlipV6/Execute.mqh` (`OrderTypeFor`, `BuildEntryRequest`, rencana ke Track)
- Modify: `ea/QlipV6/Orders.mqh` (pindahan `NormalizePrice`/`NormalizeLots` dari
  `Execute.mqh`; `ModifyPositionStops`, `ModifyPendingOrder`)
- Modify: `ea/QlipV6/Track.mqh` (field rencana, simpan/muat, `TrackPlanFacts`)
- Modify: `ea/QlipV6/Exposure.mqh` (`plan_step`, `time_limit_epoch` di `PositionJson`)
- Modify: `ea/QlipV6/SelfTest.mqh` (vektor intent v2)
- Modify: `adapter/tests/v6/golden/hmac_vectors.json` (baris intent v2)
- Test: `adapter/tests/v6/test_wire_golden.py`, `adapter/tests/v6/test_ea_wire_parity.py`,
  tes paritas sumber EA lain (`grep -rln "ea_source_fixtures" tests/`)

**Interfaces:**
- Consumes: kontrak Task 5
- Produces (MQL):
  - `PollReply` field baru (nama sama dengan JSON), `INTENT_SCHEMA "v6.intent.2"`,
    `CMD_CLOSE_POSITION`, `CMD_MODIFY_POSITION`, `CMD_MODIFY_PENDING`,
    `IsActionCommand(cmd)`, `INTENT_BUY_STOP`, `INTENT_SELL_STOP`, `IsStopOrder`,
    `IsPendingOrder`, `LadderProblem(reply)`
  - `TrackRecord.tp1`, `tp2`, `step_sl1`, `step_sl2`, `plan_step`, `plan_next_ms`
    (hanya memori), `TrackPlanFacts(key, step, limit_epoch)`
  - `ModifyPositionStops(ticket, sl, tp, retcode)`,
    `ModifyPendingOrder(ticket, price, sl, tp, expiration, retcode)`

- [ ] **Step 1: Buat baris golden v2 (skrip sekali pakai di scratchpad)**

Tulis skrip di folder scratchpad (bukan di repo) yang membangun `PollResponse` untuk empat
baris baru dan menandatanganinya dengan kunci uji publik dari golden file:

```python
"""Scratch: v2 intent rows for tests/v6/golden/hmac_vectors.json (run from adapter/)."""

import json
from pathlib import Path

from pydantic import SecretStr

from app.v6 import wire
from app.v6.schemas.intent import PollResponse

path = Path("tests/v6/golden/hmac_vectors.json")
golden = json.loads(path.read_text(encoding="utf-8"))
key = SecretStr(golden["test_key"])
T = 1789565408
rows = {
    "idle": dict(server_time_epoch=T),
    "cancel-pending": dict(server_time_epoch=T, command="CANCEL_PENDING"),
    "buy-limit": dict(server_time_epoch=T, has_intent=True, intent_id="k7w2m4pq3xza",
                      source="operator", side="buy", order_type="BUY_LIMIT", entry=4535.07,
                      sl=4528.07, tp=4549.07, lots=0.01, ref_price=4535.35,
                      max_drift_points=200, max_spread_points=35,
                      valid_until_epoch=T + 120, pending_expiry_epoch=T + 1792,
                      time_barrier_s=7200, magic=250570),
    "sell-market": dict(server_time_epoch=T, has_intent=True, intent_id="q2m7x4k5w3pz",
                        source="operator", side="sell", order_type="SELL", entry=4535.18,
                        sl=4542.39, tp=4520.76, lots=0.01, ref_price=4535.18,
                        max_drift_points=140, max_spread_points=35,
                        valid_until_epoch=T + 120, pending_expiry_epoch=0,
                        time_barrier_s=7200, magic=250570),
    "buy-stop-ladder": dict(server_time_epoch=T, has_intent=True, intent_id="w3k7m2q5z4pa",
                            source="operator", side="buy", order_type="BUY_STOP",
                            entry=4540.0, sl=4533.0, tp=4554.0, lots=0.02,
                            ref_price=4535.35, max_drift_points=140, max_spread_points=35,
                            valid_until_epoch=T + 120, pending_expiry_epoch=T + 1792,
                            time_barrier_s=9000, magic=250570, tp1=4544.0, tp2=4548.0,
                            sl_after_tp1=4540.5, sl_after_tp2=4544.0),
    "modify-position": dict(server_time_epoch=T, command="MODIFY_POSITION",
                            action_id="m3a7q2z5k6pw", action_ticket=5012345702,
                            action_sl=4536.0, action_tp=4552.0, action_tp1=0.0,
                            action_tp2=4548.0, action_sl1=0.0, action_sl2=4544.0,
                            action_barrier_s=10800, action_issued_epoch=T),
    "modify-pending": dict(server_time_epoch=T, command="MODIFY_PENDING",
                           action_id="p5q2w7m3k4za", action_ticket=5012345703,
                           action_price=4539.0, action_sl=4532.0, action_tp=4553.0,
                           action_tp1=4543.0, action_tp2=4547.0, action_sl1=4539.5,
                           action_sl2=4543.0, action_expiry_epoch=T + 1200,
                           action_barrier_s=9000, action_issued_epoch=T),
    "close-position": dict(server_time_epoch=T, command="CLOSE_POSITION",
                           action_id="c4z7k2m5q3wp", action_ticket=5012345702,
                           action_issued_epoch=T),
}
out = []
for name, fields in rows.items():
    response = wire.sign_intent(key, PollResponse(**fields), 0.01)
    out.append({"name": name, "point": 0.01,
                "response": response.model_dump(mode="json"),
                "canonical": wire.intent_canonical(response, 0.01), "sig": response.sig})
golden["intents"] = out
path.write_text(json.dumps(golden, indent=2) + "\n", encoding="utf-8")
print("rows", [row["name"] for row in out])
```

Sesuaikan nilai baris lama (`buy-limit`, `sell-market`) dengan nilai di golden file saat ini
sebelum menjalankan skrip (baca dulu `tests/v6/golden/hmac_vectors.json`), supaya hanya
schema dan field tambahan yang berubah.

Run: `../.venv/Scripts/python.exe <scratchpad>/golden_v2.py`
Expected: `rows ['idle', 'cancel-pending', 'buy-limit', 'sell-market', 'buy-stop-ladder', 'modify-position', 'modify-pending', 'close-position']`

Perbarui `test_intent_vectors` di `test_wire_golden.py` agar mem-parametrize kedelapan nama.

- [ ] **Step 2: Jalankan tes paritas, pastikan gagal**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_wire_golden.py tests/v6/test_ea_wire_parity.py`
Expected: `test_wire_golden` PASS, `test_ea_wire_parity` FAIL (SelfTest.mqh masih v1).

- [ ] **Step 3: `Intent.mqh`**

- `#define INTENT_SCHEMA "v6.intent.2"`, tambah:

```mql5
#define CMD_CLOSE_POSITION       "CLOSE_POSITION"
#define CMD_MODIFY_POSITION      "MODIFY_POSITION"
#define CMD_MODIFY_PENDING       "MODIFY_PENDING"
#define INTENT_BUY_STOP          "BUY_STOP"
#define INTENT_SELL_STOP         "SELL_STOP"
```

- `struct PollReply` bertambah (setelah `magic`, sebelum `sig`):

```mql5
   double            tp1;
   double            tp2;
   double            sl_after_tp1;
   double            sl_after_tp2;
   string            action_id;
   long              action_ticket;
   double            action_sl;
   double            action_tp;
   double            action_tp1;
   double            action_tp2;
   double            action_sl1;
   double            action_sl2;
   double            action_price;
   long              action_expiry_epoch;
   long              action_barrier_s;
   long              action_issued_epoch;
```

- `ParsePollReply` membaca semua field baru dengan pembaca strict yang sama
  (`JsonGetNumber` untuk harga, `JsonGetLong` untuk integer, `JsonGetString` untuk
  `action_id`); karena fungsi ini melewati 50 baris, pecah menjadi
  `ParseIntentFields(json, p)` dan `ParseActionFields(json, p)`.
- Kosakata:

```mql5
bool IsActionCommand(const string command)
{
   return command == CMD_CLOSE_POSITION || command == CMD_MODIFY_POSITION
          || command == CMD_MODIFY_PENDING;
}

bool IsKnownCommand(const string command)
{
   return command == CMD_NONE || command == CMD_FLATTEN || command == CMD_CANCEL_PENDING
          || IsActionCommand(command);
}

bool IsStopOrder(const string order_type)
{
   return order_type == INTENT_BUY_STOP || order_type == INTENT_SELL_STOP;
}

bool IsPendingOrder(const string order_type)
{
   return IsLimitOrder(order_type) || IsStopOrder(order_type);
}

bool OrderTypeMatchesSide(const string side, const string order_type)
{
   if(side == INTENT_SIDE_BUY)
      return order_type == INTENT_BUY_LIMIT || order_type == INTENT_BUY_STOP
             || order_type == INTENT_BUY;
   if(side == INTENT_SIDE_SELL)
      return order_type == INTENT_SELL_LIMIT || order_type == INTENT_SELL_STOP
             || order_type == INTENT_SELL;
   return false;
}
```

- `IntentCanonical` menambah, setelah `magic`, persis urutan `wire.CANONICAL_FIELDS`:

```mql5
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.tp1, point));
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.tp2, point));
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.sl_after_tp1, point));
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.sl_after_tp2, point));
   s += CANONICAL_SEPARATOR + p.action_id;
   s += CANONICAL_SEPARATOR + IntegerToString(p.action_ticket);
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.action_sl, point));
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.action_tp, point));
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.action_tp1, point));
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.action_tp2, point));
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.action_sl1, point));
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.action_sl2, point));
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.action_price, point));
   s += CANONICAL_SEPARATOR + IntegerToString(p.action_expiry_epoch);
   s += CANONICAL_SEPARATOR + IntegerToString(p.action_barrier_s);
   s += CANONICAL_SEPARATOR + IntegerToString(p.action_issued_epoch);
```

  (pecah menjadi `IntentCanonicalHead` dan `IntentCanonicalTail` bila fungsi melewati 50
  baris).
- Aturan tangga, cermin `schemas/intent.py::_ladder_checks`:

```mql5
// "" when the ladder is absent or well ordered (contract, intent v2).
string LadderProblem(const PollReply &p)
{
   if(p.tp1 == 0.0 && p.tp2 == 0.0 && p.sl_after_tp1 == 0.0 && p.sl_after_tp2 == 0.0)
      return "";
   double sign = (p.side == INTENT_SIDE_BUY) ? 1.0 : -1.0;
   if(p.tp1 <= 0.0 || p.tp2 <= 0.0 || sign * (p.tp1 - p.entry) <= 0.0
      || sign * (p.tp2 - p.tp1) <= 0.0 || sign * (p.tp - p.tp2) <= 0.0)
      return "the TP ladder must advance: entry, tp1, tp2, tp";
   if(p.sl_after_tp1 > 0.0 && (sign * (p.sl_after_tp1 - p.sl) <= 0.0
                               || sign * (p.tp1 - p.sl_after_tp1) <= 0.0))
      return "sl_after_tp1 must sit between sl and tp1";
   double floor = (p.sl_after_tp1 > 0.0) ? p.sl_after_tp1 : p.sl;
   if(p.sl_after_tp2 > 0.0 && (sign * (p.sl_after_tp2 - floor) < 0.0
                               || sign * (p.sl_after_tp2 - p.sl) <= 0.0
                               || sign * (p.tp2 - p.sl_after_tp2) <= 0.0))
      return "sl_after_tp2 must sit between the stop before it and tp2";
   return "";
}
```

- `PendingExpiryProblem` memakai `IsPendingOrder` (pesan: "a pending order must expire at
  least 60 s after valid_until"), dan `IntentFieldsProblem` memanggil `LadderProblem`
  sebelum `PendingExpiryProblem`. Pemeriksaan `reply.command != CMD_NONE` tetap: intent
  tidak pernah bersama perintah.

- [ ] **Step 4: `Checks.mqh`, `Orders.mqh`, `Execute.mqh`**

`Checks.mqh`:

```mql5
bool WithinDrift(const PollReply &p, const EntryQuote &q)
{
   if(IsPendingOrder(p.order_type))
      return QuoteDriftPoints(p, q) <= p.max_drift_points;
   return MarketDeviationPoints(p, q) > 0;
}

// A limit price still rests on the passive side; a stop price still sits beyond the quote.
bool PendingStillValid(const PollReply &p, const EntryQuote &q)
{
   if(!IsPendingOrder(p.order_type))
      return true;
   long stops = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   long entry = PriceToPoints(p.entry, q.point);
   long ask = PriceToPoints(q.ask, q.point);
   long bid = PriceToPoints(q.bid, q.point);
   if(p.order_type == INTENT_BUY_LIMIT)
      return entry < ask - stops;
   if(p.order_type == INTENT_SELL_LIMIT)
      return entry > bid + stops;
   if(p.order_type == INTENT_BUY_STOP)
      return entry > ask + stops;
   return entry < bid - stops;
}
```

  `RiskWithinCap` memakai `IsPendingOrder(p.order_type) ? p.entry : ...`.
  `SymbolShapeProblem` menambah pemeriksaan grid untuk `tp1`, `tp2`, `sl_after_tp1`,
  `sl_after_tp2` bila nilainya > 0. Ganti pemanggilan `LimitStillPassive` di
  `Execute.mqh::MarketRefusal` menjadi `PendingStillValid`.

`Orders.mqh` (pindahkan `NormalizePrice` dan `NormalizeLots` ke sini, lalu tambahkan):

```mql5
bool TradeDone(const MqlTradeResult &res)
{
   return res.retcode == TRADE_RETCODE_DONE || res.retcode == TRADE_RETCODE_NO_CHANGES
          || res.retcode == TRADE_RETCODE_PLACED;
}

// TRADE_ACTION_SLTP: new stop and target of an open position.
bool ModifyPositionStops(const ulong ticket, const double sl, const double tp, uint &retcode)
{
   MqlTradeRequest req;
   MqlTradeResult res;
   ZeroMemory(req);
   ZeroMemory(res);
   req.action = TRADE_ACTION_SLTP;
   req.position = ticket;
   req.symbol = _Symbol;
   req.magic = (ulong)g_cfg.magic;
   req.sl = NormalizePrice(sl);
   req.tp = NormalizePrice(tp);
   bool sent = OrderSend(req, res);
   retcode = res.retcode;
   return sent && TradeDone(res);
}

// TRADE_ACTION_MODIFY: new price, stop, target and expiry of a pending order.
bool ModifyPendingOrder(const ulong ticket, const double price, const double sl,
                        const double tp, const datetime expiration, uint &retcode)
{
   MqlTradeRequest req;
   MqlTradeResult res;
   ZeroMemory(req);
   ZeroMemory(res);
   req.action = TRADE_ACTION_MODIFY;
   req.order = ticket;
   req.symbol = _Symbol;
   req.price = NormalizePrice(price);
   req.sl = NormalizePrice(sl);
   req.tp = NormalizePrice(tp);
   req.type_time = ORDER_TIME_SPECIFIED;
   req.expiration = expiration;
   bool sent = OrderSend(req, res);
   retcode = res.retcode;
   return sent && TradeDone(res);
}
```

`Execute.mqh`:

```mql5
ENUM_ORDER_TYPE OrderTypeFor(const string order_type)
{
   if(order_type == INTENT_BUY_LIMIT)
      return ORDER_TYPE_BUY_LIMIT;
   if(order_type == INTENT_SELL_LIMIT)
      return ORDER_TYPE_SELL_LIMIT;
   if(order_type == INTENT_BUY_STOP)
      return ORDER_TYPE_BUY_STOP;
   if(order_type == INTENT_SELL_STOP)
      return ORDER_TYPE_SELL_STOP;
   if(order_type == INTENT_BUY)
      return ORDER_TYPE_BUY;
   return ORDER_TYPE_SELL;
}
```

  `BuildEntryRequest` memakai `bool pending = IsPendingOrder(p.order_type);` di semua tempat
  yang sekarang memakai `limit`. Di tempat record Track diisi (sekarang `t.barrier_s =
  p.time_barrier_s;`), tambahkan:

```mql5
   t.tp1 = p.tp1;
   t.tp2 = p.tp2;
   t.step_sl1 = p.sl_after_tp1;
   t.step_sl2 = p.sl_after_tp2;
   t.plan_step = 0;
```

- [ ] **Step 5: `Track.mqh` dan `Exposure.mqh`**

`TrackRecord` bertambah (setelah `exit_spread`):

```mql5
   double            tp1;              // SL+ trigger 1 (0 = none)
   double            tp2;              // SL+ trigger 2 (0 = none)
   double            step_sl1;         // stop after tp1 (0 = none)
   double            step_sl2;         // stop after tp2 (0 = none)
   int               plan_step;        // 0, 1 or 2: the last step executed
   ulong             plan_next_ms;     // memory only: SL+ retry throttle
```

`TrackSave` menambah `"T1"`, `"T2"`, `"S1"`, `"S2"`, `"PS"`; `TrackLoad` membacanya dengan
default 0. Fungsi baru:

```mql5
// The SL+ step and the time-limit epoch (UTC) of the record keyed by `key`.
bool TrackPlanFacts(const ulong key, const long open_utc, int &step, long &limit_epoch)
{
   int i = TrackFind(key);
   step = 0;
   limit_epoch = 0;
   if(i < 0)
      return false;
   step = g_track[i].plan_step;
   long barrier = (g_track[i].barrier_s > 0) ? g_track[i].barrier_s : DEFAULT_TIME_BARRIER_S;
   limit_epoch = open_utc + MathMin(barrier, (long)MAX_TIME_BARRIER_S);
   return true;
}
```

`Exposure.mqh::PositionJson` (include `Track.mqh` bila belum; pastikan tidak ada siklus
include yang memutus deklarasi — kompilasi di Step 7 membuktikannya):

```mql5
   long open_utc = MathMax(ServerToUtc(opened, offset_s), (long)0);
   int step = 0;
   long limit_epoch = 0;
   TrackPlanFacts((ulong)PositionGetInteger(POSITION_IDENTIFIER), open_utc, step, limit_epoch);
   ...
   o.AddInt("open_epoch", open_utc);
   ...
   o.AddInt("plan_step", step);
   o.AddInt("time_limit_epoch", limit_epoch);
```

- [ ] **Step 6: `SelfTest.mqh`**

- `HmacSelfTest` memanggil `IntentRowOk` untuk kedelapan baris golden baru, dengan JSON
  respons, kanonik dan tanda tangan disalin persis dari `hmac_vectors.json`.
- `IntentTamperRejected` memakai baris `buy-limit` v2 dengan `entry` digeser satu tick.
- Tambah pemeriksaan `LadderProblem` pada respons `buy-stop-ladder` yang `tp1`-nya dibuat
  lebih besar dari `tp2` (harus menghasilkan teks tidak kosong).

- [ ] **Step 7: Salin, kompilasi, dan jalankan tes paritas**

```bash
T="/c/Users/Huawei/AppData/Roaming/MetaQuotes/Terminal/D0E8209F77C8CF37AD8BF550E51FF075/MQL5/Experts"
cp ea/QlipV6_XAUUSD.mq5 "$T/" && cp ea/QlipV6/*.mqh "$T/QlipV6/"
"/c/Program Files/MetaTrader 5/MetaEditor64.exe" "/compile:C:\\Users\\Huawei\\AppData\\Roaming\\MetaQuotes\\Terminal\\D0E8209F77C8CF37AD8BF550E51FF075\\MQL5\\Experts\\QlipV6_XAUUSD.mq5" "/log:<scratchpad>\\v6compile.log"
iconv -f UTF-16 -t UTF-8 "<scratchpad>/v6compile.log" | tail -3
```

Expected: `Result: 0 errors, 0 warnings`.

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_wire_golden.py tests/v6/test_ea_wire_parity.py tests/v6 -k "ea_"`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add ea/QlipV6 adapter/tests/v6/golden/hmac_vectors.json adapter/tests/v6/test_wire_golden.py
git commit -m "feat: parse V6 intent v2 and place STOP orders in the EA"
```

---

## Task 15: EA — langkah SL+ lokal, perintah manajemen dan laporan aksi

**Files:**
- Create: `ea/QlipV6/Plan.mqh`
- Create: `ea/QlipV6/Actions.mqh`
- Modify: `ea/QlipV6/Report.mqh` (`QueueActionReport`, `QueuePlanStepReport`)
- Modify: `ea/QlipV6/Outbox.mqh` (`PATH_ACTION "/v6/action"` di daftar jalur yang sah)
- Modify: `ea/QlipV6/Poll.mqh` (`HandlePollResponse` menerapkan aksi)
- Modify: `ea/QlipV6/Track.mqh` (`CLOSE_BY_AGENT 4`)
- Modify: `ea/QlipV6/Basket.mqh` (`CloseReasonName` → `"AGENT"`)
- Modify: `ea/QlipV6/Manage.mqh` (komentar kepala: SL+ sekarang ada di `Plan.mqh`)
- Modify: `ea/QlipV6_XAUUSD.mq5` (versi `6.2.0`, `OnTick` memanggil `PlanTick()`,
  `#include` baru)
- Modify: `ea/QlipV6/SelfTest.mqh` (vektor request `/v6/action`)
- Test: tes paritas EA (`tests/v6/test_ea_*`); tambah vektor request `action` ke golden file

**Interfaces:**
- Consumes: Task 14
- Produces (MQL): `PlanTick()`, `ModifyDistance()`, `ApplyActionCommand(reply)`,
  `QueueActionReport(...)`, `QueuePlanStepReport(...)`

Aturan EA untuk perintah (spec 3.3):
- `action_id` yang pernah diterapkan diabaikan tanpa laporan (memori 32 id terakhir).
- Ditolak (`REJECTED`) bila: akun bukan DEMO (`DEMO_REQUIRED`), umur perintah > 30 s
  (`STALE`), halt lokal (`HALTED`), tiket tidak dilacak atau keadaannya salah
  (`UNKNOWN_TICKET`), SL melebar (`SL_WIDER`), SL/TP/harga pending terlalu dekat
  (`TOO_CLOSE`), batas waktu > 4 jam atau ≤ umur posisi (`BARRIER`), bentuk harga salah
  atau risiko pending di atas `InpMaxRiskUsd` (`BAD_ACTION`).
- Gagal (`FAILED`, `BROKER_ERROR`) bila broker menolak modifikasi.
- `CLOSE_POSITION` menandai record (`CLOSE_BY_AGENT`) dan memanggil
  `ExecuteMarkedExits()`; laporan `APPLIED` dikirim saat tanda dipasang (penutupan diulang
  oleh `Manage.mqh` seperti exit lain).

- [ ] **Step 1: Tambah vektor request `/v6/action` ke golden file**

Dengan skrip scratchpad yang sama pola Task 14, tambahkan baris `requests` bernama `action`:
`ts=1789565411`, `method="POST"`, `path="/v6/action"`, body JSON `ActionReport` APPLIED
(nilai bebas tapi tetap), `sig = wire.sign_request(test_key, ...)`. Tambah `"action"` ke
`{"poll", "execution"} <= set(...)` di `test_wire_golden.py` dan panggil
`RequestRowOk("action", ...)` di `SelfTest.mqh`.

- [ ] **Step 2: `Plan.mqh`**

```mql5
//+------------------------------------------------------------------+
//| QlipV6/Plan.mqh                                                  |
//| The SL+ ladder of every V6 position, run locally on every tick   |
//| and timer beat (spec section 3.4). When the bid (buy) or the ask |
//| (sell) reaches TP1 or TP2, the stop moves to that step's level:  |
//| only toward safety and never inside the modify distance. The     |
//| step is saved before it is reported, so a restart resumes it.    |
//+------------------------------------------------------------------+
#ifndef QLIPV6_PLAN_MQH
#define QLIPV6_PLAN_MQH

#include "Config.mqh"
#include "Orders.mqh"
#include "Sync.mqh"
#include "Track.mqh"
#include "Report.mqh"

#define PLAN_RETRY_MS         1000
#define PLAN_BUFFER_PRICE     0.10

// The adapter's d_min: max(stops, freeze) x point + spread + buffer.
double ModifyDistance(void)
{
   long stops = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   long freeze = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double spread = SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID);
   return (double)MathMax(stops, freeze) * point + MathMax(spread, 0.0) + PLAN_BUFFER_PRICE;
}

int PlanStepReached(const TrackRecord &r, const double bid, const double ask)
{
   bool buy = r.side == TRACK_SIDE_BUY;
   double price = buy ? bid : ask;
   double sign = buy ? 1.0 : -1.0;
   if(r.tp2 > 0.0 && sign * (price - r.tp2) >= 0.0)
      return 2;
   if(r.tp1 > 0.0 && sign * (price - r.tp1) >= 0.0)
      return 1;
   return 0;
}

double PlanStepSl(const TrackRecord &r, const int step)
{
   if(step >= 2 && r.step_sl2 > 0.0)
      return r.step_sl2;
   if(step >= 1 && r.step_sl1 > 0.0)
      return r.step_sl1;
   return 0.0;
}

bool SaferStop(const bool buy, const double target, const double current)
{
   if(buy)
      return target > current;
   return current <= 0.0 || target < current;
}

bool OutsideModifyDistance(const bool buy, const double target, const double bid,
                           const double ask)
{
   double d = ModifyDistance();
   return buy ? target <= bid - d : target >= ask + d;
}

void PlanMarkStep(const int i, const int step, const double old_sl, const double new_sl,
                  const double price)
{
   g_track[i].plan_step = step;
   g_track[i].sl = new_sl;
   TrackSave(g_track[i]);
   QueuePlanStepReport(g_track[i], step, old_sl, new_sl, price);
   PrintFormat("V6 plan %I64u: step %d, stop %s -> %s", g_track[i].key, step,
               DoubleToString(old_sl, _Digits), DoubleToString(new_sl, _Digits));
}

void PlanStepFor(const int i, const double bid, const double ask, const ulong now_ms)
{
   if(g_track[i].state != TRACK_STATE_OPEN || now_ms < g_track[i].plan_next_ms)
      return;
   int reached = PlanStepReached(g_track[i], bid, ask);
   if(reached <= g_track[i].plan_step || !SelectPositionById(g_track[i].key))
      return;
   bool buy = g_track[i].side == TRACK_SIDE_BUY;
   double current = PositionGetDouble(POSITION_SL);
   double target = NormalizePrice(PlanStepSl(g_track[i], reached));
   double price = buy ? bid : ask;
   if(target <= 0.0 || !SaferStop(buy, target, current))
   {
      PlanMarkStep(i, reached, current, current, price);   // nothing to move
      return;
   }
   uint retcode = 0;
   ulong ticket = (ulong)PositionGetInteger(POSITION_TICKET);
   if(!OutsideModifyDistance(buy, target, bid, ask)
      || !ModifyPositionStops(ticket, target, PositionGetDouble(POSITION_TP), retcode))
   {
      g_track[i].plan_next_ms = now_ms + PLAN_RETRY_MS;
      return;
   }
   PlanMarkStep(i, reached, current, target, price);
}

void PlanTick(void)
{
   if(!TradingPermitted() || LocalHaltActive())
      return;
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   if(bid <= 0.0 || ask < bid)
      return;
   ulong now_ms = GetTickCount64();
   for(int i = 0; i < g_track_count; i++)
      PlanStepFor(i, bid, ask, now_ms);
}

#endif // QLIPV6_PLAN_MQH
```

- [ ] **Step 3: `Actions.mqh`**

```mql5
//+------------------------------------------------------------------+
//| QlipV6/Actions.mqh                                               |
//| Management commands from a signed poll reply (spec section 3.3): |
//| CLOSE_POSITION, MODIFY_POSITION and MODIFY_PENDING. Every rule   |
//| is checked again against the live quote; each action id is       |
//| applied once and reported to /v6/action through the outbox.      |
//+------------------------------------------------------------------+
#ifndef QLIPV6_ACTIONS_MQH
#define QLIPV6_ACTIONS_MQH

#include "Config.mqh"
#include "Intent.mqh"
#include "Orders.mqh"
#include "Sync.mqh"
#include "Track.mqh"
#include "Report.mqh"
#include "Manage.mqh"
#include "Plan.mqh"
#include "Schedule.mqh"

#define ACTION_MEMORY         32
#define ACTION_MAX_AGE_S      30
#define ACTION_PRICE_EPSILON  1e-9

string g_action_seen[ACTION_MEMORY];
int    g_action_next = 0;

bool ActionSeen(const string id)
{
   for(int i = 0; i < ACTION_MEMORY; i++)
   {
      if(g_action_seen[i] == id)
         return true;
   }
   return false;
}

void RememberAction(const string id)
{
   g_action_seen[g_action_next] = id;
   g_action_next = (g_action_next + 1) % ACTION_MEMORY;
}

string ActionGuard(const PollReply &p, const int state, int &index)
{
   if(!AccountIsDemo())
      return ACT_REASON_DEMO_REQUIRED;
   long age = (long)TimeGMT() - p.action_issued_epoch;
   if(age > ACTION_MAX_AGE_S || age < -ACTION_MAX_AGE_S)
      return ACT_REASON_STALE;
   if(LocalHaltActive())
      return ACT_REASON_HALTED;
   index = TrackFind((ulong)p.action_ticket);
   if(index < 0 || g_track[index].state != state)
      return ACT_REASON_UNKNOWN_TICKET;
   return "";
}

string ApplyClose(const PollReply &p)
{
   int i = -1;
   string refusal = ActionGuard(p, TRACK_STATE_OPEN, i);
   if(refusal != "")
      return refusal;
   if(g_track[i].close_reason == CLOSE_BY_NONE)
      TrackMarkClose(i, CLOSE_BY_AGENT);
   ExecuteMarkedExits();
   return "";
}

string PositionLevelsRefusal(const PollReply &p, const bool buy, const double current_sl)
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double d = ModifyDistance();
   double sl = NormalizePrice(p.action_sl);
   double tp = NormalizePrice(p.action_tp);
   bool moved = MathAbs(sl - current_sl) > ACTION_PRICE_EPSILON;
   if(moved && !SaferStop(buy, sl, current_sl))
      return ACT_REASON_SL_WIDER;
   if(moved && !OutsideModifyDistance(buy, sl, bid, ask))
      return ACT_REASON_TOO_CLOSE;
   if(buy ? tp < bid + d : tp > ask - d)
      return ACT_REASON_TOO_CLOSE;
   return "";
}

string ApplyModifyPosition(const PollReply &p, uint &retcode)
{
   int i = -1;
   string refusal = ActionGuard(p, TRACK_STATE_OPEN, i);
   if(refusal != "" || !SelectPositionById(g_track[i].key))
      return (refusal != "") ? refusal : ACT_REASON_UNKNOWN_TICKET;
   bool buy = g_track[i].side == TRACK_SIDE_BUY;
   double current_sl = PositionGetDouble(POSITION_SL);
   refusal = PositionLevelsRefusal(p, buy, current_sl);
   long age = (long)TimeTradeServer() - (long)PositionGetInteger(POSITION_TIME);
   if(refusal == "" && (p.action_barrier_s > MAX_TIME_BARRIER_S || p.action_barrier_s <= age))
      refusal = ACT_REASON_BARRIER;
   if(refusal != "")
      return refusal;
   ulong ticket = (ulong)PositionGetInteger(POSITION_TICKET);
   if(!ModifyPositionStops(ticket, p.action_sl, p.action_tp, retcode))
      return ACT_REASON_BROKER_ERROR;
   g_track[i].sl = NormalizePrice(p.action_sl);
   g_track[i].tp = NormalizePrice(p.action_tp);
   g_track[i].barrier_s = p.action_barrier_s;
   if(g_track[i].plan_step < 1)
   {
      g_track[i].tp1 = p.action_tp1;
      g_track[i].step_sl1 = p.action_sl1;
   }
   if(g_track[i].plan_step < 2)
   {
      g_track[i].tp2 = p.action_tp2;
      g_track[i].step_sl2 = p.action_sl2;
   }
   TrackSave(g_track[i]);
   return "";
}

string PendingLevelsRefusal(const PollReply &p, const ENUM_ORDER_TYPE type)
{
   bool buy = type == ORDER_TYPE_BUY_LIMIT || type == ORDER_TYPE_BUY_STOP;
   double sign = buy ? 1.0 : -1.0;
   if(sign * (p.action_price - p.action_sl) <= 0.0 || sign * (p.action_tp - p.action_price) <= 0.0)
      return ACT_REASON_BAD_ACTION;
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double stops = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * point;
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   bool ok = (type == ORDER_TYPE_BUY_LIMIT) ? p.action_price < ask - stops
             : (type == ORDER_TYPE_SELL_LIMIT) ? p.action_price > bid + stops
             : (type == ORDER_TYPE_BUY_STOP) ? p.action_price > ask + stops
             : p.action_price < bid - stops;
   if(!ok)
      return ACT_REASON_TOO_CLOSE;
   double pnl = 0.0;
   ENUM_ORDER_TYPE side = buy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double volume = OrderGetDouble(ORDER_VOLUME_CURRENT);
   if(!OrderCalcProfit(side, _Symbol, volume, p.action_price, p.action_sl, pnl)
      || -pnl > g_cfg.max_risk_usd)
      return ACT_REASON_BAD_ACTION;
   return "";
}

string ApplyModifyPending(const PollReply &p, uint &retcode)
{
   int i = -1;
   string refusal = ActionGuard(p, TRACK_STATE_PENDING, i);
   if(refusal != "" || !OrderSelect(g_track[i].key))
      return (refusal != "") ? refusal : ACT_REASON_UNKNOWN_TICKET;
   refusal = PendingLevelsRefusal(p, (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE));
   if(refusal == "" && (p.action_barrier_s <= 0 || p.action_barrier_s > MAX_TIME_BARRIER_S))
      refusal = ACT_REASON_BARRIER;
   if(refusal != "")
      return refusal;
   datetime expiry = (datetime)(p.action_expiry_epoch + ServerGmtOffsetSeconds());
   if(!ModifyPendingOrder(g_track[i].key, p.action_price, p.action_sl, p.action_tp, expiry,
                          retcode))
      return ACT_REASON_BROKER_ERROR;
   g_track[i].requested = NormalizePrice(p.action_price);
   g_track[i].sl = NormalizePrice(p.action_sl);
   g_track[i].tp = NormalizePrice(p.action_tp);
   g_track[i].barrier_s = p.action_barrier_s;
   g_track[i].tp1 = p.action_tp1;
   g_track[i].tp2 = p.action_tp2;
   g_track[i].step_sl1 = p.action_sl1;
   g_track[i].step_sl2 = p.action_sl2;
   TrackSave(g_track[i]);
   return "";
}

void ApplyActionCommand(const PollReply &p)
{
   if(!IsActionCommand(p.command) || ActionSeen(p.action_id))
      return;
   RememberAction(p.action_id);
   AdoptUntracked();
   uint retcode = 0;
   string refusal = ACT_REASON_BAD_ACTION;
   if(p.command == CMD_CLOSE_POSITION)
      refusal = ApplyClose(p);
   else if(p.command == CMD_MODIFY_POSITION)
      refusal = ApplyModifyPosition(p, retcode);
   else
      refusal = ApplyModifyPending(p, retcode);
   string kind = (refusal == "") ? ACTION_KIND_APPLIED
                 : (refusal == ACT_REASON_BROKER_ERROR) ? ACTION_KIND_FAILED
                 : ACTION_KIND_REJECTED;
   QueueActionReport(kind, p, (refusal == "") ? ACT_REASON_NONE : refusal, retcode);
   PrintFormat("V6 action %s %s ticket %I64d: %s %s", p.action_id, p.command,
               p.action_ticket, kind, refusal);
}

#endif // QLIPV6_ACTIONS_MQH
```

- [ ] **Step 4: `Report.mqh`, `Outbox.mqh`, `Track.mqh`, `Basket.mqh`**

`Report.mqh` (konstanta sama dengan `schemas/intent.py`):

```mql5
#define ACTION_SCHEMA               "v6.action.1"
#define ACTION_KIND_APPLIED         "APPLIED"
#define ACTION_KIND_REJECTED        "REJECTED"
#define ACTION_KIND_FAILED          "FAILED"
#define ACTION_KIND_PLAN_STEP       "PLAN_STEP"
#define ACT_REASON_NONE             "NONE"
#define ACT_REASON_UNKNOWN_TICKET   "UNKNOWN_TICKET"
#define ACT_REASON_STALE            "STALE"
#define ACT_REASON_SL_WIDER         "SL_WIDER"
#define ACT_REASON_TOO_CLOSE        "TOO_CLOSE"
#define ACT_REASON_BARRIER          "BARRIER"
#define ACT_REASON_DEMO_REQUIRED    "DEMO_REQUIRED"
#define ACT_REASON_HALTED           "HALTED"
#define ACT_REASON_BROKER_ERROR     "BROKER_ERROR"
#define ACT_REASON_BAD_ACTION       "BAD_ACTION"

string ActionReportJson(const string kind, const string action_id, const string command,
                        const string intent_id, const long ticket, const string reason,
                        const long retcode, const int step, const double old_sl,
                        const double new_sl, const double price)
{
   CJsonObject o;
   o.AddStr("schema_version", ACTION_SCHEMA);
   o.AddStr("kind", kind);
   o.AddStr("action_id", action_id);
   o.AddStr("command", command);
   o.AddStr("intent_id", intent_id);
   o.AddInt("ticket", ticket);
   o.AddStr("reason_code", reason);
   o.AddInt("retcode", retcode);
   o.AddInt("step", step);
   o.AddNum("old_sl", old_sl, _Digits);
   o.AddNum("new_sl", new_sl, _Digits);
   o.AddNum("price", price, _Digits);
   o.AddInt("sent_at_epoch", (long)TimeGMT());
   return o.Text();
}

bool QueueActionReport(const string kind, const PollReply &p, const string reason,
                       const uint retcode)
{
   string json = ActionReportJson(kind, p.action_id, p.command, "", p.action_ticket, reason,
                                  (long)retcode, 0, p.action_sl, p.action_sl,
                                  p.action_price);
   return g_outbox.Enqueue(PATH_ACTION, json);
}

bool QueuePlanStepReport(const TrackRecord &r, const int step, const double old_sl,
                         const double new_sl, const double price)
{
   string json = ActionReportJson(ACTION_KIND_PLAN_STEP, "", CMD_NONE, TrackIntentId(r),
                                  (long)r.key, ACT_REASON_NONE, 0, step, old_sl, new_sl,
                                  price);
   return g_outbox.Enqueue(PATH_ACTION, json);
}
```

  (`TrackIntentId(r)` adalah nama fungsi yang sekarang membangun id dari
  `id_high`/`id_low`; pakai nama yang sebenarnya di `Track.mqh`. `ActionReportJson` punya
  banyak parameter tapi tetap satu tanggung jawab; bila linter MQL tidak ada, biarkan.)

`Outbox.mqh`: `#define PATH_ACTION "/v6/action"` dan jalur itu ditambahkan ke pemeriksaan
jalur yang sah (baris `path == PATH_EXECUTION || path == PATH_BASKET_RESULT`).

`Track.mqh`: `#define CLOSE_BY_AGENT 4`.

`Basket.mqh::CloseReasonName`: `if(ea_reason == CLOSE_BY_AGENT) return "AGENT";` sebelum
pemeriksaan `MANUAL`.

- [ ] **Step 5: `Poll.mqh`, `Manage.mqh`, `QlipV6_XAUUSD.mq5`**

`Poll.mqh::HandlePollResponse`:

```mql5
   if(CommandFresh(reply))
   {
      if(IsActionCommand(reply.command))
         ApplyActionCommand(reply);
      else
         ManageApplyCommand(reply.command);
   }
   if(reply.has_intent)
      ExecuteIntent(reply, received_ms);
```

  (`#include "Actions.mqh"`; pesan log "not a flat v6.intent.1 object" menjadi v6.intent.2.)

`Manage.mqh`: ganti kalimat terakhir komentar kepala menjadi "The SL+ ladder lives in
Plan.mqh; nothing here trails or closes part of a position."

`QlipV6_XAUUSD.mq5`:
- `#property version "6.20"` dan string versi EA `"6.2.0"` (nilai `ea_version` di snapshot).
- `#include "QlipV6/Plan.mqh"` dan `#include "QlipV6/Actions.mqh"`.
- `OnTick()` memanggil `PlanTick();` dan `OnTimer()` memanggil `PlanTick();` sebelum
  `ManageTick();`.

- [ ] **Step 6: Salin dan kompilasi**

Sama dengan Task 14 Step 7. Expected: `Result: 0 errors, 0 warnings`.

- [ ] **Step 7: Jalankan tes paritas EA**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6 -k "ea_ or wire or golden"`
Expected: PASS (termasuk paritas konstanta alasan aksi dengan `ActionReason`; bila belum ada,
tambahkan tes paritas yang membaca `Report.mqh` lewat `ea_source_fixtures_v6` dan
membandingkan `ACT_REASON_*` dengan `typing.get_args(ActionReason)`).

- [ ] **Step 8: Commit**

```bash
git add ea adapter/tests/v6
git commit -m "feat: run the V6 SL+ ladder and management commands in the EA"
```

---

## Task 16: Drill EA di Monex-Demo

**Files:** tidak ada perubahan kode; catatan hasil masuk ke `docs/v6-runbook.md` (Task 18).

Jalankan setelah adapter dan EA baru terpasang, **dengan persetujuan pengguna**, di akun
Monex-Demo, satu per satu. Setiap drill dicatat: waktu, perintah, log EA, baris
`v6_actions`/`v6_plan_steps`.

- [ ] **Drill 1: langkah SL+ di TP1 dan TP2.** Entry lewat paket M15 dengan TP1/TP2 dekat
  (misalnya 0,6R dan 1R). Expected: log `V6 plan <ticket>: step 1 ...`, baris
  `v6_plan_steps` step 1, lalu step 2; SL di terminal pindah ke level rencana.
- [ ] **Drill 2: `CLOSE_POSITION`.** Jawab paket posisi dengan `op CLOSE`. Expected: posisi
  tertutup, `v6_actions` APPLIED, basket result `close_reason AGENT`.
- [ ] **Drill 3: SL dilebarkan.** Adapter menolak permintaan seperti itu sebelum sampai ke
  EA, jadi penolakan di EA hanya bisa diuji dengan aksi buatan. Caranya: di worktree
  terpisah yang tidak di-commit, tambahkan endpoint debug sementara yang menaruh aksi
  `MODIFY_POSITION` dengan SL lebih lebar ke `ActionBoard`, lalu jalankan adapter itu
  (kunci dibaca aplikasi seperti biasa dan tidak pernah ditampilkan). Expected: `v6.action.1`
  REJECTED `SL_WIDER`, SL tidak berubah. Bila pengguna tidak menyetujui adapter uji ini,
  lewati drill dan catat bahwa penolakan `SL_WIDER` di EA hanya diperiksa lewat review kode.
- [ ] **Drill 4: `MODIFY_PENDING`.** Pasang LIMIT, lalu ubah harga dan SL. Expected: order
  berubah di terminal, APPLIED, `v6_intents` tangga baru.
- [ ] **Drill 5: BUY_STOP dan SELL_STOP.** Expected: order STOP terpasang dengan kedaluwarsa
  sesuai `pending_expiry_min`.
- [ ] **Drill 6: perpanjang batas waktu.** MODIFY `time_limit_min` lebih besar. Expected:
  posisi tidak ditutup pada batas lama.
- [ ] **Drill 7: restart EA dengan rencana utuh.** Hapus dan pasang lagi EA saat posisi
  berencana terbuka. Expected: log state memuat record, langkah SL+ tetap jalan.
- [ ] **Drill 8: adapter dimatikan saat posisi terbuka.** Expected: langkah SL+, SL/TP dan
  batas waktu tetap berjalan; laporan tertunda terkirim setelah adapter hidup lagi.

---

## Task 17: CLI operator untuk paket dan keputusan v3

**Files:**
- Modify: `adapter/scripts/v6ops/decisions.py` (`DECISION_SCHEMA`, `fallback_template`,
  `build_template`, `agent_entry_example`, `NEXT_EDIT`, `submission_warnings`)
- Modify: `adapter/scripts/v6ops/packet_view.py` (`limits_line`, `position_line`,
  `pending_line`, `plan_text`, `bias_line`, `action_line`, `render_packet`; hapus
  `review_line`)
- Modify: `adapter/scripts/v6ops/waiting.py` (`packet_problems` menerima `state`)
- Modify: `adapter/tests/v6/operator_cli_fixtures_v6.py` dan tes CLI yang memakai templat v2
- Test: `adapter/tests/v6/test_v6_operator_cli_v3.py`

**Interfaces:**
- Consumes: paket dan templat v3 (Task 8)
- Produces:
  - `decisions.DECISION_SCHEMA = "v6.operator.decision.3"`
  - `decisions.fallback_template(packet)` menghasilkan bentuk `DecisionTemplate`
    (`action`, `manage`, `m15_bias`, `entry_plan: None`, `note: ""`)
  - `decisions.agent_entry_example(packet)` menghasilkan contoh `entry_plan` v2 (hanya
    untuk `state == "flat"` dan `agent_entry_possible`)
  - `decisions.manage_example(packet)` menghasilkan contoh `manage` untuk posisi/pending
  - `packet_view.position_line(position)`, `pending_line(order, market)`,
    `bias_line(bias, at)`, `action_line(action)`

Contoh baris ringkasan (format yang diuji):

```text
state position | ticket 91 buy 0.01 @ 4533.35 | sl 4526.35 tp 4549.00 | initial sl 4526.35 | now +0.26R, 10.0 min open, limit 16:30Z | plan tp1 4539.00 (sl+ 4535.50) tp2 4543.00 (sl+ 4539.00) step 0 | answer action MANAGE with manage KEEP, CLOSE or MODIFY
state pending | ticket 77 BUY_LIMIT 4531.35 sl 4524.35 tp 4547.00 lots 0.01 expires 12:00Z | market needs 4.00 to fill | plan tp1 4535.50 (sl+ 4532.00) tp2 4541.00 (sl+ 4535.50) | answer action MANAGE with manage KEEP, CANCEL or MODIFY
last bias 11:45Z range | levels 4526.00 4541.00 | invalidation - | fade the box
last action m3a7q2z5k6pw MODIFY_POSITION ticket 91 APPLIED (NONE retcode 10009) 11:46Z
agent entry id agent-1789564500 | BUY LIMIT <= 4535.34 SELL LIMIT >= 4535.19 | BUY STOP >= 4535.72 SELL STOP <= 4534.81 (max 12.00 away) | stop 6.00...22.50 | tp1 >= 0.5R, tp3 1.0-5.0R | time 60-240 min, pending 15-60 min | budget $25.00 | lots 0.01-0.03
```

- [ ] **Step 1: Tulis tes yang gagal**

```python
"""The operator CLI with packets and decisions v3."""

from __future__ import annotations

import importlib
from pathlib import Path

from .operator_cli_fixtures_v6 import PACKET_NOW, cli  # noqa: F401 (CLI on sys.path)
from . import operator_fixtures_v6 as of

view = importlib.import_module("v6ops.packet_view")
decisions = importlib.import_module("v6ops.decisions")


def document(packet) -> dict:
    return packet.model_dump(mode="json")


def test_the_fallback_template_matches_the_adapter() -> None:
    for sealed in (of.packet(), of.managed_packet("position"), of.managed_packet("pending")):
        packet = document(sealed)
        expected = packet.pop("decision_template")
        assert decisions.build_template(packet) == {**expected, "agent": None}


def test_the_served_template_is_used_as_is() -> None:
    packet = document(of.managed_packet("position"))
    template = decisions.build_template(packet)
    assert (template["schema_version"], template["action"]) == (
        "v6.operator.decision.3", "MANAGE")
    assert template["manage"] == {"target": "position", "ticket": 91, "op": "KEEP",
                                  "sl": None, "tp1": None, "tp2": None, "tp3": None,
                                  "sl_after_tp1": None, "sl_after_tp2": None,
                                  "time_limit_min": None, "entry": None,
                                  "pending_expiry_min": None, "reason": ""}


def test_examples_follow_the_state() -> None:
    flat = document(of.packet())
    example = decisions.agent_entry_example(flat)
    assert example["entry_plan"]["order_type"] in {"LIMIT", "STOP", "MARKET"}
    assert set(example["entry_plan"]) >= {"tp1", "tp2", "tp3", "sl_after_tp1",
                                          "time_limit_min", "lots"}
    assert decisions.manage_example(flat) is None
    managed = document(of.managed_packet("position"))
    assert decisions.agent_entry_example(managed) is None
    assert decisions.manage_example(managed)["manage"]["op"] == "MODIFY"


def render(packet) -> list[str]:
    return view.render_packet(document(packet), now=PACKET_NOW,
                              path=Path("packet.json")).splitlines()


def test_a_position_packet_is_summarised() -> None:
    lines = render(of.managed_packet("position"))
    state = next(line for line in lines if line.startswith("state position"))
    assert "ticket 91 buy 0.01 @ 4533.35" in state
    assert "tp1 4539.00 (sl+ 4535.50)" in state and "step 0" in state
    assert "KEEP, CLOSE or MODIFY" in state
    assert not any(line.startswith("suggestions") for line in lines)


def test_a_pending_packet_is_summarised() -> None:
    lines = render(of.managed_packet("pending"))
    state = next(line for line in lines if line.startswith("state pending"))
    assert "BUY_LIMIT 4531.35" in state and "KEEP, CANCEL or MODIFY" in state


def test_limits_bias_and_last_action_lines() -> None:
    action = {"action_id": "m3a7q2z5k6pw", "op": "MODIFY_POSITION", "ticket": 91,
              "status": "APPLIED", "detail": "NONE retcode 10009",
              "at_epoch": of.BAR_OPEN + 60}
    bias = {"direction": "range", "levels": [4526.0, 4541.0], "invalidation": None,
            "scenario": "fade the box"}
    lines = render(of.packet(last_action=action, last_bias=bias,
                             last_bias_at_epoch=of.BAR_OPEN))
    assert any(line.startswith("last bias") and "range" in line for line in lines)
    assert any(line.startswith("last action m3a7q2z5k6pw MODIFY_POSITION") for line in lines)
    limits = next(line for line in lines if line.startswith("agent entry id"))
    assert "BUY STOP >= 4535.72" in limits and "time 60-240 min" in limits


def test_hostile_text_in_the_bias_is_cleaned() -> None:
    bias = {"direction": "up", "levels": [], "invalidation": None,
            "scenario": "evil\x1b[2J\x07text"}
    lines = render(of.packet(last_bias=bias, last_bias_at_epoch=of.BAR_OPEN))
    assert all("\x1b" not in line and "\x07" not in line for line in lines)
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_v6_operator_cli_v3.py`
Expected: FAIL.

- [ ] **Step 3: Implementasi `decisions.py`**

```python
DECISION_SCHEMA: Final[str] = "v6.operator.decision.3"
NEXT_EDIT: Final[str] = (
    "edit action, views, m15_bias and entry_plan or manage in the decision file "
    "(keep cycle_id, packet_hash, packet_kind and schema_version)")
UNCLEAR_BIAS: Final[Mapping[str, Any]] = MappingProxyType(
    {"direction": "unclear", "levels": [], "invalidation": None, "scenario": ""})
MANAGE_FIELDS: Final[tuple[str, ...]] = (
    "sl", "tp1", "tp2", "tp3", "sl_after_tp1", "sl_after_tp2", "time_limit_min", "entry",
    "pending_expiry_min")


def _keep(packet: Mapping[str, Any]) -> dict[str, Any] | None:
    state = packet.get("state")
    block = packet.get("position") if state == "position" else packet.get("pending_order")
    if state not in ("position", "pending") or not isinstance(block, Mapping):
        return None
    return {"target": state, "ticket": block.get("ticket"), "op": "KEEP",
            **{name: None for name in MANAGE_FIELDS}, "reason": ""}


def fallback_template(packet: Mapping[str, Any]) -> dict[str, Any]:
    """The adapter's DecisionTemplate rebuilt from the packet (older adapters)."""
    manage = _keep(packet)
    bias = packet.get("last_bias")
    return {
        "schema_version": DECISION_SCHEMA, "packet_kind": packet.get("packet_kind", "m15"),
        "cycle_id": packet.get("cycle_id"), "packet_hash": packet.get("packet_hash"),
        "agent": None, "action": "HOLD" if manage is None else "MANAGE",
        "views": _plain(_baseline_or_defaults(packet.get("baseline_views"))),
        "entry_plan": None, "manage": manage,
        "m15_bias": _plain(bias) if isinstance(bias, Mapping) else dict(UNCLEAR_BIAS),
        "note": "",
    }


def build_template(packet: Mapping[str, Any]) -> dict[str, Any]:
    template = packet.get("decision_template")
    decision = _plain(template) if isinstance(template, Mapping) else fallback_template(packet)
    return {**decision, "agent": None}
```

  `_baseline_or_defaults` adalah logika view yang sekarang ada di `fallback_template` v2
  (ABSTAIN untuk PA, `UNKNOWN_VIEWS` untuk desk yang kosong); keluarkan jadi fungsi
  tersendiri. `HOLD_CHIEF` dihapus.

```python
def agent_entry_example(packet: Mapping[str, Any]) -> dict[str, Any] | None:
    """Placeholders showing the fields of an own entry (values must be replaced)."""
    limits = packet.get("limits")
    if packet.get("state") != "flat" or not isinstance(limits, Mapping) \
            or not limits.get("agent_entry_possible"):
        return None
    entry_id = limits.get("agent_entry_id")
    return {
        "action": "ENTER",
        "views.price_action.ranked": [{"candidate_id": entry_id, "verdict": "TAKE",
                                       "conviction": 0.7, "reason_codes": [], "note": ""}],
        "entry_plan": {"side": "buy", "order_type": "LIMIT",
                       "entry": limits.get("buy_limit_max"), "sl": "<entry - stop>",
                       "tp1": "<>= 0.5R>", "tp2": "<between tp1 and tp3>",
                       "tp3": "<1R-5R>", "sl_after_tp1": "<structure or entry + costs>",
                       "sl_after_tp2": "<structure or tp1>", "time_limit_min": 120,
                       "pending_expiry_min": 30, "lots": limits.get("volume_min"),
                       "thesis": ""},
        "m15_bias": {"direction": "up", "levels": [], "invalidation": None, "scenario": ""},
    }


def manage_example(packet: Mapping[str, Any]) -> dict[str, Any] | None:
    keep = _keep(packet)
    if keep is None:
        return None
    return {"action": "MANAGE",
            "manage": {**keep, "op": "MODIFY", "sl": "<only toward safety>",
                       "reason": "<why>"}}
```

  `submission_warnings` memperingatkan (tanpa menolak) bila `action` ENTER tanpa
  `entry_plan`, MANAGE tanpa `manage`, atau `m15_bias.direction == "unclear"`.

- [ ] **Step 4: Implementasi `packet_view.py`**

```python
def plan_text(plan: object, *, with_step: bool) -> str:
    if not isinstance(plan, Mapping) or not plan.get("tp1"):
        return "plan -"
    steps = (f"tp1 {num(plan.get('tp1'))} (sl+ {num(plan.get('sl_after_tp1'))}) "
             f"tp2 {num(plan.get('tp2'))} (sl+ {num(plan.get('sl_after_tp2'))})")
    return f"plan {steps}" + (f" step {text(plan.get('step'), 2)}" if with_step else "")


def position_line(position: object) -> str:
    if not isinstance(position, Mapping):
        return "state position | -"
    return (f"state position | ticket {text(position.get('ticket'), 20)} "
            f"{clean(position.get('side'))} {num(position.get('lots'))} @ "
            f"{num(position.get('open_price'))} | sl {num(position.get('sl'))} tp "
            f"{num(position.get('tp'))} | initial sl {num(position.get('initial_sl'))} | now "
            f"{num(position.get('r_now'), '+.2f')}R, {num(position.get('minutes_open'), '.1f')}"
            f" min open, limit {_clock(position.get('time_limit_epoch'))}Z | "
            f"{plan_text(position.get('plan'), with_step=True)} | answer action MANAGE with "
            f"manage KEEP, CLOSE or MODIFY")


def pending_line(order: object) -> str:
    if not isinstance(order, Mapping):
        return "state pending | -"
    return (f"state pending | ticket {text(order.get('ticket'), 20)} "
            f"{clean(order.get('order_type'))} {num(order.get('price'))} sl "
            f"{num(order.get('sl'))} tp {num(order.get('tp'))} lots {num(order.get('lots'))} "
            f"expires {_clock(order.get('expiration_epoch'))}Z | market needs "
            f"{num(order.get('distance_from_quote'))} to fill | "
            f"{plan_text(order.get('plan'), with_step=False)} | answer action MANAGE with "
            f"manage KEEP, CANCEL or MODIFY")


def bias_line(bias: object, at: object) -> str | None:
    if not isinstance(bias, Mapping):
        return None
    levels = " ".join(num(level) for level in bias.get("levels") or ())
    return (f"last bias {_clock(at)}Z {clean(bias.get('direction'))} | levels "
            f"{levels or '-'} | invalidation {num(bias.get('invalidation'))} | "
            f"{text(bias.get('scenario'), 120)}")


def action_line(action: object) -> str | None:
    if not isinstance(action, Mapping):
        return None
    return (f"last action {clean(action.get('action_id'))} {clean(action.get('op'))} ticket "
            f"{text(action.get('ticket'), 20)} {clean(action.get('status'))} "
            f"({text(action.get('detail'), 60)}) {_clock(action.get('at_epoch'))}Z")
```

  `limits_line` menampilkan batas STOP, TP1, waktu dan pending seperti contoh di atas.
  `render_packet` menyisipkan, setelah `limits_line`: `position_line` bila `state` position,
  `pending_line` bila pending, lalu `bias_line` dan `action_line` bila ada; baris
  `suggestions (...)` dan kandidat hanya untuk `state == "flat"`. Baris terakhir menjadi
  `agents ... | next: template, decide (action HOLD/ENTER or MANAGE), submit --agent <name>
  before <expiry>`. `num(None)` harus menghasilkan `-` (periksa perilaku yang ada).

- [ ] **Step 5: Jalankan tes CLI**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_v6_operator_cli_v3.py tests/v6 -k "operator_cli or render or decisions or wait or docs"`
Expected: PASS setelah tes CLI lama diperbarui ke templat v3.

- [ ] **Step 6: Commit**

```bash
git add adapter/scripts/v6ops adapter/tests/v6
git commit -m "feat: show V6 plans and management state in the operator CLI"
```

---

## Task 18: Dokumen, skill dan `AGENTS.md`

**Files:**
- Modify: `docs/v6-operator.md` (rubrik tahap A, contoh paket/keputusan v3, tabel penolakan)
- Modify: `docs/v6-wire-contract.md` (intent v2, perintah manajemen, `v6.action.1`,
  `/v6/action`, snapshot `plan_step`/`time_limit_epoch`)
- Modify: `docs/v6-runbook.md` (EA 6.2.0, input, pengukuran jam kuotasi Monex, drill tahap A,
  pembaruan sesi)
- Modify: `.agents/skills/v6-trading/SKILL.md` lalu salin ke
  `.claude/skills/v6-trading/SKILL.md`
- Modify: `AGENTS.md` (loop, tetap < 12.000 karakter)
- Modify: `docs/superpowers/specs/2026-09-17-v6-m1-dynamic-management-design.md` (catat
  penyimpangan di kepala rencana ini)
- Test: `adapter/tests/v6/test_v6_operator_docs.py`, `test_agent_harness_parity.py`

**Isi yang wajib ada di `docs/v6-operator.md`:**
- Bagian baru "5.10 Rencana entry v2": arti setiap field `entry_plan`, aturan §2.2 spec,
  contoh buy LIMIT dan sell STOP lengkap.
- Bagian baru "5.11 SL+": pilih level struktur (higher low / lower high baru) bila ada,
  entry + biaya hanya bila struktur belum terbentuk, dan kutip ringkas temuan
  `knowledge/08` §7–8 (BE mekanis dan scale-out menurunkan ekspektasi; ledger mengukur
  dampaknya).
- Bagian baru "5.12 Mengelola posisi dan pending": kapan KEEP, CLOSE (struktur patah,
  berita mendadak, momentum berbalik kuat), MODIFY (SL ke struktur baru, TP3 ke level
  berikutnya, perpanjang waktu bila tren sehat), CANCEL (skenario pending gugur);
  aturan §2.3 spec.
- Bagian baru "5.13 Bias M15": arah, level, invalidasi, skenario; bias lama tampil di paket
  berikutnya.
- Bagian 5.9 lama (review KEEP/CANCEL) diganti rujukan ke 5.12.
- Contoh paket v3 dan keputusan v3 diregenerasi dari fixture (cara yang sama dengan contoh
  yang ada; tes docs membandingkannya).
- Tabel penolakan: `DECISION_MANAGE`, `DECISION_BIAS`, `DECISION_KIND`; hapus
  `DECISION_REVIEW`.
- Tabel `HoldReason`: `APP-V6-MANAGE-KEPT`, `APP-V6-MANAGE-SENT`, `APP-V6-MANAGE-REFUSED`.

**Isi yang wajib ada di skill (sama untuk ketiga agen):**
- "Deciding a packet" bercabang menurut `state`:
  - `flat`: HOLD atau ENTER dengan `entry_plan` v2 (lot 0,01–0,03, TP1–TP3, SL+, batas
    waktu, pending expiry), isi `m15_bias`.
  - `pending`/`position`: `action MANAGE` dengan `manage` KEEP/CANCEL/CLOSE/MODIFY, isi
    `m15_bias`.
- Laporan satu baris per paket tetap; untuk MANAGE sebutkan op dan hasil
  (`APP-V6-MANAGE-*`).
- Exit code `wait` tidak berubah; tambahkan: selama pembaruan sesi setelah rollover, `wait`
  keluar dengan 3 (timeout), jadi loop berjalan terus.
- Kalimat "Start a new chat each trading day" diganti: sesi berjalan 24 jam; mulai chat baru
  bila konteks sudah berat, lalu ucapkan "Mulai trading skrg" (sesi yang sama dilanjutkan).

**`AGENTS.md`:** langkah 3 loop menyebut `action`, `entry_plan` v2, `manage` dan
`m15_bias`; kalimat "Start a new chat each trading day" diganti seperti di skill. Periksa
panjang: `python -c "print(len(open('AGENTS.md', encoding='utf-8').read()))"` < 12000.

- [ ] **Step 1: Tulis perubahan dokumen di atas**
- [ ] **Step 2: Salin skill**

```bash
cp .agents/skills/v6-trading/SKILL.md .claude/skills/v6-trading/SKILL.md
```

- [ ] **Step 3: Jalankan tes dokumen dan paritas harness**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_v6_operator_docs.py tests/v6/test_agent_harness_parity.py`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add docs AGENTS.md .agents/skills/v6-trading/SKILL.md .claude/skills/v6-trading/SKILL.md adapter/tests/v6
git commit -m "docs: describe V6 phase A plans, SL+ and position management"
```

---

## Task 19: Dashboard — rencana dan aksi

**Files:**
- Modify: `adapter/app/v6/dashboard_queries.py` (≤ 400 baris; bila penuh, buat
  `dashboard_actions.py`)
- Modify: `adapter/app/routes/v6_dashboard.py` (`/v6/api/overview` menambah `actions` dan
  `plan`)
- Modify: `adapter/app/templates/v6.html`, `adapter/app/static/v6_render.js`
  (panel "Plan & actions", teks lewat `textContent`)
- Test: `adapter/tests/v6/test_v6_dashboard.py` (tambah kasus)

**Interfaces:**
- Produces: `overview["actions"]` = 10 aksi terbaru (`action_id`, `command`, `ticket`,
  `status`, `detail`, `created_at`, `updated_at`); `overview["plan"]` = rencana intent aktif
  (`tp1`, `tp2`, `sl_after_tp1`, `sl_after_tp2`, `plan_step`, `time_barrier_s`) atau null.

- [ ] **Step 1: Tes yang gagal**

```python
def test_the_overview_lists_actions_and_the_active_plan(dashboard_client, seeded_ledger):
    seeded_ledger.actions.insert(action_row())       # helper dari test_ledger_plan_actions
    body = dashboard_client.get("/v6/api/overview").json()
    assert body["actions"][0]["action_id"] == "m3a7q2z5k6pw"
    assert body["plan"] is None or set(body["plan"]) >= {"tp1", "plan_step"}
```

  Sesuaikan nama fixture dengan yang dipakai `test_v6_dashboard.py` (klien dashboard dan
  container dengan ledger); impor `action_row` dari `test_ledger_plan_actions`.

- [ ] **Step 2: Implementasi query, rute dan tampilan** (panel baru di bawah panel intent,
  memakai pola render yang sama; tanpa `innerHTML` untuk data).
- [ ] **Step 3: Jalankan tes dashboard**

Run: `timeout 560 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_v6_dashboard.py`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add adapter/app/v6/dashboard_queries.py adapter/app/routes/v6_dashboard.py adapter/app/templates/v6.html adapter/app/static/v6_render.js adapter/tests/v6/test_v6_dashboard.py
git commit -m "feat: show V6 plans and management actions on the dashboard"
```

---

## Task 20: Verifikasi akhir, jam kuotasi Monex dan kriteria selesai tahap A

- [ ] **Step 1: Seluruh suite dengan coverage**

Run (dari `adapter/`): `timeout 580 ../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --cov=app/v6 --cov=scripts --cov-report=term-missing`
Expected: semua lulus; total ≥ 80%; `app/v6/risk/*`, `deliberation/protocol.py`,
`deliberation/management.py`, `deliberation/plan_rules.py` 100%.

- [ ] **Step 2: Ukuran file dan fungsi**

```bash
cd adapter && find app scripts -name "*.py" -exec wc -l {} + | sort -n | tail -5
```

Expected: tidak ada file di atas 400 baris.

- [ ] **Step 3: Pindai rahasia di diff**

```bash
git diff main...HEAD | grep -iE "^\+.*(token|secret|hmac_key|password)\s*[:=]\s*['\"][A-Za-z0-9]{16,}" || echo clean
```

Expected: `clean`.

- [ ] **Step 4: Ukur jam kuotasi Monex**

Dengan bar M1 yang sudah ada di BarStore (backfill Monex), cari jeda harian tanpa bar:

```bash
cd adapter && ../.venv/Scripts/python.exe <scratchpad>/quote_gap.py
```

`quote_gap.py` (scratchpad) membaca `v6_bars` M1 tujuh hari terakhir lewat `BarStore`
(atau langsung SQLite dengan query berparameter), mengelompokkan menit UTC tanpa bar di hari
kerja, dan mencetak rentang harian yang selalu kosong. Hasilnya dipakai pengguna untuk
mengisi `V6_BROKER_QUOTE_GAP_UTC` (format `HH:MM-HH:MM`) di `adapter/.env`; perubahan `.env`
hanya dilakukan pengguna atau dengan izin eksplisitnya, lalu adapter di-restart.

- [ ] **Step 5: Terapkan dan jalankan drill Task 16** (dengan persetujuan pengguna)

- [ ] **Step 6: Satu hari London–NY**

Kriteria: minimal satu posisi dikelola lewat paket M15 (KEEP/MODIFY/CLOSE) dan satu langkah
SL+ dieksekusi EA (`v6_plan_steps`), tanpa pelanggaran invarian spec §7 di ledger
(pemeriksaan: tidak ada `v6_actions` APPLIED dengan SL lebih lebar dari SL sebelumnya;
setiap `v6_intents` aktif punya `sl > 0`; tidak ada dua intent aktif).

- [ ] **Step 7: Laporkan ke pengguna** hasil tes, drill, jam kuotasi Monex, dan hal yang
  belum lulus; tahap B (paket per menit) direncanakan terpisah setelah tahap A diterima.
