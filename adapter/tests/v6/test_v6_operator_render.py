"""The packet summary printed by `wait`, and the CLI's file and transport helpers."""

from __future__ import annotations

import importlib
import io
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from .operator_cli_fixtures_v6 import (
    PACKET_NOW, RoutedTransport, cli, operator_app, packet_document, run_cli,
)
from .operator_fixtures_v6 import BUY_ID, EXPIRES
from .test_v6_dashboard import LOOPBACK, ControlApp

view = importlib.import_module("v6ops.packet_view")  # on sys.path once the CLI is loaded
HOSTILE = "evil\x1b[2J\x07server\u2028name"


def render(document: dict[str, Any], now: float = PACKET_NOW) -> list[str]:
    return view.render_packet(document, now=now, path=Path("packet.json")).splitlines()


def test_hostile_and_missing_values_are_rendered_safely() -> None:
    document = packet_document()
    document["account"]["server"] = HOSTILE
    document["gates"] = [{"code": "NEWS", "passed": False, "value": None, "limit": None,
                          "detail": "x" * 200}, "junk"]
    document["calendar"] = {**document["calendar"], "blackout": True,
                            "codes": [f"CAL_{i}" for i in range(7)],
                            "last_event_minutes_ago": 12.4, "events": "none"}
    document["candidates"][1] = {**document["candidates"][1], "sizing": None,
                                 "sizing_refusal": ["MIN_LOT_WALL"], "features": {},
                                 "reason_codes": []}
    document["market"] = {**document["market"], "spread_points": 17.4, "atr_h1": float("nan")}
    document["baseline_views"] = {"price_action": {"abstain": True, "ranked": []},
                                  "news_risk": None, "liquidity": None, "structure": None}
    text = "\n".join(render(document))

    assert text.isascii() and "\x1b" not in text and "\x07" not in text
    assert "evil?[2J?server?name" in text
    assert "spread 17 pts" in text and "h1 -" in text
    assert "gates: 0 passed, 1 FAILED: NEWS (" + "x" * 60 + "...)" in text
    assert "calendar: BLACKOUT CAL_0,CAL_1,CAL_2,CAL_3,CAL_4,... | last event 12 min ago" \
           " | 0 event(s)" in text
    assert "size refused: MIN_LOT_WALL | - | -" in text
    assert "baseline: PA abstain | news - x- - | liquidity - x- - | structure - x- veto -" in text


def test_expired_stale_and_partial_packets() -> None:
    document = packet_document()
    document["calendar"] = {**document["calendar"], "stale": True, "codes": ["CAL_STALE"],
                            "as_of_epoch": None}
    document["baseline_views"] = {"price_action": None}
    document["session"] = None
    lines = render(document, now=float(EXPIRES + 1))
    assert "(EXPIRED)" in lines[1]
    assert lines[3] == "session phase -, third -, entries -, continuation -, armed -, blocks -"
    assert lines[5] == "calendar: STALE feed (fail closed) | 1 event(s)"
    assert "baseline: PA none |" in lines[-2]


def test_a_non_ascii_packet_path_is_printed_safely() -> None:
    text = view.render_packet(packet_document(), now=PACKET_NOW, path=Path("C:/Users/李/p.json"))
    assert text.isascii() and "Users/?/p.json" in text.replace("\\", "/")


def test_calendar_codes_without_blackout_and_ranked_views() -> None:
    document = packet_document()
    document["calendar"] = {**document["calendar"], "codes": ["CAL_US_DATA_BAR"]}
    document["candidates"][0]["exit"]["time_barrier_s"] = None
    lines = render(document)
    assert lines[5].startswith("calendar: codes CAL_US_DATA_BAR | next HIGH USD cpi-yy")
    assert f"[1] {BUY_ID} BUY displacement" in lines[7] and "barrier - min" in lines[7]
    assert f"PA TAKE {BUY_ID} 0.70" in lines[-2]


@pytest.mark.parametrize(("value", "pattern", "expected"), [
    (True, ".2f", "-"), (float("inf"), ".2f", "-"), (2.5, "d", "2"), (3, ".1f", "3.0"),
    ("7", ".2f", "-")])
def test_numbers(value: object, pattern: str, expected: str) -> None:
    assert view.num(value, pattern) == expected


