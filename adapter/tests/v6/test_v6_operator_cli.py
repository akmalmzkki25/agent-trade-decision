"""scripts/v6_operator.py: argument handling, token safety and the HTTP transport."""

from __future__ import annotations

import importlib.util
import io
import json
import socket
import subprocess
import sys
import threading
import urllib.parse
from collections.abc import Iterator, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.routes import v6_ea
from app.v6.clock import FakeClock

from .payloads_v6 import RECEIVED_AT
from .test_v6_dashboard import (
    LOOPBACK, TOKEN, ControlApp, build_control_app, control_settings, record_demo_poll,
)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "v6_operator.py"


def _load_cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("v6_operator_cli", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


class ClientTransport:
    """Routes CLI requests into a TestClient and remembers what was sent."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.sent: list[tuple[str, str, Mapping[str, str]]] = []

    def __call__(self, method: str, url: str, body: bytes | None,
                 headers: Mapping[str, str], timeout: float) -> Any:
        self.sent.append((method, url, dict(headers)))
        path = urllib.parse.urlsplit(url).path
        response = self.client.request(method, path, content=body, headers=dict(headers))
        return cli.HttpReply(response.status_code, response.content)


def _run(argv: list[str], transport: Any, environ: Mapping[str, str] | None = None,
         ) -> tuple[int, dict[str, Any], dict[str, Any], str]:
    out, err = io.StringIO(), io.StringIO()
    env = {"V6_OPERATOR_TOKEN": TOKEN} if environ is None else environ
    code = cli.main(argv, environ=env, transport=transport, stdout=out, stderr=err)
    text = out.getvalue() + err.getvalue()
    return (code, json.loads(out.getvalue() or "{}"), json.loads(err.getvalue() or "{}"), text)


@pytest.fixture
def wired(tmp_path: Path) -> ControlApp:
    built = build_control_app(control_settings(tmp_path), tmp_path / "cli.db",
                              FakeClock(epoch=RECEIVED_AT))
    built.app.include_router(v6_ea.router)
    return built


@pytest.fixture
def transport(wired: ControlApp) -> Iterator[ClientTransport]:
    with TestClient(wired.app, client=LOOPBACK) as client:
        yield ClientTransport(client)


@pytest.fixture
def no_env_file(tmp_path: Path) -> list[str]:
    return ["--env-file", str(tmp_path / "absent.env")]


# --- end to end against the real routes --------------------------------------------
def test_daily_session_flow(wired: ControlApp, transport: ClientTransport,
                            no_env_file: list[str]) -> None:
    assert wired.container is not None
    code, _, error, text = _run([*no_env_file, "session", "start"], transport)
    assert code == cli.EXIT_HTTP_ERROR
    assert error == {"error": 409, "refusal": "APP-V6-SESSION-NOT-DEMO",
                     "detail": "last known trade_mode is unknown"}

    record_demo_poll(wired.container)
    code, started, _, text_start = _run([*no_env_file, "session", "start"], transport)
    assert code == cli.EXIT_OK and started["created"] is True
    assert (started["backend"], started["mode"], started["refusal"]) == ("rules", "shadow", None)

    code, status, _, text_status = _run([*no_env_file, "session", "status"], transport)
    assert code == 0 and status["active"] is True
    assert status["session_id"] == started["session_id"] and status["halted"] is False

    code, stopped, _, text_stop = _run(
        [*no_env_file, "session", "stop", "--reason", "sudah_cukup"], transport)
    assert code == 0 and stopped["stopped"] is True
    assert (stopped["command"], stopped["open_v6_positions"], stopped["cycles"]) == (
        "CANCEL_PENDING", 0, 0)
    assert all(TOKEN not in chunk for chunk in (text, text_start, text_status, text_stop))
    assert transport.sent[-1][2]["Authorization"] == f"Bearer {TOKEN}"
    assert transport.sent[-1][1] == "http://127.0.0.1:8765/v6/control/session"


def test_halt_resume_and_runtime_status(wired: ControlApp, transport: ClientTransport,
                                        no_env_file: list[str]) -> None:
    assert wired.container is not None
    code, halted, _, _ = _run([*no_env_file, "halt", "--reason", "drill"], transport)
    assert code == 0 and halted == {"halted": True, "created": True, "actor": "operator",
                                    "halt_file": "V6_HALT"}

    code, status, _, _ = _run(["status"], transport, environ={})
    assert code == 0 and status["halt_file_present"] is True
    assert (status["enabled"], status["mode"], status["last_snapshot"]) == (True, "shadow", None)
    assert "Authorization" not in transport.sent[-1][2]

    code, resumed, _, _ = _run([*no_env_file, "resume"], transport)
    assert code == 0 and resumed == {"halted": False, "removed": True}
    assert not wired.container.halt_path.exists()


def test_a_wrong_token_is_reported_without_echoing_it(transport: ClientTransport,
                                                      no_env_file: list[str]) -> None:
    wrong = "wrong-" + "w" * 40
    code, out, error, text = _run([*no_env_file, "halt"], transport,
                                  environ={"V6_OPERATOR_TOKEN": wrong})
    assert (code, out) == (cli.EXIT_HTTP_ERROR, {})
    assert error == {"error": 401, "detail": "invalid operator token"}
    assert wrong not in text


# --- configuration -----------------------------------------------------------------
def test_a_missing_token_is_a_usage_error_and_nothing_is_sent(
        transport: ClientTransport, no_env_file: list[str]) -> None:
    code, _, error, _ = _run([*no_env_file, "session", "start"], transport, environ={})
    assert code == cli.EXIT_USAGE and error["error"] == "usage"
    assert "V6_OPERATOR_TOKEN" in error["detail"]
    assert transport.sent == []


@pytest.mark.parametrize("line", [
    f"V6_OPERATOR_TOKEN={TOKEN}", f'V6_OPERATOR_TOKEN="{TOKEN}"',
    f"export V6_OPERATOR_TOKEN='{TOKEN}'", f"  V6_OPERATOR_TOKEN = {TOKEN}  ",
    f"V6_OPERATOR_TOKEN={TOKEN}  # local operator", f'V6_OPERATOR_TOKEN="{TOKEN}" ',
])
def test_the_env_file_is_the_token_fallback(tmp_path: Path, line: str) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"# comment\nV6_ENABLED=true\n{line}\n", encoding="utf-8")
    assert cli.load_token({}, env_file) == TOKEN
    assert cli.load_token({"V6_OPERATOR_TOKEN": "  from-env  "}, env_file) == "from-env"


@pytest.mark.parametrize("content", ["V6_ENABLED=true\n", "V6_OPERATOR_TOKEN=\n",
                                     "V6_OPERATOR_TOKEN_OLD=abc\n"])
def test_env_files_without_a_token_yield_none(tmp_path: Path, content: str) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(content, encoding="utf-8")
    assert cli.load_token({"V6_OPERATOR_TOKEN": " "}, env_file) is None
    assert cli.load_token({}, tmp_path) is None  # a directory is not an env file


@pytest.mark.parametrize("url", [
    "http://example.com:8765", "https://127.0.0.1:8765", "http://user:pw@127.0.0.1:8765",
    "http://127.0.0.1:8765/v6", "http://127.0.0.1:8765?x=1", "ftp://127.0.0.1",
    "http://10.0.0.5:8765", "http://127.0.0.1.evil.example:8765",
])
def test_non_loopback_base_urls_are_refused(transport: ClientTransport, url: str,
                                            no_env_file: list[str]) -> None:
    code, _, error, _ = _run([*no_env_file, "--base-url", url, "halt"], transport)
    assert code == cli.EXIT_USAGE and error["error"] == "usage"
    assert transport.sent == []


@pytest.mark.parametrize(("url", "expected"), [
    ("http://127.0.0.1:9000/", "http://127.0.0.1:9000"), ("http://localhost:1", "http://localhost:1"),
    ("http://[::1]:8765", "http://[::1]:8765"),
])
def test_loopback_base_urls_are_normalised(url: str, expected: str) -> None:
    assert cli.resolve_base_url(url) == expected


def test_the_base_url_can_come_from_the_environment(transport: ClientTransport) -> None:
    code, _, _, _ = _run(["status"], transport, environ={"V6_ADAPTER_URL": "http://localhost:9"})
    assert code == 0 and transport.sent[-1][1] == "http://localhost:9/v6/status"


@pytest.mark.parametrize("argv", [[], ["session"], ["session", "pause"], ["halt", "--reason", ""],
                                  ["halt", "--reason", "x" * 65], ["bogus"]])
def test_bad_arguments_exit_with_usage(argv: list[str], capsys: pytest.CaptureFixture) -> None:
    assert cli.main(argv, environ={}) == cli.EXIT_USAGE
    assert "usage" in capsys.readouterr().err


def test_help_exits_cleanly(capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["--help"], environ={}) == cli.EXIT_OK
    assert "session" in capsys.readouterr().out


def test_unreachable_and_malformed_replies(no_env_file: list[str]) -> None:
    def refused(*_: object) -> Any:
        raise cli.TransportError("ConnectionRefusedError")

    code, _, error, _ = _run([*no_env_file, "halt"], refused)
    assert code == cli.EXIT_UNREACHABLE
    assert error == {"error": "unreachable", "detail": "ConnectionRefusedError",
                     "base_url": "http://127.0.0.1:8765"}

    for status, body in ((200, b"<html>"), (200, b"[1]"), (502, b"bad gateway")):
        code, _, error, _ = _run([*no_env_file, "halt"], lambda *_, s=status, b=body:
                                 cli.HttpReply(s, b))
        assert code == cli.EXIT_HTTP_ERROR
        assert error == {"error": status, "detail": "non-JSON response"}

    code, out, _, _ = _run(["status"], lambda *_: cli.HttpReply(204, b""), environ={})
    assert code == 0 and out["enabled"] is None


def test_the_script_needs_only_the_standard_library() -> None:
    completed = subprocess.run([sys.executable, "-S", "-I", str(SCRIPT), "--help"],
                               capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, completed.stderr
    assert "session" in completed.stdout


# --- urllib transport ----------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_: object) -> None:  # keep test output quiet
        return

    def _send(self, status: int, payload: bytes, location: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if location is not None:
            self.send_header("Location", location)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path == "/redirect":
            self._send(302, b"{}", location="/ok")
        elif self.path == "/fail":
            self._send(500, b'{"detail": "boom"}')
        elif self.path == "/bare":
            self.send_response(404)
            self.end_headers()
        else:
            self._send(200, b'{"ok": true}')

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        echo = {"auth": self.headers.get("Authorization"), "body": body,
                "type": self.headers.get("Content-Type")}
        self._send(200, json.dumps(echo).encode("utf-8"))


@pytest.fixture
def server() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_urllib_transport_round_trip(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")   # must be ignored
    monkeypatch.setenv("NO_PROXY", "")
    ok = cli.urllib_transport("GET", server + "/ok", None, {}, 5.0)
    assert (ok.status, json.loads(ok.body)) == (200, {"ok": True})

    echoed = cli.urllib_transport("POST", server + "/echo", b'{"a":1}',
                                  {"Authorization": "Bearer t", "Content-Type": "application/json"},
                                  5.0)
    assert json.loads(echoed.body) == {"auth": "Bearer t", "body": '{"a":1}',
                                       "type": "application/json"}


def test_urllib_transport_never_follows_redirects_and_reports_errors(server: str) -> None:
    redirected = cli.urllib_transport("GET", server + "/redirect", None,
                                      {"Authorization": "Bearer t"}, 5.0)
    assert redirected.status == 302
    failed = cli.urllib_transport("GET", server + "/fail", None, {}, 5.0)
    assert (failed.status, json.loads(failed.body)) == (500, {"detail": "boom"})
    bare = cli.urllib_transport("GET", server + "/bare", None, {}, 5.0)
    assert (bare.status, bare.body) == (404, b"")


def test_urllib_transport_maps_connection_failures() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(cli.TransportError) as caught:
        cli.urllib_transport("GET", f"http://127.0.0.1:{port}/", None, {}, 2.0)
    assert str(caught.value) in {"URLError", "ConnectionRefusedError", "TimeoutError"}
