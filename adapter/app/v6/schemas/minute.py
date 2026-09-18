"""
The minute snapshot `v6.minute.1` (spec section 3.1): one per closed M1 bar.

The EA sends it once, without a retry queue, to POST /v6/minute. It carries the closed
M1 bar and the same account, quote, tick, exposure, day and EA blocks as the M15
snapshot, so the minute worker can redo the gates at the minute close.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from ..types import TIMEFRAME_SECONDS, Bar
from .snapshot import (
    MAX_POSITIONS, AccountBlock, BarRow, DayBlock, EaStateBlock, PendingOrderBlock,
    PositionBlock, QuoteBlock, SymbolName, TickStatsBlock, _Strict, rows_to_bars,
    validate_bar_rows,
)

MINUTE_SCHEMA: Final[str] = "v6.minute.1"
MINUTE_ID_PREFIX: Final[str] = "Q6M-"
M1_S: Final[int] = TIMEFRAME_SECONDS["M1"]
MinuteSnapshotId = Annotated[str, StringConstraints(pattern=r"^Q6M-[0-9]{1,20}-[0-9]{1,12}$")]


def minute_snapshot_id(login: str, bar_open_epoch: int) -> str:
    return f"{MINUTE_ID_PREFIX}{login}-{bar_open_epoch}"


class MinuteSnapshot(_Strict):
    schema_version: Literal["v6.minute.1"]
    snapshot_id: MinuteSnapshotId
    symbol: SymbolName
    sent_at_epoch: int = Field(ge=0)
    server_gmt_offset_s: int = Field(ge=-14 * 3600, le=14 * 3600)
    bar_open_epoch: int = Field(ge=0)
    bar: BarRow
    account: AccountBlock
    quote: QuoteBlock
    ticks: TickStatsBlock
    positions: list[PositionBlock] = Field(max_length=MAX_POSITIONS)
    pending_orders: list[PendingOrderBlock] = Field(max_length=MAX_POSITIONS)
    day: DayBlock
    ea_state: EaStateBlock

    @field_validator("server_gmt_offset_s")
    @classmethod
    def _offset_on_half_hours(cls, value: int) -> int:
        if value % 1800 != 0:
            raise ValueError("server_gmt_offset_s must be a multiple of 1800")
        return value

    @model_validator(mode="after")
    def _check_minute(self) -> "MinuteSnapshot":
        validate_bar_rows([self.bar], "M1")
        checks = (
            (self.bar[0] == self.bar_open_epoch, "bar is not the snapshot's minute"),
            (self.snapshot_id == minute_snapshot_id(self.account.login, self.bar_open_epoch),
             "snapshot_id must be Q6M-<login>-<bar_open_epoch>"),
            (self.sent_at_epoch >= self.bar_open_epoch, "sent before the bar opened"),
        )
        problems = [message for ok, message in checks if not ok]
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @property
    def bar_close_epoch(self) -> int:
        return self.bar_open_epoch + M1_S

    def to_bar(self) -> Bar:
        return rows_to_bars([self.bar])[0]
