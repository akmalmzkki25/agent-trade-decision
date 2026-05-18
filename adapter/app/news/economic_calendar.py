"""
Economic calendar stub.

This module returns "no blackout" for all calls during the bulk-layering Phase A.
A real implementation will fetch Investing.com / FXStreet economic calendar,
parse impact ratings (low / medium / high), and emit a BlackoutDecision when a
high-impact event is within ±N minutes of `now_utc`.

The interface is intentionally tiny so a future LLM reasoning layer can build
on it (e.g., narrative summary of upcoming events) without changing callers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Impact = Literal["low", "medium", "high"]


class CalendarEvent(BaseModel):
    model_config = ConfigDict(strict=True)

    title: str
    impact: Impact
    minutes_to: int  # negative if past, positive if future
    country: str = ""


class BlackoutDecision(BaseModel):
    model_config = ConfigDict(strict=True)

    blackout: bool = False
    severity: float = Field(ge=0.0, le=1.0, default=0.0)
    event: str = ""


def get_blackout(symbol: str, now_utc: datetime) -> BlackoutDecision:
    """
    Phase A stub: never blacks out.

    TODO (Phase B):
      - Fetch upcoming events from Investing.com / FXStreet for the relevant
        currency basket of `symbol` (e.g., XAUUSD ↔ USD events).
      - severity rule: high impact within ±15 min => 1.0; medium within ±10 min => 0.6;
        otherwise 0.0.
      - Cache results for 60s, surface as `BlackoutDecision`.
    """
    return BlackoutDecision(blackout=False, severity=0.0, event="")
