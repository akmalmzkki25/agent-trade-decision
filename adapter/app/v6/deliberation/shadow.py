"""
From an approved candidate to the order Phase 2 would have sent (never published).

Only `risk/` turns risk into prices and lots: the exit plan comes from
`risk.exits`, the size from `risk.sizing`, and the side always from the
candidate the detectors produced. The intent source must pass the demo policy
again (plan section 3.3, layer 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ..config import V6Settings
from ..cycle_types import (
    CandidateAssessment, MarketContext, ProtocolDecision, ShadowIntent, ShadowOrderType,
)
from ..risk.policy import PolicyDecision, check_intent_source
from ..risk.sizing import size_position
from ..types import (
    TIMEFRAME_SECONDS, Candidate, ExitPlan, Refusal, Side, SizingRequest, SizingResult,
)
from .context_builder import cycle_friction, margin_per_lot

M15_S: Final[int] = TIMEFRAME_SECONDS["M15"]
LIMIT_STYLE: Final[str] = "LIMIT"


@dataclass(frozen=True)
class ShadowOrder:
    """What the ENTER branch produced: an intent, or the refusal that stopped it."""

    exit_plan: ExitPlan
    sizing: SizingResult | None = None
    refusal: Refusal | None = None
    policy: PolicyDecision | None = None
    intent: ShadowIntent | None = None


def size_for(context: MarketContext, plan: ExitPlan, multiplier: float,
             remaining_loss_usd: float, settings: V6Settings) -> SizingResult | Refusal:
    account = context.account
    request = SizingRequest(
        equity=account.equity, balance=account.balance, free_margin=account.free_margin,
        price=plan.entry, stop_distance=plan.stop_distance, risk_pct=settings.risk_pct,
        size_multiplier=multiplier, remaining_daily_loss_usd=remaining_loss_usd,
        margin_per_lot=margin_per_lot(context, plan.side),
        friction_price=cycle_friction(settings, context), spec=context.spec,
    )
    return size_position(request, equity_basis_usd=settings.sizing_equity_basis_usd,
                         max_lots=settings.max_lots,
                         notional_ratio_max=settings.notional_ratio_max)


def _order_type(side: Side, style: str) -> ShadowOrderType:
    if style == LIMIT_STYLE:
        return "BUY_LIMIT" if side == "buy" else "SELL_LIMIT"
    return "BUY" if side == "buy" else "SELL"


def _intent(context: MarketContext, candidate: Candidate, plan: ExitPlan,
            protocol: ProtocolDecision, sizing: SizingResult, source: str,
            settings: V6Settings) -> ShadowIntent:
    return ShadowIntent(
        cycle_id=context.cycle_id, candidate_id=candidate.candidate_id, setup=candidate.setup,
        side=candidate.side, order_type=_order_type(candidate.side, protocol.order_style),
        entry=plan.entry, sl=plan.sl, tp=plan.tp, lots=sizing.lots, risk_usd=sizing.risk_usd,
        risk_budget_usd=sizing.risk_budget_usd, size_multiplier=protocol.size_multiplier,
        risk_tier=protocol.risk_tier, exit_profile=protocol.exit_profile,
        time_barrier_s=plan.time_barrier_s,
        valid_until_epoch=context.as_of_epoch + settings.pending_expiry_bars * M15_S,
        source=source, labels=plan.labels,
    )


def shadow_order(context: MarketContext, item: CandidateAssessment,
                 protocol: ProtocolDecision, *, source: str, remaining_loss_usd: float,
                 settings: V6Settings) -> ShadowOrder:
    """Policy check, sizing and the shadow intent for the protocol's ENTER pick."""
    plan = item.exit_plan
    if plan is None:
        raise ValueError("an offered candidate must carry an exit plan")
    policy = check_intent_source(source, context.trade_mode, context.server,
                                 context.account.login, settings)
    if not policy.allowed:
        return ShadowOrder(exit_plan=plan, policy=policy)
    sizing = size_for(context, plan, protocol.size_multiplier, remaining_loss_usd, settings)
    if isinstance(sizing, Refusal):
        return ShadowOrder(exit_plan=plan, refusal=sizing, policy=policy)
    intent = _intent(context, item.candidate, plan, protocol, sizing, source, settings)
    return ShadowOrder(exit_plan=plan, sizing=sizing, policy=policy, intent=intent)
