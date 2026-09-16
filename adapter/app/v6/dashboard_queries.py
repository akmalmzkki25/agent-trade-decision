"""
Read-only data behind the /v6 dashboard (plan section 8).

`read_tables` performs every blocking read in one worker-thread call;
`build_overview` combines the result with in-memory state into the JSON the
page polls. Nothing here writes. The latest snapshot (for the symbol spec and
the minimum tradeable equity) is read over a separate read-only connection.

Agent notes inside recorded views are untrusted text; they are passed through
as JSON strings and the page renders them with `textContent` only.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

from .config import V6Settings
from .ledger_cycles import LedgerCycles
from .ledger_cycles_schema import (
    AgentViewRecord, BreakerRecord, CycleRecord, CycleSummary, CycleWithViews, LabelStat,
    SessionRecord,
)
from .risk.exits import _stop_floor as stop_floor  # the one stop-floor rule, shared
from .risk.sizing import min_tradeable_equity
from .runtime.ea_state import EaStateView
from .runtime.sessions import (
    DaySummary, OpenExposure, PendingCommand, last_trade_mode, read_day, session_to_dict,
    trading_day_for,
)
from .runtime.status import (  # noqa: F401 - status words re-exported for the page's callers
    STATUS_BREAKER, STATUS_DISABLED, STATUS_HALTED, STATUS_RUNNING, STATUS_STALE,
    STATUS_WAITING_EA, classify, runtime_status, snapshot_stale,
)
from .schemas.snapshot import QuoteBlock, SymbolSpecBlock
from .types import SymbolSpec

logger = logging.getLogger(__name__)

RECENT_CYCLES: Final[int] = 10
HOLD_WINDOW_S: Final[int] = 7 * 86_400
LABEL_WINDOW_S: Final[int] = 30 * 86_400
READ_TIMEOUT_S: Final[float] = 5.0
AGE_DECIMALS: Final[int] = 1

_LATEST_SNAPSHOT_SQL: Final[str] = (
    "SELECT snapshot_id, bar_open_epoch, payload_json FROM v6_snapshots"
    " WHERE payload_json IS NOT NULL ORDER BY bar_open_epoch DESC, received_at DESC LIMIT 1")


@dataclass(frozen=True)
class LatestMarket:
    snapshot_id: str
    bar_open_epoch: int
    spec: SymbolSpec
    spread_price: float


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


@dataclass(frozen=True)
class OverviewState:
    """In-memory inputs, read on the event loop thread.

    `csrf_nonce` goes back to the page on every poll, so a page left open across an
    adapter restart still holds the nonce its HALT button needs.
    """

    now: float
    active: bool
    settings: V6Settings
    ea: EaStateView
    command: PendingCommand | None
    watchdog_breaker_tripped: bool = False
    csrf_nonce: str = ""


# --- blocking reads ------------------------------------------------------------
def _parse_market(snapshot_id: str, bar_open_epoch: int, payload: str) -> LatestMarket | None:
    try:
        document = json.loads(payload)
        spec = SymbolSpecBlock.model_validate_json(json.dumps(document["symbol_spec"]))
        quote = QuoteBlock.model_validate_json(json.dumps(document["quote"]))
    except (ValueError, KeyError, TypeError) as exc:  # ValidationError is a ValueError
        logger.warning("v6 dashboard: snapshot %s is unreadable (%s)", snapshot_id,
                       type(exc).__name__)
        return None
    return LatestMarket(snapshot_id=snapshot_id, bar_open_epoch=bar_open_epoch,
                        spec=spec.to_spec(), spread_price=quote.ask - quote.bid)


def load_latest_market(db_path: str) -> LatestMarket | None:
    """Spec and spread of the newest stored snapshot that still has its payload."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=READ_TIMEOUT_S)
    try:
        row = conn.execute(_LATEST_SNAPSHOT_SQL).fetchone()
    finally:
        conn.close()
    return None if row is None else _parse_market(*row)


def read_tables(ledger: LedgerCycles, *, db_path: str, halt_path: Path,
                now: float) -> OverviewTables:
    epoch = int(now)
    active = ledger.active_session()
    day = trading_day_for(epoch) if active is None else active.trading_day
    day_counts, day_sessions = read_day(ledger, day)
    return OverviewTables(
        halted=halt_path.exists(), active_session=active, trading_day=day,
        day_counts=day_counts, day_sessions=day_sessions,
        week_hold_reasons=ledger.hold_reason_histogram(epoch - HOLD_WINDOW_S),
        breakers=ledger.active_breakers(),
        recent=ledger.recent_cycles_with_views(RECENT_CYCLES),
        label_stats=ledger.candidate_label_stats(epoch - LABEL_WINDOW_S),
        market=load_latest_market(db_path),
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
        "active": state.active,
        "status": status,
        "halted": tables.halted, "mode": settings.mode, "backend": settings.backend,
        "available_backends": list(settings.available_backends),
        "structure_veto": settings.structure_veto,
        "pa_min_conviction": settings.pa_min_conviction,
        "llm_budget_usd": settings.daily_llm_budget_usd,
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


def _session(tables: OverviewTables, state: OverviewState) -> dict[str, object]:
    active = tables.active_session
    day = DaySummary.build(tables.trading_day, tables.day_counts, tables.day_sessions,
                           OpenExposure.from_view(state.ea))
    return {"active": None if active is None else session_to_dict(active),
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
    return {
        "role": record.role, "source": record.source, "view": record.view(),
        "error_code": record.error_code, "latency_ms": record.latency_ms,
        "model": record.model, "tokens_in": record.tokens_in,
        "tokens_out": record.tokens_out, "cost_usd": record.cost_usd,
    }


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
    }
