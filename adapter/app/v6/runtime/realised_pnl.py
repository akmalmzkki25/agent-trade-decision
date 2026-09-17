"""
Realised V6 P&L per breaker period for one MT5 login (plan section 6, gate 10).

Reading: `basket_results` rows with version "v6", joined to the intent named by
the basket id, the cycle and snapshot that produced it (hence the login) and the
chosen candidate's label, become `OutcomeRecord`s (`OutcomeReader`, one
short-lived read-only connection per call).

Summing: `realised_pnl` adds up one login's results per UTC day, ISO week and
month (the `risk.breakers` periods). A result whose account cannot be resolved
counts against every login when it lost money and is ignored when it made money,
and a loss whose close time is unknown counts in every period: a broken link can
only make the breakers stricter. `RealisedPnlReader.read` raises ValueError when
a row in the window is unreadable, and the breaker feed then fails closed.

The EA may know about a close before its result reaches the adapter (it waits in
the EA outbox), so `breaker_realized(scope, ea_realized_today)` takes the lower
of the two daily figures and carries that difference into the week and month.

Breaker feed (the EA's day facts must describe the same login and UTC day):
    pnl = RealisedPnlReader(db_path).read(login, as_of_epoch)
    inputs = pnl.breaker_inputs(equity=..., floating_v6=..., daily_start=...,
                                weekly_start=..., monthly_start=...,
                                ea_realized_today=day.realized_today)
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from decimal import Decimal
from os import PathLike
from pathlib import Path
from typing import Any, Final

from ..learning.outcomes import (
    CHOSEN_VERDICT, MAX_ID_CHARS, MONEY_PLACES, RESULT_COLUMNS, V6_VERSION, ZERO, BasketResult,
    CounterfactualLabel, OutcomeRecord, finite_number, to_decimal,
)
from ..ledger_cycles import MAX_LIST_LIMIT, BreakerScope
from ..ledger_intents import IntentRecord
from ..risk.breakers import (
    SCOPES, BreakerInputs, PeriodInput, period_key, period_start_epoch,
)

logger = logging.getLogger(__name__)

BASKET_TABLE: Final[str] = "basket_results"
READ_TIMEOUT_S: Final[float] = 5.0
# A result is stored after its close, so its receipt never precedes the close by
# more than clock noise; scanning by receipt time with this slack misses nothing.
RECEIPT_SLACK_S: Final[int] = 86_400
SECONDS_PER_DAY: Final[int] = 86_400
DAYS_PER_WEEK: Final[int] = 7
# Any moment of a month plus 32 days lies in the following month, never later.
NEXT_MONTH_OFFSET_S: Final[int] = 32 * SECONDS_PER_DAY
LOGIN_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9]{1,20}")

# --- reading the journal --------------------------------------------------------------------
_INTENT_COLUMNS: Final[tuple[str, ...]] = tuple(f.name for f in fields(IntentRecord))
_LABEL_COLUMNS: Final[tuple[str, ...]] = tuple(f.name for f in fields(CounterfactualLabel))
_N_RESULT: Final[int] = len(RESULT_COLUMNS)
_N_LINKED: Final[int] = _N_RESULT + len(_INTENT_COLUMNS)
_SELECT_LIST: Final[str] = ", ".join((
    *(f"b.{name}" for name in RESULT_COLUMNS), *(f"i.{name}" for name in _INTENT_COLUMNS),
    *(f"k.{name}" for name in _LABEL_COLUMNS), "s.login"))
# Fixed text built from module literals; every value is bound.
_OUTCOME_SQL: Final[str] = (
    f"SELECT {_SELECT_LIST} FROM basket_results AS b"
    " LEFT JOIN v6_intents AS i"
    "  ON b.basket_id LIKE '%-V6B-%' AND i.intent_id = substr(b.basket_id, -12)"
    " LEFT JOIN v6_candidates AS k ON k.candidate_id = ("
    "  SELECT MIN(x.candidate_id) FROM v6_candidates AS x"
    f"  WHERE x.cycle_id = i.cycle_id AND x.verdict = '{CHOSEN_VERDICT}')"
    " LEFT JOIN v6_cycles AS c ON c.cycle_id = i.cycle_id"
    " LEFT JOIN v6_snapshots AS s ON s.snapshot_id = c.snapshot_id"
    f" WHERE b.version = '{V6_VERSION}'")
_NEWEST_FIRST: Final[str] = " ORDER BY b.created_at DESC, b.rowid DESC"
_SINCE_SQL: Final[str] = (
    _OUTCOME_SQL + " AND (b.created_at IS NULL OR b.created_at >= ?)" + _NEWEST_FIRST)
_RECENT_SQL: Final[str] = _OUTCOME_SQL + _NEWEST_FIRST + " LIMIT ?"
_BASKET_SQL: Final[str] = _OUTCOME_SQL + " AND b.basket_id = ?"
_TABLE_SQL: Final[str] = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?"


@dataclass(frozen=True)
class OutcomeQuery:
    """Readable outcomes, newest first, and the basket ids of unreadable rows."""

    records: tuple[OutcomeRecord, ...] = ()
    invalid: tuple[str, ...] = ()


def _record(row: Sequence[Any]) -> OutcomeRecord:
    result = BasketResult.from_row(row[:_N_RESULT])
    intent_row = row[_N_RESULT:_N_LINKED]
    if intent_row[0] is None or intent_row[0] != result.intent_id:
        return OutcomeRecord(result)
    label_row = row[_N_LINKED:-1]
    label = None if label_row[0] is None else CounterfactualLabel(*label_row)
    login = row[-1]
    return OutcomeRecord(result, IntentRecord(*intent_row), label,
                         None if login is None else str(login))


def _collect(conn: sqlite3.Connection, sql: str, params: tuple[object, ...]) -> OutcomeQuery:
    if conn.execute(_TABLE_SQL, (BASKET_TABLE,)).fetchone() is None:
        return OutcomeQuery()      # nothing ever reported a basket into this file
    records: list[OutcomeRecord] = []
    invalid: list[str] = []
    for row in conn.execute(sql, params).fetchall():
        try:
            records.append(_record(row))
        except (TypeError, ValueError) as exc:
            basket_id = str(row[0])[:MAX_ID_CHARS]
            logger.warning("v6 outcome: basket result %r is unreadable (%s)", basket_id,
                           type(exc).__name__)
            invalid.append(basket_id)
    return OutcomeQuery(tuple(records), tuple(invalid))


def outcomes_since(conn: sqlite3.Connection, since_epoch: int) -> OutcomeQuery:
    """V6 outcomes closed at or after `since_epoch` (and any whose close time is unknown)."""
    if isinstance(since_epoch, bool) or not isinstance(since_epoch, int):
        raise TypeError("since_epoch must be int UTC seconds")
    bound = datetime.fromtimestamp(max(since_epoch - RECEIPT_SLACK_S, 0), timezone.utc)
    found = _collect(conn, _SINCE_SQL, (bound.isoformat(),))
    kept = tuple(record for record in found.records
                 if record.result.closed_epoch is None
                 or record.result.closed_epoch >= since_epoch)
    return replace(found, records=kept)


def recent_outcomes(conn: sqlite3.Connection, limit: int) -> OutcomeQuery:
    """The newest `limit` V6 outcomes by receipt."""
    if not 1 <= limit <= MAX_LIST_LIMIT:
        raise ValueError(f"limit must be within 1-{MAX_LIST_LIMIT}")
    return _collect(conn, _RECENT_SQL, (limit,))


@contextmanager
def read_only_connection(db_path: str) -> Iterator[sqlite3.Connection]:
    """A read-only connection to the adapter database, always closed on exit."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=READ_TIMEOUT_S)
    try:
        yield conn
    finally:
        conn.close()


