# V6 Tahap B: Paket per Menit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Operator menerima satu paket `m1` di setiap close bar M1 (selain paket `m15` yang tetap prioritas), menjawabnya dalam 50 detik (atau `--quick` untuk "tidak ada perubahan"), dan jawaban itu bisa entry, mengubah, memotong atau membiarkan trade V6 lewat jalur yang sama dengan paket `m15`.

**Architecture:** EA 6.3.0 mengirim snapshot menit `v6.minute.1` ke `POST /v6/minute` setiap close M1. Route menyimpan bar M1 ke BarStore dan menaruh snapshot di inbox menit (satu slot, terbaru menang). Worker menit memakai konteks M15 terbaru dari worker M15 (fitur, level, event kalender, view yang dipakai siklus M15), menukar quote, akun, eksposur dan waktu dengan data menit, menghitung ulang gate, lalu membangun paket `m1` lewat `build_packet` yang sama dan menawarkannya di `OperatorQueue` yang sama (paket `m15` menggantikan paket `m1` yang terbuka; paket `m1` tidak ditawarkan selama paket `m15` terbuka). Keputusan `m1` divalidasi oleh `decision_minute.py` (view dan bias boleh null; view warisan M15 dipakai) dan diterapkan oleh jalur operator engine yang sudah ada (`_resolve` untuk ENTER, `_manage_result` untuk MANAGE). Setiap menit dicatat satu baris di `v6_minute_cycles`.

**Tech Stack:** Python 3.12, FastAPI, pydantic v2 strict/frozen, SQLite (WAL), asyncio; MQL5 (MetaTrader 5, MetaEditor64).

**Spec:** `docs/superpowers/specs/2026-09-17-v6-m1-dynamic-management-design.md` (bagian 1, 2.1, 2.4–2.7, 3.1, 4.1–4.2, 4.5–4.6, 5, 6, 8, 9 tahap B, 10). Tahap A sudah selesai di cabang `layering` (`docs/superpowers/plans/2026-09-17-v6-phase-a-plan-management.md`).

## Global Constraints

- DEMO saja, mutlak: konfigurasi, API operator, intent builder dan EA tetap menolak REAL dan CONTEST; paket `m1` hanya dibangun lewat `build_packet`, yang memeriksa kebijakan akun.
- File Python ≤ 400 baris, fungsi < 50 baris, type hint di mana-mana; file utama EA ≤ 300 baris, `.mqh` ≤ 400 baris (`tests/v6/test_golden_contract.py`).
- Model pydantic strict dan frozen; dataclass frozen; SQL berparameter; epoch UTC bilangan bulat; tanpa `print()` di kode app.
- Rahasia hanya lewat `SecretStr`; jangan pernah membuka atau mencetak `adapter/.env`.
- Setiap edit `.mq5`/`.mqh`: salin ke folder terminal `MQL5\Experts` dan kompilasi dengan MetaEditor di giliran yang sama; kompilasi baris perintah **tidak** memuat ulang EA yang sedang berjalan, jadi pengguna harus memasang ulang EA (atau me-restart MT5) sebelum EA baru berlaku.
- Tenggat: paket `m15` `V6_OPERATOR_DEADLINE_S` (180 s), paket `m1` `V6_M1_DEADLINE_S` (default 50, 20–55).
- Snapshot menit basi setelah `V6_MINUTE_STALE_S` (default 10, 3–30).
- `V6_MINUTE_PACKETS` (default `true` di tahap B): `false` mematikan paket `m1` tanpa mematikan penyimpanan bar menit.
- Paket `m1`: 60 bar M1 tertutup, `m1_state`, bias M15 terakhir, `limits`, keadaan, jarak harga ke entry/SL/TP1–TP3, aksi terakhir; tidak ada saran detektor.
- Aturan antrean: paket `m15` menarik (menggantikan) paket `m1` yang terbuka; selama paket `m15` terbuka tidak ada paket `m1` baru; paket `m1` yang belum dijawab diganti paket `m1` berikutnya ("tidak ada perubahan"); paket `m1` dijeda selama blok rollover dan saat sesi tidak armed.
- Keputusan `m1`: `views` dan `m15_bias` boleh `null`; ENTER dari paket `m1` memakai view (stance dan pengali) keputusan `m15` terakhir yang diterima, atau view baseline siklus itu bila tak terjawab; veto di view M15 memblokir entry `m1` sampai paket `m15` berikutnya.
- `OP submit --agent <AGENT> --quick` mengirim "tidak ada perubahan" tanpa menulis file (HOLD untuk `flat`, `manage.op = KEEP` untuk `pending`/`position`); pada paket `m15` membawa bias M15 terakhir dengan `carried: true` (atau `unclear`) dan view baseline.
- Keputusan v1/v2 pensiun: API operator menolaknya dengan `DECISION_SCHEMA`.
- Commit per tugas, format conventional, tanpa baris atribusi.

## Penyimpangan dari spec (disengaja, dicatat di dokumen pada Task 14)

- Snapshot menit memakai blok yang sama dengan snapshot M15 (`account`, `quote`, `ticks`, `positions`, `pending_orders`, `day`, `ea_state`) alih-alih subset field yang didaftar spec §3.1: satu penulis JSON di EA, satu model di adapter, dan `day.trades_today` serta status EA ikut segar setiap menit (ukuran tetap < 4 KB).
- Paket `m15` tetap membawa 30 bar M1 (M5 36 bar menutup rentang yang lebih panjang); hanya paket `m1` yang membawa 60 bar M1.
- Tenggat paket `m15` tetap `V6_OPERATOR_DEADLINE_S` (tidak ada `V6_M15_DEADLINE_S`, sama seperti tahap A).
- Bias pada keputusan `m1` tidak mengganti bias M15 yang diingat: hanya keputusan `m15` yang memperbarui `last_bias`.
- `v6_minute_cycles` mencatat setiap menit yang diproses, termasuk menit yang dilewati (dengan alasannya), supaya persentase paket terjawab dan waktu siklus menit bisa diukur dari ledger.

---

## Peta file

**Baru (adapter):**

| File | Tanggung jawab |
|---|---|
| `adapter/app/v6/schemas/minute.py` | model `MinuteSnapshot` (`v6.minute.1`) |
| `adapter/app/routes/v6_minute.py` | `POST /v6/minute` |
| `adapter/app/v6/ledger_minutes.py` | tabel `v6_minute_cycles`, `MinuteStore`, `MinuteRow`, `MinuteStats` |
| `adapter/app/v6/providers/operator_queue_types.py` | record antrean operator (dipindah dari `operator_queue.py`) |
| `adapter/app/v6/schemas/operator_minute.py` | blok paket `m1_state` (`MinuteMove`, `MinuteState`) |
| `adapter/app/v6/deliberation/minute_packet.py` | bar M1 tertutup dan `m1_state` sebuah paket `m1` |
| `adapter/app/v6/deliberation/packet_text.py` | helper skalar dan teks paket (dipindah dari `operator_packet.py`) |
| `adapter/app/v6/deliberation/decision_minute.py` | validasi keputusan untuk paket `m1` |
| `adapter/app/v6/deliberation/minute_context.py` | `MarketContext` sebuah siklus menit |
| `adapter/app/v6/deliberation/minute_flow.py` | mixin `MinuteFlow` (siklus `m1` di engine) |
| `adapter/app/v6/runtime/minute_worker.py` | `MinuteRuntime`: aturan lewati, siklus, pencatatan |
| `adapter/app/v6/dashboard_minutes.py` | ringkasan paket menit untuk dashboard |
| `adapter/scripts/v6replay/minutes.py` | replay ritme menit |

**Baru (EA):** `ea/QlipV6/Cadence.mqh` (irama snapshot M15 dipindah dari file utama, plus snapshot menit).

**Diubah:** `config.py`, `adapter/.env.example`, `runtime/ea_state.py`, `app/main.py`, `ledger_cycles.py`, `providers/operator_queue.py`, `schemas/operator_parts.py`, `schemas/operator.py`, `schemas/operator_plan.py`, `deliberation/operator_packet.py`, `deliberation/packet_extras.py`, `deliberation/operator_decision.py`, `deliberation/decision_v3.py`, `deliberation/trade_state.py`, `deliberation/operator_flow.py`, `deliberation/engine.py`, `deliberation/cycle_draft.py`, `runtime/service.py`, `runtime/wiring.py`, `container.py`, `routes/v6_status_view.py`, `routes/v6_dashboard.py`, `templates/v6.html`, `static/v6.js`, `scripts/v6_operator.py`, `scripts/v6ops/{decisions,waiting,packet_view}.py`, `scripts/v6_replay.py`, EA (`QlipV6_XAUUSD.mq5`, `Market.mqh`, `Snapshot.mqh`), dokumen dan skill.

---

## Task 1: Kunci konfigurasi tahap B

**Files:**
- Modify: `adapter/app/v6/config.py` (blok tahap A, sekitar baris 130–135)
- Modify: `adapter/.env.example`
- Test: `adapter/tests/v6/test_config_phase_b.py`

**Interfaces:**
- Produces: `V6Settings.minute_packets: bool` (default `True`), `V6Settings.m1_deadline_s: int` (default 50, 20–55), `V6Settings.minute_stale_s: int` (default 10, 3–30).

- [ ] **Step 1: Tulis tes yang gagal**

```python
"""Phase B settings: minute packets, their deadline and the minute staleness."""

from __future__ import annotations

from typing import Any

import pytest

from app.v6.config import V6Settings


def settings(**overrides: Any) -> V6Settings:
    return V6Settings(_env_file=None, **overrides)


def test_phase_b_defaults() -> None:
    config = settings()
    assert config.minute_packets is True
    assert (config.m1_deadline_s, config.minute_stale_s) == (50, 10)


@pytest.mark.parametrize("overrides", [
    {"m1_deadline_s": 19}, {"m1_deadline_s": 56},
    {"minute_stale_s": 2}, {"minute_stale_s": 31},
])
def test_minute_windows_stay_in_bounds(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        settings(**overrides)


def test_minute_packets_can_be_switched_off() -> None:
    config = settings(minute_packets=False, m1_deadline_s=40, minute_stale_s=5)
    assert (config.minute_packets, config.m1_deadline_s, config.minute_stale_s) == (False, 40, 5)
```

- [ ] **Step 2: Jalankan dan pastikan gagal**

Run (dari `adapter/`): `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_config_phase_b.py`
Expected: FAIL (`AttributeError: 'V6Settings' object has no attribute 'minute_packets'`).

- [ ] **Step 3: Tambah kunci di `config.py`**

Tepat setelah `pending_expiry_max_minutes: int = Field(default=60, ge=1)`:

```python
    # Phase B (spec section 4.5): one operator packet per closed M1 bar besides the M15 one.
    minute_packets: bool = True
    m1_deadline_s: int = Field(default=50, ge=20, le=55)
    minute_stale_s: int = Field(default=10, ge=3, le=30)
```

- [ ] **Step 4: Dokumentasikan di `adapter/.env.example`**

Tepat setelah baris `V6_PENDING_EXPIRY_MAX_MINUTES=60`:

```dotenv
# Phase B: one operator packet per closed M1 bar besides the M15 packet.
V6_MINUTE_PACKETS=true
V6_M1_DEADLINE_S=50           # 20-55: an m1 packet stays open until its bar close + this
V6_MINUTE_STALE_S=10          # 3-30: an older minute snapshot is not traded on
```

- [ ] **Step 5: Jalankan tes**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_config_phase_b.py tests/v6/test_config_phase_a.py tests/v6/test_v6_config.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add adapter/app/v6/config.py adapter/.env.example adapter/tests/v6/test_config_phase_b.py
git commit -m "feat: add V6 phase B minute packet settings"
```

---

## Task 2: Snapshot menit `v6.minute.1`

**Files:**
- Create: `adapter/app/v6/schemas/minute.py`
- Create: `adapter/tests/v6/golden/minute_sample.json`
- Test: `adapter/tests/v6/test_minute_schema.py`

**Interfaces:**
- Consumes: `schemas.snapshot` (`_Strict`, `AccountBlock`, `QuoteBlock`, `TickStatsBlock`, `PositionBlock`, `PendingOrderBlock`, `DayBlock`, `EaStateBlock`, `BarRow`, `SymbolName`, `MAX_POSITIONS`, `validate_bar_rows`, `rows_to_bars`).
- Produces: `MINUTE_SCHEMA = "v6.minute.1"`, `MINUTE_ID_PREFIX = "Q6M-"`, `M1_S = 60`, `class MinuteSnapshot` dengan properti `bar_close_epoch: int` dan metode `to_bar() -> Bar`; `minute_snapshot_id(login: str, bar_open_epoch: int) -> str`.

- [ ] **Step 1: Buat golden `adapter/tests/v6/golden/minute_sample.json`**

```json
{
  "schema_version": "v6.minute.1",
  "snapshot_id": "Q6M-10000001-1789565400",
  "symbol": "XAUUSD",
  "sent_at_epoch": 1789565461,
  "server_gmt_offset_s": 10800,
  "bar_open_epoch": 1789565400,
  "bar": [1789565400, 4535.20, 4536.05, 4534.90, 4535.84, 212, 18],
  "account": {"login":"10000001","trade_mode":"DEMO","server":"MetaQuotes-Demo","currency":"USD","leverage":200,"balance":92429.34,"equity":92429.34,"margin":0.00,"free_margin":92429.34,"margin_level":0.00},
  "quote": {"bid":4535.84,"ask":4536.02,"spread_points":18,"time_msc":1789565460812},
  "ticks": {"window_s":60,"quote_count":264,"max_gap_ms":1450,"spread_p50_points":18.0,"spread_p95_points":22.0,"mid_rv":0.000000211733},
  "positions": [],
  "pending_orders": [],
  "day": {"day_start_equity":92429.34,"realized_today":0.00,"trades_today":0},
  "ea_state": {"ea_version":"6.3.0","execute_enabled":true,"halted":false,"local_breaker":"none","outbox_pending":0,"last_intent_id":""}
}
```

- [ ] **Step 2: Tulis tes yang gagal `adapter/tests/v6/test_minute_schema.py`**

```python
"""The minute snapshot v6.minute.1 (spec section 3.1)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest
from pydantic import ValidationError

from app.v6.schemas.minute import MINUTE_SCHEMA, MinuteSnapshot, minute_snapshot_id
from app.v6.types import Bar

GOLDEN: Final[Path] = Path(__file__).resolve().parent / "golden" / "minute_sample.json"


def sample(**changes: Any) -> dict[str, Any]:
    return {**json.loads(GOLDEN.read_text(encoding="utf-8")), **changes}


def parse(document: dict[str, Any]) -> MinuteSnapshot:
    return MinuteSnapshot.model_validate_json(json.dumps(document))


def test_the_golden_sample_validates() -> None:
    minute = parse(sample())
    assert minute.schema_version == MINUTE_SCHEMA
    assert minute.bar_close_epoch == 1789565460
    assert minute.to_bar() == Bar(t=1789565400, o=4535.2, h=4536.05, l=4534.9, c=4535.84,
                                  tv=212, spr=18)


def test_the_golden_sample_names_every_field() -> None:
    assert set(sample()) == set(MinuteSnapshot.model_fields)


def test_the_id_names_the_login_and_the_bar() -> None:
    assert minute_snapshot_id("10000001", 1789565400) == "Q6M-10000001-1789565400"


@pytest.mark.parametrize("changes", [
    {"snapshot_id": "Q6M-10000001-1789565460"},                   # another bar
    {"bar_open_epoch": 1789565430, "snapshot_id": "Q6M-10000001-1789565430",
     "bar": [1789565430, 4535.2, 4536.05, 4534.9, 4535.84, 212, 18]},   # off the minute grid
    {"bar": [1789565340, 4535.2, 4536.05, 4534.9, 4535.84, 212, 18]},  # another minute
    {"bar": [1789565400, 4535.2, 4534.0, 4534.9, 4535.84, 212, 18]},   # high below open
    {"sent_at_epoch": 1789565399},                                  # sent before the bar
    {"schema_version": "v6.minute.2"},
])
def test_inconsistent_minutes_are_refused(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        parse(sample(**changes))


def test_unknown_fields_are_refused() -> None:
    with pytest.raises(ValidationError):
        parse(sample(extra=1))
```

- [ ] **Step 3: Jalankan dan pastikan gagal**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_minute_schema.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.v6.schemas.minute'`).

- [ ] **Step 4: Tulis `adapter/app/v6/schemas/minute.py`**

```python
"""
The minute snapshot `v6.minute.1` (spec section 3.1): one per closed M1 bar.

The EA sends it once, without a retry queue, to POST /v6/minute. It carries the closed
M1 bar and the same account, quote, tick, exposure, day and EA blocks as the M15
snapshot, so the minute worker can redo the gates at the minute close.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from ..types import TIMEFRAME_SECONDS, Bar
from .snapshot import (
    MAX_POSITIONS, AccountBlock, BarRow, DayBlock, EaStateBlock, PendingOrderBlock,
    PositionBlock, QuoteBlock, SymbolName, TickStatsBlock, _Strict, rows_to_bars,
    validate_bar_rows,
)

MINUTE_SCHEMA: Final[str] = "v6.minute.1"
MINUTE_ID_PREFIX: Final[str] = "Q6M-"
M1_S: Final[int] = TIMEFRAME_SECONDS["M1"]
MinuteSnapshotId = Annotated[str, StringConstraints(pattern=r"^Q6M-[0-9]{1,20}-[0-9]{1,12}$")]


def minute_snapshot_id(login: str, bar_open_epoch: int) -> str:
    return f"{MINUTE_ID_PREFIX}{login}-{bar_open_epoch}"


class MinuteSnapshot(_Strict):
    schema_version: Literal["v6.minute.1"]
    snapshot_id: MinuteSnapshotId
    symbol: SymbolName
    sent_at_epoch: int = Field(ge=0)
    server_gmt_offset_s: int = Field(ge=-14 * 3600, le=14 * 3600)
    bar_open_epoch: int = Field(ge=0)
    bar: BarRow
    account: AccountBlock
    quote: QuoteBlock
    ticks: TickStatsBlock
    positions: list[PositionBlock] = Field(max_length=MAX_POSITIONS)
    pending_orders: list[PendingOrderBlock] = Field(max_length=MAX_POSITIONS)
    day: DayBlock
    ea_state: EaStateBlock

    @field_validator("server_gmt_offset_s")
    @classmethod
    def _offset_on_half_hours(cls, value: int) -> int:
        if value % 1800 != 0:
            raise ValueError("server_gmt_offset_s must be a multiple of 1800")
        return value

    @model_validator(mode="after")
    def _check_minute(self) -> "MinuteSnapshot":
        validate_bar_rows([self.bar], "M1")
        checks = (
            (self.bar[0] == self.bar_open_epoch, "bar is not the snapshot's minute"),
            (self.snapshot_id == minute_snapshot_id(self.account.login, self.bar_open_epoch),
             "snapshot_id must be Q6M-<login>-<bar_open_epoch>"),
            (self.sent_at_epoch >= self.bar_open_epoch, "sent before the bar opened"),
        )
        problems = [message for ok, message in checks if not ok]
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @property
    def bar_close_epoch(self) -> int:
        return self.bar_open_epoch + M1_S

    def to_bar(self) -> Bar:
        return rows_to_bars([self.bar])[0]
```

- [ ] **Step 5: Jalankan tes**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_minute_schema.py`
Expected: PASS (7 tes). `validate_bar_rows` menolak bar di luar grid menit dan high di bawah open.

- [ ] **Step 6: Commit**

```bash
git add adapter/app/v6/schemas/minute.py adapter/tests/v6/golden/minute_sample.json adapter/tests/v6/test_minute_schema.py
git commit -m "feat: add the V6 minute snapshot v6.minute.1"
```

---
## Task 3: Inbox menit dan `POST /v6/minute`

**Files:**
- Modify: `adapter/app/v6/runtime/ea_state.py`
- Create: `adapter/app/routes/v6_minute.py`
- Modify: `adapter/app/main.py` (daftar `ROUTERS` dan docstring rute)
- Modify: `adapter/tests/test_security.py` (`V6_WRITE_ENDPOINTS`), `adapter/tests/v6/test_request_body_limit.py` dan `adapter/tests/v6/test_v6_ea_routes.py` (`V6_POST_PATHS`)
- Test: `adapter/tests/v6/test_v6_minute_route.py`, `adapter/tests/v6/test_minute_inbox.py`

**Interfaces:**
- Consumes: `MinuteSnapshot` (Task 2), `read_ea_body`, `require_active_container`, `BarStore.ingest(tf, bars) -> int`.
- Produces:
  - `ea_state.MINUTE_CYCLE_PREFIX = "m-"`, `minute_cycle_id_for(snapshot_id: str) -> str`;
  - `@dataclass(frozen=True) MinuteItem(cycle_id: str, minute: MinuteSnapshot, received_at: float)`;
  - `EaState.first_minute(snapshot_id: str) -> bool`, `EaState.offer_minute(item: MinuteItem) -> bool` (True bila menggantikan menit yang belum diproses), `async EaState.next_minute() -> MinuteItem`;
  - `EaStateView.minutes_superseded: int = 0`, `EaStateView.minute_pending: int = 0`, `EaStateView.last_minute_at: float | None = None`;
  - rute `POST /v6/minute`: 202 `{"accepted": true, "cycle_id": "m-…", "server_time_epoch": …}`, 200 `{"accepted": false, "duplicate": true, "cycle_id": …}` untuk id berulang, 400 untuk body tidak valid.

- [ ] **Step 1: Tes inbox yang gagal `adapter/tests/v6/test_minute_inbox.py`**

```python
"""The minute inbox: one slot, the newest minute wins, a minute id is taken once."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.v6.runtime.ea_state import (
    MINUTE_CYCLE_PREFIX, EaState, MinuteItem, minute_cycle_id_for,
)
from app.v6.schemas.minute import MinuteSnapshot

GOLDEN = Path(__file__).resolve().parent / "golden" / "minute_sample.json"


def minute(bar_open: int = 1789565400) -> MinuteSnapshot:
    document = json.loads(GOLDEN.read_text(encoding="utf-8"))
    document.update(bar_open_epoch=bar_open, snapshot_id=f"Q6M-10000001-{bar_open}",
                    sent_at_epoch=bar_open + 61)
    document["bar"][0] = bar_open
    return MinuteSnapshot.model_validate_json(json.dumps(document))


def item(bar_open: int, at: float) -> MinuteItem:
    snapshot = minute(bar_open)
    return MinuteItem(cycle_id=minute_cycle_id_for(snapshot.snapshot_id), minute=snapshot,
                      received_at=at)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_minute_cycle_ids_are_stable_and_prefixed() -> None:
    first = minute_cycle_id_for("Q6M-10000001-1789565400")
    assert first == minute_cycle_id_for("Q6M-10000001-1789565400")
    assert first.startswith(MINUTE_CYCLE_PREFIX) and len(first) == 18


def test_a_minute_id_is_taken_once() -> None:
    state = EaState()
    assert state.first_minute("Q6M-1-60") is True
    assert state.first_minute("Q6M-1-60") is False
    assert state.first_minute("Q6M-1-120") is True


@pytest.mark.anyio
async def test_the_newest_minute_wins() -> None:
    state = EaState()
    assert state.offer_minute(item(1789565400, 1.0)) is False
    assert state.offer_minute(item(1789565460, 2.0)) is True
    view = state.view()
    assert (view.minutes_superseded, view.minute_pending, view.last_minute_at) == (1, 1, 2.0)
    taken = await asyncio.wait_for(state.next_minute(), timeout=1)
    assert taken.minute.bar_open_epoch == 1789565460
```

