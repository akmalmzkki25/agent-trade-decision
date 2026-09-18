"""
The minute rhythm replay (spec section 8): how many m1 packets a day would reach the
agent, and how long the adapter needs per minute.

For every stored M15 bar in the window it builds the M15 context at the bar close
(`evaluate.bar_context`), then turns each of the next 14 closed M1 bars into a minute
snapshot (its close as the bid, its stored spread) and runs what the minute worker runs:
the minute context with the calendar assessed again at the minute close, the hard gates
with the minute staleness, and the m1 packet build. A flat minute whose gates pass and
whose packet builds is one m1 packet. The agent is scripted to change nothing, so the
account stays flat. A minute reads only bars closed by its own close.
"""

from __future__ import annotations

import bisect
import json
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Final

from app.v6.config import V6Settings
from app.v6.cycle_types import DeskViews, MarketContext
from app.v6.deliberation.context_builder import calendar_horizon_s
from app.v6.deliberation.minute_context import minute_context, minute_settings
from app.v6.deliberation.minute_packet import MINUTE_PACKET_M1_BARS
from app.v6.deliberation.operator_packet import PacketRefusal, PacketRequest, build_packet
from app.v6.market.calendar import assess_snapshot_calendar
from app.v6.risk.gates import RuntimeGateState, evaluate_gates, first_failure
from app.v6.runtime.ea_state import minute_cycle_id_for
from app.v6.schemas.minute import MinuteSnapshot, minute_snapshot_id
from app.v6.schemas.snapshot import CalendarEventBlock, V6Snapshot
from app.v6.types import TIMEFRAME_SECONDS, Bar

from .data import BarSet
from .evaluate import bar_context
from .runner import ReplayWindow
from .synth import (
    POINT, PRICE_DIGITS, RECEIVE_DELAY_S, REPLAY_SESSION_ID, Exposure, healthy_breakers,
    quote_spread,
)

M1_S: Final[int] = TIMEFRAME_SECONDS["M1"]
M15_S: Final[int] = TIMEFRAME_SECONDS["M15"]
MINUTES_PER_M15: Final[int] = M15_S // M1_S - 1        # the M15 close minute is the m15 packet
PACKET_PREFIX: Final[str] = "PACKET_"                  # a minute whose packet was refused
MS_PER_S: Final[float] = 1000.0
P50: Final[float] = 0.50
P95: Final[float] = 0.95


@dataclass(frozen=True)
class MinuteTally:
    """Minutes evaluated, m1 packets built, what stopped the rest (first failed gate, or
    PACKET_<refusal>), packets per UTC day, and the adapter time per minute."""

    minutes: int
    packets: int
    blocked_by: dict[str, int] = field(default_factory=dict)
    per_day: dict[str, int] = field(default_factory=dict)
    build_ms_p50: float = 0.0
    build_ms_p95: float = 0.0


@dataclass(frozen=True)
class _M1Series:
    """The stored M1 bars with their open times, for bisecting by time."""

    bars: tuple[Bar, ...]
    opens: tuple[int, ...]

    @classmethod
    def of(cls, bars: BarSet) -> "_M1Series":
        m1 = bars.bars("M1")
        return cls(bars=m1, opens=tuple(bar.t for bar in m1))

    def opening_in(self, start: int, end: int) -> tuple[Bar, ...]:
        """The bars that open in [start, end)."""
        return self.bars[bisect.bisect_left(self.opens, start):
                         bisect.bisect_left(self.opens, end)]

    def closed_by(self, close: int, count: int) -> tuple[Bar, ...]:
        """The newest `count` bars closed by `close`."""
        end = bisect.bisect_right(self.opens, close - M1_S)
        return self.bars[max(0, end - count):end]


