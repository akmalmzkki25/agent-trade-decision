"""The SESSION gate refuses orders that could rest into the broker quote gap."""

from __future__ import annotations

from .test_gates import AS_OF, _evaluate, _gate, _settings


def test_session_gate_blocks_an_order_that_could_rest_into_the_quote_gap() -> None:
    """With the gap at the bar close, a 30-minute order would rest in it: SESSION fails."""
    close = AS_OF
    minute = close % 86_400 // 60
    gap = f"{minute // 60:02d}:{minute % 60:02d}-{(minute // 60 + 1) % 24:02d}:{minute % 60:02d}"
    gates = _evaluate(settings=_settings(broker_quote_gap_utc=gap))

    gate = _gate(gates, "SESSION")
    assert (gate.passed, gate.value) == (False, "QUOTE_GAP")
    assert "quality=" in gate.detail


def test_session_gate_ignores_a_quote_gap_far_away() -> None:
    minute = (AS_OF % 86_400 // 60 + 180) % 1440
    gap = f"{minute // 60:02d}:{minute % 60:02d}-{(minute // 60 + 1) % 24:02d}:{minute % 60:02d}"
    assert _gate(_evaluate(settings=_settings(broker_quote_gap_utc=gap)), "SESSION").passed
