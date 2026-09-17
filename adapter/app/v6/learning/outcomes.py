"""
Realised outcomes of V6 intents: the closing end of the decision journal (plan 7.1).

A closed V6 position reaches the adapter as a basket result (`version: "v6"`,
`basket_id = "<symbol>-V6B-<intent_id>"`, docs/v6-wire-contract.md section 6.5)
stored in `basket_results`. An `OutcomeRecord` links that result to its intent
(`v6_intents`), to the account whose snapshot started the cycle and to the
counterfactual label of the candidate the Chief chose (`v6_candidates`, verdict
"chosen"). Nothing is copied: `runtime.realised_pnl` builds the records on read
from those tables, and `outcome_for_event` links a result as it arrives.

  r_multiple  net_pnl / intent.risk_usd, the loss the sizer budgeted at the stop
  label_r     the triple-barrier label of the chosen candidate (a limit order at
              its entry, friction included) once it has resolved
  r_gap       r_multiple - label_r: what execution added or cost

A result whose basket id names no stored intent still counts in P&L (unlinked)
but has no R. Non-finite numbers are refused, never summed.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Final, Literal

from ...models import BasketResultEvent
from ..ledger_cycles import LedgerCycles
from ..ledger_cycles_schema import CandidateRecord
from ..ledger_intents import IntentRecord
from ..schemas.intent import intent_id_from_basket

OutcomeKind = Literal["win", "loss", "flat"]

V6_VERSION: Final[str] = "v6"
CHOSEN_VERDICT: Final[str] = "chosen"
LABELED_STATUS: Final[str] = "labeled"
MONEY_PLACES: Final[int] = 2
R_PLACES: Final[int] = 3
PCT_PLACES: Final[int] = 2
MIN_T_SAMPLE: Final[int] = 2
MAX_ID_CHARS: Final[int] = 64
ZERO: Final[Decimal] = Decimal(0)

_TEXT_FIELDS: Final[tuple[str, ...]] = (
    "basket_id", "symbol", "side", "opened_at_utc", "closed_at_utc", "close_reason")
_NUMBER_FIELDS: Final[tuple[str, ...]] = (
    "gross_profit", "gross_loss", "net_pnl", "max_floating_dd", "avg_slippage_points",
    "avg_spread_points", "equity_at_open", "equity_at_close")
# basket_results columns in the order `BasketResult.from_row` reads them.
RESULT_COLUMNS: Final[tuple[str, ...]] = (
    *_TEXT_FIELDS, *_NUMBER_FIELDS, "decision_latency_ms", "created_at")
_LINK_KEYS: Final[tuple[str, ...]] = (
    "cycle_id", "session_id", "agent", "source", "order_type", "entry", "sl", "tp", "lots",
    "risk_usd", "fill_price", "ticket")


# --- numbers and times -----------------------------------------------------------------
def finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def to_decimal(value: float) -> Decimal:
    """The exact decimal of a float's shortest repr (0.1 -> Decimal('0.1'))."""
    return Decimal(repr(float(value)))


def rounded(value: Decimal | None, places: int) -> float | None:
    return None if value is None else float(round(value, places))


def _round(value: float | None, places: int) -> float | None:
    return None if value is None else round(value, places)


def utc_epoch(text: str) -> int | None:
    """Epoch of an ISO 8601 time; a time without an offset is read as UTC."""
    try:
        moment = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp())


def r_multiple(net_pnl: float | None, risk_usd: float | None) -> Decimal | None:
    """net_pnl / risk_usd; None unless both are finite and the risk is positive."""
    if net_pnl is None or risk_usd is None:
        return None
    if not (finite_number(net_pnl) and finite_number(risk_usd)) or risk_usd <= 0:
        return None
    return to_decimal(net_pnl) / to_decimal(risk_usd)


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _number(value: object) -> float:
    number = 0.0 if value is None else float(value)  # type: ignore[arg-type]
    if not math.isfinite(number):
        raise ValueError("non-finite number")
    return number


