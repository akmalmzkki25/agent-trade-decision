from __future__ import annotations

from typing import Protocol

from ..models import DecisionRequest, DecisionResponse


class Decider(Protocol):
    def decide(self, req: DecisionRequest) -> DecisionResponse:
        ...
