"""
One status word everywhere, and a HALT button that survives an adapter restart.

The watchdog, GET /v6/status and the /v6 overview classify through
`runtime.status`; the overview repeats the CSRF nonce so an open page can halt
after the process (and its nonce) changed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.routes import v6_ea
from app.v6.clock import FakeClock
from app.v6.dashboard_queries import OverviewState, OverviewTables, build_overview
from app.v6.ledger_cycles_schema import CycleSummary
from app.v6.runtime.ea_state import EaState, SnapshotMeta
from app.v6.runtime.status import SNAPSHOT_STALE_AFTER_S

from .payloads_v6 import BAR_OPEN, RECEIVED_AT
from .test_v6_dashboard import (
    LOOPBACK, SCRIPT, ControlApp, build_control_app, control_settings, record_demo_poll,
)

HARNESS = Path(__file__).with_name("v6_js_harness.js")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(epoch=RECEIVED_AT)


@pytest.fixture
def wired(tmp_path: Path, clock: FakeClock) -> ControlApp:
    built = build_control_app(control_settings(tmp_path), tmp_path / "status.db", clock)
    built.app.include_router(v6_ea.router)
    return built


@pytest.fixture
def client(wired: ControlApp) -> Iterator[TestClient]:
    with TestClient(wired.app, client=LOOPBACK) as test_client:
        yield test_client


def _snapshot_received(wired: ControlApp, received_at: float) -> None:
    assert wired.container is not None
    wired.container.ea_state.record_snapshot(SnapshotMeta(
        snapshot_id="s-old", cycle_id="c-old", bar_open_epoch=BAR_OPEN,
        received_at=received_at, trade_mode="DEMO", clock_skew_s=0.0))


# --- finding: the dashboard ignored snapshot staleness -----------------------------------
def test_dashboard_and_status_agree_on_a_stale_snapshot(client: TestClient, wired: ControlApp,
                                                        clock: FakeClock) -> None:
    _snapshot_received(wired, clock.epoch - SNAPSHOT_STALE_AFTER_S - 1)
    assert wired.container is not None
    record_demo_poll(wired.container)          # the EA keeps polling

    overview = client.get("/v6/api/overview").json()["runtime"]["status"]
    status = client.get("/v6/status").json()["runtime"]["status"]

    assert overview == status == "STALE"


def test_the_dashboard_shows_a_watchdog_breaker(tmp_path: Path) -> None:
    counts = CycleSummary(0, {}, {}, 0)
    tables = OverviewTables(
        halted=False, active_session=None, trading_day="2026-09-16", day_counts=counts,
        day_sessions=(), week_hold_reasons={}, breakers=(), recent=(), label_stats=(),
        market=None)
    ea = EaState()
    ea.touch(RECEIVED_AT)
    state = OverviewState(now=RECEIVED_AT, active=True, settings=control_settings(tmp_path),
                          ea=ea.view(), command=None)

    assert build_overview(tables, state)["runtime"]["status"] == "RUNNING"
    tripped = replace(state, watchdog_breaker_tripped=True)
    assert build_overview(tables, tripped)["runtime"]["status"] == "BREAKER"


# --- finding: the page kept the nonce of a process that had restarted ------------------------
def test_every_overview_carries_the_current_nonce(client: TestClient, wired: ControlApp) -> None:
    assert wired.plane is not None
    body = client.get("/v6/api/overview").json()
    assert body["csrf_nonce"] == wired.plane.csrf_nonce


def _run_page(tmp_path: Path, overview: dict[str, Any], halt_status: int) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    data = tmp_path / "overview.json"
    data.write_text(json.dumps(overview), encoding="utf-8")
    completed = subprocess.run(
        [node, str(HARNESS), str(SCRIPT), str(data), str(halt_status)],
        capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_the_page_halts_with_the_nonce_of_the_running_process(
        tmp_path: Path, client: TestClient, wired: ControlApp) -> None:
    assert wired.plane is not None
    overview = client.get("/v6/api/overview").json()

    page = _run_page(tmp_path, overview, 200)

    assert page["haltNonce"] == wired.plane.csrf_nonce != "nonce-from-the-old-page"
    assert (page["haltResult"], page["status"], page["error"]) == (
        "HALT file written.", "WAITING_EA", "")


def test_a_refused_halt_points_to_the_other_kill_switches(
        tmp_path: Path, client: TestClient) -> None:
    page = _run_page(tmp_path, client.get("/v6/api/overview").json(), 403)
    assert page["haltResult"].startswith('HALT FAILED: "invalid CSRF token"')
    assert "reload the page" in page["haltResult"] and "V6_HALT" in page["haltResult"]
