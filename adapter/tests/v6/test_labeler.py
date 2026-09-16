"""Counterfactual triple-barrier labeler: price conventions, edge cases and the ledger job."""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.v6.clock import FakeClock
from app.v6.config import V6Settings
from app.v6.cycle_types import CandidateAssessment
from app.v6.learning import (
    DEFAULT_LABELER_CONFIG, ERR_STORAGE, REASON_BAD_GEOMETRY, REASON_BARRIER,
    REASON_DATA_EXPIRED, REASON_EXIT_REFUSED, REASON_UNFILLED, LabelerConfig, LabelResult,
    LabelRun, available_from_for, build_price_path, data_through, decision_epoch,
    label_candidate, label_pending, label_pending_sync,
)
from app.v6.ledger_cycles import LedgerCycles
from app.v6.ledger_cycles_schema import CandidateRecord, CycleRecord
from app.v6.ledger_v6 import LedgerV6
from app.v6.market.bar_store import BarStore
from app.v6.risk import limits
from app.v6.types import Bar, Candidate, Refusal

from .cycle_fixtures_v6 import hold_result
from .payloads_v6 import BAR_OPEN, M15

M1, M5 = 60, 300
CFG = LabelerConfig()
DECISION = BAR_OPEN + M15
FILL_UNTIL = DECISION + CFG.pending_expiry_s
AVAILABLE = available_from_for(BAR_OPEN, CFG)
ENTRY, STOP, TARGET = 4300.0, 4290.0, 4320.0
ABOVE, BELOW = 4305.0, 4295.0      # flat prices that never fill a long / a short
SPREAD = 0.20                      # the 20-point spread every helper bar carries
SHORT: dict[str, Any] = {"candidate_id": f"orb-sell-{BAR_OPEN}", "side": "sell",
                         "stop": 4310.0, "invalidation": 4310.0, "target": 4280.0}


def _r(gross: float) -> float:
    return (gross - CFG.friction_price) / (ENTRY - STOP)


def _record(**overrides: Any) -> CandidateRecord:
    fields: dict[str, Any] = dict(
        candidate_id=f"displacement-buy-{BAR_OPEN}", cycle_id="cyc-1", setup="displacement",
        side="buy", entry=ENTRY, invalidation=STOP, stop=STOP, target=TARGET, bar_t=BAR_OPEN,
        features_json="{}", verdict="skip", label_status="pending", outcome=None,
        outcome_r=None, labeled_at=None, available_from=AVAILABLE)
    return CandidateRecord(**(fields | overrides))


def _bar(t: int, o: float, h: float, lo: float, c: float, spr: int = 20) -> Bar:
    return Bar(t=t, o=o, h=h, l=lo, c=c, tv=10, spr=spr)


def _at(i: int, o: float, h: float, lo: float, c: float, spr: int = 20) -> Bar:
    return _bar(DECISION + i * M1, o, h, lo, c, spr)


def _flat(start: int, count: int, price: float) -> tuple[Bar, ...]:
    return tuple(_bar(start + i * M1, price, price, price, price) for i in range(count))


