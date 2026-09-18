"""
One closed M15 bar through the real tier 0, as the operator backend would run it.

    synthetic snapshot -> load_bars (cut reader) -> build_context -> evaluate_gates
    -> detect_all -> assess_candidates -> build_packet (+ size_for per offered item)

The packet is built whatever the gates say, so the record can tell "would have
been offered if the position slot were free" (exposure gates ignored) from "was
offered" (every gate passed). The engine itself only builds it after the gates.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Final

from app.v6.config import V6Settings
from app.v6.cycle_codes import (
    F_ATR_M5, F_FRICTION_ATR, GATE_CODES, GATE_OCCUPANCY, GATE_TRADES_TODAY,
)
from app.v6.cycle_types import CandidateAssessment, DeskViews, MarketContext
from app.v6.deliberation.candidates import assess_candidates
from app.v6.deliberation.context_builder import (
    ContextRequest, build_context, cycle_friction, load_bars,
)
from app.v6.deliberation.operator_packet import PacketRefusal, PacketRequest, build_packet
from app.v6.deliberation.shadow import size_for
from app.v6.market.bar_store import (
    DEFAULT_COVERAGE_LOOKBACK_S, FULL_DAY_FRACTION, WARM_M15_DAYS,
)
from app.v6.market.feature_map import ADX_PERIOD, ATR_WINDOW_BARS
from app.v6.risk.gates import RuntimeGateState, evaluate_gates, failed_codes
from app.v6.runtime.ea_state import cycle_id_for
from app.v6.schemas.snapshot import CalendarEventBlock, V6Snapshot
from app.v6.setups import detect_all
from app.v6.types import TIMEFRAME_SECONDS, Bar, Refusal

from .data import BarSet
from .synth import (
    RECEIVE_DELAY_S, REPLAY_SESSION_ID, CutReader, Exposure, healthy_breakers, quote_spread,
    synth_snapshot,
)

M5_S: Final[int] = TIMEFRAME_SECONDS["M5"]
M15_S: Final[int] = TIMEFRAME_SECONDS["M15"]
SECONDS_PER_DAY: Final[int] = 86_400
SECONDS_PER_HOUR: Final[int] = 3_600
MIN_H1_BARS: Final[int] = 2 * ADX_PERIOD + 1
MIN_D1_BARS: Final[int] = 2
MIN_M15_BARS_PER_DAY: Final[int] = int(SECONDS_PER_DAY // M15_S * FULL_DAY_FRACTION)
STANDARD_MULTIPLIER: Final[float] = 1.0
EXPOSURE_GATES: Final[frozenset[str]] = frozenset({GATE_OCCUPANCY, GATE_TRADES_TODAY})
BANDS: Final[tuple[tuple[str, int, int], ...]] = (
    ("00-07", 0, 7), ("07-11", 7, 11), ("11-17", 11, 17), ("17-20", 17, 20), ("20-24", 20, 24))
INSUFFICIENT_M5: Final[str] = "M5_HISTORY"
INSUFFICIENT_M5_GAP: Final[str] = "M5_MISSING_AT_BAR"
INSUFFICIENT_M15: Final[str] = "M15_SESSIONS"
INSUFFICIENT_H1: Final[str] = "H1_HISTORY"
INSUFFICIENT_D1: Final[str] = "D1_HISTORY"


@dataclass(frozen=True)
class CandidateRecord:
    candidate_id: str
    setup: str
    side: str
    entry: float
    exit_codes: tuple[str, ...] = ()     # exit-plan refusal codes (empty: plan built)
    sizing_codes: tuple[str, ...] = ()   # standard-tier sizing refusal (offered items only)
    offered: bool = False                # in the engine's offer (first 3 with a plan)
    in_packet: bool = False              # sized at the standard tier and put in the packet


@dataclass(frozen=True)
class BarRecord:
    bar_t: int
    as_of: int
    spread_points: int
    insufficient: tuple[str, ...] = ()
    failed_gates: tuple[str, ...] = ()
    candidates: tuple[CandidateRecord, ...] = ()
    packet_refusal: str = ""
    open_position: bool = False
    trades_before: int = 0
    atr_m5: float | None = None
    friction_atr: float | None = None

    @property
    def evaluated(self) -> bool:
        return not self.insufficient

    @property
    def day(self) -> str:
        return datetime.fromtimestamp(self.as_of, tz=timezone.utc).strftime("%Y-%m-%d")

    @property
    def band(self) -> str:
        hour = self.as_of % SECONDS_PER_DAY // SECONDS_PER_HOUR
        return next(name for name, start, end in BANDS if start <= hour < end)

    @property
    def gates_pass(self) -> bool:
        return self.evaluated and not self.failed_gates

    @property
    def gates_pass_if_flat(self) -> bool:
        return self.evaluated and EXPOSURE_GATES.issuperset(self.failed_gates)

    @property
    def had_offer(self) -> bool:
        return any(item.offered for item in self.candidates)

    @property
    def packet_ids(self) -> tuple[str, ...]:
        return tuple(item.candidate_id for item in self.candidates if item.in_packet)

    @property
    def packet_if_flat(self) -> bool:
        """The operator agent gets a packet on every bar that passes the hard gates while
        flat, with or without suggestions (it may design its own entry)."""
        return self.gates_pass_if_flat

    @property
    def suggestion_if_flat(self) -> bool:
        """A packet that also carries at least one sized detector suggestion."""
        return self.gates_pass_if_flat and bool(self.packet_ids)

    @property
    def traded(self) -> bool:
        return self.gates_pass and bool(self.packet_ids)


def _full_days(bars: Sequence[Bar], as_of: int) -> int:
    since = as_of - DEFAULT_COVERAGE_LOOKBACK_S
    per_day: dict[int, int] = {}
    for bar in bars:
        if bar.t >= since:
            per_day[bar.t // SECONDS_PER_DAY] = per_day.get(bar.t // SECONDS_PER_DAY, 0) + 1
    return sum(1 for count in per_day.values() if count >= MIN_M15_BARS_PER_DAY)


def insufficiency(bars: BarSet, as_of: int) -> tuple[str, ...]:
    """Why the stored history cannot stand in for the live store at `as_of`."""
    m5 = bars.closed_by("M5", as_of)
    checks = (
        (len(m5) < ATR_WINDOW_BARS, INSUFFICIENT_M5),
        (not m5 or m5[-1].t != as_of - M5_S, INSUFFICIENT_M5_GAP),
        (_full_days(bars.closed_by("M15", as_of), as_of) < WARM_M15_DAYS, INSUFFICIENT_M15),
        (len(bars.closed_by("H1", as_of)) < MIN_H1_BARS, INSUFFICIENT_H1),
        (len(bars.closed_by("D1", as_of)) < MIN_D1_BARS, INSUFFICIENT_D1),
    )
    return tuple(code for failed, code in checks if failed)


def _codes(result: object) -> tuple[str, ...]:
    return tuple(result.codes) if isinstance(result, Refusal) else ()


def _candidate_records(context: MarketContext, assessments: Sequence[CandidateAssessment],
                       offered: frozenset[str], packet_ids: frozenset[str],
                       remaining_loss: float, settings: V6Settings) -> tuple[CandidateRecord, ...]:
    records = []
    for item in assessments:
        candidate = item.candidate
        is_offered = candidate.candidate_id in offered
        sizing = (size_for(context, item.exit_plan, STANDARD_MULTIPLIER, remaining_loss, settings)
                  if is_offered and item.exit_plan is not None else None)
        records.append(CandidateRecord(
            candidate_id=candidate.candidate_id, setup=candidate.setup, side=candidate.side,
            entry=candidate.entry, exit_codes=_codes(item.refusal), sizing_codes=_codes(sizing),
            offered=is_offered, in_packet=candidate.candidate_id in packet_ids))
    return tuple(records)


def _context_at(bar: Bar, bars: BarSet, stored_events: Sequence[CalendarEventBlock],
                settings: V6Settings, exposure: Exposure
                ) -> tuple[V6Snapshot, MarketContext, float]:
    as_of = bar.t + M15_S
    snapshot = synth_snapshot(bar, stored_events, exposure)
    now = float(as_of + RECEIVE_DELAY_S)
    window = load_bars(CutReader(bars, as_of), snapshot)
    context = build_context(ContextRequest(
        cycle_id=cycle_id_for(snapshot.snapshot_id), snapshot=snapshot, received_at=now),
        window, settings, now)
    return snapshot, context, now


def bar_context(bar: Bar, bars: BarSet, stored_events: Sequence[CalendarEventBlock],
                settings: V6Settings, exposure: Exposure
                ) -> tuple[V6Snapshot, MarketContext, float] | None:
    """(snapshot, context, now) of M15 `bar` at its close, as the engine builds them from
    bars closed by then; None when the stored history is insufficient."""
    if insufficiency(bars, bar.t + M15_S):
        return None
    return _context_at(bar, bars, stored_events, settings, exposure)


def evaluate_bar(bar: Bar, bars: BarSet, stored_events: Sequence[CalendarEventBlock],
                 settings: V6Settings, exposure: Exposure) -> BarRecord:
    """The tier-0 outcome of M15 `bar` at its close; reads only bars closed by then."""
    as_of = bar.t + M15_S
    base = BarRecord(bar_t=bar.t, as_of=as_of, spread_points=quote_spread(bar),
                     open_position=exposure.is_open(as_of),
                     trades_before=exposure.trades_today)
    reasons = insufficiency(bars, as_of)
    if reasons:
        return replace(base, insufficient=reasons)
    _, context, now = _context_at(bar, bars, stored_events, settings, exposure)
    breakers = healthy_breakers(context, settings)
    gates = evaluate_gates(context, context.calendar, RuntimeGateState(warmed_up=True),
                           settings, breakers, now)
    pool = assess_candidates(context, detect_all(context), settings,
                             friction_price=cycle_friction(settings, context))
    packet = build_packet(PacketRequest(
        context=context, gates=gates, offered=pool.offered, baseline=DeskViews(),
        remaining_loss_usd=breakers.remaining_loss_usd, session_id=REPLAY_SESSION_ID,
        armed=True, now=now, deadline_epoch=float(as_of + settings.operator_deadline_s),
    ), settings)
    refused = isinstance(packet, PacketRefusal)
    packet_ids = frozenset() if refused else frozenset(packet.allowed.candidate_ids)
    candidates = _candidate_records(context, pool.assessments, pool.offered_ids, packet_ids,
                                    breakers.remaining_loss_usd, settings)
    return replace(base, failed_gates=failed_codes(gates), candidates=candidates,
                   packet_refusal=packet.code if refused else "",
                   atr_m5=context.features.get(F_ATR_M5),
                   friction_atr=context.features.get(F_FRICTION_ATR))


def gate_order() -> tuple[str, ...]:
    """Gate codes in evaluation order (the order `first_fail` uses)."""
    return tuple(GATE_CODES)
