"""
Read-only data behind the /v6 dashboard (plan section 8).

`read_tables` performs every blocking read in one worker-thread call (the newest
snapshot, the EA execution reports and the closed-position outcomes share one
read-only connection); `build_overview` adds in-memory state and shapes the JSON
the page polls. Nothing here writes. Recorded strings (agent notes, order
comments) are untrusted: they travel as JSON text, rendered with textContent.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from pydantic import BaseModel, ConfigDict

from .config import V6Settings
from .learning.outcomes import OutcomeRecord, intent_summary, outcome_stats
from .ledger_cycles import LedgerCycles
from .ledger_cycles_schema import (
    AgentViewRecord, BreakerRecord, CycleRecord, CycleSummary, CycleWithViews, LabelStat,
    SessionRecord,
)
from .ledger_intents import IntentRecord
from .risk.exits import _stop_floor as stop_floor  # the one stop-floor rule, shared
from .risk.sizing import min_tradeable_equity
from .runtime.ea_state import EaStateView
from .runtime.realised_pnl import outcomes_since, read_only_connection, realised_pnl, window_start
from .runtime.sessions import (
    DaySummary, OpenExposure, PendingCommand, last_trade_mode, read_day, session_to_dict,
    trading_day_for,
)
from .runtime.status import (  # noqa: F401 - status words re-exported for the page's callers
    STATUS_BREAKER, STATUS_DISABLED, STATUS_HALTED, STATUS_RUNNING, STATUS_STALE,
    STATUS_WAITING_EA, classify, runtime_status, snapshot_stale,
)
from .schemas.intent import intent_id_from_comment
from .schemas.snapshot import PendingOrderBlock, PositionBlock, QuoteBlock, SymbolSpecBlock
from .types import TIMEFRAME_SECONDS, SymbolSpec

logger = logging.getLogger(__name__)

RECENT_CYCLES: Final[int] = 10
RECENT_INTENTS: Final[int] = 20
RECENT_EXECUTIONS: Final[int] = 20
RECENT_OUTCOMES: Final[int] = 10
SECONDS_PER_DAY: Final[int] = 86_400
HOLD_WINDOW_S: Final[int] = 7 * SECONDS_PER_DAY
LABEL_WINDOW_S: Final[int] = 30 * SECONDS_PER_DAY
OUTCOME_WINDOW_S: Final[int] = LABEL_WINDOW_S    # outcomes and labels cover the same days
AGE_DECIMALS: Final[int] = 1
M15_SECONDS: Final[int] = TIMEFRAME_SECONDS["M15"]

_LATEST_SNAPSHOT_SQL: Final[str] = (
    "SELECT snapshot_id, bar_open_epoch, payload_json FROM v6_snapshots"
    " WHERE payload_json IS NOT NULL ORDER BY bar_open_epoch DESC, received_at DESC LIMIT 1")
_EXECUTIONS_SQL: Final[str] = (
    "SELECT intent_id, status, reason_code, ticket, retcode, requested_price, fill_price,"
    " slippage_points, spread_points, latency_ms, sent_at_epoch, received_at"
    " FROM v6_executions ORDER BY received_at DESC, id DESC LIMIT ?")


@dataclass(frozen=True)
class LatestMarket:
    snapshot_id: str
    bar_open_epoch: int
    spec: SymbolSpec
    spread_price: float
    positions: tuple[PositionBlock, ...] = ()           # V6 magic only, as the EA saw them
    pending_orders: tuple[PendingOrderBlock, ...] = ()


class _MarketPart(BaseModel):
    """The snapshot blocks the page reads (the payload passed V6Snapshot on ingest)."""

    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)
    symbol_spec: SymbolSpecBlock
    quote: QuoteBlock
    positions: tuple[PositionBlock, ...]
    pending_orders: tuple[PendingOrderBlock, ...]


@dataclass(frozen=True)
class OperatorActivity:
    """What the operator channel last saw; the overview route fills it (None: unknown)."""

    last_agent: str | None = None
    last_seen_at: float | None = None
    pending_cycle_id: str | None = None
    pending_deadline: float | None = None      # decision deadline of the pending cycle


@dataclass(frozen=True)
class OverviewTables:
    """Everything the overview reads from disk, taken in one worker-thread call."""

    halted: bool
    active_session: SessionRecord | None
    trading_day: str
    day_counts: CycleSummary
    day_sessions: tuple[SessionRecord, ...]
    week_hold_reasons: Mapping[str, int]
    breakers: tuple[BreakerRecord, ...]
    recent: tuple[CycleWithViews, ...]
    label_stats: tuple[LabelStat, ...]
    market: LatestMarket | None
    intents: tuple[IntentRecord, ...] = ()
    executions: tuple[Mapping[str, object], ...] = ()   # v6_executions rows, newest first
    outcomes: tuple[OutcomeRecord, ...] = ()       # closed within the outcome window
    invalid_outcomes: tuple[str, ...] = ()         # basket ids of unreadable results


@dataclass(frozen=True)
class OverviewState:
    """In-memory inputs, read on the event loop thread. `csrf_nonce` goes back on every
    poll, so a page left open across an adapter restart can still HALT."""

    now: float
    active: bool
    settings: V6Settings
    ea: EaStateView
    command: PendingCommand | None
    watchdog_breaker_tripped: bool = False
    csrf_nonce: str = ""
    operator: OperatorActivity | None = None


# --- blocking reads ------------------------------------------------------------
def _parse_market(snapshot_id: str, bar_open_epoch: int, payload: str) -> LatestMarket | None:
    try:
        part = _MarketPart.model_validate_json(payload)
    except ValueError as exc:  # pydantic's ValidationError is a ValueError
        logger.warning("v6 dashboard: snapshot %s is unreadable (%s)", snapshot_id,
                       type(exc).__name__)
        return None
    return LatestMarket(snapshot_id=snapshot_id, bar_open_epoch=bar_open_epoch,
                        spec=part.symbol_spec.to_spec(),
                        spread_price=part.quote.ask - part.quote.bid,
                        positions=part.positions, pending_orders=part.pending_orders)


def _latest_market(conn: sqlite3.Connection) -> LatestMarket | None:
    row = conn.execute(_LATEST_SNAPSHOT_SQL).fetchone()
    return None if row is None else _parse_market(*row)


def _executions(conn: sqlite3.Connection) -> tuple[Mapping[str, object], ...]:
    cursor = conn.execute(_EXECUTIONS_SQL, (RECENT_EXECUTIONS,))
    names = [column[0] for column in cursor.description]
    return tuple(MappingProxyType(dict(zip(names, row))) for row in cursor.fetchall())


def load_latest_market(db_path: str) -> LatestMarket | None:
    """Spec, spread and V6 orders of the newest stored snapshot that still has its payload."""
    with read_only_connection(db_path) as conn:
        return _latest_market(conn)


def read_tables(ledger: LedgerCycles, *, db_path: str, halt_path: Path,
                now: float) -> OverviewTables:
    epoch = int(now)
    active = ledger.active_session()
    day = trading_day_for(epoch) if active is None else active.trading_day
    day_counts, day_sessions = read_day(ledger, day)
    with read_only_connection(db_path) as conn:
        market = _latest_market(conn)
        executions = _executions(conn)
        # The statistics window and every breaker period of the polled login.
        outcomes = outcomes_since(conn, min(epoch - OUTCOME_WINDOW_S, window_start(epoch)))
    return OverviewTables(
        halted=halt_path.exists(), active_session=active, trading_day=day,
        day_counts=day_counts, day_sessions=day_sessions,
        week_hold_reasons=ledger.hold_reason_histogram(epoch - HOLD_WINDOW_S),
        breakers=ledger.active_breakers(),
        recent=ledger.recent_cycles_with_views(RECENT_CYCLES),
        label_stats=ledger.candidate_label_stats(epoch - LABEL_WINDOW_S),
        market=market, intents=ledger.intents.list_recent(RECENT_INTENTS),
        executions=executions,
        outcomes=outcomes.records, invalid_outcomes=outcomes.invalid,
    )


# --- pure shaping --------------------------------------------------------------
def sizing_floor(market: LatestMarket | None, settings: V6Settings) -> dict[str, object]:
    """`min_tradeable_equity` at the stop floor for the latest spec and spread."""
    base: dict[str, object] = {
        "risk_pct": settings.risk_pct, "equity_basis_usd": settings.sizing_equity_basis_usd,
        "friction_price": settings.friction_price,
        "stop_floor_points": settings.stop_floor_points}
    if market is None:
        return {**base, "available": False, "reason": "no snapshot yet"}
    spec = market.spec
    floor = stop_floor(spec, settings.stop_floor_points, market.spread_price,
                       settings.friction_price)
    source = {"snapshot_id": market.snapshot_id, "bar_open_epoch": market.bar_open_epoch}
    try:
        equity = min_tradeable_equity(floor, settings.friction_price, settings.risk_pct, spec)
    except ValueError:
        return {**base, **source, "available": False, "reason": "symbol spec cannot be sized"}
    return {
        **base, **source, "available": True, "min_tradeable_equity": equity,
        "tradeable_at_basis": equity <= settings.sizing_equity_basis_usd,
        "stop_floor": round(floor, spec.digits), "volume_min": spec.volume_min,
        "tick_value": spec.tick_value, "tick_value_loss": spec.tick_value_loss,
        "reported_tick_value": spec.reported_tick_value,
        "tick_value_source": spec.tick_value_source,
    }


def _age(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds, AGE_DECIMALS)


def _runtime(tables: OverviewTables, state: OverviewState,
             ea_age: float | None) -> dict[str, object]:
    settings = state.settings
    status = classify(
        active=state.active, halted=tables.halted,
        breaker_tripped=bool(tables.breakers) or state.watchdog_breaker_tripped,
        ea_age_s=ea_age, ea_stale_s=settings.ea_stale_s,
        stale_snapshot=snapshot_stale(state.ea, state.now, quote_gap=settings.quote_gap))
    return {
        "active": state.active, "status": status,
        "halted": tables.halted, "mode": settings.mode, "backend": settings.backend,
        "operator_agents": list(settings.operator_agents),
        "operator_ready": settings.operator_token_ok,
        "ea_signing": settings.ea_signing,
        "structure_veto": settings.structure_veto,
        "pa_min_conviction": settings.pa_min_conviction,
        "pending_command": None if state.command is None else state.command.to_dict(),
    }


def _ea(state: OverviewState, ea_age: float | None) -> dict[str, object]:
    view = state.ea
    snap = view.last_snapshot
    last_snapshot = None if snap is None else {
        "snapshot_id": snap.snapshot_id, "cycle_id": snap.cycle_id,
        "bar_open_epoch": snap.bar_open_epoch, "age_s": _age(state.now - snap.received_at)}
    return {
        "last_seen_age_s": _age(ea_age),
        "stale": ea_age is None or ea_age > state.settings.ea_stale_s,
        "trade_mode": last_trade_mode(view),
        "spread_points": None if view.last_poll is None else view.last_poll.poll.spread_points,
        "exposure": OpenExposure.from_view(view).to_dict(),
        "last_snapshot": last_snapshot,
        "superseded": view.superseded, "inbox_pending": view.inbox_pending,
    }


def _operator(state: OverviewState) -> dict[str, object]:
    activity = state.operator or OperatorActivity()
    seen, deadline = activity.last_seen_at, activity.pending_deadline
    return {
        "agents": list(state.settings.operator_agents), "last_agent": activity.last_agent,
        "last_seen_age_s": None if seen is None else _age(state.now - seen),
        "pending_cycle_id": activity.pending_cycle_id,
        "pending_seconds_left": None if deadline is None else max(0, int(deadline - state.now)),
    }


def _session(tables: OverviewTables, state: OverviewState) -> dict[str, object]:
    active = tables.active_session
    day = DaySummary.build(tables.trading_day, tables.day_counts, tables.day_sessions,
                           OpenExposure.from_view(state.ea))
    return {"active": None if active is None else session_to_dict(active),
            "armed": active is not None and active.armed,
            "mode": state.settings.mode, "backend": state.settings.backend,
            "operator": _operator(state),
            "trading_day": tables.trading_day, "day": day.to_dict()}


def _breaker(record: BreakerRecord) -> dict[str, object]:
    return {"scope": record.scope, "period_key": record.period_key,
            "reason": record.reason, "tripped_at": record.tripped_at}


def cycle_header(cycle: CycleRecord) -> dict[str, object]:
    """The stored columns of a cycle; shared with GET /v6/status."""
    return {
        "cycle_id": cycle.cycle_id, "snapshot_id": cycle.snapshot_id,
        "bar_open_epoch": cycle.bar_open_epoch, "status": cycle.status,
        "hold_reason": cycle.hold_reason, "backend": cycle.backend,
        "provider": cycle.provider, "provider_status": cycle.provider_status,
        "session_id": cycle.session_id, "total_ms": cycle.total_ms,
        "created_at": cycle.created_at,
    }


def _gates(summary: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    gates = summary.get("gates")
    return [gate for gate in gates if isinstance(gate, Mapping)] if isinstance(gates, list) else []


def failed_gate_codes(summary: Mapping[str, Any]) -> list[object]:
    """Codes of the failed gates in a stored cycle summary; shared with GET /v6/status."""
    return [gate.get("code") for gate in _gates(summary) if not gate.get("passed")]


def _candidate(item: Mapping[str, Any]) -> dict[str, object]:
    candidate = item.get("candidate") or {}
    refusal = item.get("refusal") or {}
    return {
        "candidate_id": candidate.get("candidate_id"), "setup": candidate.get("setup"),
        "side": candidate.get("side"), "entry": candidate.get("entry"),
        "verdict": item.get("verdict"), "stop": item.get("stop"), "target": item.get("target"),
        "refusal": refusal.get("codes"),
    }


def _view(record: AgentViewRecord) -> dict[str, object]:
    """One role's attempt; for an operator view `model` names the agent."""
    return {"role": record.role, "source": record.source, "view": record.view(),
            "error_code": record.error_code, "latency_ms": record.latency_ms,
            "model": record.model}


