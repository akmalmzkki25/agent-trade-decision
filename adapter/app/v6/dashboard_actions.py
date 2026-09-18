"""
Management on the V6 dashboard (phase A).

The newest management actions (what the agent sent, what the EA made of it) and the plan
of the active V6 intent: the TP ladder, the SL+ steps, the last step the EA took and the
holding time. `read_management` is a blocking read: call it in a worker thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .ledger_actions import ActionRow
from .ledger_cycles import LedgerCycles
from .ledger_intents import IntentRecord

RECENT_ACTIONS: Final[int] = 10
PLAN_FIELDS: Final[tuple[str, ...]] = (
    "intent_id", "status", "side", "order_type", "entry", "sl", "tp", "tp1", "tp2",
    "sl_after_tp1", "sl_after_tp2", "plan_step", "time_barrier_s", "ticket")


@dataclass(frozen=True)
class ManagementTables:
    actions: tuple[ActionRow, ...] = ()
    active_intent: IntentRecord | None = None


def read_management(ledger: LedgerCycles) -> ManagementTables:
    return ManagementTables(actions=ledger.actions.recent(RECENT_ACTIONS),
                            active_intent=ledger.intents.active_intent())


def action_summary(row: ActionRow) -> dict[str, object]:
    return {"action_id": row.action_id, "command": row.command, "ticket": row.ticket,
            "intent_id": row.intent_id, "agent": row.agent, "status": row.status,
            "detail": row.detail, "created_at": row.created_at, "updated_at": row.updated_at}


def plan_summary(record: IntentRecord | None) -> dict[str, object] | None:
    """The active intent's plan (None when no V6 intent is active)."""
    if record is None:
        return None
    return {name: getattr(record, name) for name in PLAN_FIELDS}


def management_overview(tables: ManagementTables) -> dict[str, object]:
    """The `actions` and `plan` keys of GET /v6/api/overview."""
    return {"actions": [action_summary(row) for row in tables.actions],
            "plan": plan_summary(tables.active_intent)}
