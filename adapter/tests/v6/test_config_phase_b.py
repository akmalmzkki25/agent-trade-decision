"""Phase B settings: minute packets, their deadline and the minute staleness."""

from __future__ import annotations

from typing import Any

import pytest

from app.v6.config import V6Settings


def settings(**overrides: Any) -> V6Settings:
    return V6Settings(_env_file=None, **overrides)


def test_phase_b_defaults() -> None:
    config = settings()
    assert config.minute_packets is True
    assert (config.m1_deadline_s, config.minute_stale_s) == (50, 10)


@pytest.mark.parametrize("overrides", [
    {"m1_deadline_s": 19}, {"m1_deadline_s": 56},
    {"minute_stale_s": 2}, {"minute_stale_s": 31},
])
def test_minute_windows_stay_in_bounds(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        settings(**overrides)


def test_minute_packets_can_be_switched_off() -> None:
    config = settings(minute_packets=False, m1_deadline_s=40, minute_stale_s=5)
    assert (config.minute_packets, config.m1_deadline_s, config.minute_stale_s) == (False, 40, 5)