- [ ] **Step 2: Jalankan dan pastikan gagal**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_minute_inbox.py`
Expected: FAIL (`ImportError: cannot import name 'MINUTE_CYCLE_PREFIX'`).

- [ ] **Step 3: Perluas `runtime/ea_state.py`**

1. Tambah impor `from collections import OrderedDict` dan `from ..schemas.minute import MinuteSnapshot`.
2. Tepat setelah `CYCLE_ID_HEX_CHARS`:

```python
MINUTE_CYCLE_PREFIX: Final[str] = "m-"
MINUTE_MEMORY: Final[int] = 32          # minute ids remembered against a repeated POST
```

3. Tepat setelah `cycle_id_for`:

```python
def minute_cycle_id_for(snapshot_id: str) -> str:
    """The m1 cycle id of a minute snapshot (m- plus 16 hex characters)."""
    digest = hashlib.sha256(snapshot_id.encode("utf-8")).hexdigest()
    return MINUTE_CYCLE_PREFIX + digest[:CYCLE_ID_HEX_CHARS]
```

4. Tepat setelah dataclass `InboxItem`:

```python
@dataclass(frozen=True)
class MinuteItem:
    cycle_id: str
    minute: MinuteSnapshot
    received_at: float
```

5. `EaStateView` mendapat tiga field terakhir:

```python
    minutes_superseded: int = 0
    minute_pending: int = 0
    last_minute_at: float | None = None
```

6. Di `EaState.__init__`, setelah `self._inbox`:

```python
        self._minutes: asyncio.Queue[MinuteItem] = asyncio.Queue(maxsize=INBOX_MAXSIZE)
        self._seen_minutes: OrderedDict[str, None] = OrderedDict()
        self._minutes_superseded = 0
        self._last_minute_at: float | None = None
```

7. Ganti isi `offer_snapshot` dengan helper bersama, dan tambah metode menit:

```python
    @staticmethod
    def _replace_newest(queue: asyncio.Queue, item: object) -> bool:
        """Queue `item` in a one-slot queue; True when it replaced an unconsumed item."""
        try:
            queue.get_nowait()
            replaced = True
        except asyncio.QueueEmpty:
            replaced = False
        # Single-threaded: the slot freed above cannot be taken in between.
        queue.put_nowait(item)
        return replaced

    def offer_snapshot(self, item: InboxItem) -> bool:
        """Queue `item`; True when it replaced a snapshot nobody had consumed yet."""
        replaced = self._replace_newest(self._inbox, item)
        if replaced:
            self._superseded += 1
        return replaced

    def first_minute(self, snapshot_id: str) -> bool:
        """True the first time a minute id arrives (the EA never resends a minute)."""
        if snapshot_id in self._seen_minutes:
            return False
        self._seen_minutes[snapshot_id] = None
        while len(self._seen_minutes) > MINUTE_MEMORY:
            self._seen_minutes.popitem(last=False)
        return True

    def offer_minute(self, item: MinuteItem) -> bool:
        """Queue a minute for the minute worker; the newest minute wins."""
        replaced = self._replace_newest(self._minutes, item)
        if replaced:
            self._minutes_superseded += 1
        self._last_minute_at = item.received_at
        self.touch(item.received_at)
        return replaced

    async def next_minute(self) -> MinuteItem:
        return await self._minutes.get()
```

8. `view()` mengisi field baru: `minutes_superseded=self._minutes_superseded, minute_pending=self._minutes.qsize(), last_minute_at=self._last_minute_at`.
9. Docstring modul: tambah satu kalimat "The minute inbox works the same way for the minute worker (`offer_minute`)."

- [ ] **Step 4: Jalankan tes inbox**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_minute_inbox.py tests/v6/test_ea_state.py`
Expected: PASS.

- [ ] **Step 5: Tes rute yang gagal `adapter/tests/v6/test_v6_minute_route.py`**

```python
"""POST /v6/minute: the minute bar is stored, the minute is queued once."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import create_app
from app.v6.clock import FakeClock
from app.v6.config import V6Settings

GOLDEN = Path(__file__).resolve().parent / "golden" / "minute_sample.json"
JSON_HEADERS = {"Content-Type": "application/json"}
NOW = 1789565462.0


def sample(**changes: Any) -> dict[str, Any]:
    return {**json.loads(GOLDEN.read_text(encoding="utf-8")), **changes}


def build(tmp_path: Path, db_path: Path, **overrides: Any) -> FastAPI:
    settings = V6Settings(_env_file=None, **({"enabled": True,
                                              "halt_file": str(tmp_path / "V6_HALT")}
                                             | overrides))
    return create_app(v6_settings=settings, clock=FakeClock(epoch=NOW),
                      v6_db_path=str(db_path), v6_tasks=False)


def post(client: TestClient, body: dict[str, Any] | bytes):
    content = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    return client.post("/v6/minute", content=content, headers=JSON_HEADERS)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "minute.db"


@pytest.fixture
def client(tmp_path: Path, db_path: Path) -> Iterator[TestClient]:
    with TestClient(build(tmp_path, db_path)) as test_client:
        yield test_client


def test_a_minute_is_accepted_and_its_bar_stored(client: TestClient, db_path: Path) -> None:
    reply = post(client, sample())
    assert reply.status_code == 202
    body = reply.json()
    assert body["accepted"] is True and body["cycle_id"].startswith("m-")
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT t, c FROM v6_bars WHERE tf = 'M1'").fetchall()
    assert rows == [(1789565400, 4535.84)]


def test_a_repeated_minute_is_a_duplicate(client: TestClient) -> None:
    assert post(client, sample()).status_code == 202
    again = post(client, sample())
    assert again.status_code == 200 and again.json()["duplicate"] is True


def test_an_invalid_minute_is_refused(client: TestClient) -> None:
    reply = post(client, sample(bar_open_epoch=1789565430))
    assert reply.status_code == 400


def test_the_route_is_404_when_v6_is_disabled(tmp_path: Path, db_path: Path) -> None:
    with TestClient(build(tmp_path, db_path, enabled=False)) as disabled:
        assert post(disabled, sample()).status_code == 404
```

- [ ] **Step 6: Jalankan dan pastikan gagal**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_v6_minute_route.py`
Expected: FAIL (404 untuk rute yang belum ada).

- [ ] **Step 7: Tulis `adapter/app/routes/v6_minute.py`**

```python
"""
POST /v6/minute (spec section 4.1): one minute snapshot per closed M1 bar.

The body passes `read_ea_body` (JSON only, no cross-site requests, a size cap and, in
execute mode, the V6 request signature), then the strict `v6.minute.1` model. A repeated
snapshot id answers 200 duplicate. The closed M1 bar goes into the BarStore before the
minute is queued for the minute worker (one slot, the newest wins), so a minute that is
never processed still leaves its bar.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..v6.container import V6Container, require_active_container
from ..v6.runtime.ea_state import MinuteItem, minute_cycle_id_for
from ..v6.schemas.minute import MinuteSnapshot
from .v6_ea_auth import read_ea_body

logger = logging.getLogger(__name__)

router = APIRouter(tags=["v6"])

ActiveContainer = Annotated[V6Container, Depends(require_active_container)]
MAX_REPORTED_ERRORS: Final[int] = 20
STORAGE_UNAVAILABLE: Final[str] = "V6 storage unavailable"


def _parse(raw: bytes) -> MinuteSnapshot:
    try:
        return MinuteSnapshot.model_validate_json(raw)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        raise HTTPException(status_code=400, detail=errors[:MAX_REPORTED_ERRORS]) from exc


async def _store_bar(container: V6Container, minute: MinuteSnapshot) -> None:
    try:
        await asyncio.to_thread(container.bar_store.ingest, "M1", (minute.to_bar(),))
    except sqlite3.Error:
        logger.exception("v6 minute %s: the bar was not stored", minute.snapshot_id)
        raise HTTPException(status_code=503, detail=STORAGE_UNAVAILABLE) from None


@router.post("/v6/minute", status_code=202)
async def v6_minute(request: Request, container: ActiveContainer) -> JSONResponse:
    raw = await read_ea_body(request, container)
    minute = _parse(raw)
    now = container.clock.now_epoch()
    state = container.ea_state
    state.touch(now)
    cycle_id = minute_cycle_id_for(minute.snapshot_id)
    if not state.first_minute(minute.snapshot_id):
        return JSONResponse(status_code=200, content={
            "accepted": False, "duplicate": True, "cycle_id": cycle_id})
    await _store_bar(container, minute)
    if state.offer_minute(MinuteItem(cycle_id=cycle_id, minute=minute, received_at=now)):
        logger.info("v6 minute %s superseded an unprocessed one", minute.snapshot_id)
    return JSONResponse(status_code=202, content={
        "accepted": True, "cycle_id": cycle_id, "server_time_epoch": int(now)})
```

- [ ] **Step 8: Daftarkan rute**

Di `adapter/app/main.py`: impor `v6_minute` di blok `from .routes import (...)`, tambah `v6_minute.router,` tepat setelah `v6_ea.router,` di `ROUTERS`, dan tambah baris docstring `/v6/minute  V6 minute snapshots (signed)  routes/v6_minute.py` di bawah baris `/v6/*`.

- [ ] **Step 9: Masukkan rute ke daftar penjaga**

- `adapter/tests/test_security.py`: tambah `"/v6/minute",` ke `V6_WRITE_ENDPOINTS`.
- `adapter/tests/v6/test_request_body_limit.py` dan `adapter/tests/v6/test_v6_ea_routes.py`: tambah `"/v6/minute"` ke `V6_POST_PATHS`.

- [ ] **Step 10: Jalankan tes**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_v6_minute_route.py tests/v6/test_minute_inbox.py tests/test_security.py tests/v6/test_request_body_limit.py tests/v6/test_v6_ea_routes.py`
Expected: PASS. Penjaga yang sudah ada (tanpa tanda tangan di mode execute → 401, `text/plain` → 415, body terlalu besar → 413, V6 mati → 404) kini juga berlaku untuk `/v6/minute`.

- [ ] **Step 11: Commit**

```bash
git add adapter/app/v6/runtime/ea_state.py adapter/app/routes/v6_minute.py adapter/app/main.py adapter/tests/test_security.py adapter/tests/v6/test_request_body_limit.py adapter/tests/v6/test_v6_ea_routes.py adapter/tests/v6/test_minute_inbox.py adapter/tests/v6/test_v6_minute_route.py
git commit -m "feat: accept V6 minute snapshots on /v6/minute"
```

---

## Task 4: Ledger `v6_minute_cycles`

**Files:**
- Create: `adapter/app/v6/ledger_minutes.py`
- Modify: `adapter/app/v6/ledger_cycles.py` (docstring, impor, skema, `self.minutes`)
- Test: `adapter/tests/v6/test_ledger_minutes.py`

**Interfaces:**
- Produces:
  - `MINUTE_OUTCOMES = ("SKIPPED", "ANSWERED", "UNANSWERED")`, `MINUTE_SCHEMA_DDL`;
  - `@dataclass(frozen=True) MinuteRow(cycle_id: str, bar_open_epoch: int, state: str, outcome: str, created_at: float, session_id: str = "", reason: str = "", action: str = "", status: str = "", hold_reason: str = "", agent: str = "", latency_ms: int = 0, tier0_ms: int = 0, intent_id: str = "")` dengan `to_dict() -> dict[str, object]`;
  - `@dataclass(frozen=True) MinuteStats(processed: int, offered: int, answered: int, entries: int, manages: int, tier0_p95_ms: int)` dengan properti `answered_pct: float | None` dan `to_dict()`;
  - `MinuteStore.record(row) -> bool`, `.recent(limit=20) -> tuple[MinuteRow, ...]`, `.stats(since_epoch: int) -> MinuteStats`, `.prune(older_than_epoch: int) -> int`;
  - `LedgerCycles.minutes: MinuteStore`.

- [ ] **Step 1: Tes yang gagal `adapter/tests/v6/test_ledger_minutes.py`**

```python
"""v6_minute_cycles: one row per processed minute, stats and pruning."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.v6.ledger_cycles import LedgerCycles
from app.v6.ledger_minutes import MinuteRow

T0 = 1789565400


def row(k: int, outcome: str = "ANSWERED", **changes: object) -> MinuteRow:
    values: dict[str, object] = {
        "cycle_id": f"m-{k:016x}", "bar_open_epoch": T0 + 60 * k, "state": "flat",
        "outcome": outcome, "created_at": float(T0 + 60 * k + 61), "tier0_ms": 10 + k}
    return MinuteRow(**(values | changes))  # type: ignore[arg-type]


@pytest.fixture
def ledger(tmp_path: Path) -> LedgerCycles:
    return LedgerCycles(tmp_path / "minutes.db")


def test_a_row_is_recorded_once(ledger: LedgerCycles) -> None:
    assert ledger.minutes.record(row(1)) is True
    assert ledger.minutes.record(row(1)) is False
    assert ledger.minutes.recent(5) == (row(1),)


def test_stats_count_offers_answers_and_actions(ledger: LedgerCycles) -> None:
    for item in (row(1, "SKIPPED", reason="GATES:SPREAD"), row(2), row(3, "UNANSWERED"),
                 row(4, action="ENTER", status="ENTER"),
                 row(5, state="position", action="MANAGE:CLOSE")):
        ledger.minutes.record(item)
    stats = ledger.minutes.stats(T0)
    assert (stats.processed, stats.offered, stats.answered) == (5, 4, 3)
    assert (stats.entries, stats.manages) == (1, 1)
    assert stats.answered_pct == 75.0 and stats.tier0_p95_ms == 15


def test_old_rows_are_pruned(ledger: LedgerCycles) -> None:
    ledger.minutes.record(row(1))
    ledger.minutes.record(row(100))
    assert ledger.minutes.prune(T0 + 60 * 50) == 1
    assert [item.bar_open_epoch for item in ledger.minutes.recent(5)] == [T0 + 6000]


@pytest.mark.parametrize("changes", [{"outcome": "LATE"}, {"latency_ms": -1},
                                     {"created_at": float("nan")}])
def test_bad_rows_are_refused(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        row(1, **changes)
```

- [ ] **Step 2: Jalankan dan pastikan gagal**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_ledger_minutes.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.v6.ledger_minutes'`).

- [ ] **Step 3: Tulis `adapter/app/v6/ledger_minutes.py`**

```python
"""
v6_minute_cycles (spec section 4.6): one row per closed M1 bar the minute worker processed.

`MinuteStore` shares the connection and lock of `LedgerCycles`; reach it as
`ledger_cycles.minutes`. A row says what became of the minute: SKIPPED (the reason says
why no packet was offered), ANSWERED (an agent's decision was accepted) or UNANSWERED
(timeout, or closed by a newer packet), with the adapter's own processing time. The
minute worker prunes rows older than three days.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from typing import Final

MINUTE_OUTCOMES: Final[tuple[str, ...]] = ("SKIPPED", "ANSWERED", "UNANSWERED")
MAX_TEXT_CHARS: Final[int] = 120
MAX_LIST_LIMIT: Final[int] = 500
P95: Final[float] = 0.95
ENTER_ACTION: Final[str] = "ENTER"
MANAGE_PREFIX: Final[str] = "MANAGE:"
_OUTCOME_LIST: Final[str] = ", ".join(f"'{outcome}'" for outcome in MINUTE_OUTCOMES)

# Every statement is repeatable; LedgerCycles runs them on open.
MINUTE_SCHEMA_DDL: Final[tuple[str, ...]] = (
    f"""CREATE TABLE IF NOT EXISTS v6_minute_cycles (
        cycle_id TEXT NOT NULL PRIMARY KEY, bar_open_epoch INTEGER NOT NULL,
        session_id TEXT NOT NULL DEFAULT '', state TEXT NOT NULL,
        outcome TEXT NOT NULL CHECK (outcome IN ({_OUTCOME_LIST})),
        reason TEXT NOT NULL DEFAULT '', action TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT '', hold_reason TEXT NOT NULL DEFAULT '',
        agent TEXT NOT NULL DEFAULT '', latency_ms INTEGER NOT NULL DEFAULT 0,
        tier0_ms INTEGER NOT NULL DEFAULT 0, intent_id TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_v6_minute_cycles_bar ON v6_minute_cycles(bar_open_epoch)",
)
_COLUMNS: Final[str] = ("cycle_id, bar_open_epoch, state, outcome, created_at, session_id,"
                        " reason, action, status, hold_reason, agent, latency_ms, tier0_ms,"
                        " intent_id")
_INSERT_SQL: Final[str] = (f"INSERT OR IGNORE INTO v6_minute_cycles ({_COLUMNS})"
                           f" VALUES ({', '.join('?' * 14)})")
_RECENT_SQL: Final[str] = (f"SELECT {_COLUMNS} FROM v6_minute_cycles"
                           " ORDER BY bar_open_epoch DESC LIMIT ?")
_SINCE_SQL: Final[str] = ("SELECT outcome, action, tier0_ms FROM v6_minute_cycles"
                          " WHERE bar_open_epoch >= ?")
_PRUNE_SQL: Final[str] = "DELETE FROM v6_minute_cycles WHERE bar_open_epoch < ?"

WriteTx = Callable[[], AbstractContextManager]
Fetch = Callable[[str, tuple[object, ...]], list[tuple]]


@dataclass(frozen=True)
class MinuteRow:
    """What the minute worker did with one closed M1 bar."""

    cycle_id: str
    bar_open_epoch: int
    state: str
    outcome: str
    created_at: float
    session_id: str = ""
    reason: str = ""
    action: str = ""
    status: str = ""
    hold_reason: str = ""
    agent: str = ""
    latency_ms: int = 0
    tier0_ms: int = 0
    intent_id: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in MINUTE_OUTCOMES:
            raise ValueError(f"unknown minute outcome {self.outcome[:20]!r}")
        if not math.isfinite(self.created_at) or min(self.latency_ms, self.tier0_ms) < 0:
            raise ValueError("a minute row needs a finite time and non-negative durations")
        for name in ("reason", "status", "hold_reason"):
            object.__setattr__(self, name, getattr(self, name)[:MAX_TEXT_CHARS])

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class MinuteStats:
    processed: int = 0
    offered: int = 0
    answered: int = 0
    entries: int = 0
    manages: int = 0
    tier0_p95_ms: int = 0

    @property
    def answered_pct(self) -> float | None:
        return None if self.offered == 0 else round(100.0 * self.answered / self.offered, 1)

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "answered_pct": self.answered_pct}


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(P95 * len(ordered)) - 1)]


class MinuteStore:
    """Reads and writes v6_minute_cycles. Blocking: call it in a thread."""

    def __init__(self, write: WriteTx, fetchall: Fetch) -> None:
        self._write = write
        self._fetchall = fetchall

    def record(self, row: MinuteRow) -> bool:
        """False when this minute was recorded already."""
        values = (row.cycle_id, row.bar_open_epoch, row.state, row.outcome, row.created_at,
                  row.session_id, row.reason, row.action, row.status, row.hold_reason,
                  row.agent, row.latency_ms, row.tier0_ms, row.intent_id)
        with self._write() as conn:
            return conn.execute(_INSERT_SQL, values).rowcount == 1

    def recent(self, limit: int = 20) -> tuple[MinuteRow, ...]:
        bounded = max(1, min(int(limit), MAX_LIST_LIMIT))
        return tuple(MinuteRow(*values) for values in self._fetchall(_RECENT_SQL, (bounded,)))

    def stats(self, since_epoch: int) -> MinuteStats:
        rows = self._fetchall(_SINCE_SQL, (int(since_epoch),))
        offered = [row for row in rows if row[0] != "SKIPPED"]
        return MinuteStats(
            processed=len(rows), offered=len(offered),
            answered=sum(1 for row in offered if row[0] == "ANSWERED"),
            entries=sum(1 for row in offered if row[1] == ENTER_ACTION),
            manages=sum(1 for row in offered if str(row[1]).startswith(MANAGE_PREFIX)),
            tier0_p95_ms=_p95([int(row[2]) for row in rows]))

    def prune(self, older_than_epoch: int) -> int:
        with self._write() as conn:
            return conn.execute(_PRUNE_SQL, (int(older_than_epoch),)).rowcount
```

- [ ] **Step 4: Sambungkan ke `ledger_cycles.py`**

1. Ganti docstring modul (8 baris → 6 baris, supaya file tetap ≤ 400 baris):

```python
"""
Runtime persistence: cycles, views, candidates, breakers, sessions, intents and minutes.

Same SQLite file as `LedgerV6`, own connection (WAL, busy_timeout), one lock, bound
parameters only; JSON columns refuse credential-shaped keys like the V6 control log.
`self.intents`, `self.actions` and `self.minutes` share the connection and the lock.
"""
```

2. Tambah `from .ledger_minutes import MINUTE_SCHEMA_DDL, MinuteStore` setelah impor `ledger_intents`.
3. Tuple skema di `__init__` menjadi:

```python
            apply_schema(self._conn,
                         (*CYCLE_SCHEMA_DDL, *INTENT_SCHEMA_DDL, *ACTION_SCHEMA_DDL,
                          *MINUTE_SCHEMA_DDL),
                         (*CYCLE_COLUMN_MIGRATIONS, *INTENT_COLUMN_MIGRATIONS))
```

4. Setelah `self.actions = ActionStore(...)`: `self.minutes = MinuteStore(self._write, self._fetchall)`.

Periksa: `wc -l adapter/app/v6/ledger_cycles.py` ≤ 400.

- [ ] **Step 5: Jalankan tes**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_ledger_minutes.py tests/v6/test_ledger_cycles.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add adapter/app/v6/ledger_minutes.py adapter/app/v6/ledger_cycles.py adapter/tests/v6/test_ledger_minutes.py
git commit -m "feat: record every V6 minute cycle in v6_minute_cycles"
```

---

## Task 5: Antrean operator: pisah record, `closed()` dan jenis paket yang tertunda

**Files:**
- Create: `adapter/app/v6/providers/operator_queue_types.py`
- Modify: `adapter/app/v6/providers/operator_queue.py`
- Test: `adapter/tests/v6/test_operator_queue.py` (tambah tes)

`operator_queue.py` sudah 397 baris. Record-nya (baris 46–211: konstanta kode, `CloseReason`, `_positive`, `_elapsed_ms`, `PendingCycle`, `ClosedCycle`, `Accepted`, `Refused`, `SubmitResult`, `AgentSighting`, `QueueCounts`, `QueueStatus`) dipindah ke modul baru tanpa perubahan, dan diimpor ulang di `operator_queue.py` supaya semua impor lama tetap bekerja.

**Interfaces:**
- Produces: `OperatorQueue.closed(cycle_id: str) -> ClosedCycle | None`; `OperatorQueue.pending_kind() -> str | None` ("m15", "m1" atau None).

- [ ] **Step 1: Tes yang gagal (tambahkan ke `tests/v6/test_operator_queue.py`)**

```python
def test_closed_cycles_can_be_looked_up() -> None:
    queue, clock = _queue()
    sealed = of.packet()
    queue.offer(sealed)
    assert queue.pending_kind() == "m15"
    queue.withdraw(clock.now_epoch())
    closed = queue.closed(sealed.cycle_id)
    assert closed is not None and closed.reason == "cancelled" and closed.decision is None
    assert queue.pending_kind() is None and queue.closed("c-unknown") is None
```

(`_queue()` dan `of` adalah helper yang sudah ada di file tes ini; bila namanya berbeda, pakai helper pembuat `OperatorQueue` dan `FakeClock` yang dipakai tes lain di file yang sama.)

- [ ] **Step 2: Jalankan dan pastikan gagal**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_operator_queue.py -k closed_cycles`
Expected: FAIL (`AttributeError: 'OperatorQueue' object has no attribute 'pending_kind'`).

