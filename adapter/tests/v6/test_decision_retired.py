"""Decisions v1 and v2 are retired (spec section 9, phase B)."""

from __future__ import annotations

import pytest

from app.v6.config import V6Settings
from app.v6.deliberation.decision_parts import RETIRED_DETAIL, parse_envelope
from app.v6.deliberation.operator_decision import DecisionError, validate_decision
from app.v6.schemas.operator import DECISION_SCHEMA, DECISION_SCHEMAS

from . import operator_fixtures_v6 as of

SETTINGS = V6Settings(_env_file=None)
NOW = float(of.CREATED + 10)
RETIRED = ("v6.operator.decision.1", "v6.operator.decision.2")


@pytest.mark.parametrize("version", RETIRED)
def test_old_decisions_are_refused(version: str) -> None:
    sealed = of.packet()
    document = {**of.decision_v3(sealed), "schema_version": version}
    outcome = validate_decision(sealed, of.raw(document), SETTINGS, now=NOW)
    assert isinstance(outcome, DecisionError) and outcome.code == "DECISION_SCHEMA"
    assert outcome.detail == RETIRED_DETAIL and "retired" in outcome.detail


@pytest.mark.parametrize("version", RETIRED)
def test_an_old_body_with_chief_and_rebuttal_is_refused_before_parsing(version: str) -> None:
    sealed = of.packet()
    old = {"schema_version": version, "cycle_id": sealed.cycle_id,
           "packet_hash": sealed.packet_hash, "agent": "codex", "views": {},
           "chief": {"action": "HOLD"}, "rebuttal": {}}
    outcome = parse_envelope(of.raw(old))
    assert isinstance(outcome, DecisionError) and outcome.detail == RETIRED_DETAIL


def test_only_version_3_is_a_decision_schema() -> None:
    assert DECISION_SCHEMAS == (DECISION_SCHEMA,) == ("v6.operator.decision.3",)