# --- records -----------------------------------------------------------------------------
@dataclass(frozen=True)
class BasketResult:
    """The basket_results columns V6 reads; `received_at_utc` is the row's created_at."""

    basket_id: str
    symbol: str
    side: str
    opened_at_utc: str
    closed_at_utc: str
    close_reason: str
    gross_profit: float
    gross_loss: float
    net_pnl: float
    max_floating_dd: float
    avg_slippage_points: float
    avg_spread_points: float
    decision_latency_ms: int
    equity_at_open: float
    equity_at_close: float
    received_at_utc: str = ""

    def __post_init__(self) -> None:
        numbers = [getattr(self, name) for name in (*_NUMBER_FIELDS, "decision_latency_ms")]
        if not all(finite_number(value) for value in numbers):
            raise ValueError(f"basket {self.basket_id[:MAX_ID_CHARS]!r} has a non-finite number")

    @classmethod
    def from_event(cls, event: BasketResultEvent, received_at_utc: str = "") -> "BasketResult":
        """Raises ValueError for a result of another strategy or a non-finite number."""
        if event.version != V6_VERSION:
            raise ValueError(f"not a V6 basket result (version {event.version})")
        names = (*_TEXT_FIELDS, *_NUMBER_FIELDS, "decision_latency_ms")
        return cls(**{name: getattr(event, name) for name in names},
                   received_at_utc=received_at_utc)

    @classmethod
    def from_row(cls, row: Sequence[object]) -> "BasketResult":
        """A row in RESULT_COLUMNS order; NULL reads as "" or 0. Raises TypeError/ValueError."""
        values = dict(zip(RESULT_COLUMNS, row, strict=True))
        return cls(**{name: _text(values[name]) for name in _TEXT_FIELDS},
                   **{name: _number(values[name]) for name in _NUMBER_FIELDS},
                   decision_latency_ms=int(_number(values["decision_latency_ms"])),
                   received_at_utc=_text(values["created_at"]))

    @property
    def intent_id(self) -> str | None:
        return intent_id_from_basket(self.basket_id)

    @property
    def opened_epoch(self) -> int | None:
        return utc_epoch(self.opened_at_utc)

    @property
    def closed_epoch(self) -> int | None:
        """When the position closed; the receipt time when the close time is unreadable."""
        closed = utc_epoch(self.closed_at_utc)
        return utc_epoch(self.received_at_utc) if closed is None else closed


@dataclass(frozen=True)
class CounterfactualLabel:
    """The labeler's verdict on the candidate the Chief chose in the intent's cycle."""

    candidate_id: str
    cycle_id: str
    setup: str
    verdict: str
    label_status: str          # pending | labeled | unfillable
    outcome: str | None        # tp | sl | time | unfilled | data_gap
    outcome_r: float | None

    @classmethod
    def from_candidate(cls, record: CandidateRecord) -> "CounterfactualLabel":
        return cls(record.candidate_id, record.cycle_id, record.setup, record.verdict,
                   record.label_status, record.outcome, record.outcome_r)

    @property
    def r(self) -> Decimal | None:
        """R of a resolved label; None while pending or when the limit never filled."""
        if self.label_status != LABELED_STATUS or not finite_number(self.outcome_r):
            return None
        return to_decimal(self.outcome_r)  # type: ignore[arg-type]


@dataclass(frozen=True)
class OutcomeRecord:
    """One closed V6 position. `login` is the account of the cycle's snapshot."""

    result: BasketResult
    intent: IntentRecord | None = None
    label: CounterfactualLabel | None = None
    login: str | None = None

    def __post_init__(self) -> None:
        intent = self.intent
        if intent is not None and intent.intent_id != self.result.intent_id:
            raise ValueError("the intent does not belong to this basket result")
        if self.label is not None and (intent is None or self.label.cycle_id != intent.cycle_id):
            raise ValueError("the label does not belong to the intent's cycle")

    @property
    def net_pnl(self) -> Decimal:
        return to_decimal(self.result.net_pnl)

    @property
    def kind(self) -> OutcomeKind:
        pnl = self.result.net_pnl
        return "win" if pnl > 0 else "loss" if pnl < 0 else "flat"

    @property
    def r_multiple(self) -> Decimal | None:
        return None if self.intent is None else r_multiple(self.result.net_pnl,
                                                           self.intent.risk_usd)

    @property
    def mae_r(self) -> Decimal | None:
        """Worst floating P&L in R."""
        return None if self.intent is None else r_multiple(self.result.max_floating_dd,
                                                           self.intent.risk_usd)

    @property
    def label_r(self) -> Decimal | None:
        return None if self.label is None else self.label.r

    @property
    def r_gap(self) -> Decimal | None:
        realised, labelled = self.r_multiple, self.label_r
        return None if realised is None or labelled is None else realised - labelled

    @property
    def side_mismatch(self) -> bool:
        return self.intent is not None and self.intent.side != self.result.side

    def to_dict(self) -> dict[str, object]:
        result, intent, label = self.result, self.intent, self.label
        return {
            "basket_id": result.basket_id, "intent_id": result.intent_id,
            "linked": intent is not None, "login": self.login, "symbol": result.symbol,
            "side": result.side, "close_reason": result.close_reason,
            "opened_epoch": result.opened_epoch, "closed_epoch": result.closed_epoch,
            "net_pnl": round(result.net_pnl, MONEY_PLACES),
            "max_floating_dd": round(result.max_floating_dd, MONEY_PLACES), "kind": self.kind,
            **{key: None if intent is None else getattr(intent, key) for key in _LINK_KEYS},
            "r_multiple": rounded(self.r_multiple, R_PLACES),
            "mae_r": rounded(self.mae_r, R_PLACES),
            "label": None if label is None else {**asdict(label),
                                                 "r": rounded(label.r, R_PLACES)},
            "r_gap": rounded(self.r_gap, R_PLACES), "side_mismatch": self.side_mismatch,
        }