- [ ] **Step 3: Pindahkan record ke `operator_queue_types.py`**

Buat `adapter/app/v6/providers/operator_queue_types.py` dengan docstring
`"""Records of the operator decision queue (see operator_queue): codes, cycles, submissions and status."""`,
impor yang dibutuhkan (`math`, `collections.abc.Mapping`, `dataclasses.asdict, dataclass`, `types.MappingProxyType`, `typing.Final, Literal`, `..deliberation.operator_decision.ValidatedDecision`, `..schemas.operator.(DECISION_ERR_AGENT, DECISION_ERR_EXPIRED, DECISION_ERR_STALE, OperatorPacket)`, `.base.MS_PER_SECOND`), lalu salin apa adanya baris 46–211 dari `operator_queue.py` (dari `HISTORY_SIZE` sampai akhir kelas `QueueStatus`). Hapus baris itu dari `operator_queue.py` dan ganti dengan:

```python
from .operator_queue_types import (  # re-exported: tests and routes import them from here
    CLOSE_CANCELLED, CLOSE_DECIDED, CLOSE_EXPIRED, CLOSE_REASONS, CLOSE_SUPERSEDED,
    CLOSE_TIMEOUT, HISTORY_SIZE, MAX_WAIT_S, REFUSAL_CODES, REFUSAL_FOR_ERROR,
    REFUSE_AGENT_NOT_ALLOWED, REFUSE_ALREADY_DECIDED, REFUSE_EXPIRED, REFUSE_HASH_MISMATCH,
    REFUSE_INVALID, REFUSE_UNKNOWN_CYCLE, SUBMIT_ACCEPTED, VIA_SUBMIT, VIA_WAIT, Accepted,
    AgentSighting, ClosedCycle, CloseReason, PendingCycle, QueueCounts, QueueStatus,
    Refused, SubmitResult, _elapsed_ms, _positive,
)
```

Impor di `operator_queue.py` yang tidak dipakai lagi (`asdict`, `MappingProxyType`, `Mapping`, `Literal`, `math`, `MS_PER_SECOND`, `DECISION_ERR_*`) dihapus; pastikan dengan
`../.venv/Scripts/python.exe <scratchpad>/namecheck.py app/v6/providers/operator_queue.py app/v6/providers/operator_queue_types.py` (tidak ada nama tak terpakai atau tak terdefinisi).

- [ ] **Step 4: Tambah dua metode di `OperatorQueue` (bagian "operator side")**

```python
    def closed(self, cycle_id: str) -> ClosedCycle | None:
        """How a recent cycle closed (None once it left the bounded history)."""
        return self._closed.get(cycle_id)

    def pending_kind(self, now: float | None = None) -> str | None:
        """The packet kind of the pending cycle ("m15" or "m1"), None when nothing is open."""
        packet = self.current(now)
        return None if packet is None else packet.packet_kind
```

- [ ] **Step 5: Jalankan seluruh tes antrean dan operator**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_operator_queue.py tests/v6/test_v6_operator_routes.py tests/v6/test_engine_operator.py`
Expected: PASS. `wc -l` kedua file ≤ 400.

- [ ] **Step 6: Commit**

```bash
git add adapter/app/v6/providers/operator_queue.py adapter/app/v6/providers/operator_queue_types.py adapter/tests/v6/test_operator_queue.py
git commit -m "refactor: split the V6 operator queue records and expose closed cycles"
```

---
## Task 6: Paket `m1`

**Files:**
- Modify: `adapter/app/v6/schemas/operator_parts.py` (`PacketKind`, batas bar M1)
- Create: `adapter/app/v6/schemas/operator_minute.py`
- Modify: `adapter/app/v6/schemas/operator.py` (`m1_state`, cek per jenis, templat tanpa view/bias untuk `m1`)
- Create: `adapter/app/v6/deliberation/packet_text.py` (helper dipindah dari `operator_packet.py`)
- Create: `adapter/app/v6/deliberation/minute_packet.py`
- Modify: `adapter/app/v6/deliberation/packet_extras.py` (`bars_block` per jenis)
- Modify: `adapter/app/v6/deliberation/operator_packet.py` (`PacketRequest.kind`, tenggat, body `m1`)
- Modify: `adapter/tests/v6/test_golden_contract.py` (batas M1 paket `m15`)
- Test: `adapter/tests/v6/test_minute_packet.py`

**Interfaces:**
- Consumes: `bars_closed_by(bars, as_of, tf_seconds)`, `MarketContext`, `PacketRequest`, `build_packet`, `seal_packet`.
- Produces:
  - `operator_parts.PacketKind = Literal["m15", "m1"]`, `MAX_PACKET_M1_BARS = 60`, `M15_PACKET_M1_BARS = 30`;
  - `operator_minute.MinuteMove(bars: int, change: float, direction: "up"|"down"|"flat", strength: float)`, `operator_minute.MinuteState(atr_m1, range_15, last_5, last_15, quotes_per_s, max_gap_ms, distances: dict[str, float])`, `DISTANCE_KEYS = ("entry", "sl", "tp1", "tp2", "tp3")`;
  - `OperatorPacketBody.m1_state: MinuteState | None = None`; `DecisionTemplate.views: OperatorViews | None`, `DecisionTemplate.m15_bias: M15Bias | None` (keduanya `None` pada paket `m1`);
  - `minute_packet.MINUTE_PACKET_M1_BARS = 60`, `closed_minutes(context) -> tuple[Bar, ...]`, `atr(bars, period=14) -> float`, `minute_state(context, trade: Mapping[str, object] | None) -> MinuteState`;
  - `packet_extras.bars_block(context, kind="m15") -> Document`;
  - `PacketRequest.kind: PacketKind = "m15"`; paket `m1` kedaluwarsa pada bar close + `V6_M1_DEADLINE_S`.

- [ ] **Step 1: Tes yang gagal `adapter/tests/v6/test_minute_packet.py`**

```python
"""The m1 packet: closed M1 bars only, m1_state, its own deadline and template."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
from typing import Final

import pytest

from app.v6.cycle_types import DeskViews, MarketContext
from app.v6.deliberation.minute_packet import atr, closed_minutes, minute_state
from app.v6.deliberation.operator_packet import PacketRequest, build_packet
from app.v6.schemas.operator import OperatorPacket
from app.v6.types import Bar

from . import engine_fixtures_v6 as ef
from .test_engine_operator import OPERATOR

MINUTE_OPEN: Final[int] = ef.AS_OF + 120          # the second minute after the M15 close
MINUTE_CLOSE: Final[int] = MINUTE_OPEN + 60


def rising(count: int = 60, last_open: int = MINUTE_OPEN) -> tuple[Bar, ...]:
    """Bars opening 0.1 higher each minute: every true range is 0.20."""
    first = last_open - 60 * (count - 1)
    bars = []
    for i in range(count):
        o = round(4300.0 + 0.1 * i, 2)
        bars.append(Bar(t=first + 60 * i, o=o, h=round(o + 0.15, 2), l=round(o - 0.05, 2),
                        c=round(o + 0.1, 2), tv=50, spr=20))
    return tuple(bars)


def minute_context(bars: tuple[Bar, ...] = rising()) -> MarketContext:
    base = ef.market_context()
    return replace(base, bar_open_epoch=MINUTE_OPEN, as_of_epoch=MINUTE_CLOSE,
                   bars=MappingProxyType({**base.bars, "M1": bars}))


def test_only_closed_minutes_are_used() -> None:
    forming = Bar(t=MINUTE_CLOSE, o=1.0, h=1.0, l=1.0, c=1.0, tv=1, spr=1)
    context = minute_context(rising() + (forming,))
    assert closed_minutes(context)[-1].t == MINUTE_OPEN
    assert len(closed_minutes(context)) == 60


def test_minute_state_on_a_steady_rise() -> None:
    state = minute_state(minute_context(), None)
    assert atr(rising()) == pytest.approx(0.20)
    assert (state.atr_m1, state.range_15) == (0.2, 1.6)
    assert (state.last_5.direction, state.last_5.change, state.last_5.strength) == ("up", 0.5, 2.5)
    assert (state.last_15.change, state.last_15.strength) == (1.5, 7.5)
    assert state.distances == {}


def test_distances_of_a_position_are_measured_from_its_exit_side() -> None:
    context = minute_context()
    position = {"side": "buy", "open_price": 4302.0, "sl": 4296.0, "tp": 4316.0,
                "plan": {"tp1": 4308.0, "tp2": 4312.0}}
    distances = minute_state(context, position).distances
    bid = context.quote.bid
    assert distances == {"entry": round(4302.0 - bid, 2), "sl": round(4296.0 - bid, 2),
                         "tp1": round(4308.0 - bid, 2), "tp2": round(4312.0 - bid, 2),
                         "tp3": round(4316.0 - bid, 2)}


def sealed_minute_packet() -> OperatorPacket:
    config = ef.settings(**OPERATOR)
    context = minute_context()
    built = build_packet(PacketRequest(
        context=context, gates=(), offered=(), baseline=DeskViews(),
        remaining_loss_usd=100.0, session_id="sess-1", armed=True,
        now=float(MINUTE_CLOSE + 1), kind="m1"), config)
    assert isinstance(built, OperatorPacket), built
    return built


def test_an_m1_packet_has_its_own_shape() -> None:
    packet = sealed_minute_packet()
    config = ef.settings(**OPERATOR)
    assert (packet.packet_kind, packet.bar_close_epoch) == ("m1", MINUTE_CLOSE)
    assert packet.expires_at_epoch == MINUTE_CLOSE + config.m1_deadline_s
    assert len(packet.bars.M1) == 60 and packet.bars.M15 == () and packet.bars.H1 == ()
    assert packet.m1_state is not None and packet.m1_state.last_5.direction == "up"
    assert packet.limits.agent_entry_id.endswith(str(MINUTE_OPEN))
    template = packet.decision_template
    assert (template.packet_kind, template.action, template.views, template.m15_bias) == (
        "m1", "HOLD", None, None)
```

- [ ] **Step 2: Jalankan dan pastikan gagal**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_minute_packet.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.v6.deliberation.minute_packet'`).

- [ ] **Step 3: `operator_parts.py`**

```python
MAX_PACKET_M1_BARS: Final[int] = 60          # an m1 packet: the last hour of closed M1 bars
M15_PACKET_M1_BARS: Final[int] = 30          # an m15 packet shows the last 30 of them
```

(ganti baris `MAX_PACKET_M1_BARS: Final[int] = 30`) dan `PacketKind = Literal["m15", "m1"]`.

- [ ] **Step 4: Tulis `adapter/app/v6/schemas/operator_minute.py`**

```python
"""The `m1_state` block of an m1 packet (spec section 1): what the closed M1 bars did."""

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field, field_validator

from .operator_parts import Frozen

MoveDirection = Literal["up", "down", "flat"]
DISTANCE_KEYS: Final[tuple[str, ...]] = ("entry", "sl", "tp1", "tp2", "tp3")


class MinuteMove(Frozen):
    """The move over the last `bars` closed M1 bars; strength is |change| / ATR(M1)."""

    bars: int = Field(ge=1, le=60)
    change: float
    direction: MoveDirection
    strength: float = Field(ge=0)


class MinuteState(Frozen):
    """ATR(14) and 15-bar range of the closed M1 bars, the 5- and 15-bar moves, the quote
    rate of the minute and, for a managed trade, each level minus the price that triggers it."""

    atr_m1: float = Field(ge=0)
    range_15: float = Field(ge=0)
    last_5: MinuteMove
    last_15: MinuteMove
    quotes_per_s: float = Field(ge=0)
    max_gap_ms: int = Field(ge=0)
    distances: dict[str, float] = Field(default_factory=dict, max_length=len(DISTANCE_KEYS))

    @field_validator("distances")
    @classmethod
    def _known_levels(cls, value: dict[str, float]) -> dict[str, float]:
        unknown = sorted(set(value) - set(DISTANCE_KEYS))
        if unknown:
            raise ValueError(f"unknown distance keys {unknown}")
        return value
```

- [ ] **Step 5: `schemas/operator.py`**

1. Impor `from .operator_minute import MinuteState` dan `M1_S: Final[int] = TIMEFRAME_SECONDS["M1"]` di sebelah `M15_S`.
2. `OperatorPacketBody` mendapat field terakhir `m1_state: MinuteState | None = None`.
3. Di `_check_body`, ganti cek pertama dan tambah satu cek:

```python
        bar_s = M15_S if self.packet_kind == "m15" else M1_S
        checks = (
            (self.bar_close_epoch == self.bar_open_epoch + bar_s,
             "bar_close is not the bar open plus the packet's bar"),
            ((self.packet_kind == "m1") == (self.m1_state is not None),
             "an m1 packet carries m1_state, an m15 packet does not"),
```

(cek lain tetap).
4. `DecisionTemplate`: `views: OperatorViews | None` dan `m15_bias: M15Bias | None`.
5. `decision_template`:

```python
def decision_template(body: OperatorPacketBody, digest: str) -> DecisionTemplate:
    """HOLD (or KEEP when managing) for the first agent; an m15 template carries the rules
    views and the last bias, an m1 template neither (both may stay null, spec 2.1)."""
    manage = _keep(body)
    m15 = body.packet_kind == "m15"
    return DecisionTemplate(
        schema_version=DECISION_SCHEMA, packet_kind=body.packet_kind, cycle_id=body.cycle_id,
        packet_hash=digest, agent=body.allowed.agents[0],
        action="HOLD" if manage is None else "MANAGE",
        views=baseline_or_defaults(body) if m15 else None, manage=manage,
        m15_bias=(body.last_bias or UNCLEAR_BIAS) if m15 else None)
```

- [ ] **Step 6: Pindahkan helper ke `adapter/app/v6/deliberation/packet_text.py`**

Pindahkan dari `operator_packet.py` apa adanya: `MAX_GATE_VALUE_CHARS`, `CODE_RE`, `FEATURE_NAME_RE` dan fungsi `_finite`, `_number`, `_positive`, `_text`, `_gate_value`, `_codes`, `_features` (seluruh bagian "scalar helpers"), dengan nama publik:

| lama | baru |
|---|---|
| `_finite` | `is_finite` |
| `_number` | `number` |
| `_positive` | `positive_number` |
| `_text` | `clean_text` |
| `_gate_value` | `gate_value` |
| `_codes` | `codes` |
| `_features` | `features` |

Docstring modul: `"""Packet values from untrusted or computed inputs: finite numbers, printable text, codes and feature names (docs/v6-wire-contract.md section 9)."""`. Di `operator_packet.py`, bagian yang dipindah diganti satu impor yang mempertahankan nama lama di pemanggil:

```python
from .packet_text import (
    clean_text as _text, codes as _codes, features as _features, gate_value as _gate_value,
    is_finite as _finite, number as _number, positive_number as _positive,
)
```

- [ ] **Step 7: Tulis `adapter/app/v6/deliberation/minute_packet.py`**

```python
"""
The blocks only an m1 packet carries (spec section 1): the last hour of closed M1 bars and
`m1_state` (ATR and range of the M1 bars, the 5- and 15-bar moves, the quote rate, and the
distance from the price to each level of the managed trade). Bars that are still forming at
the packet's minute close are never read.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

from ..cycle_types import MarketContext
from ..market.features import bars_closed_by
from ..schemas.operator_minute import MinuteMove, MinuteState
from ..types import TIMEFRAME_SECONDS, Bar

M1_S: Final[int] = TIMEFRAME_SECONDS["M1"]
MINUTE_PACKET_M1_BARS: Final[int] = 60
ATR_PERIOD: Final[int] = 14
SHORT_MOVE_BARS: Final[int] = 5
LONG_MOVE_BARS: Final[int] = 15
RATE_DIGITS: Final[int] = 2


def closed_minutes(context: MarketContext) -> tuple[Bar, ...]:
    """The M1 bars closed by the packet's minute close, oldest first (at most 60)."""
    bars = context.bars.get("M1", ())
    return bars_closed_by(bars, context.as_of_epoch, M1_S)[-MINUTE_PACKET_M1_BARS:]


def atr(bars: Sequence[Bar], period: int = ATR_PERIOD) -> float:
    """Average true range of the last `period` bars; 0 without period + 1 bars."""
    if len(bars) < period + 1:
        return 0.0
    pairs = zip(bars[-period - 1:-1], bars[-period:])
    return sum(max(bar.h, prev.c) - min(bar.l, prev.c) for prev, bar in pairs) / period


def _move(bars: Sequence[Bar], count: int, atr_value: float, digits: int) -> MinuteMove:
    if len(bars) <= count:
        return MinuteMove(bars=count, change=0.0, direction="flat", strength=0.0)
    change = round(bars[-1].c - bars[-1 - count].c, digits)
    direction = "up" if change > 0 else "down" if change < 0 else "flat"
    strength = abs(change) / atr_value if atr_value > 0 else 0.0
    return MinuteMove(bars=count, change=change, direction=direction,
                      strength=round(strength, RATE_DIGITS))


def _range(bars: Sequence[Bar], digits: int) -> float:
    recent = bars[-LONG_MOVE_BARS:]
    return round(max(bar.h for bar in recent) - min(bar.l for bar in recent), digits) if recent else 0.0


def _trade_levels(trade: Mapping[str, object]) -> tuple[bool, dict[str, float]]:
    """(buy?, levels) of a packet position or pending_order block; 0 means no level."""
    plan = trade.get("plan") if isinstance(trade.get("plan"), Mapping) else {}
    position = "open_price" in trade
    buy = (trade.get("side") == "buy") if position else str(trade.get("order_type")).startswith("BUY")
    raw = {"entry": trade.get("open_price" if position else "price"), "sl": trade.get("sl"),
           "tp1": plan.get("tp1"), "tp2": plan.get("tp2"), "tp3": trade.get("tp")}
    return buy, {key: float(value) for key, value in raw.items()
                 if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0}


def _distances(context: MarketContext, trade: Mapping[str, object] | None) -> dict[str, float]:
    """Level minus the price that triggers it: a position exits on the bid (buy) or the ask
    (sell); a resting buy order fills on the ask, a sell order on the bid."""
    if trade is None:
        return {}
    buy, levels = _trade_levels(trade)
    quote, digits = context.quote, context.spec.digits
    if "open_price" in trade:
        price = quote.bid if buy else quote.ask
    else:
        price = quote.ask if buy else quote.bid
    return {key: round(level - price, digits) for key, level in levels.items()}


def minute_state(context: MarketContext, trade: Mapping[str, object] | None) -> MinuteState:
    """`m1_state` of the packet; `trade` is its position or pending_order block, if any."""
    bars = closed_minutes(context)
    digits = context.spec.digits
    atr_value = atr(bars)
    ticks = context.ticks
    rate = ticks.quote_count / ticks.window_s if ticks.window_s > 0 else 0.0
    return MinuteState(
        atr_m1=round(atr_value, digits), range_15=_range(bars, digits),
        last_5=_move(bars, SHORT_MOVE_BARS, atr_value, digits),
        last_15=_move(bars, LONG_MOVE_BARS, atr_value, digits),
        quotes_per_s=round(rate, RATE_DIGITS), max_gap_ms=int(ticks.max_gap_ms),
        distances=_distances(context, trade))
```

(Bila `_range` melebihi 100 karakter per baris, pecah ekspresi `return`-nya menjadi `if not recent: return 0.0` lalu `return round(...)`.)

- [ ] **Step 8: `packet_extras.bars_block` per jenis**

Ganti `BAR_LIMITS` dan `bars_block`:

```python
BAR_LIMITS: Final[tuple[tuple[str, int], ...]] = (
    ("M1", M15_PACKET_M1_BARS), ("M5", MAX_PACKET_M5_BARS), ("M15", MAX_PACKET_M15_BARS),
    ("H1", MAX_PACKET_H1_BARS), ("D1", MAX_PACKET_D1_BARS))


def bars_block(context: MarketContext, kind: str = "m15") -> Document:
    """An m15 packet shows every timeframe; an m1 packet only the last hour of M1 bars."""
    if kind == "m1":
        empty = {tf: [] for tf, _ in BAR_LIMITS}
        return {**empty, "M1": compact_bars(closed_minutes(context), MINUTE_PACKET_M1_BARS)}
    return {tf: compact_bars(context.bars.get(tf, ()), limit) for tf, limit in BAR_LIMITS}
```

Impor `M15_PACKET_M1_BARS` (ganti `MAX_PACKET_M1_BARS`) dan `from .minute_packet import MINUTE_PACKET_M1_BARS, closed_minutes`.

- [ ] **Step 9: `operator_packet.py` membangun paket `m1`**

1. Impor `PacketKind` dari `..schemas.operator_parts` dan `from .minute_packet import minute_state`.
2. `PacketRequest` mendapat field terakhir `kind: PacketKind = "m15"` (docstring: "`kind` m1 builds the packet of a closed M1 bar: M1 bars only, `m1_state`, the M1 deadline").
3. `_window`:

```python
    close = request.context.as_of_epoch
    budget = settings.operator_deadline_s if request.kind == "m15" else settings.m1_deadline_s
    expires = close + budget
```

4. Di `_body`, hitung `managed = _managed(request, settings.time_barrier_s)` sebelum `document`, lalu:
   - `"packet_kind": request.kind,`
   - `"bars": bars_block(context, request.kind),`
   - tambah `"m1_state": _minute_block(request, managed),`
   - ganti `**_managed(request, settings.time_barrier_s)` dengan `**managed`.

```python
def _minute_block(request: PacketRequest, managed: Document) -> Document | None:
    """`m1_state` of an m1 packet (None for m15), measured against its managed trade."""
    if request.kind != "m1":
        return None
    trade = managed["position"] or managed["pending_order"]
    state = minute_state(request.context, trade if isinstance(trade, dict) else None)
    return state.model_dump(mode="json")
```

5. Docstring modul: tambah kalimat "An m1 packet (`PacketRequest.kind`) is the same packet for a closed M1 bar: only the M1 bars, `m1_state`, no suggestions, and the M1 deadline."

Periksa `wc -l adapter/app/v6/deliberation/operator_packet.py` ≤ 400 (pemindahan helper membebaskan ~45 baris).

- [ ] **Step 10: Golden contract**

Di `tests/v6/test_golden_contract.py`, impor `M15_PACKET_M1_BARS` (bukan `MAX_PACKET_M1_BARS`) dan ubah asersi menjadi `assert counts["M1"] >= M15_PACKET_M1_BARS` dengan komentar "The m15 packet's M1 window comes whole from the newest snapshot."

- [ ] **Step 11: Jalankan tes**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_minute_packet.py tests/v6/test_operator_packet.py tests/v6/test_operator_packet_v3.py tests/v6/test_operator_schemas.py tests/v6/test_golden_contract.py tests/v6/test_v6_operator_docs.py`
Expected: PASS. Paket `m15` tidak berubah (30 bar M1, templat dengan view dan bias).
Setiap paket kini membawa `"m1_state": null`, jadi contoh paket di `docs/v6-operator.md`
bagian 6 punya hash baru: bila `test_v6_operator_docs.py` gagal karena hash, tambahkan
`"m1_state": null` ke contoh paket lalu segel ulang contoh itu seperti di tahap A
(`<scratchpad>/reseal_doc_packet.py`: `packet_hash` dan `decision_template` dihitung ulang
dengan `seal_packet`). Hal yang sama berlaku di Task 7 untuk `"carried": false` pada bias.

- [ ] **Step 12: Commit**

```bash
git add adapter/app/v6/schemas adapter/app/v6/deliberation/packet_text.py adapter/app/v6/deliberation/minute_packet.py adapter/app/v6/deliberation/packet_extras.py adapter/app/v6/deliberation/operator_packet.py adapter/tests/v6/test_minute_packet.py adapter/tests/v6/test_golden_contract.py
git commit -m "feat: build V6 m1 packets with M1 bars and m1_state"
```

