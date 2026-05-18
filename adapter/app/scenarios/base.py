from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from ..models import LayerPlanRequest


Side = Literal["buy", "sell", "both", "none"]


@dataclass
class ScenarioResult:
    name: str
    score: float           # 0..1
    side: Side
    confidence: float      # 0..1
    invalidation_price: float | None
    key_levels: dict[str, float] = field(default_factory=dict)
    reason_codes: list[str] = field(default_factory=list)


class Scenario(Protocol):
    name: str

    def evaluate(self, req: LayerPlanRequest) -> ScenarioResult: ...


def _safe(features: dict, key: str, default: float = 0.0) -> float:
    val = features.get(key, default)
    try:
        return float(val)
    except (TypeError, ValueError):
        return default
