"""Agent backends for V6: the deterministic rules baseline and, later, LLM providers."""

from .base import AgentProvider, ProviderResult, ask_safely

__all__ = ["AgentProvider", "ProviderResult", "ask_safely"]
