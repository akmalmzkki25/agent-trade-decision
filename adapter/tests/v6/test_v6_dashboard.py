"""
/v6 dashboard page, script and overview JSON (plan section 8).

`build_control_app` wires the control and dashboard routers exactly as the
integrator will: container on `app.state`, control plane beside it, and the
V6 lifespan turning the runtime on. The control and CLI tests reuse it.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import v6_control, v6_dashboard
from app.routes.v6_control import install_control_plane
from app.v6.clock import FakeClock
from app.v6.config import OPERATOR_AGENTS, V6Settings
from app.v6.container import APP_STATE_KEY, V6Container, container_for_app, v6_lifespan
from app.v6.cycle_types import ViewRecord
from app.v6.dashboard_queries import (
    LatestMarket, OverviewState, OverviewTables, build_overview, load_latest_market,
    runtime_status, sizing_floor,
)
from app.v6.ledger_cycles import LedgerCycles
from app.v6.ledger_cycles_schema import CycleRecord, CycleSummary, CycleWithViews
from app.v6.ledger_intents import NewIntent
from app.v6.ledger_v6 import SnapshotRecord
from app.v6.runtime.ea_state import EaState
from app.v6.runtime.sessions import ControlPlane, build_control_plane
from app.v6.schemas.agents import validate_view
from app.v6.types import SymbolSpec

from .cycle_fixtures_v6 import CANDIDATE_ID, enter_result, hold_result, pa_payload
from .test_ledger_plan_actions import ACTION_ID, INTENT_ID, action_row
from .payloads_v6 import (
    BAR_OPEN, M15, RECEIVED_AT, as_poll, as_snapshot, poll_payload, snapshot_payload,
)

LOOPBACK = ("127.0.0.1", 50123)
TOKEN = "operator-" + "k" * 40
JSON = {"Content-Type": "application/json"}
AUTH = {**JSON, "Authorization": f"Bearer {TOKEN}"}


@dataclass(frozen=True)
class ControlApp:
    app: FastAPI
    container: V6Container | None
    plane: ControlPlane | None
    ledger: LedgerCycles | None


def control_settings(tmp_path: Path, **overrides: Any) -> V6Settings:
    values: dict[str, Any] = {"enabled": True, "halt_file": str(tmp_path / "V6_HALT"),
                              "operator_token": TOKEN}
    return V6Settings(_env_file=None, **(values | overrides))


def build_control_app(settings: V6Settings, db_path: Path, clock: FakeClock) -> ControlApp:
    container = container_for_app(settings, db_path, clock)
    ledger = None if container is None else LedgerCycles(db_path)
    plane = None if container is None or ledger is None else build_control_plane(
        ledger=ledger, control_log=container.ledger_v6, ea_state=container.ea_state)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with v6_lifespan(app):
            yield
        if ledger is not None:
            ledger.close()

    app = FastAPI(lifespan=lifespan)
    setattr(app.state, APP_STATE_KEY, container)
    install_control_plane(app, plane)
    app.include_router(v6_control.router)
    app.include_router(v6_dashboard.router)
    return ControlApp(app=app, container=container, plane=plane, ledger=ledger)


def post(client: TestClient, path: str, body: dict[str, Any] | bytes | None = None,
         headers: dict[str, str] | None = None):
    content = body if isinstance(body, bytes) else json.dumps(body or {}).encode("utf-8")
    return client.post(path, content=content, headers=AUTH if headers is None else headers)


def record_demo_poll(container: V6Container, trade_mode: str = "DEMO") -> None:
    container.ea_state.record_poll(as_poll(poll_payload(trade_mode=trade_mode)),
                                   container.clock.now_epoch())


APP_DIR = Path(__file__).resolve().parents[2] / "app"
TEMPLATE = APP_DIR / "templates" / "v6.html"
SCRIPT = APP_DIR / "static" / "v6.js"
RENDER_SCRIPT = APP_DIR / "static" / "v6_render.js"
SCRIPTS = (RENDER_SCRIPT, SCRIPT)          # the page loads them in this order
MAX_LINES = 400
HOSTILE_NOTE = '<img src=x onerror="alert(1)">'
FORBIDDEN_JS_SINKS = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                      "eval(", "new Function")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(epoch=RECEIVED_AT)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "dashboard.db"


@pytest.fixture
def wired(tmp_path: Path, db_path: Path, clock: FakeClock) -> ControlApp:
    return build_control_app(control_settings(tmp_path), db_path, clock)


@pytest.fixture
def client(wired: ControlApp) -> Iterator[TestClient]:
    with TestClient(wired.app, client=LOOPBACK) as test_client:
        yield test_client


def _store_snapshot(wired: ControlApp, snapshot_id: str = "snap-dash",
                    bar_open: int = BAR_OPEN) -> None:
    assert wired.container is not None
    payload = snapshot_payload(snapshot_id, bar_open=bar_open, bars={})
    record = SnapshotRecord.from_snapshot(as_snapshot(payload), json.dumps(payload),
                                          RECEIVED_AT)
    assert wired.container.ledger_v6.insert_snapshot(record)


def _hostile_enter(cycle_id: str):
    view = validate_view("price_action", {**pa_payload(), "ranked": [
        {**pa_payload()["ranked"][0], "note": HOSTILE_NOTE}]}, {CANDIDATE_ID})
    record = ViewRecord(role="price_action", source="rules", view=view)
    return replace(enter_result(cycle_id, bar_open=BAR_OPEN),
                   view_records=(record,) + enter_result(cycle_id).view_records[1:])


# --- page and script -------------------------------------------------------------
def test_page_renders_with_the_csrf_nonce_when_v6_runs(client: TestClient,
                                                       wired: ControlApp) -> None:
    assert wired.plane is not None
    response = client.get("/v6")
    assert response.status_code == 200
    assert f'data-csrf="{wired.plane.csrf_nonce}"' in response.text
    assert 'data-enabled="true"' in response.text
    tags = [f'<script src="/v6/static/{script.name}" defer></script>' for script in SCRIPTS]
    assert all(tag in response.text for tag in tags)
    assert response.text.index(tags[0]) < response.text.index(tags[1])
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    banner = re.search(r'<div id="v6-disabled"[^>]*>', response.text)
    assert banner is not None and "hidden" in banner.group(0)


def test_page_renders_a_disabled_banner_without_a_nonce(tmp_path: Path, db_path: Path,
                                                        clock: FakeClock) -> None:
    wired = build_control_app(control_settings(tmp_path, enabled=False), db_path, clock)
    with TestClient(wired.app, client=LOOPBACK) as off:
        page = off.get("/v6")
        overview = off.get("/v6/api/overview")
    assert page.status_code == 200
    assert 'data-csrf=""' in page.text and 'data-enabled="false"' in page.text
    assert "V6 is not running in this process." in page.text
    assert overview.status_code == 404


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_the_scripts_are_served(client: TestClient, wired: ControlApp, script: Path) -> None:
    url = f"/v6/static/{script.name}"
    if not any(getattr(route, "path", "") == url for route in wired.app.routes):
        pytest.skip(f"{url} is not routed yet (integrator: routes/v6_dashboard.py)")
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.content == script.read_bytes()


def test_templates_and_scripts_stay_within_the_safety_rules() -> None:
    template = TEMPLATE.read_text(encoding="utf-8")
    assert not re.search(r"\|\s*safe\b", template)
    assert "autoescape" not in template and len(template.splitlines()) <= MAX_LINES
    for script in SCRIPTS:
        source = script.read_text(encoding="utf-8")
        assert "textContent" in source and len(source.splitlines()) <= MAX_LINES
        for sink in FORBIDDEN_JS_SINKS:
            assert sink not in source, (script.name, sink)


# --- overview --------------------------------------------------------------------
def test_overview_before_any_data(client: TestClient) -> None:
    response = client.get("/v6/api/overview")
    body = response.json()
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert body["runtime"]["status"] == "WAITING_EA"
    assert (body["runtime"]["mode"], body["runtime"]["backend"]) == ("shadow", "rules")
    assert body["ea"]["stale"] is True and body["ea"]["trade_mode"] is None
    assert body["session"]["active"] is None and body["session"]["trading_day"] == "2026-09-16"
    assert body["sizing"]["available"] is False
    assert (body["last_cycle"], body["breakers"], body["recent_cycles"]) == (None, [], [])
    assert (body["label_stats"], body["hold_reasons_7d"]) == ([], {})
    assert (body["intents"], body["executions"]) == ([], [])
    assert (body["actions"], body["plan"]) == ([], None)
    assert body["open_orders"]["available"] is False and body["open_orders"]["positions"] == []
    outcomes = body["outcomes"]
    assert (outcomes["stats"]["n"], outcomes["recent"], outcomes["realised"]) == (0, [], None)
    assert body["session"]["armed"] is False and body["runtime"]["operator_ready"] is True
    assert body["session"]["operator"] == {
        "agents": list(OPERATOR_AGENTS), "last_agent": None, "last_seen_age_s": None,
        "pending_cycle_id": None, "pending_seconds_left": None}
    assert "operator_token" not in response.text and "k" * 40 not in response.text
    assert "budget" not in response.text.lower() and "openrouter" not in response.text.lower()


def test_the_overview_lists_actions_and_the_active_plan(client: TestClient,
                                                        wired: ControlApp) -> None:
    assert wired.ledger is not None
    wired.ledger.intents.insert(NewIntent(
        intent_id=INTENT_ID, cycle_id="c-0123456789abcdef", session_id="a1b2c3d4e5f6",
        agent="claude_code", source="operator", side="buy", order_type="BUY_STOP",
        entry=4303.5, sl=4296.5, tp=4317.5, lots=0.01, risk_usd=7.4,
        valid_until_epoch=int(RECEIVED_AT) + 120, pending_expiry_epoch=int(RECEIVED_AT) + 1800,
        time_barrier_s=9000, created_at=RECEIVED_AT, tp1=4307.5, tp2=4311.5,
        sl_after_tp1=4304.0, sl_after_tp2=4307.5))
    wired.ledger.actions.insert(action_row(detail=HOSTILE_NOTE))
    body = client.get("/v6/api/overview").json()
    (action,) = body["actions"]
    assert (action["action_id"], action["status"], action["detail"]) == (
        ACTION_ID, "PUBLISHED", HOSTILE_NOTE)
    plan = body["plan"]
    assert (plan["intent_id"], plan["order_type"], plan["tp1"], plan["sl_after_tp2"]) == (
        INTENT_ID, "BUY_STOP", 4307.5, 4307.5)
    assert (plan["plan_step"], plan["time_barrier_s"]) == (0, 9000)


def _seed_day(client: TestClient, wired: ControlApp) -> None:
    """A DEMO EA, a stored snapshot, an open session, two cycles and one label."""
    assert wired.container is not None and wired.ledger is not None
    record_demo_poll(wired.container)
    _store_snapshot(wired)
    assert post(client, "/v6/control/session", {"action": "start"}).status_code == 200
    wired.ledger.record_cycle(hold_result("c-hold", bar_open=BAR_OPEN - M15), RECEIVED_AT)
    wired.ledger.record_cycle(_hostile_enter("c-enter"), RECEIVED_AT)
    wired.ledger.write_label(CANDIDATE_ID, label_status="labeled", outcome="tp",
                             outcome_r=2.0, labeled_at=RECEIVED_AT)


def test_overview_with_cycles_views_and_labels(client: TestClient, wired: ControlApp) -> None:
    _seed_day(client, wired)

    body = client.get("/v6/api/overview").json()

    assert body["runtime"]["status"] == "RUNNING"
    assert body["ea"]["trade_mode"] == "DEMO" and body["ea"]["last_seen_age_s"] == 0.0
    assert body["session"]["active"]["backend"] == "rules"
    day = body["session"]["day"]
    assert (day["cycles"], day["shadow_intents"]) == (2, 1)
    assert body["hold_reasons_7d"] == {"APP-V6-GATE": 1}
    assert body["last_cycle"]["cycle_id"] == "c-enter"
    assert body["last_cycle"]["gates"][0] == {"code": "SPREAD", "passed": True, "value": 20,
                                              "limit": 35, "detail": ""}
    cycles = body["recent_cycles"]
    assert [c["cycle_id"] for c in cycles] == ["c-enter", "c-hold"]
    assert cycles[1]["failed_gates"] == ["SPREAD"] and cycles[1]["views"] == []
    enter = cycles[0]
    assert enter["shadow_intent"]["lots"] == 0.01 and enter["protocol"]["action"] == "ENTER"
    assert enter["candidates"][0] == {
        "candidate_id": CANDIDATE_ID, "setup": "displacement", "side": "buy", "entry": 4300.0,
        "verdict": "chosen", "stop": 4289.8, "target": 4320.4, "refusal": None}
    assert [v["role"] for v in enter["views"]] == ["price_action", "news_risk", "chief"]
    assert enter["views"][0]["view"]["ranked"][0]["note"] == HOSTILE_NOTE
    assert enter["views"][2]["error_code"] == "PROVIDER_TIMEOUT"
    assert enter["views"][2]["model"] == "m" and "cost_usd" not in enter["views"][2]
    assert body["label_stats"] == [{"setup": "displacement", "verdict": "chosen",
                                    "label_status": "labeled", "outcome": "tp", "count": 1,
                                    "mean_r": 2.0}]
    sizing = body["sizing"]
    assert (sizing["available"], sizing["min_tradeable_equity"], sizing["stop_floor"]) == (
        True, 1280.0, 6.0)
    assert (sizing["tick_value"], sizing["reported_tick_value"]) == (1.0, 0.1)
    assert sizing["tick_value_source"] == "order_calc" and sizing["tradeable_at_basis"]


def test_overview_shows_breakers_and_the_pending_command(client: TestClient,
                                                         wired: ControlApp) -> None:
    assert wired.ledger is not None and wired.plane is not None
    _seed_day(client, wired)
    wired.ledger.trip_breaker("daily", "2026-09-16", "LOSS_3PCT", RECEIVED_AT)
    wired.plane.commands.request_cancel_pending("test", RECEIVED_AT)
    later = client.get("/v6/api/overview").json()
    assert later["runtime"]["status"] == "BREAKER"
    assert later["runtime"]["pending_command"]["command"] == "CANCEL_PENDING"
    assert later["breakers"] == [{"scope": "daily", "period_key": "2026-09-16",
                                  "reason": "LOSS_3PCT", "tripped_at": RECEIVED_AT}]


def test_overview_reports_halted_and_stale(client: TestClient, wired: ControlApp,
                                           clock: FakeClock) -> None:
    assert wired.container is not None
    record_demo_poll(wired.container)
    clock.advance(wired.container.settings.ea_stale_s + 1)
    stale = client.get("/v6/api/overview").json()
    assert stale["runtime"]["status"] == "STALE" and stale["ea"]["stale"] is True
    wired.container.halt_path.write_text("{}", encoding="utf-8")
    halted = client.get("/v6/api/overview").json()
    assert halted["runtime"]["status"] == "HALTED" and halted["runtime"]["halted"] is True


def test_overview_storage_failure_is_503(client: TestClient,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_: object, **__: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(v6_dashboard, "read_tables", broken)
    response = client.get("/v6/api/overview")
    assert response.status_code == 503
    assert response.json()["detail"] == "V6 storage unavailable"


# --- queries -----------------------------------------------------------------------
def test_latest_market_skips_pruned_and_survives_unreadable_payloads(
        wired: ControlApp, caplog: pytest.LogCaptureFixture) -> None:
    assert wired.container is not None
    ledger_v6 = wired.container.ledger_v6
    assert load_latest_market(ledger_v6.path) is None
    _store_snapshot(wired, "snap-old", BAR_OPEN - M15)
    market = load_latest_market(ledger_v6.path)
    assert market is not None and market.snapshot_id == "snap-old"
    assert round(market.spread_price, 2) == 0.2

    ledger_v6.prune_snapshots(BAR_OPEN)
    assert load_latest_market(ledger_v6.path) is None

    broken = SnapshotRecord("snap-bad", RECEIVED_AT, BAR_OPEN, "1", "DEMO", "s", 1.0, 1.0,
                            20, 0.0, "0" * 64, "[]")
    assert ledger_v6.insert_snapshot(broken)
    invalid = replace(broken, snapshot_id="snap-invalid", bar_open_epoch=BAR_OPEN + M15,
                      payload_json='{"symbol_spec": {"digits": "two"}, "quote": {}}')
    with caplog.at_level(logging.WARNING):
        assert load_latest_market(ledger_v6.path) is None
        assert "snap-bad" in caplog.text and "ValidationError" in caplog.text
        assert ledger_v6.insert_snapshot(invalid)
        assert load_latest_market(ledger_v6.path) is None
    assert "snap-invalid" in caplog.text and "ValidationError" in caplog.text


def _spec(**overrides: float) -> SymbolSpec:
    values = {"digits": 2, "point": 0.01, "tick_size": 0.01, "tick_value": 1.0,
              "tick_value_loss": 1.0, "contract_size": 100.0, "volume_min": 0.01,
              "volume_step": 0.01, "volume_max": 100.0}
    return SymbolSpec(**(values | overrides))  # type: ignore[arg-type]


def test_sizing_floor_uses_the_widest_floor_and_reports_unsizeable_specs(
        tmp_path: Path) -> None:
    settings = control_settings(tmp_path, risk_pct=0.25, sizing_equity_basis_usd=2000.0)
    wide_spread = LatestMarket("s1", BAR_OPEN, _spec(), spread_price=0.9)
    sized = sizing_floor(wide_spread, settings)
    # floor = max(6.00, 10 x 0.90, 0.40 / 0.10) = 9.00; loss/lot 940 -> $9.40 at 0.25 %
    assert (sized["stop_floor"], sized["min_tradeable_equity"]) == (9.0, 3760.0)
    assert sized["tradeable_at_basis"] is False

    unsizeable = sizing_floor(LatestMarket("s2", BAR_OPEN, _spec(tick_value_loss=0.0), 0.2),
                              settings)
    assert unsizeable["available"] is False and unsizeable["snapshot_id"] == "s2"
    assert unsizeable["reason"] == "symbol spec cannot be sized"


@pytest.mark.parametrize(("kwargs", "expected"), [
    ({"active": False, "halted": True, "breakers_tripped": True, "ea_age_s": 1.0}, "DISABLED"),
    ({"active": True, "halted": True, "breakers_tripped": True, "ea_age_s": 1.0}, "HALTED"),
    ({"active": True, "halted": False, "breakers_tripped": True, "ea_age_s": 1.0}, "BREAKER"),
    ({"active": True, "halted": False, "breakers_tripped": False, "ea_age_s": None},
     "WAITING_EA"),
    ({"active": True, "halted": False, "breakers_tripped": False, "ea_age_s": 31.0}, "STALE"),
    ({"active": True, "halted": False, "breakers_tripped": False, "ea_age_s": 30.0}, "RUNNING"),
])
def test_runtime_status_precedence(kwargs: dict[str, object], expected: str) -> None:
    assert runtime_status(ea_stale_s=30.0, **kwargs) == expected  # type: ignore[arg-type]


def test_overview_tolerates_sparse_cycle_summaries(tmp_path: Path) -> None:
    record = CycleRecord("c-x", "snap-x", BAR_OPEN, "ERROR", "APP-V6-ERROR", "rules", "rules",
                         "failed", None, 0, json.dumps({"gates": "n/a", "candidates": None}),
                         RECEIVED_AT)
    counts = CycleSummary(0, MappingProxyType({}), MappingProxyType({}), 0)
    tables = OverviewTables(
        halted=False, active_session=None, trading_day="2026-09-16", day_counts=counts,
        day_sessions=(), week_hold_reasons=MappingProxyType({}), breakers=(),
        recent=(CycleWithViews(record, ()),), label_stats=(), market=None)
    state = OverviewState(now=RECEIVED_AT, active=True, settings=control_settings(tmp_path),
                          ea=EaState().view(), command=None)

    body = build_overview(tables, state)

    cycle = body["recent_cycles"][0]  # type: ignore[index]
    assert (cycle["failed_gates"], cycle["candidates"], cycle["hold_detail"]) == ([], [], "")
    assert body["last_cycle"]["gates"] == []  # type: ignore[index]
    assert body["ea"]["last_snapshot"] is None  # type: ignore[index]
    json.dumps(body)
