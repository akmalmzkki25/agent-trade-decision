"""
Breaker inputs the runtime owns (plan section 6 gate 10).

`risk.breakers` is pure and needs period anchors: the equity at the start of each
UTC day, ISO week and month. The EA reports the day's start equity in every
snapshot; the week and month anchors come from the first per-minute account mark
(`v6_account_marks`, written by the poll route) inside the period. When the
adapter started after the period began, that mark is the best anchor it has.

Realised V6 P&L is only known for today (the EA's DayBlock). Phase 2 places no
orders, so it is zero in practice; Phase 5 must feed weekly and monthly realised
V6 results (basket_results with version "v6").

Account equity includes every strategy on the account, so a V5 loss on the same
demo account trips the V6 equity breaker too, by design.

Anchors and the carried day facts belong to one MT5 login: when the EA moves to
another account, the new account is never measured against the old one's equity.
A reading with no equity (a terminal that has not connected yet) is unusable,
not a 100% loss: it fails closed for that evaluation and persists no trip.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ..clock import Clock
from ..config import V6Settings
from ..cycle_types import MarketContext
from ..ledger_cycles import LedgerCycles
from ..risk.breakers import (
    SCOPES as PERIOD_SCOPES, BreakerInputs, BreakerStatus, PeriodInput, account_usable,
    inputs_from_context, period_start_epoch, refresh_breakers,
)
from ..schemas.intent import PollRequest

logger = logging.getLogger(__name__)

READ_TIMEOUT_S: Final[float] = 5.0
SECONDS_PER_DAY: Final[int] = 86_400
INPUTS_UNUSABLE: Final[str] = "breaker inputs unusable"
LEDGER_UNAVAILABLE: Final[str] = "breaker ledger unavailable"
_FIRST_MARK_SQL: Final[str] = (
    "SELECT equity FROM v6_account_marks WHERE login = ? AND minute_epoch >= ?"
    " AND minute_epoch <= ? AND equity > 0 ORDER BY minute_epoch ASC LIMIT 1")


@dataclass(frozen=True)
class PeriodAnchors:
    """First marked equity of the day, week and month containing `as_of_epoch`."""

    as_of_epoch: int
    daily: float | None
    weekly: float | None
    monthly: float | None


@dataclass(frozen=True)
class DayFacts:
    """The EA's own view of the UTC day for one login, from the newest snapshot."""

    login: str
    day_start_epoch: int
    day_start_equity: float
    realized_today: float

    @classmethod
    def from_context(cls, context: MarketContext) -> "DayFacts":
        return cls(login=context.account.login,
                   day_start_epoch=_day_start(context.as_of_epoch),
                   day_start_equity=context.day.day_start_equity,
                   realized_today=context.day.realized_today)


class MarksReader:
    """Read-only access to v6_account_marks over a short-lived connection."""

    def __init__(self, db_path: str) -> None:
        self._uri = Path(db_path).resolve().as_uri() + "?mode=ro"

    @staticmethod
    def _first_equity(conn: sqlite3.Connection, login: str, since_epoch: int,
                      until_epoch: int) -> float | None:
        row = conn.execute(_FIRST_MARK_SQL, (login, since_epoch, until_epoch)).fetchone()
        return None if row is None else float(row[0])

    def anchors(self, as_of_epoch: int, login: str) -> PeriodAnchors:
        """Blocking: `login`'s first marks; raises sqlite3.Error when unreadable."""
        conn = sqlite3.connect(self._uri, uri=True, timeout=READ_TIMEOUT_S)
        try:
            found = {scope: self._first_equity(conn, login,
                                               period_start_epoch(scope, as_of_epoch),
                                               as_of_epoch)
                     for scope in PERIOD_SCOPES}
        finally:
            conn.close()
        return PeriodAnchors(as_of_epoch=as_of_epoch, daily=found["daily"],
                             weekly=found["weekly"], monthly=found["monthly"])


def _day_start(epoch: int) -> int:
    return epoch - epoch % SECONDS_PER_DAY


