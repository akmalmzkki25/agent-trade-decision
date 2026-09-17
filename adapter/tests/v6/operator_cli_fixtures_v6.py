"""
Shared pieces of the operator CLI tests.

The scripts are loaded from their files (they are not a package). `RoutedTransport`
answers the routes a test scripts itself and sends everything else to a real
TestClient, so the CLI reads the real /v6/status, /v6/control/* and
/v6/operator/* documents. `operator_app` wires the real operator router and
queue; scripted routes cover what a live queue cannot do quickly (long empty
polls, outages, odd replies).
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Final

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.routes import v6_ea, v6_operator
from app.v6.clock import FakeClock
from app.v6.providers.operator_queue import OperatorQueue

from . import engine_fixtures_v6 as ef
from .operator_fixtures_v6 import BAR_CLOSE, packet
from .payloads_v6 import RECEIVED_AT
from .test_v6_dashboard import ControlApp, build_control_app, control_settings

SCRIPTS_DIR: Final[Path] = Path(__file__).resolve().parents[2] / "scripts"
OPERATOR_SCRIPT: Final[Path] = SCRIPTS_DIR / "v6_operator.py"
SYNC_SCRIPT: Final[Path] = SCRIPTS_DIR / "v6_sync_ea_key.py"
TOKEN: Final[str] = "operator-" + "k" * 40
EA_KEY: Final[str] = "qlip-v6-test-key-" + "x" * 24
PACKET_NOW: Final[float] = float(BAR_CLOSE + 5)
LONG_POLL_S: Final[float] = 25.0


def load_script(path: Path, name: str) -> ModuleType:
    """Load a script once per name (dataclasses resolve annotations via sys.modules)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cli = load_script(OPERATOR_SCRIPT, "v6_operator_cli")
sync_cli = load_script(SYNC_SCRIPT, "v6_sync_ea_key_cli")


def packet_document(**changes: Any) -> dict[str, Any]:
    return packet(**changes).model_dump(mode="json")


def to_json(document: object) -> bytes:
    return json.dumps(document).encode("utf-8")


@dataclass
class ManualClock:
    """The CLI's clock; `sleep` and scripted long-polls move it forward."""

    now: float = PACKET_NOW
    slept: list[float] = field(default_factory=list)

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@dataclass(frozen=True)
class Sent:
    method: str
    path: str
    body: bytes | None
    headers: Mapping[str, str]
    timeout: float


Reply = tuple[int, bytes] | Exception
Handler = Callable[[Sent], Reply]


def replies(*items: Reply, advance: float = 0.0, clock: ManualClock | None = None) -> Handler:
    """A handler answering `items` in order and then repeating the last one."""
    queue = list(items)

    def handle(_: Sent) -> Reply:
        if clock is not None:
            clock.now += advance
        return queue.pop(0) if len(queue) > 1 else queue[0]
    return handle


def json_reply(status: int, document: object) -> tuple[int, bytes]:
    return status, to_json(document)


class RoutedTransport:
    def __init__(self, client: TestClient | None = None,
                 routes: Mapping[tuple[str, str], Handler] | None = None) -> None:
        self.client = client
        self.routes = dict(routes or {})
        self.sent: list[Sent] = []

    def paths(self) -> list[str]:
        return [sent.path for sent in self.sent]

    def __call__(self, method: str, url: str, body: bytes | None, headers: Mapping[str, str],
                 timeout: float) -> Any:
        sent = Sent(method, urllib.parse.urlsplit(url).path, body, dict(headers), timeout)
        self.sent.append(sent)
        handler = self.routes.get((method, sent.path))
        if handler is not None:
            reply = handler(sent)
            if isinstance(reply, Exception):
                raise reply
            return cli.HttpReply(*reply)
        if self.client is None:
            return cli.HttpReply(404, b'{"detail": "Not Found"}')
        response = self.client.request(method, sent.path, content=body, headers=dict(headers))
        return cli.HttpReply(response.status_code, response.content)


@dataclass(frozen=True)
class Result:
    code: int
    out: str
    err: str

    @property
    def text(self) -> str:
        return self.out + self.err

    def out_json(self) -> dict[str, Any]:
        return json.loads(self.out)

    def err_json(self) -> dict[str, Any]:
        return json.loads(self.err)


