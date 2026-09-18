"""
The engine's side of publishing an approved entry (execute mode only).

The engine never publishes by itself: it hands a `PublishRequest` to an
`IntentPort` (`runtime.publisher.IntentPublisher`) and records the outcome.

  published    `PublishOutcome.intent_id` is set: the cycle is ENTER
  not armed    neither an intent id nor a hold reason: the decision is only
               recorded (ENTER_SHADOW)
  refused      a hold reason: a safety check refused publishing

A management decision travels the same way: a `ManageDispatch` (the agent's request
plus the EA command `management.build_action` made of it) goes to `IntentPort.manage`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol

from ..cycle_codes import HoldReason
from ..cycle_types import MarketContext, ProtocolDecision
from ..schemas.operator_plan import ManageRequest
from ..types import Candidate, ExitPlan, SizingResult, TradePlan

ACTION_PAYLOAD_FIELDS: Final[tuple[str, ...]] = (
    "sl", "tp", "tp1", "tp2", "sl_after_tp1", "sl_after_tp2", "price", "expiry_epoch",
    "barrier_s")


@dataclass(frozen=True)
class PublishRequest:
    decision: ProtocolDecision
    candidate: Candidate
    exit_plan: ExitPlan
    sizing: SizingResult
    context: MarketContext
    agent: str
    plan: TradePlan | None = None       # an agent plan v3: order type, ladder, windows


@dataclass(frozen=True)
class PublishOutcome:
    intent_id: str | None = None
    hold_reason: HoldReason | None = None
    code: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        if self.intent_id is not None and self.hold_reason is not None:
            raise ValueError("a published intent carries no hold reason")

    @property
    def published(self) -> bool:
        return self.intent_id is not None


@dataclass(frozen=True)
class ManagementAction:
    """An EA command in the making: the full values after a CLOSE or MODIFY (0 = none)."""

    action_id: str
    command: str
    ticket: int
    intent_id: str
    cycle_id: str
    issued_at: int
    sl: float = 0.0
    tp: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    sl_after_tp1: float = 0.0
    sl_after_tp2: float = 0.0
    price: float = 0.0
    expiry_epoch: int = 0
    barrier_s: int = 0

    def payload(self) -> dict[str, float | int]:
        return {name: getattr(self, name) for name in ACTION_PAYLOAD_FIELDS}


@dataclass(frozen=True)
class ManageDispatch:
    request: ManageRequest
    action: ManagementAction | None     # None for CANCEL (the CANCEL_PENDING command)
    cycle_id: str
    agent: str
    session_id: str


@dataclass(frozen=True)
class ManageOutcome:
    sent: bool
    code: str
    detail: str = ""


class IntentPort(Protocol):
    async def publish(self, request: PublishRequest) -> PublishOutcome: ...

    async def cancel_pending(self, reason: str) -> tuple[str, ...]:
        """Queue CANCEL_PENDING for the EA; returns the undelivered intents cancelled."""
        ...

    async def manage(self, dispatch: ManageDispatch) -> ManageOutcome:
        """Queue a management action (or CANCEL_PENDING) for the armed session."""
        ...