class OutcomeReader:
    """Blocking and read-only: one short-lived connection per call; sqlite3.Error propagates.

    `db_path` must be the file the V6 basket-result route writes `basket_results` to.
    """

    def __init__(self, db_path: str | PathLike[str]) -> None:
        self._db_path = str(db_path)

    def since(self, since_epoch: int) -> OutcomeQuery:
        with read_only_connection(self._db_path) as conn:
            return outcomes_since(conn, since_epoch)

    def recent(self, limit: int = 20) -> OutcomeQuery:
        with read_only_connection(self._db_path) as conn:
            return recent_outcomes(conn, limit)

    def for_basket(self, basket_id: str) -> OutcomeRecord | None:
        with read_only_connection(self._db_path) as conn:
            found = _collect(conn, _BASKET_SQL, (basket_id,))
        return found.records[0] if found.records else None


# --- periods ---------------------------------------------------------------------------------
def period_bounds(scope: BreakerScope, epoch: int) -> tuple[int, int]:
    """[start, end) in UTC seconds of the breaker period containing `epoch`."""
    start = period_start_epoch(scope, epoch)
    if scope == "monthly":
        return start, period_start_epoch("monthly", start + NEXT_MONTH_OFFSET_S)
    days = DAYS_PER_WEEK if scope == "weekly" else 1
    return start, start + days * SECONDS_PER_DAY


def window_start(as_of_epoch: int) -> int:
    """The earliest start among the day, week and month containing `as_of_epoch`."""
    return min(period_start_epoch(scope, as_of_epoch) for scope in SCOPES)


@dataclass(frozen=True)
class PeriodPnl:
    """One period's closed V6 results for the login (money in account currency)."""

    scope: BreakerScope
    period_key: str
    start_epoch: int
    end_epoch: int
    realized: Decimal = ZERO             # the login's results
    trades: int = 0
    unattributed_loss: Decimal = ZERO    # losing results of an unknown account (<= 0)
    unattributed_trades: int = 0

    @property
    def base(self) -> Decimal:
        """What the breaker counts before the EA day correction."""
        return self.realized + self.unattributed_loss

    def to_dict(self) -> dict[str, object]:
        return {"period_key": self.period_key, "start_epoch": self.start_epoch,
                "end_epoch": self.end_epoch, "trades": self.trades,
                "realized": float(round(self.realized, MONEY_PLACES)),
                "unattributed_loss": float(round(self.unattributed_loss, MONEY_PLACES)),
                "unattributed_trades": self.unattributed_trades,
                "breaker_realized": float(round(self.base, MONEY_PLACES))}


