"""The review-packet pieces: the resting-order block, its CLI line and the CLI template."""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from app.v6.cycle_types import MarketContext
from app.v6.deliberation.packet_extras import pending_order_block
from app.v6.deliberation.pending_review import review_hold, wants_review
from app.v6.cycle_codes import GATE_HALTED, GATE_OCCUPANCY, HoldReason
from app.v6.market.sessions import session_state
from app.v6.types import GateResult

from . import engine_fixtures_v6 as ef
from .cycle_fixtures_v6 import calendar
from .operator_cli_fixtures_v6 import cli  # noqa: F401  (puts v6ops on sys.path)

view = importlib.import_module("v6ops.packet_view")
decisions = importlib.import_module("v6ops.decisions")

ORDER = {"ticket": 7, "magic": 250570, "order_type": "SELL_LIMIT", "price": 4310.0,
         "sl": 4318.0, "tp": 4294.0, "volume": 0.01, "expiration_epoch": ef.AS_OF + 900,
         "comment": "manual"}


def context(**payload: Any) -> MarketContext:
    return MarketContext.from_snapshot(
        ef.engine_snapshot(**payload), cycle_id="c-00000000000000cc", received_at=ef.RECEIVED,
        bars={}, session=session_state(ef.AS_OF), calendar=calendar(), features={})


def gate(code: str, passed: bool) -> GateResult:
    return GateResult(code=code, passed=passed, detail="")


def test_no_resting_order_means_no_block() -> None:
    assert pending_order_block(context()) is None


def test_a_sell_order_without_a_v6_comment() -> None:
    built = context(pending_orders=[ORDER])
    block = pending_order_block(built)
    assert block is not None and block["intent_id"] == ""
    assert block["distance_from_quote"] == round(4310.0 - built.quote.bid, built.spec.digits)
    assert (block["order_type"], block["lots"]) == ("SELL_LIMIT", 0.01)


def test_a_review_needs_a_resting_order_and_healthy_gates() -> None:
    resting = context(pending_orders=[ORDER])
    occupied = (gate(GATE_OCCUPANCY, False),)
    assert wants_review(resting, occupied)
    assert not wants_review(context(), occupied)
    assert not wants_review(resting, (gate(GATE_OCCUPANCY, True),))
    assert not wants_review(resting, occupied + (gate(GATE_HALTED, False),))
    assert review_hold("CANCEL")[::2] == (HoldReason.PENDING_CANCELLED, True)
    assert review_hold("KEEP")[::2] == (HoldReason.PENDING_KEPT, False)
    assert review_hold(None)[2] is False


def test_the_review_line() -> None:
    assert view.review_line(None, {}) is None
    order = {"order_type": "BUY_LIMIT", "price": 4306.5, "sl": 4298.9, "tp": 4317.8,
             "lots": 0.01, "expiration_epoch": ef.AS_OF, "distance_from_quote": 20.5}
    line = view.review_line(order, {"bid": 4326.8, "ask": 4327.0})
    assert line.startswith("REVIEW resting BUY_LIMIT 4306.50")
    assert "20.50 to fill" in line and "KEEP or CANCEL" in line


@pytest.mark.parametrize(("limits", "expected"), [
    (None, "agent entry: -"),
    ({"agent_entry_possible": False}, "agent entry: NOT POSSIBLE"),
])
def test_limits_line_without_an_entry(limits: Any, expected: str) -> None:
    assert view.limits_line(limits).startswith(expected)
    assert decisions.agent_entry_example({"limits": limits}) is None


def test_bars_line_without_bars() -> None:
    assert view.bars_line({"M15": []}) == "M15: -"
