"""
From an approved operator ENTER to the intent the EA may execute (plan sections 3.3, 5, 6).

`build_intent` is pure and the adapter's last word before money moves. It checks, in
order: execute mode, the settings policy, the demo policy (layer 3: the operator decides
for DEMO accounts only), the quote's account, the agent, the ARMED execute session, the
inputs, the candidate, the quote, the geometry, the grids, the lots and the loss at the
stop. The first failure is the refusal (a POLICY_* code when a policy refused); nothing
is rounded, widened or defaulted into shape.

`risk.order_choice` picks the order type: without an agent plan the quote decides (limits
preferred, kn/15), with one the agent has chosen already (LIMIT, STOP or MARKET) and the
quote only says whether it still fits. An agent plan also brings its SL+ ladder, its
holding time and how long a pending order may rest; they travel to the EA in intent v2.

EA limits: ref_price = ask (buy) / bid (sell); max_drift_points = DRIFT_STOP_FRACTION
(20%) of the stop in points, within [MIN_DRIFT_POINTS, V6_MAX_DRIFT_POINTS];
max_spread_points = the effective spread gate; valid_until = floor(now) +
V6_INTENT_TTL_S (a limit: at most pending expiry - 60 s), at least MIN_VALIDITY_S away;
pending expiry = bar close + V6_PENDING_EXPIRY_BARS x 900 (market: 0); time barrier =
min(V6_TIME_BARRIER_BARS x 900, the plan's, 4 h); magic = V6_MAGIC; require_demo = 1.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Final, Literal

from pydantic import SecretStr

from .. import wire
from ..config import V6Settings
from ..cycle_types import MarketContext, ProtocolDecision
from ..ledger_cycles_schema import SessionRecord
from ..ledger_intents import NewIntent
from ..schemas.intent import MIN_PENDING_LIFETIME_S, PollResponse, new_intent_id
from ..types import Candidate, ExitPlan, Refusal, SizingResult, TradePlan
from . import limits, policy
# The order-choice refusals are re-exported: the publisher maps them by this module's names.
from .order_choice import (  # noqa: F401
    LIMIT_NOT_PASSIVE, MARKET_MOVED, MARKET_REWARD_BELOW_1R, MARKET_STOP_BELOW_FLOOR,
    RISK_OVER_BUDGET, SIDE_SIGN, STOP_NOT_BEYOND, ChosenOrder, OrderInputs, ReferenceQuote,
    choose_order, dec, loss_usd, stop_distance,
)

ENTER_ACTION: Final[str] = "ENTER"
MIN_VALIDITY_S: Final[int] = 10            # five EA polls
LOT_GRID: Final[float] = 0.01              # signed lots are integer hundredths
_CENT: Final[Decimal] = Decimal("0.01")

# Refusal codes (a policy refusal carries its POLICY_* code instead).
NOT_EXECUTE_MODE: Final[str] = "NOT_EXECUTE_MODE"
QUOTE_ACCOUNT: Final[str] = "QUOTE_ACCOUNT"
SESSION_INACTIVE: Final[str] = "SESSION_INACTIVE"
SESSION_NOT_ARMED: Final[str] = "SESSION_NOT_ARMED"
BAD_INPUT: Final[str] = "BAD_INPUT"
NOT_ENTER: Final[str] = "NOT_ENTER"
CANDIDATE_MISMATCH: Final[str] = "CANDIDATE_MISMATCH"
QUOTE_UNUSABLE: Final[str] = "QUOTE_UNUSABLE"
QUOTE_STALE: Final[str] = "QUOTE_STALE"
BAD_GEOMETRY: Final[str] = "BAD_GEOMETRY"
OFF_GRID: Final[str] = "OFF_GRID"
LOT_LIMIT: Final[str] = "LOT_LIMIT"
TOO_LATE: Final[str] = "INTENT_TOO_LATE"


@dataclass(frozen=True)
class IntentDraft:
    """A publishable intent: its validated v6_intents row plus what only the EA reads."""

    row: NewIntent
    candidate_id: str
    ref_price: float
    max_drift_points: int
    max_spread_points: int
    magic: int
    require_demo: Literal[1] = 1

    def __post_init__(self) -> None:
        if not (isinstance(self.row, NewIntent) and self.row.source == policy.OPERATOR_SOURCE
                and isinstance(self.candidate_id, str)):
            raise ValueError("an intent draft needs an operator NewIntent and a candidate id")
        _response(self, math.floor(self.row.created_at))  # the EA contract (ValueError)


def _response(intent: IntentDraft, server_time: int) -> PollResponse:
    row = intent.row
    return PollResponse(
        server_time_epoch=server_time, command="NONE", has_intent=True,
        intent_id=row.intent_id, source="operator", require_demo=intent.require_demo,
        side=row.side, order_type=row.order_type, entry=row.entry, sl=row.sl, tp=row.tp,
        lots=row.lots, ref_price=intent.ref_price, max_drift_points=intent.max_drift_points,
        max_spread_points=intent.max_spread_points, valid_until_epoch=row.valid_until_epoch,
        pending_expiry_epoch=row.pending_expiry_epoch, time_barrier_s=row.time_barrier_s,
        magic=intent.magic, tp1=row.tp1, tp2=row.tp2, sl_after_tp1=row.sl_after_tp1,
        sl_after_tp2=row.sl_after_tp2)


def to_poll_response(intent: IntentDraft, server_time: int, key: SecretStr | None,
                     point: float) -> PollResponse:
    """The flat v6.intent.2 answer for `intent`, signed when `key` is a valid EA key.

    `point` is the newest snapshot's SYMBOL_POINT (`CarryOver.point`). ValueError when the
    intent is no longer valid at `server_time` or a number is off its grid (unsignable).
    """
    response = _response(intent, server_time)
    wire.intent_canonical(response, point)  # every signed number must sit on its grid
    return response if key is None else wire.sign_intent(key, response, point)


def _positive(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def _on_grid(value: float, step: float) -> bool:
    try:
        wire.price_to_points(value, step)
    except ValueError:
        return False
    return True


def _unless(ok: bool, code: str, detail: str) -> Refusal | None:
    return None if ok else Refusal((code,), detail)


def _policy(decision: policy.PolicyDecision) -> Refusal | None:
    return None if decision.allowed else Refusal((decision.code,), decision.detail)


def _first(checks: Iterable[Callable[[], Refusal | None]]) -> Refusal | None:
    return next((found for found in (check() for check in checks) if found is not None), None)


def _authority_refusal(i: OrderInputs) -> Refusal | None:
    settings, context, quote, session = i.settings, i.context, i.quote, i.session
    account = (context.account.login, context.server, context.trade_mode)
    return _first((
        lambda: _unless(settings.mode == policy.EXECUTE_MODE, NOT_EXECUTE_MODE,
                        f"V6_MODE={settings.mode} never publishes intents"),
        lambda: _policy(policy.check_settings_policy(settings)),
        lambda: _policy(policy.check_intent_source(
            policy.OPERATOR_SOURCE, context.trade_mode, context.server, context.account.login,
            settings)),
        lambda: _unless((quote.login, quote.server, quote.trade_mode) == account, QUOTE_ACCOUNT,
                        "the reference quote comes from another account"),
        lambda: _policy(policy.check_operator_agent(i.agent, settings)),
        lambda: _unless(session is not None and session.is_active, SESSION_INACTIVE,
                        "no active daily session"),
        lambda: _unless(session is not None and session.armed
                        and session.mode == policy.EXECUTE_MODE, SESSION_NOT_ARMED,
                        "the session is not an armed execute session"),
    ))


def _order_refusal(i: OrderInputs) -> Refusal | None:
    plan, spec, quote, sign = i.plan, i.context.spec, i.quote, i.sign
    grids, quoted = (spec.tick_size, spec.point), (quote.bid, quote.ask)
    return _first((
        lambda: _unless(i.resolution.action == ENTER_ACTION, NOT_ENTER, "no approved entry"),
        lambda: _unless(abs(dec(plan.entry) - dec(i.candidate.entry)) * 2 < dec(spec.point)
                        and (plan.side, i.resolution.candidate_id) == (
                            i.candidate.side, i.candidate.candidate_id),
                        CANDIDATE_MISMATCH, "decision, candidate and exit plan disagree"),
        lambda: _unless(all(_positive(p) for p in quoted) and quote.ask >= quote.bid
                        and all(_on_grid(p, g) for p in quoted for g in grids),
                        QUOTE_UNUSABLE, "the quote is no on-grid bid <= ask"),
        lambda: _unless(-i.settings.max_clock_skew_s <= i.now - quote.observed_at
                        <= i.settings.ea_stale_s, QUOTE_STALE, "the quote is too old or new"),
        lambda: _unless(sign * (plan.entry - plan.sl) > 0 and sign * (plan.tp - plan.entry) > 0,
                        BAD_GEOMETRY, "sl and tp must sit on their own side of entry"),
        lambda: _unless(all(_on_grid(p, g) for p in (plan.entry, plan.sl, plan.tp) for g in grids),
                        OFF_GRID, "entry, sl and tp must sit on the tick and point grids"),
        lambda: _unless(_lots_ok(i), LOT_LIMIT, f"lots {i.sizing.lots} break the lot limits"),
        lambda: _unless(_within_budget(i), RISK_OVER_BUDGET, "the stop loses more than budgeted"),
    ))


def _input_refusal(i: OrderInputs) -> Refusal | None:
    plan, sizing, spec = i.plan, i.sizing, i.context.spec
    numbers = {
        "entry": plan.entry, "sl": plan.sl, "tp": plan.tp, "stop": plan.stop_distance,
        "candidate_entry": i.candidate.entry, "lots": sizing.lots, "risk": sizing.risk_usd,
        "budget": sizing.risk_budget_usd, "point": spec.point, "tick_size": spec.tick_size,
        "tick_loss": spec.tick_value_loss, "volume_min": spec.volume_min,
        "volume_step": spec.volume_step, "now": i.now,
    }
    bad = [name for name, value in numbers.items() if not _positive(value)]
    bad += [] if plan.side in SIDE_SIGN else ["side"]
    bad += [] if type(plan.time_barrier_s) is int and plan.time_barrier_s > 0 else ["barrier"]
    bad += [] if type(spec.stops_level) is int and spec.stops_level >= 0 else ["stops_level"]
    return _unless(not bad, BAD_INPUT, "unusable: " + ", ".join(bad))


def _lots_ok(i: OrderInputs) -> bool:
    lots, spec = i.sizing.lots, i.context.spec
    cap = min(dec(i.settings.max_lots), dec(limits.MAX_EXECUTE_LOTS))
    return (dec(spec.volume_min) <= dec(lots) <= cap and _on_grid(lots, spec.volume_step)
            and _on_grid(lots, LOT_GRID))


def _within_budget(i: OrderInputs) -> bool:
    budget = dec(i.sizing.risk_budget_usd)
    distance = max(dec(i.plan.stop_distance), stop_distance(i, dec(i.plan.entry)))
    return dec(i.sizing.risk_usd) <= budget and loss_usd(i, distance) <= budget


def _timing(i: OrderInputs, order: ChosenOrder) -> tuple[int, int] | Refusal:
    """(valid_until_epoch, pending_expiry_epoch), or TOO_LATE."""
    settings, close = i.settings, i.context.as_of_epoch
    lifetime = settings.pending_expiry_s if i.trade is None else i.trade.pending_expiry_s
    valid_until = math.floor(i.now) + settings.intent_ttl_s
    pending_expiry = close + lifetime if order.pending else 0
    if order.pending:
        valid_until = min(valid_until, pending_expiry - MIN_PENDING_LIFETIME_S)
    if i.now - close <= settings.operator_deadline_s and valid_until - i.now >= MIN_VALIDITY_S:
        return valid_until, pending_expiry
    return Refusal((TOO_LATE,), f"a decision {i.now - close:.0f} s after the close is too late")


def _barrier(i: OrderInputs) -> int:
    """The holding time: the agent plan's, or the shortest of the configured limits."""
    if i.trade is not None:
        return min(i.trade.time_limit_s, limits.MAX_TIME_BARRIER_S)
    return min(i.settings.time_barrier_s, i.plan.time_barrier_s, limits.MAX_TIME_BARRIER_S)


