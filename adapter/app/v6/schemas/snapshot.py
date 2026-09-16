"""
EA -> adapter data contracts for V6 (`v6.snapshot.1`, `v6.backfill.1`).

Every model is strict and forbids unknown fields. Bars travel as rows
`[t, o, h, l, c, tick_volume, spread_points]` where `t` is the bar OPEN time in
UTC epoch seconds, and only CLOSED bars are allowed: the EA copies from shift 1.
"""

from __future__ import annotations

import math
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from ..types import TIMEFRAME_SECONDS, Bar, SymbolSpec, TickValueSource

MAX_BARS_PER_TF: Final[int] = 500
MAX_BACKFILL_ROWS: Final[int] = 3000
MAX_POSITIONS: Final[int] = 20
MAX_CALENDAR_EVENTS: Final[int] = 50
_GRID_CHECKED: Final[frozenset[str]] = frozenset({"M1", "M5", "M15"})

TimeframeName = Literal["M1", "M5", "M15", "H1", "D1"]
BarRow = tuple[int, float, float, float, float, int, int]
SnapshotId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:-]{8,64}$")]
# Brokers suffix symbol names (XAUUSDm, XAUUSD#, XAUUSD+), so those are allowed.
SymbolName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._#+-]{3,20}$")]
ShortText = Annotated[str, StringConstraints(max_length=80)]
OrderComment = Annotated[str, StringConstraints(max_length=31)]


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, allow_inf_nan=False)


def _clean_text(value: str) -> str:
    """Strip control characters; broker-supplied text is untrusted."""
    return "".join(ch for ch in value if ch.isprintable())


def validate_bar_rows(rows: list[BarRow], tf: str) -> list[BarRow]:
    """Reject rows that are unordered, inconsistent, non-finite or off-grid."""
    step = TIMEFRAME_SECONDS[tf]
    previous = -1
    for t, o, h, lo, c, tv, spr in rows:
        if not all(math.isfinite(v) for v in (o, h, lo, c)):
            raise ValueError(f"{tf}: non-finite price at t={t}")
        if min(o, h, lo, c) <= 0:
            raise ValueError(f"{tf}: non-positive price at t={t}")
        if h < max(o, c) or lo > min(o, c):
            raise ValueError(f"{tf}: high/low inconsistent with open/close at t={t}")
        if t <= previous:
            raise ValueError(f"{tf}: bar times must be strictly increasing (t={t})")
        # H1/D1 open on server-hour boundaries, which a half-hour GMT offset shifts.
        if tf in _GRID_CHECKED and t % step != 0:
            raise ValueError(f"{tf}: bar time {t} is not aligned to the {step}s grid")
        if tv < 0 or spr < 0:
            raise ValueError(f"{tf}: negative tick volume or spread at t={t}")
        previous = t
    return rows


def rows_to_bars(rows: list[BarRow]) -> tuple[Bar, ...]:
    return tuple(Bar(t=t, o=o, h=h, l=lo, c=c, tv=tv, spr=spr) for t, o, h, lo, c, tv, spr in rows)


class AccountBlock(_Strict):
    login: Annotated[str, StringConstraints(pattern=r"^[0-9]{1,20}$")]
    trade_mode: Literal["DEMO", "CONTEST", "REAL"]
    server: ShortText
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    leverage: int = Field(ge=1, le=100_000)
    balance: float = Field(ge=0)
    equity: float = Field(ge=0)
    margin: float = Field(ge=0)
    free_margin: float
    margin_level: float = Field(ge=0)

    @field_validator("server")
    @classmethod
    def _sanitize_server(cls, value: str) -> str:
        return _clean_text(value)


class SymbolSpecBlock(_Strict):
    digits: int = Field(ge=0, le=8)
    point: float = Field(gt=0)
    tick_size: float = Field(gt=0)
    tick_value: float = Field(gt=0)
    tick_value_loss: float = Field(gt=0)
    contract_size: float = Field(gt=0)
    volume_min: float = Field(gt=0)
    volume_step: float = Field(gt=0)
    volume_max: float = Field(gt=0)
    stops_level: int = Field(ge=0)
    freeze_level: int = Field(ge=0)
    margin_per_lot_buy: float = Field(ge=0)
    margin_per_lot_sell: float = Field(ge=0)
    filling_modes: int = Field(ge=0)
    expiration_modes: int = Field(ge=0)
    # OrderCalcProfit for a BUY of 1.00 lot from ask to ask+1.0, and the absolute
    # loss from ask to ask-1.0. 0 when the terminal could not price it.
    calc_profit_per_price: float = Field(ge=0)
    calc_loss_per_price: float = Field(ge=0)

    def to_spec(self) -> SymbolSpec:
        """Sizing spec; tick values come from OrderCalcProfit whenever it priced them."""
        has_profit = self.calc_profit_per_price > 0
        has_loss = self.calc_loss_per_price > 0
        tick_value = self.calc_profit_per_price * self.tick_size if has_profit else self.tick_value
        tick_value_loss = (
            self.calc_loss_per_price * self.tick_size if has_loss else self.tick_value_loss)
        return SymbolSpec(
            digits=self.digits, point=self.point, tick_size=self.tick_size,
            tick_value=tick_value, tick_value_loss=tick_value_loss,
            contract_size=self.contract_size, volume_min=self.volume_min,
            volume_step=self.volume_step, volume_max=self.volume_max,
            stops_level=self.stops_level, freeze_level=self.freeze_level,
            reported_tick_value=self.tick_value,
            reported_tick_value_loss=self.tick_value_loss,
            tick_value_source=_tick_value_source(has_profit, has_loss),
        )


