"""
The document behind GET /v6/status (built field by field; no secrets).

Blocking reads (`read_ledger`, `read_market`) run in worker threads; the rest
shapes in-memory state. Shared by the route in `routes/v6_ea.py`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Final

from ..v6.container import V6Container
from ..v6.dashboard_queries import cycle_header, failed_gate_codes
from ..v6.ledger_cycles import LedgerCycles
from ..v6.ledger_cycles_schema import BreakerRecord, CycleRecord, SessionRecord
from ..v6.ledger_intents import IntentRecord
from ..v6.market.bar_store import BarStore
from ..v6.providers.operator_queue import OperatorQueue
from ..v6.runtime.ea_state import EaStateView, SnapshotMeta
from ..v6.runtime.status import classify, snapshot_stale
from ..v6.types import TIMEFRAME_SECONDS

AGE_DECIMALS: Final[int] = 3


@dataclass(frozen=True)
class LedgerView:
    session: SessionRecord | None
    breakers: tuple[BreakerRecord, ...]
    last_cycle: CycleRecord | None
    active_intent: IntentRecord | None


def age(now: float, then: float | None) -> float | None:
    return None if then is None else round(now - then, AGE_DECIMALS)


def read_market(store: BarStore, as_of: int) -> tuple[dict[str, dict[str, Any]], bool]:
    coverage = {
        tf: {**asdict(store.coverage(tf, as_of)), "cached": store.cached_count(tf)}
        for tf in TIMEFRAME_SECONDS
    }
    return coverage, store.is_warm(as_of)


def read_ledger(ledger: LedgerCycles) -> LedgerView:
    recent = ledger.recent_cycles(1)
    return LedgerView(session=ledger.active_session(), breakers=ledger.active_breakers(),
                      last_cycle=recent[0] if recent else None,
                      active_intent=ledger.intents.active_intent())


def snapshot_status(meta: SnapshotMeta | None, now: float) -> dict[str, Any] | None:
    if meta is None:
        return None
    return {"snapshot_id": meta.snapshot_id, "cycle_id": meta.cycle_id,
            "bar_open_epoch": meta.bar_open_epoch, "age_s": age(now, meta.received_at)}


def cycle_status(cycle: CycleRecord | None) -> dict[str, Any] | None:
    if cycle is None:
        return None
    summary = cycle.summary()
    candidates = summary.get("candidates")
    return {
        **cycle_header(cycle), "hold_detail": summary.get("hold_detail", ""),
        "failed_gates": failed_gate_codes(summary),
        "candidates": len(candidates) if isinstance(candidates, list) else 0,
        "shadow_intent": summary.get("shadow_intent"),
        "intent_id": summary.get("intent_id"),
    }


def operator_status(queue: OperatorQueue, now: float) -> dict[str, Any]:
    """The pending operator cycle and the last agent seen (no packet content)."""
    status = queue.status(now)
    pending, agent = status.pending, status.last_agent
    return {
        "pending_cycle_id": None if pending is None else pending.cycle_id,
        "pending_expires_at_epoch": None if pending is None else pending.expires_at,
        "waiting": status.waiting,
        "last_agent": None if agent is None else agent.agent,
        "last_agent_age_s": None if agent is None else age(now, agent.at),
    }


def runtime_status(container: V6Container, view: EaStateView, now: float, halted: bool,
                   breakers: tuple[BreakerRecord, ...]) -> dict[str, Any]:
    parts = container.parts
    watchdog = parts.watchdog.state
    command = parts.control.commands.current(now)
    status = classify(
        active=container.active, halted=halted,
        breaker_tripped=bool(breakers) or watchdog.breakers_tripped,
        ea_age_s=view.ea_age_s(now), ea_stale_s=container.settings.ea_stale_s,
        stale_snapshot=snapshot_stale(view, now, quote_gap=container.settings.quote_gap))
    return {
        "status": status, "tasks_running": container.run_tasks,
        "breakers": [f"{record.scope}:{record.period_key}" for record in breakers],
        "pending_command": None if command is None else command.to_dict(),
        "worker": parts.worker.stats.to_dict(), "watchdog": watchdog.to_dict(),
    }


def account_status(view: EaStateView) -> dict[str, Any]:
    """What the newest EA poll says about the account (the login is not repeated)."""
    poll = None if view.last_poll is None else view.last_poll.poll
    return {"trade_mode": None if poll is None else poll.trade_mode,
            "server": None if poll is None else poll.server}