def _cycle(item: CycleWithViews) -> dict[str, object]:
    summary = item.cycle.summary()
    candidates = summary.get("candidates")
    return {
        **cycle_header(item.cycle),
        "hold_detail": summary.get("hold_detail", ""),
        "failed_gates": failed_gate_codes(summary),
        "candidates": [_candidate(c) for c in candidates if isinstance(c, Mapping)]
        if isinstance(candidates, list) else [],
        "decision": summary.get("decision"), "protocol": summary.get("protocol"),
        "sizing": summary.get("sizing"), "refusal": summary.get("refusal"),
        "shadow_intent": summary.get("shadow_intent"),
        "views": [_view(record) for record in item.views],
    }


def _last_cycle(recent: tuple[CycleWithViews, ...]) -> dict[str, object] | None:
    if not recent:
        return None
    cycle = recent[0].cycle
    summary = cycle.summary()
    return {**cycle_header(cycle), "hold_detail": summary.get("hold_detail", ""),
            "gates": _gates(summary)}


def _order_entry(block: PositionBlock | PendingOrderBlock) -> dict[str, object]:
    return {**block.model_dump(mode="json"), "intent_id": intent_id_from_comment(block.comment)}


def _open_orders(market: LatestMarket | None) -> dict[str, object]:
    """V6 positions and pending orders as of the newest snapshot's bar close."""
    if market is None:
        return {"available": False, "snapshot_id": None, "as_of_epoch": None,
                "positions": [], "pending_orders": []}
    return {"available": True, "snapshot_id": market.snapshot_id,
            "as_of_epoch": market.bar_open_epoch + M15_SECONDS,
            "positions": [_order_entry(block) for block in market.positions],
            "pending_orders": [_order_entry(block) for block in market.pending_orders]}


