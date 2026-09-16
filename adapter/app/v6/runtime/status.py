"""
The one runtime status word (plan sections 4 and 8).

The watchdog, GET /v6/status and the /v6 dashboard all classify through here, so
the three can never disagree about whether V6 is RUNNING or STALE.

A snapshot is stale only after SNAPSHOT_STALE_AFTER_S of open market without one.
No bar forms while the market is closed (weekend, rollover block, the broker's
daily quote gap), so the EA sends nothing then; the clock restarts when quotes
return, which keeps the daily 20:00-22:00 UTC gap from raising a false STALE.
"""

from __future__ import annotations

from typing import Final

from ..market.broker_hours import DailyUtcWindow, market_closed
from .ea_state import EaStateView

STATUS_DISABLED: Final[str] = "DISABLED"
STATUS_HALTED: Final[str] = "HALTED"
STATUS_BREAKER: Final[str] = "BREAKER"
STATUS_WAITING_EA: Final[str] = "WAITING_EA"
STATUS_STALE: Final[str] = "STALE"
STATUS_RUNNING: Final[str] = "RUNNING"
# The EA posts one snapshot per M15 close; two missed bars plus a minute is stale.
SNAPSHOT_STALE_AFTER_S: Final[int] = 2 * 900 + 60
# Closures start and end on whole minutes, so one sample a minute finds them.
MARKET_SAMPLE_S: Final[int] = 60


def runtime_status(*, active: bool, halted: bool, breakers_tripped: bool,
                   ea_age_s: float | None, ea_stale_s: float) -> str:
    """The single word the page leads with, most severe first (snapshots aside)."""
    if not active:
        return STATUS_DISABLED
    if halted:
        return STATUS_HALTED
    if breakers_tripped:
        return STATUS_BREAKER
    if ea_age_s is None:
        return STATUS_WAITING_EA
    return STATUS_STALE if ea_age_s > ea_stale_s else STATUS_RUNNING


def _open_for_longer_than(end_epoch: int, span_s: int,
                          quote_gap: DailyUtcWindow | None) -> bool:
    """Open at every sample of [end - span - 1, end]: open for more than `span_s`."""
    offsets = (*range(0, span_s + 1, MARKET_SAMPLE_S), span_s + 1)
    return not any(market_closed(end_epoch - offset, quote_gap) for offset in offsets)


def snapshot_stale(view: EaStateView, now: float, *,
                   quote_gap: DailyUtcWindow | None) -> bool:
    """No snapshot for SNAPSHOT_STALE_AFTER_S while the market was open all along."""
    last = view.last_snapshot
    if last is None or now - last.received_at <= SNAPSHOT_STALE_AFTER_S:
        return False
    return _open_for_longer_than(int(now), SNAPSHOT_STALE_AFTER_S, quote_gap)


def classify(*, active: bool, halted: bool, breaker_tripped: bool, ea_age_s: float | None,
             ea_stale_s: float, stale_snapshot: bool) -> str:
    status = runtime_status(active=active, halted=halted, breakers_tripped=breaker_tripped,
                            ea_age_s=ea_age_s, ea_stale_s=ea_stale_s)
    return STATUS_STALE if status == STATUS_RUNNING and stale_snapshot else status
