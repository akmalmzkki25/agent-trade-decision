import pytest
from pydantic import ValidationError

from app.models import DecisionRequest

from .fixtures import make_request


def test_make_request_roundtrip_json():
    req = make_request()
    blob = req.model_dump_json()
    again = DecisionRequest.model_validate_json(blob)
    assert again.symbol == "XAUUSD"
    assert again.features.context_tf["adx_strength"] == 0.35


def test_strict_rejects_wrong_types():
    payload = make_request().model_dump()
    payload["market"]["bid"] = "not-a-number"
    with pytest.raises(ValidationError):
        DecisionRequest.model_validate(payload)


def test_unknown_action_rejected():
    from app.models import TradeDecision
    with pytest.raises(ValidationError):
        TradeDecision(
            action="explode",  # type: ignore[arg-type]
            side="flat",
            order_type="none",
            lots=0.0,
            max_deviation_points=10,
            valid_until_utc="2026-05-18T09:00:00+00:00",
            confidence=0.5,
            rationale_short="x",
            reason_codes=["A"],
        )
