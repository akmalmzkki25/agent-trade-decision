"""The m1 packet: closed M1 bars only, m1_state, its own deadline and template."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
from typing import Final

import pytest

from app.v6.cycle_types import DeskViews, MarketContext
from app.v6.deliberation.minute_packet import atr, closed_minutes, minute_state
from app.v6.deliberation.operator_packet import PacketRequest, build_packet
from app.v6.schemas.operator import OperatorPacket
from app.v6.types import Bar

from . import engine_fixtures_v6 as ef
from .test_engine_operator import OPERATOR

MINUTE_OPEN: Final[int] = ef.AS_OF + 120          # the second minute after the M15 close
MINUTE_CLOSE: Final[int] = MINUTE_OPEN + 60


def rising(count: int = 60, last_open: int = MINUTE_OPEN) -> tuple[Bar, ...]:
    """Bars opening 0.1 higher each minute: every true range is 0.20."""
    first = last_open - 60 * (count - 1)
    bars = []
    for i in range(count):
        o = round(4300.0 + 0.1 * i, 2)
        bars.append(Bar(t=first + 60 * i, o=o, h=round(o + 0.15, 2), l=round(o - 0.05, 2),
                        c=round(o + 0.1, 2), tv=50, spr=20))
    return tuple(bars)


def minute_context(bars: tuple[Bar, ...] = rising()) -> MarketContext:
    base = ef.market_context()
    return replace(base, bar_open_epoch=MINUTE_OPEN, as_of_epoch=MINUTE_CLOSE,
                   bars=MappingProxyType({**base.bars, "M1": bars}))


def test_only_closed_minutes_are_used() -> None:
    forming = Bar(t=MINUTE_CLOSE, o=1.0, h=1.0, l=1.0, c=1.0, tv=1, spr=1)
    with pytest.raises(ValueError, match="not closed"):   # the context holds closed bars only
        minute_context(rising() + (forming,))
    closed = closed_minutes(minute_context(rising(61)))
    assert len(closed) == 60 and closed[-1].t == MINUTE_OPEN


def test_minute_state_on_a_steady_rise() -> None:
    state = minute_state(minute_context(), None)
    assert atr(rising()) == pytest.approx(0.20)
    assert (state.atr_m1, state.range_15) == (0.2, 1.6)
    assert (state.last_5.direction, state.last_5.change, state.last_5.strength) == ("up", 0.5, 2.5)
    assert (state.last_15.change, state.last_15.strength) == (1.5, 7.5)
    assert state.distances == {}


def test_distances_of_a_position_are_measured_from_its_exit_side() -> None:
    context = minute_context()
    position = {"side": "buy", "open_price": 4302.0, "sl": 4296.0, "tp": 4316.0,
                "plan": {"tp1": 4308.0, "tp2": 4312.0}}
    distances = minute_state(context, position).distances
    bid = context.quote.bid
    assert distances == {"entry": round(4302.0 - bid, 2), "sl": round(4296.0 - bid, 2),
                         "tp1": round(4308.0 - bid, 2), "tp2": round(4312.0 - bid, 2),
                         "tp3": round(4316.0 - bid, 2)}


def sealed_minute_packet() -> OperatorPacket:
    config = ef.settings(**OPERATOR)
    context = minute_context()
    built = build_packet(PacketRequest(
        context=context, gates=(), offered=(), baseline=DeskViews(),
        remaining_loss_usd=100.0, session_id="sess-1", armed=True,
        now=float(MINUTE_CLOSE + 1), kind="m1"), config)
    assert isinstance(built, OperatorPacket), built
    return built


def test_an_m1_packet_has_its_own_shape() -> None:
    packet = sealed_minute_packet()
    config = ef.settings(**OPERATOR)
    assert (packet.packet_kind, packet.bar_close_epoch) == ("m1", MINUTE_CLOSE)
    assert packet.expires_at_epoch == MINUTE_CLOSE + config.m1_deadline_s
    assert len(packet.bars.M1) == 60 and packet.bars.M15 == () and packet.bars.H1 == ()
    assert packet.m1_state is not None and packet.m1_state.last_5.direction == "up"
    assert packet.limits.agent_entry_id.endswith(str(MINUTE_OPEN))
    template = packet.decision_template
    assert (template.packet_kind, template.action, template.views, template.m15_bias) == (
        "m1", "HOLD", None, None)