def _same_day(day: DayFacts | None, login: str, as_of: int) -> bool:
    """The carried day facts describe this login's current UTC day."""
    return day is not None and day.login == login and day.day_start_epoch == _day_start(as_of)


def _first_known(*values: float | None) -> float:
    return next((value for value in values if value is not None and value > 0), 0.0)


def _longer_periods(anchors: PeriodAnchors, fallback: float,
                    realized: float) -> tuple[PeriodInput, PeriodInput]:
    """Week and month inputs; a period without a mark starts at `fallback`."""
    weekly = PeriodInput(scope="weekly", start_equity=_first_known(anchors.weekly, fallback),
                         realized_v6=realized)
    monthly = PeriodInput(scope="monthly", start_equity=_first_known(anchors.monthly, fallback),
                          realized_v6=realized)
    return weekly, monthly


def _unavailable(ledger: LedgerCycles, detail: str) -> BreakerStatus:
    try:
        active = ledger.active_breakers()
    except sqlite3.Error:
        return BreakerStatus(unavailable=LEDGER_UNAVAILABLE)
    return BreakerStatus(active=active, unavailable=detail)


class BreakerFeed:
    """Builds the BreakerStatus a cycle or the watchdog acts on, persisting new trips."""

    def __init__(self, *, ledger: LedgerCycles, marks: MarksReader, settings: V6Settings,
                 clock: Clock) -> None:
        self._ledger = ledger
        self._marks = marks
        self._settings = settings
        self._clock = clock

    def _refresh(self, inputs: BreakerInputs) -> BreakerStatus:
        return refresh_breakers(self._ledger, inputs, self._settings, self._clock.now_epoch())

    def _context_inputs(self, context: MarketContext) -> BreakerInputs:
        anchors = self._marks.anchors(context.as_of_epoch, context.account.login)
        day = context.day
        weekly, monthly = _longer_periods(anchors, day.day_start_equity, day.realized_today)
        return inputs_from_context(context, weekly=weekly, monthly=monthly)

    def context_status(self, context: MarketContext) -> BreakerStatus:
        """Blocking: daily figures from the snapshot, week and month from the marks."""
        try:
            return self._refresh(self._context_inputs(context))
        except sqlite3.Error as exc:
            logger.error("v6 breaker anchors unreadable (%s)", type(exc).__name__)
            return _unavailable(self._ledger, LEDGER_UNAVAILABLE)
        except ValueError as exc:
            logger.warning("v6 breaker inputs unusable for %s: %s", context.cycle_id, exc)
            return _unavailable(self._ledger, INPUTS_UNUSABLE)

    async def for_context(self, context: MarketContext) -> BreakerStatus:
        return await asyncio.to_thread(self.context_status, context)

    def poll_status(self, poll: PollRequest, now: float,
                    day: DayFacts | None) -> BreakerStatus | None:
        """Blocking: the status from the newest poll; None when no day anchor is known.

        A poll without usable equity or login fails closed without persisting a
        trip. Raises sqlite3.Error when the marks cannot be read (the watchdog logs it).
        """
        if not account_usable(poll.login, poll.equity):
            logger.debug("v6 breaker poll inputs unusable (login or equity missing)")
            return _unavailable(self._ledger, INPUTS_UNUSABLE)
        as_of = int(now)
        anchors = self._marks.anchors(as_of, poll.login)
        today = day if _same_day(day, poll.login, as_of) else None
        daily_start = _first_known(None if today is None else today.day_start_equity,
                                   anchors.daily)
        if daily_start <= 0:
            return None
        realized = 0.0 if today is None else today.realized_today
        weekly, monthly = _longer_periods(anchors, daily_start, realized)
        daily = PeriodInput(scope="daily", start_equity=daily_start, realized_v6=realized)
        return self._refresh(BreakerInputs(as_of_epoch=as_of, equity=poll.equity,
                                           floating_v6=poll.floating_pnl_v6,
                                           periods=(daily, weekly, monthly)))

    async def for_poll(self, poll: PollRequest, now: float,
                       day: DayFacts | None) -> BreakerStatus | None:
        return await asyncio.to_thread(self.poll_status, poll, now, day)
