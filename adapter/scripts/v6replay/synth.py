"""
What the replay invents because only a live terminal knows it (see ASSUMPTIONS).

Settings are built from explicit values only: no adapter/.env and no V6_*
environment variables, so a run is reproducible and never touches secrets.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from pydantic_settings import PydanticBaseSettingsSource

from app.v6.config import V6Settings
from app.v6.cycle_types import MarketContext
from app.v6.market.calendar import (
    EVENT_LOOKBACK_S, MT5_CALENDAR_HORIZON_S, STATIC_CALENDAR, STATIC_DEDUP_TOLERANCE_S,
)
from app.v6.risk.breakers import (
    SCOPES, BreakerInputs, BreakerStatus, PeriodInput, evaluate_breakers,
)
from app.v6.schemas.snapshot import MAX_CALENDAR_EVENTS, CalendarEventBlock, V6Snapshot
from app.v6.types import TIMEFRAME_SECONDS, Bar

from .data import BarSet

M15_S: Final[int] = TIMEFRAME_SECONDS["M15"]
REPLAY_EQUITY_USD: Final[float] = 96_896.0
REPLAY_LEVERAGE: Final[int] = 200
CONTRACT_SIZE: Final[float] = 100.0
POINT: Final[float] = 0.01
PRICE_DIGITS: Final[int] = 2
# MetaQuotes-Demo p50 spread; used only when a stored bar reports spread 0.
FALLBACK_SPREAD_POINTS: Final[int] = 27
RECEIVE_DELAY_S: Final[int] = 1
SERVER_GMT_OFFSET_S: Final[int] = 10_800
REPLAY_LOGIN: Final[str] = "10000001"
REPLAY_SERVER: Final[str] = "MetaQuotes-Demo"
REPLAY_SESSION_ID: Final[str] = "replay-session"
REPLAY_MAGIC: Final[int] = 250_570
STATIC_EVENT_ID_BASE: Final[int] = 9_000_000_000
PROBE_CALENDAR_EVENTS_SEEN: Final[int] = 78
BASE_SETTINGS: Final[Mapping[str, Any]] = {
    "enabled": False, "backend": "operator", "mode": "shadow",
    "sizing_equity_basis_usd": 5000.0,
}
SECRET_FIELDS: Final[frozenset[str]] = frozenset({"operator_token", "ea_hmac_key"})
ASSUMPTIONS: Final[tuple[str, ...]] = (
    "decision time = M15 bar close; snapshot sent at the close, received 1 s later",
    "quote: bid = bar close, ask = bid + the bar's spread column (27 points if it is 0)",
    f"account DEMO on {REPLAY_SERVER}, equity = balance = free margin = {REPLAY_EQUITY_USD:,.0f}",
    "margin per lot = price x 100 / 200; tick values priced by OrderCalcProfit ($1 per 0.01 lot)",
    "freshness, clock skew, halt, EA state and spec gates pass by construction; "
    "session active and armed",
    "WARMUP forced ready; bars lacking history are reported as insufficient instead",
    "breakers healthy: simulated trades carry no P&L",
    "calendar: static FOMC/NFP table plus events stored in v6_snapshots, sent as the MT5 block "
    "(no forecast, so no surprise extension); other USD releases outside stored days are unknown",
    "every packet becomes a trade at the bar close; it occupies the one position until the "
    "time barrier and counts toward trades_today (UTC day, like the EA)",
)


class ReplaySettings(V6Settings):
    """V6Settings from explicit values only (no .env file, no environment)."""

    @classmethod
    def settings_customise_sources(
        cls, settings_cls: type[Any], init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource, dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def _field_key(raw: str) -> str:
    fields = V6Settings.model_fields
    aliases = {info.alias: name for name, info in fields.items() if info.alias}
    upper = raw.strip().upper()
    if upper in aliases:
        return aliases[upper]
    name = upper.removeprefix("V6_").lower()
    if name not in fields:
        raise ValueError(f"unknown setting {raw.strip()[:40]!r}")
    return name


def parse_overrides(pairs: Sequence[str]) -> dict[str, str]:
    """KEY=VALUE pairs (field names or V6_* names) as field-name overrides."""
    parsed: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise ValueError(f"expected KEY=VALUE, got {pair[:40]!r}")
        name = _field_key(key)
        if name in SECRET_FIELDS:
            raise ValueError(f"{name} is a secret and cannot be set here")
        parsed[name] = value.strip()
    return parsed


def replay_settings(overrides: Mapping[str, Any] | None = None) -> V6Settings:
    return ReplaySettings(**{**BASE_SETTINGS, **(overrides or {})})


def settings_view(settings: V6Settings) -> dict[str, Any]:
    """Every non-secret setting, JSON-ready."""
    return settings.model_dump(mode="json", exclude=set(SECRET_FIELDS))


@dataclass(frozen=True)
class Exposure:
    """The simulated V6 position and today's entry count, as the EA would report them."""

    trades_today: int = 0
    open_until: int = 0
    side: str = "buy"
    entry: float = 0.0
    opened_at: int = 0

    def is_open(self, as_of: int) -> bool:
        return as_of < self.open_until


def _static_blocks() -> tuple[CalendarEventBlock, ...]:
    return tuple(CalendarEventBlock(
        event_id=STATIC_EVENT_ID_BASE + index, time_epoch=event.time_epoch,
        currency=event.currency, importance=event.importance, code=event.code,
        name=event.code) for index, event in enumerate(STATIC_CALENDAR.events))


