"""
The execute-mode adapter end to end: the real app (lifespan, worker, watchdog), a
FakeClock, an EA that signs like `ea/QlipV6` and an operator agent that speaks the
operator API.

Every signed EA request first moves the clock one second, so no two requests share
a (timestamp, signature) pair. The detectors are replaced by one fixed candidate
(`engine_fixtures_v6.candidate`: buy 4300.00, stop 4292.00, target 4316.00) so the
cycle always has something the $2,000 / 0.5% budget can size at 0.01 lots.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final, TypeVar

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from pydantic import SecretStr

from app.main import create_app
from app.v6 import wire
from app.v6.clock import FakeClock
from app.v6.config import V6Settings
from app.v6.container import APP_STATE_KEY, V6Container
from app.v6.deliberation.engine import DeliberationEngine
from app.v6.runtime import wiring
from app.v6.schemas.intent import PollResponse, basket_id_for

from . import engine_fixtures_v6 as ef
from .fixtures_v6 import to_rows
from .operator_fixtures_v6 import as_v2
from .payloads_v6 import backfill_payload, poll_payload

TOKEN: Final[str] = "operator-" + "x" * 40
EA_KEY: Final[str] = "ea-e2e-key-" + "y" * 40
LOOPBACK: Final[tuple[str, int]] = ("127.0.0.1", 50126)
JSON: Final[dict[str, str]] = {"Content-Type": "application/json"}
AUTH: Final[dict[str, str]] = {**JSON, "Authorization": f"Bearer {TOKEN}"}
POINT: Final[float] = 0.01
DB_NAME: Final[str] = "execute-e2e.db"
WAIT_S: Final[float] = 15.0
WAIT_STEP_S: Final[float] = 0.02
CANDIDATE_ID: Final[str] = ef.CANDIDATE_ID
ENTRY, STOP, TARGET = 4300.0, 4292.0, 4316.0

ResultT = TypeVar("ResultT")


def execute_settings(tmp_path: Path, **overrides: Any) -> V6Settings:
    values: dict[str, Any] = {
        "enabled": True, "mode": "execute", "backend": "operator",
        "halt_file": str(tmp_path / "V6_HALT"), "operator_token": SecretStr(TOKEN),
        "ea_hmac_key": SecretStr(EA_KEY)}
    return V6Settings(_env_file=None, **(values | overrides))


def fixed_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    """The runtime's engine sees `ef.candidate()` on every bar."""
    real = wiring.build_engine

    def build(*args: Any, **kwargs: Any) -> DeliberationEngine:
        engine = real(*args, **kwargs)
        return DeliberationEngine(replace(engine.deps, detector=ef.detector_of(ef.candidate())))

    monkeypatch.setattr(wiring, "build_engine", build)


def history() -> dict[str, tuple]:
    base = ef.history(ef.AS_OF)
    return {**base, "M15": ef.flat_bars(ef.M15, ef.AS_OF - 12 * ef.DAY, ef.AS_OF, 5.0)}


@dataclass(frozen=True)
class Adapter:
    client: TestClient
    clock: FakeClock
    db: Path
    signed: bool = True

    # --- EA side -----------------------------------------------------------------------
    def ea_post(self, path: str, body: dict[str, Any], *, sign: bool | None = None,
                advance: bool = True) -> Response:
        raw = json.dumps(body).encode("utf-8")
        if advance:
            self.clock.advance(1)
        ts = int(self.clock.now_epoch())
        headers = dict(JSON)
        if self.signed if sign is None else sign:
            headers |= {wire.TS_HEADER: str(ts),
                        wire.SIG_HEADER: wire.sign_request(EA_KEY, ts, "POST", path, raw)}
        return self.client.post(path, content=raw, headers=headers)

    def backfill(self) -> None:
        for tf, bars in history().items():
            payload = backfill_payload(tf, [list(row) for row in to_rows(bars)])
            response = self.ea_post("/v6/bars/backfill", payload)
            assert response.status_code == 200, response.text

    def poll(self, **changes: Any) -> dict[str, Any]:
        response = self.ea_post("/v6/intent/poll", {**poll_payload(), **changes})
        assert response.status_code == 200, response.text
        body = response.json()
        if self.signed:
            assert wire.verify_intent(SecretStr(EA_KEY), PollResponse(**body), POINT)
        return body

    def snapshot(self, snapshot_id: str, bar_open: int = ef.T_BAR,
                 **changes: Any) -> str:
        row = [bar_open, ef.PRICE, ef.PRICE + 5, ef.PRICE - 5, ef.PRICE, 100, 20]
        payload = ef.engine_snapshot_payload(snapshot_id, bar_open=bar_open,
                                             bars={"M15": [row]})
        payload |= {"sent_at_epoch": int(self.clock.now_epoch()) + 1, **changes}
        response = self.ea_post("/v6/snapshot", payload)
        assert response.status_code == 202, response.text
        return response.json()["cycle_id"]

    # --- operator side -----------------------------------------------------------------
    def operator_post(self, path: str, body: dict[str, Any]) -> Response:
        return self.client.post(path, content=json.dumps(body).encode("utf-8"),
                                headers=AUTH)

    def start_session(self) -> dict[str, Any]:
        response = self.operator_post("/v6/control/session", {"action": "start"})
        assert response.status_code == 200, response.text
        return response.json()

    def wait_packet(self, agent: str = "codex") -> dict[str, Any]:
        response = self.operator_post("/v6/operator/wait", {"timeout_s": 10, "agent": agent})
        assert response.status_code == 200, response.text
        packet = response.json()["pending"]
        assert packet is not None, "no packet was offered"
        return packet

    # --- ledger --------------------------------------------------------------------------
    def rows(self, sql: str, params: tuple[object, ...] = ()) -> list[tuple]:
        with sqlite3.connect(str(self.db)) as conn:
            return conn.execute(sql, params).fetchall()

    def cycle(self, cycle_id: str) -> tuple:
        """(status, hold_reason, provider, provider_status) once the cycle is recorded."""
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline:
            found = self.rows("SELECT status, hold_reason, provider, provider_status"
                              " FROM v6_cycles WHERE cycle_id = ?", (cycle_id,))
            if found:
                return found[0]
            time.sleep(WAIT_STEP_S)
        raise AssertionError(f"cycle {cycle_id} was not recorded within {WAIT_S} s")

    def intent(self, intent_id: str) -> dict[str, Any]:
        with sqlite3.connect(str(self.db)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM v6_intents WHERE intent_id = ?",
                               (intent_id,)).fetchone()
        assert row is not None, f"no intent {intent_id}"
        return dict(row)

    # --- the running app -----------------------------------------------------------------
    @property
    def container(self) -> V6Container:
        return getattr(self.client.app.state, APP_STATE_KEY)

    def run(self, step: Callable[[], Awaitable[ResultT]]) -> ResultT:
        """Await `step` on the app's own event loop (e.g. one watchdog tick)."""
        portal = self.client.portal
        assert portal is not None, "the app is not running"
        return portal.call(step)