def run_cli(argv: list[str], transport: Any, *, environ: Mapping[str, str] | None = None,
            clock: ManualClock | None = None, stdin: bytes | None = None,
            module: ModuleType = cli) -> Result:
    out, err = io.StringIO(), io.StringIO()
    env = {"V6_OPERATOR_TOKEN": TOKEN} if environ is None else environ
    timer = ManualClock() if clock is None else clock
    code = module.main(argv, environ=env, transport=transport, stdout=out, stderr=err,
                       stdin=io.BytesIO(stdin or b""), clock=timer, sleep=timer.sleep)
    return Result(code, out.getvalue(), err.getvalue())


def status_document(**changes: Any) -> dict[str, Any]:
    """A /v6/status document of a ready operator setup (shape of routes/v6_ea.py)."""
    document: dict[str, Any] = {
        "enabled": True, "mode": "execute", "backend": "operator",
        "operator_agents": ["claude_code", "codex"], "operator_ready": True,
        "ea_signing": "required", "server_time_epoch": int(PACKET_NOW),
        "ea_last_seen_age_s": 1.5, "trade_mode": "DEMO",
        "last_snapshot": {"snapshot_id": "Q6S-1-1", "cycle_id": "c-1", "bar_open_epoch": 1,
                          "age_s": 4.0},
        "warm": True, "halt_file_present": False,
        "runtime": {"status": "RUNNING", "breakers": [], "pending_command": None},
        "session": {"session_id": "a1b2c3d4e5f6", "trading_day": "2026-09-17",
                    "backend": "operator", "mode": "execute", "started_at": PACKET_NOW - 60,
                    "stopped_at": None, "stop_reason": None, "armed": True,
                    "armed_at": PACKET_NOW - 60, "disarmed_at": None, "disarm_reason": None,
                    "active": True},
        "last_cycle": {"cycle_id": "c-1", "status": "HOLD", "hold_reason": "APP-V6-GATE"},
    }
    return {**document, **changes}


def session_document(**changes: Any) -> dict[str, Any]:
    """A GET /v6/control/session document with an armed session."""
    status = status_document()
    document = {"trading_day": "2026-09-17", "session": status["session"],
                "summary": {"trading_day": "2026-09-17", "cycles": 3, "intents": 1},
                "command": None, "mode": "execute", "backend": "operator", "halted": False}
    return {**document, **changes}


def warm_up(built: ControlApp) -> None:
    """Enough closed bars for BarStore.is_warm at RECEIVED_AT."""
    assert built.container is not None
    as_of = int(RECEIVED_AT) - int(RECEIVED_AT) % ef.M15
    for tf, bars in ef.history(as_of).items():
        built.container.bar_store.ingest(tf, bars)
    built.container.bar_store.ingest("M15", ef.flat_bars(ef.M15, as_of - 12 * ef.DAY, as_of, 5.0))


def operator_app(tmp_path: Path, **overrides: Any) -> ControlApp:
    """Control, dashboard, EA and operator routes of an operator/execute adapter."""
    values = {"backend": "operator", "mode": "execute", "ea_hmac_key": SecretStr(EA_KEY)}
    settings = control_settings(tmp_path, **(values | overrides))
    clock = FakeClock(epoch=RECEIVED_AT)
    built = build_control_app(settings, tmp_path / "operator-cli.db", clock)
    built.app.include_router(v6_ea.router)
    built.app.include_router(v6_operator.router)
    v6_operator.install_operator_queue(built.app, OperatorQueue(settings=settings, clock=clock))
    return built


def queue_of(built: ControlApp) -> OperatorQueue:
    queue = v6_operator.operator_queue_for(built.app)
    assert queue is not None
    return queue


def operator_status_document(**changes: Any) -> dict[str, Any]:
    """A GET /v6/operator/status document (shape of routes/v6_operator.py)."""
    document: dict[str, Any] = {
        "pending": None, "waiting": False, "last_agent": None, "last_submit": None,
        "last_closed": None, "counts": {}, "backend": "operator", "mode": "execute",
        "operator_agents": ["claude_code", "codex"], "account_policy": "POLICY_OK",
        "server_time_epoch": int(PACKET_NOW)}
    return {**document, **changes}
