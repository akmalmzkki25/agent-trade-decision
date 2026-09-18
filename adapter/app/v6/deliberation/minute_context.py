"""
The market context of an m1 cycle (spec section 4.2).

It is the newest M15 cycle's context (spec, features, levels, probe, M5 to D1 bars) with
what changes every minute swapped in from the minute snapshot: times, quote, account,
ticks, day, EA state, positions, pending orders and the closed M1 bars. What depends on
the time or the spread is assessed again at the minute close: the session, the calendar
(passed in) and the friction features. The gates then see the minute as it is.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType

from ..config import V6Settings
from ..cycle_codes import F_ATR_M5, F_FRICTION_ATR, F_FRICTION_PRICE
from ..cycle_types import CalendarAssessment, MarketContext
from ..market.feature_map import effective_friction
from ..market.sessions import session_state
from ..schemas.minute import MinuteSnapshot
from ..types import Bar


def minute_settings(settings: V6Settings) -> V6Settings:
    """The gate settings of an m1 cycle: a minute snapshot is stale after
    V6_MINUTE_STALE_S (the SNAPSHOT_AGE gate reads `snapshot_stale_s`)."""
    return settings.model_copy(update={"snapshot_stale_s": settings.minute_stale_s})


def _features(base: MarketContext, minute: MinuteSnapshot,
              settings: V6Settings) -> Mapping[str, float]:
    features = dict(base.features)
    friction = effective_friction(settings.friction_price, minute.quote.ask - minute.quote.bid)
    features[F_FRICTION_PRICE] = friction
    atr_m5 = features.get(F_ATR_M5)
    if atr_m5 is not None and atr_m5 > 0:
        features[F_FRICTION_ATR] = friction / atr_m5
    else:
        features.pop(F_FRICTION_ATR, None)
    return MappingProxyType(features)


def minute_context(base: MarketContext, minute: MinuteSnapshot, m1_bars: tuple[Bar, ...], *,
                   cycle_id: str, received_at: float, calendar: CalendarAssessment,
                   settings: V6Settings) -> MarketContext:
    """`base` (the newest M15 context) as it stands at the minute's close."""
    close = minute.bar_close_epoch
    account = minute.account
    return replace(
        base, cycle_id=cycle_id, snapshot_id=minute.snapshot_id,
        bar_open_epoch=minute.bar_open_epoch, as_of_epoch=close,
        sent_at_epoch=minute.sent_at_epoch, received_at=received_at,
        trade_mode=account.trade_mode, server=account.server, account=account,
        quote=minute.quote, ticks=minute.ticks, day=minute.day, ea_state=minute.ea_state,
        positions=tuple(minute.positions), pending_orders=tuple(minute.pending_orders),
        bars=MappingProxyType({**base.bars, "M1": m1_bars}), session=session_state(close),
        calendar=calendar, features=_features(base, minute, settings))
