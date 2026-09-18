"""The minute inbox: one slot, the newest minute wins, a minute id is taken once."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.v6.runtime.ea_state import (
    MINUTE_CYCLE_PREFIX, EaState, MinuteItem, minute_cycle_id_for,
)
from app.v6.schemas.minute import MinuteSnapshot

GOLDEN = Path(__file__).resolve().parent / "golden" / "minute_sample.json"


def minute(bar_open: int = 1789565400) -> MinuteSnapshot:
    document = json.loads(GOLDEN.read_text(encoding="utf-8"))
    document.update(bar_open_epoch=bar_open, snapshot_id=f"Q6M-10000001-{bar_open}",
                    sent_at_epoch=bar_open + 61)
    document["bar"][0] = bar_open
    return MinuteSnapshot.model_validate_json(json.dumps(document))


def item(bar_open: int, at: float) -> MinuteItem:
    snapshot = minute(bar_open)
    return MinuteItem(cycle_id=minute_cycle_id_for(snapshot.snapshot_id), minute=snapshot,
                      received_at=at)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_minute_cycle_ids_are_stable_and_prefixed() -> None:
    first = minute_cycle_id_for("Q6M-10000001-1789565400")
    assert first == minute_cycle_id_for("Q6M-10000001-1789565400")
    assert first.startswith(MINUTE_CYCLE_PREFIX) and len(first) == 18


def test_a_minute_id_is_taken_once() -> None:
    state = EaState()
    assert state.first_minute("Q6M-1-60") is True
    assert state.first_minute("Q6M-1-60") is False
    assert state.first_minute("Q6M-1-120") is True


@pytest.mark.anyio
async def test_the_newest_minute_wins() -> None:
    state = EaState()
    assert state.offer_minute(item(1789565400, 1.0)) is False
    assert state.offer_minute(item(1789565460, 2.0)) is True
    view = state.view()
    assert (view.minutes_superseded, view.minute_pending, view.last_minute_at) == (1, 1, 2.0)
    taken = await asyncio.wait_for(state.next_minute(), timeout=1)
    assert taken.minute.bar_open_epoch == 1789565460
