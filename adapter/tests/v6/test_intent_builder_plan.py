"""An agent plan through the intent builder: STOP, LIMIT and MARKET with a TP ladder."""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr

from app.v6 import wire
from app.v6.risk import order_choice
from app.v6.risk.intent_builder import to_poll_response
from app.v6.types import TradePlan

from .test_intent_builder import (
    CLOSE, EA_KEY, NOW, POINT, build, drafted, plan, refused, sizing,
)


def trade(order_type: str = "LIMIT", **changes: Any) -> TradePlan:
    fields: dict[str, Any] = dict(order_type=order_type, tp1=4302.0, tp2=4306.0,
                                  sl_after_tp1=4298.5, sl_after_tp2=4302.0,
                                  time_limit_s=9000, pending_expiry_s=1800)
    return TradePlan(**{**fields, **changes})


def test_a_planned_limit_carries_the_ladder_and_its_own_times() -> None:
    draft = drafted(build(trade=trade()))
    row = draft.row

    assert (row.order_type, row.entry, row.sl, row.tp) == ("BUY_LIMIT", 4298.0, 4291.0, 4312.0)
    assert (row.tp1, row.tp2, row.sl_after_tp1, row.sl_after_tp2) == (
        4302.0, 4306.0, 4298.5, 4302.0)
    assert (row.time_barrier_s, row.pending_expiry_epoch) == (9000, CLOSE + 1800)
    response = to_poll_response(draft, int(NOW), SecretStr(EA_KEY), POINT)
    assert (response.schema_version, response.tp1, response.sl_after_tp2) == (
        "v6.intent.2", 4302.0, 4302.0)
    assert wire.verify_intent(SecretStr(EA_KEY), response, POINT)


def test_a_buy_stop_above_the_ask() -> None:
    ladder = trade("STOP", tp1=4309.0, tp2=4313.0, sl_after_tp1=4305.5, sl_after_tp2=4309.0)
    draft = drafted(build(entry=4305.0, exit_plan=plan(entry=4305.0), trade=ladder))

    assert (draft.row.order_type, draft.row.entry) == ("BUY_STOP", 4305.0)
    assert draft.row.pending_expiry_epoch == CLOSE + 1800


def test_a_stop_below_the_ask_is_refused() -> None:
    ladder = trade("STOP", tp1=4303.0, tp2=4307.0, sl_after_tp1=0.0, sl_after_tp2=0.0)

    assert refused(build(entry=4300.1, exit_plan=plan(entry=4300.1), trade=ladder)) == (
        order_choice.STOP_NOT_BEYOND,)


def test_a_planned_limit_that_is_no_longer_passive_is_refused() -> None:
    ladder = trade(tp1=4305.0, tp2=4309.0, sl_after_tp1=0.0, sl_after_tp2=0.0)

    assert refused(build(entry=4300.5, exit_plan=plan(entry=4300.5), trade=ladder)) == (
        order_choice.LIMIT_NOT_PASSIVE,)


def test_a_planned_market_order_fills_at_the_quote() -> None:
    market = trade("MARKET", tp1=4304.5, tp2=4308.5, sl_after_tp1=0.0, sl_after_tp2=0.0,
                   pending_expiry_s=0)
    draft = drafted(build(entry=4300.2, exit_plan=plan(entry=4300.2), trade=market,
                          sizing=sizing(lots=0.01)))

    assert (draft.row.order_type, draft.row.pending_expiry_epoch) == ("BUY", 0)
    assert refused(build(entry=4296.0, exit_plan=plan(entry=4296.0), trade=market)) == (
        order_choice.MARKET_MOVED,)


def test_a_sell_stop_below_the_bid() -> None:
    ladder = trade("STOP", tp1=4291.0, tp2=4287.0, sl_after_tp1=4294.5, sl_after_tp2=4291.0)
    draft = drafted(build(side="sell", entry=4295.0, exit_plan=plan("sell", 4295.0),
                          trade=ladder))

    assert (draft.row.side, draft.row.order_type) == ("sell", "SELL_STOP")


def test_without_a_plan_the_old_rules_hold() -> None:
    draft = drafted(build())

    assert (draft.row.tp1, draft.row.sl_after_tp1, draft.row.time_barrier_s) == (0.0, 0.0, 7200)