STATIC_BLOCKS: Final[tuple[CalendarEventBlock, ...]] = _static_blocks()


def calendar_block(stored: Sequence[CalendarEventBlock],
                   sent_at: int) -> list[dict[str, Any]]:
    """What the EA would list at `sent_at`: stored events, then uncovered static ones."""
    start, end = sent_at - EVENT_LOOKBACK_S, sent_at + MT5_CALENDAR_HORIZON_S
    known = [block for block in stored if start <= block.time_epoch <= end]
    extra = [block for block in STATIC_BLOCKS if start <= block.time_epoch <= end
             and all(abs(block.time_epoch - k.time_epoch) > STATIC_DEDUP_TOLERANCE_S
                     for k in known)]
    chosen = sorted(known + extra, key=lambda b: (b.time_epoch, b.event_id))
    return [block.model_dump(mode="json") for block in chosen[:MAX_CALENDAR_EVENTS]]


def quote_spread(bar: Bar) -> int:
    return bar.spr if bar.spr > 0 else FALLBACK_SPREAD_POINTS


def _position(exposure: Exposure) -> dict[str, Any]:
    return {"ticket": 1, "magic": REPLAY_MAGIC, "side": exposure.side, "volume": 0.01,
            "price_open": exposure.entry, "sl": 0.0, "tp": 0.0, "profit": 0.0, "swap": 0.0,
            "open_epoch": exposure.opened_at, "comment": "V6 replay", "mae_points": 0.0,
            "mfe_points": 0.0}


def _spec(price: float) -> dict[str, Any]:
    margin = round(price * CONTRACT_SIZE / REPLAY_LEVERAGE, PRICE_DIGITS)
    return {"digits": PRICE_DIGITS, "point": POINT, "tick_size": POINT, "tick_value": 0.1,
            "tick_value_loss": 0.1, "contract_size": CONTRACT_SIZE, "volume_min": 0.01,
            "volume_step": 0.01, "volume_max": 100.0, "stops_level": 0, "freeze_level": 0,
            "margin_per_lot_buy": margin, "margin_per_lot_sell": margin, "filling_modes": 3,
            "expiration_modes": 15, "calc_profit_per_price": CONTRACT_SIZE,
            "calc_loss_per_price": CONTRACT_SIZE}


def _account() -> dict[str, Any]:
    return {"login": REPLAY_LOGIN, "trade_mode": "DEMO", "server": REPLAY_SERVER,
            "currency": "USD", "leverage": REPLAY_LEVERAGE, "balance": REPLAY_EQUITY_USD,
            "equity": REPLAY_EQUITY_USD, "margin": 0.0, "free_margin": REPLAY_EQUITY_USD,
            "margin_level": 0.0}


def synth_snapshot(bar: Bar, stored_events: Sequence[CalendarEventBlock],
                   exposure: Exposure) -> V6Snapshot:
    """The snapshot the EA would have sent at the close of M15 `bar` (no bar rows)."""
    as_of = bar.t + M15_S
    spread = quote_spread(bar)
    ask = round(bar.c + spread * POINT, PRICE_DIGITS)
    payload = {
        "schema_version": "v6.snapshot.1", "snapshot_id": f"replay-{bar.t}",
        "symbol": "XAUUSD", "sent_at_epoch": as_of, "server_gmt_offset_s": SERVER_GMT_OFFSET_S,
        "bar_tf": "M15", "bar_open_epoch": bar.t, "account": _account(),
        "symbol_spec": _spec(bar.c),
        "quote": {"bid": bar.c, "ask": ask, "spread_points": spread, "time_msc": as_of * 1000},
        "bars": {},
        "ticks": {"window_s": M15_S, "quote_count": max(bar.tv, 0), "max_gap_ms": 1000,
                  "spread_p50_points": float(spread), "spread_p95_points": float(spread),
                  "mid_rv": 0.0},
        "positions": [_position(exposure)] if exposure.is_open(as_of) else [],
        "pending_orders": [],
        "day": {"day_start_equity": REPLAY_EQUITY_USD, "realized_today": 0.0,
                "trades_today": exposure.trades_today},
        "calendar": calendar_block(stored_events, as_of),
        "probe": {"book_depth": 32, "trade_ticks_count": 0, "real_volume_count": 0,
                  "dom_synthetic": True, "gmt_offset_s": SERVER_GMT_OFFSET_S,
                  "dst_active": False, "calendar_events_seen": PROBE_CALENDAR_EVENTS_SEEN},
        "ea_state": {"ea_version": "6.0.0", "execute_enabled": True, "halted": False,
                     "local_breaker": "none", "outbox_pending": 0, "last_intent_id": ""},
    }
    return V6Snapshot.model_validate_json(json.dumps(payload))


def healthy_breakers(context: MarketContext, settings: V6Settings) -> BreakerStatus:
    equity = context.account.equity
    periods = tuple(PeriodInput(scope=scope, start_equity=equity, realized_v6=0.0)
                    for scope in SCOPES)
    inputs = BreakerInputs(as_of_epoch=context.as_of_epoch, equity=equity, floating_v6=0.0,
                           periods=periods)
    return BreakerStatus(evaluation=evaluate_breakers(inputs, settings))


@dataclass(frozen=True)
class CutReader:
    """A BarReader that can only return bars closed by `as_of` (no look-ahead)."""

    bars: BarSet
    as_of: int

    def latest(self, tf: str, n: int) -> tuple[Bar, ...]:
        return self.bars.closed_by(tf, self.as_of)[-n:]
