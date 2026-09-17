"""LedgerCycles: cycles, views, candidates, breakers and sessions on SQLite."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from app.v6.cycle_types import HoldReason
from app.v6.ledger_cycles import LedgerCycles, encode_json
from app.v6.ledger_v6 import LedgerV6

from .cycle_fixtures_v6 import CANDIDATE_ID, CYCLE_ID, enter_result, hold_result
from .payloads_v6 import BAR_OPEN, M15

CREATED = float(BAR_OPEN + M15 + 2)
TABLES = ("v6_cycles", "v6_agent_views", "v6_candidates", "v6_breaker_state", "v6_sessions")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "cycles.db"


@pytest.fixture
def ledger(db_path: Path) -> Iterator[LedgerCycles]:
    led = LedgerCycles(db_path)
    yield led
    led.close()


def _table_names(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}


# --- schema -------------------------------------------------------------------


def test_schema_is_created_and_repeatable(db_path: Path) -> None:
    LedgerCycles(db_path).close()
    second = LedgerCycles(db_path)
    second.close()
    second.close()  # idempotent

    assert set(TABLES) <= _table_names(db_path)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_shares_the_file_with_ledger_v6(db_path: Path) -> None:
    v6 = LedgerV6(db_path)
    cycles = LedgerCycles(db_path)
    try:
        assert cycles.record_cycle(hold_result(), CREATED)
        assert {"v6_bars", *TABLES} <= _table_names(db_path)
    finally:
        cycles.close()
        v6.close()


def test_open_failure_closes_the_connection(tmp_path: Path) -> None:
    garbage = tmp_path / "not_a_db.db"
    garbage.write_bytes(b"this is not an sqlite database" * 100)

    with pytest.raises(sqlite3.DatabaseError):
        LedgerCycles(garbage)


# --- cycles -------------------------------------------------------------------


def test_record_and_read_back_a_hold_cycle(ledger: LedgerCycles) -> None:
    assert ledger.record_cycle(hold_result(), CREATED)

    record = ledger.get_cycle(CYCLE_ID)

    assert record is not None
    assert (record.status, record.hold_reason, record.provider_status) == \
        ("HOLD", "APP-V6-GATE", "skipped")
    assert record.total_ms == 250 and record.created_at == CREATED
    assert record.summary()["gates"][0]["code"] == "SPREAD"
    assert ledger.views_for_cycle(CYCLE_ID) == ()
    assert ledger.get_cycle("missing") is None


def test_record_enter_cycle_writes_views_and_candidates(ledger: LedgerCycles) -> None:
    assert ledger.record_cycle(enter_result(), CREATED)

    record = ledger.get_cycle(CYCLE_ID)
    views = ledger.views_for_cycle(CYCLE_ID)
    candidates = ledger.candidates_for_cycle(CYCLE_ID)

    assert record is not None and record.hold_reason is None
    assert record.session_id == "abc123" and record.backend == "operator"
    assert "view_records" not in record.summary()
    assert record.summary()["shadow_intent"]["lots"] == 0.01
    assert [(v.role, v.source, v.error_code) for v in views] == [
        ("price_action", "rules", ""), ("news_risk", "rules", ""),
        ("chief", "operator", "PROVIDER_TIMEOUT")]
    assert views[0].view()["ranked"][0]["candidate_id"] == CANDIDATE_ID
    assert views[2].view() is None and views[2].tokens_in == 900
    assert len(candidates) == 1
    row = candidates[0]
    assert (row.setup, row.side, row.verdict, row.label_status) == \
        ("displacement", "buy", "chosen", "pending")
    assert (row.stop, row.target, row.bar_t) == (4289.8, 4320.4, BAR_OPEN)
    assert row.features() == {"body_ratio": 0.7, "range_atr": 1.8}


def test_duplicate_cycle_is_ignored(ledger: LedgerCycles) -> None:
    assert ledger.record_cycle(enter_result(), CREATED)
    assert not ledger.record_cycle(enter_result(), CREATED + 1)

    assert len(ledger.views_for_cycle(CYCLE_ID)) == 3


def test_record_cycle_is_atomic(ledger: LedgerCycles) -> None:
    bad = enter_result()
    item = bad.candidates[0]
    secret = replace(item, candidate=replace(item.candidate, features={"api_key": 1.0}))

    with pytest.raises(ValueError, match="credential"):
        ledger.record_cycle(replace(bad, candidates=(secret,)), CREATED)
    assert ledger.get_cycle(CYCLE_ID) is None


def test_failed_child_insert_rolls_the_cycle_back(ledger: LedgerCycles) -> None:
    result = enter_result()
    broken = replace(result.view_records[0], model=None)  # violates NOT NULL in SQL

    with pytest.raises(sqlite3.IntegrityError):
        ledger.record_cycle(replace(result, view_records=(broken,)), CREATED)
    assert ledger.get_cycle(CYCLE_ID) is None
    assert ledger.record_cycle(result, CREATED)


def test_recent_cycles_newest_first_with_paging(ledger: LedgerCycles) -> None:
    for i in range(3):
        ledger.record_cycle(hold_result(f"c{i}", bar_open=BAR_OPEN + i * M15), CREATED + i)

    newest = ledger.recent_cycles(limit=2)
    older = ledger.recent_cycles(limit=2, before_bar_epoch=newest[-1].bar_open_epoch)

    assert [c.cycle_id for c in newest] == ["c2", "c1"]
    assert [c.cycle_id for c in older] == ["c0"]
    with pytest.raises(ValueError):
        ledger.recent_cycles(limit=0)


def test_recent_cycles_with_views(ledger: LedgerCycles) -> None:
    assert ledger.recent_cycles_with_views() == ()
    ledger.record_cycle(hold_result("h1"), CREATED)
    ledger.record_cycle(enter_result("e1", bar_open=BAR_OPEN + M15,
                                     candidate_ids=("cand-a",)), CREATED)

    rows = ledger.recent_cycles_with_views(limit=5)

    assert [(r.cycle.cycle_id, len(r.views)) for r in rows] == [("e1", 3), ("h1", 0)]


def test_cycle_summary_and_histogram(ledger: LedgerCycles) -> None:
    ledger.record_cycle(hold_result("a", reason=HoldReason.GATE), CREATED)
    ledger.record_cycle(hold_result("b", reason=HoldReason.GATE, bar_open=BAR_OPEN + M15), CREATED)
    ledger.record_cycle(hold_result("c", reason=HoldReason.LATE, status="LATE",
                                    bar_open=BAR_OPEN + 2 * M15), CREATED)
    ledger.record_cycle(enter_result("d", bar_open=BAR_OPEN + 3 * M15,
                                     candidate_ids=("cand-d",)), CREATED)
    published = replace(enter_result("e", bar_open=BAR_OPEN + 4 * M15,
                                     candidate_ids=("cand-e",)), status="ENTER")
    ledger.record_cycle(published, CREATED)

    summary = ledger.cycle_summary(BAR_OPEN)
    window = ledger.cycle_summary(BAR_OPEN + M15, BAR_OPEN + 3 * M15)

    assert summary.total == 5 and (summary.shadow_entries, summary.entries) == (1, 1)
    assert dict(summary.by_status) == {"HOLD": 2, "LATE": 1, "ENTER_SHADOW": 1, "ENTER": 1}
    assert dict(ledger.hold_reason_histogram(BAR_OPEN)) == {"APP-V6-GATE": 2, "APP-V6-LATE": 1}
    assert window.total == 2
    with pytest.raises(TypeError):
        summary.by_status["HOLD"] = 0  # type: ignore[index]


# --- candidates and labels ----------------------------------------------------


def test_pending_candidates_respect_available_from(ledger: LedgerCycles) -> None:
    ledger.record_cycle(enter_result(candidate_ids=("a", "b")), CREATED)
    available = ledger.candidates_for_cycle(CYCLE_ID)[0].available_from

    assert ledger.pending_candidates(available - 1) == ()
    assert [c.candidate_id for c in ledger.pending_candidates(available)] == ["a", "b"]
    assert len(ledger.pending_candidates(available, limit=1)) == 1


def test_write_label_once(ledger: LedgerCycles) -> None:
    ledger.record_cycle(enter_result(candidate_ids=("a", "b")), CREATED)

    assert ledger.write_label("a", label_status="labeled", outcome="tp", outcome_r=1.95,
                              labeled_at=CREATED + 9000)
    assert not ledger.write_label("a", label_status="labeled", outcome="sl", outcome_r=-1.0,
                                  labeled_at=CREATED + 9001)
    assert ledger.write_label("b", label_status="unfillable", outcome="unfilled",
                              outcome_r=None, labeled_at=CREATED + 9000)
    assert not ledger.write_label("zzz", label_status="labeled", outcome="time",
                                  outcome_r=0.1, labeled_at=CREATED)

    rows = {c.candidate_id: c for c in ledger.candidates_for_cycle(CYCLE_ID)}
    assert (rows["a"].label_status, rows["a"].outcome, rows["a"].outcome_r) == \
        ("labeled", "tp", 1.95)
    assert rows["b"].label_status == "unfillable" and rows["b"].outcome_r is None
    assert ledger.pending_candidates(2**40) == ()


@pytest.mark.parametrize(
    ("status", "outcome", "outcome_r"),
    [
        ("labeled", "unfilled", 0.0),
        ("labeled", "tp", None),
        ("labeled", "sl", float("nan")),
        ("unfillable", "tp", None),
        ("unfillable", "unfilled", 0.0),
        ("pending", "tp", 1.0),
    ],
)
def test_write_label_refuses_inconsistent_labels(
    ledger: LedgerCycles, status: str, outcome: str, outcome_r: float | None
) -> None:
    with pytest.raises(ValueError):
        ledger.write_label("a", label_status=status, outcome=outcome,  # type: ignore[arg-type]
                           outcome_r=outcome_r, labeled_at=CREATED)


def test_candidate_label_stats(ledger: LedgerCycles) -> None:
    ledger.record_cycle(enter_result(candidate_ids=("a", "b", "c")), CREATED)
    ledger.write_label("a", label_status="labeled", outcome="tp", outcome_r=2.0, labeled_at=1.0)
    ledger.write_label("b", label_status="labeled", outcome="tp", outcome_r=1.0, labeled_at=1.0)

    stats = ledger.candidate_label_stats(BAR_OPEN)

    assert [(s.label_status, s.outcome, s.count, s.mean_r) for s in stats] == [
        ("labeled", "tp", 2, 1.5), ("pending", None, 1, None)]
    assert ledger.candidate_label_stats(BAR_OPEN + 1) == ()


def test_candidate_with_duplicate_id_in_a_later_cycle_is_ignored(ledger: LedgerCycles) -> None:
    ledger.record_cycle(enter_result("first"), CREATED)
    ledger.record_cycle(enter_result("second"), CREATED)

    assert len(ledger.candidates_for_cycle("first")) == 1
    assert ledger.candidates_for_cycle("second") == ()


# --- JSON encoding ------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [{"Authorization": "x"}, {"nested": {"operator_token": "x"}}, {"x": float("nan")},
     {"x": object()}, {"x": "y" * 50}],
)
def test_encode_json_refuses_bad_documents(value: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        encode_json(value, max_chars=40)


def test_encode_json_is_canonical() -> None:
    assert encode_json({"b": 1, "a": [1.5, None]}, max_chars=100) == '{"a":[1.5,null],"b":1}'