@dataclass(frozen=True)
class RealisedPnl:
    login: str
    as_of_epoch: int
    periods: tuple[PeriodPnl, ...]       # daily, weekly, monthly

    def period(self, scope: BreakerScope) -> PeriodPnl:
        for item in self.periods:
            if item.scope == scope:
                return item
        raise ValueError(f"unknown breaker scope {str(scope)[:16]!r}")

    def breaker_realized(self, scope: BreakerScope,
                         ea_realized_today: float | None = None) -> float:
        """The period's realised V6 P&L for `risk.breakers.PeriodInput`.

        With the EA's realised-today figure (same login, same UTC day), a loss the
        EA knows and the journal does not yet is added to every period.
        """
        base = self.period(scope).base
        if ea_realized_today is None:
            return float(base)
        if not finite_number(ea_realized_today):
            raise ValueError("ea_realized_today must be a finite number")
        lag = min(ZERO, to_decimal(ea_realized_today) - self.period("daily").base)
        return float(base + lag)

    def period_inputs(self, *, daily_start: float, weekly_start: float, monthly_start: float,
                      ea_realized_today: float | None = None,
                      ) -> tuple[PeriodInput, PeriodInput, PeriodInput]:
        """One PeriodInput per scope; raises ValueError for a non-positive start equity."""
        def build(scope: BreakerScope, start: float) -> PeriodInput:
            return PeriodInput(scope=scope, start_equity=start,
                               realized_v6=self.breaker_realized(scope, ea_realized_today))

        return (build("daily", daily_start), build("weekly", weekly_start),
                build("monthly", monthly_start))

    def breaker_inputs(self, *, equity: float, floating_v6: float, daily_start: float,
                       weekly_start: float, monthly_start: float,
                       ea_realized_today: float | None = None) -> BreakerInputs:
        periods = self.period_inputs(daily_start=daily_start, weekly_start=weekly_start,
                                     monthly_start=monthly_start,
                                     ea_realized_today=ea_realized_today)
        return BreakerInputs(as_of_epoch=self.as_of_epoch, equity=equity,
                             floating_v6=floating_v6, periods=periods)

    def to_dict(self) -> dict[str, object]:
        return {"login": self.login, "as_of_epoch": self.as_of_epoch,
                "periods": {item.scope: item.to_dict() for item in self.periods}}


def _require_login(login: str) -> str:
    if not isinstance(login, str) or not LOGIN_PATTERN.fullmatch(login):
        raise ValueError("login must be 1-20 digits")
    return login


def _counts_in(record: OutcomeRecord, start: int, end: int) -> bool:
    closed = record.result.closed_epoch
    if closed is None:
        return record.kind == "loss"     # unknown close time: a loss counts everywhere
    return start <= closed < end


def _period(scope: BreakerScope, as_of_epoch: int, outcomes: tuple[OutcomeRecord, ...],
            login: str) -> PeriodPnl:
    start, end = period_bounds(scope, as_of_epoch)
    inside = [record for record in outcomes if _counts_in(record, start, end)]
    mine = [record.net_pnl for record in inside if record.login == login]
    unknown = [record.net_pnl for record in inside
               if record.login is None and record.kind == "loss"]
    return PeriodPnl(scope=scope, period_key=period_key(scope, as_of_epoch), start_epoch=start,
                     end_epoch=end, realized=sum(mine, ZERO), trades=len(mine),
                     unattributed_loss=sum(unknown, ZERO), unattributed_trades=len(unknown))


def realised_pnl(outcomes: Iterable[OutcomeRecord], login: str,
                 as_of_epoch: int) -> RealisedPnl:
    """Pure: `login`'s realised V6 P&L in the day, week and month containing `as_of_epoch`."""
    _require_login(login)
    items = tuple(outcomes)
    return RealisedPnl(login=login, as_of_epoch=as_of_epoch,
                       periods=tuple(_period(scope, as_of_epoch, items, login)
                                     for scope in SCOPES))


class RealisedPnlReader:
    """The breaker feed's facade: blocking `read`, or `for_login` from the event loop."""

    def __init__(self, db_path: str | PathLike[str]) -> None:
        self._outcomes = OutcomeReader(db_path)

    def read(self, login: str, as_of_epoch: int) -> RealisedPnl:
        """Raises ValueError (bad login, unreadable result in the window) or sqlite3.Error."""
        _require_login(login)
        found = self._outcomes.since(window_start(as_of_epoch))
        if found.invalid:
            raise ValueError(f"{len(found.invalid)} V6 basket results in the breaker window "
                             "are unreadable")
        return realised_pnl(found.records, login, as_of_epoch)

    async def for_login(self, login: str, as_of_epoch: int) -> RealisedPnl:
        return await asyncio.to_thread(self.read, login, as_of_epoch)
