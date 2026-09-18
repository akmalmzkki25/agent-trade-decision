"""
The compact, human-readable summary `wait` prints for a packet.

One line per topic and one per candidate, ASCII only; a management packet shows
its position or resting order with the plan instead of suggestions, and every
packet shows the agent's last M15 bias and the last management action when there
are any. The full packet is in the packet file; this summary is what the agent
reads first. Every value taken from
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
RECENT_M15_BARS: Final[int] = 4
MAX_PIVOTS_SHOWN: Final[int] = 3


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
    return (f"session phase {text(get_path(session, 'phase'))}, quality "
            f"{text(get_path(session, 'quality'))}, third "
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


def _pivots_text(pivots: object) -> str:
    if not isinstance(pivots, list) or not pivots:
        return MISSING
    rows = [item for item in pivots if isinstance(item, Mapping)][-MAX_PIVOTS_SHOWN:]
    return " ".join(f"{clean(item.get('kind'))}@{num(item.get('price'))}" for item in rows)


def levels_line(levels: object) -> str:
    return (f"levels: PDH {num(get_path(levels, 'prior_day_high'))} PDL "
            f"{num(get_path(levels, 'prior_day_low'))} | round10 "
            f"{num(get_path(levels, 'round_10_below'))}/{num(get_path(levels, 'round_10_above'))}"
            f" round50 {num(get_path(levels, 'round_50_below'))}/"
            f"{num(get_path(levels, 'round_50_above'))} | pivots M15 "
            f"{_pivots_text(get_path(levels, 'pivots_m15'))} H1 "
            f"{_pivots_text(get_path(levels, 'pivots_h1'))}")


def bars_line(bars: object) -> str:
    rows = get_path(bars, "M15")
    if not isinstance(rows, list) or not rows:
        return "M15: -"
    recent = [row for row in rows if isinstance(row, list) and len(row) == 5]
    shown = " | ".join(f"{_clock(row[0])} o{num(row[1])} h{num(row[2])} l{num(row[3])} "
                       f"c{num(row[4])}" for row in recent[-RECENT_M15_BARS:])
    return f"M15 last {min(len(recent), RECENT_M15_BARS)}: {shown}"


def limits_line(limits: object, state: object = "flat") -> str:
    if not isinstance(limits, Mapping):
        return "agent entry: -"
    if state not in (None, "flat"):
        return f"agent entry: none while a V6 {clean(state)} is open (manage it instead)"
    if limits.get("agent_entry_possible") is not True:
        return "agent entry: NOT POSSIBLE (budget cannot fund the stop floor)"
    return (f"agent entry id {clean(limits.get('agent_entry_id'), ID_CHARS)} | BUY LIMIT <= "
            f"{num(limits.get('buy_limit_max'))} SELL LIMIT >= "
            f"{num(limits.get('sell_limit_min'))} | BUY STOP >= "
            f"{num(limits.get('buy_stop_min'))} SELL STOP <= {num(limits.get('sell_stop_max'))}"
            f" (max {num(limits.get('max_entry_distance'))} away) | stop "
            f"{num(limits.get('stop_floor'))}...{num(limits.get('max_stop_distance'))} | tp1 >= "
            f"{num(limits.get('min_tp1_r'), '.1f')}R, tp3 "
            f"{num(limits.get('min_reward_r'), '.1f')}-{num(limits.get('max_reward_r'), '.1f')}R"
            f" | SL+ {num(limits.get('modify_distance'))} before its trigger | time "
            f"{num(limits.get('time_limit_min_minutes'), 'd')}-"
            f"{num(limits.get('time_limit_max_minutes'), 'd')} min, pending "
            f"{num(limits.get('pending_expiry_min_minutes'), 'd')}-"
            f"{num(limits.get('pending_expiry_max_minutes'), 'd')} min | budget "
            f"${num(limits.get('risk_budget_usd'))} | lots {num(limits.get('volume_min'))}-"
            f"{num(limits.get('max_lots'))} (you choose; the budget may reduce it)")


def plan_text(plan: object, *, with_step: bool) -> str:
    """The TP ladder and SL+ steps of a resting order or an open position."""
    if not isinstance(plan, Mapping) or not plan.get("tp1"):
        return "plan -"
    steps = (f"tp1 {num(plan.get('tp1'))} (sl+ {num(plan.get('sl_after_tp1') or None)}) "
             f"tp2 {num(plan.get('tp2'))} (sl+ {num(plan.get('sl_after_tp2') or None)})")
    return f"plan {steps}" + (f" step {text(plan.get('step'), 2)}" if with_step else "")


def position_line(position: object) -> str:
    if not isinstance(position, Mapping):
        return "state position | -"
    return (f"state position | ticket {text(position.get('ticket'), 20)} "
            f"{clean(position.get('side'))} {num(position.get('lots'))} @ "
            f"{num(position.get('open_price'))} | sl {num(position.get('sl'))} tp "
            f"{num(position.get('tp'))} | initial sl {num(position.get('initial_sl'))} | now "
            f"{num(position.get('r_now'), '+.2f')}R, "
            f"{num(position.get('minutes_open'), '.1f')} min open, limit "
            f"{_clock(position.get('time_limit_epoch'))}Z | "
            f"{plan_text(position.get('plan'), with_step=True)} | answer action MANAGE with "
            f"manage KEEP, CLOSE or MODIFY")


def pending_line(order: object) -> str:
    if not isinstance(order, Mapping):
        return "state pending | -"
    return (f"state pending | ticket {text(order.get('ticket'), 20)} "
            f"{clean(order.get('order_type'))} {num(order.get('price'))} sl "
            f"{num(order.get('sl'))} tp {num(order.get('tp'))} lots {num(order.get('lots'))} "
            f"expires {_clock(order.get('expiration_epoch'))}Z | market needs "
            f"{num(order.get('distance_from_quote'))} to fill | "
            f"{plan_text(order.get('plan'), with_step=False)} | answer action MANAGE with "
            f"manage KEEP, CANCEL or MODIFY")


def bias_line(bias: object, at: object) -> str | None:
    """The agent's last M15 bias, echoed by the adapter (None when there is none)."""
    if not isinstance(bias, Mapping):
        return None
    levels = bias.get("levels") if isinstance(bias.get("levels"), list) else []
    shown = " ".join(num(level) for level in levels)
    return (f"last bias {_clock(at)}Z {clean(bias.get('direction'))} | levels "
            f"{shown or MISSING} | invalidation {num(bias.get('invalidation'))} | "
            f"{text(bias.get('scenario'), 120)}")


