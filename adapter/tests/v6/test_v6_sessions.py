"""SessionService, CommandBoard and trading-day helpers (plan section 4b)."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator, Mapping
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.v6.clock import FakeClock
from app.v6.ledger_cycles import LedgerCycles
from app.v6.ledger_v6 import LedgerV6
from app.v6.runtime import sessions as sessions_module
from app.v6.runtime.ea_state import EaState, SnapshotMeta, cycle_id_for
from app.v6.runtime.sessions import (
    ACTOR_RUNTIME,
    CANCEL_PENDING_TTL_S,
    REFUSE_BREAKER,
    REFUSE_MARKET_CLOSED,
    REFUSE_NOT_DEMO,
    STOP_REASON_DAY_CHANGED,
    STOP_REASON_ROLLOVER,
    CommandBoard,
    ControlPlane,
    OpenExposure,
    SessionService,
    build_control_plane,
    last_trade_mode,
    poll_command,
    trading_day_bounds,
    trading_day_for,
)
from app.v6.cycle_types import HoldReason

from .cycle_fixtures_v6 import enter_result, hold_result
from .payloads_v6 import BAR_OPEN, RECEIVED_AT, as_poll, as_snapshot, poll_payload, snapshot_payload

# Wednesday 2026-09-16 (EDT, UTC-4): 17:00 New York is 21:00 UTC.
MIDDAY: float = RECEIVED_AT                      # 12:15:01 UTC
ROLLOVER_START: int = 1_789_592_400              # 2026-09-16 21:00:00 UTC
DAY: str = "2026-09-16"
NEXT_DAY: str = "2026-09-17"
SATURDAY_NOON: int = 1_789_819_200               # 2026-09-19 12:00 UTC


def _utc(*parts: int) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp())


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "sessions.db"


@pytest.fixture
def ledger(db_path: Path) -> Iterator[LedgerCycles]:
    led = LedgerCycles(db_path)
    yield led
    led.close()


@pytest.fixture
def control_log(db_path: Path) -> Iterator[LedgerV6]:
    log = LedgerV6(db_path, clock=FakeClock(epoch=MIDDAY))
    yield log
    log.close()


@pytest.fixture
def ea_state() -> EaState:
    return EaState()


@pytest.fixture
def plane(ledger: LedgerCycles, control_log: LedgerV6, ea_state: EaState) -> ControlPlane:
    return build_control_plane(ledger=ledger, control_log=control_log, ea_state=ea_state)


@pytest.fixture
def service(plane: ControlPlane) -> SessionService:
    return plane.sessions


def _poll(ea_state: EaState, trade_mode: str = "DEMO", at: float = MIDDAY) -> None:
    body = poll_payload(trade_mode=trade_mode)
    body.update(open_v6_positions=1, pending_v6_orders=0)
    ea_state.record_poll(as_poll(body), at)


def _snapshot_meta(trade_mode: str, at: float) -> SnapshotMeta:
    snapshot = as_snapshot(snapshot_payload("snap-meta", bars={}, trade_mode=trade_mode))
    return SnapshotMeta.from_snapshot(snapshot, cycle_id_for("snap-meta"), at)


def _actions(db_path: Path) -> list[tuple[str, str, dict[str, object]]]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT actor, action, detail_json FROM v6_control_log"
                            " ORDER BY id").fetchall()
    return [(actor, action, json.loads(detail)) for actor, action, detail in rows]


# --- trading day -----------------------------------------------------------------
@pytest.mark.parametrize(("epoch", "expected"), [
    (_utc(2026, 9, 16, 12, 0), "2026-09-16"),
    (_utc(2026, 9, 16, 20, 59, 59), "2026-09-16"),
    (_utc(2026, 9, 16, 21, 0), "2026-09-17"),       # 17:00 EDT
    (_utc(2026, 11, 16, 21, 30), "2026-11-16"),      # 16:30 EST
    (_utc(2026, 11, 16, 22, 0), "2026-11-17"),       # 17:00 EST
    (_utc(2026, 9, 13, 21, 0), "2026-09-14"),        # Sunday reopen is Monday's day
])
def test_trading_day_rolls_at_the_new_york_close(epoch: int, expected: str) -> None:
    assert trading_day_for(epoch) == expected


@pytest.mark.parametrize("bad", [1.5, True, "1789560000"])
def test_trading_day_needs_an_int_epoch(bad: object) -> None:
    with pytest.raises(TypeError):
        trading_day_for(bad)  # type: ignore[arg-type]


def test_trading_day_bounds_span_close_to_close() -> None:
    start, end = trading_day_bounds(DAY)
    assert (start, end) == (_utc(2026, 9, 15, 21, 0), ROLLOVER_START)
    assert trading_day_for(start) == DAY and trading_day_for(end) == NEXT_DAY
    # US clocks fall back on Sunday 2026-11-01, so that trading day lasts 25 hours.
    start, end = trading_day_bounds("2026-11-01")
    assert end - start == 25 * 3600
    start, end = trading_day_bounds("2026-11-02")
    assert end - start == 24 * 3600


@pytest.mark.parametrize("bad", ["2026-9-16", "yesterday", "2026-13-01", ""])
def test_trading_day_bounds_refuse_malformed_days(bad: str) -> None:
    with pytest.raises(ValueError):
        trading_day_bounds(bad)


# --- command board ---------------------------------------------------------------
def test_cancel_pending_is_delivered_then_cleared_once_the_ea_reports_none_pending() -> None:
    board = CommandBoard()
    assert board.command_for_poll(0, MIDDAY) == "NONE"
    requested = board.request_cancel_pending("session_stop", MIDDAY)
    assert (requested.command, requested.delivered_at) == ("CANCEL_PENDING", None)
    with pytest.raises(FrozenInstanceError):
        requested.reason = "x"  # type: ignore[misc]

    assert board.command_for_poll(1, MIDDAY + 2) == "CANCEL_PENDING"
    assert board.current(MIDDAY + 2).delivered_at == MIDDAY + 2  # type: ignore[union-attr]
    assert board.command_for_poll(1, MIDDAY + 4) == "CANCEL_PENDING"   # still pending
    assert board.command_for_poll(0, MIDDAY + 6) == "NONE"             # acknowledged
    assert board.current(MIDDAY + 6) is None
    assert requested.delivered_at is None, "the earlier value must not change"


def test_cancel_pending_expires_even_without_an_acknowledgement() -> None:
    board = CommandBoard()
    board.request_cancel_pending("halt", MIDDAY)
    assert board.command_for_poll(3, MIDDAY + CANCEL_PENDING_TTL_S - 1) == "CANCEL_PENDING"
    assert board.command_for_poll(3, MIDDAY + CANCEL_PENDING_TTL_S) == "NONE"
    assert board.current(MIDDAY + CANCEL_PENDING_TTL_S) is None


def test_a_new_request_resets_delivery_and_expiry() -> None:
    board = CommandBoard()
    board.request_cancel_pending("first", MIDDAY)
    board.command_for_poll(0, MIDDAY + 1)
    renewed = board.request_cancel_pending("second", MIDDAY + 5)
    assert renewed.delivered_at is None and renewed.expires_at == MIDDAY + 5 + CANCEL_PENDING_TTL_S
    assert board.command_for_poll(0, MIDDAY + 6) == "CANCEL_PENDING"
    assert renewed.to_dict()["reason"] == "second"


def test_poll_command_always_cancels_while_halted_and_still_acknowledges() -> None:
    board = CommandBoard()
    assert poll_command(board, halted=True, pending_v6_orders=0, now=MIDDAY) == "CANCEL_PENDING"
    assert poll_command(board, halted=False, pending_v6_orders=0, now=MIDDAY) == "NONE"
    board.request_cancel_pending("stop", MIDDAY)
    assert poll_command(board, halted=True, pending_v6_orders=0, now=MIDDAY + 1) == "CANCEL_PENDING"
    assert poll_command(board, halted=False, pending_v6_orders=0, now=MIDDAY + 2) == "NONE"


# --- exposure and trade mode -----------------------------------------------------
def test_exposure_and_trade_mode_come_from_the_newest_ea_observation(ea_state: EaState) -> None:
    empty = OpenExposure.from_view(ea_state.view())
    assert empty.open_v6_positions is None and empty.to_dict()["observed_at"] is None
    assert last_trade_mode(ea_state.view()) is None

    ea_state.record_snapshot(_snapshot_meta("CONTEST", MIDDAY - 10))
    assert last_trade_mode(ea_state.view()) == "CONTEST"
    _poll(ea_state, "DEMO", MIDDAY)
    assert last_trade_mode(ea_state.view()) == "DEMO"
    ea_state.record_snapshot(_snapshot_meta("REAL", MIDDAY + 1))
    assert last_trade_mode(ea_state.view()) == "REAL"

    exposure = OpenExposure.from_view(ea_state.view())
    assert (exposure.open_v6_positions, exposure.pending_v6_orders) == (1, 0)
    assert exposure.observed_at == MIDDAY and exposure.local_halt is False


# --- start -----------------------------------------------------------------------
@pytest.mark.anyio
async def test_start_is_refused_until_the_ea_has_reported_a_demo_account(
        service: SessionService, ea_state: EaState, ledger: LedgerCycles) -> None:
    unknown = await service.start(backend="rules", mode="shadow", actor="operator", now=MIDDAY)
    assert (unknown.refused, unknown.refusal, unknown.session) == (True, REFUSE_NOT_DEMO, None)
    assert "unknown" in unknown.detail

    _poll(ea_state, "REAL")
    real = await service.start(backend="rules", mode="shadow", actor="operator", now=MIDDAY)
    assert real.refusal == REFUSE_NOT_DEMO and "REAL" in real.detail
    assert ledger.active_session() is None


@pytest.mark.anyio
async def test_start_is_refused_while_a_breaker_is_tripped(
        service: SessionService, ea_state: EaState, ledger: LedgerCycles) -> None:
    _poll(ea_state)
    ledger.trip_breaker("weekly", "2026-W38", "LOSS_6PCT", MIDDAY - 60)
    outcome = await service.start(backend="rules", mode="shadow", actor="operator", now=MIDDAY)
    assert outcome.refusal == REFUSE_BREAKER and "weekly" in outcome.detail
    assert outcome.to_dict()["refusal"] == REFUSE_BREAKER


@pytest.mark.anyio
@pytest.mark.parametrize("now", [ROLLOVER_START + 60, SATURDAY_NOON])
async def test_start_is_refused_in_the_rollover_block_and_at_the_weekend(
        service: SessionService, ea_state: EaState, now: int) -> None:
    _poll(ea_state, at=float(now))
    outcome = await service.start(backend="rules", mode="shadow", actor="operator",
                                  now=float(now))
    assert outcome.refusal == REFUSE_MARKET_CLOSED


@pytest.mark.anyio
async def test_start_opens_one_session_per_trading_day(
        service: SessionService, ea_state: EaState, db_path: Path) -> None:
    _poll(ea_state)
    first = await service.start(backend="operator", mode="shadow", actor="operator",
                                now=MIDDAY)
    again = await service.start(backend="operator", mode="shadow", actor="operator",
                                now=MIDDAY + 60)

    assert first.created and not first.refused and first.trading_day == DAY
    assert first.session is not None and first.session.armed is False
    assert (first.session.backend, first.session.mode) == ("operator", "shadow")
    assert not again.created and again.session == first.session
    assert [a[:2] for a in _actions(db_path)] == [("operator", "session_start")]
    assert _actions(db_path)[0][2]["session_id"] == first.session.session_id
    json.dumps(first.to_dict())


@pytest.mark.anyio
async def test_start_on_a_new_trading_day_closes_the_forgotten_session(
        service: SessionService, ea_state: EaState, ledger: LedgerCycles) -> None:
    _poll(ea_state)
    old = await service.start(backend="rules", mode="shadow", actor="operator", now=MIDDAY)
    tomorrow = MIDDAY + 86_400
    _poll(ea_state, at=tomorrow)
    new = await service.start(backend="rules", mode="shadow", actor="operator", now=tomorrow)

    assert new.created and new.trading_day == NEXT_DAY
    closed = ledger.sessions_for_day(DAY)
    assert old.session is not None and closed[0].session_id == old.session.session_id
    assert closed[0].stop_reason == STOP_REASON_DAY_CHANGED and not closed[0].is_active


# --- stop ------------------------------------------------------------------------
@pytest.mark.anyio
async def test_stop_closes_the_session_cancels_pending_and_summarises_the_day(
        plane: ControlPlane, ea_state: EaState, ledger: LedgerCycles, db_path: Path) -> None:
    _poll(ea_state)
    started = await plane.sessions.start(backend="rules", mode="shadow", actor="operator",
                                         now=MIDDAY)
    ledger.record_cycle(hold_result("c-1", bar_open=BAR_OPEN - 900,
                                    reason=HoldReason.NO_SESSION), MIDDAY)
    ledger.record_cycle(hold_result("c-2", bar_open=BAR_OPEN), MIDDAY)
    ledger.record_cycle(enter_result("c-3", bar_open=BAR_OPEN + 900), MIDDAY)
    ledger.record_cycle(hold_result("c-old", bar_open=BAR_OPEN - 86_400), MIDDAY)

    outcome = await plane.sessions.stop(actor="operator", reason="done_for_today",
                                        now=MIDDAY + 3600)

    assert outcome.stopped and outcome.session is not None
    assert started.session is not None
    assert outcome.session.session_id == started.session.session_id
    assert (outcome.session.stop_reason, outcome.session.stopped_at) == (
        "done_for_today", MIDDAY + 3600)
    assert outcome.command.command == "CANCEL_PENDING"
    assert plane.commands.current(MIDDAY + 3600) == outcome.command
    summary = outcome.summary
    assert (summary.trading_day, summary.cycles, summary.shadow_intents) == (DAY, 3, 1)
    assert dict(summary.hold_reasons) == {"APP-V6-GATE": 1, "APP-V6-NO-SESSION": 1}
    assert dict(summary.by_status) == {"HOLD": 2, "ENTER_SHADOW": 1}
    assert summary.exposure.open_v6_positions == 1, "positions are left running"
    assert [s.session_id for s in summary.sessions] == [started.session.session_id]
    payload = json.loads(json.dumps(outcome.to_dict()))
    assert payload["summary"]["exposure"]["open_v6_positions"] == 1
    assert payload["session"]["active"] is False and payload["summary"]["intents"] == 0
    assert ledger.active_session() is None
    assert [a[1] for a in _actions(db_path)] == ["session_start", "session_stop"]


@pytest.mark.anyio
async def test_stop_without_a_session_still_cancels_pending_orders(
        plane: ControlPlane, db_path: Path) -> None:
    outcome = await plane.sessions.stop(actor="operator", reason="manual", now=MIDDAY)
    assert not outcome.stopped and outcome.session is None
    assert outcome.summary.trading_day == DAY and outcome.summary.cycles == 0
    assert plane.commands.command_for_poll(0, MIDDAY + 1) == "CANCEL_PENDING"
    assert _actions(db_path) == []
    assert outcome.to_dict()["session"] is None


@pytest.mark.anyio
async def test_status_reports_the_active_session_and_pending_command(
        plane: ControlPlane, ea_state: EaState) -> None:
    idle = await plane.sessions.status(MIDDAY)
    assert (idle.session, idle.command, idle.trading_day) == (None, None, DAY)
    _poll(ea_state)
    await plane.sessions.start(backend="rules", mode="shadow", actor="operator", now=MIDDAY)
    busy = await plane.sessions.status(MIDDAY + 1)
    assert busy.session is not None and busy.session == await plane.sessions.active()
    body = busy.to_dict()
    assert body["session"]["trading_day"] == DAY and body["command"] is None
    json.dumps(body)


# --- rollover auto-close -----------------------------------------------------------
@pytest.mark.anyio
async def test_auto_close_does_nothing_without_a_session_or_before_rollover(
        service: SessionService, ea_state: EaState) -> None:
    assert await service.auto_close_if_rollover(float(ROLLOVER_START)) is None
    _poll(ea_state)
    await service.start(backend="rules", mode="shadow", actor="operator", now=MIDDAY)
    assert await service.auto_close_if_rollover(float(ROLLOVER_START - 1)) is None
    assert await service.active() is not None


@pytest.mark.anyio
async def test_auto_close_stops_the_session_in_the_rollover_block(
        plane: ControlPlane, ea_state: EaState, db_path: Path) -> None:
    _poll(ea_state)
    await plane.sessions.start(backend="rules", mode="shadow", actor="operator", now=MIDDAY)
    outcome = await plane.sessions.auto_close_if_rollover(float(ROLLOVER_START + 5))
    assert outcome is not None and outcome.stopped
    assert outcome.session is not None and outcome.session.stop_reason == STOP_REASON_ROLLOVER
    assert outcome.summary.trading_day == DAY
    assert _actions(db_path)[-1][:2] == (ACTOR_RUNTIME, "session_stop")
    assert await plane.sessions.auto_close_if_rollover(float(ROLLOVER_START + 10)) is None


@pytest.mark.anyio
async def test_auto_close_catches_a_session_left_from_an_earlier_day(
        service: SessionService, ea_state: EaState) -> None:
    _poll(ea_state)
    await service.start(backend="rules", mode="shadow", actor="operator", now=MIDDAY)
    two_days_later = MIDDAY + 2 * 86_400          # midday again: no rollover block
    outcome = await service.auto_close_if_rollover(two_days_later)
    assert outcome is not None and outcome.stopped


# --- audit failures and wiring -------------------------------------------------------
class _BrokenLog:
    def log_control(self, actor: str, action: str, detail: Mapping[str, object]) -> None:
        raise sqlite3.OperationalError("database is locked")


@pytest.mark.anyio
async def test_a_failed_audit_write_is_logged_but_does_not_undo_the_session(
        ledger: LedgerCycles, ea_state: EaState, caplog: pytest.LogCaptureFixture) -> None:
    plane = build_control_plane(ledger=ledger, control_log=_BrokenLog(), ea_state=ea_state)
    _poll(ea_state)
    with caplog.at_level(logging.ERROR, logger=sessions_module.__name__):
        outcome = await plane.sessions.start(backend="rules", mode="shadow",
                                             actor="operator", now=MIDDAY)
    assert outcome.created and ledger.active_session() is not None
    assert "session_start" in caplog.text


def test_each_control_plane_gets_its_own_csrf_nonce(
        ledger: LedgerCycles, control_log: LedgerV6, ea_state: EaState) -> None:
    first = build_control_plane(ledger=ledger, control_log=control_log, ea_state=ea_state)
    second = build_control_plane(ledger=ledger, control_log=control_log, ea_state=ea_state)
    assert len(first.csrf_nonce) >= 40 and first.csrf_nonce != second.csrf_nonce
    assert first.ledger is ledger and first.sessions.commands is first.commands
    with pytest.raises(FrozenInstanceError):
        first.csrf_nonce = "x"  # type: ignore[misc]
    assert "hidden-nonce" not in repr(replace(first, csrf_nonce="hidden-nonce"))
