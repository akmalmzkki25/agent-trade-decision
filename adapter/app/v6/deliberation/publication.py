"""
The engine's side of publishing an approved entry (execute mode only).

The engine never publishes by itself: it hands a `PublishRequest` to an
`IntentPort` (`runtime.publisher.IntentPublisher`) and records the outcome.

  published    `PublishOutcome.intent_id` is set: the cycle is ENTER
  not armed    neither an intent id nor a hold reason: the decision is only
               recorded (ENTER_SHADOW)
  refused      a hold reason: a safety check refused publishing
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..cycle_codes import HoldReason
from ..cycle_types import MarketContext, ProtocolDecision
from ..types import Candidate, ExitPlan, SizingResult


@dataclass(frozen=True)
class PublishRequest:
    decision: ProtocolDecision
    candidate: Candidate
    exit_plan: ExitPlan
    sizing: SizingResult
    context: MarketContext
    agent: str


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


class IntentPort(Protocol):
    async def publish(self, request: PublishRequest) -> PublishOutcome: ...
