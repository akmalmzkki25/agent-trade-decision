"""Demo-only account policy (plan section 3.3 layers 1-3): truth tables per backend."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from pydantic import SecretStr

from app.v6.config import V6Settings
from app.v6.risk.policy import (
    DEMO_403_CODE, POLICY_AGENT_NOT_ALLOWED, POLICY_CONTEST_REFUSED,
    POLICY_EXECUTE_LOT_CAP, POLICY_EXECUTE_NEEDS_KEY, POLICY_EXECUTE_NEEDS_OPERATOR,
    POLICY_LOGIN_NOT_ALLOWED, POLICY_OK, POLICY_OPERATOR_DEMO_ONLY, POLICY_OPERATOR_MODE,
    POLICY_REAL_ACCOUNT_FLAG, POLICY_REAL_REFUSED, POLICY_SERVER_NOT_DEMO,
    POLICY_UNKNOWN_SOURCE, POLICY_UNKNOWN_TRADE_MODE, SOURCES, PolicyDecision,
    check_intent_source, check_operator_agent, check_settings_policy, evaluate_account_policy,
    is_demo_allowed,
)
from app.v6.schemas.intent import SOURCES as INTENT_SOURCES

DEMO_SERVER = "MetaQuotes-Demo"
LOGIN = "12345"
BACKENDS = ("rules", "operator")
EA_KEY = "ea-key-" + "k" * 40

# (trade_mode, backend) -> expected code. Only DEMO is allowed in V6.0, for every
# backend; the operator reports its permanent rule before the V6.0 refusals.
TRUTH_TABLE = {
    ("DEMO", "rules"): POLICY_OK,
    ("DEMO", "operator"): POLICY_OK,
    ("CONTEST", "rules"): POLICY_CONTEST_REFUSED,
    ("CONTEST", "operator"): POLICY_OPERATOR_DEMO_ONLY,
    ("REAL", "rules"): POLICY_REAL_REFUSED,
    ("REAL", "operator"): POLICY_OPERATOR_DEMO_ONLY,
}


def _settings(**overrides: Any) -> V6Settings:
    return V6Settings(_env_file=None, **overrides)


# --- truth tables ----------------------------------------------------------------


@pytest.mark.parametrize(("trade_mode", "backend"), sorted(TRUTH_TABLE))
def test_configured_backend_truth_table(trade_mode: str, backend: str) -> None:
    settings = _settings(backend=backend)

    decision = evaluate_account_policy(trade_mode, DEMO_SERVER, LOGIN, settings)

    expected = TRUTH_TABLE[(trade_mode, backend)]
    assert decision.code == expected
    assert decision.allowed is (expected == POLICY_OK)
    assert is_demo_allowed(trade_mode, DEMO_SERVER, LOGIN, settings) is decision.allowed


@pytest.mark.parametrize(("trade_mode", "source"), sorted(TRUTH_TABLE))
@pytest.mark.parametrize("configured", BACKENDS)
def test_intent_source_truth_table_ignores_configured_backend(
        trade_mode: str, source: str, configured: str) -> None:
    decision = check_intent_source(source, trade_mode, DEMO_SERVER, LOGIN,
                                   _settings(backend=configured))

    assert decision.code == TRUTH_TABLE[(trade_mode, source)]


@pytest.mark.parametrize("trade_mode", ["CONTEST", "REAL"])
def test_real_money_flag_cannot_unlock_non_demo_accounts(trade_mode: str) -> None:
    # The validator refuses the flag; model_copy skips validation on purpose.
    settings = _settings().model_copy(update={"allow_real_account": True})

    for backend in BACKENDS:
        decision = evaluate_account_policy(trade_mode, DEMO_SERVER, LOGIN, settings,
                                           source=backend)
        assert not decision.allowed


def test_operator_on_real_names_the_permanent_rule() -> None:
    decision = check_intent_source("operator", "REAL", "Broker-Real", LOGIN, _settings())

    assert decision.code == POLICY_OPERATOR_DEMO_ONLY
    assert "DEMO accounts only" in decision.detail
    assert DEMO_403_CODE == "APP-V6-DEMO-403"


def test_sources_match_the_intent_schema() -> None:
    assert SOURCES == frozenset(INTENT_SOURCES) == frozenset(BACKENDS)
    for removed in ("claude_code", "codex"):
        assert evaluate_account_policy("DEMO", DEMO_SERVER, LOGIN, _settings(),
                                       source=removed).code == POLICY_UNKNOWN_SOURCE


@pytest.mark.parametrize(
    ("agents", "agent", "code"),
    [
        ("claude_code,codex", "claude_code", POLICY_OK),
        ("claude_code,codex", "codex", POLICY_OK),
        ("codex", "claude_code", POLICY_AGENT_NOT_ALLOWED),
        ("claude_code", "gpt", POLICY_AGENT_NOT_ALLOWED),
        ("claude_code", None, POLICY_AGENT_NOT_ALLOWED),
        ("claude_code", "claude_code" * 5, POLICY_AGENT_NOT_ALLOWED),
    ],
)
def test_operator_agent_must_be_enabled(agents: str, agent: Any, code: str) -> None:
    decision = check_operator_agent(agent, _settings(V6_OPERATOR_AGENTS=agents))

    assert (decision.code, decision.allowed) == (code, code == POLICY_OK)
    assert len(decision.detail) < 80


# --- server pattern ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("server", "allowed"),
    [
        (DEMO_SERVER, True),
        ("Exness-Trial5", True),
        ("broker-DEMO-2", True),
        ("Exness-Real7", False),
        ("MetaQuotes-Live", False),
        ("", False),
    ],
)
def test_server_must_match_the_demo_pattern(server: str, allowed: bool) -> None:
    decision = evaluate_account_policy("DEMO", server, LOGIN, _settings())

    assert decision.allowed is allowed
    if not allowed:
        assert decision.code == POLICY_SERVER_NOT_DEMO


def test_custom_server_pattern_is_honoured() -> None:
    settings = _settings(demo_server_pattern=r"^Broker-Demo$")

    assert is_demo_allowed("DEMO", "Broker-Demo", LOGIN, settings)
    assert not is_demo_allowed("DEMO", DEMO_SERVER, LOGIN, settings)


def test_invalid_pattern_or_server_type_fails_closed() -> None:
    broken = _settings().model_copy(update={"demo_server_pattern": "(unclosed"})

    decision = evaluate_account_policy("DEMO", DEMO_SERVER, LOGIN, broken)
    missing = evaluate_account_policy("DEMO", None, LOGIN, _settings())  # type: ignore[arg-type]

    assert (decision.code, decision.detail) == (
        POLICY_SERVER_NOT_DEMO, "V6_DEMO_SERVER_PATTERN is not a valid regex")
    assert (missing.code, missing.detail) == (POLICY_SERVER_NOT_DEMO, "server name is missing")


def test_long_server_name_is_truncated_in_the_detail() -> None:
    decision = evaluate_account_policy("DEMO", "L" * 200, LOGIN, _settings())

    assert not decision.allowed
    assert "L" * 81 not in decision.detail


# --- login allow-list --------------------------------------------------------------


@pytest.mark.parametrize(
    ("login", "allowed"),
    [("12345", True), (" 777 ", True), ("999", False), ("", False), (None, False)],
)
def test_allowed_logins_restrict_accounts(login: Any, allowed: bool) -> None:
    settings = _settings(V6_ALLOWED_LOGINS="12345, 777")

    decision = evaluate_account_policy("DEMO", DEMO_SERVER, login, settings)

    assert decision.allowed is allowed
    assert decision.detail == ("DEMO account on a demo server; login allow-list applied"
                               if allowed else "login is not in V6_ALLOWED_LOGINS")


def test_empty_allow_list_means_no_login_restriction() -> None:
    decision = evaluate_account_policy("DEMO", DEMO_SERVER, "424242", _settings())

    assert decision.allowed
    assert decision.detail.endswith("no login allow-list")


def test_refusal_detail_never_contains_the_login() -> None:
    settings = _settings(V6_ALLOWED_LOGINS="111")

    decision = evaluate_account_policy("DEMO", DEMO_SERVER, "98765432", settings)

    assert decision.code == POLICY_LOGIN_NOT_ALLOWED
    assert "98765432" not in decision.detail


# --- unknown values ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("trade_mode", "source", "code"),
    [
        ("DEMO", "gpt", POLICY_UNKNOWN_SOURCE),
        ("DEMO", "", POLICY_UNKNOWN_SOURCE),
        ("demo", "rules", POLICY_UNKNOWN_TRADE_MODE),
        ("HEDGE" * 20, "rules", POLICY_UNKNOWN_TRADE_MODE),
        (None, "rules", POLICY_UNKNOWN_TRADE_MODE),
    ],
)
def test_unknown_source_or_trade_mode_is_refused(trade_mode: Any, source: str,
                                                 code: str) -> None:
    decision = evaluate_account_policy(trade_mode, DEMO_SERVER, LOGIN, _settings(),
                                       source=source)

    assert (decision.allowed, decision.code) == (False, code)
    assert len(decision.detail) < 60  # untrusted values are truncated


# --- layer 1: settings ---------------------------------------------------------------


EXECUTE = {"backend": "operator", "mode": "execute", "ea_hmac_key": SecretStr(EA_KEY)}


@pytest.mark.parametrize(
    ("update", "code"),
    [
        ({}, POLICY_OK),
        ({"backend": "operator", "mode": "shadow"}, POLICY_OK),
        (EXECUTE, POLICY_OK),
        ({**EXECUTE, "max_lots": 0.005}, POLICY_OK),
        ({"backend": "rules", "mode": "off"}, POLICY_OK),
        ({"backend": "operator", "mode": "off"}, POLICY_OPERATOR_MODE),
        ({"allow_real_account": True}, POLICY_REAL_ACCOUNT_FLAG),
        ({**EXECUTE, "backend": "rules"}, POLICY_EXECUTE_NEEDS_OPERATOR),
        ({**EXECUTE, "ea_hmac_key": SecretStr("")}, POLICY_EXECUTE_NEEDS_KEY),
        ({**EXECUTE, "ea_hmac_key": SecretStr("short")}, POLICY_EXECUTE_NEEDS_KEY),
        ({**EXECUTE, "max_lots": 0.04}, POLICY_EXECUTE_LOT_CAP),
    ],
)
def test_settings_policy(update: dict[str, Any], code: str) -> None:
    settings = _settings().model_copy(update=update)

    decision = check_settings_policy(settings)

    assert decision.code == code
    assert decision.allowed is (code == POLICY_OK)


def test_validated_settings_already_refuse_the_real_money_flag() -> None:
    with pytest.raises(ValueError, match="V6_ALLOW_REAL_ACCOUNT"):
        _settings(allow_real_account=True)


# --- PolicyDecision --------------------------------------------------------------------


def test_policy_decision_invariants() -> None:
    decision = PolicyDecision(allowed=True, code=POLICY_OK)

    with pytest.raises(FrozenInstanceError):
        decision.allowed = False  # type: ignore[misc]
    with pytest.raises(ValueError, match="exactly when"):
        PolicyDecision(allowed=True, code=POLICY_REAL_REFUSED)
    with pytest.raises(ValueError, match="exactly when"):
        PolicyDecision(allowed=False, code=POLICY_OK)
    with pytest.raises(ValueError, match="unknown policy code"):
        PolicyDecision(allowed=False, code="NOPE")