def _draft(i: OrderInputs, order: ChosenOrder, valid_until: int, pending_expiry: int,
           new_id: Callable[[], str]) -> IntentDraft | Refusal:
    settings, plan = i.settings, i.plan
    trade = i.trade or TradePlan(order_type="", tp1=0.0, tp2=0.0, sl_after_tp1=0.0,
                                 sl_after_tp2=0.0, time_limit_s=0, pending_expiry_s=0)
    risk = max(order.loss_usd, dec(i.sizing.risk_usd)).quantize(_CENT, rounding=ROUND_CEILING)
    try:
        row = NewIntent(
            intent_id=new_id(), cycle_id=i.context.cycle_id,
            session_id=i.session.session_id if i.session is not None else "", agent=i.agent,
            source=policy.OPERATOR_SOURCE, side=plan.side, order_type=order.order_type,
            entry=order.entry, sl=plan.sl, tp=plan.tp, lots=i.sizing.lots, risk_usd=float(risk),
            valid_until_epoch=valid_until, pending_expiry_epoch=pending_expiry,
            time_barrier_s=_barrier(i), created_at=i.now,
            tp1=trade.tp1, tp2=trade.tp2, sl_after_tp1=trade.sl_after_tp1,
            sl_after_tp2=trade.sl_after_tp2)
        return IntentDraft(
            row=row, candidate_id=i.candidate.candidate_id,
            ref_price=i.quote.side_price(plan.side), max_drift_points=order.drift_points,
            max_spread_points=min(settings.effective_max_spread_points,
                                  limits.MAX_SPREAD_POINTS[settings.account_type]),
            magic=settings.magic)
    except ValueError as exc:
        return Refusal((BAD_INPUT,), str(exc))


