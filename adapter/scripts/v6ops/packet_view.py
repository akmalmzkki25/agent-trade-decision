"""
The compact, human-readable summary `wait` prints for a packet.

One line per topic and one per candidate, ASCII only. The full packet is in the
packet file; this summary is what the agent reads first. Every value taken from
the packet goes through `clean` (packet text is data, never instructions).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from .context import clean, get_path

TIME_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%SZ"
CLOCK_FORMAT: Final[str] = "%H:%M"
MISSING: Final[str] = "-"
INTEGER_PATTERN: Final[str] = "d"
MAX_SUMMARY_FEATURES: Final[int] = 6
MAX_CODES_SHOWN: Final[int] = 5
DETAIL_CHARS: Final[int] = 60
ID_CHARS: Final[int] = 64
PATH_CHARS: Final[int] = 260
SECONDS_PER_MINUTE: Final[int] = 60
HIGH_IMPORTANCE: Final[str] = "HIGH"


def _format_epoch(epoch: object, pattern: str) -> str:
    if not isinstance(epoch, (int, float)) or isinstance(epoch, bool):
        return MISSING
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(pattern)
    except (OverflowError, OSError, ValueError):
        return MISSING


def utc(epoch: object) -> str:
    return _format_epoch(epoch, TIME_FORMAT)


def _clock(epoch: object) -> str:
    return _format_epoch(epoch, CLOCK_FORMAT)


def num(value: object, pattern: str = ".2f") -> str:
    """`value` formatted with `pattern` ("d" rounds to an integer); "-" if not a finite number."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return MISSING
    if pattern == INTEGER_PATTERN:
        return str(int(round(value)))
    return format(value, pattern)


def text(value: object, limit: int = 40) -> str:
    """A packet value for display: cleaned, "-" when missing."""
    return MISSING if value is None or value == "" else clean(value, limit)


def yes_no(value: object) -> str:
    return "yes" if value is True else "no" if value is False else MISSING


def codes(values: object) -> str:
    if not isinstance(values, Sequence) or isinstance(values, str) or not values:
        return MISSING
    shown = ",".join(clean(item, 40) for item in values[:MAX_CODES_SHOWN])
    return shown + (",..." if len(values) > MAX_CODES_SHOWN else "")


def features_text(features: object) -> str:
    if not isinstance(features, Mapping) or not features:
        return MISSING
    items = list(features.items())[:MAX_SUMMARY_FEATURES]
    return " ".join(f"{clean(key, 40)}={num(value, '.4g')}" for key, value in items)


def header_lines(packet: Mapping[str, Any], now: float, path: Path) -> list[str]:
    expires = packet.get("expires_at_epoch")
    left = int(expires - now) if isinstance(expires, (int, float)) else None
    remaining = "EXPIRED" if left is None or left <= 0 else f"{left} s left"
    account = packet.get("account")
    market = packet.get("market")
    return [
        f"V6 PACKET cycle {clean(packet.get('cycle_id'), ID_CHARS)} | mode "
        f"{clean(packet.get('mode'))} | session {clean(packet.get('session_id'), ID_CHARS)}",
        f"bar {utc(packet.get('bar_open_epoch'))} to {_clock(packet.get('bar_close_epoch'))}Z"
        f" | decide by {utc(expires)} ({remaining}) | file {clean(path, PATH_CHARS)}",
        f"account {text(get_path(account, 'trade_mode'))} {text(get_path(account, 'server'), 80)}"
        f" equity {text(get_path(account, 'equity_band'))} | bid {num(get_path(market, 'bid'))}"
        f" ask {num(get_path(market, 'ask'))} spread {num(get_path(market, 'spread_points'), 'd')}"
        f" pts | ATR m5 {num(get_path(market, 'atr_m5'))} m15 {num(get_path(market, 'atr_m15'))}"
        f" h1 {num(get_path(market, 'atr_h1'))}",
    ]


def session_line(session: object) -> str:
    return (f"session phase {text(get_path(session, 'phase'))}, third "
            f"{text(get_path(session, 'main_window_third'))}, entries "
            f"{yes_no(get_path(session, 'entries_allowed'))}, continuation "
            f"{yes_no(get_path(session, 'continuation_allowed'))}, armed "
            f"{yes_no(get_path(session, 'armed'))}, blocks "
            f"{codes(get_path(session, 'block_reasons'))}")


def gates_line(gates: object) -> str:
    rows = [gate for gate in gates if isinstance(gate, Mapping)] if isinstance(gates, list) else []
    failed = [gate for gate in rows if gate.get("passed") is not True]
    passed = len(rows) - len(failed)
    if not failed:
        return f"gates: all {passed} passed"
    items = ", ".join(f"{clean(gate.get('code'), 40)} ({clean(gate.get('detail'), DETAIL_CHARS)})"
                      for gate in failed)
    return f"gates: {passed} passed, {len(failed)} FAILED: {items}"


