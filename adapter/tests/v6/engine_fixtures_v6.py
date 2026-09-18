"""
A tradeable tier-0 scenario for engine, runtime and end-to-end tests.

Thursday 2026-09-17: the M15 bar opening 12:45 UTC closes at 13:00, inside the
main window, clear of the US data bar, the LBMA fixes and the FOMC day. Flat bars
give exact features: ATR(M5) = 8.0 (friction / ATR = 0.05), ATR(M15) = 10.0,
same-slot range 10.0, ER 0 (a RANGE regime, multiplier 1). With a fresh USD
event six hours away every gate passes, so one injected candidate is enough to
reach a shadow entry with the rules backend.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from app.v6.clock import FakeClock
from app.v6.config import V6Settings
from app.v6.cycle_types import (
    CalendarAssessment, CalendarEvent, DeskViews, MarketContext, candidate_id_for,
)
from app.v6.deliberation.cycle_draft import CycleRequest
from app.v6.desks import liquidity_view, news_risk_view, price_action_view, structure_view
from app.v6.market.sessions import session_state
from app.v6.risk.breakers import (
    SCOPES, BreakerInputs, BreakerStatus, PeriodInput, evaluate_breakers,
)
from app.v6.risk.gates import RuntimeGateState
from app.v6.runtime.ea_state import cycle_id_for
from app.v6.schemas.snapshot import V6Snapshot
from app.v6.types import Bar, Candidate

from .payloads_v6 import BAR_OPEN, snapshot_payload

DAY: Final[int] = 86_400
HOUR: Final[int] = 3_600
M5: Final[int] = 300
M15: Final[int] = 900
H1: Final[int] = 3_600
T_BAR: Final[int] = BAR_OPEN + DAY + 45 * 60           # Thu 2026-09-17 12:45 UTC
AS_OF: Final[int] = T_BAR + M15
RECEIVED: Final[float] = float(AS_OF + 1)
PRICE: Final[float] = 4300.0
SPREAD_POINTS: Final[int] = 20
SNAPSHOT_ID: Final[str] = "snap-eng-0001"
CANDIDATE_ID: Final[str] = candidate_id_for("displacement", "buy", T_BAR)
STRONG_FEATURES: Final[Mapping[str, float]] = {
    "body_ratio": 0.8, "range_vol": 2.5, "tick_volume_z": 2.0}
STRONG_CODES: Final[tuple[str, ...]] = ("CONFIRMED_CLOSE", "LEVEL_CONFLUENCE", "HTF_ALIGNED")


def settings(**overrides: Any) -> V6Settings:
    return V6Settings(_env_file=None, **({"enabled": True} | overrides))


def flat_bars(tf_seconds: int, start: int, end: int, half: float,
              price: float = PRICE) -> tuple[Bar, ...]:
    return tuple(Bar(t=t, o=price, h=price + half, l=price - half, c=price, tv=100,
                     spr=SPREAD_POINTS)
                 for t in range(start, end, tf_seconds))


def history(as_of: int = AS_OF) -> dict[str, tuple[Bar, ...]]:
    """Closed flat bars up to `as_of` on every timeframe."""
    day_start = as_of - as_of % DAY
    return {
        "M5": flat_bars(M5, as_of - 7 * DAY, as_of, 4.0),
        "M15": flat_bars(M15, as_of - 8 * DAY, as_of, 5.0),
        "H1": flat_bars(H1, as_of - as_of % H1 - 21 * DAY, as_of - as_of % H1, 6.0),
        "D1": flat_bars(DAY, day_start - 30 * DAY, day_start, 20.0),
    }


@dataclass(frozen=True)
class MemoryBars:
    """A BarReader over fixed series (oldest first)."""

    series: Mapping[str, tuple[Bar, ...]]

    def latest(self, tf: str, n: int) -> tuple[Bar, ...]:
        return tuple(self.series.get(tf, ()))[-n:]


def calendar_block(offset_s: int = 6 * HOUR, as_of: int = AS_OF) -> list[dict[str, Any]]:
    return [{"event_id": 840_001, "time_epoch": as_of + offset_s, "currency": "USD",
             "importance": "HIGH", "code": "cpi-yy", "name": "CPI y/y",
             "actual": None, "forecast": 2.9, "previous": 2.8}]


def engine_snapshot_payload(snapshot_id: str = SNAPSHOT_ID, *, bar_open: int = T_BAR,
                            **changes: Any) -> dict[str, Any]:
    payload = snapshot_payload(snapshot_id, bar_open=bar_open, bars={})
    payload["calendar"] = calendar_block(as_of=bar_open + M15)
    return payload | changes


def engine_snapshot(snapshot_id: str = SNAPSHOT_ID, **changes: Any) -> V6Snapshot:
    return V6Snapshot.model_validate_json(json.dumps(engine_snapshot_payload(
        snapshot_id, **changes)))


def ask_price() -> float:
    return float(engine_snapshot_payload()["quote"]["ask"])


def calendar_assessment(as_of: int = AS_OF) -> CalendarAssessment:
    """The calendar tier 0 sees: one USD event six hours ahead, fresh."""
    event = CalendarEvent(event_id="mt5:840001", source="mt5", time_epoch=as_of + 6 * HOUR,
                          currency="USD", importance="HIGH", code="cpi-yy", forecast=2.9)
    return CalendarAssessment(as_of_epoch=as_of, blackout=False, codes=(),
                              next_event_minutes=360.0, last_event_minutes_ago=None,
                              stale=False, events=(event,))


def market_context(snapshot: V6Snapshot | None = None,
                   cycle_id: str = "c-00000000000000bb") -> MarketContext:
    """The MarketContext of a snapshot at AS_OF (no bars, no features)."""
    snap = engine_snapshot() if snapshot is None else snapshot
    return MarketContext.from_snapshot(
        snap, cycle_id=cycle_id, received_at=RECEIVED, bars={},
        session=session_state(AS_OF), calendar=calendar_assessment(), features={})


def rules_views(context: MarketContext) -> DeskViews:
    """The rules desk views with nothing offered."""
    return DeskViews(price_action=price_action_view(context, ()),
                     news_risk=news_risk_view(context), liquidity=liquidity_view(context),
                     structure=structure_view(context, ()))


def request(snapshot: V6Snapshot | None = None, *, warmed_up: bool = True,
            halt_sources: tuple[str, ...] = (), session_id: str | None = "sess-1",
            received_at: float = RECEIVED) -> CycleRequest:
    snap = engine_snapshot() if snapshot is None else snapshot
    return CycleRequest(
        cycle_id=cycle_id_for(snap.snapshot_id), snapshot=snap, received_at=received_at,
        runtime=RuntimeGateState(warmed_up=warmed_up, halt_sources=halt_sources),
        session_id=session_id)


def candidate(*, side: str = "buy", entry: float = PRICE, invalidation: float = PRICE - 8.0,
              setup: str = "displacement", variant: str = "",
              bar_t: int = T_BAR) -> Candidate:
    return Candidate(
        candidate_id=candidate_id_for(setup, side, bar_t, variant),  # type: ignore[arg-type]
        setup=setup, side=side, entry=entry,  # type: ignore[arg-type]
        invalidation=invalidation, bar_t=bar_t, features=dict(STRONG_FEATURES),
        reason_codes=STRONG_CODES)


def detector_of(*found: Candidate):
    def detect(_context: MarketContext) -> tuple[Candidate, ...]:
        return tuple(found)
    return detect


def healthy_status(context: MarketContext, config: V6Settings) -> BreakerStatus:
    periods = tuple(PeriodInput(scope=scope, start_equity=2000.0, realized_v6=0.0)
                    for scope in SCOPES)
    inputs = BreakerInputs(as_of_epoch=context.as_of_epoch, equity=context.account.equity,
                           floating_v6=0.0, periods=periods)
    return BreakerStatus(evaluation=evaluate_breakers(inputs, config))


def healthy_breakers(config: V6Settings):
    async def source(context: MarketContext) -> BreakerStatus:
        return healthy_status(context, config)
    return source


def clock_at(epoch: float = RECEIVED) -> FakeClock:
    return FakeClock(epoch=epoch)
