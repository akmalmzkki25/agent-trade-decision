"""
The short summary `wait` prints for an m1 packet (three lines; spec section 5).

An m1 packet comes every minute and must be answered within V6_M1_DEADLINE_S, so the
summary shows only what an m1 decision needs: the state and quote, what the last M1 bars
did, the M15 bias still in force, the managed trade's distances, and the quick answer.
Everything else stays in packet.json. An m15 packet keeps the full summary.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from .context import get_path
from .packet_view import num, render_packet, utc

QUICK_HINT: Final[str] = ("no change: submit --agent <name> --quick | otherwise template, "
                          "edit (entry_plan or manage), submit before ")


def _hhmm(epoch: object) -> str:
    text = utc(epoch)
    return text[11:16] if len(text) >= 16 else text


def _move(move: object) -> str:
    if not isinstance(move, Mapping):
        return "?"
    return (f"{move.get('bars')} bars {move.get('direction')} {num(move.get('change'), '+.2f')}"
            f" ({num(move.get('strength'), '.1f')}x)")


def _distances(packet: Mapping[str, Any]) -> str:
    distances = get_path(packet, "m1_state", "distances")
    if not isinstance(distances, Mapping):
        return ""
    return " ".join(f"{key} {num(value, '+.2f')}" for key, value in distances.items())


def _trade(packet: Mapping[str, Any], now: float) -> str:
    state = packet.get("state")
    block = packet.get("position") if state == "position" else packet.get("pending_order")
    if not isinstance(block, Mapping):
        return ""
    if state == "position":
        limit = block.get("time_limit_epoch")
        left = int(limit - now) // 60 if isinstance(limit, (int, float)) else 0
        return (f" | pos {block.get('side')} {block.get('lots')} @ {num(block.get('open_price'))}"
                f" step {get_path(block, 'plan', 'step')} | to {_distances(packet)}"
                f" | {max(0, left)} min left")
    return (f" | order {block.get('order_type')} @ {num(block.get('price'))}"
            f" | to {_distances(packet)}")


def _bias(packet: Mapping[str, Any]) -> str:
    bias = packet.get("last_bias")
    if not isinstance(bias, Mapping):
        return "bias none"
    return (f"bias {bias.get('direction')} @{_hhmm(packet.get('last_bias_at_epoch'))}Z"
            f" inv {num(bias.get('invalidation'))}")


def render_minute(packet: Mapping[str, Any], *, now: float, path: Path) -> str:
    state = packet.get("m1_state") if isinstance(packet.get("m1_state"), Mapping) else {}
    market = packet.get("market") if isinstance(packet.get("market"), Mapping) else {}
    expires = packet.get("expires_at_epoch")
    left = int(expires - now) if isinstance(expires, (int, float)) else 0
    first = (f"M1 PACKET {packet.get('cycle_id')} | {_hhmm(packet.get('bar_close_epoch'))}Z"
             f" | {packet.get('state')} | bid {num(market.get('bid'))}"
             f" ask {num(market.get('ask'))} spr {market.get('spread_points')}"
             f" | {max(0, left)} s left | file {path}")
    second = (f"m1: atr {num(state.get('atr_m1'))} range15 {num(state.get('range_15'))} | "
              f"{_move(state.get('last_5'))} | {_move(state.get('last_15'))} | "
              f"{num(state.get('quotes_per_s'), '.1f')} q/s | {_bias(packet)}"
              f"{_trade(packet, now)}")
    return "\n".join((first, second, QUICK_HINT + utc(expires))) + "\n"


def render_any(packet: Mapping[str, Any], *, now: float, path: Path) -> str:
    """The m1 summary for an m1 packet, the full summary otherwise."""
    if packet.get("packet_kind") == "m1":
        return render_minute(packet, now=now, path=path)
    return render_packet(packet, now=now, path=path)
