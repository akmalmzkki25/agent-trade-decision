"""
Post-release calendar windows: unknown actuals and the decision bar's own window.

NFP on Friday 2026-10-02 is released at 08:30 New York (12:30 UTC); the static
table holds the same release. The EA used to list only events at or after its
send time, so a released value (the only kind with an actual) never arrived;
it now also lists the past EVENT_LOOKBACK_S with their actuals.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.v6.cycle_codes import CAL_POST_EVENT, CAL_POST_SURPRISE, CAL_STALE
from app.v6.cycle_types import CalendarEvent
from app.v6.deliberation.context_builder import ContextRequest, build_context, load_bars
from app.v6.market.calendar import (
    CODE_FOMC_PRESS_CONFERENCE, CODE_FOMC_STATEMENT, CODE_NONFARM_PAYROLLS, assess_calendar,
    may_be_surprise, surprise_unknown,
)
from app.v6.schemas.snapshot import CalendarEventBlock

from . import engine_fixtures_v6 as ef

MIN = 60
HOUR_S = 60 * MIN


def utc(*parts: int) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp())


NFP = utc(2026, 10, 2, 12, 30)
RELEASE_BAR_CLOSE = NFP + 15 * MIN


def nfp_block(*, actual: float | None = None, forecast: float | None = 100.0,
              code: str = CODE_NONFARM_PAYROLLS) -> CalendarEventBlock:
    return CalendarEventBlock(event_id=840_100, time_epoch=NFP, currency="USD",
                              importance="HIGH", code=code, name="Nonfarm Payrolls",
                              actual=actual, forecast=forecast, previous=None)


def assess(blocks: tuple[CalendarEventBlock, ...], now: int, **kwargs: Any):
    return assess_calendar(blocks, now_epoch=now, sent_at_epoch=now, **kwargs)


def carried_before_release() -> tuple[CalendarEvent, ...]:
    """What the previous cycle carries: the event as listed before its release."""
    return assess((nfp_block(),), NFP - 5 * MIN).events


# --- finding: CAL_POST_SURPRISE could never fire in production ------------------------------
@pytest.mark.parametrize(("minutes", "codes"), [
    (20, (CAL_POST_SURPRISE,)), (35, (CAL_POST_SURPRISE,)), (36, ()),
])
def test_an_ea_shaped_block_without_the_actual_fails_closed(minutes: int,
                                                            codes: tuple[str, ...]) -> None:
    # The released event is gone from the block; only the carried pre-release copy is left.
    result = assess((), NFP + minutes * MIN, carried_events=carried_before_release())
    assert (result.codes, result.blackout, result.stale) == (codes, bool(codes), False)


@pytest.mark.parametrize(("actual", "codes"), [(300.0, (CAL_POST_SURPRISE,)), (105.0, ())])
def test_a_released_actual_from_the_lookback_decides(actual: float,
                                                     codes: tuple[str, ...]) -> None:
    released = (nfp_block(actual=actual),)
    result = assess(released, NFP + 20 * MIN, carried_events=carried_before_release())
    assert (result.codes, result.blackout) == (codes, bool(codes))
    assert result.events[0].actual == actual      # the fresh copy replaced the carried one


def test_a_static_release_without_mt5_extends_but_the_press_conference_does_not() -> None:
    assert assess((), NFP + 20 * MIN).codes == (CAL_POST_SURPRISE,)
    assert not assess((), NFP + 35 * MIN + 1).blackout
    fomc_press = utc(2026, 10, 28, 18, 30)
    assert not assess((), fomc_press + 15 * MIN + 1).blackout


def test_a_block_of_released_events_alone_does_not_vouch_for_the_future() -> None:
    now = utc(2027, 2, 10, 14)           # no static coverage in 2027
    released = CalendarEventBlock(event_id=7, time_epoch=now - HOUR_S, currency="USD",
                                  importance="HIGH", code="cpi-yy", name="CPI",
                                  actual=3.0, forecast=3.0, previous=None)
    upcoming = released.model_copy(update={"time_epoch": now + HOUR_S, "actual": None})
    assert assess((released,), now).codes == (CAL_STALE,)
    assert not assess((released, upcoming), now).stale


@pytest.mark.parametrize(("event", "unknown", "maybe"), [
    ({"actual": None, "forecast": 1.0}, True, True),
    ({"actual": 1.0, "forecast": 1.0}, False, False),
    ({"actual": 2.0, "forecast": 1.0}, False, True),
    ({"actual": None, "forecast": None}, False, False),
    ({"source": "static", "code": CODE_NONFARM_PAYROLLS}, True, True),
    ({"source": "static", "code": CODE_FOMC_STATEMENT}, True, True),
    ({"source": "static", "code": CODE_FOMC_PRESS_CONFERENCE}, False, False),
    ({"source": "mt5", "code": CODE_NONFARM_PAYROLLS}, False, False),
])
def test_unknown_actuals(event: dict[str, Any], unknown: bool, maybe: bool) -> None:
    fields: dict[str, Any] = {"event_id": "x:1", "source": "mt5", "time_epoch": NFP,
                              "currency": "USD", "importance": "HIGH", "code": "x"}
    item = CalendarEvent(**(fields | event))
    assert (surprise_unknown(item), may_be_surprise(item)) == (unknown, maybe)


# --- finding: the release bar's block depended on processing latency -----------------------
KNOWN_SMALL_MISS = (nfp_block(actual=105.0),)


@pytest.mark.parametrize("delay", [0, 1, 3, 90])
def test_the_release_bar_is_blocked_however_late_the_cycle_runs(delay: int) -> None:
    now = RELEASE_BAR_CLOSE + delay
    at_bar = assess(KNOWN_SMALL_MISS, now, decision_epoch=RELEASE_BAR_CLOSE)
    assert (at_bar.codes, at_bar.blackout) == ((CAL_POST_EVENT,), True)
    assert at_bar.as_of_epoch == now
    if delay:
        assert not assess(KNOWN_SMALL_MISS, now).blackout    # the old, latency-dependent view


def test_the_next_bar_and_a_clock_behind_the_bar_are_unchanged() -> None:
    next_close = RELEASE_BAR_CLOSE + 15 * MIN
    assert not assess(KNOWN_SMALL_MISS, next_close + 1, decision_epoch=next_close).blackout
    # A decision instant after now never shortens the checked interval.
    behind = assess(KNOWN_SMALL_MISS, RELEASE_BAR_CLOSE, decision_epoch=next_close)
    assert behind.codes == (CAL_POST_EVENT,)


@pytest.mark.parametrize(("decision", "error"), [(True, TypeError), (1.5, TypeError),
                                                  (-1, ValueError)])
def test_the_decision_epoch_is_validated(decision: Any, error: type[Exception]) -> None:
    with pytest.raises(error):
        assess(KNOWN_SMALL_MISS, RELEASE_BAR_CLOSE, decision_epoch=decision)


@pytest.mark.parametrize("delay", [0, 1, 3])
def test_the_cycle_context_assesses_the_calendar_at_its_bar_close(delay: int) -> None:
    snapshot = ef.engine_snapshot(bar_open=NFP, calendar=[
        {**nfp_block(actual=105.0).model_dump(), "time_epoch": NFP}])
    request = ContextRequest(cycle_id="c-nfp", snapshot=snapshot,
                             received_at=float(RELEASE_BAR_CLOSE + delay))
    bars = load_bars(ef.MemoryBars({}), snapshot)

    context = build_context(request, bars, ef.settings(), RELEASE_BAR_CLOSE + delay)

    assert context.calendar.blackout and CAL_POST_EVENT in context.calendar.codes
