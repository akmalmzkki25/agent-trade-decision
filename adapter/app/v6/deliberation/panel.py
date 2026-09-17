"""
The agent panel for one cycle: the rules baseline (R0) and tier 1 (desks + Chief).

R0 runs the deterministic desks on every cycle that reaches tier 0; its views are
the dashboard's desk board and the fallback for a failed risk desk. Tier 1 asks
the configured backend. `panel=None` means the operator backend has no decision
channel attached: the cycle records a PROVIDER_UNAVAILABLE Chief attempt and the
protocol holds, because direction never comes from the rules desks there.

A provider's views are type-checked per role before use; a wrong or missing
Price Action view or Chief decision is left as None so the protocol holds on it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final, TypeVar

from ..clock import Clock
from ..cycle_types import DeliberationInput, DeskViews, ViewRecord, rules_packet
from ..providers.base import (
    ERR_UNAVAILABLE, OPERATOR_PROVIDER_NAME, PROVIDER_STATUS_FAILED, PROVIDER_STATUS_OK,
    PROVIDER_STATUS_PARTIAL, RULES_PROVIDER_NAME, AgentProvider, ProviderResult, ask_safely,
)
from ..schemas.agents import (
    DESK_ROLES, SCHEMA_NAMES, AgentRole, AgentView, ChiefDecision, DeskRole, LiquidityView,
    NewsRiskView, PriceActionView, StructureView,
)

ViewT = TypeVar("ViewT")
CHIEF_ROLE: Final[AgentRole] = "chief"
DESK_VIEW_TYPES: Final[dict[DeskRole, type]] = {
    "price_action": PriceActionView, "news_risk": NewsRiskView,
    "liquidity": LiquidityView, "structure": StructureView,
}


@dataclass(frozen=True)
class Baseline:
    """R0: the rules desk views of this cycle and their attempt records."""

    views: DeskViews
    records: tuple[ViewRecord, ...]


@dataclass(frozen=True)
class PanelResult:
    views: DeskViews                    # effective views the protocol reads
    decision: ChiefDecision | None
    records: tuple[ViewRecord, ...]     # tier-1 attempts (the baseline's are separate)
    provider: str
    status: str


def _typed(view: AgentView | None, expected: type[ViewT]) -> ViewT | None:
    return view if isinstance(view, expected) else None


def _desk_views(results: Sequence[ProviderResult]) -> DeskViews:
    by_role = dict(zip(DESK_ROLES, results))
    return DeskViews(**{role: _typed(by_role[role].view, DESK_VIEW_TYPES[role])
                        for role in DESK_ROLES})


async def _ask(provider: AgentProvider, role: AgentRole, inputs: DeliberationInput,
               deadline: float, clock: Clock) -> ProviderResult:
    return await ask_safely(provider, role, rules_packet(inputs), SCHEMA_NAMES[role],
                            deadline, clock)


async def _ask_desks(provider: AgentProvider, inputs: DeliberationInput, deadline: float,
                     clock: Clock) -> tuple[ProviderResult, ...]:
    """Desks never see each other's views."""
    blind = replace(inputs, views=DeskViews())
    calls = (_ask(provider, role, blind, deadline, clock) for role in DESK_ROLES)
    return tuple(await asyncio.gather(*calls))


def _records(provider: AgentProvider, roles: Sequence[AgentRole],
             results: Sequence[ProviderResult]) -> tuple[ViewRecord, ...]:
    return tuple(ViewRecord.from_result(role, provider.name, result)
                 for role, result in zip(roles, results))


async def rules_baseline(rules: AgentProvider, inputs: DeliberationInput, deadline: float,
                         clock: Clock) -> Baseline:
    """R0: every rules desk, recorded under the rules provider's name."""
    results = await _ask_desks(rules, inputs, deadline, clock)
    return Baseline(views=_desk_views(results), records=_records(rules, DESK_ROLES, results))


def _either(primary: ViewT | None, fallback: ViewT | None) -> ViewT | None:
    return fallback if primary is None else primary


def _merge(primary: DeskViews, fallback: DeskViews) -> DeskViews:
    """Risk desks fall back to the rules views; Price Action never does."""
    return DeskViews(
        price_action=primary.price_action,
        news_risk=_either(primary.news_risk, fallback.news_risk),
        liquidity=_either(primary.liquidity, fallback.liquidity),
        structure=_either(primary.structure, fallback.structure),
    )


def _status(desks: DeskViews, decision: ChiefDecision | None) -> str:
    if desks.price_action is None or decision is None:
        return PROVIDER_STATUS_FAILED
    if None in (desks.news_risk, desks.liquidity, desks.structure):
        return PROVIDER_STATUS_PARTIAL
    return PROVIDER_STATUS_OK


async def _chief(provider: AgentProvider, inputs: DeliberationInput, views: DeskViews,
                 deadline: float, clock: Clock) -> tuple[ChiefDecision | None, ViewRecord]:
    result = await _ask(provider, CHIEF_ROLE, replace(inputs, views=views), deadline, clock)
    record = ViewRecord.from_result(CHIEF_ROLE, provider.name, result)
    return _typed(result.view, ChiefDecision), record


async def _rules_panel(rules: AgentProvider, inputs: DeliberationInput, baseline: Baseline,
                       deadline: float, clock: Clock) -> PanelResult:
    decision, record = await _chief(rules, inputs, baseline.views, deadline, clock)
    return PanelResult(views=baseline.views, decision=decision, records=(record,),
                       provider=RULES_PROVIDER_NAME, status=_status(baseline.views, decision))


def unavailable_panel(baseline: Baseline) -> PanelResult:
    """No operator decision channel: risk desks keep their rules views, PA and Chief stay empty."""
    record = ViewRecord.from_result(CHIEF_ROLE, OPERATOR_PROVIDER_NAME,
                                    ProviderResult.failure(ERR_UNAVAILABLE))
    return PanelResult(views=replace(baseline.views, price_action=None), decision=None,
                       records=(record,), provider=OPERATOR_PROVIDER_NAME,
                       status=PROVIDER_STATUS_FAILED)


async def run_panel(panel: AgentProvider | None, rules: AgentProvider,
                    inputs: DeliberationInput, baseline: Baseline, deadline: float,
                    clock: Clock) -> PanelResult:
    """Tier 1. `panel` None: the operator backend has no decision channel attached."""
    if panel is None:
        return unavailable_panel(baseline)
    if panel is rules:
        return await _rules_panel(rules, inputs, baseline, deadline, clock)
    results = await _ask_desks(panel, inputs, deadline, clock)
    own = _desk_views(results)
    effective = _merge(own, baseline.views)
    decision, chief_record = await _chief(panel, inputs, effective, deadline, clock)
    records = _records(panel, DESK_ROLES, results) + (chief_record,)
    return PanelResult(views=effective, decision=decision, records=records,
                       provider=panel.name, status=_status(own, decision))
