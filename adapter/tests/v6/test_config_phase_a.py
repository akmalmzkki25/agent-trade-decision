"""Phase A settings: entry hours, trade cap, deadlines, plan windows, session renewal."""

from __future__ import annotations

from typing import Any

import pytest

from app.v6.config import V6Settings
from app.v6.risk import limits


def settings(**overrides: Any) -> V6Settings:
    return V6Settings(_env_file=None, **overrides)


def test_phase_a_defaults() -> None:
    config = settings()
    assert config.entry_hours == "all_day" and config.session_auto_renew is True
    assert (config.max_trades_per_day, config.operator_deadline_s) == (8, 180)
    assert config.time_limit_bounds_s == (3600, 14400)
    assert config.pending_expiry_bounds_s == (900, 3600)


@pytest.mark.parametrize("overrides", [
    {"time_limit_min_minutes": 59},
    {"time_limit_max_minutes": 241},
    {"time_limit_min_minutes": 120, "time_limit_max_minutes": 90},
    {"pending_expiry_min_minutes": 14},
    {"pending_expiry_max_minutes": 61},
    {"pending_expiry_min_minutes": 30, "pending_expiry_max_minutes": 20},
    {"max_trades_per_day": 21},
    {"entry_hours": "asia_only"},
])
def test_plan_windows_may_only_tighten(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        settings(**overrides)


def test_tightened_windows_are_accepted() -> None:
    config = settings(time_limit_min_minutes=90, time_limit_max_minutes=180,
                      pending_expiry_min_minutes=20, pending_expiry_max_minutes=45,
                      entry_hours="london_ny")
    assert config.time_limit_bounds_s == (5400, 10800)
    assert config.pending_expiry_bounds_s == (1200, 2700)


def test_the_deadline_leaves_the_shortest_pending_order_time_to_live() -> None:
    with pytest.raises(ValueError, match="pending expiry"):
        settings(operator_deadline_s=700, intent_ttl_s=300)


def test_phase_a_limits() -> None:
    assert (limits.MIN_TIME_LIMIT_S, limits.MAX_TIME_BARRIER_S) == (3600, 14400)
    assert (limits.MIN_PENDING_EXPIRY_S, limits.MAX_PENDING_EXPIRY_S) == (900, 3600)
    assert (limits.MIN_TP1_R, limits.MODIFY_BUFFER_PRICE) == (0.5, 0.10)
    assert (limits.ACTION_MAX_AGE_S, limits.MAX_TRADES_PER_DAY_CEILING) == (30, 20)
