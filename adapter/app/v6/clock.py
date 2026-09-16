"""Injectable clock so time-dependent V6 logic is testable without sleeping."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol


class Clock(Protocol):
    def now_epoch(self) -> float:
        """Seconds since the Unix epoch, UTC."""
        ...


class SystemClock:
    def now_epoch(self) -> float:
        return time.time()


@dataclass
class FakeClock:
    """Manually advanced clock for tests."""

    epoch: float = 1_789_560_000.0
    _history: list[float] = field(default_factory=list)

    def now_epoch(self) -> float:
        return self.epoch

    def advance(self, seconds: float) -> None:
        self._history.append(self.epoch)
        self.epoch += seconds
