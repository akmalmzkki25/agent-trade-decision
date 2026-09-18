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