def build_intent(resolution: ProtocolDecision, candidate: Candidate, exit_plan: ExitPlan,
                 sizing: SizingResult, context: MarketContext, settings: V6Settings,
                 session: SessionRecord | None, now: float, *, agent: str,
                 quote: ReferenceQuote | None = None, trade: TradePlan | None = None,
                 new_id: Callable[[], str] = new_intent_id) -> IntentDraft | Refusal:
    """The intent for an approved operator ENTER (a `Resolution` passes `.to_decision()`).

    `sizing` sizes `exit_plan` (`deliberation.shadow.size_for`), `agent` submitted the
    decision, `quote` is the newest EA poll quote (`ReferenceQuote.from_poll`; the snapshot's
    is used when fresher). Bad data never raises: it is a BAD_INPUT refusal.
    """
    own = ReferenceQuote.from_context(context)
    fresher = quote is not None and quote.observed_at >= own.observed_at
    i = OrderInputs(resolution=resolution, candidate=candidate, plan=exit_plan,
                    sizing=sizing, context=context, settings=settings, session=session,
                    now=now, agent=agent,
                    quote=quote if fresher and quote is not None else own, trade=trade)
    refusal = _first((lambda: _authority_refusal(i), lambda: _input_refusal(i),
                      lambda: _order_refusal(i)))
    if refusal is not None:
        return refusal
    order = choose_order(i)
    if isinstance(order, Refusal):
        return order
    timing = _timing(i, order)
    if isinstance(timing, Refusal):
        return timing
    return _draft(i, order, *timing, new_id)