def _path(*bars: Bar, until: int = AVAILABLE, price: float = ABOVE) -> tuple[Bar, ...]:
    """Flat M1 bars over [DECISION, until) with `bars` replacing their minutes."""
    by_t = {b.t: b for b in _flat(DECISION, (until - DECISION) // M1, price)}
    return tuple(sorted((by_t | {b.t: b for b in bars}).values(), key=lambda b: b.t))


def _m5_of(m1: Sequence[Bar]) -> tuple[Bar, ...]:
    buckets: dict[int, list[Bar]] = {}
    for bar in m1:
        buckets.setdefault(bar.t - bar.t % M5, []).append(bar)
    return tuple(_bar(t, rows[0].o, max(b.h for b in rows), min(b.l for b in rows), rows[-1].c)
                 for t, rows in sorted(buckets.items()))


FILL_LONG = _at(0, ABOVE, ABOVE, 4299.5, 4301.0)     # ask low 4299.7 <= entry
FILL_SHORT = _at(0, BELOW, 4300.0, BELOW, 4296.0)    # bid high reaches entry
EARLY = {"available_from": DECISION + 10 * M1}

# name: (record overrides, flat price, bars, outcome, exit price, minute the outcome is known)
EXIT_CASES: dict[str, tuple[Any, ...]] = {
    "long_target_after_fill": (
        {}, ABOVE, (FILL_LONG, _at(10, ABOVE, 4320.0, 4304.0, 4318.0)), "tp", TARGET, 11),
    "long_stop_wins_a_tie": (
        {}, ABOVE, (FILL_LONG, _at(5, ABOVE, 4325.0, 4290.0, 4310.0)), "sl", STOP, 6),
    "long_stop_in_fill_bar_counts": (
        {}, ABOVE, (_at(0, ABOVE, ABOVE, 4289.0, 4292.0),), "sl", STOP, 1),
    "long_gap_exits_at_open": (
        {}, ABOVE, (FILL_LONG, _at(3, 4285.0, 4286.0, 4284.0, 4285.5)), "sl", 4285.0, 4),
    "long_target_in_fill_bar_ignored": (
        {}, ABOVE, (_at(0, ABOVE, 4321.0, 4299.0, ABOVE),), "time", ABOVE, 120),
    "time_exit_ignores_bars_after_the_barrier": ({}, 4303.0, (
        _at(0, 4303.0, 4303.0, 4299.0, 4303.0), _at(119, 4303.0, 4303.5, 4302.5, 4303.25),
        _at(120, 4303.0, 4330.0, 4280.0, 4303.0)), "time", 4303.25, 120),
    "available_from_bounds_the_walk": (EARLY, ABOVE, (
        FILL_LONG, _at(9, ABOVE, ABOVE, 4304.0, 4304.0), _at(10, ABOVE, ABOVE, 4280.0, 4281.0)),
        "time", 4304.0, 10),
    # The ask (bid + 0.20) triggers a short's target and stop; its time exit pays the ask.
    "short_target_on_the_ask": (SHORT, BELOW, (
        FILL_SHORT, _at(2, BELOW, BELOW, 4279.9, 4290.0), _at(3, BELOW, BELOW, 4279.8, 4290.0)),
        "tp", 4280.0, 4),
    "short_stop_on_the_ask": (
        SHORT, BELOW, (FILL_SHORT, _at(1, BELOW, 4309.8, BELOW, BELOW)), "sl", 4310.0, 2),
    "short_gap_exits_at_ask_open": (
        SHORT, BELOW, (FILL_SHORT, _at(1, 4312.0, 4313.0, 4311.0, 4312.0)), "sl", 4312.2, 2),
    "short_time_exit_pays_the_ask": (SHORT, BELOW, (FILL_SHORT,), "time", BELOW + SPREAD, 120),
}
UNFILLED_CASES: dict[str, tuple[dict[str, Any], tuple[Bar, ...]]] = {
    "bid_touch_is_not_ask_touch": ({}, _path(price=ENTRY)),
    "touch_after_expiry": ({}, _path(_bar(FILL_UNTIL, ENTRY, ENTRY, 4280.0, 4282.0), price=ENTRY)),
    "touch_before_decision": ({}, (_bar(DECISION - M1, ABOVE, ABOVE, 4280.0, ABOVE),) + _path()),
    "short_below_entry": (SHORT, _path(price=4299.99)),
}


class TestPriceConventions:
    @pytest.mark.parametrize(("overrides", "price", "bars", "outcome", "exit_price", "minute"),
                             EXIT_CASES.values(), ids=EXIT_CASES)
    def test_barrier_exits(self, overrides: dict[str, Any], price: float, bars: tuple[Bar, ...],
                           outcome: str, exit_price: float, minute: int) -> None:
        record = _record(**overrides)
        side = 1 if record.side == "buy" else -1

        result = label_candidate(record, _path(*bars, price=price), (), CFG)

        assert (result.label_status, result.outcome, result.reason) == (
            "labeled", outcome, REASON_BARRIER)
        assert result.outcome_r == pytest.approx(_r(side * (exit_price - ENTRY)))
        assert (result.fill_epoch, result.resolved_at, result.coarse_bars) == (
            DECISION, DECISION + minute * M1, 0)

    @pytest.mark.parametrize(("overrides", "bars"), UNFILLED_CASES.values(), ids=UNFILLED_CASES)
    def test_unfilled_limits(self, overrides: dict[str, Any], bars: tuple[Bar, ...]) -> None:
        result = label_candidate(_record(**overrides), bars, (), CFG)

        assert (result.label_status, result.outcome, result.outcome_r, result.reason) == (
            "unfillable", "unfilled", None, REASON_UNFILLED)
        assert (result.fill_epoch, result.resolved_at) == (None, FILL_UNTIL)

    def test_future_and_past_bars_do_not_change_the_label(self) -> None:
        bars = _path(FILL_LONG, _at(7, ABOVE, 4320.0, ABOVE, ABOVE))
        noise = _flat(DECISION - 120 * M1, 120, 4250.0) + bars + _flat(AVAILABLE, 120, 4000.0)

        base = label_candidate(_record(), bars, (), CFG)

        assert label_candidate(_record(), noise, _m5_of(noise), CFG) == base

    @pytest.mark.parametrize(("low", "spr", "filled"), [
        (4299.80, 20, True),     # ask low exactly at entry
        (4299.80, 21, False),    # one more point of spread keeps the ask above entry
        (4300.00, 20, False),    # bid touches entry, ask does not
        (4299.72, 0, True),      # no bar spread: the 28-point fallback applies
        (4299.80, 0, False),
    ])
    def test_buy_limit_fills_on_the_ask(self, low: float, spr: int, filled: bool) -> None:
        bars = _path(_at(0, ABOVE, ABOVE, low, ABOVE, spr=spr))

        assert (label_candidate(_record(), bars, (), CFG).outcome != "unfilled") is filled

    def test_incomplete_m1_bucket_falls_back_to_the_m5_bar(self) -> None:
        m1 = _path(FILL_LONG)
        holed = tuple(b for b in m1 if not DECISION + M5 <= b.t < DECISION + M5 + 3 * M1)
        m5 = tuple(replace(b, l=4289.0) if b.t == DECISION + M5 else b for b in _m5_of(m1))

        result = label_candidate(_record(), holed, m5, CFG)

        assert (result.outcome, result.resolved_at, result.coarse_bars) == (
            "sl", DECISION + 2 * M5, 1)
        # A complete bucket keeps its M1 bars, whatever its M5 bar says.
        assert label_candidate(_record(), m1, m5, CFG).outcome == "time"

    def test_build_price_path_keeps_order_and_window(self) -> None:
        m1 = _flat(DECISION - M5, 11, ABOVE)            # the bucket at DECISION + M5 has 1 bar
        m5 = _m5_of(_flat(DECISION - M5, 15, ABOVE))

        path = build_price_path(m1, m5, DECISION, DECISION + 3 * M5, CFG)

        assert [(b.t, b.span_s, b.coarse) for b in path] == (
            [(DECISION + i * M1, M1, False) for i in range(5)] + [(DECISION + M5, M5, True)])
        assert all(b.spread == pytest.approx(SPREAD) for b in path)


class TestGeometryAndConfig:
    @pytest.mark.parametrize("overrides", [
        {"stop": 4301.0}, {"target": 4299.0}, {"side": "flat"}, {"entry": math.nan},
        {"stop": math.inf}, {"target": 0.0}, SHORT | {"stop": 4295.0},
    ])
    def test_unusable_geometry_is_unfillable(self, overrides: dict[str, Any]) -> None:
        result = label_candidate(_record(**overrides), _path(FILL_LONG, FILL_SHORT), (), CFG)

        assert (result.label_status, result.reason) == ("unfillable", REASON_BAD_GEOMETRY)

    @pytest.mark.parametrize(("status", "outcome", "r"), [
        ("labeled", "unfilled", 1.0), ("labeled", "tp", None), ("labeled", "sl", math.nan),
        ("unfillable", "unfilled", 0.0), ("unfillable", "tp", None), ("pending", "tp", 1.0),
    ])
    def test_label_result_rejects_inconsistent_labels(self, status: str, outcome: str,
                                                      r: float | None) -> None:
        fields: dict[str, Any] = {"label_status": status, "outcome": outcome, "outcome_r": r}
        with pytest.raises(ValueError):
            LabelResult(candidate_id="c", reason=REASON_BARRIER, **fields)

    @pytest.mark.parametrize("overrides", [
        {"point": 0.0}, {"point": math.nan}, {"friction_price": -0.01},
        {"friction_price": math.inf}, {"pending_expiry_s": 0}, {"pending_expiry_s": 1.5},
        {"time_barrier_s": 0}, {"time_barrier_s": limits.MAX_TIME_BARRIER_S + 1},
        {"time_barrier_s": True}, {"fallback_spread_points": 0}, {"batch_limit": 0},
        {"batch_limit": 1001}, {"max_window_bars": 0}, {"max_window_bars": 5001},
    ])
    def test_config_rejects_bad_values(self, overrides: dict[str, Any]) -> None:
        with pytest.raises(ValueError, match=next(iter(overrides))):
            LabelerConfig(**overrides)

    def test_config_defaults_and_settings(self) -> None:
        fields = V6Settings.model_fields
        settings = V6Settings(_env_file=None, account_type="raw", pending_expiry_bars=3,
                              time_barrier_bars=4)

        cfg = LabelerConfig.from_settings(settings, point=0.001)

        assert DEFAULT_LABELER_CONFIG == CFG
        assert CFG.friction_price == limits.FRICTION_PRICE["standard"]
        assert CFG.pending_expiry_s == fields["pending_expiry_bars"].default * M15
        assert CFG.time_barrier_s == fields["time_barrier_bars"].default * M15
        assert (cfg.point, cfg.friction_price) == (0.001, limits.FRICTION_PRICE["raw"])
        assert available_from_for(BAR_OPEN, cfg) == BAR_OPEN + M15 + 7 * M15
        assert decision_epoch(_record()) == DECISION


Stores = tuple[LedgerCycles, BarStore]
TP_PATH = (_flat(DECISION - 60 * M1, 60, ABOVE)
           + _path(FILL_LONG, _at(30, ABOVE, 4321.0, ABOVE, ABOVE)))


class RacingLedger(LedgerCycles):
    """Another worker labels every candidate just before this one writes."""
    def write_label(self, candidate_id: str, **kwargs: Any) -> bool:
        super().write_label(candidate_id, **kwargs)
        return super().write_label(candidate_id, **kwargs)


class CorruptSummaryLedger(LedgerCycles):
    def get_cycle(self, cycle_id: str) -> CycleRecord | None:
        record = super().get_cycle(cycle_id)
        return None if record is None else replace(record, summary_json="{broken")


@pytest.fixture(params=[LedgerCycles])
def stores(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Stores]:
    bars_ledger = LedgerV6(tmp_path / "labels.db")
    cycles = request.param(tmp_path / "labels.db")
    yield cycles, BarStore(bars_ledger)
    cycles.close()
    bars_ledger.close()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _assessment(record: CandidateRecord, refusal: Refusal | None) -> CandidateAssessment:
    candidate = Candidate(candidate_id=record.candidate_id, setup=record.setup,
                          side=record.side, entry=record.entry,  # type: ignore[arg-type]
                          invalidation=record.invalidation, bar_t=record.bar_t)
    return CandidateAssessment(
        candidate=candidate, verdict=record.verdict,  # type: ignore[arg-type]
        stop=record.stop, target=record.target, available_from=record.available_from,
        refusal=refusal)


def _seed(stores: Stores, *records: CandidateRecord, bars: Sequence[Bar] = TP_PATH,
          refusal: Refusal | None = None) -> None:
    ledger, store = stores
    items = tuple(_assessment(r, refusal) for r in records or (_record(),))
    assert ledger.record_cycle(replace(hold_result("cyc-1"), candidates=items), 1.0)
    store.ingest("M1", bars)
    store.ingest("M5", _m5_of(bars))


def _run(stores: Stores, now: float = AVAILABLE + 1.0, **config: Any) -> LabelRun:
    return label_pending_sync(*stores, now, FakeClock(epoch=now), replace(CFG, **config))


def _stored(stores: Stores, candidate_id: str = _record().candidate_id) -> CandidateRecord:
    return next(c for c in stores[0].candidates_for_cycle("cyc-1")
                if c.candidate_id == candidate_id)


class TestLedgerJob:
    def test_labels_ready_candidates(self, stores: Stores) -> None:
        never = _record(candidate_id=f"engulfing-buy-{BAR_OPEN}", entry=4290.0, stop=4280.0,
                        invalidation=4280.0, target=4310.0, verdict="gated")
        _seed(stores, _record(), never)

        run = _run(stores, now=AVAILABLE + 5.0)

        assert (run.written, run.labeled, run.unfillable, run.waiting) == (2, 1, 1, 0)
        stored = _stored(stores)
        assert (stored.label_status, stored.outcome, stored.labeled_at) == (
            "labeled", "tp", AVAILABLE + 5.0)
        assert stored.outcome_r == pytest.approx(_r(TARGET - ENTRY))
        missed = _stored(stores, never.candidate_id)
        assert (missed.label_status, missed.outcome, missed.outcome_r) == (
            "unfillable", "unfilled", None)

    def test_candidates_waiting_for_data_stay_pending(self, stores: Stores) -> None:
        _seed(stores, bars=tuple(b for b in TP_PATH if b.t < AVAILABLE - M15))

        waiting = _run(stores, now=AVAILABLE + 60.0)

        assert (waiting.written, waiting.waiting, waiting.results) == (0, 1, ())
        assert _stored(stores).label_status == "pending"
        stores[1].ingest("M1", TP_PATH)
        stores[1].ingest("M5", _m5_of(TP_PATH))
        assert (_run(stores, AVAILABLE + 60.0).written, _stored(stores).outcome) == (1, "tp")

    def test_nothing_happens_without_bars_or_before_available_from(self, stores: Stores) -> None:
        _seed(stores, bars=())
        assert (data_through(stores[1]), _run(stores)) == (None, LabelRun(waiting=1))
        stores[1].ingest("M1", TP_PATH)
        stores[1].ingest("M5", _m5_of(TP_PATH))
        assert (data_through(stores[1]), _run(stores, AVAILABLE - 1.0)) == (AVAILABLE, LabelRun())

    def test_labeling_is_idempotent(self, stores: Stores) -> None:
        _seed(stores)
        first = _run(stores)
        before = _stored(stores)

        second = _run(stores, now=AVAILABLE + 900.0)

        assert (first.written, second, _stored(stores)) == (1, LabelRun(), before)

    @pytest.mark.parametrize(("refusal", "detail"), [
        (Refusal(("STOP_BELOW_FLOOR",), "3.1 < 6.0"), "STOP_BELOW_FLOOR"), (None, "unknown")])
    def test_refused_candidates_need_no_bars(self, stores: Stores, refusal: Refusal | None,
                                             detail: str) -> None:
        _seed(stores, _record(verdict="refused"), bars=(), refusal=refusal)

        run = _run(stores)

        assert (run.written, run.waiting, run.results[0].reason) == (1, 0, REASON_EXIT_REFUSED)
        assert detail in run.results[0].detail
        assert _stored(stores).outcome == "unfilled"

    @pytest.mark.parametrize("stores", [CorruptSummaryLedger], indirect=True)
    def test_unreadable_summary_still_labels_the_refusal(self, stores: Stores) -> None:
        _seed(stores, _record(verdict="refused"), bars=(), refusal=Refusal(("X",)))

        (result,) = _run(stores).results

        assert (result.reason, "unknown" in result.detail) == (REASON_EXIT_REFUSED, True)

    @pytest.mark.parametrize("stores", [RacingLedger], indirect=True)
    def test_a_concurrent_writer_wins_quietly(self, stores: Stores) -> None:
        _seed(stores)

        run = _run(stores)

        assert (run.written, run.labeled, _stored(stores).outcome) == (0, 1, "tp")

    @pytest.mark.parametrize(("cap", "expected"), [
        (5, ("unfillable", REASON_DATA_EXPIRED, None, False)),     # no series reaches back
        (40, ("labeled", REASON_BARRIER, DECISION + 7 * M5, True)),  # M5 does, M1 does not
    ])
    def test_bar_load_cap(self, stores: Stores, cap: int, expected: tuple[Any, ...]) -> None:
        _seed(stores)

        (result,) = _run(stores, max_window_bars=cap).results

        assert (result.label_status, result.reason, result.resolved_at,
                result.coarse_bars > 0) == expected

    def test_horizon_before_the_decision_is_unfilled(self, stores: Stores) -> None:
        _seed(stores, _record(available_from=BAR_OPEN + 60), bars=_flat(BAR_OPEN - M5, 15, ABOVE))
        (result,) = _run(stores).results
        assert (result.outcome, result.resolved_at) == ("unfilled", BAR_OPEN + 60)

    def test_storage_errors_are_reported_not_raised(self, stores: Stores) -> None:
        stores[0].close()

        assert _run(stores) == LabelRun(error=ERR_STORAGE)

    @pytest.mark.anyio
    async def test_async_job_labels_in_a_worker_thread(self, stores: Stores) -> None:
        _seed(stores)
        clock = FakeClock(epoch=AVAILABLE + 1.0)

        run = await label_pending(*stores, clock.now_epoch(), clock, config=CFG)

        assert (run.written, run.labeled, _stored(stores).outcome) == (1, 1, "tp")
