"""
V6 configuration, loaded from `adapter/.env` with the `V6_` prefix.

Backends:
  rules     the deterministic desks decide; shadow only.
  operator  an operator agent in a chat session (V6_OPERATOR_AGENTS: Claude Code,
            Codex, Antigravity) submits the decision. DEMO accounts only, for every
            agent, whatever else is set.

Every risk-bearing value may only be tightened relative to the ceilings in
`app.v6.risk.limits`. Configurations that would loosen one, execute without the
operator backend, the V6 EA key or the demo lot cap, or select a backend without
its credentials are rejected at startup rather than logged.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Final, Literal, get_args

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .market.broker_hours import DEFAULT_QUOTE_GAP_UTC, DailyUtcWindow, parse_daily_window
from .risk import limits
from .schemas.intent import MIN_PENDING_LIFETIME_S

ADAPTER_DIR: Final[Path] = Path(__file__).resolve().parents[2]
MIN_TOKEN_LENGTH: Final[int] = 32
MIN_EA_KEY_LENGTH: Final[int] = 32
MAX_EA_KEY_LENGTH: Final[int] = 256
# Printable ASCII without whitespace: the HMAC key bytes are identical in Python and MQL5.
EA_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"[\x21-\x7e]+")
PLACEHOLDER_SECRETS: Final[frozenset[str]] = frozenset(
    {"", "change-me", "change-me-dev-only", "changeme", "your-key-here"}
)
PLACEHOLDER_FRAGMENTS: Final[tuple[str, ...]] = (
    "change-me", "changeme", "your-key", "placeholder")
M15_SECONDS: Final[int] = 15 * 60
LEGACY_OPERATOR_BACKEND: Final[str] = "claude_code"

Backend = Literal["rules", "operator"]
# The one list of operator agent names (Claude Code, Codex, Antigravity). The operator
# CLI restates it (scripts/v6ops/context.py AGENTS); tests/v6/test_agent_harness_parity.py
# pins the two, and the agent instructions, together.
OperatorAgent = Literal["claude_code", "codex", "antigravity"]
OPERATOR_AGENTS: Final[tuple[OperatorAgent, ...]] = get_args(OperatorAgent)
DEFAULT_OPERATOR_AGENTS: Final[str] = ",".join(OPERATOR_AGENTS)
Mode = Literal["off", "shadow", "execute"]
AccountType = Literal["standard", "raw"]
# How the EA contract is signed: required (execute), available (a key is set), off.
EaSigning = Literal["required", "available", "off"]


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _is_placeholder(secret: SecretStr) -> bool:
    value = secret.get_secret_value().strip().lower()
    return value in PLACEHOLDER_SECRETS or any(part in value for part in PLACEHOLDER_FRAGMENTS)


def token_ok(secret: SecretStr) -> bool:
    """An operator bearer token: at least MIN_TOKEN_LENGTH characters, not a placeholder."""
    return len(secret.get_secret_value()) >= MIN_TOKEN_LENGTH and not _is_placeholder(secret)


def ea_key_ok(secret: SecretStr) -> bool:
    """A V6 EA HMAC key: 32-256 printable ASCII characters without whitespace, no placeholder."""
    value = secret.get_secret_value()
    return (MIN_EA_KEY_LENGTH <= len(value) <= MAX_EA_KEY_LENGTH
            and EA_KEY_PATTERN.fullmatch(value) is not None and not _is_placeholder(secret))


class V6Settings(BaseSettings):
    # hide_input_in_errors: a validation error must never print the raw settings,
    # which hold V6_OPERATOR_TOKEN and V6_EA_HMAC_KEY.
    model_config = SettingsConfigDict(
        env_prefix="V6_",
        env_file=str(ADAPTER_DIR / ".env"),
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    # --- runtime -----------------------------------------------------------
    enabled: bool = False
    mode: Mode = "shadow"
    backend: Backend = "rules"
    decision_deadline_s: int = Field(default=120, ge=10, le=600)
    operator_deadline_s: int = Field(default=300, ge=30, le=840)
    intent_ttl_s: int = Field(default=120, ge=30, le=300)
    halt_file: str = "V6_HALT"
    snapshot_stale_s: int = Field(default=20, ge=5, le=120)
    ea_stale_s: int = Field(default=30, ge=5, le=300)
    max_clock_skew_s: int = Field(default=5, ge=1, le=60)

    # --- account and sizing ------------------------------------------------
    account_type: AccountType = "standard"
    risk_pct: float = Field(default=0.5, gt=0.0)
    sizing_equity_basis_usd: float = Field(default=5000.0, gt=0.0)
    max_lots: float = Field(default=0.03, gt=0.0)
    max_trades_per_day: int = Field(default=4, ge=1, le=20)
    allow_real_account: bool = False
    demo_server_pattern: str = r"(?i)(trial|demo)"
    allowed_logins_csv: str = Field(default="", alias="V6_ALLOWED_LOGINS")

    # --- gates (may only tighten) -----------------------------------------
    max_spread_points: int | None = None
    stop_floor_points: int = limits.MIN_STOP_FLOOR_POINTS
    min_atr_m5_points: int = limits.MIN_ATR_M5_POINTS
    friction_atr_max: float = limits.MAX_FRICTION_TO_ATR_M5
    notional_ratio_max: float = limits.MAX_NOTIONAL_RATIO
    daily_loss_pct: float = limits.MAX_DAILY_LOSS_PCT
    weekly_loss_pct: float = limits.MAX_WEEKLY_LOSS_PCT
    monthly_loss_pct: float = limits.MAX_MONTHLY_LOSS_PCT

    # --- exits -------------------------------------------------------------
    time_barrier_bars: int = Field(default=8, ge=1)
    pending_expiry_bars: int = Field(default=2, ge=1, le=8)
    tp_r_multiple: float = Field(default=2.0, ge=1.0, le=5.0)

    # --- deliberation ------------------------------------------------------
    pa_min_conviction: float = Field(default=0.6, ge=0.0, le=1.0)
    debate_rounds: int = Field(default=1, ge=0, le=1)
    structure_veto: Literal["log", "enforce"] = "log"

    # --- execution (EA contract, docs/v6-wire-contract.md) ---------------------
    magic: int = Field(default=limits.V6_MAGIC_FIRST, ge=limits.V6_MAGIC_FIRST,
                       le=limits.V6_MAGIC_LAST)
    max_drift_points: int = Field(default=200, ge=10, le=1000)
    ea_hmac_key: SecretStr = Field(default=SecretStr(""), alias="V6_EA_HMAC_KEY")

    # --- operator agents -------------------------------------------------------
    operator_token: SecretStr = SecretStr("")
    operator_agents_csv: str = Field(default=DEFAULT_OPERATOR_AGENTS, alias="V6_OPERATOR_AGENTS")

    # --- news --------------------------------------------------------------
    news_rss_urls_csv: str = Field(default="", alias="V6_NEWS_RSS_URLS")

    # --- broker facts -------------------------------------------------------
    # "HH:MM-HH:MM" UTC without quotes every day ("" for none); market.broker_hours.
    broker_quote_gap_utc: str = DEFAULT_QUOTE_GAP_UTC

    # --- derived views -----------------------------------------------------
    @property
    def effective_max_spread_points(self) -> int:
        ceiling = limits.MAX_SPREAD_POINTS[self.account_type]
        return ceiling if self.max_spread_points is None else self.max_spread_points

    @property
    def friction_price(self) -> float:
        return limits.FRICTION_PRICE[self.account_type]

    @property
    def time_barrier_s(self) -> int:
        return self.time_barrier_bars * M15_SECONDS

    @property
    def pending_expiry_s(self) -> int:
        """Lifetime of a pending order, counted from the decision bar's close."""
        return self.pending_expiry_bars * M15_SECONDS

    @property
    def allowed_logins(self) -> tuple[str, ...]:
        return _split_csv(self.allowed_logins_csv)

    @property
    def operator_agents(self) -> tuple[str, ...]:
        """Agents allowed to submit operator decisions, in configured order."""
        return _split_csv(self.operator_agents_csv)

    @property
    def news_rss_urls(self) -> tuple[str, ...]:
        return _split_csv(self.news_rss_urls_csv)

    @property
    def quote_gap(self) -> DailyUtcWindow | None:
        return parse_daily_window(self.broker_quote_gap_utc)

    @property
    def operator_token_ok(self) -> bool:
        return token_ok(self.operator_token)

    @property
    def ea_hmac_key_ok(self) -> bool:
        return ea_key_ok(self.ea_hmac_key)

    @property
    def ea_signing(self) -> EaSigning:
        if self.mode == "execute":
            return "required"
        return "available" if self.ea_hmac_key_ok else "off"

    # --- validation --------------------------------------------------------
    @field_validator("backend", mode="before")
    @classmethod
    def _renamed_backend(cls, value: object) -> object:
        if value == LEGACY_OPERATOR_BACKEND:
            raise ValueError("V6_BACKEND=claude_code was renamed: set V6_BACKEND=operator and "
                             "name the agents in V6_OPERATOR_AGENTS.")
        return value

    @model_validator(mode="after")
    def _only_tighten(self) -> "V6Settings":
        checks = (
            (self.risk_pct <= limits.MAX_RISK_PCT_CEILING, "V6_RISK_PCT", limits.MAX_RISK_PCT_CEILING),
            (self.stop_floor_points >= limits.MIN_STOP_FLOOR_POINTS, "V6_STOP_FLOOR_POINTS",
             limits.MIN_STOP_FLOOR_POINTS),
            (self.min_atr_m5_points >= limits.MIN_ATR_M5_POINTS, "V6_MIN_ATR_M5_POINTS",
             limits.MIN_ATR_M5_POINTS),
            (0 < self.friction_atr_max <= limits.MAX_FRICTION_TO_ATR_M5, "V6_FRICTION_ATR_MAX",
             limits.MAX_FRICTION_TO_ATR_M5),
            (0 < self.notional_ratio_max <= limits.MAX_NOTIONAL_RATIO, "V6_NOTIONAL_RATIO_MAX",
             limits.MAX_NOTIONAL_RATIO),
            (0 < self.daily_loss_pct <= limits.MAX_DAILY_LOSS_PCT, "V6_DAILY_LOSS_PCT",
             limits.MAX_DAILY_LOSS_PCT),
            (0 < self.weekly_loss_pct <= limits.MAX_WEEKLY_LOSS_PCT, "V6_WEEKLY_LOSS_PCT",
             limits.MAX_WEEKLY_LOSS_PCT),
            (0 < self.monthly_loss_pct <= limits.MAX_MONTHLY_LOSS_PCT, "V6_MONTHLY_LOSS_PCT",
             limits.MAX_MONTHLY_LOSS_PCT),
            (self.time_barrier_s <= limits.MAX_TIME_BARRIER_S, "V6_TIME_BARRIER_BARS",
             limits.MAX_TIME_BARRIER_S // M15_SECONDS),
        )
        for ok, name, bound in checks:
            if not ok:
                raise ValueError(f"{name} may only tighten the built-in limit ({bound}).")
        ceiling = limits.MAX_SPREAD_POINTS[self.account_type]
        if self.max_spread_points is not None and not 0 < self.max_spread_points <= ceiling:
            raise ValueError(f"V6_MAX_SPREAD_POINTS must be in (0, {ceiling}] for {self.account_type}.")
        return self

    @model_validator(mode="after")
    def _enforce_account_policy(self) -> "V6Settings":
        if self.allow_real_account:
            raise ValueError(
                "V6_ALLOW_REAL_ACCOUNT=true is refused: V6.0 trades demo accounts only "
                "until the knowledge/15 evaluation standard is met."
            )
        try:
            re.compile(self.demo_server_pattern)
        except re.error as exc:
            raise ValueError(f"V6_DEMO_SERVER_PATTERN is not a valid regex: {exc}") from exc
        try:
            parse_daily_window(self.broker_quote_gap_utc)
        except ValueError as exc:
            raise ValueError(f"V6_BROKER_QUOTE_GAP_UTC: {exc}") from exc
        return self

    @model_validator(mode="after")
    def _check_operator_agents(self) -> "V6Settings":
        agents = self.operator_agents
        unknown = [agent for agent in agents if agent not in OPERATOR_AGENTS]
        if not agents or unknown or len(set(agents)) != len(agents):
            raise ValueError(f"V6_OPERATOR_AGENTS must list distinct agents out of "
                             f"{', '.join(OPERATOR_AGENTS)}.")
        return self

    @model_validator(mode="after")
    def _check_intent_timing(self) -> "V6Settings":
        """A decision may arrive V6_OPERATOR_DEADLINE_S after the close and its intent stays
        valid for V6_INTENT_TTL_S; the pending order must still have time to live then."""
        latest_valid_until = self.operator_deadline_s + self.intent_ttl_s + MIN_PENDING_LIFETIME_S
        if latest_valid_until > self.pending_expiry_s:
            raise ValueError(
                f"V6_OPERATOR_DEADLINE_S + V6_INTENT_TTL_S + {MIN_PENDING_LIFETIME_S} s must not "
                f"exceed the pending expiry (V6_PENDING_EXPIRY_BARS x {M15_SECONDS} s).")
        return self

    @model_validator(mode="after")
    def _enforce_backend_credentials(self) -> "V6Settings":
        if not self.enabled:
            return self
        if self.backend == "operator" and not self.operator_token_ok:
            raise ValueError(
                f"V6_BACKEND=operator requires V6_OPERATOR_TOKEN of at least "
                f"{MIN_TOKEN_LENGTH} random characters."
            )
        if self.ea_hmac_key.get_secret_value() and not self.ea_hmac_key_ok:
            raise ValueError(
                f"V6_EA_HMAC_KEY must be {MIN_EA_KEY_LENGTH}-{MAX_EA_KEY_LENGTH} printable "
                f"ASCII characters without spaces, and not a placeholder.")
        if self.mode == "execute":
            self._check_execute()
        return self

    def _check_execute(self) -> None:
        if self.backend != "operator":
            raise ValueError("V6_MODE=execute requires V6_BACKEND=operator.")
        if not self.ea_hmac_key_ok:
            raise ValueError("V6_MODE=execute requires V6_EA_HMAC_KEY "
                             "(see docs/v6-wire-contract.md).")
        if Decimal(repr(self.max_lots)) > Decimal(repr(limits.MAX_EXECUTE_LOTS)):
            raise ValueError(f"V6_MODE=execute requires V6_MAX_LOTS <= {limits.MAX_EXECUTE_LOTS} "
                             f"(it may only tighten).")


def load_v6_settings() -> V6Settings:
    """Build settings from the environment (fresh instance, never cached here)."""
    return V6Settings()
