"""
The trade state of a cycle and when the agent manages instead of entering (spec 1, 2.3).

A V6 position or resting order fails the OCCUPANCY gate; if nothing else that makes the
system unsafe failed as well, the agent gets a management packet. A halt, a breaker,
stale data or a failed account policy are handled by the runtime (FLATTEN or
CANCEL_PENDING), never by the agent. Market gates (spread, session, news, ATR) do not
stop management: the agent may want to close or tighten exactly then.
"""

from __future__ import annotations

from typing import Final, Protocol

from ..cycle_codes import (
    GATE_ACCOUNT_POLICY, GATE_BREAKER, GATE_CLOCK_SKEW, GATE_HALTED, GATE_OCCUPANCY,
    GATE_SNAPSHOT_AGE, GATE_SPEC, GATE_WARMUP,
)
from ..cycle_types import MarketContext
from ..ledger_actions import ActionRow
from ..ledger_intents import IntentRecord
from ..risk.gates import failed_codes
from ..schemas.intent import intent_id_from_comment
from ..schemas.operator_plan import M15Bias, PacketState
from ..types import GateResult

# Gates that must pass before the agent is asked to manage.
MANAGEMENT_BLOCKING_GATES: Final[frozenset[str]] = frozenset({
    GATE_HALTED, GATE_WARMUP, GATE_ACCOUNT_POLICY, GATE_SNAPSHOT_AGE, GATE_CLOCK_SKEW,
    GATE_SPEC, GATE_BREAKER})


def trade_state(context: MarketContext) -> PacketState:
    """position before pending: V6 holds one trade at a time."""
    if context.positions:
        return "position"
    return "pending" if context.pending_orders else "flat"


def wants_management(context: MarketContext, gates: tuple[GateResult, ...]) -> bool:
    """A V6 trade is open or resting and only market or occupancy gates failed."""
    failed = frozenset(failed_codes(gates))
    return (trade_state(context) != "flat" and GATE_OCCUPANCY in failed
            and not failed & MANAGEMENT_BLOCKING_GATES)


def managed_trade(context: MarketContext) -> tuple[str | None, int]:
    """(intent id from the MT5 comment, ticket) of the position or resting order."""
    trades = context.positions or context.pending_orders
    if not trades:
        return None, 0
    return intent_id_from_comment(trades[0].comment), trades[0].ticket


class PlanReader(Protocol):
    """Blocking reads of what the adapter stored about a trade (run in a thread)."""

    def intent(self, intent_id: str) -> IntentRecord | None: ...

    def by_ticket(self, ticket: int) -> IntentRecord | None: ...

    def last_action(self, session_id: str) -> ActionRow | None: ...


def stored_intent(plans: PlanReader, context: MarketContext) -> IntentRecord | None:
    """The intent behind the managed trade: by its comment, else by its ticket (a broker
    may rewrite comments)."""
    intent_id, ticket = managed_trade(context)
    record = None if intent_id is None else plans.intent(intent_id)
    if record is None and ticket > 0:
        record = plans.by_ticket(ticket)
    return record


class BiasMemory:
    """The newest M15 bias an agent gave, kept in memory (a restart forgets it)."""

    def __init__(self) -> None:
        self._bias: M15Bias | None = None
        self._at: int | None = None

    def remember(self, bias: M15Bias, at: int) -> None:
        """A carried bias keeps the remembered one and its time (it is not a new reading)."""
        if bias.carried and self._bias is not None:
            return
        self._bias, self._at = bias, at

    def latest(self) -> tuple[M15Bias | None, int | None]:
        return self._bias, self._at
