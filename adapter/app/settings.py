"""Adapter configuration, loaded from environment / .env."""

from __future__ import annotations

import ipaddress
from typing import Final

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_HMAC_KEY: Final[str] = "change-me-dev-only"
# Binds that name no host a client could send (every interface).
WILDCARD_BIND_HOSTS: Final[frozenset[str]] = frozenset({"", "0.0.0.0", "::"})  # nosec B104
HOST_WILDCARD_MARK: Final[str] = "*"


def split_hosts(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _is_loopback(host: str) -> bool:
    """True when the bind address can only be reached from this machine."""
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class Settings(BaseSettings):
    # hide_input_in_errors: a validation error must never print the raw settings,
    # which hold INTERNAL_HMAC_KEY and ANTHROPIC_API_KEY.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore",
                                      hide_input_in_errors=True)

    adapter_host: str = "127.0.0.1"
    adapter_port: int = 8765
    # Extra Host header names accepted besides loopback (comma-separated), e.g. the
    # LAN name remote EAs use. Required when ADAPTER_HOST binds every interface.
    allowed_hosts: str = ""

    db_path: str = "./trade_ledger.db"
    replay_dir: str = "./replay_tapes"

    # Secrets are SecretStr so a repr, a dump or a logged Settings never shows
    # them; read the value only where it is used, via get_secret_value().
    internal_hmac_key: SecretStr = SecretStr(PLACEHOLDER_HMAC_KEY)
    hmac_required: bool = False

    # Largest request body accepted before parsing (payloads are ~2-4 KB).
    max_request_bytes: int = 256 * 1024

    # FastAPI's interactive docs map every trading endpoint; off unless asked for.
    enable_docs: bool = False

    decider: str = "dummy_trend_breakout"
    adapter_default_max_deviation_points: int = 20

    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = "claude-opus-4-7"
    anthropic_timeout_seconds: float = 1.6

    @property
    def extra_allowed_hosts(self) -> tuple[str, ...]:
        return split_hosts(self.allowed_hosts)

    @model_validator(mode="after")
    def _enforce_host_allowlist(self) -> "Settings":
        """Only listed Host headers are served (app.host_guard), so say which ones."""
        extra = self.extra_allowed_hosts
        if any(HOST_WILDCARD_MARK in host for host in extra):
            raise ValueError("ALLOWED_HOSTS must list explicit host names, not wildcards.")
        if self.adapter_host in WILDCARD_BIND_HOSTS and not extra:
            raise ValueError(
                f"ADAPTER_HOST={self.adapter_host!r} binds every interface, but only loopback "
                "Host headers are accepted. Set ALLOWED_HOSTS to the host names or addresses "
                "remote clients use (comma-separated), or bind to 127.0.0.1."
            )
        return self

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
        hmac_key = self.internal_hmac_key.get_secret_value().strip()
        if self.hmac_required and hmac_key in ("", PLACEHOLDER_HMAC_KEY):
            raise ValueError(
                "HMAC_REQUIRED=true but INTERNAL_HMAC_KEY is unset or still the "
                "published placeholder. Set a strong random secret."
            )
        return self


settings = Settings()