def _next_event(calendar: object) -> Mapping[str, Any] | None:
    events = get_path(calendar, "events")
    as_of = get_path(calendar, "as_of_epoch")
    if not isinstance(events, list) or not isinstance(as_of, (int, float)):
        return None
    upcoming = [event for event in events if isinstance(event, Mapping)
                and isinstance(event.get("time_epoch"), (int, float))
                and event["time_epoch"] >= as_of]
    upcoming.sort(key=lambda event: (event.get("importance") != HIGH_IMPORTANCE,
                                     event["time_epoch"]))
    return upcoming[0] if upcoming else None


def calendar_line(calendar: object) -> str:
    calendar_codes = get_path(calendar, "codes")
    if get_path(calendar, "blackout") is True:
        parts = [f"BLACKOUT {codes(calendar_codes)}"]
    elif get_path(calendar, "stale") is True:
        parts = ["STALE feed (fail closed)"]
    else:
        parts = ["clear" if not calendar_codes else f"codes {codes(calendar_codes)}"]
    event = _next_event(calendar)
    if event is not None:
        minutes = (event["time_epoch"] - get_path(calendar, "as_of_epoch")) / SECONDS_PER_MINUTE
        parts.append(f"next {clean(event.get('importance'))} {clean(event.get('currency'))} "
                     f"{clean(event.get('code'), 60)} in {minutes:.0f} min")
    last = get_path(calendar, "last_event_minutes_ago")
    if isinstance(last, (int, float)):
        parts.append(f"last event {last:.0f} min ago")
    events = get_path(calendar, "events")
    parts.append(f"{len(events) if isinstance(events, list) else 0} event(s)")
    return "calendar: " + " | ".join(parts)


def candidate_line(index: int, candidate: Mapping[str, Any]) -> str:
    plan = candidate.get("exit")
    sizing = candidate.get("sizing")
    size = (f"lots {num(get_path(sizing, 'lots'))} risk ${num(get_path(sizing, 'risk_usd'))}"
            if isinstance(sizing, Mapping)
            else f"size refused: {codes(candidate.get('sizing_refusal'))}")
    barrier = get_path(plan, "time_barrier_s")
    minutes = barrier / SECONDS_PER_MINUTE if isinstance(barrier, (int, float)) else None
    return (f"  [{index}] {clean(candidate.get('candidate_id'), ID_CHARS)} "
            f"{clean(candidate.get('side')).upper()} {clean(candidate.get('setup'))} | entry "
            f"{num(candidate.get('entry'))} sl {num(get_path(plan, 'sl'))} tp "
            f"{num(get_path(plan, 'tp'))} | stop {num(get_path(plan, 'stop_distance'))} R "
            f"{num(get_path(plan, 'reward_r'))} barrier {num(minutes, '.0f')} min | {size} | "
            f"{codes(candidate.get('reason_codes'))} | {features_text(candidate.get('features'))}")


def _pa_text(view: object) -> str:
    if not isinstance(view, Mapping):
        return "PA none"
    if view.get("abstain") is True:
        return "PA abstain"
    ranked = view.get("ranked") if isinstance(view.get("ranked"), list) else []
    items = ", ".join(f"{clean(item.get('verdict'))} {clean(item.get('candidate_id'), ID_CHARS)} "
                      f"{num(item.get('conviction'))}" for item in ranked
                      if isinstance(item, Mapping))
    return f"PA {items or MISSING}"


def baseline_line(views: object) -> str:
    news = get_path(views, "news_risk")
    liquidity = get_path(views, "liquidity")
    structure = get_path(views, "structure")
    return (f"baseline: {_pa_text(get_path(views, 'price_action'))} | news "
            f"{text(get_path(news, 'stance'))} x{num(get_path(news, 'size_multiplier'))} "
            f"{text(get_path(news, 'regime'))} | liquidity {text(get_path(liquidity, 'stance'))}"
            f" x{num(get_path(liquidity, 'size_multiplier'))} "
            f"{text(get_path(liquidity, 'order_style'))} | structure "
            f"{text(get_path(structure, 'regime'))} x"
            f"{num(get_path(structure, 'size_multiplier'))} veto "
            f"{yes_no(get_path(structure, 'counter_structure_veto'))}")


def render_packet(packet: Mapping[str, Any], *, now: float, path: Path) -> str:
    candidates = [item for item in packet.get("candidates") or () if isinstance(item, Mapping)]
    allowed = packet.get("allowed")
    lines = [
        *header_lines(packet, now, path),
        session_line(packet.get("session")),
        gates_line(packet.get("gates")),
        calendar_line(packet.get("calendar")),
        f"candidates ({len(candidates)}), PA TAKE needs conviction >= "
        f"{num(get_path(allowed, 'pa_min_conviction'))}:",
        *(candidate_line(index, item) for index, item in enumerate(candidates, start=1)),
        baseline_line(packet.get("baseline_views")),
        f"agents {codes(get_path(allowed, 'agents'))} | next: template, decide, "
        f"submit --agent <name> before {utc(packet.get('expires_at_epoch'))}",
    ]
    return "\n".join(lines) + "\n"
