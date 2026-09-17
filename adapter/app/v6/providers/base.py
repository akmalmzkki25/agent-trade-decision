"""
The agent-provider contract (plan section 3).

A provider turns one role packet into one validated view. Every failure is
reported as an `error_code` on the result; a provider must never raise into the
orchestrator. `ask_safely` enforces that at the call site as well, so a buggy
provider degrades to a HOLD instead of killing the cycle.

Backends: `rules` (OfflineProvider, always the R0 baseline) and `operator` (an
operator agent's submitted decision; its views are recorded with source
OPERATOR_PROVIDER_NAME and the agent name in the view record's `model`).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

import anyio

from ..clock import Clock
from ..schemas.agents import AgentRole, AgentView

logger = logging.getLogger(__name__)

# --- ProviderResult.error_code values ("" means success) -------------------
ERR_NONE: Final[str] = ""
ERR_TIMEOUT: Final[str] = "PROVIDER_TIMEOUT"
ERR_DEADLINE_PASSED: Final[str] = "PROVIDER_DEADLINE_PASSED"
ERR_INVALID_OUTPUT: Final[str] = "PROVIDER_INVALID_OUTPUT"
ERR_OUTPUT_TOO_LARGE: Final[str] = "PROVIDER_OUTPUT_TOO_LARGE"
ERR_UNKNOWN_ID: Final[str] = "PROVIDER_UNKNOWN_ID"
ERR_EMPTY: Final[str] = "PROVIDER_EMPTY"
ERR_BAD_REQUEST: Final[str] = "PROVIDER_BAD_REQUEST"
ERR_UNAVAILABLE: Final[str] = "PROVIDER_UNAVAILABLE"      # no decision channel (no operator)
ERR_UNSUPPORTED_ROLE: Final[str] = "PROVIDER_UNSUPPORTED_ROLE"
ERR_DISABLED: Final[str] = "PROVIDER_DISABLED"
ERR_INTERNAL: Final[str] = "PROVIDER_INTERNAL"
ERROR_CODES: Final[frozenset[str]] = frozenset({
    ERR_TIMEOUT, ERR_DEADLINE_PASSED, ERR_INVALID_OUTPUT, ERR_OUTPUT_TOO_LARGE,
    ERR_UNKNOWN_ID, ERR_EMPTY, ERR_BAD_REQUEST, ERR_UNAVAILABLE, ERR_UNSUPPORTED_ROLE,
    ERR_DISABLED, ERR_INTERNAL,
})

# --- CycleResult.provider_status values -------------------------------------
PROVIDER_STATUS_OK: Final[str] = "ok"
PROVIDER_STATUS_PARTIAL: Final[str] = "partial"          # some roles fell back to rules
PROVIDER_STATUS_FAILED: Final[str] = "failed"            # PA or Chief unusable
PROVIDER_STATUS_SKIPPED: Final[str] = "skipped"          # tier 0 held; no provider call
PROVIDER_STATUSES: Final[frozenset[str]] = frozenset({
    PROVIDER_STATUS_OK, PROVIDER_STATUS_PARTIAL, PROVIDER_STATUS_FAILED,
    PROVIDER_STATUS_SKIPPED,
})

RULES_PROVIDER_NAME: Final[str] = "rules"
OPERATOR_PROVIDER_NAME: Final[str] = "operator"
MS_PER_SECOND: Final[int] = 1000


@dataclass(frozen=True)
class ProviderResult:
    """Exactly one of `view` / `error_code` is set."""

    view: AgentView | None
    error_code: str = ERR_NONE
    model: str = ""
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0

    def __post_init__(self) -> None:
        if (self.view is None) == (self.error_code == ERR_NONE):
            raise ValueError("ProviderResult needs exactly one of view or error_code")
        if self.error_code and self.error_code not in ERROR_CODES:
            raise ValueError(f"unknown provider error code {self.error_code[:40]!r}")
        counters = (self.latency_ms, self.tokens_in, self.tokens_out)
        if min(counters) < 0 or not math.isfinite(self.cost_usd) or self.cost_usd < 0:
            raise ValueError("latency, tokens and cost must be finite and >= 0")

    @property
    def ok(self) -> bool:
        return self.view is not None

    @classmethod
    def success(cls, view: AgentView, *, model: str = "", latency_ms: int = 0,
                tokens_in: int = 0, tokens_out: int = 0,
                cost_usd: float = 0.0) -> "ProviderResult":
        return cls(view=view, model=model, latency_ms=latency_ms, tokens_in=tokens_in,
                   tokens_out=tokens_out, cost_usd=cost_usd)

    @classmethod
    def failure(cls, error_code: str, *, model: str = "", latency_ms: int = 0,
                tokens_in: int = 0, tokens_out: int = 0,
                cost_usd: float = 0.0) -> "ProviderResult":
        return cls(view=None, error_code=error_code, model=model, latency_ms=latency_ms,
                   tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost_usd)


@runtime_checkable
class AgentProvider(Protocol):
    """One backend. `name` is what `v6_agent_views.source` records."""

    name: str

    async def ask(self, role: AgentRole, packet: Mapping[str, object], schema_name: str,
                  deadline_epoch: float) -> ProviderResult:
        """Return the role's validated view, or a failure result. Never raises."""
        ...


def _elapsed_ms(clock: Clock, started: float) -> int:
    return max(0, int((clock.now_epoch() - started) * MS_PER_SECOND))


async def ask_safely(provider: AgentProvider, role: AgentRole, packet: Mapping[str, object],
                     schema_name: str, deadline_epoch: float, clock: Clock) -> ProviderResult:
    """Call `provider.ask` with the deadline enforced and every exception contained.

    The remaining time is measured on `clock`; a deadline already past returns
    ERR_DEADLINE_PASSED without calling the provider.
    """
    started = clock.now_epoch()
    remaining = deadline_epoch - started
    if not remaining > 0:
        return ProviderResult.failure(ERR_DEADLINE_PASSED)
    try:
        with anyio.fail_after(remaining):
            result = await provider.ask(role, packet, schema_name, deadline_epoch)
    except TimeoutError:
        return ProviderResult.failure(ERR_TIMEOUT, latency_ms=_elapsed_ms(clock, started))
    except Exception as exc:  # noqa: BLE001 - the contract is "never raise"
        logger.error("v6 provider %s raised %s on role %s", provider.name,
                     type(exc).__name__, role)
        return ProviderResult.failure(ERR_INTERNAL, latency_ms=_elapsed_ms(clock, started))
    if not isinstance(result, ProviderResult):
        logger.error("v6 provider %s returned %s on role %s", provider.name,
                     type(result).__name__, role)
        return ProviderResult.failure(ERR_INTERNAL, latency_ms=_elapsed_ms(clock, started))
    return result