---

## Task 7: Keputusan untuk paket `m1` dan bias yang dibawa

**Files:**
- Modify: `adapter/app/v6/deliberation/decision_v3.py` (helper dijadikan publik)
- Create: `adapter/app/v6/deliberation/decision_minute.py`
- Modify: `adapter/app/v6/deliberation/operator_decision.py` (dispatch per jenis paket)
- Modify: `adapter/app/v6/schemas/operator_plan.py` (`M15Bias.carried`)
- Modify: `adapter/app/v6/deliberation/trade_state.py` (`BiasMemory.remember`)
- Modify: `adapter/app/v6/deliberation/operator_flow.py` (bias hanya dari paket `m15`)
- Test: `adapter/tests/v6/test_decision_minute.py`

**Interfaces:**
- Consumes: paket `m1` (Task 6), `decision_parts` (`packet_problem`, `checked_desks`, `parse_json_value`, `decision_error`, `DecisionEnvelopeV3`, `ValidatedDecision`), `baseline_or_defaults`.
- Produces:
  - `decision_v3.state_problem`, `decision_v3.action_parts`, `decision_v3.derived_chief` (nama publik untuk `_state_problem`, `_action_parts`, `_chief`);
  - `decision_minute.MINUTE_PA_NOTE = "m1 ENTER"`, `minute_price_action(envelope, packet) -> PriceActionView`, `validate_minute(packet, envelope, settings, *, now) -> DecisionOutcome`;
  - `M15Bias.carried: bool = False`; `BiasMemory.remember(bias, at)` mengabaikan bias `carried` bila sudah ada bias.

- [ ] **Step 1: Tes yang gagal `adapter/tests/v6/test_decision_minute.py`**

```python
"""Decisions on an m1 packet: views and bias may be null; the inherited views apply."""

from __future__ import annotations

import json
from typing import Any

from app.v6.deliberation.decision_parts import DecisionError, ValidatedDecision
from app.v6.deliberation.operator_decision import validate_decision
from app.v6.deliberation.trade_state import BiasMemory
from app.v6.schemas.operator import OperatorPacket
from app.v6.schemas.operator_plan import M15Bias

from . import engine_fixtures_v6 as ef
from .test_engine_operator import OPERATOR
from .test_minute_packet import MINUTE_CLOSE, sealed_minute_packet


def submit(packet: OperatorPacket, **changes: Any):
    template = packet.decision_template.model_dump(mode="json")
    document = {**template, "agent": "claude_code", **changes}
    return validate_decision(packet, json.dumps(document).encode("utf-8"),
                             ef.settings(**OPERATOR), now=float(MINUTE_CLOSE + 5))


def plan(packet: OperatorPacket) -> dict[str, Any]:
    limits = packet.limits
    entry = round(limits.buy_limit_max - 1.0, 2)
    sl = round(entry - limits.stop_floor - 0.5, 2)
    risk = entry - sl
    return {"side": "buy", "order_type": "LIMIT", "entry": entry, "sl": sl,
            "tp1": round(entry + 0.6 * risk, 2), "tp2": round(entry + 1.2 * risk, 2),
            "tp3": round(entry + 2.0 * risk, 2), "sl_after_tp1": None, "sl_after_tp2": None,
            "time_limit_min": 90, "pending_expiry_min": 20, "lots": 0.01, "thesis": "m1 test"}


def test_the_m1_template_is_a_valid_hold() -> None:
    decision = submit(sealed_minute_packet())
    assert isinstance(decision, ValidatedDecision), decision
    assert (decision.action, decision.bias, decision.price_action.abstain) == ("HOLD", None, True)


def test_an_m1_enter_takes_the_entry_at_the_minimum_conviction() -> None:
    packet = sealed_minute_packet()
    decision = submit(packet, action="ENTER", entry_plan=plan(packet))
    assert isinstance(decision, ValidatedDecision), decision
    ranked = decision.price_action.ranked[0]
    assert (ranked.candidate_id, ranked.verdict) == (packet.limits.agent_entry_id, "TAKE")
    assert ranked.conviction == packet.allowed.pa_min_conviction
    assert decision.chief.action == "ENTER" and decision.plan is not None


def test_an_m1_decision_for_an_m15_packet_is_refused() -> None:
    decision = submit(sealed_minute_packet(), packet_kind="m15")
    assert isinstance(decision, DecisionError) and decision.code == "DECISION_KIND"


def test_an_m1_decision_may_carry_a_bias() -> None:
    bias = {"direction": "up", "levels": [4300.0], "invalidation": 4290.0, "scenario": "x",
            "carried": False}
    decision = submit(sealed_minute_packet(), m15_bias=bias)
    assert isinstance(decision, ValidatedDecision) and decision.bias is not None


def test_a_carried_bias_keeps_the_remembered_one() -> None:
    memory = BiasMemory()
    original = M15Bias(direction="up", scenario="first")
    memory.remember(original, 100)
    memory.remember(M15Bias(direction="up", scenario="first", carried=True), 200)
    assert memory.latest() == (original, 100)
    fresh = BiasMemory()
    fresh.remember(M15Bias(direction="unclear", carried=True), 300)
    assert fresh.latest()[1] == 300
```

- [ ] **Step 2: Jalankan dan pastikan gagal**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_decision_minute.py`
Expected: FAIL (templat `m1` belum lolos validasi: `DECISION_VIEW` "an m15 packet needs the four views").

- [ ] **Step 3: `M15Bias.carried`**

Di `schemas/operator_plan.py`, `M15Bias` mendapat field terakhir:

```python
    # True when `submit --quick` carried the last bias forward unchanged (spec 2.6).
    carried: bool = False
```

- [ ] **Step 4: `BiasMemory.remember`**

```python
    def remember(self, bias: M15Bias, at: int) -> None:
        """A carried bias keeps the remembered one and its time (it is not a new reading)."""
        if bias.carried and self._bias is not None:
            return
        self._bias, self._at = bias, at
```

- [ ] **Step 5: Helper publik di `decision_v3.py`**

Ganti nama `_state_problem` → `state_problem`, `_action_parts` → `action_parts`, `_chief` → `derived_chief` (termasuk pemanggilnya di `validate_v3`). Isi fungsi tidak berubah.

- [ ] **Step 6: Tulis `adapter/app/v6/deliberation/decision_minute.py`**

```python
"""
Decision v3 against an m1 packet (spec sections 2.1 and 2.5).

An m1 packet carries no desk views of its own: its `baseline_views` are the views the
newest M15 cycle used (the accepted decision's, or that cycle's rules views). A decision
may still send its four views, which are then checked like an m15 decision's; without
them the inherited views apply, and an ENTER is the agent's Price Action TAKE of the
packet's entry id at the minimum conviction (the protocol asks exactly that). `m15_bias`
may be null.
"""

from __future__ import annotations

from typing import Final

from ..config import V6Settings
from ..schemas.agents import PriceActionView, RankedCandidate
from ..schemas.operator import (
    ABSTAIN_VIEW, DECISION_ERR_BIAS, DECISION_SCHEMA, OperatorPacket, baseline_or_defaults,
)
from ..schemas.operator_plan import M15Bias
from .decision_parts import (
    DecisionEnvelopeV3, DecisionError, DecisionOutcome, ValidatedDecision, checked_desks,
    packet_problem, parse_json_value,
)
from .decision_v3 import action_parts, derived_chief, state_problem

MINUTE_PA_NOTE: Final[str] = "m1 ENTER"


def minute_price_action(envelope: DecisionEnvelopeV3, packet: OperatorPacket) -> PriceActionView:
    """Without views: an ENTER TAKEs the entry id at the minimum conviction, else abstain."""
    if envelope.action != "ENTER":
        return ABSTAIN_VIEW
    take = RankedCandidate(candidate_id=packet.limits.agent_entry_id, verdict="TAKE",
                           conviction=packet.allowed.pa_min_conviction, reason_codes=(),
                           note=MINUTE_PA_NOTE)
    return PriceActionView(abstain=False, ranked=(take,))


def _desks(envelope: DecisionEnvelopeV3, packet: OperatorPacket):
    """(Price Action, risk desk views, flags): the sent views, or the inherited ones."""
    if envelope.views is not None:
        return checked_desks(envelope.views, packet)
    inherited = baseline_or_defaults(packet)
    views = {"news_risk": inherited.news_risk, "liquidity": inherited.liquidity,
             "structure": inherited.structure}
    return minute_price_action(envelope, packet), views, ()


def _bias(envelope: DecisionEnvelopeV3) -> M15Bias | DecisionError | None:
    if envelope.m15_bias is None:
        return None
    return parse_json_value(M15Bias, envelope.m15_bias, DECISION_ERR_BIAS, "m15_bias")


def validate_minute(packet: OperatorPacket, envelope: DecisionEnvelopeV3, settings: V6Settings,
                    *, now: float) -> DecisionOutcome:
    """Check a v3 decision for an m1 packet: the packet, the action for its state, the
    views (sent or inherited), the optional bias, then the plan or the manage request."""
    problem = (packet_problem(envelope, packet, settings, now)
               or state_problem(envelope, packet))
    if problem is not None:
        return problem
    desks = _desks(envelope, packet)
    if isinstance(desks, DecisionError):
        return desks
    pa_view, views, flags = desks
    bias = _bias(envelope)
    if isinstance(bias, DecisionError):
        return bias
    parts = action_parts(envelope, packet, pa_view)
    if isinstance(parts, DecisionError):
        return parts
    plan, manage = parts
    return ValidatedDecision(
        cycle_id=envelope.cycle_id, packet_hash=envelope.packet_hash, agent=envelope.agent,
        price_action=pa_view, chief=derived_chief(plan, packet, pa_view, envelope.note),
        flags=flags, schema_version=DECISION_SCHEMA, action=envelope.action, plan=plan,
        manage=manage, bias=bias, note=envelope.note, **views)
```

(Pastikan `ABSTAIN_VIEW` diekspor di `__all__` `schemas/operator.py`; sudah ada di daftar.)

- [ ] **Step 7: Dispatch di `operator_decision.validate_decision`**

```python
    if isinstance(envelope, DecisionEnvelopeV3):
        validate = validate_minute if packet.packet_kind == "m1" else validate_v3
        outcome = validate(packet, envelope, settings, now=now)
    else:
        outcome = _validate_v2(packet, envelope, settings, now)
```

dengan impor `from .decision_minute import validate_minute`. (Sebuah decision `m15` untuk paket `m1` ditolak oleh `state_problem` dengan `DECISION_KIND`.)

- [ ] **Step 8: Bias hanya dari paket `m15` di `operator_flow._operator_decide`**

```python
        if decision.bias is not None and outcome.packet.packet_kind == "m15":
            deps.bias.remember(decision.bias, outcome.packet.bar_close_epoch)
```

- [ ] **Step 9: Jalankan tes**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_decision_minute.py tests/v6/test_decision_v3.py tests/v6/test_operator_decision.py tests/v6/test_engine_operator.py tests/v6/test_engine_management.py tests/v6/test_v6_operator_docs.py`
Expected: PASS (segel ulang contoh dokumen bila hash-nya berubah karena `carried`, lihat Task 6 Step 11).

- [ ] **Step 10: Commit**

```bash
git add adapter/app/v6/deliberation adapter/app/v6/schemas/operator_plan.py adapter/tests/v6/test_decision_minute.py
git commit -m "feat: validate V6 m1 decisions with the inherited M15 views"
```

---
## Task 8: Konteks menit dan siklus `m1` di engine

**Files:**
- Modify: `adapter/app/v6/deliberation/cycle_draft.py` (`CycleRequest.minute` dan propertinya)
- Create: `adapter/app/v6/deliberation/minute_context.py`
- Create: `adapter/app/v6/deliberation/minute_flow.py`
- Modify: `adapter/app/v6/deliberation/engine.py` (mixin `MinuteFlow`, tenggat `m1`)
- Modify: `adapter/app/v6/deliberation/operator_flow.py` (`PacketRequest.kind`)
- Test: `adapter/tests/v6/test_minute_context.py`, `adapter/tests/v6/test_minute_flow.py`

**Interfaces:**
- Consumes: `MinuteSnapshot` (Task 2), paket `m1` (Task 6), `validate_minute` (Task 7), `assess_snapshot_calendar`, `calendar_horizon_s`, `evaluate_gates`, `effective_friction`, `session_state`, `bars_closed_by`, `Tier0`, `CandidatePool`, `Baseline`, `OperatorFlow._operator_path`.
- Produces:
  - `CycleRequest.minute: MinuteSnapshot | None = None`; properti `packet_kind`, `snapshot_id`, `bar_open_epoch`, `as_of_epoch`; `CycleDraft.finish` memakai `request.snapshot_id` dan `request.bar_open_epoch`;
  - `minute_context.minute_settings(settings) -> V6Settings`, `minute_context.minute_context(base, minute, m1_bars, *, cycle_id, received_at, calendar, settings) -> MarketContext`;
  - `minute_flow.MinuteBase(snapshot: V6Snapshot, context: MarketContext, views: DeskViews, events: tuple[CalendarEvent, ...] = (), probe: ProbeBlock | None = None)`;
  - `minute_flow.MinuteRun(state: PacketState, tier0_ms: int = 0, outcome: CycleOutcome | None = None, skipped: str = "")`;
  - `minute_flow.skip_reason(tier0, state) -> str` (`""`, atau `"GATES:<kode,…>"`);
  - `DeliberationEngine.run_minute(request, base) -> MinuteRun` (tidak pernah raise).

- [ ] **Step 1: Tes yang gagal `adapter/tests/v6/test_minute_context.py`**

```python
"""The context of an m1 cycle: the M15 context with the minute's data swapped in."""

from __future__ import annotations

import json
from dataclasses import replace
from types import MappingProxyType

from app.v6.cycle_codes import F_ATR_M5, F_FRICTION_ATR, F_FRICTION_PRICE
from app.v6.deliberation.minute_context import minute_context, minute_settings
from app.v6.market.feature_map import effective_friction
from app.v6.market.sessions import session_state
from app.v6.schemas.minute import MinuteSnapshot
from app.v6.types import Bar

from . import engine_fixtures_v6 as ef

MINUTE_OPEN = ef.AS_OF + 120
MINUTE_CLOSE = MINUTE_OPEN + 60


def minute(**changes: object) -> MinuteSnapshot:
    snap = ef.engine_snapshot()
    document = {
        "schema_version": "v6.minute.1",
        "snapshot_id": f"Q6M-{snap.account.login}-{MINUTE_OPEN}", "symbol": snap.symbol,
        "sent_at_epoch": MINUTE_CLOSE, "server_gmt_offset_s": snap.server_gmt_offset_s,
        "bar_open_epoch": MINUTE_OPEN,
        "bar": [MINUTE_OPEN, 4300.0, 4300.4, 4299.8, 4300.2, 60, 20],
        "account": snap.account.model_dump(mode="json"),
        # The M15 snapshot's quote, one bar later: the gates judge it exactly as tier 0 did.
        "quote": {**snap.quote.model_dump(mode="json"), "time_msc": MINUTE_CLOSE * 1000},
        "ticks": {**snap.ticks.model_dump(mode="json"), "window_s": 60},
        "positions": [], "pending_orders": [],
        "day": {**snap.day.model_dump(mode="json"), "trades_today": 3},
        "ea_state": snap.ea_state.model_dump(mode="json"), **changes}
    return MinuteSnapshot.model_validate_json(json.dumps(document))


def test_the_minute_replaces_what_changes_every_minute() -> None:
    base = replace(ef.market_context(), features=MappingProxyType({F_ATR_M5: 5.0}))
    bars = (Bar(t=MINUTE_OPEN, o=4300.0, h=4300.4, l=4299.8, c=4300.2, tv=60, spr=20),)
    config = ef.settings()
    snapshot = minute(quote={"bid": 4300.10, "ask": 4300.40, "spread_points": 30,
                             "time_msc": MINUTE_CLOSE * 1000})
    context = minute_context(base, snapshot, bars, cycle_id="m-00000000000000aa",
                             received_at=float(MINUTE_CLOSE + 1),
                             calendar=ef.calendar_assessment(MINUTE_CLOSE), settings=config)
    assert (context.cycle_id, context.bar_open_epoch, context.as_of_epoch) == (
        "m-00000000000000aa", MINUTE_OPEN, MINUTE_CLOSE)
    assert (context.quote.spread_points, context.day.trades_today) == (30, 3)
    assert context.bars["M1"] == bars and context.session == session_state(MINUTE_CLOSE)
    friction = effective_friction(config.friction_price, snapshot.quote.ask - snapshot.quote.bid)
    assert context.features[F_FRICTION_PRICE] == friction
    assert context.features[F_FRICTION_ATR] == friction / 5.0
    assert context.spec == base.spec and context.calendar.as_of_epoch == MINUTE_CLOSE


def test_minute_settings_use_the_minute_staleness() -> None:
    config = ef.settings(minute_stale_s=7)
    assert minute_settings(config).snapshot_stale_s == 7
```

- [ ] **Step 2: Jalankan dan pastikan gagal**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_minute_context.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.v6.deliberation.minute_context'`).

- [ ] **Step 3: Tulis `adapter/app/v6/deliberation/minute_context.py`**

```python
"""
The market context of an m1 cycle (spec section 4.2).

It is the newest M15 cycle's context (spec, features, levels, probe, M15 to D1 bars) with
what changes every minute swapped in from the minute snapshot: times, quote, account,
ticks, day, EA state, positions, pending orders and the closed M1 bars. What depends on
the time or the spread is assessed again at the minute close: the session, the calendar
(passed in) and the friction features. The gates then see the minute as it is.
"""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
from typing import Mapping

from ..config import V6Settings
from ..cycle_codes import F_ATR_M5, F_FRICTION_ATR, F_FRICTION_PRICE
from ..cycle_types import CalendarAssessment, MarketContext
from ..market.feature_map import effective_friction
from ..market.sessions import session_state
from ..schemas.minute import MinuteSnapshot
from ..types import Bar


def minute_settings(settings: V6Settings) -> V6Settings:
    """The gate settings of an m1 cycle: a minute snapshot is stale after
    V6_MINUTE_STALE_S (the SNAPSHOT_AGE gate reads `snapshot_stale_s`)."""
    return settings.model_copy(update={"snapshot_stale_s": settings.minute_stale_s})


def _features(base: MarketContext, minute: MinuteSnapshot,
              settings: V6Settings) -> Mapping[str, float]:
    features = dict(base.features)
    friction = effective_friction(settings.friction_price, minute.quote.ask - minute.quote.bid)
    features[F_FRICTION_PRICE] = friction
    atr_m5 = features.get(F_ATR_M5)
    if atr_m5 is not None and atr_m5 > 0:
        features[F_FRICTION_ATR] = friction / atr_m5
    else:
        features.pop(F_FRICTION_ATR, None)
    return MappingProxyType(features)


def minute_context(base: MarketContext, minute: MinuteSnapshot, m1_bars: tuple[Bar, ...], *,
                   cycle_id: str, received_at: float, calendar: CalendarAssessment,
                   settings: V6Settings) -> MarketContext:
    """`base` (the newest M15 context) as it stands at the minute's close."""
    close = minute.bar_close_epoch
    account = minute.account
    return replace(
        base, cycle_id=cycle_id, snapshot_id=minute.snapshot_id,
        bar_open_epoch=minute.bar_open_epoch, as_of_epoch=close,
        sent_at_epoch=minute.sent_at_epoch, received_at=received_at,
        trade_mode=account.trade_mode, server=account.server, account=account,
        quote=minute.quote, ticks=minute.ticks, day=minute.day, ea_state=minute.ea_state,
        positions=tuple(minute.positions), pending_orders=tuple(minute.pending_orders),
        bars=MappingProxyType({**base.bars, "M1": m1_bars}), session=session_state(close),
        calendar=calendar, features=_features(base, minute, settings))
```

- [ ] **Step 4: `CycleRequest` untuk siklus menit (`cycle_draft.py`)**

Impor `from ..schemas.minute import MinuteSnapshot`, `from ..schemas.operator_parts import PacketKind`, `from ..types import TIMEFRAME_SECONDS` dan `M15_S: Final[int] = TIMEFRAME_SECONDS["M15"]`. `CycleRequest` mendapat field terakhir dan properti:

```python
    minute: MinuteSnapshot | None = None      # an m1 cycle: the minute it decides on

    @property
    def packet_kind(self) -> PacketKind:
        return "m15" if self.minute is None else "m1"

    @property
    def snapshot_id(self) -> str:
        return self.snapshot.snapshot_id if self.minute is None else self.minute.snapshot_id

    @property
    def bar_open_epoch(self) -> int:
        return (self.snapshot.bar_open_epoch if self.minute is None
                else self.minute.bar_open_epoch)

    @property
    def as_of_epoch(self) -> int:
        """The close of the bar this cycle decides on."""
        if self.minute is None:
            return self.snapshot.bar_open_epoch + M15_S
        return self.minute.bar_close_epoch
```

Di `CycleDraft.finish`: `snapshot_id=request.snapshot_id, bar_open_epoch=request.bar_open_epoch`.

- [ ] **Step 5: Tenggat dan jenis paket di engine**

`engine.py`:

```python
    def _deadline(self, request: CycleRequest) -> float:
        settings = self._deps.settings
        if request.minute is not None:
            return float(request.as_of_epoch + settings.m1_deadline_s)
        budget = (settings.operator_deadline_s if settings.backend == OPERATOR_PROVIDER_NAME
                  else settings.decision_deadline_s)
        return float(as_of_for(request.snapshot) + budget)
```

`class DeliberationEngine(OperatorFlow, MinuteFlow):` dengan `from .minute_flow import MinuteFlow`. Di `operator_flow._operator_decide`, `PacketRequest(...)` mendapat `kind=request.packet_kind`.

- [ ] **Step 6: Tulis `adapter/app/v6/deliberation/minute_flow.py`**

