"""
EA-facing V6 routes, exercised through a real app with the lifespan running.

Each test builds its own app on a temporary database with an injected
`FakeClock`, so nothing here touches the global adapter ledger or V6 settings.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.main import app as global_app
from app.main import create_app
from app.settings import settings as adapter_settings
from app.v6.clock import FakeClock
from app.v6.config import OPERATOR_AGENTS, V6Settings

from .payloads_v6 import (
    BAR_OPEN,
    M15,
    RECEIVED_AT,
    backfill_payload,
    closed_bar_blocks,
    execution_payload,
    poll_payload,
    snapshot_payload,
)

JSON_HEADERS = {"Content-Type": "application/json"}
V6_POST_PATHS = ("/v6/bars/backfill", "/v6/snapshot", "/v6/intent/poll", "/v6/execution")
OPERATOR_TOKEN = "operator-token-" + "q" * 40
EA_KEY = "ea-hmac-key-" + "z" * 40
SECRET_FRAGMENTS = ("token", "secret", "password", "api_key")


def _settings(tmp_path: Path, **overrides: Any) -> V6Settings:
    values: dict[str, Any] = {"enabled": True, "halt_file": str(tmp_path / "V6_HALT")}
    return V6Settings(_env_file=None, **(values | overrides))


def _build(tmp_path: Path, db_path: Path, clock: FakeClock, **overrides: Any) -> FastAPI:
    # Data-plane tests: the Phase 2 worker would consume the inbox these tests inspect.
    return create_app(v6_settings=_settings(tmp_path, **overrides), clock=clock,
                      v6_db_path=str(db_path), v6_tasks=False)


def _post(client: TestClient, path: str, body: dict[str, Any] | bytes, **headers: str):
    content = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    return client.post(path, content=content, headers={**JSON_HEADERS, **headers})


def _rows(db_path: Path, sql: str) -> list[tuple]:
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute(sql).fetchall()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(epoch=RECEIVED_AT)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "v6_routes.db"


@pytest.fixture
def client(tmp_path: Path, db_path: Path, clock: FakeClock) -> Iterator[TestClient]:
    with TestClient(_build(tmp_path, db_path, clock)) as test_client:
        yield test_client


# --- disabled ----------------------------------------------------------------
@pytest.mark.parametrize("path", V6_POST_PATHS)
def test_every_post_route_is_404_when_v6_is_disabled(tmp_path: Path, db_path: Path,
                                                     clock: FakeClock, path: str) -> None:
    app = _build(tmp_path, db_path, clock, enabled=False)
    with TestClient(app) as disabled:
        r = _post(disabled, path, b"not even json", **{"Content-Type": "text/plain"})
    assert r.status_code == 404
    assert r.json() == {"detail": "V6 disabled"}
    assert not db_path.exists(), "a disabled V6 must not open its database"


def test_status_is_404_when_v6_is_disabled(tmp_path: Path, db_path: Path,
                                           clock: FakeClock) -> None:
    with TestClient(_build(tmp_path, db_path, clock, enabled=False)) as disabled:
        assert disabled.get("/v6/status").status_code == 404


def test_the_default_app_keeps_v6_disabled_under_test() -> None:
    r = TestClient(global_app).get("/v6/status")
    assert r.status_code == 404
    assert r.json() == {"detail": "V6 disabled"}


# --- request guards ----------------------------------------------------------
@pytest.mark.parametrize("path", V6_POST_PATHS)
def test_non_json_content_type_is_415(client: TestClient, path: str) -> None:
    r = _post(client, path, b"{}", **{"Content-Type": "text/plain"})
    assert r.status_code == 415


@pytest.mark.parametrize("path", V6_POST_PATHS)
def test_cross_site_requests_are_403(client: TestClient, path: str) -> None:
    assert _post(client, path, b"{}", **{"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert _post(client, path, b"{}", Origin="https://evil.example").status_code == 403


@pytest.mark.parametrize("path", V6_POST_PATHS)
def test_oversized_bodies_are_413(client: TestClient, path: str) -> None:
    oversized = b" " * (adapter_settings.max_request_bytes + 1)
    assert _post(client, path, oversized).status_code == 413


@pytest.mark.parametrize("path", V6_POST_PATHS)
def test_malformed_or_unknown_bodies_are_400(client: TestClient, path: str) -> None:
    assert _post(client, path, b"{not json").status_code == 400
    r = _post(client, path, {"schema_version": "nope", "extra": 1})
    assert r.status_code == 400
    assert isinstance(r.json()["detail"], list)


def test_the_v1_v5_hmac_never_applies_to_v6_routes(client: TestClient,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """V6 routes use their own key and headers (wire contract section 1)."""
    key = "k" * 40
    monkeypatch.setattr(adapter_settings, "hmac_required", True)
    monkeypatch.setattr(adapter_settings, "internal_hmac_key", SecretStr(key))
    body = json.dumps(execution_payload()).encode("utf-8")
    assert _post(client, "/v6/execution", body).status_code == 200
    signature = hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert _post(client, "/v6/execution", body, **{"X-Internal-Sig": signature}).status_code == 200


# --- backfill ----------------------------------------------------------------
def test_backfill_stores_bars_and_reports_the_count(client: TestClient, db_path: Path) -> None:
    r = _post(client, "/v6/bars/backfill", backfill_payload("M5"))
    assert r.status_code == 200
    assert r.json() == {"accepted": 20}
    assert _rows(db_path, "SELECT COUNT(*) FROM v6_bars WHERE tf = 'M5'") == [(20,)]
    assert client.get("/v6/status").json()["bar_coverage"]["M5"]["cached"] == 20


@pytest.mark.parametrize("mutate", [
    lambda rows: rows.reverse(),                         # not increasing
    lambda rows: rows[0].__setitem__(2, rows[0][1] - 5),  # high below open
    lambda rows: rows[0].__setitem__(0, rows[0][0] + 1),  # off the grid
    lambda rows: rows[0].__setitem__(5, -1),             # negative tick volume
])
def test_backfill_rejects_invalid_rows(client: TestClient, db_path: Path, mutate) -> None:
    payload = backfill_payload()
    mutate(payload["rows"])
    r = _post(client, "/v6/bars/backfill", payload)
    assert r.status_code == 400
    assert _rows(db_path, "SELECT COUNT(*) FROM v6_bars") == [(0,)]


@pytest.mark.parametrize("symbol", ["XAUUSDm", "XAUUSD#", "XAUUSD+", "XAU-USD.r"])
def test_backfill_accepts_broker_symbol_suffixes(client: TestClient, symbol: str) -> None:
    payload = backfill_payload() | {"symbol": symbol}
    assert _post(client, "/v6/bars/backfill", payload).status_code == 200


@pytest.mark.parametrize("symbol", ["XA", "XAU USD", "XAU/USD", "XAUUSD<script>"])
def test_backfill_rejects_malformed_symbols(client: TestClient, symbol: str) -> None:
    payload = backfill_payload() | {"symbol": symbol}
    assert _post(client, "/v6/bars/backfill", payload).status_code == 400


def test_storage_failures_become_503(client: TestClient,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_: object) -> int:
        raise sqlite3.OperationalError("database is locked")

    container = client.app.state.v6_container
    monkeypatch.setattr(container.ledger_v6, "upsert_bars", broken)
    r = _post(client, "/v6/bars/backfill", backfill_payload())
    assert r.status_code == 503
    assert r.json() == {"detail": "V6 storage unavailable"}


# --- snapshot ----------------------------------------------------------------
def _expected_cycle_id(snapshot_id: str) -> str:
    return "c-" + hashlib.sha256(snapshot_id.encode("utf-8")).hexdigest()[:16]


def test_snapshot_is_accepted_persisted_and_queued(client: TestClient, db_path: Path) -> None:
    body = json.dumps(snapshot_payload()).encode("utf-8")
    r = _post(client, "/v6/snapshot", body)
    assert r.status_code == 202
    assert r.json() == {"accepted": True, "cycle_id": _expected_cycle_id("snap-0001"),
                        "server_time_epoch": int(RECEIVED_AT)}
    stored = _rows(db_path, "SELECT snapshot_id, clock_skew_s, payload_sha256, payload_json"
                            " FROM v6_snapshots")
    assert stored == [("snap-0001", RECEIVED_AT - (BAR_OPEN + M15),
                       hashlib.sha256(body).hexdigest(), body.decode("utf-8"))]
    counts = dict(_rows(db_path, "SELECT tf, COUNT(*) FROM v6_bars GROUP BY tf"))
    assert counts == {"H1": 8, "M15": 16, "M5": 48}
    status = client.get("/v6/status").json()
    assert status["inbox_pending"] == 1
    assert status["last_snapshot"]["snapshot_id"] == "snap-0001"


def test_duplicate_snapshot_is_200_with_the_same_cycle_id(client: TestClient,
                                                          db_path: Path) -> None:
    first = _post(client, "/v6/snapshot", snapshot_payload())
    again = _post(client, "/v6/snapshot", snapshot_payload())
    assert again.status_code == 200
    assert again.json() == {"accepted": False, "duplicate": True,
                            "cycle_id": first.json()["cycle_id"]}
    assert _rows(db_path, "SELECT COUNT(*) FROM v6_snapshots") == [(1,)]
    assert client.get("/v6/status").json()["superseded"] == 0


def test_duplicate_found_only_at_insert_is_still_reported(client: TestClient,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    _post(client, "/v6/snapshot", snapshot_payload())
    container = client.app.state.v6_container
    monkeypatch.setattr(container.ledger_v6, "snapshot_exists", lambda _id: False)
    r = _post(client, "/v6/snapshot", snapshot_payload())
    assert r.status_code == 200
    assert r.json()["duplicate"] is True
    assert client.get("/v6/status").json()["inbox_pending"] == 1


def test_a_newer_snapshot_supersedes_an_unconsumed_one(client: TestClient) -> None:
    _post(client, "/v6/snapshot", snapshot_payload("snap-0001"))
    r = _post(client, "/v6/snapshot", snapshot_payload("snap-0002", bar_open=BAR_OPEN + M15))
    assert r.status_code == 202
    status = client.get("/v6/status").json()
    assert status["superseded"] == 1
    assert status["inbox_pending"] == 1
    assert status["last_snapshot"]["snapshot_id"] == "snap-0002"


def test_snapshot_with_a_still_open_bar_is_rejected(client: TestClient, db_path: Path) -> None:
    bars = closed_bar_blocks()
    forming = list(bars["M15"][-1])
    forming[0] = BAR_OPEN + M15
    bars["M15"].append(forming)
    r = _post(client, "/v6/snapshot", snapshot_payload(bars=bars))
    assert r.status_code == 400
    assert "not closed" in json.dumps(r.json())
    assert _rows(db_path, "SELECT COUNT(*) FROM v6_snapshots") == [(0,)]
    assert _rows(db_path, "SELECT COUNT(*) FROM v6_bars") == [(0,)]


def test_snapshot_without_bars_is_accepted(client: TestClient, db_path: Path) -> None:
    assert _post(client, "/v6/snapshot", snapshot_payload(bars={})).status_code == 202
    assert _rows(db_path, "SELECT COUNT(*) FROM v6_bars") == [(0,)]


# --- poll --------------------------------------------------------------------
def test_poll_returns_no_intent_and_marks_the_account_once_per_minute(
        client: TestClient, db_path: Path, clock: FakeClock) -> None:
    r = _post(client, "/v6/intent/poll", poll_payload())
    assert r.status_code == 200
    body = r.json()
    assert body["schema_version"] == "v6.intent.1"
    assert body["has_intent"] is False
    assert body["command"] == "NONE"
    assert body["server_time_epoch"] == int(RECEIVED_AT)
    clock.advance(2)
    _post(client, "/v6/intent/poll", poll_payload(equity=2020.0))
    marks = _rows(db_path, "SELECT minute_epoch, equity FROM v6_account_marks")
    assert marks == [(int(RECEIVED_AT) // 60 * 60, 2010.5)]
    clock.advance(60)
    _post(client, "/v6/intent/poll", poll_payload(equity=2030.0))
    assert _rows(db_path, "SELECT COUNT(*) FROM v6_account_marks") == [(2,)]


def test_a_failed_mark_is_retried_on_the_next_poll(client: TestClient, db_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = client.app.state.v6_container.ledger_v6
    real = ledger.record_account_mark

    def broken(*_: object) -> bool:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(ledger, "record_account_mark", broken)
    assert _post(client, "/v6/intent/poll", poll_payload()).status_code == 200
    monkeypatch.setattr(ledger, "record_account_mark", real)
    assert _post(client, "/v6/intent/poll", poll_payload()).status_code == 200
    assert _rows(db_path, "SELECT COUNT(*) FROM v6_account_marks") == [(1,)]


def test_halt_file_turns_the_poll_into_cancel_pending(client: TestClient,
                                                      tmp_path: Path) -> None:
    (tmp_path / "V6_HALT").write_text("halt", encoding="utf-8")
    r = _post(client, "/v6/intent/poll", poll_payload())
    assert r.status_code == 200
    assert r.json()["command"] == "CANCEL_PENDING"
    assert r.json()["has_intent"] is False
    assert client.get("/v6/status").json()["halt_file_present"] is True


# --- execution ---------------------------------------------------------------
def test_execution_report_is_persisted_once(client: TestClient, db_path: Path) -> None:
    for _ in range(2):
        r = _post(client, "/v6/execution", execution_payload())
        assert r.status_code == 200
        assert r.json() == {"ok": True}
    rows = _rows(db_path, "SELECT intent_id, status, received_at FROM v6_executions")
    assert rows == [("abcdefgh2345", "filled", RECEIVED_AT)]


# --- status ------------------------------------------------------------------
def test_status_reports_runtime_ea_and_market_state(client: TestClient,
                                                    clock: FakeClock) -> None:
    empty = client.get("/v6/status").json()
    assert empty["ea_last_seen_age_s"] is None
    assert empty["last_snapshot"] is None
    assert empty["trade_mode"] is None
    _post(client, "/v6/snapshot", snapshot_payload())
    _post(client, "/v6/intent/poll", poll_payload(trade_mode="CONTEST"))
    clock.advance(4)
    status = client.get("/v6/status").json()
    assert status["enabled"] is True
    assert (status["mode"], status["backend"]) == ("shadow", "rules")
    assert status["operator_agents"] == list(OPERATOR_AGENTS)
    assert (status["operator_ready"], status["ea_signing"]) == (False, "off")
    assert status["ea_last_seen_age_s"] == pytest.approx(4.0)
    assert status["trade_mode"] == "CONTEST"
    assert status["last_snapshot"] == {
        "snapshot_id": "snap-0001", "cycle_id": _expected_cycle_id("snap-0001"),
        "bar_open_epoch": BAR_OPEN, "age_s": pytest.approx(4.0)}
    assert status["bar_coverage"]["M15"]["count"] == 16
    assert status["bar_coverage"]["M15"]["cached"] == 16
    assert set(status["bar_coverage"]) == {"M1", "M5", "M15", "H1", "D1"}
    assert status["warm"] is False
    assert status["halt_file_present"] is False


def _keys(node: object) -> list[str]:
    if isinstance(node, dict):
        return [str(k) for k in node] + [k for v in node.values() for k in _keys(v)]
    if isinstance(node, list):
        return [k for item in node for k in _keys(item)]
    return []


def test_status_never_contains_secret_material(tmp_path: Path, db_path: Path,
                                               clock: FakeClock) -> None:
    app = _build(tmp_path, db_path, clock, backend="operator",
                 operator_token=SecretStr(OPERATOR_TOKEN), ea_hmac_key=SecretStr(EA_KEY))
    with TestClient(app) as secret_client:
        r = secret_client.get("/v6/status")
    assert r.status_code == 200
    assert OPERATOR_TOKEN not in r.text
    assert EA_KEY not in r.text
    assert (r.json()["operator_ready"], r.json()["ea_signing"]) == (True, "available")
    keys = " ".join(_keys(r.json())).lower()
    assert not [fragment for fragment in SECRET_FRAGMENTS if fragment in keys]


# --- single runtime ----------------------------------------------------------
def test_a_second_app_on_the_same_database_stays_disabled(
        client: TestClient, tmp_path: Path, db_path: Path, clock: FakeClock,
        caplog: pytest.LogCaptureFixture) -> None:
    holder = client.app.state.v6_container.holder
    with caplog.at_level(logging.ERROR, logger="app.v6.container"):
        with TestClient(_build(tmp_path, db_path, clock)) as second:
            assert second.get("/v6/status").status_code == 404
            assert _post(second, "/v6/intent/poll", poll_payload()).status_code == 404
    assert "runtime lock" in caplog.text
    assert holder in caplog.text
    assert client.get("/v6/status").status_code == 200
    assert _rows(db_path, "SELECT holder FROM v6_runtime_lock") == [(holder,)]


def test_shutdown_releases_the_runtime_lock(tmp_path: Path, db_path: Path,
                                            clock: FakeClock) -> None:
    with TestClient(_build(tmp_path, db_path, clock)) as first:
        assert _rows(db_path, "SELECT COUNT(*) FROM v6_runtime_lock") == [(1,)]
        assert first.get("/v6/status").status_code == 200
    assert _rows(db_path, "SELECT COUNT(*) FROM v6_runtime_lock") == [(0,)]
    with TestClient(_build(tmp_path, db_path, clock)) as restarted:
        assert restarted.get("/v6/status").status_code == 200