def intent_summary(record: IntentRecord) -> dict[str, object]:
    """A v6_intents row with its realised R once the position has closed."""
    realised = r_multiple(record.outcome_pnl, record.risk_usd)
    return {**record.to_dict(), "r_multiple": rounded(realised, R_PLACES)}


# --- statistics ----------------------------------------------------------------------------
@dataclass(frozen=True)
class OutcomeStats:
    """Counts over every outcome; R figures over the outcomes that have them (n shown)."""

    n: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    unlinked: int = 0
    total_pnl: Decimal = ZERO
    n_r: int = 0
    avg_r: float | None = None
    sd_r: float | None = None
    n_label: int = 0
    avg_label_r: float | None = None
    n_gap: int = 0
    avg_r_gap: float | None = None

    @property
    def win_rate_pct(self) -> float | None:
        """Wins over decided outcomes; breakeven is neither (as in app.metrics)."""
        decided = self.wins + self.losses
        return None if decided == 0 else round(100.0 * self.wins / decided, PCT_PLACES)

    @property
    def t_r(self) -> float | None:
        """t statistic of the mean R; None below two samples or without spread."""
        if self.avg_r is None or self.n_r < MIN_T_SAMPLE or not self.sd_r:
            return None
        return self.avg_r / (self.sd_r / math.sqrt(self.n_r))

    def to_dict(self) -> dict[str, object]:
        return {
            "n": self.n, "wins": self.wins, "losses": self.losses,
            "breakeven": self.breakeven, "unlinked": self.unlinked,
            "win_rate_pct": self.win_rate_pct,
            "total_pnl": float(round(self.total_pnl, MONEY_PLACES)),
            "n_r": self.n_r, "avg_r": _round(self.avg_r, R_PLACES),
            "sd_r": _round(self.sd_r, R_PLACES), "t_r": _round(self.t_r, R_PLACES),
            "n_label": self.n_label, "avg_label_r": _round(self.avg_label_r, R_PLACES),
            "n_gap": self.n_gap, "avg_r_gap": _round(self.avg_r_gap, R_PLACES),
        }


def _floats(values: Iterable[Decimal | None]) -> list[float]:
    return [float(value) for value in values if value is not None]


def outcome_stats(outcomes: Iterable[OutcomeRecord]) -> OutcomeStats:
    items = tuple(outcomes)
    kinds = Counter(item.kind for item in items)
    r_values = _floats(item.r_multiple for item in items)
    labels = _floats(item.label_r for item in items)
    gaps = _floats(item.r_gap for item in items)
    return OutcomeStats(
        n=len(items), wins=kinds["win"], losses=kinds["loss"], breakeven=kinds["flat"],
        unlinked=sum(1 for item in items if item.intent is None),
        total_pnl=sum((item.net_pnl for item in items), ZERO),
        n_r=len(r_values), avg_r=statistics.fmean(r_values) if r_values else None,
        sd_r=statistics.stdev(r_values) if len(r_values) >= MIN_T_SAMPLE else None,
        n_label=len(labels), avg_label_r=statistics.fmean(labels) if labels else None,
        n_gap=len(gaps), avg_r_gap=statistics.fmean(gaps) if gaps else None,
    )


# --- linking a result as it arrives ----------------------------------------------------------
def chosen_label(ledger: LedgerCycles, cycle_id: str) -> CounterfactualLabel | None:
    """Label of the candidate the Chief chose in `cycle_id` (an ENTER cycle has one)."""
    chosen = [c for c in ledger.candidates_for_cycle(cycle_id) if c.verdict == CHOSEN_VERDICT]
    return CounterfactualLabel.from_candidate(chosen[0]) if chosen else None


def outcome_for_event(ledger: LedgerCycles, event: BasketResultEvent, *,
                      received_at_utc: str = "", login: str | None = None) -> OutcomeRecord:
    """Blocking: link a V6 basket result through `ledger` (the route logs the outcome).

    `login` is the account the caller knows the EA trades; it is kept only for a
    linked result. Raises ValueError for a non-V6 or non-finite result and
    sqlite3.Error when the ledger is unreadable.
    """
    result = BasketResult.from_event(event, received_at_utc)
    intent = None if result.intent_id is None else ledger.intents.get(result.intent_id)
    if intent is None:
        return OutcomeRecord(result)
    return OutcomeRecord(result, intent, chosen_label(ledger, intent.cycle_id), login)
