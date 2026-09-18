"""The minute snapshot v6.minute.1 (spec section 3.1)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest
from pydantic import ValidationError

from app.v6.schemas.minute import MINUTE_SCHEMA, MinuteSnapshot, minute_snapshot_id
from app.v6.types import Bar

GOLDEN: Final[Path] = Path(__file__).resolve().parent / "golden" / "minute_sample.json"


def sample(**changes: Any) -> dict[str, Any]:
    return {**json.loads(GOLDEN.read_text(encoding="utf-8")), **changes}


def parse(document: dict[str, Any]) -> MinuteSnapshot:
    return MinuteSnapshot.model_validate_json(json.dumps(document))


def test_the_golden_sample_validates() -> None:
    minute = parse(sample())
    assert minute.schema_version == MINUTE_SCHEMA
    assert minute.bar_close_epoch == 1789565460
    assert minute.to_bar() == Bar(t=1789565400, o=4535.2, h=4536.05, l=4534.9, c=4535.84,
                                  tv=212, spr=18)


def test_the_golden_sample_names_every_field() -> None:
    assert set(sample()) == set(MinuteSnapshot.model_fields)


def test_the_id_names_the_login_and_the_bar() -> None:
    assert minute_snapshot_id("10000001", 1789565400) == "Q6M-10000001-1789565400"


@pytest.mark.parametrize("changes", [
    {"snapshot_id": "Q6M-10000001-1789565460"},                   # another bar
    {"bar_open_epoch": 1789565430, "snapshot_id": "Q6M-10000001-1789565430",
     "bar": [1789565430, 4535.2, 4536.05, 4534.9, 4535.84, 212, 18]},   # off the minute grid
    {"bar": [1789565340, 4535.2, 4536.05, 4534.9, 4535.84, 212, 18]},  # another minute
    {"bar": [1789565400, 4535.2, 4534.0, 4534.9, 4535.84, 212, 18]},   # high below open
    {"sent_at_epoch": 1789565399},                                  # sent before the bar
    {"schema_version": "v6.minute.2"},
])
def test_inconsistent_minutes_are_refused(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        parse(sample(**changes))


def test_unknown_fields_are_refused() -> None:
    with pytest.raises(ValidationError):
        parse(sample(extra=1))
