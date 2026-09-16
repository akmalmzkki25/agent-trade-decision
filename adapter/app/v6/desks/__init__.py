"""
Deterministic desk views (plan section 2, R0): the `rules` baseline.

Each desk is a pure function of the MarketContext (and, for price action and
structure, the offered candidates) returning the same frozen view model an LLM
desk would return. They run on every closed M15 bar, are the fallback when an
LLM desk fails, and are the baseline an LLM has to beat. The rules Chief lives
with the provider in `app.v6.providers.offline`.
"""

from .liquidity import liquidity_view
from .news_risk import news_risk_view
from .price_action import price_action_view
from .structure import structure_view

__all__ = ["liquidity_view", "news_risk_view", "price_action_view", "structure_view"]
