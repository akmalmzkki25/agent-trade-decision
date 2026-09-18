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


def second_line(packet: dict) -> str:
    text = minute_view.render_any(packet, now=float(MINUTE_CLOSE + 2), path=Path("p.json"))
    return text.splitlines()[1]


def test_a_position_m1_summary_shows_the_trade_the_bias_and_the_distances() -> None:
    packet = document(sealed_minute_packet())
    packet |= {"state": "position",
               "position": of.position_block(time_limit_epoch=MINUTE_CLOSE + 2 + 98 * 60),
               "last_bias": {"direction": "up", "invalidation": 4290.0},
               "last_bias_at_epoch": MINUTE_CLOSE - 420}
    packet["m1_state"] = {**packet["m1_state"], "distances": {"entry": -2.65, "sl": -10.15}}
    line = second_line(packet)
    assert "| bias up @" in line and "Z inv 4290.00" in line
    assert "| pos buy 0.01 @ 4533.35 step 0 | to entry -2.65 sl -10.15 | 98 min left" in line


def test_a_pending_m1_summary_shows_the_order() -> None:
    packet = document(sealed_minute_packet())
    packet |= {"state": "pending", "pending_order": of.pending_block()}
    packet["m1_state"] = {**packet["m1_state"], "distances": {"entry": -1.2}}
    assert second_line(packet).endswith("| order BUY_LIMIT @ 4531.35 | to entry -1.20")


def test_missing_blocks_print_placeholders_instead_of_failing() -> None:
    packet = document(sealed_minute_packet())
    packet |= {"state": "position", "position": None}
    packet["m1_state"] = {**packet["m1_state"], "last_5": None, "distances": None}
    line = second_line(packet)
    assert "| ? |" in line and line.endswith("bias none")
