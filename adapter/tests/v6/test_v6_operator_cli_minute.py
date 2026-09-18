"""The operator CLI with m1 packets: the short summary and submit --quick."""

from __future__ import annotations

import importlib
from pathlib import Path

from . import operator_fixtures_v6 as of
from .operator_cli_fixtures_v6 import cli  # noqa: F401 (puts the CLI on sys.path)
from .test_minute_packet import MINUTE_CLOSE, sealed_minute_packet

minute_view = importlib.import_module("v6ops.minute_view")
decisions = importlib.import_module("v6ops.decisions")


def document(packet) -> dict:
    return packet.model_dump(mode="json")


def test_an_m1_packet_gets_a_three_line_summary() -> None:
    packet = document(sealed_minute_packet())
    lines = minute_view.render_any(packet, now=float(MINUTE_CLOSE + 2),
                                   path=Path("packet.json")).splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("M1 PACKET ") and "flat" in lines[0] and "48 s left" in lines[0]
    assert "5 bars up" in lines[1] and "atr" in lines[1]
    assert "--quick" in lines[2]


def test_an_m15_packet_keeps_the_full_summary() -> None:
    packet = document(of.packet())
    text = minute_view.render_any(packet, now=float(of.BAR_CLOSE + 5), path=Path("p.json"))
    assert text.startswith("V6 PACKET ")


def test_the_fallback_template_of_an_m1_packet_has_no_views() -> None:
    packet = document(sealed_minute_packet())
    expected = packet.pop("decision_template")
    assert decisions.build_template(packet) == {**expected, "agent": None}


def test_quick_holds_a_flat_m1_packet() -> None:
    decision = decisions.quick_decision(document(sealed_minute_packet()), "claude_code")
    assert (decision["action"], decision["agent"], decision["views"], decision["m15_bias"]) == (
        "HOLD", "claude_code", None, None)


def test_quick_keeps_a_managed_trade_and_carries_the_m15_bias() -> None:
    decision = decisions.quick_decision(document(of.managed_packet("position")), "codex")
    assert (decision["action"], decision["manage"]["op"]) == ("MANAGE", "KEEP")
    assert decision["m15_bias"]["carried"] is True
