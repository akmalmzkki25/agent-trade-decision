"""The `m1_state` block of an m1 packet (spec section 1): what the closed M1 bars did."""

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field, field_validator

from .operator_parts import Frozen

MoveDirection = Literal["up", "down", "flat"]
DISTANCE_KEYS: Final[tuple[str, ...]] = ("entry", "sl", "tp1", "tp2", "tp3")


class MinuteMove(Frozen):
    """The move over the last `bars` closed M1 bars; strength is |change| / ATR(M1)."""

    bars: int = Field(ge=1, le=60)
    change: float
    direction: MoveDirection
    strength: float = Field(ge=0)


class MinuteState(Frozen):
    """ATR(14) and 15-bar range of the closed M1 bars, the 5- and 15-bar moves, the quote
    rate of the minute and, for a managed trade, each level minus the price that triggers it."""

    atr_m1: float = Field(ge=0)
    range_15: float = Field(ge=0)
    last_5: MinuteMove
    last_15: MinuteMove
    quotes_per_s: float = Field(ge=0)
    max_gap_ms: int = Field(ge=0)
    distances: dict[str, float] = Field(default_factory=dict, max_length=len(DISTANCE_KEYS))

    @field_validator("distances")
    @classmethod
    def _known_levels(cls, value: dict[str, float]) -> dict[str, float]:
        unknown = sorted(set(value) - set(DISTANCE_KEYS))
        if unknown:
            raise ValueError(f"unknown distance keys {unknown}")
        return value