```python
"""
The m1 part of a cycle (spec sections 1, 2.5 and 4.2; mixed into `engine.DeliberationEngine`).

An m1 cycle starts from the newest M15 cycle (`MinuteBase`): its context with the minute
swapped in (`minute_context`), the calendar assessed again at the minute close, and every
hard gate evaluated anew (a minute snapshot is stale after V6_MINUTE_STALE_S). A flat
minute that fails a gate, or a managed minute that fails a management-blocking gate,
serves no packet: the run is skipped with the failed gate codes. Otherwise the ordinary
operator path serves an m1 packet whose views are the M15 cycle's (the accepted
decision's, or that cycle's rules views), waits until the minute close +
V6_M1_DEADLINE_S, and enters, manages or holds exactly like an m15 cycle.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from ..cycle_codes import hold_reason_for_gates
from ..cycle_types import CalendarEvent, DeliberationInput, DeskViews, MarketContext
from ..market.calendar import assess_snapshot_calendar
from ..market.features import bars_closed_by
from ..risk.gates import evaluate_gates, failed_codes
from ..schemas.operator_plan import PacketState
from ..schemas.snapshot import ProbeBlock, V6Snapshot
from ..types import TIMEFRAME_SECONDS, Bar
from .candidates import CandidatePool
from .context_builder import calendar_horizon_s
from .cycle_draft import CycleDraft, CycleOutcome, CycleRequest, elapsed_ms
from .minute_context import minute_context, minute_settings
from .minute_packet import MINUTE_PACKET_M1_BARS
from .panel import Baseline
from .trade_state import trade_state, wants_management

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .engine import EngineDeps, Tier0

logger = logging.getLogger(__name__)

M1_S: Final[int] = TIMEFRAME_SECONDS["M1"]
SKIP_GATES: Final[str] = "GATES"
SKIP_NO_OPERATOR: Final[str] = "NO_OPERATOR"
SKIP_ERROR: Final[str] = "ERROR"


@dataclass(frozen=True)
class MinuteBase:
    """The newest M15 cycle an m1 cycle starts from."""

    snapshot: V6Snapshot
    context: MarketContext
    views: DeskViews
    events: tuple[CalendarEvent, ...] = ()
    probe: ProbeBlock | None = None


@dataclass(frozen=True)
class MinuteRun:
    """What an m1 cycle did: `outcome` when a packet was served, else why not."""

    state: PacketState
    tier0_ms: int = 0
    outcome: CycleOutcome | None = None
    skipped: str = ""


def _merged(stored: tuple[Bar, ...], fresh: Bar) -> tuple[Bar, ...]:
    by_time = {bar.t: bar for bar in stored}
    by_time[fresh.t] = fresh
    return tuple(by_time[t] for t in sorted(by_time))


def skip_reason(tier0: "Tier0", state: PacketState) -> str:
    """"" when a packet is due; else the failed gates (a managed trade is skipped only on
    a management-blocking gate, exactly like an m15 cycle)."""
    if hold_reason_for_gates(tier0.gates) is None:
        return ""
    if state != "flat" and wants_management(tier0.context, tier0.gates):
        return ""
    return f"{SKIP_GATES}:" + ",".join(failed_codes(tier0.gates))


class MinuteFlow:
    """Mixed into DeliberationEngine, whose `_deps`, `_now`, `_breaker_status` and
    `_operator_path` it uses."""

    _deps: "EngineDeps"

    async def run_minute(self, request: CycleRequest, base: MinuteBase) -> MinuteRun:
        """One m1 cycle; never raises (CancelledError aside)."""
        started = self._now()
        try:
            tier0 = await self._minute_tier0(request, base)
        except Exception as exc:  # noqa: BLE001 - a failed minute is skipped and logged
            logger.error("v6 minute %s failed in tier 0: %s", request.cycle_id,
                         type(exc).__name__, exc_info=True)
            return MinuteRun(state="flat", skipped=f"{SKIP_ERROR}:{type(exc).__name__}")
        state = trade_state(tier0.context)
        tier0_ms = elapsed_ms(started, self._now())
        skipped = skip_reason(tier0, state)
        queue = self._deps.operator
        if skipped or queue is None:
            return MinuteRun(state=state, tier0_ms=tier0_ms,
                             skipped=skipped or SKIP_NO_OPERATOR)
        draft = CycleDraft(request=request, backend=self._deps.settings.backend,
                           started_at=started).update(
            context=tier0.context, gates=tier0.gates, views=base.views, tier0_ms=tier0_ms)
        outcome = await self._operator_path(queue, draft, tier0, state)
        return MinuteRun(state=state, tier0_ms=tier0_ms, outcome=outcome)

    async def _minute_tier0(self, request: CycleRequest, base: MinuteBase) -> "Tier0":
        from .engine import Tier0  # the engine module imports this one at load time
        deps, minute = self._deps, request.minute
        if minute is None:
            raise ValueError("an m1 cycle needs its minute snapshot")
        settings, close = deps.settings, minute.bar_close_epoch
        stored = await asyncio.to_thread(deps.bars.latest, "M1", MINUTE_PACKET_M1_BARS + 1)
        bars = bars_closed_by(_merged(stored, minute.to_bar()), close, M1_S)
        calendar = assess_snapshot_calendar(
            base.snapshot, now_epoch=int(self._now()), carried_events=base.events,
            probe=base.probe, horizon_s=calendar_horizon_s(settings), decision_epoch=close)
        context = minute_context(base.context, minute, bars[-MINUTE_PACKET_M1_BARS:],
                                 cycle_id=request.cycle_id, received_at=request.received_at,
                                 calendar=calendar, settings=settings)
        breakers = await self._breaker_status(context)
        gates = evaluate_gates(context, calendar, request.runtime, minute_settings(settings),
                               breakers, self._now())
        return Tier0(context=context, gates=gates, breakers=breakers, pool=CandidatePool(),
                     inputs=DeliberationInput(context=context, gates=gates, offered=()),
                     baseline=Baseline(views=base.views, records=()))
```

- [ ] **Step 7: Tes siklus yang gagal `adapter/tests/v6/test_minute_flow.py`**

```python
"""m1 cycles through the engine: served packets, entries, management, vetoes and skips."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any, Callable

import pytest

from app.v6.cycle_codes import HoldReason
from app.v6.deliberation.cycle_draft import CycleRequest
from app.v6.deliberation.minute_flow import MinuteBase, MinuteRun
from app.v6.deliberation.publication import PublishOutcome
from app.v6.risk.gates import RuntimeGateState
from app.v6.schemas.agents import LiquidityView

from . import engine_fixtures_v6 as ef
from .test_engine_management import POSITION
from .test_engine_operator import WAIT_STEP_S, WAIT_STEPS, FakePublisher, Rig, rig
from .test_minute_context import MINUTE_CLOSE, MINUTE_OPEN, minute

Decide = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
NO_TRADE = LiquidityView(stance="NO_TRADE", size_multiplier=0.0, order_style="LIMIT",
                         reason_codes=("FRICTION_HIGH",), note="")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _served(setup: Rig, task: asyncio.Future) -> bool:
    for _ in range(WAIT_STEPS):
        if setup.queue.pending is not None or task.done():
            break
        await asyncio.sleep(WAIT_STEP_S)
    return setup.queue.pending is not None


async def m15_base(setup: Rig) -> MinuteBase:
    """The newest M15 cycle, unanswered: what the minute worker would carry."""
    task = asyncio.ensure_future(setup.engine.run(ef.request()))
    if await _served(setup, task):
        setup.queue.withdraw(setup.clock.now_epoch())
    outcome = await task
    return MinuteBase(snapshot=ef.engine_snapshot(), context=outcome.context,
                      views=outcome.result.views)


def minute_request(**changes: Any) -> CycleRequest:
    snapshot = minute(**changes)
    return CycleRequest(cycle_id=f"m-{MINUTE_OPEN:016x}", snapshot=ef.engine_snapshot(),
                        received_at=float(MINUTE_CLOSE + 1),
                        runtime=RuntimeGateState(warmed_up=True), session_id="sess-1",
                        session_armed=True, minute=snapshot)


async def run(setup: Rig, base: MinuteBase, request: CycleRequest,
              decide: Decide | None) -> tuple[MinuteRun, dict[str, Any]]:
    seen: dict[str, Any] = {}
    setup.clock.advance(MINUTE_CLOSE + 1 - setup.clock.now_epoch())
    task = asyncio.ensure_future(setup.engine.run_minute(request, base))
    if await _served(setup, task):
        packet = setup.queue.pending.packet.model_dump(mode="json")
        seen.update(packet)
        if decide is None:
            setup.queue.withdraw(setup.clock.now_epoch())
        else:
            template = {**json.loads(json.dumps(packet["decision_template"])),
                        "agent": "claude_code"}
            body = json.dumps(decide(template, packet)).encode("utf-8")
            seen["submit"] = setup.queue.submit(body, setup.clock.now_epoch())
    return await task, seen


def hold(template: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    return template


def sell_limit(template: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    """A sell LIMIT 10 above the bid whose 1.5R TP3 stays in front of the $4300 level."""
    risk = round(packet["limits"]["stop_floor"] + 0.5, 2)
    entry = round(packet["market"]["bid"] + 10.0, 2)
    plan = {"side": "sell", "order_type": "LIMIT", "entry": entry, "sl": round(entry + risk, 2),
            "tp1": round(entry - 0.6 * risk, 2), "tp2": round(entry - 1.0 * risk, 2),
            "tp3": round(entry - 1.5 * risk, 2), "sl_after_tp1": None, "sl_after_tp2": None,
            "time_limit_min": 90, "pending_expiry_min": 20, "lots": 0.01,
            "thesis": "fade the M1 spike into resistance"}
    return {**template, "action": "ENTER", "entry_plan": plan}


def close(template: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    return {**template, "manage": {"target": "position", "ticket": 91, "op": "CLOSE"}}


def published() -> FakePublisher:
    return FakePublisher(PublishOutcome(intent_id="k7w2m4pq3xza", code="BOOK_PUBLISHED"))


@pytest.mark.anyio
async def test_a_flat_minute_serves_an_m1_packet() -> None:
    setup = rig(publisher=published())
    base = await m15_base(setup)
    result, packet = await run(setup, base, minute_request(), hold)
    assert (packet["packet_kind"], packet["state"], packet["bar_close_epoch"]) == (
        "m1", "flat", MINUTE_CLOSE)
    assert packet["decision_template"]["views"] is None and packet["m1_state"] is not None
    assert result.outcome is not None and result.outcome.result.status == "HOLD"
    assert result.outcome.result.cycle_id.startswith("m-")


@pytest.mark.anyio
async def test_an_m1_enter_is_published() -> None:
    publisher = published()
    setup = rig(publisher=publisher)
    base = await m15_base(setup)
    result, packet = await run(setup, base, minute_request(), sell_limit)
    assert packet["submit"].accepted, packet["submit"]
    assert result.outcome.result.status == "ENTER"
    request = publisher.requests[0]
    assert request.candidate.candidate_id == packet["limits"]["agent_entry_id"]
    assert request.context.as_of_epoch == MINUTE_CLOSE and request.plan is not None


@pytest.mark.anyio
async def test_an_m15_liquidity_veto_blocks_an_m1_enter() -> None:
    setup = rig(publisher=published())
    base = await m15_base(setup)
    vetoed = replace(base, views=replace(base.views, liquidity=NO_TRADE))
    result, packet = await run(setup, vetoed, minute_request(), sell_limit)
    assert packet["baseline_views"]["liquidity"]["stance"] == "NO_TRADE"
    assert result.outcome.result.hold_reason == HoldReason.VETO


@pytest.mark.anyio
async def test_a_managed_minute_can_close_the_position() -> None:
    publisher = published()
    setup = rig(publisher=publisher)
    base = await m15_base(setup)
    result, packet = await run(setup, base, minute_request(positions=[POSITION]), close)
    assert (packet["state"], packet["packet_kind"]) == ("position", "m1")
    assert result.outcome.result.hold_reason == HoldReason.MANAGE_SENT
    assert publisher.dispatches[0].action.command == "CLOSE_POSITION"


@pytest.mark.anyio
async def test_a_failed_gate_skips_a_flat_minute() -> None:
    setup = rig(publisher=published())
    base = await m15_base(setup)
    wide = {"bid": 4300.0, "ask": 4301.0, "spread_points": 100, "time_msc": MINUTE_CLOSE * 1000}
    result, packet = await run(setup, base, minute_request(quote=wide), hold)
    assert result.outcome is None and result.skipped.startswith("GATES:")
    assert "SPREAD" in result.skipped and packet == {}


@pytest.mark.anyio
async def test_an_unanswered_minute_holds() -> None:
    setup = rig(publisher=published())
    base = await m15_base(setup)
    result, _ = await run(setup, base, minute_request(), None)
    assert result.outcome.result.hold_reason == HoldReason.OPERATOR_TIMEOUT
```

- [ ] **Step 8: Jalankan tes**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_minute_context.py tests/v6/test_minute_flow.py tests/v6/test_engine.py tests/v6/test_engine_operator.py tests/v6/test_engine_management.py tests/v6/test_engine_parts.py`
Expected: PASS. Bila `ef.engine_snapshot()` tidak memuat field yang dipakai `minute()` (misalnya `ticks`), ambil nilainya dari `tests/v6/payloads_v6.py` yang membangun snapshot fixture.

- [ ] **Step 9: Commit**

```bash
git add adapter/app/v6/deliberation adapter/tests/v6/test_minute_context.py adapter/tests/v6/test_minute_flow.py
git commit -m "feat: run V6 m1 cycles through the operator path of the engine"
```

---

## Task 9: Worker menit dan sambungan runtime

**Files:**
- Modify: `adapter/app/v6/runtime/service.py` (`CarryOver` membawa siklus M15 terbaru, `minute_base()`)
- Create: `adapter/app/v6/runtime/minute_worker.py`
- Modify: `adapter/app/v6/runtime/wiring.py` (`RuntimeParts.minutes`)
- Modify: `adapter/app/v6/container.py` (task `v6-minutes`)
- Test: `adapter/tests/v6/test_minute_worker.py`

**Interfaces:**
- Consumes: `EaState.next_minute()`, `MinuteFlow.run_minute`, `OperatorQueue.pending_kind()`/`closed()`, `LedgerCycles.minutes`, `IntentBook.active()`, `SessionService.active()`, `session_state(epoch).rollover_block`.
- Produces:
  - `CarryOver.snapshot/context/views/runtime` (siklus M15 terbaru), `CarryOver.after(outcome, item, runtime=None)`, `DeliberationRuntime.minute_base() -> MinuteBase | None`;
  - `minute_worker.MinuteDeps(...)`, `MinuteRuntime(deps)` dengan `run_forever()`, `process(item) -> MinuteRow`, `stats -> MinuteWorkerStats`;
  - kode lewati: `INACTIVE`, `DISABLED`, `M15_CLOSE`, `NOT_ARMED`, `ROLLOVER`, `M15_PENDING`, `NO_M15_CONTEXT`, `INTENT_ACTIVE`, plus `GATES:<kode>`/`ERROR:<tipe>` dari engine;
  - `RuntimeParts.minutes: MinuteRuntime`.

- [ ] **Step 1: Tes yang gagal `adapter/tests/v6/test_minute_worker.py`**

```python
"""The minute worker: skip rules, one ledger row per minute, stats and pruning."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.v6.clock import FakeClock
from app.v6.cycle_codes import HoldReason
from app.v6.cycle_types import CycleResult, CycleTimings, DeskViews
from app.v6.deliberation.cycle_draft import CycleOutcome
from app.v6.deliberation.minute_flow import MinuteBase, MinuteRun
from app.v6.ledger_cycles import LedgerCycles
from app.v6.providers.operator_queue import OperatorQueue
from app.v6.risk.gates import RuntimeGateState
from app.v6.runtime.ea_state import EaState, MinuteItem, minute_cycle_id_for
from app.v6.runtime.minute_worker import MinuteDeps, MinuteRuntime

from . import engine_fixtures_v6 as ef
from . import operator_fixtures_v6 as of
from .test_engine_operator import OPERATOR
from .test_minute_context import MINUTE_CLOSE, MINUTE_OPEN, minute


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class FakeEngine:
    run: MinuteRun
    calls: list[Any] = field(default_factory=list)

    async def run_minute(self, request: Any, base: MinuteBase) -> MinuteRun:
        self.calls.append(request)
        return self.run


@dataclass
class FakeSessions:
    record: Any = SimpleNamespace(session_id="sess-1", armed=True)

    async def active(self) -> Any:
        return self.record


@dataclass
class FakeBook:
    intent: Any = None

    def active(self) -> Any:
        return self.intent


def base() -> MinuteBase:
    return MinuteBase(snapshot=ef.engine_snapshot(), context=ef.market_context(),
                      views=DeskViews())


def item(bar_open: int = MINUTE_OPEN, **changes: Any) -> MinuteItem:
    snapshot = minute(**changes) if bar_open == MINUTE_OPEN else minute(
        bar_open_epoch=bar_open, snapshot_id=f"Q6M-{ef.engine_snapshot().account.login}-{bar_open}",
        bar=[bar_open, 4300.0, 4300.4, 4299.8, 4300.2, 60, 20], sent_at_epoch=bar_open + 60,
        **changes)
    return MinuteItem(cycle_id=minute_cycle_id_for(snapshot.snapshot_id), minute=snapshot,
                      received_at=float(snapshot.bar_close_epoch + 1))


def hold_run() -> MinuteRun:
    result = CycleResult(
        cycle_id="m-x", snapshot_id="Q6M-x", bar_open_epoch=MINUTE_OPEN, status="HOLD",
        hold_reason=HoldReason.OPERATOR_TIMEOUT, backend="operator", provider="operator",
        provider_status="timeout",
        timings=CycleTimings(started_at=MINUTE_CLOSE + 1.0, finished_at=MINUTE_CLOSE + 50.0))
    return MinuteRun(state="flat", tier0_ms=12, outcome=CycleOutcome(result=result))


def worker(tmp_path: Path, *, run: MinuteRun | None = None, sessions: FakeSessions | None = None,
           book: FakeBook | None = None, minute_base: MinuteBase | None = base(),
           queue: OperatorQueue | None = None, **overrides: Any) -> MinuteRuntime:
    config = ef.settings(**(OPERATOR | overrides))
    clock = FakeClock(epoch=MINUTE_CLOSE + 1)
    return MinuteRuntime(MinuteDeps(
        settings=config, clock=clock, ea_state=EaState(),
        engine=FakeEngine(run or MinuteRun(state="flat", skipped="GATES:SPREAD")),
        sessions=sessions or FakeSessions(),
        queue=queue or OperatorQueue(settings=config, clock=clock),
        ledger=LedgerCycles(tmp_path / "minutes.db"), book=book or FakeBook(),
        base=lambda: minute_base, runtime=lambda: RuntimeGateState(warmed_up=True),
        halt_path=tmp_path / "V6_HALT", is_active=lambda: True))


@pytest.mark.anyio
@pytest.mark.parametrize(("setup", "reason"), [
    ({"minute_packets": False}, "DISABLED"),
    ({"sessions": FakeSessions(SimpleNamespace(session_id="sess-1", armed=False))}, "NOT_ARMED"),
    ({"minute_base": None}, "NO_M15_CONTEXT"),
    ({"book": FakeBook(intent=object())}, "INTENT_ACTIVE"),
])
async def test_minutes_are_skipped_with_their_reason(tmp_path: Path, setup: dict[str, Any],
                                                     reason: str) -> None:
    row = await worker(tmp_path, **setup).process(item())
    assert (row.outcome, row.reason) == ("SKIPPED", reason)


@pytest.mark.anyio
async def test_the_minute_that_closes_an_m15_bar_is_left_to_the_m15_cycle(tmp_path: Path) -> None:
    row = await worker(tmp_path).process(item(ef.AS_OF - 60))
    assert row.reason == "M15_CLOSE"


@pytest.mark.anyio
async def test_an_open_m15_packet_pauses_the_minutes(tmp_path: Path) -> None:
    config = ef.settings(**OPERATOR)
    queue = OperatorQueue(settings=config, clock=FakeClock(epoch=of.BAR_CLOSE + 1))
    queue.offer(of.packet())
    row = await worker(tmp_path, queue=queue).process(item())
    assert row.reason == "M15_PENDING"


@pytest.mark.anyio
async def test_a_skipped_engine_run_is_recorded(tmp_path: Path) -> None:
    runtime = worker(tmp_path)
    row = await runtime.process(item())
    assert (row.outcome, row.reason, row.session_id) == ("SKIPPED", "GATES:SPREAD", "sess-1")
    assert runtime.stats.processed == 1 and runtime.stats.skipped == 1
    stored = runtime._deps.ledger.minutes.recent(5)
    assert [entry.cycle_id for entry in stored] == [row.cycle_id]


@pytest.mark.anyio
async def test_an_unanswered_packet_is_recorded_as_such(tmp_path: Path) -> None:
    runtime = worker(tmp_path, run=hold_run())
    row = await runtime.process(item())
    assert (row.outcome, row.status, row.hold_reason, row.tier0_ms) == (
        "UNANSWERED", "HOLD", "APP-V6-OPERATOR-TIMEOUT", 12)
    assert runtime.stats.offered == 1 and runtime.stats.answered == 0
```

(Nilai `hold_reason` di asersi terakhir adalah `str(HoldReason.OPERATOR_TIMEOUT)`; sesuaikan bila enum itu menulis kode lain.)

- [ ] **Step 2: Jalankan dan pastikan gagal**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_minute_worker.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.v6.runtime.minute_worker'`).

- [ ] **Step 3: `CarryOver` dan `minute_base()` di `runtime/service.py`**

Impor `from ..cycle_types import CalendarEvent, CycleResult, DeskViews, MarketContext`, `from ..deliberation.minute_flow import MinuteBase`, `from ..schemas.snapshot import ProbeBlock, V6Snapshot`. `CarryOver`:

```python
@dataclass(frozen=True)
class CarryOver:
    """What one cycle hands to the next, to the watchdog and to the minute worker."""

    probe: ProbeBlock | None = None
    events: tuple[CalendarEvent, ...] = ()
    point: float = XAUUSD_POINT
    day: DayFacts | None = None
    snapshot: V6Snapshot | None = None      # the newest M15 cycle with a context
    context: MarketContext | None = None
    views: DeskViews | None = None
    runtime: RuntimeGateState | None = None

    def after(self, outcome: CycleOutcome, item: InboxItem,
              runtime: RuntimeGateState | None = None) -> "CarryOver":
        snapshot = item.snapshot
        context = outcome.context
        fresh = context is not None
        return CarryOver(
            probe=snapshot.probe if snapshot.probe is not None else self.probe,
            events=self.events if outcome.calendar is None else outcome.calendar.events,
            point=snapshot.symbol_spec.point,
            day=self.day if context is None else DayFacts.from_context(context),
            snapshot=snapshot if fresh else self.snapshot,
            context=context if fresh else self.context,
            views=outcome.result.views if fresh else self.views,
            runtime=self.runtime if runtime is None else runtime,
        )
```

Di `process`: `self._carry = self._carry.after(outcome, item, request.runtime)`. Metode baru `DeliberationRuntime`:

```python
    def minute_base(self) -> MinuteBase | None:
        """The newest M15 cycle for the minute worker (None before the first one)."""
        carry = self._carry
        if carry.snapshot is None or carry.context is None:
            return None
        return MinuteBase(snapshot=carry.snapshot, context=carry.context,
                          views=carry.views or DeskViews(), events=carry.events,
                          probe=carry.probe)
```

- [ ] **Step 4: Tulis `adapter/app/v6/runtime/minute_worker.py`**

```python
"""
The minute worker (spec section 4.2): one m1 cycle per closed M1 bar, the M15 packet first.

It takes the newest minute from the EaState inbox and skips it, with a recorded reason,
when the runtime is not active here, V6_MINUTE_PACKETS is off, the minute closes an M15
bar (the M15 cycle covers it), no session is active and armed, the rollover block holds,
an m15 packet is open, the newest M15 cycle is missing or too old, or the account is
flat while an intent is still on its way to the EA. Otherwise the engine runs the m1
cycle. Every processed minute is one v6_minute_cycles row; rows older than three days are
pruned on the hour. A failed minute never stops the worker.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Final, Protocol

from ..clock import Clock
from ..config import V6Settings
from ..deliberation.cycle_draft import CycleRequest
from ..deliberation.minute_flow import MinuteBase, MinuteRun
from ..ledger_cycles import LedgerCycles
from ..ledger_minutes import MinuteRow
from ..market.sessions import session_state
from ..providers.operator_queue import OperatorQueue
from ..risk.gates import HALT_SOURCE_FILE, RuntimeGateState
from ..types import TIMEFRAME_SECONDS
from .ea_state import EaState, MinuteItem

logger = logging.getLogger(__name__)