def _tick_value_source(has_profit: bool, has_loss: bool) -> TickValueSource:
    if has_profit and has_loss:
        return "order_calc"
    return "mixed" if has_profit or has_loss else "reported"


class QuoteBlock(_Strict):
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    spread_points: int = Field(ge=0)
    time_msc: int = Field(ge=0)

    @model_validator(mode="after")
    def _ask_not_below_bid(self) -> "QuoteBlock":
        if self.ask < self.bid:
            raise ValueError("ask must not be below bid")
        return self


class TickStatsBlock(_Strict):
    window_s: int = Field(ge=0, le=3600)
    quote_count: int = Field(ge=0)
    max_gap_ms: int = Field(ge=0)
    spread_p50_points: float = Field(ge=0)
    spread_p95_points: float = Field(ge=0)
    mid_rv: float = Field(ge=0)


class PositionBlock(_Strict):
    ticket: int = Field(ge=0)
    magic: int = Field(ge=0)
    side: Literal["buy", "sell"]
    volume: float = Field(gt=0)
    price_open: float = Field(gt=0)
    sl: float = Field(ge=0)
    tp: float = Field(ge=0)
    profit: float
    swap: float
    open_epoch: int = Field(ge=0)
    comment: OrderComment
    mae_points: float = Field(ge=0)
    mfe_points: float = Field(ge=0)


class PendingOrderBlock(_Strict):
    ticket: int = Field(ge=0)
    magic: int = Field(ge=0)
    order_type: Literal["BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP"]
    price: float = Field(gt=0)
    sl: float = Field(ge=0)
    tp: float = Field(ge=0)
    volume: float = Field(gt=0)
    expiration_epoch: int = Field(ge=0)
    comment: OrderComment


class DayBlock(_Strict):
    day_start_equity: float = Field(ge=0)
    realized_today: float
    trades_today: int = Field(ge=0)


class CalendarEventBlock(_Strict):
    event_id: int = Field(ge=0)
    time_epoch: int = Field(ge=0)
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    importance: Literal["NONE", "LOW", "MODERATE", "HIGH"]
    code: Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]{1,60}$")]
    name: ShortText
    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None

    @field_validator("name")
    @classmethod
    def _sanitize_name(cls, value: str) -> str:
        return _clean_text(value)


class ProbeBlock(_Strict):
    book_depth: int = Field(ge=0)
    trade_ticks_count: int = Field(ge=0)
    real_volume_count: int = Field(ge=0)
    dom_synthetic: bool
    gmt_offset_s: int
    dst_active: bool
    calendar_events_seen: int = Field(ge=0)


class EaStateBlock(_Strict):
    ea_version: Annotated[str, StringConstraints(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
    execute_enabled: bool
    halted: bool
    local_breaker: Literal["none", "daily"]
    outbox_pending: int = Field(ge=0)
    last_intent_id: Annotated[str, StringConstraints(max_length=16)]


class V6Snapshot(_Strict):
    schema_version: Literal["v6.snapshot.1"]
    snapshot_id: SnapshotId
    symbol: SymbolName
    sent_at_epoch: int = Field(ge=0)
    server_gmt_offset_s: int = Field(ge=-14 * 3600, le=14 * 3600)
    bar_tf: Literal["M15"]
    bar_open_epoch: int = Field(ge=0)
    account: AccountBlock
    symbol_spec: SymbolSpecBlock
    quote: QuoteBlock
    bars: dict[TimeframeName, list[BarRow]]
    ticks: TickStatsBlock
    positions: list[PositionBlock] = Field(max_length=MAX_POSITIONS)
    pending_orders: list[PendingOrderBlock] = Field(max_length=MAX_POSITIONS)
    day: DayBlock
    calendar: list[CalendarEventBlock] = Field(max_length=MAX_CALENDAR_EVENTS)
    probe: ProbeBlock | None = None
    ea_state: EaStateBlock

    @field_validator("server_gmt_offset_s")
    @classmethod
    def _offset_on_half_hours(cls, value: int) -> int:
        if value % 1800 != 0:
            raise ValueError("server_gmt_offset_s must be a multiple of 1800")
        return value

    @field_validator("bars")
    @classmethod
    def _validate_bars(cls, value: dict[str, list[BarRow]]) -> dict[str, list[BarRow]]:
        for tf, rows in value.items():
            if len(rows) > MAX_BARS_PER_TF:
                raise ValueError(f"{tf}: at most {MAX_BARS_PER_TF} bars per snapshot")
            validate_bar_rows(rows, tf)
        return value

    @model_validator(mode="after")
    def _bars_are_closed(self) -> "V6Snapshot":
        """No row may still be forming at the snapshot's bar close."""
        close_epoch = self.bar_open_epoch + TIMEFRAME_SECONDS["M15"]
        for tf, rows in self.bars.items():
            if rows and rows[-1][0] + TIMEFRAME_SECONDS[tf] > close_epoch:
                raise ValueError(f"{tf}: last bar is not closed at {close_epoch}")
        return self


class BackfillRequest(_Strict):
    schema_version: Literal["v6.backfill.1"]
    symbol: SymbolName
    tf: TimeframeName
    sent_at_epoch: int = Field(ge=0)
    rows: list[BarRow] = Field(max_length=MAX_BACKFILL_ROWS)

    @model_validator(mode="after")
    def _validate_rows(self) -> "BackfillRequest":
        validate_bar_rows(self.rows, self.tf)
        return self