def _minute(snapshot: V6Snapshot, bar: Bar) -> MinuteSnapshot:
    """The minute snapshot the EA would send at the close of M1 `bar`."""
    close = bar.t + M1_S
    spread = quote_spread(bar)
    quote = {"bid": bar.c, "ask": round(bar.c + spread * POINT, PRICE_DIGITS),
             "spread_points": spread, "time_msc": close * 1000}
    ticks = {**snapshot.ticks.model_dump(mode="json"), "window_s": M1_S,
             "quote_count": max(0, bar.tv), "spread_p50_points": float(spread),
             "spread_p95_points": float(spread)}
    document = {
        "schema_version": "v6.minute.1",
        "snapshot_id": minute_snapshot_id(snapshot.account.login, bar.t),
        "symbol": snapshot.symbol, "sent_at_epoch": close,
        "server_gmt_offset_s": snapshot.server_gmt_offset_s, "bar_open_epoch": bar.t,
        "bar": [bar.t, bar.o, bar.h, bar.l, bar.c, bar.tv, bar.spr],
        "account": snapshot.account.model_dump(mode="json"), "quote": quote, "ticks": ticks,
        "positions": [], "pending_orders": [], "day": snapshot.day.model_dump(mode="json"),
        "ea_state": snapshot.ea_state.model_dump(mode="json")}
    return MinuteSnapshot.model_validate_json(json.dumps(document))


def _one_minute(base: MarketContext, snapshot: V6Snapshot, bar: Bar, m1: _M1Series,
                settings: V6Settings) -> str:
    """What stopped the m1 packet of M1 `bar` ("" when it was built)."""
    minute = _minute(snapshot, bar)
    close = minute.bar_close_epoch
    now = float(close + RECEIVE_DELAY_S)
    calendar = assess_snapshot_calendar(snapshot, now_epoch=int(now), probe=snapshot.probe,
                                        horizon_s=calendar_horizon_s(settings),
                                        decision_epoch=close)
    context = minute_context(base, minute, m1.closed_by(close, MINUTE_PACKET_M1_BARS),
                             cycle_id=minute_cycle_id_for(minute.snapshot_id),
                             received_at=now, calendar=calendar, settings=settings)
    breakers = healthy_breakers(context, settings)
    gates = evaluate_gates(context, calendar, RuntimeGateState(warmed_up=True),
                           minute_settings(settings), breakers, now)
    failed = first_failure(gates)
    if failed is not None:
        return failed.code
    packet = build_packet(PacketRequest(
        context=context, gates=gates, offered=(), baseline=DeskViews(),
        remaining_loss_usd=breakers.remaining_loss_usd, session_id=REPLAY_SESSION_ID,
        armed=True, now=now, deadline_epoch=float(close + settings.m1_deadline_s),
        kind="m1"), settings)
    return PACKET_PREFIX + packet.code if isinstance(packet, PacketRefusal) else ""


def _percentile(values: Sequence[float], share: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(share * len(ordered)))], 2)


def _day(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).date().isoformat()


def run_minute_replay(bars: BarSet, stored_events: Sequence[CalendarEventBlock],
                      settings: V6Settings, window: ReplayWindow = ReplayWindow()
                      ) -> MinuteTally:
    """Every closed M1 bar between two M15 closes in `window` as an m1 cycle."""
    m1 = _M1Series.of(bars)
    blocked: Counter[str] = Counter()
    per_day: Counter[str] = Counter()
    timings: list[float] = []
    for bar in bars.bars("M15"):
        close = bar.t + M15_S
        minutes = m1.opening_in(close, close + MINUTES_PER_M15 * M1_S)
        built = (bar_context(bar, bars, stored_events, settings, Exposure())
                 if minutes and window.contains(close) else None)
        if built is None:
            continue
        snapshot, base, _ = built
        for minute_bar in minutes:
            started = time.perf_counter()
            stopped = _one_minute(base, snapshot, minute_bar, m1, settings)
            timings.append((time.perf_counter() - started) * MS_PER_S)
            if stopped:
                blocked[stopped] += 1
            else:
                per_day[_day(minute_bar.t)] += 1
    return MinuteTally(minutes=len(timings), packets=sum(per_day.values()),
                       blocked_by=dict(blocked), per_day=dict(sorted(per_day.items())),
                       build_ms_p50=_percentile(timings, P50),
                       build_ms_p95=_percentile(timings, P95))