def test_times() -> None:
    assert view.utc(0) == "1970-01-01T00:00:00Z"
    assert view.utc("soon") == "-" and view.utc(True) == "-"
    assert view.utc(10**20) == "-"
    assert view.yes_no(None) == "-" and view.codes("ABC") == "-"


# --- helpers ---------------------------------------------------------------------------
def test_an_interrupted_write_leaves_the_old_file(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "packet.json"
    target.write_text('{"old": true}', encoding="utf-8")

    def broken(*_: object) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(PermissionError):
        cli.waiting.write_json_atomic(target, {"new": True})
    monkeypatch.undo()
    assert json.loads(target.read_text(encoding="utf-8")) == {"old": True}
    assert [path.name for path in tmp_path.iterdir()] == ["packet.json"]


def test_stdin_is_capped() -> None:
    read = cli.decisions.read_stream_capped
    assert read(io.BytesIO(b"{}"), 10, "decision") == b"{}"
    with pytest.raises(cli.UsageError, match="larger than 2 bytes"):
        read(io.BytesIO(b"{ }"), 2, "decision")


def test_the_client_needs_a_token_for_auth_routes() -> None:
    client = cli.Client("http://127.0.0.1:8765", RoutedTransport())
    with pytest.raises(cli.UsageError, match="V6_OPERATOR_TOKEN"):
        client.send("GET", "/v6/control/session")
    holder = cli.Client("http://127.0.0.1:8765", RoutedTransport(), token="secret-value")
    assert "secret-value" not in repr(holder) and "secret-value" not in str(holder)


def test_template_write_failures_exit_with_an_io_error(tmp_path: Path) -> None:
    packet_file = tmp_path / "packet.json"
    packet_file.write_text(json.dumps(packet_document()), encoding="utf-8")
    (tmp_path / "blocker").write_text("file", encoding="utf-8")
    argv = ["template", "--packet", str(packet_file),
            "--out", str(tmp_path / "blocker" / "decision.json")]
    result = run_cli(argv, RoutedTransport(), environ={})
    assert result.code == cli.EXIT_ERROR and result.err_json()["error"] == "io"


def test_submit_without_a_packet_file_has_no_warnings(tmp_path: Path) -> None:
    decision = {**json.loads(json.dumps(packet_document()["decision_template"])), "agent": None}
    (tmp_path / "decision.json").write_text(json.dumps(decision), encoding="utf-8")
    transport = RoutedTransport(routes={("POST", "/v6/operator/decision"): lambda _: (
        200, b'{"accepted": true, "action": "HOLD"}')})
    argv = ["--env-file", "absent.env", "submit", "--agent", "claude_code",
            "--file", str(tmp_path / "decision.json"), "--packet", str(tmp_path / "gone.json")]
    result = run_cli(argv, transport)
    assert result.code == 0 and result.out_json()["warnings"] == []


# --- breaker reset (control route) ------------------------------------------------------
@pytest.fixture
def control(tmp_path: Path) -> Iterator[TestClient]:
    built: ControlApp = operator_app(tmp_path)
    with TestClient(built.app, client=LOOPBACK) as client:
        yield client


def test_breaker_reset_is_sent_and_judged_by_the_adapter(control: TestClient) -> None:
    transport = RoutedTransport(control)
    argv = ["--env-file", "absent.env", "breaker-reset", "--scope", "daily",
            "--period-key", "2026-09-16"]
    result = run_cli(argv, transport)
    assert result.code == cli.EXIT_HTTP_ERROR
    assert result.err_json() == {"error": 409, "code": "RESET_NOT_EVALUATED",
                                 "detail": "no EA poll recent enough to evaluate the breakers"}
    assert json.loads(transport.sent[0].body or b"{}") == {
        "scope": "daily", "period_key": "2026-09-16", "reason": "operator_reset"}


@pytest.mark.parametrize("key", ["2026-9-16", "yesterday", "2026-W3"])
def test_bad_period_keys(key: str, capsys: pytest.CaptureFixture) -> None:
    argv = ["breaker-reset", "--scope", "daily", "--period-key", key]
    assert run_cli(argv, RoutedTransport()).code == cli.EXIT_USAGE
    assert "period key" in capsys.readouterr().err