M15_S: Final[int] = TIMEFRAME_SECONDS["M15"]
BASE_MAX_AGE_S: Final[int] = M15_S + 120        # one missed M15 cycle, plus its deadline
PRUNE_AFTER_S: Final[int] = 3 * 86_400
PRUNE_EVERY_S: Final[int] = 3_600
HALT_CHECK_FAILED: Final[str] = "HALT_CHECK_FAILED"
SKIP_INACTIVE: Final[str] = "INACTIVE"
SKIP_DISABLED: Final[str] = "DISABLED"
SKIP_M15_CLOSE: Final[str] = "M15_CLOSE"
SKIP_NOT_ARMED: Final[str] = "NOT_ARMED"
SKIP_ROLLOVER: Final[str] = "ROLLOVER"
SKIP_M15_PENDING: Final[str] = "M15_PENDING"
SKIP_NO_BASE: Final[str] = "NO_M15_CONTEXT"
SKIP_INTENT_ACTIVE: Final[str] = "INTENT_ACTIVE"
NOT_OFFERED: Final[str] = "NOT_OFFERED"


class MinuteEngine(Protocol):
    async def run_minute(self, request: CycleRequest, base: MinuteBase) -> MinuteRun: ...


class Sessions(Protocol):
    async def active(self) -> Any: ...


class ActiveIntent(Protocol):
    def active(self) -> object | None: ...


@dataclass(frozen=True)
class MinuteDeps:
    settings: V6Settings
    clock: Clock
    ea_state: EaState
    engine: MinuteEngine
    sessions: Sessions
    queue: OperatorQueue
    ledger: LedgerCycles
    book: ActiveIntent
    base: Callable[[], MinuteBase | None]
    runtime: Callable[[], RuntimeGateState | None]
    halt_path: Path
    is_active: Callable[[], bool]


@dataclass(frozen=True)
class MinuteWorkerStats:
    processed: int = 0
    offered: int = 0
    answered: int = 0
    skipped: int = 0
    errors: int = 0
    last: MinuteRow | None = None
    running: bool = False

    def to_dict(self) -> dict[str, object]:
        values = {key: value for key, value in asdict(self).items() if key != "last"}
        return {**values, "last": None if self.last is None else self.last.to_dict()}


def _state_of(item: MinuteItem) -> str:
    minute = item.minute
    return "position" if minute.positions else "pending" if minute.pending_orders else "flat"


def _action_of(decision: Any) -> str:
    if decision is None:
        return ""
    manage = decision.manage
    return f"MANAGE:{manage.op}" if manage is not None else str(decision.action)


class MinuteRuntime:
    def __init__(self, deps: MinuteDeps) -> None:
        self._deps = deps
        self._stats = MinuteWorkerStats()

    @property
    def stats(self) -> MinuteWorkerStats:
        return self._stats

    async def run_forever(self) -> None:
        """Consume the minute inbox until cancelled; a failed minute never stops it."""
        self._stats = replace(self._stats, running=True)
        try:
            while True:
                item = await self._deps.ea_state.next_minute()
                try:
                    await self.process(item)
                except Exception as exc:  # noqa: BLE001 - the worker must keep running
                    self._stats = replace(self._stats, errors=self._stats.errors + 1)
                    logger.error("v6 minute %s aborted: %s", item.cycle_id,
                                 type(exc).__name__, exc_info=True)
        finally:
            self._stats = replace(self._stats, running=False)

    async def process(self, item: MinuteItem) -> MinuteRow:
        reason, session, base = await self._gate(item)
        if reason or base is None or session is None:
            row = self._row(item, session, MinuteRun(state=_state_of(item), skipped=reason))
        else:
            row = await self._run(item, session, base)
        await self._record(row)
        await self._prune(item)
        self._note(row)
        return row

    async def _gate(self, item: MinuteItem) -> tuple[str, Any, MinuteBase | None]:
        deps, minute = self._deps, item.minute
        close = minute.bar_close_epoch
        if not deps.is_active():
            return SKIP_INACTIVE, None, None
        if not deps.settings.minute_packets:
            return SKIP_DISABLED, None, None
        if close % M15_S == 0:
            return SKIP_M15_CLOSE, None, None
        session = await deps.sessions.active()
        if session is None or not session.armed:
            return SKIP_NOT_ARMED, session, None
        if session_state(close).rollover_block:
            return SKIP_ROLLOVER, session, None
        if deps.queue.pending_kind() == "m15":
            return SKIP_M15_PENDING, session, None
        base = deps.base()
        if base is None or close - base.context.as_of_epoch > BASE_MAX_AGE_S:
            return SKIP_NO_BASE, session, None
        flat = not (minute.positions or minute.pending_orders)
        if flat and await asyncio.to_thread(deps.book.active) is not None:
            return SKIP_INTENT_ACTIVE, session, None
        return "", session, base

    async def _runtime(self) -> RuntimeGateState:
        carried = self._deps.runtime() or RuntimeGateState(warmed_up=False)
        try:
            halted = await asyncio.to_thread(self._deps.halt_path.exists)
        except OSError:
            return replace(carried, halt_sources=(HALT_CHECK_FAILED,))
        return replace(carried, halt_sources=(HALT_SOURCE_FILE,) if halted else ())

    async def _run(self, item: MinuteItem, session: Any, base: MinuteBase) -> MinuteRow:
        request = CycleRequest(
            cycle_id=item.cycle_id, snapshot=base.snapshot, received_at=item.received_at,
            runtime=await self._runtime(), session_id=session.session_id,
            session_armed=bool(session.armed), minute=item.minute)
        return self._row(item, session, await self._deps.engine.run_minute(request, base))

    def _row(self, item: MinuteItem, session: Any, run: MinuteRun) -> MinuteRow:
        common: dict[str, Any] = {
            "cycle_id": item.cycle_id, "bar_open_epoch": item.minute.bar_open_epoch,
            "state": run.state, "created_at": self._deps.clock.now_epoch(),
            "session_id": "" if session is None else str(session.session_id),
            "tier0_ms": run.tier0_ms}
        if run.outcome is None:
            return MinuteRow(outcome="SKIPPED", reason=run.skipped, **common)
        result = run.outcome.result
        closed = self._deps.queue.closed(item.cycle_id)
        decision = None if closed is None else closed.decision
        reason = "" if decision is not None else (NOT_OFFERED if closed is None else closed.reason)
        return MinuteRow(
            outcome="ANSWERED" if decision is not None else "UNANSWERED", reason=reason,
            action=_action_of(decision), status=result.status,
            hold_reason=str(result.hold_reason or ""),
            agent="" if decision is None else decision.agent,
            latency_ms=0 if decision is None else decision.latency_ms,
            intent_id=result.intent_id or "", **common)

    async def _record(self, row: MinuteRow) -> None:
        try:
            await asyncio.to_thread(self._deps.ledger.minutes.record, row)
        except sqlite3.Error as exc:
            logger.error("v6 minute %s was not recorded: %s", row.cycle_id, type(exc).__name__)

    async def _prune(self, item: MinuteItem) -> None:
        bar_open = item.minute.bar_open_epoch
        if bar_open % PRUNE_EVERY_S != 0:
            return
        try:
            removed = await asyncio.to_thread(self._deps.ledger.minutes.prune,
                                              bar_open - PRUNE_AFTER_S)
        except sqlite3.Error as exc:
            logger.error("v6 minute rows were not pruned: %s", type(exc).__name__)
            return
        if removed:
            logger.info("v6 pruned %d minute rows", removed)

    def _note(self, row: MinuteRow) -> None:
        stats = self._stats
        offered = row.outcome != "SKIPPED"
        self._stats = replace(
            stats, processed=stats.processed + 1, offered=stats.offered + int(offered),
            answered=stats.answered + int(row.outcome == "ANSWERED"),
            skipped=stats.skipped + int(not offered), last=row)
        logger.info("v6 minute %s bar=%d state=%s outcome=%s reason=%s action=%s status=%s "
                    "tier0_ms=%d", row.cycle_id, row.bar_open_epoch, row.state, row.outcome,
                    row.reason or "-", row.action or "-", row.status or "-", row.tier0_ms)
```

- [ ] **Step 5: Sambungan di `runtime/wiring.py`**

`RuntimeParts` mendapat field terakhir `minutes: MinuteRuntime`. Di `build_runtime_parts`, setelah `worker = DeliberationRuntime(...)`:

```python
    minutes = MinuteRuntime(MinuteDeps(
        settings=settings, clock=clock, ea_state=core.ea_state, engine=engine,
        sessions=control.sessions, queue=queue, ledger=ledger_cycles, book=desk.deps.book,
        base=worker.minute_base, runtime=lambda: worker.carry.runtime,
        halt_path=core.halt_path, is_active=core.is_active))
```

dan `minutes=minutes` di `RuntimeParts(...)`. Impor `from .minute_worker import MinuteDeps, MinuteRuntime`.

- [ ] **Step 6: Task runtime di `container.py`**

Di `_runtime_tasks`, tambah `asyncio.create_task(parts.minutes.run_forever(), name="v6-minutes"),` setelah task `v6-worker`.

- [ ] **Step 7: Jalankan tes**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_minute_worker.py tests/v6/test_runtime_service.py tests/v6/test_v6_runtime.py tests/v6/test_container.py`
Expected: PASS (nama file tes runtime yang ada bisa berbeda; jalankan `tests/v6 -k "runtime or container or service"` bila salah satu tidak ada).

- [ ] **Step 8: Jalankan seluruh suite**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add adapter/app/v6/runtime adapter/app/v6/container.py adapter/tests/v6/test_minute_worker.py
git commit -m "feat: run a V6 minute worker beside the M15 worker"
```

---
## Task 10: Status dan dashboard paket menit

**Files:**
- Modify: `adapter/app/routes/v6_status_view.py` (`runtime_status` mendapat `minutes`)
- Create: `adapter/app/v6/dashboard_minutes.py`
- Modify: `adapter/app/routes/v6_dashboard.py` (overview mendapat `minutes`)
- Modify: `adapter/app/templates/v6.html`, `adapter/app/static/v6.js`
- Test: `adapter/tests/v6/test_minute_dashboard.py`

**Interfaces:**
- Consumes: `MinuteRuntime.stats` (Task 9), `LedgerCycles.minutes` (Task 4).
- Produces:
  - `GET /v6/status` → `runtime.minutes` = `MinuteWorkerStats.to_dict()` (termasuk `last`, baris menit terbaru);
  - `dashboard_minutes.WINDOW_S = 7200`, `MinuteTables(window: MinuteStats, day: MinuteStats, recent: tuple[MinuteRow, ...])`, `read_minutes(ledger, now) -> MinuteTables`, `minute_overview(tables) -> {"minutes": {...}}`;
  - kartu dashboard "Minute packets" (`v6-minutes-summary`, tabel `v6-minutes`).

- [ ] **Step 1: Tes yang gagal `adapter/tests/v6/test_minute_dashboard.py`**

```python
"""The minute packets on the dashboard: answered share, actions and the newest rows."""

from __future__ import annotations

from pathlib import Path

from app.v6.dashboard_minutes import WINDOW_S, minute_overview, read_minutes
from app.v6.ledger_cycles import LedgerCycles
from app.v6.ledger_minutes import MinuteRow

NOW = 1_789_600_000.0


def row(age_s: int, outcome: str, **changes: object) -> MinuteRow:
    bar = int(NOW) - age_s
    return MinuteRow(**({"cycle_id": f"m-{bar:016x}", "bar_open_epoch": bar, "state": "flat",
                         "outcome": outcome, "created_at": float(bar + 61), "tier0_ms": 20}
                        | changes))  # type: ignore[arg-type]


def test_the_overview_splits_the_window_and_the_day(tmp_path: Path) -> None:
    ledger = LedgerCycles(tmp_path / "dash.db")
    for item in (row(600, "ANSWERED", action="ENTER"), row(1200, "UNANSWERED"),
                 row(1800, "SKIPPED", reason="GATES:SPREAD"),
                 row(WINDOW_S + 600, "ANSWERED")):
        ledger.minutes.record(item)
    overview = minute_overview(read_minutes(ledger, NOW))["minutes"]
    assert overview["window_hours"] == 2
    assert (overview["window"]["offered"], overview["window"]["answered"]) == (2, 1)
    assert overview["window"]["answered_pct"] == 50.0 and overview["window"]["entries"] == 1
    assert (overview["day"]["offered"], overview["day"]["answered"]) == (3, 2)
    assert [item["outcome"] for item in overview["recent"]][:2] == ["ANSWERED", "UNANSWERED"]
```

- [ ] **Step 2: Jalankan dan pastikan gagal**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_minute_dashboard.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.v6.dashboard_minutes'`).

- [ ] **Step 3: Tulis `adapter/app/v6/dashboard_minutes.py`**

```python
"""
The minute packets on the dashboard (spec sections 4.6 and 9): how many m1 packets the
agent answered before their deadline in the last two hours and the last day, the entries
and management actions they produced, the adapter's p95 time per minute, and the newest
minute rows. Blocking reads: call `read_minutes` in a worker thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .ledger_cycles import LedgerCycles
from .ledger_minutes import MinuteRow, MinuteStats

WINDOW_S: Final[int] = 2 * 3_600          # the spec's "two busy hours"
DAY_S: Final[int] = 86_400
RECENT_ROWS: Final[int] = 12


@dataclass(frozen=True)
class MinuteTables:
    window: MinuteStats
    day: MinuteStats
    recent: tuple[MinuteRow, ...]


def read_minutes(ledger: LedgerCycles, now: float) -> MinuteTables:
    minutes = ledger.minutes
    return MinuteTables(window=minutes.stats(int(now) - WINDOW_S),
                        day=minutes.stats(int(now) - DAY_S),
                        recent=minutes.recent(RECENT_ROWS))


def minute_overview(tables: MinuteTables) -> dict[str, object]:
    return {"minutes": {"window_hours": WINDOW_S // 3_600, "window": tables.window.to_dict(),
                        "day": tables.day.to_dict(),
                        "recent": [row.to_dict() for row in tables.recent]}}
```

- [ ] **Step 4: Overview dashboard**

Di `routes/v6_dashboard.py` impor `from ..v6.dashboard_minutes import minute_overview, read_minutes`; di `v6_overview`, di dalam `try` setelah `management = ...`:

```python
        minutes = await asyncio.to_thread(read_minutes, plane.ledger, now)
```

dan `content = {**build_overview(tables, state), **management_overview(management), **minute_overview(minutes)}` (pecah baris bila > 100 karakter).

- [ ] **Step 5: Status**

Di `routes/v6_status_view.runtime_status`, dict hasil mendapat `"minutes": parts.minutes.stats.to_dict(),` setelah `"worker"`.

- [ ] **Step 6: Kartu dashboard**

`templates/v6.html`, tepat setelah section "Plan & actions":

```html
    <section class="rounded-xl border border-slate-800 bg-slate-900/40">
      {{ card_head('Minute packets', 'One packet per closed M1 bar: how many the agent answered before the deadline, and what the newest minutes did.', 'v6-minutes-summary') }}
      {{ data_table('v6-minutes', [('Minute', ''), ('State', ''), ('Outcome', ''), ('Reason', ''), ('Action', ''), ('Result', ''), ('Agent', ''), ('Latency', R), ('Tier 0', R)], 'No minute packet yet.') }}
    </section>
```

`static/v6.js`, setelah `renderPlan`:

```js
  const MINUTE_COLUMNS = [
    [F, (m) => utc(m.bar_open_epoch)],
    [C, (m) => m.state],
    [C, (m) => statusBadge(m.outcome)],
    [MONO, (m) => m.reason || '—'],
    [MONO, (m) => m.action || '—'],
    [MONO, (m) => m.hold_reason || m.status || '—'],
    [MONO, (m) => m.agent || '—'],
    [N, (m) => (m.latency_ms ? (m.latency_ms / 1000).toFixed(1) + ' s' : '—')],
    [N, (m) => m.tier0_ms + ' ms'],
  ];

  function minuteSummary(label, s) {
    const pct = s.answered_pct === null ? '—' : s.answered_pct + '%';
    return label + ': ' + s.answered + '/' + s.offered + ' answered (' + pct + '), ' +
      s.entries + ' entries, ' + s.manages + ' actions, p95 tier 0 ' + s.tier0_p95_ms + ' ms';
  }

  function renderMinutes(minutes) {
    if (!minutes) return;
    setText('v6-minutes-summary', minuteSummary('last ' + minutes.window_hours + ' h',
      minutes.window) + ' · ' + minuteSummary('24 h', minutes.day));
    renderTable('v6-minutes', MINUTE_COLUMNS, minutes.recent, 'No minute packet yet.');
  }
```

dan `renderMinutes(data.minutes);` di `render(data)` setelah baris `v6-actions`. (`card_head` dengan argumen ketiga membuat elemen teks ber-id itu, seperti `v6-orders-asof`.)

- [ ] **Step 7: Jalankan tes dashboard dan status**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_minute_dashboard.py tests/v6/test_v6_dashboard.py tests/v6/test_v6_dashboard_status.py tests/v6/test_v6_ea_routes.py`
Expected: PASS. Bila ada tes yang membandingkan daftar key overview atau status secara persis, tambahkan `minutes` di sana.

- [ ] **Step 8: Commit**

```bash
git add adapter/app/v6/dashboard_minutes.py adapter/app/routes/v6_dashboard.py adapter/app/routes/v6_status_view.py adapter/app/templates/v6.html adapter/app/static/v6.js adapter/tests/v6/test_minute_dashboard.py
git commit -m "feat: show V6 minute packets on the status and the dashboard"
```

---

## Task 11: CLI operator untuk paket `m1` dan `submit --quick`

**Files:**
- Create: `adapter/scripts/v6ops/minute_view.py`
- Modify: `adapter/scripts/v6ops/waiting.py` (ringkasan per jenis paket)
- Modify: `adapter/scripts/v6ops/decisions.py` (templat cadangan `m1`, `quick_decision`, `run_submit(quick=)`, hasil siklus menit)
- Modify: `adapter/scripts/v6_operator.py` (`submit --quick`)
- Test: `adapter/tests/v6/test_v6_operator_cli_minute.py`

**Interfaces:**
- Consumes: paket `m1` (Task 6), `GET /v6/status` `runtime.minutes.last` (Task 10).
- Produces:
  - `minute_view.render_minute(packet, *, now, path) -> str` (tiga baris), `minute_view.render_any(packet, *, now, path) -> str`;
  - `decisions.quick_decision(packet, agent) -> dict`, `decisions.run_submit(ctx, *, agent, decision_path, packet_path, quick=False) -> int`;
  - opsi CLI `submit --quick`.

- [ ] **Step 1: Tes yang gagal `adapter/tests/v6/test_v6_operator_cli_minute.py`**

```python
"""The operator CLI with m1 packets: the short summary and submit --quick."""

from __future__ import annotations

import importlib
from pathlib import Path

from . import operator_fixtures_v6 as of
from .operator_cli_fixtures_v6 import cli  # noqa: F401 (puts the CLI on sys.path)
from .test_minute_packet import MINUTE_CLOSE, sealed_minute_packet

minute_view = importlib.import_module("v6ops.minute_view")
decisions = importlib.import_module("v6ops.decisions")


def document(packet) -> dict:
    return packet.model_dump(mode="json")


def test_an_m1_packet_gets_a_three_line_summary() -> None:
    packet = document(sealed_minute_packet())
    lines = minute_view.render_any(packet, now=float(MINUTE_CLOSE + 2),
                                   path=Path("packet.json")).splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("M1 PACKET ") and "flat" in lines[0] and "48 s left" in lines[0]
    assert "5 bars up" in lines[1] and "atr" in lines[1]
    assert "--quick" in lines[2]


def test_an_m15_packet_keeps_the_full_summary() -> None:
    packet = document(of.packet())
    text = minute_view.render_any(packet, now=float(of.BAR_CLOSE + 5), path=Path("p.json"))
    assert text.startswith("V6 PACKET ")


def test_the_fallback_template_of_an_m1_packet_has_no_views() -> None:
    packet = document(sealed_minute_packet())
    expected = packet.pop("decision_template")
    assert decisions.build_template(packet) == {**expected, "agent": None}


def test_quick_holds_a_flat_m1_packet() -> None:
    decision = decisions.quick_decision(document(sealed_minute_packet()), "claude_code")
    assert (decision["action"], decision["agent"], decision["views"], decision["m15_bias"]) == (
        "HOLD", "claude_code", None, None)


def test_quick_keeps_a_managed_trade_and_carries_the_m15_bias() -> None:
    decision = decisions.quick_decision(document(of.managed_packet("position")), "codex")
    assert (decision["action"], decision["manage"]["op"]) == ("MANAGE", "KEEP")
    assert decision["m15_bias"]["carried"] is True
```

- [ ] **Step 2: Jalankan dan pastikan gagal**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_v6_operator_cli_minute.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'v6ops.minute_view'`).

- [ ] **Step 3: Tulis `adapter/scripts/v6ops/minute_view.py`**

```python
"""
The short summary `wait` prints for an m1 packet (three lines; spec section 5).

An m1 packet comes every minute and must be answered within V6_M1_DEADLINE_S, so the
summary shows only what an m1 decision needs: the state and quote, what the last M1 bars
did, the M15 bias still in force, the managed trade's distances, and the quick answer.
Everything else stays in packet.json. An m15 packet keeps the full summary.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .context import get_path
from .packet_view import num, render_packet, utc

QUICK_HINT = ("no change: submit --agent <name> --quick | otherwise template, edit "
              "(entry_plan or manage), submit before ")


def _move(move: object) -> str:
    if not isinstance(move, Mapping):
        return "?"
    return (f"{move.get('bars')} bars {move.get('direction')} {num(move.get('change'), '+.2f')} "
            f"({num(move.get('strength'), '.1f')}x)")


def _trade(packet: Mapping[str, Any], now: float) -> str:
    state = packet.get("state")
    block = packet.get("position") if state == "position" else packet.get("pending_order")
    if not isinstance(block, Mapping):
        return ""
    distances = get_path(packet, "m1_state", "distances") or {}
    to = " ".join(f"{key} {num(value, '+.2f')}" for key, value in distances.items())
    if state == "position":
        left = (block.get("time_limit_epoch") or 0) - now
        return (f" | pos {block.get('side')} {block.get('lots')} @ {num(block.get('open_price'))}"
                f" step {get_path(block, 'plan', 'step')} | to {to} | {max(0, int(left // 60))}"
                f" min left")
    return f" | order {block.get('order_type')} @ {num(block.get('price'))} | to {to}"


def _bias(packet: Mapping[str, Any]) -> str:
    bias = packet.get("last_bias")
    if not isinstance(bias, Mapping):
        return "bias none"
    return (f"bias {bias.get('direction')} @{utc(packet.get('last_bias_at_epoch'))[11:16]}Z "
            f"inv {num(bias.get('invalidation'))}")


def render_minute(packet: Mapping[str, Any], *, now: float, path: Path) -> str:
    state = packet.get("m1_state") or {}
    market = packet.get("market") or {}
    left = int((packet.get("expires_at_epoch") or 0) - now)
    first = (f"M1 PACKET {packet.get('cycle_id')} | {utc(packet.get('bar_close_epoch'))[11:16]}Z"
             f" | {packet.get('state')} | bid {num(market.get('bid'))} ask {num(market.get('ask'))}"
             f" spr {market.get('spread_points')} | {max(0, left)} s left | file {path}")
    second = (f"m1: atr {num(state.get('atr_m1'))} range15 {num(state.get('range_15'))} | "
              f"{_move(state.get('last_5'))} | {_move(state.get('last_15'))} | "
              f"{num(state.get('quotes_per_s'), '.1f')} q/s | {_bias(packet)}{_trade(packet, now)}")
    third = QUICK_HINT + utc(packet.get("expires_at_epoch"))
    return "\n".join((first, second, third)) + "\n"


def render_any(packet: Mapping[str, Any], *, now: float, path: Path) -> str:
    if packet.get("packet_kind") == "m1":
        return render_minute(packet, now=now, path=path)
    return render_packet(packet, now=now, path=path)
```

