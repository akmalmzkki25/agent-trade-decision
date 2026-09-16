"""
Internal, immutable value types shared across V6 modules.

Pydantic models in `app.v6.schemas` describe what crosses the wire; these frozen
dataclasses describe what flows between pure functions inside the adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final, Literal, Mapping

Side = Literal["buy", "sell"]
Timeframe = Literal["M1", "M5", "M15", "H1", "D1"]

TIMEFRAME_SECONDS: Final[Mapping[str, int]] = MappingProxyType(
    {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "D1": 86400}
)

_EMPTY: Final[Mapping[str, float]] = MappingProxyType({})


@dataclass(frozen=True)
class Bar:
    """One closed OHLC bar. `t` is the bar OPEN time, UTC epoch seconds."""

    t: int
    o: float
    h: float
    l: float  # noqa: E741 - conventional OHLC name
    c: float
    tv: int = 0
    spr: int = 0

    @property
    def range(self) -> float:
        return self.h - self.l

    @property
    def body(self) -> float:
        return abs(self.c - self.o)


TickValueSource = Literal["order_calc", "reported", "mixed"]


@dataclass(frozen=True)
class SymbolSpec:
    """Contract facts used for sizing.

    `tick_value` / `tick_value_loss` are the values sizing must use. When the EA
    could price a 1.00-lot move with OrderCalcProfit they are derived from that
    (`tick_value_source="order_calc"`), because some servers report a
    SYMBOL_TRADE_TICK_VALUE that is 10x off. `reported_*` keep the broker's own
    numbers for the spec gate; None means the spec was built directly (tests,
    V1-V5 style callers) and the reported value equals `tick_value`.
    """

    digits: int
    point: float
    tick_size: float
    tick_value: float
    tick_value_loss: float
    contract_size: float
    volume_min: float
    volume_step: float
    volume_max: float
    stops_level: int = 0
    freeze_level: int = 0
    reported_tick_value: float | None = None
    reported_tick_value_loss: float | None = None
    tick_value_source: TickValueSource = "reported"


@dataclass(frozen=True)
class GateResult:
    code: str
    passed: bool
    value: float | str | None = None
    limit: float | str | None = None
    detail: str = ""


@dataclass(frozen=True)
class Candidate:
    """A setup found by a deterministic detector. LLMs may only rank these."""

    candidate_id: str
    setup: str
    side: Side
    entry: float
    invalidation: float
    bar_t: int
    features: Mapping[str, float] = field(default=_EMPTY)
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SizingRequest:
    equity: float
    balance: float
    free_margin: float
    price: float
    stop_distance: float
    risk_pct: float
    size_multiplier: float
    remaining_daily_loss_usd: float
    margin_per_lot: float
    friction_price: float
    spec: SymbolSpec


@dataclass(frozen=True)
class SizingResult:
    lots: float
    risk_usd: float
    risk_budget_usd: float
    loss_per_lot: float
    notional_usd: float
    margin_usd: float


@dataclass(frozen=True)
class Refusal:
    """A deterministic 'no'. `codes` explain why; never silently rounded up."""

    codes: tuple[str, ...]
    detail: str = ""


@dataclass(frozen=True)
class ExitPlan:
    side: Side
    entry: float
    sl: float
    tp: float
    stop_distance: float
    reward_r: float
    time_barrier_s: int
    labels: tuple[str, ...] = ()
