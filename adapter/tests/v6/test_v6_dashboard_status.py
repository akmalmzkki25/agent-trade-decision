"""
The /v6 page: one status word everywhere, a HALT button that survives an adapter
restart, and the trading half of the overview.

The watchdog, GET /v6/status and the /v6 overview classify through
`runtime.status`; the overview repeats the CSRF nonce so an open page can halt
after the process (and its nonce) changed. The page scripts run under node
against a fake DOM that offers nothing but createElement and textContent.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient

from app.ledger import Ledger
from app.routes import v6_ea
from app.v6.clock import FakeClock
from app.v6.config import OPERATOR_AGENTS
from app.v6.dashboard_queries import (
    OperatorActivity, OverviewState, OverviewTables, build_overview, read_tables,
)
from app.v6.ledger_cycles_schema import CycleSummary
from app.v6.ledger_v6 import SnapshotRecord
from app.v6.runtime.ea_state import EaState, SnapshotMeta
from app.v6.runtime.status import SNAPSHOT_STALE_AFTER_S
from app.v6.schemas.intent import ExecutionReport, basket_id_for

from .payloads_v6 import (
    BAR_OPEN, M15, RECEIVED_AT, as_snapshot, execution_payload, snapshot_payload,
)
from .test_outcomes import (
    CLOSE, DAY, FIRST, LOGIN, SECOND, THIRD, Journal, event, seed_trade, store_result,
)
from .test_v6_dashboard import (
    LOOPBACK, SCRIPT, SCRIPTS, ControlApp, build_control_app, control_settings,
    record_demo_poll,
)

HARNESS = Path(__file__).with_name("v6_js_harness.js")
DB_NAME: Final[str] = "status.db"
HOSTILE_COMMENT: Final[str] = "<img src=x onerror=alert(1)>"
POSITION: Final[dict[str, Any]] = {
    "ticket": 91, "magic": 250570, "side": "buy", "volume": 0.01, "price_open": 4300.3,
    "sl": 4289.8, "tp": 4320.4, "profit": 1.5, "swap": -0.2, "open_epoch": BAR_OPEN + 60,
    "comment": "Q6:" + SECOND, "mae_points": 40.0, "mfe_points": 180.0}
ORDER: Final[dict[str, Any]] = {
    "ticket": 92, "magic": 250570, "order_type": "SELL_LIMIT", "price": 4310.0, "sl": 4320.2,
    "tp": 4289.6, "volume": 0.01, "expiration_epoch": BAR_OPEN + 3600,
    "comment": HOSTILE_COMMENT}
# Runs the bundled page scripts against a fake DOM and prints every element's text.
RENDER_HARNESS: Final[str] = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const [bundlePath, overviewPath] = process.argv.slice(2);
const overview = JSON.parse(fs.readFileSync(overviewPath, 'utf8'));
function Node() {}
function fakeElement(tag) {
  const node = Object.create(Node.prototype);
  Object.assign(node, { tagName: tag, children: [], textContent: '', className: '',
    hidden: false, disabled: false, dataset: {}, style: {}, listeners: {} });
  node.append = (...kids) => {
    if (!kids.every((kid) => kid instanceof Node)) throw new Error('append takes nodes only');
    node.children.push(...kids);
  };
  node.replaceChildren = (...kids) => { node.children = kids; };
  node.addEventListener = (type, handler) => { node.listeners[type] = handler; };
  return node;
}
const elements = new Map();
const byId = (id) => {
  if (!elements.has(id)) elements.set(id, fakeElement('div'));
  return elements.get(id);
};
byId('v6-root').dataset = { pollMs: '5000', csrf: 'nonce' };
const text = (node) => [String(node.textContent), ...node.children.map(text)]
  .filter(Boolean).join(' ');
const context = vm.createContext({
  Node, JSON, Number, String, Object, Array, Math, Date, Set, Error,
  fetch: async () => ({ status: 200, ok: true, json: async () => overview }),
  document: { getElementById: byId, createElement: fakeElement, hidden: false,
              addEventListener: () => undefined },
  window: { confirm: () => true, setInterval: () => 0 },
});
(async () => {
  vm.runInContext(fs.readFileSync(bundlePath, 'utf8'), context);
  await new Promise((resolve) => setTimeout(resolve, 10));
  const dump = {};
  elements.forEach((node, id) => { dump[id] = { text: text(node), rows: node.children.length }; });
  process.stdout.write(JSON.stringify(dump));
})().catch((error) => {
  process.stderr.write(String(error && error.stack));
  process.exit(1);
});
"""


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(epoch=RECEIVED_AT)