(Periksa nama helper `num`, `utc`, `get_path` di `packet_view.py`/`context.py`; `num(value, pattern)` sudah ada. Bila `utc` tidak mengembalikan format `YYYY-MM-DDTHH:MM:SSZ`, pakai helper jam yang ada.)

- [ ] **Step 4: `waiting.finish` memakai `render_any`**

Ganti `render_packet(outcome.packet, ...)` dengan `render_any(outcome.packet, now=ctx.clock(), path=plan.out_path)` dan impor `from .minute_view import render_any`. Di `packet_problems`, tambah cek `(packet.get("packet_kind", "m15") in ("m15", "m1"), "packet_kind")`.

- [ ] **Step 5: `decisions.py`**

1. `fallback_template`: paket `m1` tanpa view dan bias:

```python
    m15 = packet.get("packet_kind", "m15") == "m15"
    ...
        "views": _baseline_or_defaults(packet.get("baseline_views")) if m15 else None,
        ...
        "m15_bias": (_plain(bias) if isinstance(bias, Mapping) else _plain(UNCLEAR_BIAS))
                    if m15 else None,
```

2. Fungsi baru:

```python
QUICK_NOTE: Final[str] = "quick: no change"


def quick_decision(packet: Mapping[str, Any], agent: str) -> dict[str, Any]:
    """`submit --quick`: no change for the packet (HOLD, or KEEP the managed trade); an
    m15 packet carries the last bias forward marked `carried` (or unclear)."""
    decision = with_agent(build_template(packet), agent)
    if decision.get("packet_kind", "m15") == "m15":
        bias = decision.get("m15_bias")
        decision["m15_bias"] = {**(bias if isinstance(bias, Mapping) else UNCLEAR_BIAS),
                                "carried": True}
    managed = decision.get("manage") is not None
    return {**decision, "action": "MANAGE" if managed else "HOLD", "entry_plan": None,
            "note": QUICK_NOTE}
```

3. `run_submit` mendapat `quick: bool = False`:

```python
def run_submit(ctx: Context, *, agent: str, decision_path: Path, packet_path: Path,
               quick: bool = False) -> int:
    if quick:
        decision = quick_decision(load_json_file(packet_path, "packet file"), agent)
        warnings: list[str] = []
    else:
        decision = with_agent(read_decision(ctx, decision_path), agent)
        warnings = submission_warnings(decision, _optional_packet(packet_path), ctx.clock())
    body = encode_decision(decision)
    ...  # (unchanged)
    done = code == EXIT_OK and not quick
    emit(ctx.stdout, with_result(ctx, summary) if done else summary)
    return code
```

(`--quick` tidak menunggu hasil siklus: jawabannya selalu "tidak ada perubahan", dan menit berikutnya datang sebentar lagi.)

4. `cycle_result` mengenali siklus menit:

```python
def _cycle_in(status: object, cycle_id: object) -> Mapping[str, Any] | None:
    """The finished cycle in a status reply: the M15 worker's, or the minute worker's."""
    for path in (("last_cycle",), ("runtime", "minutes", "last")):
        cycle = get_path(status, *path)
        if isinstance(cycle, Mapping) and cycle.get("cycle_id") == cycle_id:
            return cycle
    return None
```

dan di dalam loop `cycle_result`: `cycle = _cycle_in(reply.json(), cycle_id)`; `if cycle is not None: return {key: cycle[key] for key in RESULT_KEYS if key in cycle}`. Tambah `"outcome"` dan `"action"` ke `RESULT_KEYS`.

Periksa `wc -l scripts/v6ops/decisions.py` ≤ 400; bila lebih, pindahkan `quick_decision` dan `QUICK_NOTE` ke `scripts/v6ops/quick.py` dan impor dari sana.

- [ ] **Step 6: `v6_operator.py`**

Di `_add_loop_commands`, setelah argumen `--packet` milik `submit`:

```python
    submit.add_argument("--quick", action="store_true",
                        help="no change for the packet (HOLD, or KEEP the managed trade); "
                             "reads packet.json, writes no decision file")
```

dan di `dispatch`: `decisions.run_submit(ctx, agent=args.agent, decision_path=args.file, packet_path=args.packet, quick=args.quick)`.

- [ ] **Step 7: Jalankan tes CLI**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_v6_operator_cli_minute.py tests/v6/test_v6_operator_cli_v3.py tests/v6/test_v6_operator_cli.py tests/v6/test_agent_harness_parity.py`
Expected: PASS (nama file tes CLI lama bisa berbeda; jalankan `tests/v6 -k cli` bila perlu).

- [ ] **Step 8: Commit**

```bash
git add adapter/scripts adapter/tests/v6/test_v6_operator_cli_minute.py
git commit -m "feat: summarise V6 m1 packets and add submit --quick"
```

---
## Task 12: EA 6.3.0 — snapshot menit

**Files:**
- Create: `ea/QlipV6/Cadence.mqh` (irama snapshot M15 dipindah dari file utama, plus snapshot menit)
- Modify: `ea/QlipV6/Market.mqh` (`M1_SECONDS`, jendela tick bebas, baris bar tunggal)
- Modify: `ea/QlipV6/Snapshot.mqh` (`BuildMinuteJson`, versi 6.3.0)
- Modify: `ea/QlipV6_XAUUSD.mq5` (input menit, include `Cadence.mqh`, `ServiceMinute()`, versi 6.30)
- Modify: `adapter/tests/v6/test_golden_contract.py`, `adapter/tests/v6/test_ea_safety_parity.py`
- Modify: `docs/v6-wire-contract.md` (rute `/v6/minute` dan bentuk `v6.minute.1`; tes paritas meminta setiap rute EA ada di dokumen)

**Interfaces:**
- Consumes: `AccountJson`, `QuoteJson`, `TickStatsJson`, `PositionsJson`, `PendingOrdersJson`, `DayJson`, `EaStateJson`, `ClosedBarsJson`, `HttpPostJson`, `FillEaStatus`, `AdapterUrl`.
- Produces: input `InpMinuteSnapshots` (true) dan `InpMinuteTimeoutMs` (800); `ServiceMinute()` di `OnTimer`; body `v6.minute.1` ke `/v6/minute` sekali per close M1 (tanpa antrean ulang; kegagalan dicatat paling banyak sekali per 15 menit).

- [ ] **Step 1: Tes paritas yang gagal**

Di `tests/v6/test_golden_contract.py`:
1. impor `from app.v6.schemas.minute import MinuteSnapshot`;
2. parametrize `test_golden_emits_every_top_level_field` mendapat `("minute_sample.json", MinuteSnapshot)`;
3. parametrize `test_ea_sources_emit_every_contract_field` dan `test_ea_writes_every_field_with_the_contract_number_kind` mendapat `MinuteSnapshot`;
4. `test_ea_emits_the_contract_schema_versions` memeriksa juga `"v6.minute.1"`;
5. tes baru:

```python
def test_minute_golden_validates_against_minute_snapshot() -> None:
    minute = MinuteSnapshot.model_validate_json(_read_golden("minute_sample.json"))
    assert minute.snapshot_id == f"Q6M-{minute.account.login}-{minute.bar_open_epoch}"
    assert minute.ticks.window_s == 60
```

Di `tests/v6/test_ea_safety_parity.py`, set rute di `test_ea_routes_are_contract_routes` mendapat `"/v6/minute"`.

- [ ] **Step 2: Jalankan dan pastikan gagal**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_golden_contract.py tests/v6/test_ea_safety_parity.py`
Expected: FAIL (EA belum menulis `"bar"`, `"v6.minute.1"`, dan rute `/v6/minute`).

- [ ] **Step 3: `Market.mqh`**

1. `#define M1_SECONDS          60` di sebelah `M15_SECONDS`.
2. `struct TickStats` mendapat `int window_s;` (field pertama).
3. `ComputeTickStats(close_server, stats)` menjadi pembungkus:

```mql5
// Tick statistics over the `window_s` seconds before `close_server`.
void ComputeTickStatsOver(const datetime close_server, const int window_s, TickStats &stats)
{
   stats.window_s = window_s;
   long to_msc = (long)close_server * MS_PER_SECOND - 1;
   long from_msc = ((long)close_server - window_s) * MS_PER_SECOND;
   // ... the rest of the former ComputeTickStats body, unchanged ...
}

void ComputeTickStats(const datetime close_server, TickStats &stats)
{
   ComputeTickStatsOver(close_server, TICK_WINDOW_SECONDS, stats);
}
```

4. `TickStatsJson` menulis `o.AddInt("window_s", stats.window_s);`.
5. Helper baris tunggal:

```mql5
// The closed bar of `tf` that ends at `close_server` as one row [t,o,h,l,c,tv,spr],
// or "" when that bar is not in the history yet.
string ClosedBarRowJson(const ENUM_TIMEFRAMES tf, const datetime close_server,
                        const int offset_s, const int digits)
{
   string rows = ClosedBarsJson(tf, close_server, 1, offset_s, digits);
   int length = StringLen(rows);
   if(length < 3 || StringGetCharacter(rows, 0) != '[' || StringGetCharacter(rows, 1) != '[')
      return "";
   return StringSubstr(rows, 1, length - 2);
}
```

- [ ] **Step 4: `Snapshot.mqh`**

`#define V6_EA_VERSION "6.3.0"` dan fungsi baru setelah `BuildSnapshotJson`:

```mql5
// Minute snapshot for the M1 bar that opened at `bar_open_server` and has closed.
// Returns "" when there is no quote or the bar is not in the history yet.
string BuildMinuteJson(const long magic, const datetime bar_open_server, const EaStatus &status)
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick) || tick.bid <= 0.0 || tick.ask <= 0.0)
      return "";
   int offset = ServerGmtOffsetSeconds();
   datetime close_server = bar_open_server + M1_SECONDS;
   string bar = ClosedBarRowJson(PERIOD_M1, close_server, offset, _Digits);
   if(bar == "")
      return "";
   datetime now_utc = TimeGMT();
   long bar_open_utc = ServerToUtc(bar_open_server, offset);
   TickStats stats;
   ComputeTickStatsOver(close_server, M1_SECONDS, stats);

   CJsonObject o;
   o.AddStr("schema_version", "v6.minute.1");
   o.AddStr("snapshot_id", "Q6M-" + LoginText() + "-" + JInt(bar_open_utc));
   o.AddStr("symbol", _Symbol);
   o.AddInt("sent_at_epoch", (long)now_utc);
   o.AddInt("server_gmt_offset_s", offset);
   o.AddInt("bar_open_epoch", bar_open_utc);
   o.AddRaw("bar", bar);
   o.AddRaw("account", AccountJson());
   o.AddRaw("quote", QuoteJson(tick, offset));
   o.AddRaw("ticks", TickStatsJson(stats));
   o.AddRaw("positions", PositionsJson(magic, offset));
   o.AddRaw("pending_orders", PendingOrdersJson(magic, offset));
   o.AddRaw("day", DayJson(magic, now_utc, offset));
   o.AddRaw("ea_state", EaStateJson(status));
   return o.Text();
}
```

- [ ] **Step 5: Tulis `ea/QlipV6/Cadence.mqh`**

Pindahkan apa adanya dari `QlipV6_XAUUSD.mq5` fungsi `TrySendSnapshot`, `DetectClosedBar` dan `ServiceSnapshot` (baris 173–229) ke file ini, lalu tambahkan irama menit:

```mql5
//+------------------------------------------------------------------+
//| Cadence.mqh: when snapshots go out.                               |
//|  - one snapshot per closed M15 bar, retried for InpSnapshotRetryS  |
//|  - one minute snapshot per closed M1 bar, sent once and never      |
//|    retried: the next minute replaces it (spec section 3.1)         |
//| Included by the main file after its inputs and globals.           |
//+------------------------------------------------------------------+
#ifndef QLIPV6_CADENCE_MQH
#define QLIPV6_CADENCE_MQH

#define PATH_MINUTE         "/v6/minute"
#define MINUTE_LOG_EVERY_S  900      // at most one minute-failure line per 15 minutes

datetime g_last_m1_open = 0;         // server open time of the forming M1 bar
datetime g_minute_log_at = 0;        // UTC time of the last minute-failure line

// (TrySendSnapshot, DetectClosedBar and ServiceSnapshot, moved here unchanged)

// The M1 bar that just closed, or 0 (first sighting, no new bar, or a gap).
datetime DetectClosedMinute(void)
{
   datetime forming = iTime(_Symbol, PERIOD_M1, 0);
   if(forming == 0 || forming == g_last_m1_open)
      return 0;
   bool first_sighting = (g_last_m1_open == 0);
   g_last_m1_open = forming;
   if(first_sighting)
      return 0;
   datetime closed = iTime(_Symbol, PERIOD_M1, 1);
   if(closed == 0 || (long)TimeTradeServer() - ((long)closed + M1_SECONDS) > M1_SECONDS)
      return 0;
   return closed;
}

void LogMinuteFailure(const HttpResult &result)
{
   datetime now = TimeGMT();
   if((long)now - (long)g_minute_log_at < MINUTE_LOG_EVERY_S)
      return;
   g_minute_log_at = now;
   Print(HttpDescribeFailure("minute", AdapterUrl(PATH_MINUTE), result));
}

void ServiceMinute(void)
{
   if(!InpMinuteSnapshots)
      return;
   datetime closed = DetectClosedMinute();
   if(closed == 0)
      return;
   EaStatus status;
   FillEaStatus(status);
   string body = BuildMinuteJson(InpMagic, closed, status);
   if(body == "")
      return;
   HttpResult result;
   if(!HttpPostJson(AdapterUrl(PATH_MINUTE), body, InpMinuteTimeoutMs, 1, result))
      LogMinuteFailure(result);
}

#endif // QLIPV6_CADENCE_MQH
```

- [ ] **Step 6: File utama `ea/QlipV6_XAUUSD.mq5`**

1. `#property version "6.30"`.
2. Input baru setelah `InpSnapshotRetryS`:

```mql5
input bool   InpMinuteSnapshots    = true;    // Send one minute snapshot per closed M1 bar
input int    InpMinuteTimeoutMs    = 800;     // Minute snapshot timeout, ms
```

3. `InputsAreValid` menolak `InpMinuteTimeoutMs` di luar 200–1500 (pesan seperti cek timeout lain).
4. Tiga fungsi yang dipindah diganti `#include "QlipV6/Cadence.mqh"` di tempat yang sama (setelah `LogStartup`).
5. `OnInit`: `g_last_m1_open = iTime(_Symbol, PERIOD_M1, 0);` setelah `g_last_m15_open = ...`.
6. `OnTimer`: `ServiceMinute();` tepat setelah `ServiceSnapshot();`.

Periksa: file utama ≤ 300 baris, setiap `.mqh` ≤ 400 baris.

- [ ] **Step 7: Kontrak wire (minimal, lengkap di Task 15)**

Di `docs/v6-wire-contract.md`: tabel endpoint mendapat baris `` `/v6/minute` `` (POST, sekali per close M1, tanpa kirim ulang, timeout 800 ms, 202/200 duplicate/400), dan sub-bagian baru "6.6 `POST /v6/minute` — `v6.minute.1`" yang mendaftar field (`schema_version`, `snapshot_id` `Q6M-<login>-<bar_open_epoch>`, `symbol`, `sent_at_epoch`, `server_gmt_offset_s`, `bar_open_epoch`, `bar` `[t,o,h,l,c,tv,spr]`, `account`, `quote`, `ticks` dengan `window_s` 60, `positions`, `pending_orders`, `day`, `ea_state`) dan menyebut bahwa bar M1 disimpan sebelum menit diantrikan.

- [ ] **Step 8: Jalankan tes paritas**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_golden_contract.py tests/v6/test_ea_safety_parity.py tests/v6/test_ea_wire_parity.py`
Expected: PASS.

- [ ] **Step 9: Deploy dan kompilasi**

```bash
bash "<scratchpad>/compile_v6.sh"
```

Expected: `Result: 0 errors, 0 warnings`; file di `MQL5\Experts` identik dengan repo (`cmp`). Kompilasi baris perintah tidak memuat ulang EA yang berjalan: laporkan ke pengguna bahwa EA harus dipasang ulang (atau MT5 di-restart), lalu baris `V6 EA 6.3.0 started` harus muncul di `MQL5\Logs`.

- [ ] **Step 10: Commit**

```bash
git add ea adapter/tests/v6/test_golden_contract.py adapter/tests/v6/test_ea_safety_parity.py docs/v6-wire-contract.md
git commit -m "feat: send one V6 minute snapshot per closed M1 bar (EA 6.3.0)"
```

---

## Task 13: Pensiunkan keputusan v1/v2

**Files:**
- Modify: `adapter/app/v6/deliberation/decision_parts.py` (`parse_envelope`, `DecisionEnvelope` dihapus)
- Modify: `adapter/app/v6/deliberation/operator_decision.py` (jalur v2 dihapus)
- Modify: `adapter/app/v6/schemas/operator.py`, `adapter/app/v6/schemas/operator_checks.py` (model dan parser v2 dihapus)
- Modify: `adapter/app/v6/deliberation/operator_flow.py`, `adapter/app/v6/deliberation/engine.py` (`_agent_item` dan cabang v2 di `_entry_item`/`_resolve` dihapus)
- Modify: `adapter/scripts/v6ops/decisions.py` (hanya v3)
- Modify: tes v6 yang memakai keputusan v1/v2 (tabel di Step 5)
- Test: `adapter/tests/v6/test_decision_retired.py`

**Interfaces:**
- Produces: sebuah body dengan `schema_version` `v6.operator.decision.1` atau `.2` ditolak `DECISION_SCHEMA` dengan detail "decisions v1 and v2 are retired: answer with v6.operator.decision.3 (run OP template)"; `DECISION_SCHEMAS = (DECISION_SCHEMA,)`.

- [ ] **Step 1: Tes yang gagal `adapter/tests/v6/test_decision_retired.py`**

```python
"""Decisions v1 and v2 are retired (spec section 9, phase B)."""

from __future__ import annotations

import pytest

from app.v6.deliberation.decision_parts import DecisionError
from app.v6.deliberation.operator_decision import validate_decision

from . import operator_fixtures_v6 as of


@pytest.mark.parametrize("version", ["v6.operator.decision.1", "v6.operator.decision.2"])
def test_old_decisions_are_refused(version: str) -> None:
    sealed = of.packet()
    document = {**of.decision_v3(sealed), "schema_version": version}
    outcome = validate_decision(sealed, of.raw(document), of.settings(), now=of.EXPIRES - 1)
    assert isinstance(outcome, DecisionError) and outcome.code == "DECISION_SCHEMA"
    assert "retired" in outcome.detail
```

(`of.settings()` dan `of.EXPIRES` adalah helper yang dipakai tes keputusan lain; bila namanya berbeda, pakai settings dan waktu yang sama dengan `tests/v6/test_decision_v3.py`.)

- [ ] **Step 2: Jalankan dan pastikan gagal**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_decision_retired.py`
Expected: FAIL (body v2 masih diparse sebagai `DecisionEnvelope`).

- [ ] **Step 3: `parse_envelope` hanya v3**

```python
RETIRED_SCHEMAS: Final[frozenset[str]] = frozenset({
    "v6.operator.decision.1", "v6.operator.decision.2"})
RETIRED_DETAIL: Final[str] = ("decisions v1 and v2 are retired: answer with "
                              "v6.operator.decision.3 (run OP template)")


def parse_envelope(raw: bytes) -> DecisionEnvelopeV3 | DecisionError:
    """The strict v3 envelope; the views stay raw for per-role checks."""
    if schema_of(raw) in RETIRED_SCHEMAS:
        return decision_error(DECISION_ERR_SCHEMA, RETIRED_DETAIL)
    return parse_model(DecisionEnvelopeV3, raw)
```

Hapus `DecisionEnvelope`, `Envelope` menjadi alias `DecisionEnvelopeV3`, dan `KNOWN_FIELDS` dihitung dari `DecisionEnvelopeV3, RawViews, EntryPlanV2, ManageRequest, M15Bias`.

- [ ] **Step 4: Hapus jalur v2 di produksi**

- `operator_decision.py`: `_validate_v2` dan helper-nya (cek Chief, rebuttal, `entry_plan` v2, `lots`) dihapus; `validate_decision` hanya memanggil `validate_minute` atau `validate_v3`. `panel_from_decision` dan `timeout_panel` tetap.
- `schemas/operator.py`: hapus `OperatorDecision`, `DECISION_SCHEMA_V2`, `DecisionSchema`, `DECISION_ERR_REBUTTAL`, re-export `operator_checks` untuk v2 (`OperatorDecisionError`, `decision_extras_problem`, `entry_plan_problem`, `parse_operator_decision`) dan entri `__all__`-nya; `DECISION_SCHEMAS = (DECISION_SCHEMA,)`. Hapus fungsi v2 di `schemas/operator_checks.py` (bila file itu kosong setelahnya, hapus file dan impornya).
- `decision_parts.ValidatedDecision`: hapus field `rebuttal`, `entry_plan`, `lots`; `withdrawn_ids` menjadi `frozenset()` tetap (properti) supaya `OperatorRound.withdrawn_ids` dan protokol tidak berubah.
- `operator_flow.py`: hapus `_agent_item` dan impor `AgentEntryPlan`, `agent_candidate`, `limits_from_packet`.
- `engine.py`: `_entry_item` tanpa cabang `decision.entry_plan`; di `_resolve`, `lots = plan.lots if plan is not None else tier0.context.spec.volume_min` (tanpa `decision.lots`).
- `AgentEntryPlan` di `schemas/operator_parts.py` dan fungsi v2 di `deliberation/agent_entry.py` dihapus hanya bila tidak dipakai lagi (`grep -rn "AgentEntryPlan\|agent_candidate\|limits_from_packet" adapter/app adapter/scripts`).
- `scripts/v6ops/decisions.py`: `DECISION_SCHEMAS = (DECISION_SCHEMA,)`; peringatan untuk skema lain berbunyi "decisions v1 and v2 are retired: run template".

Setelah itu jalankan `namecheck.py` pada semua file yang disentuh (tidak ada nama tak terpakai atau tak terdefinisi).

- [ ] **Step 5: Migrasi tes**

| File | Tindakan |
|---|---|
| `tests/v6/operator_fixtures_v6.py` | hapus `decision`, `as_v2`, `V2_KEYS`; `decision_v3` tetap |
| `tests/v6/test_operator_decision.py` | tes umum (ukuran, bukan JSON, hash basi, kedaluwarsa, agen tak diizinkan, injeksi di catatan, field asing) memakai `of.decision_v3(sealed)`; tes rebuttal, bentuk Chief, `entry_plan` v2 dan `lots` v2 dihapus |
| `tests/v6/test_operator_entry_plan.py` | hapus tes keputusan v2; aturan rencana v3 sudah diuji di `test_plan_rules.py` dan `test_decision_v3.py` (hapus file bila tidak ada tes yang tersisa) |
| `tests/v6/test_operator_queue.py` | `_raw`/body keputusan memakai `of.decision_v3`; tes yang mengubah view mengubah `views` v3 |
| `tests/v6/test_operator_schemas.py` | hapus tes `parse_operator_decision` |
| `tests/v6/test_decision_v3.py` | tes "v2 untuk paket manajemen → DECISION_KIND" menjadi "v2 → DECISION_SCHEMA retired" |
| `tests/v6/execute_fixtures_v6.py`, `tests/v6/test_engine_operator.py` | `as_v2(...)` + Chief ENTER diganti keputusan v3: `action` ENTER, `entry_plan` bentuk v3 (sl, tp1–tp3, `time_limit_min`, `pending_expiry_min`, `lots`) dan Price Action TAKE `limits.agent_entry_id` conviction 0.8 |
| `tests/v6/test_v6_operator_decisions.py`, tes e2e (`test_v6_execute_e2e.py`, `test_v6_phase2_e2e.py`) | ikut perubahan fixture; keputusan yang masih v2 ditulis ulang ke v3 |
| tes CLI yang memeriksa peringatan v2 | harapkan teks "retired" |

