"""The context of an m1 cycle: the M15 context with the minute's data swapped in."""

from __future__ import annotations

import json
from dataclasses import replace
from types import MappingProxyType

from app.v6.cycle_codes import F_ATR_M5, F_FRICTION_ATR, F_FRICTION_PRICE
from app.v6.deliberation.minute_context import minute_context, minute_settings
from app.v6.market.feature_map import effective_friction
from app.v6.market.sessions import session_state
from app.v6.schemas.minute import MinuteSnapshot
from app.v6.types import Bar

from . import engine_fixtures_v6 as ef

MINUTE_OPEN = ef.AS_OF + 120
MINUTE_CLOSE = MINUTE_OPEN + 60


def minute(**changes: object) -> MinuteSnapshot:
    snap = ef.engine_snapshot()
    document = {
        "schema_version": "v6.minute.1",
        "snapshot_id": f"Q6M-{snap.account.login}-{MINUTE_OPEN}", "symbol": snap.symbol,
        "sent_at_epoch": MINUTE_CLOSE, "server_gmt_offset_s": snap.server_gmt_offset_s,
        "bar_open_epoch": MINUTE_OPEN,
        "bar": [MINUTE_OPEN, 4300.0, 4300.4, 4299.8, 4300.2, 60, 20],
        "account": snap.account.model_dump(mode="json"),
        # The M15 snapshot's quote, one bar later: the gates judge it exactly as tier 0 did.
        "quote": {**snap.quote.model_dump(mode="json"), "time_msc": MINUTE_CLOSE * 1000},
        "ticks": {**snap.ticks.model_dump(mode="json"), "window_s": 60},
        "positions": [], "pending_orders": [],
        "day": {**snap.day.model_dump(mode="json"), "trades_today": 3},
        "ea_state": snap.ea_state.model_dump(mode="json"), **changes}
    return MinuteSnapshot.model_validate_json(json.dumps(document))


def test_the_minute_replaces_what_changes_every_minute() -> None:
    base = replace(ef.market_context(), features=MappingProxyType({F_ATR_M5: 5.0}))
    bars = (Bar(t=MINUTE_OPEN, o=4300.0, h=4300.4, l=4299.8, c=4300.2, tv=60, spr=20),)
    config = ef.settings()
    snapshot = minute(quote={"bid": 4300.10, "ask": 4300.40, "spread_points": 30,
                             "time_msc": MINUTE_CLOSE * 1000})
    context = minute_context(base, snapshot, bars, cycle_id="m-00000000000000aa",
                             received_at=float(MINUTE_CLOSE + 1),
                             calendar=ef.calendar_assessment(MINUTE_CLOSE), settings=config)
    assert (context.cycle_id, context.bar_open_epoch, context.as_of_epoch) == (
        "m-00000000000000aa", MINUTE_OPEN, MINUTE_CLOSE)
    assert (context.quote.spread_points, context.day.trades_today) == (30, 3)
    assert context.bars["M1"] == bars and context.session == session_state(MINUTE_CLOSE)
    friction = effective_friction(config.friction_price, snapshot.quote.ask - snapshot.quote.bid)
    assert context.features[F_FRICTION_PRICE] == friction
    assert context.features[F_FRICTION_ATR] == friction / 5.0
    assert context.spec == base.spec and context.calendar.as_of_epoch == MINUTE_CLOSE


def test_minute_settings_use_the_minute_staleness() -> None:
    config = ef.settings(minute_stale_s=7)
    assert minute_settings(config).snapshot_stale_s == 7