@pytest.fixture
def wired(tmp_path: Path, clock: FakeClock) -> ControlApp:
    built = build_control_app(control_settings(tmp_path), tmp_path / DB_NAME, clock)
    built.app.include_router(v6_ea.router)
    return built


@pytest.fixture
def client(wired: ControlApp) -> Iterator[TestClient]:
    with TestClient(wired.app, client=LOOPBACK) as test_client:
        yield test_client


@pytest.fixture
def trading(tmp_path: Path, wired: ControlApp) -> Iterator[Journal]:
    """The V1 ledger (basket_results) on the dashboard's database, as in production."""
    assert wired.container is not None and wired.ledger is not None
    v1 = Ledger(str(tmp_path / DB_NAME))
    yield Journal(tmp_path / DB_NAME, v1, wired.ledger, wired.container.ledger_v6)
    v1.conn.close()


def _snapshot_received(wired: ControlApp, received_at: float) -> None:
    assert wired.container is not None
    wired.container.ea_state.record_snapshot(SnapshotMeta(
        snapshot_id="s-old", cycle_id="c-old", bar_open_epoch=BAR_OPEN,
        received_at=received_at, trade_mode="DEMO", clock_skew_s=0.0))


def _seed_trading(trading: Journal, wired: ControlApp) -> None:
    """Two closed intents, an orphan result, EA reports and a snapshot with open orders."""
    assert wired.container is not None
    record_demo_poll(wired.container)
    seed_trade(trading, FIRST, bar_open=BAR_OPEN - DAY, net_pnl=-10.4, label_r=-1.0)
    seed_trade(trading, SECOND, net_pnl=20.8)
    store_result(trading, event(THIRD, net_pnl=-5.0), CLOSE + 60)
    ledger_v6 = wired.container.ledger_v6
    for status, sent_at in (("placed", BAR_OPEN + 1), ("filled", BAR_OPEN + 2)):
        report = {**execution_payload(status, sent_at), "intent_id": SECOND}
        assert ledger_v6.insert_execution(
            ExecutionReport.model_validate_json(json.dumps(report)), float(sent_at))
    payload = snapshot_payload("snap-open", bar_open=BAR_OPEN + M15, bars={})
    payload.update(positions=[POSITION], pending_orders=[ORDER])
    assert ledger_v6.insert_snapshot(SnapshotRecord.from_snapshot(
        as_snapshot(payload), json.dumps(payload), RECEIVED_AT))


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


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    return node


