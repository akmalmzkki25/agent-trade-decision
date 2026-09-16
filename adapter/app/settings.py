"""Adapter configuration, loaded from environment / .env."""

from __future__ import annotations

import ipaddress
from typing import Final

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_HMAC_KEY: Final[str] = "change-me-dev-only"


def _is_loopback(host: str) -> bool:
    """True when the bind address can only be reached from this machine."""
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    adapter_host: str = "127.0.0.1"
    adapter_port: int = 8765

    db_path: str = "./trade_ledger.db"
    replay_dir: str = "./replay_tapes"

    internal_hmac_key: str = PLACEHOLDER_HMAC_KEY
    hmac_required: bool = False

    # Largest request body accepted before parsing (payloads are ~2-4 KB).
    max_request_bytes: int = 256 * 1024

    # FastAPI's interactive docs map every trading endpoint; off unless asked for.
    enable_docs: bool = False

    decider: str = "dummy_trend_breakout"
    adapter_default_max_deviation_points: int = 20

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-7"
    anthropic_timeout_seconds: float = 1.6

    @model_validator(mode="after")
    def _enforce_auth_posture(self) -> "Settings":
        """
        Refuse configurations that would expose unauthenticated trading endpoints.

        Binding beyond loopback puts /v1/decision, /v2..v5 and the event intake
        on the network. Without HMAC anyone who can reach the port can write to
        the ledger or trigger decision work, so that combination is rejected at
        startup rather than merely logged.
        """
        if not _is_loopback(self.adapter_host) and not self.hmac_required:
            raise ValueError(
                f"adapter_host={self.adapter_host!r} is not loopback, so HMAC_REQUIRED "
                "must be true. Either bind to 127.0.0.1 or set HMAC_REQUIRED=true "
                "together with a strong INTERNAL_HMAC_KEY."
            )
        if self.hmac_required and self.internal_hmac_key.strip() in ("", PLACEHOLDER_HMAC_KEY):
            raise ValueError(
                "HMAC_REQUIRED=true but INTERNAL_HMAC_KEY is unset or still the "
                "published placeholder. Set a strong random secret."
            )
        return self


settings = Settings()