def action_line(action: object) -> str | None:
    """The session's newest management action and what became of it."""
    if not isinstance(action, Mapping):
        return None
    return (f"last action {clean(action.get('action_id'))} {clean(action.get('op'))} ticket "
            f"{text(action.get('ticket'), 20)} {clean(action.get('status'))} "
            f"({text(action.get('detail'), DETAIL_CHARS)}) {_clock(action.get('at_epoch'))}Z")


def _state_lines(packet: Mapping[str, Any], state: object) -> list[str]:
    if state == "position":
        return [position_line(packet.get("position"))]
    if state == "pending":
        return [pending_line(packet.get("pending_order"))]
    return []


def _memory_lines(packet: Mapping[str, Any]) -> list[str]:
    lines = (bias_line(packet.get("last_bias"), packet.get("last_bias_at_epoch")),
             action_line(packet.get("last_action")))
    return [line for line in lines if line is not None]


def _suggestion_lines(candidates: list[Mapping[str, Any]], allowed: object) -> list[str]:
    return [f"suggestions ({len(candidates)}), PA TAKE needs conviction >= "
            f"{num(get_path(allowed, 'pa_min_conviction'))}:",
            *(candidate_line(index, item) for index, item in enumerate(candidates, start=1))]


def render_packet(packet: Mapping[str, Any], *, now: float, path: Path) -> str:
    candidates = [item for item in packet.get("candidates") or () if isinstance(item, Mapping)]
    allowed = packet.get("allowed")
    state = packet.get("state", "flat")
    lines = [
        *header_lines(packet, now, path),
        session_line(packet.get("session")),
        gates_line(packet.get("gates")),
        calendar_line(packet.get("calendar")),
        bars_line(packet.get("bars")),
        levels_line(packet.get("levels")),
        limits_line(packet.get("limits"), state),
        *_state_lines(packet, state),
        *_memory_lines(packet),
        *(_suggestion_lines(candidates, allowed) if state in (None, "flat") else []),
        baseline_line(packet.get("baseline_views")),
        f"agents {codes(get_path(allowed, 'agents'))} | next: template, decide (action "
        f"{'HOLD or ENTER' if state in (None, 'flat') else 'MANAGE'}), submit --agent <name> "
        f"before {utc(packet.get('expires_at_epoch'))}",
    ]
    return "\n".join(lines) + "\n"
