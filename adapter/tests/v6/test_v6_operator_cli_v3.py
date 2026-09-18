"""The operator CLI with packets and decisions v3."""

from __future__ import annotations

import importlib
from pathlib import Path

from . import operator_fixtures_v6 as of
from .operator_cli_fixtures_v6 import PACKET_NOW, cli  # noqa: F401 (puts the CLI on sys.path)

view = importlib.import_module("v6ops.packet_view")
decisions = importlib.import_module("v6ops.decisions")
waiting = importlib.import_module("v6ops.waiting")


def document(packet) -> dict:
    return packet.model_dump(mode="json")


def test_the_fallback_template_matches_the_adapter() -> None:
    for sealed in (of.packet(), of.managed_packet("position"), of.managed_packet("pending")):
        packet = document(sealed)
        expected = packet.pop("decision_template")
        assert decisions.build_template(packet) == {**expected, "agent": None}


def test_the_served_template_is_used_as_is() -> None:
    packet = document(of.managed_packet("position"))
    template = decisions.build_template(packet)
    assert (template["schema_version"], template["action"]) == (
        "v6.operator.decision.3", "MANAGE")
    assert template["manage"] == {"target": "position", "ticket": 91, "op": "KEEP",
                                  "sl": None, "tp1": None, "tp2": None, "tp3": None,
                                  "sl_after_tp1": None, "sl_after_tp2": None,
                                  "time_limit_min": None, "entry": None,
                                  "pending_expiry_min": None, "reason": ""}


def render(packet) -> list[str]:
    return view.render_packet(document(packet), now=PACKET_NOW,
                              path=Path("packet.json")).splitlines()


def test_a_position_packet_is_summarised() -> None:
    lines = render(of.managed_packet("position"))
    state = next(line for line in lines if line.startswith("state position"))
    assert "ticket 91 buy 0.01 @ 4533.35" in state
    assert "now +0.26R, 10.0 min open" in state
    assert "tp1 4539.00 (sl+ 4535.50)" in state and "step 0" in state
    assert "KEEP, CLOSE or MODIFY" in state
    assert not any(line.startswith("suggestions") for line in lines)
    assert any(line.startswith("agent entry: none while a V6 position") for line in lines)
    assert lines[-1].startswith("agents ") and "(action MANAGE)" in lines[-1]


def test_a_pending_packet_is_summarised() -> None:
    lines = render(of.managed_packet("pending"))
    state = next(line for line in lines if line.startswith("state pending"))
    assert "BUY_LIMIT 4531.35" in state and "KEEP, CANCEL or MODIFY" in state
    assert "market needs 4.00 to fill" in state


def test_an_order_without_a_ladder_shows_no_plan() -> None:
    bare = of.managed_packet("pending", pending_order=of.pending_block(plan=None))
    state = next(line for line in render(bare) if line.startswith("state pending"))
    assert "plan -" in state


def test_limits_bias_and_last_action_lines() -> None:
    action = {"action_id": "m3a7q2z5k6pw", "op": "MODIFY_POSITION", "ticket": 91,
              "status": "APPLIED", "detail": "NONE retcode 10009",
              "at_epoch": of.BAR_OPEN + 60}
    bias = {"direction": "range", "levels": [4526.0, 4541.0], "invalidation": None,
            "scenario": "fade the box"}
    lines = render(of.packet(last_action=action, last_bias=bias,
                             last_bias_at_epoch=of.BAR_OPEN))
    assert any(line.startswith("last bias") and "range | levels 4526.00 4541.00" in line
               for line in lines)
    assert any(line.startswith("last action m3a7q2z5k6pw MODIFY_POSITION ticket 91 APPLIED")
               for line in lines)
    limits = next(line for line in lines if line.startswith("agent entry id"))
    assert "BUY STOP >= 4535.72 SELL STOP <= 4534.81" in limits
    assert "tp1 >= 0.5R, tp3 1.0-5.0R" in limits and "time 60-240 min, pending 15-60 min" in limits
    assert lines[-1].startswith("agents ") and "(action HOLD or ENTER)" in lines[-1]


def test_hostile_text_in_the_bias_is_cleaned() -> None:
    bias = {"direction": "up", "levels": [], "invalidation": None,
            "scenario": "evil\x1b[2J\x07text"}
    lines = render(of.packet(last_bias=bias, last_bias_at_epoch=of.BAR_OPEN))
    assert all("\x1b" not in line and "\x07" not in line for line in lines)


def test_a_management_packet_needs_its_block() -> None:
    packet = document(of.managed_packet("position"))
    assert waiting.packet_problems(packet) == []
    assert waiting.packet_problems({**packet, "position": None}) == ["position"]
    pending = document(of.managed_packet("pending"))
    assert waiting.packet_problems({**pending, "pending_order": "x"}) == ["pending_order"]
    assert waiting.packet_problems({**pending, "state": "open"}) == ["state"]