Jalankan `grep -rn "operator.decision.1\|operator.decision.2\|as_v2\|of.decision(" adapter/tests` sampai hanya tes `test_decision_retired.py` yang tersisa.

- [ ] **Step 6: Jalankan seluruh suite**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add adapter/app adapter/scripts adapter/tests
git commit -m "refactor: retire V6 operator decisions v1 and v2"
```

---

## Task 14: Replay ritme menit

**Files:**
- Modify: `adapter/scripts/v6replay/evaluate.py` (ekstrak `bar_context`)
- Create: `adapter/scripts/v6replay/minutes.py`
- Modify: `adapter/scripts/v6_replay.py` (`--minutes`)
- Test: `adapter/tests/v6/test_replay_minutes.py`

**Interfaces:**
- Consumes: `minute_context`, `minute_settings`, `evaluate_gates`, `build_packet(kind="m1")`, `trade_state`.
- Produces: `evaluate.bar_context(bar, bars, stored_events, settings, exposure) -> tuple[V6Snapshot, MarketContext, float] | None`; `minutes.MinuteTally(minutes: int, packets: int, blocked_by: dict[str, int], per_day: dict[str, int], build_ms_p50: float, build_ms_p95: float)`; `minutes.run_minute_replay(bars, stored_events, settings, window) -> MinuteTally`; opsi `v6_replay.py --minutes`.

- [ ] **Step 1: Tes yang gagal `adapter/tests/v6/test_replay_minutes.py`**

Pakai pembangun `BarSet` yang dipakai `tests/v6/test_v6_replay.py` (atau `v6replay.data.load_csv_dir` pada direktori sementara berisi CSV M1–D1 datar, seperti tes replay yang ada).

```python
"""The minute replay counts m1 packets and times the adapter per minute."""

from __future__ import annotations

import importlib

from .test_v6_replay import small_barset, replay_settings_for_test  # the helpers of the M15 replay tests

minutes = importlib.import_module("v6replay.minutes")
runner = importlib.import_module("v6replay.runner")


def test_every_minute_between_two_m15_closes_is_counted() -> None:
    bars, events = small_barset()
    tally = minutes.run_minute_replay(bars, events, replay_settings_for_test(),
                                      runner.ReplayWindow())
    evaluated = tally.minutes
    assert evaluated > 0 and evaluated % 14 == 0          # the M15 close minute is not an m1
    assert 0 <= tally.packets <= evaluated
    assert sum(tally.per_day.values()) == tally.packets
    assert sum(tally.blocked_by.values()) == evaluated - tally.packets
    assert 0 <= tally.build_ms_p50 <= tally.build_ms_p95
```

(Bila `test_v6_replay.py` tidak punya helper bernama begitu, buat helper kecil di file tes ini dengan data sintetis yang sama seperti tes replay M15.)

- [ ] **Step 2: Jalankan dan pastikan gagal**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_replay_minutes.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'v6replay.minutes'`).

- [ ] **Step 3: `evaluate.bar_context`**

Ekstrak dari `evaluate_bar` bagian yang membangun snapshot sintetis dan konteks M15:

```python
def bar_context(bar: Bar, bars: BarSet, stored_events: Sequence[CalendarEventBlock],
                settings: V6Settings, exposure: Exposure
                ) -> tuple[V6Snapshot, MarketContext, float] | None:
    """(snapshot, context, now) of M15 `bar` at its close; None without enough history."""
    as_of = bar.t + M15_S
    if insufficiency(bars, as_of):
        return None
    snapshot = synth_snapshot(bar, stored_events, exposure)
    now = as_of + RECEIVE_DELAY_S
    window = load_bars(CutReader(bars, as_of), snapshot)
    context = build_context(ContextRequest(
        cycle_id=cycle_id_for(snapshot.snapshot_id), snapshot=snapshot,
        received_at=float(now)), window, settings, float(now))
    return snapshot, context, float(now)
```

`evaluate_bar` memakai fungsi ini (perilaku tidak berubah; tes replay M15 tetap hijau).

- [ ] **Step 4: Tulis `adapter/scripts/v6replay/minutes.py`**

```python
"""
The minute rhythm replay (spec section 8): how many m1 packets a day would reach the
agent, and how long the adapter needs per minute.

For every stored M15 bar in the window it builds the M15 context at the bar close
(`evaluate.bar_context`), then turns each of the next 14 closed M1 bars into a minute
snapshot (its close as the bid, its stored spread) and runs the minute context, the hard
gates and the m1 packet build. A flat minute whose gates pass is one m1 packet. The agent
is scripted to change nothing, so the account stays flat.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

from app.v6.config import V6Settings
from app.v6.cycle_types import DeskViews, MarketContext
from app.v6.deliberation.minute_context import minute_context, minute_settings
from app.v6.deliberation.operator_packet import PacketRequest, build_packet
from app.v6.market.features import bars_closed_by
from app.v6.risk.gates import RuntimeGateState, evaluate_gates, first_failure
from app.v6.schemas.minute import MinuteSnapshot, minute_snapshot_id
from app.v6.schemas.snapshot import CalendarEventBlock, V6Snapshot
from app.v6.types import TIMEFRAME_SECONDS, Bar

from .data import BarSet
from .evaluate import REPLAY_SESSION_ID, bar_context, healthy_breakers
from .runner import ReplayWindow
from .synth import Exposure

M1_S: Final[int] = TIMEFRAME_SECONDS["M1"]
M15_S: Final[int] = TIMEFRAME_SECONDS["M15"]
MINUTES_PER_M15: Final[int] = M15_S // M1_S - 1        # the M15 close minute is the m15 packet
RECEIVE_DELAY_S: Final[int] = 1
MS: Final[float] = 1000.0


@dataclass(frozen=True)
class MinuteTally:
    minutes: int
    packets: int
    blocked_by: dict[str, int]
    per_day: dict[str, int]
    build_ms_p50: float
    build_ms_p95: float


def _minute(snapshot: V6Snapshot, bar: Bar) -> MinuteSnapshot:
    point = snapshot.symbol_spec.point
    quote = {"bid": bar.c, "ask": round(bar.c + bar.spr * point, 5),
             "spread_points": bar.spr, "time_msc": (bar.t + M1_S) * 1000}
    document = {
        "schema_version": "v6.minute.1",
        "snapshot_id": minute_snapshot_id(snapshot.account.login, bar.t),
        "symbol": snapshot.symbol, "sent_at_epoch": bar.t + M1_S,
        "server_gmt_offset_s": snapshot.server_gmt_offset_s, "bar_open_epoch": bar.t,
        "bar": [bar.t, bar.o, bar.h, bar.l, bar.c, bar.tv, bar.spr],
        "account": snapshot.account.model_dump(mode="json"), "quote": quote,
        "ticks": {**snapshot.ticks.model_dump(mode="json"), "window_s": M1_S,
                  "quote_count": max(0, bar.tv)},
        "positions": [], "pending_orders": [],
        "day": snapshot.day.model_dump(mode="json"),
        "ea_state": snapshot.ea_state.model_dump(mode="json")}
    return MinuteSnapshot.model_validate(document)


def _one_minute(base: MarketContext, snapshot: V6Snapshot, bar: Bar, history: tuple[Bar, ...],
                settings: V6Settings) -> tuple[str, float]:
    """(first failed gate or "", build time in ms) of one closed M1 bar."""
    started = time.perf_counter()
    minute = _minute(snapshot, bar)
    close = bar.t + M1_S
    now = float(close + RECEIVE_DELAY_S)
    context = minute_context(base, minute, bars_closed_by(history, close, M1_S)[-60:],
                             cycle_id=f"m-{bar.t:016x}", received_at=now,
                             calendar=base.calendar, settings=settings)
    breakers = healthy_breakers(context, settings)
    gates = evaluate_gates(context, context.calendar, RuntimeGateState(warmed_up=True),
                           minute_settings(settings), breakers, now)
    failed = first_failure(gates)
    if failed is None:
        build_packet(PacketRequest(
            context=context, gates=gates, offered=(), baseline=DeskViews(),
            remaining_loss_usd=breakers.remaining_loss_usd, session_id=REPLAY_SESSION_ID,
            armed=True, now=now, kind="m1"), settings)
    return ("" if failed is None else failed.code), (time.perf_counter() - started) * MS


def _percentile(values: Sequence[float], share: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(share * len(ordered)))], 2)


def run_minute_replay(bars: BarSet, stored_events: Sequence[CalendarEventBlock],
                      settings: V6Settings, window: ReplayWindow = ReplayWindow()
                      ) -> MinuteTally:
    m1 = tuple(bars.bars("M1"))
    blocked: Counter[str] = Counter()
    per_day: Counter[str] = Counter()
    timings: list[float] = []
    for bar in bars.bars("M15"):
        close = bar.t + M15_S
        built = bar_context(bar, bars, stored_events, settings, Exposure()) \
            if window.contains(close) else None
        if built is None:
            continue
        snapshot, base, _ = built
        for minute_bar in (b for b in m1 if close <= b.t < close + MINUTES_PER_M15 * M1_S):
            failed, spent = _one_minute(base, snapshot, minute_bar, m1, settings)
            timings.append(spent)
            if failed:
                blocked[failed] += 1
            else:
                day = datetime.fromtimestamp(minute_bar.t, tz=timezone.utc).date().isoformat()
                per_day[day] += 1
    return MinuteTally(minutes=len(timings), packets=sum(per_day.values()),
                       blocked_by=dict(blocked), per_day=dict(per_day),
                       build_ms_p50=_percentile(timings, 0.50),
                       build_ms_p95=_percentile(timings, 0.95))
```

(Sesuaikan nama `REPLAY_SESSION_ID`, `healthy_breakers` dan `first_failure` dengan yang diekspor `evaluate.py`/`synth.py`/`risk.gates`; semuanya sudah dipakai `evaluate_bar`. `bar.spr` adalah spread bar dalam point. Bila `MinuteSnapshot.model_validate` menolak tuple di mode strict untuk `bar`, pakai `model_validate_json(json.dumps(document))`.)

- [ ] **Step 5: `v6_replay.py --minutes`**

Tambah argumen `--minutes` ("also replay the minute rhythm: m1 packets per day and the adapter time per minute"). Bila diberikan, jalankan `run_minute_replay(bars, stored_events, settings, window)` setelah replay M15, cetak bagian markdown:

```text
## Minute rhythm
- minutes evaluated: <minutes>, m1 packets: <packets>
- m1 packets per day: <YYYY-MM-DD n, ...>
- first failed gate: <GATE n, ...>
- adapter time per minute: p50 <x> ms, p95 <y> ms
```

dan tambahkan `"minutes": asdict(tally)` ke output `--json`.

- [ ] **Step 6: Jalankan tes replay**

Run: `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_replay_minutes.py tests/v6/test_v6_replay.py`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add adapter/scripts/v6replay adapter/scripts/v6_replay.py adapter/tests/v6/test_replay_minutes.py
git commit -m "feat: replay the V6 minute rhythm (m1 packets per day, time per minute)"
```

---
## Task 15: Dokumen, skill dan `AGENTS.md`

**Files:**
- Modify: `docs/v6-operator.md`, `docs/v6-wire-contract.md`, `docs/v6-runbook.md`
- Modify: `.agents/skills/v6-trading/SKILL.md` lalu salin ke `.claude/skills/v6-trading/SKILL.md`
- Modify: `AGENTS.md` (tetap < 12.000 karakter)
- Modify: `docs/superpowers/specs/2026-09-17-v6-m1-dynamic-management-design.md` (status tahap B dan penyimpangannya)
- Test: `adapter/tests/v6/test_v6_operator_docs.py`, `adapter/tests/v6/test_agent_harness_parity.py`

**Isi yang wajib ada di `docs/v6-operator.md`:**
- Ringkasan Indonesia dan bagian 1: paket `m15` setiap 15 menit dan paket `m1` setiap close M1 lain (24 jam, dijeda saat rollover dan saat sesi tidak armed).
- Bagian 2 (perintah): `submit --quick` (tanpa file; HOLD atau KEEP; pada `m15` bias terakhir dibawa dengan `carried: true`).
- Bagian 3 (loop): satu loop untuk kedua jenis paket; paket `m1` dijawab dalam 45 detik, `--quick` bila tidak ada perubahan; laporan satu baris hanya untuk aksi (entry, modifikasi, cut, pengisian, penutupan) dan untuk setiap paket `m15`; chat baru saat konteks berat, sesi tidak dihentikan.
- Bagian 4 (timing): tenggat `m1` = close + `V6_M1_DEADLINE_S` (50 s); aturan antrean (paket `m15` menggantikan `m1` yang terbuka; tidak ada `m1` selama `m15` terbuka; `m1` tak terjawab diganti yang berikutnya); snapshot menit basi setelah `V6_MINUTE_STALE_S`.
- Bagian baru **5.12 Paket `m1`: timing M1 dan view warisan**: `views` dan `m15_bias` boleh null; view warisan (keputusan `m15` terakhir yang diterima, atau baseline siklus itu); veto M15 memblokir entry `m1` sampai paket `m15` berikutnya; ENTER `m1` = TAKE Price Action pada conviction minimum; kapan bertindak di `m1` (timing entry di level M15, cut saat struktur M1 patah melawan posisi, geser SL ke higher low/lower high M1 yang terkonfirmasi) dan kapan `--quick`; sinyal M1 melawan bias M15 adalah alasan menunggu, bukan membalik.
- Bagian 5.5 (rebuttal) dan paragraf keputusan v1/v2 di 5.6 dihapus; tabel penolakan: `DECISION_SCHEMA` untuk v1/v2 ("retired").
- Bagian 6: contoh ringkasan `wait` untuk paket `m1` (tiga baris), contoh keputusan `m1` ENTER, contoh `--quick`.

**Isi yang wajib ada di skill (sama untuk ketiga agen):**
- "Deciding a packet" bercabang menurut `packet_kind`:
  - `m1`: baca ringkasan tiga baris; tidak ada perubahan → `OP submit --agent <AGENT> --quick`; selain itu `OP template`, edit, `OP submit` dalam 45 detik; laporan satu baris hanya bila ada aksi;
  - `m15`: seperti tahap A (baca rubrik bila perlu, analisis, template, edit, submit), dengan laporan satu baris.
- Kalimat penutup: sesi berjalan 24 jam; mulai chat baru saat konteks berat lalu ucapkan "Mulai trading skrg" (sesi yang sama dilanjutkan).

**`AGENTS.md`:** langkah 3 loop menyebut paket `m1` setiap menit (jawab dalam 45 detik, `--quick` bila tidak ada perubahan) dan paket `m15` setiap 15 menit. Periksa panjang dengan
`python -c "print(len(open('AGENTS.md', encoding='utf-8').read()))"` (< 12.000).

**`docs/v6-wire-contract.md`:** bagian 6.6 lengkap (Task 12 menambahkan versi minimal), paket `m1` di bagian 9 (`packet_kind` m1, `m1_state`, bar M1 60, tenggat), keputusan `m1` (view dan bias opsional), dan penolakan v1/v2.

**`docs/v6-runbook.md`:**
- `.env`: `V6_MINUTE_PACKETS`, `V6_M1_DEADLINE_S`, `V6_MINUTE_STALE_S`.
- Bagian 2: EA 6.3.0, input `InpMinuteSnapshots` (true) dan `InpMinuteTimeoutMs` (800), baris log `V6 EA 6.3.0 started`, dan pengingat bahwa EA harus dipasang ulang setelah kompilasi.
- Bagian 3: beban paket menit (sekitar 1.300 paket per hari; `--quick` untuk menit tanpa perubahan).
- Troubleshooting: alasan lewati di dashboard "Minute packets" (`NOT_ARMED`, `M15_PENDING`, `M15_CLOSE`, `ROLLOVER`, `NO_M15_CONTEXT`, `INTENT_ACTIVE`, `GATES:<kode>`), dan `v6_minute_cycles`.
- Bagian 6: drill tahap B (paket `m1` datang tiap menit; `--quick`; ENTER dari paket `m1`; paket `m15` menggantikan `m1` yang terbuka; jeda saat rollover; adapter mati → EA hanya mencatat kegagalan menit sekali per 15 menit) dan kriteria selesai (dashboard: p95 tier 0 < 300 ms, ≥ 90% paket `m1` terjawab dalam dua jam pasar ramai; invarian ledger bersih).

**Spec:** bagian "Status dan penyimpangan saat implementasi" mendapat status tahap B dan penyimpangan di kepala rencana ini.

- [ ] **Step 1: Tulis perubahan dokumen di atas**
- [ ] **Step 2: Salin skill**

```bash
cp .agents/skills/v6-trading/SKILL.md .claude/skills/v6-trading/SKILL.md
```

- [ ] **Step 3: Jalankan tes dokumen, paritas harness dan paritas EA**

Run (dari `adapter/`): `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/v6/test_v6_operator_docs.py tests/v6/test_agent_harness_parity.py tests/v6/test_ea_safety_parity.py tests/v6/test_golden_contract.py`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add docs AGENTS.md .agents/skills/v6-trading/SKILL.md .claude/skills/v6-trading/SKILL.md adapter/tests/v6
git commit -m "docs: describe V6 phase B minute packets, submit --quick and EA 6.3.0"
```

---

## Task 16: Verifikasi akhir dan kriteria selesai tahap B

- [ ] **Step 1: Seluruh suite dengan coverage**

Run (dari `adapter/`): `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --cov=app/v6 --cov=scripts --cov-report=term-missing`
Expected: semua lulus; total ≥ 80%; `app/v6/risk/*`, `deliberation/protocol.py`, `deliberation/management.py`, `deliberation/plan_rules.py` 100%; modul baru tahap B (`ledger_minutes`, `minute_context`, `minute_flow`, `minute_packet`, `decision_minute`, `runtime/minute_worker`, `dashboard_minutes`) ≥ 90%.

- [ ] **Step 2: Ukuran file dan fungsi**

```bash
git diff --name-only --diff-filter=d <commit spec tahap B>^..HEAD -- 'adapter/app/*.py' 'adapter/scripts/*.py' | xargs .venv/Scripts/python.exe <scratchpad>/sizecheck.py
wc -l ea/QlipV6_XAUUSD.mq5 ea/QlipV6/*.mqh | sort -n | tail -3
```

Expected: tidak ada file Python > 400 baris atau fungsi ≥ 50 baris; file utama EA ≤ 300, `.mqh` ≤ 400.

- [ ] **Step 3: Pindai rahasia**

```bash
git diff main...HEAD | grep -iE "^\+.*(token|secret|hmac_key|password)\s*[:=]\s*['\"][A-Za-z0-9]{16,}" || echo clean
```

Expected: `clean`; tidak ada `.env` atau file kunci yang dilacak git.

- [ ] **Step 4: EA terpasang**

`compile_v6.sh` → `Result: 0 errors, 0 warnings`; file di `MQL5\Experts` identik dengan repo. Minta pengguna memasang ulang EA (atau me-restart MT5) dan memeriksa `V6 EA 6.3.0 started` di `MQL5\Logs`.

- [ ] **Step 5: Uji hidup (dengan izin pengguna)**

1. Pengguna menjalankan adapter dengan commit ini, lalu memasang ulang EA (backfill terkirim saat adapter hidup).
2. `/v6/status` → `runtime.minutes.processed` naik setiap menit; sebelum sesi armed, baris menit `SKIPPED/NOT_ARMED`.
3. "Mulai trading skrg" → paket `m1` datang setiap menit di luar close M15; `--quick` diterima; paket `m15` menggantikan `m1` yang terbuka.
4. Dashboard "Minute packets" menunjukkan persentase terjawab dan p95 tier 0.

- [ ] **Step 6: Kriteria selesai tahap B (spec bagian 9)**

- Satu hari penuh tanpa antrean menumpuk di adapter: p95 `tier0_ms` di `v6_minute_cycles` < 300 ms.
- Minimal 90% paket `m1` terjawab dalam tenggat selama dua jam pasar ramai (dashboard, jendela 2 jam).
- Pemeriksaan invarian ledger bersih: tidak ada `v6_actions` APPLIED dengan SL lebih lebar; setiap intent aktif punya `sl > 0`; tidak ada dua intent aktif; tidak ada entry saat ada posisi atau pending; paling banyak 8 entry per hari trading.

- [ ] **Step 7: Laporkan ke pengguna**

Hasil tes dan coverage, EA 6.3.0 (perlu dipasang ulang), hasil uji hidup, kriteria yang sudah dan belum terpenuhi, dan beban token paket menit (perkiraan jumlah paket per hari dari replay `--minutes`).

---

## Self-review

**Cakupan spec:**

| Spec | Task |
|---|---|
| §1 ritme `m1`, aturan antrean, jeda rollover dan sesi tak armed | 5, 8, 9 |
| §2.1 `views`/`m15_bias` opsional di `m1` | 6, 7 |
| §2.4 bias berlaku sampai `m15` berikutnya, tampil di `m1` | 6 (`last_bias`), 7 |
| §2.5 view warisan dan veto M15 | 7, 8 |
| §2.6 jalur cepat `--quick` dengan `carried: true` | 7, 11 |
| §2.7 kode penolakan (`DECISION_KIND` untuk jenis salah) | 7, 13 |
| §3.1 snapshot menit | 2, 3, 12 |
| §4.1 `POST /v6/minute` | 3 |
| §4.2 `MinuteInbox`, worker menit, id `m-`, penarikan oleh M15, tenggat per paket | 3, 5, 8, 9 |
| §4.5 `V6_MINUTE_PACKETS`, `V6_M1_DEADLINE_S`, `V6_MINUTE_STALE_S` | 1 |
| §4.6 `v6_minute_cycles` dipangkas 3 hari; dashboard persentase terjawab | 4, 9, 10 |
| §5 harness: `wait` jenis paket dan ringkasan `m1`, `template`, `--quick`, skill, `AGENTS.md`, dokumen | 11, 15 |
| §6 paket `m1` tak terjawab = tidak ada perubahan; snapshot menit hilang atau basi → dilewati | 8, 9 |
| §8 tes: `minute_packet` hanya bar tutup, `m1_state` sintetis, dua tenggat, terbaru menang, penarikan M15, siklus menit, integrasi rute; replay | 3, 5, 6, 8, 9, 14 |
| §9 pensiun keputusan v2; kriteria selesai | 13, 16 |

**Pemindaian placeholder:** setiap langkah kode memuat kodenya; catatan "bila nama helper berbeda" hanya menunjuk ke helper yang sudah ada di file yang disebut (fixture tes), bukan ke kode yang belum ditulis.

**Konsistensi tipe:** `MinuteSnapshot.bar_close_epoch`/`to_bar()` (Task 2) dipakai di Task 3, 8, 9, 14; `MinuteItem(cycle_id, minute, received_at)` (Task 3) di Task 9; `MinuteRow`/`MinuteStats` (Task 4) di Task 9, 10; `OperatorQueue.closed()`/`pending_kind()` (Task 5) di Task 9; `PacketRequest.kind` dan `minute_state` (Task 6) di Task 8, 14; `validate_minute` (Task 7) dipanggil dari `validate_decision`; `MinuteBase`/`MinuteRun`/`run_minute` (Task 8) di Task 9 dan tes; `CycleRequest.minute`/`packet_kind`/`as_of_epoch` (Task 8) di `engine._deadline`, `operator_flow` dan Task 9.
