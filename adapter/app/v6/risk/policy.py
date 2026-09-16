"""
Demo-only account policy for V6.0 (plan section 3.3 layers 1-3, section 9).

V6.0 never trades money. Every backend is limited to DEMO accounts on a server
whose name matches V6_DEMO_SERVER_PATTERN, optionally narrowed to
V6_ALLOWED_LOGINS (an empty list means no login restriction). REAL and CONTEST
accounts are refused whatever the configuration says: V6_ALLOW_REAL_ACCOUNT
cannot unlock anything here, and the settings validator refuses it anyway.

Separately from that V6.0 rule, the `claude_code` source is permanently limited
to DEMO accounts. A later release that unlocks real money for the rules or
OpenRouter backends still cannot let a chat session decide a real trade, so that
check runs first and names its own code.

One function serves all three layers:
  1. settings     - `check_settings_policy` (defence in depth behind config.py)
  2. operator     - `evaluate_account_policy`; the route answers 403 DEMO_403_CODE
  3. intents      - `check_intent_source` for the source that built the intent
The ACCOUNT_POLICY gate calls `evaluate_account_policy` with the configured backend.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from ..config import V6Settings

DEMO_TRADE_MODE: Final[str] = "DEMO"
CONTEST_TRADE_MODE: Final[str] = "CONTEST"
REAL_TRADE_MODE: Final[str] = "REAL"
TRADE_MODES: Final[frozenset[str]] = frozenset(
    {DEMO_TRADE_MODE, CONTEST_TRADE_MODE, REAL_TRADE_MODE})
CLAUDE_CODE_SOURCE: Final[str] = "claude_code"
# Same spelling as config.Backend and schemas.intent.Source.
SOURCES: Final[frozenset[str]] = frozenset({"rules", "openrouter", CLAUDE_CODE_SOURCE})
CLAUDE_CODE_MODES: Final[frozenset[str]] = frozenset({"shadow", "execute"})
# Error code the operator route returns with HTTP 403 (plan section 3.3, layer 2).
DEMO_403_CODE: Final[str] = "APP-V6-DEMO-403"
MAX_ECHO_CHARS: Final[int] = 16
MAX_SERVER_ECHO_CHARS: Final[int] = 80

POLICY_OK: Final[str] = "POLICY_OK"
POLICY_UNKNOWN_SOURCE: Final[str] = "POLICY_UNKNOWN_SOURCE"
POLICY_UNKNOWN_TRADE_MODE: Final[str] = "POLICY_UNKNOWN_TRADE_MODE"
POLICY_CLAUDE_CODE_DEMO_ONLY: Final[str] = "POLICY_CLAUDE_CODE_DEMO_ONLY"
POLICY_REAL_REFUSED: Final[str] = "POLICY_REAL_REFUSED"
POLICY_CONTEST_REFUSED: Final[str] = "POLICY_CONTEST_REFUSED"
POLICY_SERVER_NOT_DEMO: Final[str] = "POLICY_SERVER_NOT_DEMO"
POLICY_LOGIN_NOT_ALLOWED: Final[str] = "POLICY_LOGIN_NOT_ALLOWED"
POLICY_REAL_ACCOUNT_FLAG: Final[str] = "POLICY_REAL_ACCOUNT_FLAG"
POLICY_CLAUDE_CODE_MODE: Final[str] = "POLICY_CLAUDE_CODE_MODE"
POLICY_CODES: Final[frozenset[str]] = frozenset({
    POLICY_OK, POLICY_UNKNOWN_SOURCE, POLICY_UNKNOWN_TRADE_MODE, POLICY_CLAUDE_CODE_DEMO_ONLY,
    POLICY_REAL_REFUSED, POLICY_CONTEST_REFUSED, POLICY_SERVER_NOT_DEMO,
    POLICY_LOGIN_NOT_ALLOWED, POLICY_REAL_ACCOUNT_FLAG, POLICY_CLAUDE_CODE_MODE,
})


@dataclass(frozen=True)
class PolicyDecision:
    """`allowed` is True exactly when `code` is POLICY_OK. `detail` never holds the login."""

    allowed: bool
    code: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.code not in POLICY_CODES:
            raise ValueError(f"unknown policy code {self.code[:40]!r}")
        if self.allowed != (self.code == POLICY_OK):
            raise ValueError("a decision is allowed exactly when its code is POLICY_OK")


@dataclass(frozen=True)
class _Facts:
    """The account as the EA reported it (untrusted), plus the deciding source."""

    trade_mode: object
    server: object
    login: object
    source: object


_Check = Callable[[_Facts, V6Settings], PolicyDecision | None]


def _refuse(code: str, detail: str) -> PolicyDecision:
    return PolicyDecision(allowed=False, code=code, detail=detail)


def _echo(value: object, limit: int = MAX_ECHO_CHARS) -> str:
    return repr(str(value)[:limit])


def _source_known(facts: _Facts, settings: V6Settings) -> PolicyDecision | None:
    if facts.source in SOURCES:
        return None
    return _refuse(POLICY_UNKNOWN_SOURCE, f"unknown decision source {_echo(facts.source)}")


def _trade_mode_known(facts: _Facts, settings: V6Settings) -> PolicyDecision | None:
    if facts.trade_mode in TRADE_MODES:
        return None
    return _refuse(POLICY_UNKNOWN_TRADE_MODE, f"unknown trade mode {_echo(facts.trade_mode)}")


def _claude_code_demo_only(facts: _Facts, settings: V6Settings) -> PolicyDecision | None:
    if facts.source != CLAUDE_CODE_SOURCE or facts.trade_mode == DEMO_TRADE_MODE:
        return None
    return _refuse(POLICY_CLAUDE_CODE_DEMO_ONLY,
                   f"claude_code decides for DEMO accounts only, not {facts.trade_mode}")


def _real_refused(facts: _Facts, settings: V6Settings) -> PolicyDecision | None:
    if facts.trade_mode != REAL_TRADE_MODE:
        return None
    return _refuse(POLICY_REAL_REFUSED, "REAL accounts are refused in V6.0")


def _contest_refused(facts: _Facts, settings: V6Settings) -> PolicyDecision | None:
    if facts.trade_mode != CONTEST_TRADE_MODE:
        return None
    return _refuse(POLICY_CONTEST_REFUSED, "CONTEST accounts are refused in V6.0")


def _server_is_demo(facts: _Facts, settings: V6Settings) -> PolicyDecision | None:
    server = facts.server
    if not isinstance(server, str) or not server:
        return _refuse(POLICY_SERVER_NOT_DEMO, "server name is missing")
    try:
        matched = re.search(settings.demo_server_pattern, server) is not None
    except re.error:
        return _refuse(POLICY_SERVER_NOT_DEMO, "V6_DEMO_SERVER_PATTERN is not a valid regex")
    if matched:
        return None
    return _refuse(POLICY_SERVER_NOT_DEMO,
                   f"server {_echo(server, MAX_SERVER_ECHO_CHARS)} does not match "
                   "V6_DEMO_SERVER_PATTERN")


def _login_allowed(facts: _Facts, settings: V6Settings) -> PolicyDecision | None:
    allowed = settings.allowed_logins
    if not allowed:
        return None
    login = facts.login.strip() if isinstance(facts.login, str) else ""
    if login and login in allowed:
        return None
    return _refuse(POLICY_LOGIN_NOT_ALLOWED, "login is not in V6_ALLOWED_LOGINS")


# Order matters: the permanent claude_code rule is reported before the V6.0 rules.
_ACCOUNT_CHECKS: Final[tuple[_Check, ...]] = (
    _source_known, _trade_mode_known, _claude_code_demo_only, _real_refused,
    _contest_refused, _server_is_demo, _login_allowed,
)


def evaluate_account_policy(trade_mode: str, server: str, login: str, settings: V6Settings,
                            source: str | None = None) -> PolicyDecision:
    """May `source` (default: the configured backend) decide for this account?

    Checks, first refusal wins: known source, known trade mode, claude_code on
    DEMO only, no REAL, no CONTEST, server matches V6_DEMO_SERVER_PATTERN (regex
    search), login in V6_ALLOWED_LOGINS when that list is set.
    """
    facts = _Facts(trade_mode=trade_mode, server=server, login=login,
                   source=settings.backend if source is None else source)
    for check in _ACCOUNT_CHECKS:
        refusal = check(facts, settings)
        if refusal is not None:
            return refusal
    restriction = "login allow-list applied" if settings.allowed_logins else "no login allow-list"
    return PolicyDecision(allowed=True, code=POLICY_OK,
                          detail=f"DEMO account on a demo server; {restriction}")


def is_demo_allowed(trade_mode: str, server: str, login: str, settings: V6Settings) -> bool:
    """True when the configured backend may act on this account (see evaluate_account_policy)."""
    return evaluate_account_policy(trade_mode, server, login, settings).allowed


def check_intent_source(source: str, trade_mode: str, server: str, login: str,
                        settings: V6Settings) -> PolicyDecision:
    """Layer 3: may an intent decided by `source` be built for this account?"""
    return evaluate_account_policy(trade_mode, server, login, settings, source=source)


def check_settings_policy(settings: V6Settings) -> PolicyDecision:
    """Layer 1, behind config.py's validators (settings built with model_construct skip those).

    Refuses V6_ALLOW_REAL_ACCOUNT=true, and the claude_code backend outside the
    shadow and execute modes.
    """
    if settings.allow_real_account:
        return _refuse(POLICY_REAL_ACCOUNT_FLAG, "V6_ALLOW_REAL_ACCOUNT=true is refused in V6.0")
    if settings.backend == CLAUDE_CODE_SOURCE and settings.mode not in CLAUDE_CODE_MODES:
        return _refuse(POLICY_CLAUDE_CODE_MODE,
                       f"the claude_code backend needs mode shadow or execute, not "
                       f"{_echo(settings.mode)}")
    return PolicyDecision(allowed=True, code=POLICY_OK, detail="settings respect the demo policy")
