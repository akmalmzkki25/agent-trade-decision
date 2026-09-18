"""
One operator decision checked role by role against the packet it answers.

Every role goes through `agents.validate_view` on its own, so the protocol's rule 2
holds exactly: an invalid (or missing) Price Action view refuses the submission, while
an invalid or missing news, liquidity or structure view is only flagged and the cycle
uses that desk's rules view instead.

Nothing an agent writes can leave the enum space: views are re-parsed strictly
(unknown fields, NaN, oversized text and ids that were not offered are refused), notes
are bounded printable text that no rule reads, and the plan and lots are checked against
the packet's limits, so the agent learns at once why a plan does not fit and can
resubmit before the deadline; `risk/` still sizes it and may refuse it.

A decision is version 3: `decision_v3` checks it against an m15 packet,
`decision_minute` against an m1 packet. Versions 1 and 2 are retired
(`decision_parts.parse_envelope` refuses them). The shared parts live in
`decision_parts` and are re-exported here. Error details never echo submitted text:
they name known fields, pydantic error types, VIEW_ERR_* codes and PROBLEM_* codes only.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TypeVar

from ..config import V6Settings
from ..cycle_types import DeskViews, ViewRecord
from ..providers.base import (
    ERR_TIMEOUT, OPERATOR_PROVIDER_NAME, PROVIDER_STATUS_FAILED, ProviderResult,
)
from ..schemas.operator import OperatorPacket
from .decision_parts import (  # noqa: F401 - re-exported for the operator API and tests
    KNOWN_FIELDS, RETIRED_DETAIL, RISK_DESKS, VIEW_MISSING, DecisionEnvelopeV3,
    DecisionError, DecisionOutcome, DeskFlag, Envelope, RawViews, ValidatedDecision,
    checked_desks, decision_error, error_locations, packet_problem, parse_envelope,
    parse_json_value, role_checker,
)
from .decision_minute import validate_minute
from .decision_v3 import validate_v3
from .panel import CHIEF_ROLE, Baseline, PanelResult

logger = logging.getLogger(__name__)

ViewT = TypeVar("ViewT")


def validate_decision(packet: OperatorPacket, decision: bytes | Envelope,
                      settings: V6Settings, *, now: float) -> DecisionOutcome:
    """Check one submission against the packet it answers; never raises on bad input.

    Order: size and envelope (v1 and v2 are retired), same cycle and packet hash, not
    expired at `now`, agent enabled (V6_OPERATOR_AGENTS and the packet), then the rules
    of the packet kind (`decision_v3.validate_v3` for m15, `decision_minute.
    validate_minute` for m1). The risk desks are flagged, never refused. Raises TypeError
    only when `decision` is not bytes.
    """
    envelope = (decision if isinstance(decision, DecisionEnvelopeV3)
                else parse_envelope(decision))
    if isinstance(envelope, DecisionError):
        return envelope
    validate = validate_minute if packet.packet_kind == "m1" else validate_v3
    outcome = validate(packet, envelope, settings, now=now)
    if isinstance(outcome, ValidatedDecision) and outcome.flags:
        logger.warning("v6 operator cycle %s: %s flagged %s; rules views used",
                       outcome.cycle_id, outcome.agent,
                       ",".join(f"{flag.role}={flag.code}" for flag in outcome.flags))
    return outcome


# --- tier 1 --------------------------------------------------------------------------------
def _either(primary: ViewT | None, fallback: ViewT | None) -> ViewT | None:
    return fallback if primary is None else primary


def panel_from_decision(decision: ValidatedDecision, baseline: Baseline) -> PanelResult:
    """Tier 1 from an accepted decision: flagged desks take their rules view, PA never does."""
    rules = baseline.views
    views = DeskViews(price_action=decision.price_action,
                      news_risk=_either(decision.news_risk, rules.news_risk),
                      liquidity=_either(decision.liquidity, rules.liquidity),
                      structure=_either(decision.structure, rules.structure))
    return PanelResult(views=views, decision=decision.chief, records=decision.records(),
                       provider=OPERATOR_PROVIDER_NAME, status=decision.status)


def timeout_panel(baseline: Baseline, *, latency_ms: int = 0) -> PanelResult:
    """No decision in time: a Chief timeout is recorded, PA stays empty, the cycle holds
    (the engine reports HoldReason.OPERATOR_TIMEOUT)."""
    failure = ProviderResult.failure(ERR_TIMEOUT, latency_ms=max(0, latency_ms))
    record = ViewRecord.from_result(CHIEF_ROLE, OPERATOR_PROVIDER_NAME, failure)
    return PanelResult(views=replace(baseline.views, price_action=None), decision=None,
                       records=(record,), provider=OPERATOR_PROVIDER_NAME,
                       status=PROVIDER_STATUS_FAILED)
