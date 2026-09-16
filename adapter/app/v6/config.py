"""
V6 configuration, loaded from `adapter/.env` with the `V6_` prefix.

Every risk-bearing value may only be tightened relative to the ceilings in
`app.v6.risk.limits`. Configurations that would loosen one, run Claude Code
against a real account, or select a backend without its credentials are
rejected at startup rather than logged.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final, Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .market.broker_hours import DEFAULT_QUOTE_GAP_UTC, DailyUtcWindow, parse_daily_window
from .risk import limits

ADAPTER_DIR: Final[Path] = Path(__file__).resolve().parents[2]
MIN_TOKEN_LENGTH: Final[int] = 32
PLACEHOLDER_SECRETS: Final[frozenset[str]] = frozenset(
    {"", "change-me", "change-me-dev-only", "changeme", "your-key-here"}
)

Backend = Literal["rules", "openrouter", "claude_code"]
Mode = Literal["off", "shadow", "execute"]
AccountType = Literal["standard", "raw"]


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _is_placeholder(secret: SecretStr) -> bool:
    return secret.get_secret_value().strip().lower() in PLACEHOLDER_SECRETS


class V6Settings(BaseSettings):
    # hide_input_in_errors: a validation error must never print the raw settings,
    # which hold V6_OPERATOR_TOKEN and OPENROUTER_API_KEY.
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
    halt_file: str = "V6_HALT"
    snapshot_stale_s: int = Field(default=20, ge=5, le=120)
    ea_stale_s: int = Field(default=30, ge=5, le=300)
    max_clock_skew_s: int = Field(default=5, ge=1, le=60)

    # --- account and sizing ------------------------------------------------
    account_type: AccountType = "standard"
    risk_pct: float = Field(default=0.5, gt=0.0)
    sizing_equity_basis_usd: float = Field(default=2000.0, gt=0.0)
    max_lots: float = Field(default=0.01, gt=0.0)
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

    # --- OpenRouter --------------------------------------------------------
    openrouter_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("OPENROUTER_API_KEY", "V6_OPENROUTER_API_KEY"),
    )
    openrouter_desk_models_csv: str = Field(default="", alias="V6_OPENROUTER_DESK_MODELS")
    openrouter_chief_models_csv: str = Field(default="", alias="V6_OPENROUTER_CHIEF_MODELS")
    openrouter_timeout_s: float = Field(default=20.0, gt=0.0, le=60.0)
    daily_llm_budget_usd: float = Field(default=3.0, ge=0.0, le=100.0)

    # --- Claude Code operator ---------------------------------------------
    operator_token: SecretStr = SecretStr("")

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
        return self.time_barrier_bars * 15 * 60

    @property
    def allowed_logins(self) -> tuple[str, ...]:
        return _split_csv(self.allowed_logins_csv)

    @property
    def openrouter_desk_models(self) -> tuple[str, ...]:
        return _split_csv(self.openrouter_desk_models_csv)

    @property
    def openrouter_chief_models(self) -> tuple[str, ...]:
        return _split_csv(self.openrouter_chief_models_csv)

    @property
    def news_rss_urls(self) -> tuple[str, ...]:
        return _split_csv(self.news_rss_urls_csv)

    @property
    def quote_gap(self) -> DailyUtcWindow | None:
        return parse_daily_window(self.broker_quote_gap_utc)

    @property
    def available_backends(self) -> tuple[Backend, ...]:
        found: list[Backend] = ["rules"]
        if not _is_placeholder(self.openrouter_api_key):
            found.append("openrouter")
        if self._operator_token_ok():
            found.append("claude_code")
        return tuple(found)

    def _operator_token_ok(self) -> bool:
        token = self.operator_token.get_secret_value()
        return len(token) >= MIN_TOKEN_LENGTH and not _is_placeholder(self.operator_token)

    # --- validation --------------------------------------------------------
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
             limits.MAX_TIME_BARRIER_S // 900),
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
    def _enforce_backend_credentials(self) -> "V6Settings":
        if not self.enabled:
            return self
        if self.backend == "openrouter":
            if _is_placeholder(self.openrouter_api_key):
                raise ValueError("V6_BACKEND=openrouter requires OPENROUTER_API_KEY.")
            if not self.openrouter_desk_models or not self.openrouter_chief_models:
                raise ValueError(
                    "V6_BACKEND=openrouter requires V6_OPENROUTER_DESK_MODELS and "
                    "V6_OPENROUTER_CHIEF_MODELS (comma-separated model ids)."
                )
        if self.backend == "claude_code" and not self._operator_token_ok():
            raise ValueError(
                f"V6_BACKEND=claude_code requires V6_OPERATOR_TOKEN of at least "
                f"{MIN_TOKEN_LENGTH} random characters."
            )
        return self


def load_v6_settings() -> V6Settings:
    """Build settings from the environment (fresh instance, never cached here)."""
    return V6Settings()
