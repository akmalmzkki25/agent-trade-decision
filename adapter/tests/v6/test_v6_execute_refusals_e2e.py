"""
Execute-mode refusals end to end: unsigned, forged, stale and replayed EA requests,
a REAL account, an operator that never answers, and shadow mode.

HARD RULE (user decision 4): the operator backend decides for DEMO accounts only.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.v6 import wire
from app.v6.clock import FakeClock
from app.v6.schemas.intent import PollResponse

from . import engine_fixtures_v6 as ef
from .execute_fixtures_v6 import (
    EA_KEY, JSON, POINT, TOKEN, Adapter, basket_result, enter_decision, execute_settings,
    fixed_candidate, running,
)
from .payloads_v6 import poll_payload

POLL_PATH = "/v6/intent/poll"


def _signed_headers(ts: int, raw: bytes, path: str = POLL_PATH) -> dict[str, str]:
    return {**JSON, wire.TS_HEADER: str(ts),
            wire.SIG_HEADER: wire.sign_request(EA_KEY, ts, "POST", path, raw)}


def _refusal(adapter: Adapter, headers: dict[str, str], raw: bytes) -> str:
    response = adapter.client.post(POLL_PATH, content=raw, headers=headers)
    assert response.status_code == 401, response.text
    return response.json()["detail"]


def test_unsigned_forged_stale_and_replayed_ea_requests_are_401(
        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    clock = FakeClock(epoch=float(ef.AS_OF + 1))
    raw = json.dumps(poll_payload()).encode("utf-8")
    now = int(clock.now_epoch())
    with caplog.at_level(logging.DEBUG), \
            running(tmp_path, execute_settings(tmp_path), clock) as adapter:
        codes = [
            _refusal(adapter, dict(JSON), raw),
            _refusal(adapter, {**_signed_headers(now, raw), wire.TS_HEADER: "1e9"}, raw),
            _refusal(adapter, _signed_headers(now - 31, raw), raw),
            _refusal(adapter, {**_signed_headers(now, raw), wire.SIG_HEADER: "0" * 64}, raw),
            _refusal(adapter, _signed_headers(now, raw, "/v6/execution"), raw),
        ]
        assert adapter.client.get("/v6/status").json()["trade_mode"] is None
        good = _signed_headers(now, raw)
        assert adapter.client.post(POLL_PATH, content=raw, headers=good).status_code == 200
        codes.append(_refusal(adapter, good, raw))
        body = adapter.client.get("/v6/status").json()
    assert codes == ["SIG_MISSING", "SIG_BAD_TS", "SIG_STALE", "SIG_MISMATCH", "SIG_MISMATCH",
                     "SIG_REPLAY"]
    assert body["trade_mode"] == "DEMO"
    assert EA_KEY not in caplog.text and TOKEN not in caplog.text
    assert good[wire.SIG_HEADER] not in caplog.text


EA_ROUTES = ("/v6/bars/backfill", "/v6/snapshot", "/v6/intent/poll", "/v6/execution",
             "/v6/basket-result")


def test_every_ea_route_refuses_an_unsigned_request(tmp_path: Path) -> None:
    clock = FakeClock(epoch=float(ef.AS_OF + 1))
    with running(tmp_path, execute_settings(tmp_path), clock) as adapter:
        refused = {path: adapter.ea_post(path, {}, sign=False) for path in EA_ROUTES}
        forged = {path: adapter.client.post(path, content=b"{}", headers={
            **JSON, wire.TS_HEADER: str(int(clock.now_epoch())),
            wire.SIG_HEADER: "f" * 64}) for path in EA_ROUTES}
    assert {path: (r.status_code, r.json()["detail"]) for path, r in refused.items()} == {
        path: (401, "SIG_MISSING") for path in EA_ROUTES}
    assert {path: (r.status_code, r.json()["detail"]) for path, r in forged.items()} == {
        path: (401, "SIG_MISMATCH") for path in EA_ROUTES}


def test_the_v6_basket_route_takes_only_signed_v6_results(tmp_path: Path) -> None:
    clock = FakeClock(epoch=float(ef.AS_OF + 1))
    result = basket_result("abcdefgh2345", "2026-09-17T13:00:00Z", 5.0)
    with running(tmp_path, execute_settings(tmp_path), clock) as adapter:
        assert adapter.ea_post("/v6/basket-result", result, sign=False).status_code == 401
        v5 = adapter.ea_post("/v6/basket-result", result | {"version": "v5"})
        extra = adapter.ea_post("/v6/basket-result", result | {"note": "x"})
        unlinked = adapter.ea_post("/v6/basket-result", result)
        stored = adapter.rows("SELECT basket_id, net_pnl FROM basket_results")
    assert (v5.status_code, extra.status_code, unlinked.status_code) == (400, 400, 200)
    assert stored == [("XAUUSD-V6B-abcdefgh2345", 5.0)]


def test_a_real_account_gets_403_and_never_an_intent(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixed_candidate(monkeypatch)
    clock = FakeClock(epoch=float(ef.AS_OF + 1))
    with running(tmp_path, execute_settings(tmp_path), clock) as adapter:
        adapter.backfill()
        adapter.poll(trade_mode="REAL", server="Broker-Live")
        refused = adapter.operator_post("/v6/control/session", {"action": "start"})
        account = ef.engine_snapshot_payload()["account"] | {"trade_mode": "REAL"}
        cycle_id = adapter.snapshot("snap-real-1", account=account)
        waited = adapter.operator_post("/v6/operator/wait", {"timeout_s": 1, "agent": "codex"})
        cycle = adapter.cycle(cycle_id)
        reply = adapter.poll(trade_mode="REAL", server="Broker-Live")
        intents = adapter.rows("SELECT COUNT(*) FROM v6_intents")
    assert (refused.status_code, refused.json()["refusal"]) == (409, "APP-V6-SESSION-NOT-DEMO")
    assert waited.status_code == 403
    assert (waited.json()["code"], waited.json()["policy"]) == (
        "APP-V6-DEMO-403", "POLICY_OPERATOR_DEMO_ONLY")
    assert cycle[:2] == ("HOLD", "APP-V6-GATE")
    assert reply["has_intent"] is False and intents == [(0,)]


def test_no_decision_by_the_deadline_holds_with_operator_timeout(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """V6_OPERATOR_DEADLINE_S=30 and a snapshot 27 s after the close: a 3 s wait."""
    fixed_candidate(monkeypatch)
    clock = FakeClock(epoch=float(ef.AS_OF + 21))
    settings = execute_settings(tmp_path, operator_deadline_s=30)
    with running(tmp_path, settings, clock) as adapter:
        adapter.backfill()
        adapter.poll()
        assert adapter.start_session()["armed"] is True
        cycle_id = adapter.snapshot("snap-timeout-1")
        packet = adapter.wait_packet("claude_code")
        assert packet["expires_at_epoch"] == ef.AS_OF + 30
        cycle = adapter.cycle(cycle_id)
        late = adapter.operator_post("/v6/operator/decision",
                                     enter_decision(packet, "claude_code"))
        chief = adapter.rows("SELECT source, error_code FROM v6_agent_views"
                             " WHERE cycle_id = ? AND role = 'chief'", (cycle_id,))
        intents = adapter.rows("SELECT COUNT(*) FROM v6_intents")
    assert cycle == ("HOLD", "APP-V6-OPERATOR-TIMEOUT", "operator", "failed")
    assert (late.status_code, late.json()["code"]) == (409, "EXPIRED")
    assert chief == [("operator", "PROVIDER_TIMEOUT")]      # R0 asks the desks only
    assert intents == [(0,)]


def test_shadow_mode_records_the_decision_but_never_publishes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixed_candidate(monkeypatch)
    clock = FakeClock(epoch=float(ef.AS_OF + 1))
    settings = execute_settings(tmp_path, mode="shadow")
    with running(tmp_path, settings, clock, signed=False) as adapter:
        adapter.backfill()
        adapter.poll()
        started = adapter.start_session()
        cycle_id = adapter.snapshot("snap-shadow-1")
        packet = adapter.wait_packet()
        adapter.poll()
        accepted = adapter.operator_post("/v6/operator/decision", enter_decision(packet))
        cycle = adapter.cycle(cycle_id)
        reply = adapter.poll()
        intents = adapter.rows("SELECT COUNT(*) FROM v6_intents")
        summary = adapter.rows("SELECT summary_json FROM v6_cycles WHERE cycle_id = ?",
                               (cycle_id,))
        resumed = adapter.operator_post("/v6/control/resume", {})
    assert (resumed.status_code, resumed.json()) == (200, {"halted": False, "removed": False})
    assert (started["armed"], started["arm"]) == (False, None)
    assert (packet["mode"], packet["session"]["armed"]) == ("shadow", False)
    assert accepted.status_code == 202
    assert cycle == ("ENTER_SHADOW", None, "operator", "ok")
    shadow_intent = json.loads(summary[0][0])["shadow_intent"]
    assert (shadow_intent["source"], shadow_intent["lots"]) == ("operator", 0.01)
    assert intents == [(0,)] and reply["has_intent"] is False
    assert wire.verify_intent(SecretStr(EA_KEY), PollResponse(**reply), POINT)
