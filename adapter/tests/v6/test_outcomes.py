"""
V6 outcomes: basket results linked to intents, cycles, accounts and labels.

The journal fixture puts the V1 ledger (basket_results) and both V6 ledgers on
one SQLite file, as in production. `seed_trade` walks one intent through its
whole life; test_realised_pnl and the dashboard tests reuse it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Final
from unittest.mock import patch

import pytest

from app import ledger as v1_ledger
from app.ledger import Ledger
from app.models import BasketResultEvent
from app.v6.cycle_types import candidate_id_for
from app.v6.learning.outcomes import (
    BasketResult, CounterfactualLabel, OutcomeRecord, outcome_for_event, outcome_stats,
    r_multiple, utc_epoch,
)
from app.v6.ledger_cycles import LedgerCycles
from app.v6.ledger_intents import IntentRecord, IntentUpdate, NewIntent
from app.v6.ledger_v6 import LedgerV6, SnapshotRecord
from app.v6.schemas.intent import basket_id_for

from .cycle_fixtures_v6 import enter_result
from .payloads_v6 import BAR_OPEN, M15

LOGIN: Final[str] = "12345"
OTHER_LOGIN: Final[str] = "67890"
FIRST: Final[str] = "k7w2m4pq3xza"
SECOND: Final[str] = "b5n6r7t2vw3y"
THIRD: Final[str] = "c2d3e4f5g6h7"
RISK: Final[float] = 10.4
DAY: Final[int] = 86_400
CLOSE: Final[int] = BAR_OPEN + 3 * M15     # 12:45 UTC, Wednesday 2026-09-16


def iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def event(intent_id: str = FIRST, *, net_pnl: float = 20.8, closed_at: int = CLOSE,
          side: str = "buy", version: str = "v6", basket_id: str = "") -> BasketResultEvent:
    return BasketResultEvent(
        schema_version="basket-result-event.v1", version=version,
        basket_id=basket_id or basket_id_for("XAUUSD", intent_id), symbol="XAUUSD", side=side,
        opened_at_utc=iso(closed_at - 1800), closed_at_utc=iso(closed_at),
        close_reason="TP" if net_pnl > 0 else "SL", bursts=1, positions=1,
        gross_profit=max(net_pnl, 0.0), gross_loss=min(net_pnl, 0.0), net_pnl=net_pnl,
        max_floating_dd=-5.2, avg_slippage_points=3.0, avg_spread_points=24.0,
        decision_latency_ms=900, equity_at_open=2000.0, equity_at_close=2000.0 + net_pnl)


def result(intent_id: str = FIRST, *, net_pnl: float = 20.8, closed_at: int = CLOSE,
           side: str = "buy") -> BasketResult:
    return BasketResult.from_event(event(intent_id, net_pnl=net_pnl, closed_at=closed_at,
                                         side=side), iso(closed_at + 2))


def intent_record(intent_id: str = FIRST, *, cycle_id: str = "c-1", side: str = "buy",
                  risk_usd: float = RISK, **changes: Any) -> IntentRecord:
    sign = 1 if side == "buy" else -1
    values: dict[str, Any] = dict(
        intent_id=intent_id, cycle_id=cycle_id, session_id="a1b2c3d4e5f6", agent="codex",
        source="operator", status="CLOSED", side=side,
        order_type="BUY_LIMIT" if side == "buy" else "SELL_LIMIT", entry=4300.0,
        sl=4300.0 - sign * 10.2, tp=4300.0 + sign * 20.4, lots=0.01, risk_usd=risk_usd,
        valid_until_epoch=BAR_OPEN + 1020, pending_expiry_epoch=BAR_OPEN + 2700,
        time_barrier_s=7200, created_at=float(BAR_OPEN + 905))
    return IntentRecord(**{**values, **changes})


def label(cycle_id: str = "c-1", *, status: str = "labeled", outcome: str | None = "tp",
          r: float | None = 1.9) -> CounterfactualLabel:
    return CounterfactualLabel(
        candidate_id=candidate_id_for("displacement", "buy", BAR_OPEN), cycle_id=cycle_id,
        setup="displacement", verdict="chosen", label_status=status, outcome=outcome,
        outcome_r=r)


def linked(intent_id: str = FIRST, net_pnl: float = 20.8, login: str | None = LOGIN,
           closed_at: int = CLOSE, label_r: float | None = 1.9) -> OutcomeRecord:
    tag = None if label_r is None else label(f"c-{intent_id}", r=label_r)
    return OutcomeRecord(result(intent_id, net_pnl=net_pnl, closed_at=closed_at),
                         intent_record(intent_id, cycle_id=f"c-{intent_id}"), tag, login)


def unlinked(net_pnl: float, closed_at: int = CLOSE) -> OutcomeRecord:
    return OutcomeRecord(result(THIRD, net_pnl=net_pnl, closed_at=closed_at))


# --- journal (one SQLite file, as in production) ----------------------------------------
@dataclass(frozen=True)
class Journal:
    path: Path
    v1: Ledger
    cycles: LedgerCycles
    v6: LedgerV6


@pytest.fixture
def journal(tmp_path: Path) -> Iterator[Journal]:
    path = tmp_path / "journal.db"
    v6, cycles, v1 = LedgerV6(path), LedgerCycles(path), Ledger(str(path))
    yield Journal(path, v1, cycles, v6)
    v1.conn.close()
    cycles.close()
    v6.close()


def store_result(journal: Journal, basket: BasketResultEvent, received_at: int) -> None:
    with patch.object(v1_ledger, "now_utc", return_value=iso(received_at)):
        journal.v1.write_basket_result(basket)


def _new_intent(intent_id: str, cycle_id: str, bar_open: int) -> NewIntent:
    decided = bar_open + M15 + 5
    return NewIntent(
        intent_id=intent_id, cycle_id=cycle_id, session_id="a1b2c3d4e5f6", agent="claude_code",
        source="operator", side="buy", order_type="BUY_LIMIT", entry=4300.0, sl=4289.8,
        tp=4320.4, lots=0.01, risk_usd=RISK, valid_until_epoch=decided + 120,
        pending_expiry_epoch=bar_open + M15 + 1800, time_barrier_s=7200,
        created_at=float(decided))


def seed_trade(journal: Journal, intent_id: str, *, bar_open: int = BAR_OPEN,
               login: str | None = LOGIN, net_pnl: float = 20.8,
               label_r: float | None = 1.9, closed_at: int | None = None) -> str:
    """Snapshot of `login`, ENTER cycle with its chosen candidate (and label), the intent
    walked to CLOSED, and its stored basket result. Returns the basket id."""
    cycle_id = f"c-{intent_id}"
    candidate_id = candidate_id_for("displacement", "buy", bar_open)
    closed = bar_open + 3 * M15 if closed_at is None else closed_at
    if login is not None:
        assert journal.v6.insert_snapshot(SnapshotRecord(
            f"snap-{cycle_id}", float(bar_open + M15), bar_open, login, "DEMO", "Broker-Demo",
            2000.0, 2000.0, 20, 0.0, "0" * 64, "{}"))
    cycle = enter_result(cycle_id, bar_open=bar_open, candidate_ids=(candidate_id,))
    assert journal.cycles.record_cycle(replace(cycle, status="ENTER"), float(bar_open + M15))
    if label_r is not None:
        assert journal.cycles.write_label(
            candidate_id, label_status="labeled", outcome="tp" if label_r > 0 else "sl",
            outcome_r=label_r, labeled_at=float(closed))
    intents = journal.cycles.intents
    intents.insert(_new_intent(intent_id, cycle_id, bar_open))
    for status in ("DELIVERED", "FILLED"):
        intents.transition(intent_id, status, at=float(bar_open + M15 + 10))
    basket_id = basket_id_for("XAUUSD", intent_id)
    intents.transition(intent_id, "CLOSED", at=float(closed),
                       update=IntentUpdate(outcome_pnl=net_pnl, basket_id=basket_id))
    store_result(journal, event(intent_id, net_pnl=net_pnl, closed_at=closed), closed + 2)
    return basket_id


# --- basket results ------------------------------------------------------------------------
def test_basket_results_are_read_from_v6_events_only() -> None:
    read = result()
    assert (read.intent_id, read.closed_epoch, read.opened_epoch) == (FIRST, CLOSE, CLOSE - 1800)
    assert read.received_at_utc == iso(CLOSE + 2)
    with pytest.raises(ValueError, match="not a V6 basket result"):
        BasketResult.from_event(event(version="v5"))
    with pytest.raises(ValueError, match="non-finite"):
        replace(read, net_pnl=float("inf"))


@pytest.mark.parametrize(("closed", "received", "expected"), [
    ("2026-09-16T12:45:00Z", "", CLOSE),
    ("2026-09-16T15:45:00+03:00", "", CLOSE),
    ("2026-09-16T12:45:00", "", CLOSE),
    ("16/09/2026", "2026-09-16T12:45:00.250000+00:00", CLOSE),
    ("", "", None),
])
def test_close_times_read_as_utc_and_fall_back_to_the_receipt(
        closed: str, received: str, expected: int | None) -> None:
    read = replace(result(), closed_at_utc=closed, received_at_utc=received)
    assert read.closed_epoch == expected
    assert utc_epoch("0001-01-01T00:00:00+01:00") is not None


@pytest.mark.parametrize(("net", "risk", "expected"), [
    (5.2, 10.4, Decimal("0.5")), (-10.4, 10.4, Decimal(-1)), (1.0, 0.0, None),
    (None, 10.0, None), (1.0, None, None), (float("nan"), 10.0, None),
    (1.0, float("inf"), None), (1.0, -2.0, None),
])
def test_r_multiple_needs_finite_money_and_a_positive_risk(
        net: float | None, risk: float | None, expected: Decimal | None) -> None:
    assert r_multiple(net, risk) == expected


# --- outcome records ------------------------------------------------------------------------
def test_an_outcome_links_the_intent_the_label_and_the_execution_gap() -> None:
    record = linked(net_pnl=20.8)

    assert (record.kind, record.r_multiple, record.label_r) == ("win", Decimal(2), Decimal("1.9"))
    assert record.r_gap == Decimal("0.1") and record.mae_r == Decimal("-0.5")
    assert record.net_pnl == Decimal("20.8") and not record.side_mismatch
    document = record.to_dict()
    assert json.loads(json.dumps(document)) == document
    assert (document["intent_id"], document["agent"], document["login"]) == (FIRST, "codex", LOGIN)
    assert (document["r_multiple"], document["r_gap"], document["mae_r"]) == (2.0, 0.1, -0.5)
    assert document["label"]["outcome"] == "tp" and document["label"]["r"] == 1.9


def test_an_outcome_refuses_links_that_belong_elsewhere() -> None:
    with pytest.raises(ValueError, match="intent does not belong"):
        OutcomeRecord(result(FIRST), intent_record(SECOND))
    with pytest.raises(ValueError, match="label does not belong"):
        OutcomeRecord(result(FIRST), intent_record(FIRST, cycle_id="c-1"), label("c-2"))
    with pytest.raises(ValueError, match="label does not belong"):
        OutcomeRecord(result(FIRST), None, label())


@pytest.mark.parametrize(("status", "outcome", "r"), [
    ("pending", None, None), ("unfillable", "unfilled", None), ("labeled", "tp", None),
])
def test_unresolved_labels_carry_no_r(status: str, outcome: str | None, r: float | None) -> None:
    record = OutcomeRecord(result(), intent_record(), label(status=status, outcome=outcome, r=r))
    assert record.label_r is None and record.r_gap is None
    assert record.to_dict()["label"]["r"] is None


def test_unlinked_and_mismatched_outcomes() -> None:
    orphan = unlinked(-5.0)
    assert (orphan.kind, orphan.r_multiple, orphan.mae_r) == ("loss", None, None)
    assert orphan.r_gap is None
    assert orphan.to_dict()["cycle_id"] is None and orphan.to_dict()["linked"] is False
    flipped = OutcomeRecord(result(side="buy", net_pnl=0.0), intent_record(side="sell"))
    assert flipped.side_mismatch and flipped.kind == "flat"


def test_outcome_stats_count_every_result_and_average_what_is_measured() -> None:
    outcomes = (linked(FIRST, 20.8), linked(SECOND, -10.4, label_r=-1.0),
                linked(THIRD, 0.0, label_r=None), unlinked(-5.0))

    stats = outcome_stats(outcomes)

    assert (stats.n, stats.wins, stats.losses, stats.breakeven, stats.unlinked) == (4, 1, 2, 1, 1)
    assert stats.total_pnl == Decimal("5.4") and stats.win_rate_pct == 33.33
    assert stats.n_r == 3 and stats.avg_r == pytest.approx(1 / 3)
    assert stats.sd_r == pytest.approx(1.527525, rel=1e-6)
    assert stats.t_r == pytest.approx((1 / 3) / (1.527525 / 3 ** 0.5), rel=1e-6)
    assert (stats.n_label, stats.avg_label_r) == (2, pytest.approx(0.45))
    assert (stats.n_gap, stats.avg_r_gap) == (2, pytest.approx(0.05))
    document = stats.to_dict()
    assert json.loads(json.dumps(document)) == document
    assert (document["total_pnl"], document["avg_r"], document["t_r"]) == (5.4, 0.333, 0.378)


def test_empty_and_single_outcome_stats_make_no_claims() -> None:
    empty = outcome_stats(())
    assert (empty.n, empty.win_rate_pct, empty.avg_r, empty.t_r) == (0, None, None, None)
    assert empty.to_dict()["total_pnl"] == 0.0
    single = outcome_stats((linked(),))
    assert (single.n_r, single.sd_r, single.t_r) == (1, None, None)
    flat = outcome_stats((linked(FIRST, 10.4), linked(SECOND, 10.4)))
    assert (flat.sd_r, flat.t_r) == (0.0, None)


# --- linking through the ledgers ------------------------------------------------------------
def test_outcome_for_event_links_through_the_ledger(journal: Journal) -> None:
    seed_trade(journal, FIRST, net_pnl=-10.4, label_r=-1.0)

    record = outcome_for_event(journal.cycles, event(FIRST, net_pnl=-10.4), login=LOGIN,
                               received_at_utc=iso(CLOSE))
    assert record.intent is not None and record.intent.status == "CLOSED"
    assert (record.r_multiple, record.label_r, record.login) == (Decimal(-1), Decimal(-1), LOGIN)
    unknown = outcome_for_event(journal.cycles, event(SECOND), login=LOGIN)
    assert (unknown.intent, unknown.label, unknown.login) == (None, None, None)
    malformed = outcome_for_event(journal.cycles, event(basket_id="XAUUSD-V5B-1"))
    assert malformed.result.intent_id is None and malformed.intent is None
