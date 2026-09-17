"""
Trading days and the day summary a session stop reports (plan section 4b).

A trading day runs from 17:00 New York to 17:00 New York and is named after
the date it ends on, so the Sunday reopen belongs to Monday. The summary counts
the day's cycles, hold reasons and intents (per lifecycle status) and shows what
the EA last said is still open.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from types import MappingProxyType
from typing import Final

from ..ledger_cycles import TRADING_DAY_PATTERN, LedgerCycles
from ..ledger_cycles_schema import CycleSummary, SessionRecord
from ..market.sessions import NEW_YORK, NY_DAILY_CLOSE
from .ea_state import EaStateView

_UNIX_EPOCH: Final[datetime] = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ONE_DAY: Final[timedelta] = timedelta(days=1)
_NO_COUNTS: Final[Mapping[str, int]] = MappingProxyType({})


# --- trading day ---------------------------------------------------------------
def trading_day_for(epoch: int) -> str:
    """ISO date of the trading day containing `epoch` (UTC seconds)."""
    if isinstance(epoch, bool) or not isinstance(epoch, int):
        raise TypeError("epoch must be int UTC seconds")
    ny = (_UNIX_EPOCH + timedelta(seconds=epoch)).astimezone(NEW_YORK)
    day = ny.date() + _ONE_DAY if ny.time() >= NY_DAILY_CLOSE else ny.date()
    return day.isoformat()


def _ny_close_epoch(day: date) -> int:
    local = datetime.combine(day, NY_DAILY_CLOSE, tzinfo=NEW_YORK)
    return int((local - _UNIX_EPOCH).total_seconds())


def trading_day_bounds(trading_day: str) -> tuple[int, int]:
    """[start, end) in UTC seconds: the previous and the same day's New York close."""
    if not TRADING_DAY_PATTERN.match(trading_day):
        raise ValueError("trading_day must be YYYY-MM-DD")
    day = date.fromisoformat(trading_day)
    return _ny_close_epoch(day - _ONE_DAY), _ny_close_epoch(day)


# --- what the EA reports ---------------------------------------------------------
@dataclass(frozen=True)
class OpenExposure:
    """What the EA last said is still running; None everywhere until it has polled."""

    open_v6_positions: int | None
    pending_v6_orders: int | None
    floating_pnl_v6: float | None
    local_halt: bool | None
    observed_at: float | None

    @classmethod
    def from_view(cls, view: EaStateView) -> "OpenExposure":
        if view.last_poll is None:
            return cls(None, None, None, None, None)
        poll = view.last_poll.poll
        return cls(poll.open_v6_positions, poll.pending_v6_orders, poll.floating_pnl_v6,
                   poll.local_halt, view.last_poll.received_at)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def last_trade_mode(view: EaStateView) -> str | None:
    """Trade mode from the newest EA poll or snapshot; None if the EA was never seen."""
    seen: list[tuple[float, str]] = []
    if view.last_poll is not None:
        seen.append((view.last_poll.received_at, view.last_poll.poll.trade_mode))
    if view.last_snapshot is not None:
        seen.append((view.last_snapshot.received_at, view.last_snapshot.trade_mode))
    return max(seen, key=lambda item: item[0])[1] if seen else None


def session_to_dict(record: SessionRecord) -> dict[str, object]:
    return {**asdict(record), "active": record.is_active}


# --- the day summary ---------------------------------------------------------------
def read_day(ledger: LedgerCycles, trading_day: str,
             ) -> tuple[CycleSummary, tuple[SessionRecord, ...]]:
    """Blocking: cycle counts and sessions of one trading day."""
    start, end = trading_day_bounds(trading_day)
    return ledger.cycle_summary(start, end), ledger.sessions_for_day(trading_day)


def read_intent_statuses(ledger: LedgerCycles, trading_day: str) -> Mapping[str, int]:
    """Blocking: intents published during the trading day, per lifecycle status."""
    start, end = trading_day_bounds(trading_day)
    return ledger.intents.status_counts(start, end)


@dataclass(frozen=True)
class DaySummary:
    trading_day: str
    day_start_epoch: int
    day_end_epoch: int
    cycles: int
    by_status: Mapping[str, int]
    hold_reasons: Mapping[str, int]
    shadow_intents: int
    sessions: tuple[SessionRecord, ...]
    exposure: OpenExposure
    intents: int = 0                    # cycles whose intent was published (ENTER)
    intent_statuses: Mapping[str, int] = field(default=_NO_COUNTS)

    @classmethod
    def build(cls, trading_day: str, counts: CycleSummary,
              sessions: tuple[SessionRecord, ...], exposure: OpenExposure,
              intent_statuses: Mapping[str, int] = _NO_COUNTS) -> "DaySummary":
        start, end = trading_day_bounds(trading_day)
        return cls(
            trading_day=trading_day, day_start_epoch=start, day_end_epoch=end,
            cycles=counts.total, by_status=MappingProxyType(dict(counts.by_status)),
            hold_reasons=MappingProxyType(dict(counts.by_hold_reason)),
            shadow_intents=counts.shadow_entries, sessions=tuple(sessions), exposure=exposure,
            intents=counts.entries, intent_statuses=MappingProxyType(dict(intent_statuses)))

    def to_dict(self) -> dict[str, object]:
        return {
            "trading_day": self.trading_day, "day_start_epoch": self.day_start_epoch,
            "day_end_epoch": self.day_end_epoch, "cycles": self.cycles,
            "by_status": dict(self.by_status), "hold_reasons": dict(self.hold_reasons),
            "shadow_intents": self.shadow_intents, "intents": self.intents,
            "intent_statuses": dict(self.intent_statuses),
            "sessions": [session_to_dict(s) for s in self.sessions],
            "exposure": self.exposure.to_dict(),
        }