def _outcomes(tables: OverviewTables, state: OverviewState) -> dict[str, object]:
    """30-day outcome statistics, and the polled login's realised P&L per breaker period."""
    epoch = int(state.now)
    since = epoch - OUTCOME_WINDOW_S
    window = [record for record in tables.outcomes
              if record.result.closed_epoch is None or record.result.closed_epoch >= since]
    poll = state.ea.last_poll
    realised = None if poll is None else realised_pnl(tables.outcomes, poll.poll.login, epoch)
    return {
        "window_days": OUTCOME_WINDOW_S // SECONDS_PER_DAY,
        "stats": outcome_stats(window).to_dict(),
        "recent": [record.to_dict() for record in window[:RECENT_OUTCOMES]],
        "invalid": list(tables.invalid_outcomes),
        "realised": None if realised is None else realised.to_dict(),
    }


def build_overview(tables: OverviewTables, state: OverviewState) -> dict[str, object]:
    """The JSON document behind GET /v6/api/overview."""
    ea_age = state.ea.ea_age_s(state.now)
    return {
        "server_time_epoch": int(state.now),
        "csrf_nonce": state.csrf_nonce,
        "runtime": _runtime(tables, state, ea_age),
        "ea": _ea(state, ea_age),
        "session": _session(tables, state),
        "breakers": [_breaker(record) for record in tables.breakers],
        "last_cycle": _last_cycle(tables.recent),
        "sizing": sizing_floor(tables.market, state.settings),
        "hold_reasons_7d": dict(tables.week_hold_reasons),
        "recent_cycles": [_cycle(item) for item in tables.recent],
        "label_stats": [asdict(stat) for stat in tables.label_stats],
        "intents": [intent_summary(record) for record in tables.intents],
        "executions": [dict(row) for row in tables.executions],
        "open_orders": _open_orders(tables.market),
        "outcomes": _outcomes(tables, state),
    }
