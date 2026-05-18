"""
Phase 2 — Claude Opus 4.7 decider stub.

NOT implemented in Phase 1 MVP. Will be filled in once the dummy decider has
proven the end-to-end execution path on Exness demo. See docs/setup-mt5-exness.md
and the design notes in knowledge/Rancangan Teknis Bot Trading... for the
target implementation (Anthropic SDK, strict JSON schema, adaptive thinking
only for ambiguous regimes, prompt caching for the static system prompt).
"""

from __future__ import annotations

from ..models import DecisionRequest, DecisionResponse


class ClaudeDecider:
    name = "claude"

    def decide(self, req: DecisionRequest) -> DecisionResponse:
        raise NotImplementedError(
            "ClaudeDecider is not implemented yet. "
            "Set DECIDER=dummy_trend_breakout in .env for Phase 1 MVP."
        )