def _node_json(tmp_path: Path, harness: Path, overview: dict[str, Any],
               scripts: tuple[Path, ...], *extra: str) -> dict[str, Any]:
    bundle = tmp_path / "page-bundle.js"
    bundle.write_text("\n".join(s.read_text(encoding="utf-8") for s in scripts), encoding="utf-8")
    data = tmp_path / "overview.json"
    data.write_text(json.dumps(overview), encoding="utf-8")
    completed = subprocess.run(
        [_node(), str(harness), str(bundle), str(data), *extra],
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _run_page(tmp_path: Path, overview: dict[str, Any], halt_status: int,
              scripts: tuple[Path, ...] = SCRIPTS) -> dict[str, Any]:
    return _node_json(tmp_path, HARNESS, overview, scripts, str(halt_status))


def _render_page(tmp_path: Path, overview: dict[str, Any]) -> dict[str, Any]:
    harness = tmp_path / "render_harness.js"
    harness.write_text(RENDER_HARNESS, encoding="utf-8")
    return _node_json(tmp_path, harness, overview, SCRIPTS)


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


def test_halt_still_works_when_the_renderer_did_not_load(
        tmp_path: Path, client: TestClient, wired: ControlApp) -> None:
    assert wired.plane is not None
    page = _run_page(tmp_path, client.get("/v6/api/overview").json(), 200, scripts=(SCRIPT,))
    assert page["error"] == "Refresh failed: the page renderer (v6_render.js) did not load"
    assert (page["haltNonce"], page["haltResult"]) == (wired.plane.csrf_nonce,
                                                       "HALT file written.")


# --- the trading half ------------------------------------------------------------------------
def test_the_overview_shows_the_trading_day(client: TestClient, wired: ControlApp,
                                            trading: Journal) -> None:
    _seed_trading(trading, wired)

    body = client.get("/v6/api/overview").json()

    assert [intent["intent_id"] for intent in body["intents"]] == [SECOND, FIRST]
    newest = body["intents"][0]
    assert (newest["status"], newest["r_multiple"], newest["basket_id"]) == (
        "CLOSED", 2.0, basket_id_for("XAUUSD", SECOND))
    assert [(x["intent_id"], x["status"], x["ticket"]) for x in body["executions"]] == [
        (SECOND, "filled", 777), (SECOND, "placed", 777)]
    orders = body["open_orders"]
    assert (orders["available"], orders["as_of_epoch"]) == (True, BAR_OPEN + 2 * M15)
    assert orders["positions"] == [{**POSITION, "plan_step": 0, "time_limit_epoch": 0,
                                    "intent_id": SECOND}]
    assert orders["pending_orders"] == [{**ORDER, "intent_id": None}]
    stats = body["outcomes"]["stats"]
    assert (stats["n"], stats["wins"], stats["losses"], stats["unlinked"]) == (3, 1, 2, 1)
    assert (stats["total_pnl"], stats["avg_r"], stats["n_r"], stats["n_label"]) == (5.4, 0.5, 2, 2)
    assert [o["intent_id"] for o in body["outcomes"]["recent"]] == [THIRD, SECOND, FIRST]
    realised = body["outcomes"]["realised"]
    assert realised["login"] == LOGIN and body["outcomes"]["invalid"] == []
    assert realised["periods"]["daily"]["breaker_realized"] == 15.8     # 20.8 and an orphan -5
    assert (realised["periods"]["weekly"]["realized"], realised["periods"]["weekly"]["trades"]) == (
        10.4, 2)


def test_operator_activity_reaches_the_session_block(tmp_path: Path) -> None:
    counts = CycleSummary(0, {}, {}, 0)
    tables = OverviewTables(
        halted=False, active_session=None, trading_day="2026-09-16", day_counts=counts,
        day_sessions=(), week_hold_reasons={}, breakers=(), recent=(), label_stats=(),
        market=None, invalid_outcomes=("XAUUSD-V6B-zzzzzzzzzzzz",))
    activity = OperatorActivity(last_agent="codex", last_seen_at=RECEIVED_AT - 30.04,
                                pending_cycle_id="c-pending", pending_deadline=RECEIVED_AT - 5)
    state = OverviewState(now=RECEIVED_AT, active=True, settings=control_settings(tmp_path),
                          ea=EaState().view(), command=None, operator=activity)

    body = build_overview(tables, state)

    assert body["session"]["operator"] == {  # type: ignore[index]
        "agents": list(OPERATOR_AGENTS), "last_agent": "codex", "last_seen_age_s": 30.0,
        "pending_cycle_id": "c-pending", "pending_seconds_left": 0}
    assert body["outcomes"]["invalid"] == ["XAUUSD-V6B-zzzzzzzzzzzz"]  # type: ignore[index]
    assert body["outcomes"]["realised"] is None  # type: ignore[index]


def test_the_page_renders_the_trading_day_as_text(tmp_path: Path, client: TestClient,
                                                  wired: ControlApp, trading: Journal) -> None:
    _seed_trading(trading, wired)
    assert wired.container is not None and wired.plane is not None
    container = wired.container
    tables = read_tables(wired.plane.ledger, db_path=container.ledger_v6.path,
                         halt_path=container.halt_path, now=RECEIVED_AT)
    state = OverviewState(
        now=RECEIVED_AT, active=True, settings=container.settings, ea=container.ea_state.view(),
        command=None, csrf_nonce="n", operator=OperatorActivity(
            last_agent="codex", last_seen_at=RECEIVED_AT - 30, pending_cycle_id="c-pending",
            pending_deadline=RECEIVED_AT + 95))

    page = _render_page(tmp_path, build_overview(tables, state))

    text = {element: page[element]["text"] for element in page}
    assert (text["v6-error"], text["v6-status"], text["v6-tile-session"]) == ("", "RUNNING", "None")
    assert SECOND in text["v6-intents"] and "published" in text["v6-intents"]
    assert "+2.00R" in text["v6-intents"] and "CLOSED" in text["v6-intents"]
    assert page["v6-intents"]["rows"] == 2 and page["v6-outcomes"]["rows"] == 3
    assert "filled" in text["v6-executions"] and "85 ms" in text["v6-executions"]
    assert HOSTILE_COMMENT in text["v6-orders"] and "$1.30" in text["v6-positions"]
    assert "codex" in text["v6-operator"] and "95 s left" in text["v6-operator"]
    assert "33.3%" in text["v6-outcome-stats"] and "n = 2" in text["v6-outcome-stats"]
    assert "unlinked" in text["v6-outcomes"] and "-1.00R" in text["v6-outcomes"]
    assert "login 12345" in text["v6-realised"] and "$15.80" in text["v6-realised"]
    assert "1 result(s) name no known intent" in text["v6-outcome-note"]
