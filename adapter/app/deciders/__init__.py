from __future__ import annotations

from ..settings import settings
from .base import Decider
from .dummy_trend_breakout import DummyTrendBreakoutDecider


def get_decider() -> Decider:
    name = settings.decider
    if name == "dummy_trend_breakout":
        return DummyTrendBreakoutDecider()
    if name == "claude":
        from .claude import ClaudeDecider

        return ClaudeDecider()
    raise ValueError(f"Unknown decider: {name}")
