"""
The operator packet for one cycle (plan section 3.3; docs/v6-wire-contract.md section 9).

Built from what tier 0 already computed: the market context, the gates, the
detector suggestions with their exit plans, the rules desk views and the breaker
allowance, plus the analysis blocks of `packet_extras` (closed bars M1-D1,
reference levels, the limits of an agent-designed entry). A suggestion is
offered only when it has an exit plan AND sizes at the standard tier; a packet
is served on every bar that reaches tier 1, with or without suggestions,
because the agent may design its own entry (user decision 2026-09-17). The rules
views are trimmed to what is offered, so the decision template (HOLD) is itself
an acceptable decision.

The agent sees an equity band, never the balance or the login, and never sets
lots: an agent entry is validated and sized by `risk/`. Text is cleaned of
control characters and cut to its bound before the strict schema checks the
whole packet again. Packets exist only for the operator backend and DEMO
accounts; anything else is a `PacketRefusal`, never a packet.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from pydantic import BaseModel

from ..config import V6Settings
from ..cycle_codes import (
    F_ATR_H1, F_ATR_M5, F_ATR_M15, FEATURE_KEYS, MAX_OFFERED_CANDIDATES, HoldReason,
)
from ..cycle_types import (
    CalendarAssessment, CalendarEvent, CandidateAssessment, DeskViews, MarketContext,
)
from ..risk.policy import (
    OPERATOR_MODES, OPERATOR_SOURCE, check_settings_policy, evaluate_account_policy,
)
from ..schemas.agents import NewsRiskView, PriceActionView
from ..schemas.operator import (
    MAX_CODES, MAX_EVENTS, MAX_FEATURES, MAX_GATES, PACKET_SCHEMA, OperatorPacket,
    OperatorPacketBody, allowed_values, canonical_json, equity_band, seal_packet,
)
from ..schemas.operator_parts import MAX_DETAIL_CHARS, MAX_SERVER_CHARS
from ..types import GateResult, Refusal
from .packet_extras import bars_block, levels_block, limits_block, pending_order_block
from .shadow import size_for

logger = logging.getLogger(__name__)

Document = dict[str, object]

STANDARD_MULTIPLIER: Final[float] = 1.0
MAX_GATE_VALUE_CHARS: Final[int] = 64
NO_EXIT_PLAN: Final[str] = "NO_EXIT_PLAN"
# The same patterns as schemas.operator_parts.Code and FeatureName: values that do
# not fit are dropped here instead of failing the whole packet.
CODE_RE: Final[re.Pattern[str]] = re.compile(r"[A-Z0-9_:.-]{1,48}")
FEATURE_NAME_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9_]{1,40}")

REFUSE_POLICY: Final[str] = "PACKET_POLICY"
REFUSE_NO_SESSION: Final[str] = "PACKET_NO_SESSION"
REFUSE_DEADLINE: Final[str] = "PACKET_DEADLINE_PASSED"
REFUSE_INVALID: Final[str] = "PACKET_INVALID"
REFUSAL_HOLD_REASONS: Final[Mapping[str, HoldReason]] = MappingProxyType({
    REFUSE_POLICY: HoldReason.GATE, REFUSE_NO_SESSION: HoldReason.NO_SESSION,
    REFUSE_DEADLINE: HoldReason.LATE, REFUSE_INVALID: HoldReason.ERROR,
})


@dataclass(frozen=True)
class PacketRequest:
    """Tier 0 of one cycle plus what only the runtime knows.

    `offered`: the engine's CandidatePool.offered; `baseline`: the rules desk views;
    `remaining_loss_usd`: BreakerStatus.remaining_loss_usd; `deadline_epoch`: when
    the engine stops waiting (the packet never outlives it).
    """

    context: MarketContext
    gates: tuple[GateResult, ...]
    offered: tuple[CandidateAssessment, ...]
    baseline: DeskViews
    remaining_loss_usd: float
    session_id: str | None
    armed: bool
    now: float
    deadline_epoch: float | None = None
    review: bool = False            # a V6 order rests: ask KEEP or CANCEL, offer no entry


@dataclass(frozen=True)
class PacketRefusal:
    """Why no packet was built; `hold_reason` is what the cycle records."""

    code: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.code not in REFUSAL_HOLD_REASONS:
            raise ValueError(f"unknown packet refusal {self.code[:40]!r}")
        object.__setattr__(self, "detail", self.detail[:MAX_DETAIL_CHARS])

    @property
    def hold_reason(self) -> HoldReason:
        return REFUSAL_HOLD_REASONS[self.code]


# --- entry point -----------------------------------------------------------------------------
def build_packet(request: PacketRequest, settings: V6Settings) -> OperatorPacket | PacketRefusal:
    """The sealed packet for `request`, or the refusal that stops the cycle.

    Checks, in order: operator backend in shadow/execute and the settings
    policy, an active session, the account policy for the operator source
    (DEMO only), a creation time before the deadline, and finally the full
    schema. Suggestions that do not size at the standard tier are left out; a
    packet without suggestions still offers the agent's own entry.
    """
    refusal = _policy_refusal(request, settings)
    if refusal is not None:
        return refusal
    window = _window(request, settings)
    if isinstance(window, PacketRefusal):
        return window
    candidates, skipped = ((), ()) if request.review else _sized_candidates(request, settings)
    if skipped:
        logger.info("v6 operator packet %s: suggestions left out (%s)",
                    request.context.cycle_id, ",".join(skipped))
    try:
        return seal_packet(_body(request, settings, window, candidates))
    except (ValueError, TypeError) as exc:  # pydantic's ValidationError is a ValueError
        logger.error("v6 operator packet for %s failed its schema: %s",
                     request.context.cycle_id, type(exc).__name__)
        return PacketRefusal(REFUSE_INVALID, f"packet failed its schema ({type(exc).__name__})")


def _policy_refusal(request: PacketRequest, settings: V6Settings) -> PacketRefusal | None:
    if settings.backend != OPERATOR_SOURCE or settings.mode not in OPERATOR_MODES:
        return PacketRefusal(REFUSE_POLICY, "packets need V6_BACKEND=operator in shadow/execute")
    settings_policy = check_settings_policy(settings)
    if not settings_policy.allowed:
        return PacketRefusal(REFUSE_POLICY, settings_policy.code)
    if request.session_id is None:
        return PacketRefusal(REFUSE_NO_SESSION, "no active trading session")
    context = request.context
    account = evaluate_account_policy(context.trade_mode, context.server,
                                      context.account.login, settings, source=OPERATOR_SOURCE)
    return None if account.allowed else PacketRefusal(REFUSE_POLICY, account.code)


def _window(request: PacketRequest, settings: V6Settings) -> tuple[int, int] | PacketRefusal:
    """(created_at, expires_at): expiry at bar close + V6_OPERATOR_DEADLINE_S at most."""
    close = request.context.as_of_epoch
    expires = close + settings.operator_deadline_s
    deadline, now = request.deadline_epoch, request.now
    if not (_finite(now) and (deadline is None or _finite(deadline))):
        return PacketRefusal(REFUSE_DEADLINE, "the clock or the deadline is not finite")
    if deadline is not None:
        expires = min(expires, math.floor(deadline))
    created = max(close, math.floor(now))
    if created >= expires:
        return PacketRefusal(REFUSE_DEADLINE, f"created {created} is not before {expires}")
    return created, expires


# --- candidates ------------------------------------------------------------------------------
def _sized_candidates(request: PacketRequest, settings: V6Settings
                      ) -> tuple[tuple[Document, ...], tuple[str, ...]]:
    offered: list[Document] = []
    skipped: list[str] = []
    for item in request.offered:
        document = _candidate(request, item, settings)
        if isinstance(document, Refusal):
            skipped.extend(document.codes)
        elif len(offered) < MAX_OFFERED_CANDIDATES:
            offered.append(document)
    return tuple(offered), tuple(dict.fromkeys(skipped))


def _candidate(request: PacketRequest, item: CandidateAssessment,
               settings: V6Settings) -> Document | Refusal:
    plan = item.exit_plan
    if plan is None:
        return Refusal((NO_EXIT_PLAN,))
    sizing = size_for(request.context, plan, STANDARD_MULTIPLIER, request.remaining_loss_usd,
                      settings)
    if isinstance(sizing, Refusal):
        return sizing
    candidate = item.candidate
    return {
        "candidate_id": candidate.candidate_id, "setup": candidate.setup, "side": candidate.side,
        "entry": plan.entry, "invalidation": candidate.invalidation,
        "reason_codes": _codes(candidate.reason_codes),
        "features": _features(candidate.features, MAX_FEATURES),
        "exit": {"sl": plan.sl, "tp": plan.tp, "stop_distance": plan.stop_distance,
                 "reward_r": plan.reward_r, "time_barrier_s": plan.time_barrier_s},
        "sizing": {"lots": sizing.lots, "risk_usd": sizing.risk_usd,
                   "loss_per_lot": sizing.loss_per_lot},
        "sizing_refusal": [],
    }


# --- body ------------------------------------------------------------------------------------
def _body(request: PacketRequest, settings: V6Settings, window: tuple[int, int],
          candidates: tuple[Document, ...]) -> OperatorPacketBody:
    context = request.context
    events = context.calendar.events[:MAX_EVENTS]
    ids = tuple(str(document["candidate_id"]) for document in candidates)
    event_ids = tuple(event.event_id for event in events)
    limits = limits_block(context, settings, request.remaining_loss_usd, review=request.review)
    allowed = allowed_values(operator_agents=settings.operator_agents,
                             candidate_ids=ids + (str(limits["agent_entry_id"]),),
                             event_ids=event_ids, pa_min_conviction=settings.pa_min_conviction)
    document: Document = {
        "schema_version": PACKET_SCHEMA, "cycle_id": context.cycle_id,
        "created_at_epoch": window[0], "expires_at_epoch": window[1],
        "bar_open_epoch": context.bar_open_epoch, "bar_close_epoch": context.as_of_epoch,
        "mode": settings.mode, "session_id": request.session_id,
        "account": {"trade_mode": context.trade_mode,
                    "server": _text(context.server, MAX_SERVER_CHARS),
                    "equity_band": equity_band(context.account.equity)},
        "market": _market(context),
        "session": _session(context, request.armed),
        "bars": bars_block(context),
        "levels": levels_block(context),
        "limits": limits,
        "gates": [_gate(gate) for gate in request.gates[:MAX_GATES]],
        "calendar": _calendar(context.calendar, events),
        "candidates": list(candidates),
        "baseline_views": _baseline(request.baseline, frozenset(ids), frozenset(event_ids)),
        "allowed": allowed.model_dump(mode="json"),
        "pending_order": pending_order_block(context) if request.review else None,
    }
    return OperatorPacketBody.model_validate_json(canonical_json(document))


def _market(context: MarketContext) -> Document:
    features = _features(context.features, len(FEATURE_KEYS), FEATURE_KEYS)
    quote = context.quote
    return {"bid": quote.bid, "ask": quote.ask, "spread_points": quote.spread_points,
            "atr_m5": _positive(features.get(F_ATR_M5)),
            "atr_m15": _positive(features.get(F_ATR_M15)),
            "atr_h1": _positive(features.get(F_ATR_H1)), "features": features}


def _session(context: MarketContext, armed: bool) -> Document:
    state = context.session
    return {"phase": state.phase, "main_window_third": state.main_window_third,
            "entries_allowed": state.entries_allowed,
            "continuation_allowed": state.continuation_allowed,
            "block_reasons": _codes(state.block_reasons), "armed": bool(armed),
            "quality": state.quality, "in_main_window": bool(state.in_main_window)}


def _gate(gate: GateResult) -> Document:
    return {"code": gate.code, "passed": bool(gate.passed), "value": _gate_value(gate.value),
            "limit": _gate_value(gate.limit), "detail": _text(gate.detail, MAX_DETAIL_CHARS)}


def _calendar(calendar: CalendarAssessment, events: Sequence[CalendarEvent]) -> Document:
    return {"as_of_epoch": calendar.as_of_epoch, "blackout": calendar.blackout,
            "stale": calendar.stale, "codes": _codes(calendar.codes),
            "next_event_minutes": _number(calendar.next_event_minutes),
            "last_event_minutes_ago": _number(calendar.last_event_minutes_ago),
            "events": [_event(event) for event in events]}


def _event(event: CalendarEvent) -> Document:
    return {"event_id": event.event_id, "time_epoch": event.time_epoch,
            "currency": event.currency, "importance": event.importance, "code": event.code,
            "actual": _number(event.actual), "forecast": _number(event.forecast),
            "previous": _number(event.previous)}


# --- baseline views -----------------------------------------------------------------------------
def _baseline(views: DeskViews, candidate_ids: frozenset[str],
              event_ids: frozenset[str]) -> Document:
    """The rules views, limited to what this packet offers."""
    return {"price_action": _dump(_offered_ranking(views.price_action, candidate_ids)),
            "news_risk": _news(views.news_risk, event_ids),
            "liquidity": _dump(views.liquidity), "structure": _dump(views.structure)}


def _offered_ranking(view: PriceActionView | None,
                     candidate_ids: frozenset[str]) -> PriceActionView | None:
    if view is None:
        return None
    ranked = tuple(item for item in view.ranked if item.candidate_id in candidate_ids)
    if ranked == view.ranked:
        return view
    return PriceActionView(abstain=not ranked, ranked=ranked)


def _news(view: NewsRiskView | None, event_ids: frozenset[str]) -> Document | None:
    if view is None:
        return None
    return {**view.model_dump(mode="json"),
            "event_ids": [event_id for event_id in view.event_ids if event_id in event_ids]}


def _dump(view: BaseModel | None) -> Document | None:
    return None if view is None else view.model_dump(mode="json")


# --- scalar helpers ------------------------------------------------------------------------------
def _finite(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # an int beyond float range
        return False


def _number(value: object) -> float | None:
    return float(value) if _finite(value) else None  # type: ignore[arg-type]


def _positive(value: object) -> float | None:
    number = _number(value)
    return number if number is not None and number > 0 else None


def _text(value: object, limit: int) -> str:
    """Untrusted text: printable characters only, cut to `limit`."""
    return "".join(ch for ch in str(value) if ch.isprintable())[:limit]


def _gate_value(value: object) -> float | str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).upper()
    if isinstance(value, (int, float)):
        return _number(value)
    return _text(value, MAX_GATE_VALUE_CHARS)


def _codes(values: Iterable[object], limit: int = MAX_CODES) -> list[str]:
    valid = (value for value in values if isinstance(value, str) and CODE_RE.fullmatch(value))
    return list(dict.fromkeys(valid))[:limit]


def _features(values: Mapping[str, object], limit: int,
              allowed: frozenset[str] | None = None) -> dict[str, float]:
    usable = [(key, float(value))  # type: ignore[arg-type]
              for key, value in sorted(values.items(), key=lambda item: str(item[0]))
              if isinstance(key, str) and FEATURE_NAME_RE.fullmatch(key)
              and (allowed is None or key in allowed) and _finite(value)]
    return dict(usable[:limit])