@contextmanager
def running(tmp_path: Path, settings: V6Settings, clock: FakeClock,
            *, signed: bool = True) -> Iterator[Adapter]:
    app = create_app(v6_settings=settings, clock=clock, v6_db_path=str(tmp_path / DB_NAME))
    with TestClient(app, client=LOOPBACK) as client:
        yield Adapter(client=client, clock=clock, db=tmp_path / DB_NAME, signed=signed)


def armed_session(adapter: Adapter) -> str:
    """Backfill, a DEMO poll and "Mulai trading skrg": the execute session starts ARMED."""
    adapter.backfill()
    adapter.poll()
    started = adapter.start_session()
    assert (started["armed"], started["arm"]["reason"]) == (True, "ARMED")
    assert (started["session"]["mode"], started["session"]["backend"]) == ("execute", "operator")
    return started["session"]["session_id"]


def published(adapter: Adapter) -> tuple[str, str]:
    """(cycle_id, intent_id): the cycle's packet answered ENTER by codex, intent published."""
    session_id = armed_session(adapter)
    cycle_id = adapter.snapshot("snap-exec-1")
    packet = adapter.wait_packet()
    assert (packet["cycle_id"], packet["mode"], packet["session_id"]) == (
        cycle_id, "execute", session_id)
    assert packet["session"]["armed"] is True and packet["account"]["trade_mode"] == "DEMO"
    assert [item["candidate_id"] for item in packet["candidates"]] == [CANDIDATE_ID]
    adapter.poll()                       # the quote the intent builder judges the order by
    accepted = adapter.operator_post("/v6/operator/decision", enter_decision(packet))
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["agent"] == "codex"
    assert adapter.cycle(cycle_id) == ("ENTER", None, "operator", "ok")
    (intent_id,) = adapter.rows("SELECT intent_id FROM v6_intents WHERE cycle_id = ?",
                                (cycle_id,))[0]
    return cycle_id, intent_id


def enter_decision(packet: dict[str, Any], agent: str = "codex") -> dict[str, Any]:
    """The packet's own template, with Price Action taking and the Chief entering."""
    decision = as_v2(packet["decision_template"])
    candidate_id = packet["candidates"][0]["candidate_id"]
    decision["agent"] = agent
    decision["views"]["price_action"] = {"abstain": False, "ranked": [{
        "candidate_id": candidate_id, "verdict": "TAKE", "conviction": 0.8,
        "reason_codes": ["LEVEL_CONFLUENCE", "CONFIRMED_CLOSE"], "note": "clean break"}]}
    decision["chief"] = {
        "action": "ENTER", "candidate_id": candidate_id, "risk_tier": "standard",
        "order_style": "LIMIT", "exit_profile": "STANDARD", "confidence": 0.7,
        "rationale": "PA take, no veto", "dissent": ""}
    decision["rebuttal"] = {candidate_id: "maintain"}
    return decision


def execution(intent_id: str, status: str, ticket: int, sent_at: int,
              fill_price: float = 0.0) -> dict[str, Any]:
    return {
        "schema_version": "v6.execution.1", "intent_id": intent_id, "status": status,
        "reason_code": "NONE", "ticket": ticket, "retcode": 10009,
        "requested_price": ENTRY, "fill_price": fill_price, "slippage_points": 0.0,
        "spread_points": 20, "latency_ms": 40, "sent_at_epoch": sent_at}


def basket_result(intent_id: str, closed_at: str, net_pnl: float) -> dict[str, Any]:
    return {
        "schema_version": "basket-result-event.v1",
        "basket_id": basket_id_for("XAUUSD", intent_id), "version": "v6",
        "symbol": "XAUUSD", "side": "buy", "opened_at_utc": closed_at,
        "closed_at_utc": closed_at, "close_reason": "TP", "bursts": 1, "positions": 1,
        "gross_profit": max(net_pnl, 0.0), "gross_loss": min(net_pnl, 0.0),
        "net_pnl": net_pnl, "max_floating_dd": -2.0, "avg_slippage_points": 0.0,
        "avg_spread_points": 20.0, "decision_latency_ms": 9000, "equity_at_open": 2010.5,
        "equity_at_close": 2010.5 + net_pnl}
