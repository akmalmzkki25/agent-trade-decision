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
