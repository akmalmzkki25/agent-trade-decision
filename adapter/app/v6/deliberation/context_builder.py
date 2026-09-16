"""
Tier 0 input: the bars window and the MarketContext for one snapshot.

The bars come from the BarStore (ledger-backed) merged with the snapshot's own
rows, cut to what had CLOSED at the snapshot's M15 close, so a context never
holds a forming bar. The calendar windows are checked from the decision bar's
close to the end of the pending order's lifetime (so a release bar is blocked
however late the cycle runs); its staleness is judged at the adapter clock.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Protocol

from ..config import V6Settings
from ..cycle_types import CalendarEvent, MarketContext
from ..market.calendar import MAX_ASSESS_HORIZON_S, assess_snapshot_calendar
from ..market.feature_map import FeatureInputs, build_feature_map, effective_friction
from ..market.features import bars_closed_by
from ..market.sessions import session_state
from ..schemas.snapshot import ProbeBlock, V6Snapshot, rows_to_bars
from ..types import TIMEFRAME_SECONDS, Bar

M15_S: Final[int] = TIMEFRAME_SECONDS["M15"]
# Enough history for every tier-0 consumer: 21 days of M15 (same-slot range and the
# setups' slot lookback), a week of M5 (spread by hour), three weeks of H1, 60 D1.
LOOKBACK_BARS: Final[Mapping[str, int]] = MappingProxyType({
    "M1": 120, "M5": 2016, "M15": 2016, "H1": 504, "D1": 60,
})


class BarReader(Protocol):
    """What tier 0 reads bars through; `BarStore` satisfies it (blocking)."""

    def latest(self, tf: str, n: int) -> tuple[Bar, ...]: ...


@dataclass(frozen=True)
class ContextRequest:
    cycle_id: str
    snapshot: V6Snapshot
    received_at: float
    carried_events: tuple[CalendarEvent, ...] = ()
    probe: ProbeBlock | None = None


def as_of_for(snapshot: V6Snapshot) -> int:
    return snapshot.bar_open_epoch + M15_S


def _merge(stored: Iterable[Bar], fresh: Iterable[Bar]) -> tuple[Bar, ...]:
    by_time = {bar.t: bar for bar in stored}
    by_time.update((bar.t, bar) for bar in fresh)
    return tuple(by_time[t] for t in sorted(by_time))


def load_bars(reader: BarReader, snapshot: V6Snapshot,
              lookback: Mapping[str, int] = LOOKBACK_BARS) -> Mapping[str, tuple[Bar, ...]]:
    """Blocking: the closed-bar window per timeframe (store rows, snapshot rows win)."""
    as_of = as_of_for(snapshot)
    window: dict[str, tuple[Bar, ...]] = {}
    for tf, count in lookback.items():
        fresh = rows_to_bars(snapshot.bars.get(tf, []))  # type: ignore[call-overload]
        merged = _merge(reader.latest(tf, count), fresh)
        closed = bars_closed_by(merged, as_of, TIMEFRAME_SECONDS[tf])[-count:]
        if closed:
            window[tf] = closed
    return MappingProxyType(window)


def calendar_horizon_s(settings: V6Settings) -> int:
    """A limit order lives for the pending expiry; the blackout must cover that span."""
    return min(settings.pending_expiry_bars * M15_S, MAX_ASSESS_HORIZON_S)


def _features(snapshot: V6Snapshot, bars: Mapping[str, tuple[Bar, ...]],
              settings: V6Settings, probe: ProbeBlock | None) -> Mapping[str, float]:
    quote = snapshot.quote
    return build_feature_map(FeatureInputs(
        as_of_epoch=as_of_for(snapshot), bars=bars, point=snapshot.symbol_spec.point,
        spread_points=quote.spread_points, spread_price=quote.ask - quote.bid,
        friction_price=settings.friction_price, mid=(quote.ask + quote.bid) / 2,
        ticks=snapshot.ticks, probe=probe,
    ))


def build_context(request: ContextRequest, bars: Mapping[str, tuple[Bar, ...]],
                  settings: V6Settings, now: float) -> MarketContext:
    """Pure: the MarketContext for `request`; raises ValueError on unusable input."""
    if not math.isfinite(now):
        raise ValueError("clock returned a non-finite time")
    snapshot = request.snapshot
    probe = snapshot.probe if snapshot.probe is not None else request.probe
    calendar = assess_snapshot_calendar(
        snapshot, now_epoch=int(now), carried_events=request.carried_events, probe=probe,
        horizon_s=calendar_horizon_s(settings), decision_epoch=as_of_for(snapshot))
    return MarketContext.from_snapshot(
        snapshot, cycle_id=request.cycle_id, received_at=request.received_at,
        bars=bars, session=session_state(as_of_for(snapshot)), calendar=calendar,
        features=_features(snapshot, bars, settings, probe), probe=request.probe,
    )


def cycle_friction(settings: V6Settings, context: MarketContext) -> float:
    """The friction exits and sizing charge: the same rule as the feature map."""
    return effective_friction(settings.friction_price, context.spread_price)


def margin_per_lot(context: MarketContext, side: str) -> float:
    return context.margin_per_lot_buy if side == "buy" else context.margin_per_lot_sell
