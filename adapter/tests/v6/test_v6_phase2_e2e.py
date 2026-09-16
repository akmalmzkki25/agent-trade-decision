"""
Phase 2 end to end through the real app: backfill, snapshots, the worker, the ledger,
polls, the daily session, HALT/resume, the dashboard and the single-runtime rule.

Shadow only: every poll must answer has_intent=false, whatever the cycle decided.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import create_app
from app.v6.clock import FakeClock
from app.v6.config import V6Settings

from . import engine_fixtures_v6 as ef
from .fixtures_v6 import to_rows
from .payloads_v6 import backfill_payload, poll_payload

LOOPBACK = ("127.0.0.1", 50124)
TOKEN = "operator-" + "e" * 40
JSON = {"Content-Type": "application/json"}
AUTH = {**JSON, "Authorization": f"Bearer {TOKEN}"}
A_OPEN = ef.T_BAR - ef.M15                        # Thu 12:30 UTC, closes 12:45
DISPLACEMENT_ROW = [ef.T_BAR, 4300.0, 4322.0, 4299.5, 4321.5, 500, 20]
WAIT_S = 10.0
WAIT_STEP_S = 0.02


def settings(tmp_path: Path) -> V6Settings:
    return V6Settings(_env_file=None, enabled=True, mode="shadow", backend="rules",
                      halt_file=str(tmp_path / "V6_HALT"), operator_token=TOKEN)


def build(tmp_path: Path, clock: FakeClock) -> FastAPI:
    return create_app(v6_settings=settings(tmp_path), clock=clock,
                      v6_db_path=str(tmp_path / "e2e.db"))


def post(client: TestClient, path: str, body: dict[str, Any] | None = None,
         headers: dict[str, str] | None = None):
    return client.post(path, content=json.dumps(body or {}).encode("utf-8"),
                       headers=JSON if headers is None else headers)


def rows(db: Path, sql: str, params: tuple[object, ...] = ()) -> list[tuple]:
    with sqlite3.connect(str(db)) as conn:
        return conn.execute(sql, params).fetchall()


def wait_for_cycles(db: Path, count: int) -> list[tuple]:
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        found = rows(db, "SELECT cycle_id, status, hold_reason, session_id FROM v6_cycles"
                         " ORDER BY bar_open_epoch")
        if len(found) >= count:
            return found
        time.sleep(WAIT_STEP_S)
    raise AssertionError(f"expected {count} cycle rows within {WAIT_S} s")


def history() -> dict[str, tuple]:
    as_of = A_OPEN + ef.M15
    base = ef.history(as_of)
    return {**base, "M15": ef.flat_bars(ef.M15, as_of - 12 * ef.DAY, as_of, 5.0)}


def backfill(client: TestClient) -> None:
    for tf, bars in history().items():
        payload = backfill_payload(tf, [list(row) for row in to_rows(bars)])
        response = post(client, "/v6/bars/backfill", payload)
        assert response.status_code == 200, response.text
        assert response.json() == {"accepted": len(bars)}


def snapshot(client: TestClient, snapshot_id: str, bar_open: int,
             m15_row: list[Any] | None = None) -> str:
    row = m15_row or [bar_open, ef.PRICE, ef.PRICE + 5, ef.PRICE - 5, ef.PRICE, 100, 20]
    payload = ef.engine_snapshot_payload(snapshot_id, bar_open=bar_open,
                                         bars={"M15": [row]})
    response = post(client, "/v6/snapshot", payload)
    assert response.status_code == 202, response.text
    return response.json()["cycle_id"]


def poll(client: TestClient) -> dict[str, Any]:
    response = post(client, "/v6/intent/poll", poll_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["has_intent"] is False, "Phase 2 never publishes an intent"
    assert body["lots"] == 0.0 and body["intent_id"] == ""
    return body


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(epoch=float(A_OPEN + ef.M15 + 1))


@pytest.fixture
def client(tmp_path: Path, clock: FakeClock) -> Iterator[TestClient]:
    with TestClient(build(tmp_path, clock), client=LOOPBACK) as test_client:
        yield test_client


def _bar_a_without_session(client: TestClient, db: Path) -> None:
    """Warm, every gate passes, flat bar: nothing to offer and no session."""
    cycle_a = snapshot(client, "snap-e2e-a", A_OPEN)
    (first,) = wait_for_cycles(db, 1)
    assert first == (cycle_a, "HOLD", "APP-V6-NO-CANDIDATE", None)
    assert poll(client)["command"] == "NONE"


def _bar_b_in_session(client: TestClient, clock: FakeClock, db: Path) -> None:
    """A displacement through the prior-day high: the panel enters, but the
    $2,000 / 0.5% budget cannot afford the structural stop (HOLD SIZE)."""
    started = post(client, "/v6/control/session", {"action": "start"}, AUTH)
    assert started.status_code == 200, started.text
    session_id = started.json()["session"]["session_id"]
    clock.advance(ef.M15)
    cycle_b = snapshot(client, "snap-e2e-b", ef.T_BAR, DISPLACEMENT_ROW)
    second = wait_for_cycles(db, 2)[1]
    assert second == (cycle_b, "HOLD", "APP-V6-SIZE", session_id)
    candidates = rows(db, "SELECT verdict, stop, target, label_status FROM v6_candidates"
                          " WHERE cycle_id = ?", (cycle_b,))
    assert candidates == [("chosen", 4298.3, 4335.65, "pending")]
    roles = rows(db, "SELECT role, source FROM v6_agent_views WHERE cycle_id = ?", (cycle_b,))
    assert [role for role, _ in roles] == [
        "price_action", "news_risk", "liquidity", "structure", "chief"]
    assert poll(client)["command"] == "NONE"


def _stop_session(client: TestClient) -> None:
    """"Sudah cukup hari ini": the session closes, CANCEL_PENDING is delivered once."""
    stopped = post(client, "/v6/control/session", {"action": "stop"}, AUTH)
    assert stopped.status_code == 200
    body = stopped.json()
    assert body["stopped"] is True and body["summary"]["cycles"] == 2
    assert body["command"]["command"] == "CANCEL_PENDING"
    assert poll(client)["command"] == "CANCEL_PENDING"
    assert poll(client)["command"] == "NONE"


def _halt_and_resume(client: TestClient, clock: FakeClock, db: Path) -> str:
    """HALT shows in the status, the poll command and the next cycle; resume clears it."""
    halted = post(client, "/v6/control/halt", {"reason": "e2e"}, AUTH)
    assert halted.status_code == 200 and halted.json()["halted"] is True
    assert client.get("/v6/status").json()["runtime"]["status"] == "HALTED"
    assert poll(client)["command"] == "CANCEL_PENDING"
    clock.advance(ef.M15)
    cycle_c = snapshot(client, "snap-e2e-c", ef.T_BAR + ef.M15)
    third = wait_for_cycles(db, 3)[2]
    assert third == (cycle_c, "HOLD", "APP-V6-HALTED", None)
    resumed = post(client, "/v6/control/resume", {}, AUTH)
    assert resumed.status_code == 200 and resumed.json() == {"halted": False, "removed": True}
    assert poll(client)["command"] == "NONE"
    return cycle_c


def test_a_trading_day_in_shadow_mode(client: TestClient, clock: FakeClock,
                                      tmp_path: Path) -> None:
    db = tmp_path / "e2e.db"
    backfill(client)
    assert client.get("/v6/status").json()["warm"] is True
    _bar_a_without_session(client, db)
    _bar_b_in_session(client, clock, db)
    _stop_session(client)
    cycle_c = _halt_and_resume(client, clock, db)
    status = client.get("/v6/status").json()
    assert status["runtime"]["status"] == "RUNNING"
    assert status["runtime"]["worker"]["processed"] == 3
    assert status["runtime"]["worker"]["running"] is True
    assert status["session"] is None
    last = status["last_cycle"]
    assert (last["cycle_id"], last["hold_reason"], last["failed_gates"]) == (
        cycle_c, "APP-V6-HALTED", ["HALTED"])
    assert rows(db, "SELECT COUNT(*) FROM v6_cycles WHERE status = 'ENTER_SHADOW'") == [(0,)]


def test_dashboard_and_status_show_the_runtime(client: TestClient, tmp_path: Path) -> None:
    snapshot(client, "snap-e2e-cold", A_OPEN)
    (cycle,) = wait_for_cycles(tmp_path / "e2e.db", 1)
    assert cycle[2] == "APP-V6-WARMUP"
    page = client.get("/v6")
    assert page.status_code == 200 and 'data-enabled="true"' in page.text
    assert 'href="/v6"' in page.text and "V6 Desk" in page.text
    overview = client.get("/v6/api/overview").json()
    assert overview["recent_cycles"][0]["hold_reason"] == "APP-V6-WARMUP"
    status = client.get("/v6/status").json()
    assert status["runtime"]["tasks_running"] is True
    assert status["runtime"]["pending_command"] is None
    # No bars at all: warm-up decides, and the ATR-based gates cannot pass either.
    assert status["last_cycle"]["failed_gates"] == ["WARMUP", "FRICTION_ATR", "ATR_M5"]


def test_a_second_app_on_the_same_database_runs_no_worker(client: TestClient,
                                                          tmp_path: Path,
                                                          clock: FakeClock) -> None:
    first = client.app.state.v6_container
    second_app = build(tmp_path, clock)
    with TestClient(second_app, client=LOOPBACK) as second:
        container = second_app.state.v6_container
        assert container.active is False
        assert second.get("/v6/status").status_code == 404
        assert post(second, "/v6/control/session", {"action": "start"},
                    AUTH).status_code == 404
        assert container.parts.worker.stats.running is False
    assert first.active and first.parts.worker.stats.running


def test_hosts_outside_loopback_are_refused(tmp_path: Path, clock: FakeClock) -> None:
    app = build(tmp_path, clock)
    with TestClient(app, base_url="http://attacker.example") as rebound:
        assert rebound.get("/v1/healthz").status_code == 400
    with TestClient(app, base_url="http://127.0.0.1:8765") as local:
        assert local.get("/v1/healthz").status_code == 200
