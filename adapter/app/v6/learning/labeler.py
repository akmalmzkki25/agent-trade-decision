"""
Counterfactual triple-barrier labeler (plan 7.2, kn/12 section 4, kn/08 section 7).

Every stored candidate, taken or not, gets the outcome a LIMIT order at its entry
would have had. Conventions (MT5 bars are Bid bars; a touch of a level counts):
- ask = bid + spread; spread = bar.spr points x point (fallback when a bar has 0).
- Decision D = candidate bar close. Only bars opening at/after D (none of their
  ticks was known at D) and closing by `available_from` are read.
- Fill: bars opening in [D, D + pending_expiry_s). Buy limit: ask low <= entry;
  sell limit: bid high >= entry. Filled at the limit price, never better.
- Long: stop on bid low <= stop, target on bid high >= target. Short: stop on ask
  high >= stop, target on ask low <= target. A stop fills at the stop, or at the
  open of a bar that gapped through it; a target fills at the target.
- In the fill bar only the stop counts (the price crossed the entry first); a
  target touch there may predate the fill (kn/12 rule 2). Both in one later bar:
  the stop wins (rule 3). Time exit: close of the last bar opening before
  fill-bar open + time_barrier_s (bid for longs, ask for shorts).
- outcome_r = (side x (exit - entry) - FRICTION_PRICE) / |entry - stop|: friction
  once per filled trade, as kn/01's p* = (S+c)/(T+S) and the sizer's loss per lot.
  It overlaps the spread paid through the trigger sides, deliberately.
- Path (`price_path`): M1 bars in complete 5-minute buckets, else the bucket's M5
  bar. A bucket with neither is a market closure only when no quotes were expected
  (weekend, rollover, the broker's daily gap); otherwise it is a data hole, and a
  label that depends on the missing span is DATA_GAP ('unfillable'/'data_gap').
  The ledger job (`label_job`) keeps such a candidate pending while a backfill can
  still fill the hole.
Unfilled limits, refused exit plans, unusable geometry and windows older than
the bar load cap are written 'unfillable'/'unfilled'; tp/sl/time are 'labeled'.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from ..ledger_cycles import LABELED_OUTCOMES, MAX_LIST_LIMIT, UNFILLABLE_OUTCOMES, LabelOutcome
from ..ledger_cycles_schema import CandidateRecord
from ..market.bar_store import MAX_LOAD_BARS
from ..market.broker_hours import DEFAULT_QUOTE_GAP, DailyUtcWindow
from ..risk import limits
from ..risk.exits import PRICE_EPS
from ..types import TIMEFRAME_SECONDS, Bar
from .price_path import PathBar, build_price_path, uncovered_buckets  # noqa: F401 - re-export

if TYPE_CHECKING:
    from ..config import V6Settings

WrittenStatus = Literal["labeled", "unfillable"]

M15_S: Final[int] = TIMEFRAME_SECONDS["M15"]
# Probe 2026-09-16: XAUUSD digits 2, point = tick_size = 0.01.
XAUUSD_POINT: Final[float] = 0.01
# Probe spreads were 24-28 points; the top of that range when a bar has none.
DEFAULT_FALLBACK_SPREAD_POINTS: Final[int] = 28
# V6Settings defaults: pending_expiry_bars=2, time_barrier_bars=8.
DEFAULT_PENDING_EXPIRY_S: Final[int] = 2 * M15_S
DEFAULT_TIME_BARRIER_S: Final[int] = 8 * M15_S
DEFAULT_BATCH_LIMIT: Final[int] = 500
# The EA backfills 3 days of M5 when it starts; an older hole cannot be filled.
DEFAULT_DATA_GAP_GRACE_S: Final[int] = 3 * 86_400
R_DECIMALS: Final[int] = 6
REFUSED_VERDICT: Final[str] = "refused"

REASON_BARRIER: Final[str] = "BARRIER"            # tp, sl or time barrier reached
REASON_UNFILLED: Final[str] = "LIMIT_UNFILLED"
REASON_EXIT_REFUSED: Final[str] = "EXIT_REFUSED"
REASON_BAD_GEOMETRY: Final[str] = "BAD_GEOMETRY"
REASON_DATA_EXPIRED: Final[str] = "DATA_EXPIRED"
REASON_DATA_GAP: Final[str] = "DATA_GAP"
OUTCOME_UNFILLED: Final[LabelOutcome] = "unfilled"
OUTCOME_DATA_GAP: Final[LabelOutcome] = "data_gap"

_DIRECTION: Final[dict[str, int]] = {"buy": 1, "sell": -1}


def _int_in(value: object, low: int, high: float = math.inf) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _is_price(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0


@dataclass(frozen=True)
class LabelerConfig:
    point: float = XAUUSD_POINT
    friction_price: float = limits.FRICTION_PRICE["standard"]
    pending_expiry_s: int = DEFAULT_PENDING_EXPIRY_S
    time_barrier_s: int = DEFAULT_TIME_BARRIER_S
    fallback_spread_points: int = DEFAULT_FALLBACK_SPREAD_POINTS
    batch_limit: int = DEFAULT_BATCH_LIMIT
    max_window_bars: int = MAX_LOAD_BARS
    quote_gap: DailyUtcWindow | None = DEFAULT_QUOTE_GAP
    data_gap_grace_s: int = DEFAULT_DATA_GAP_GRACE_S

    def __post_init__(self) -> None:
        friction = self.friction_price
        checks = (
            ("point", _is_price(self.point)),
            ("friction_price", _is_price(friction) or friction == 0),
            ("pending_expiry_s", _int_in(self.pending_expiry_s, 1)),
            ("time_barrier_s", _int_in(self.time_barrier_s, 1, limits.MAX_TIME_BARRIER_S)),
            ("fallback_spread_points", _int_in(self.fallback_spread_points, 1)),
            ("batch_limit", _int_in(self.batch_limit, 1, MAX_LIST_LIMIT)),
            ("max_window_bars", _int_in(self.max_window_bars, 1, MAX_LOAD_BARS)),
            ("quote_gap", self.quote_gap is None or isinstance(self.quote_gap, DailyUtcWindow)),
            ("data_gap_grace_s", _int_in(self.data_gap_grace_s, 0)),
        )
        bad = [name for name, ok in checks if not ok]
        if bad:
            raise ValueError(f"invalid labeler config: {', '.join(bad)}")

    @classmethod
    def from_settings(cls, settings: V6Settings, point: float = XAUUSD_POINT) -> LabelerConfig:
        """The geometry the runtime trades with; `point` comes from the symbol spec."""
        return cls(point=point, friction_price=settings.friction_price,
                   pending_expiry_s=settings.pending_expiry_bars * M15_S,
                   time_barrier_s=settings.time_barrier_s, quote_gap=settings.quote_gap)


DEFAULT_LABELER_CONFIG: Final[LabelerConfig] = LabelerConfig()


def decision_epoch(candidate: CandidateRecord) -> int:
    """Close of the M15 bar that produced the candidate."""
    return candidate.bar_t + M15_S


def available_from_for(bar_t: int, config: LabelerConfig = DEFAULT_LABELER_CONFIG) -> int:
    """When the barrier has certainly resolved: bar close + pending expiry + time barrier."""
    return bar_t + M15_S + config.pending_expiry_s + config.time_barrier_s


@dataclass(frozen=True)
class LabelResult:
    candidate_id: str
    label_status: WrittenStatus
    outcome: LabelOutcome
    outcome_r: float | None
    reason: str
    fill_epoch: int | None = None     # open of the fill bar
    resolved_at: int | None = None    # when the outcome became certain
    coarse_bars: int = 0              # M5 bars used before resolution
    detail: str = ""

    def __post_init__(self) -> None:
        r = self.outcome_r
        labeled = (self.label_status == "labeled" and self.outcome in LABELED_OUTCOMES
                   and r is not None and math.isfinite(r))
        not_filled = (self.label_status == "unfillable"
                      and self.outcome in UNFILLABLE_OUTCOMES and r is None)
        if not (labeled or not_filled):
            raise ValueError("labeled needs tp/sl/time and a finite R; unfillable has neither")

    @property
    def data_gap(self) -> bool:
        return self.outcome == OUTCOME_DATA_GAP


def _geometry_ok(candidate: CandidateRecord, direction: int) -> bool:
    prices = (candidate.entry, candidate.stop, candidate.target)
    return (all(_is_price(p) for p in prices)
            and direction * (candidate.entry - candidate.stop) > 0
            and direction * (candidate.target - candidate.entry) > 0)


def _fills(bar: PathBar, entry: float, direction: int) -> bool:
    return bar.l + bar.spread <= entry + PRICE_EPS if direction > 0 else bar.h >= entry - PRICE_EPS


def _stop_exit(bar: PathBar, stop: float, direction: int) -> float | None:
    """Exit price when the bar reaches the stop (the open if it gapped through), else None."""
    if direction > 0:
        return min(stop, bar.o) if bar.l <= stop + PRICE_EPS else None
    hit = bar.h + bar.spread >= stop - PRICE_EPS
    return max(stop, bar.o + bar.spread) if hit else None


def _target_hit(bar: PathBar, level: float, direction: int) -> bool:
    return bar.h >= level - PRICE_EPS if direction > 0 else bar.l + bar.spread <= level + PRICE_EPS


def _walk_exit(candidate: CandidateRecord, direction: int, path: Sequence[PathBar],
               fill_at: int, config: LabelerConfig) -> tuple[LabelOutcome, float, int]:
    """(outcome, exit price, resolved_at) for a position filled in `path[fill_at]`."""
    fill = path[fill_at]
    exit_by = fill.t + config.time_barrier_s
    last = fill
    for bar in (b for b in path[fill_at:] if b.t < exit_by):
        stop_price = _stop_exit(bar, candidate.stop, direction)
        if stop_price is not None:
            return "sl", stop_price, bar.end
        if bar is not fill and _target_hit(bar, candidate.target, direction):
            return "tp", candidate.target, bar.end
        last = bar
    close = last.c if direction > 0 else last.c + last.spread
    return "time", close, last.end


def unfillable(candidate: CandidateRecord, reason: str, detail: str,
               resolved_at: int | None = None, coarse: int = 0) -> LabelResult:
    return LabelResult(candidate_id=candidate.candidate_id, label_status="unfillable",
                       outcome=OUTCOME_UNFILLED, outcome_r=None, reason=reason,
                       resolved_at=resolved_at, coarse_bars=coarse, detail=detail)


def _data_gap(candidate: CandidateRecord, holes: Sequence[int]) -> LabelResult:
    detail = f"{len(holes)} open-market 5-minute buckets without bars, first at {holes[0]}"
    return LabelResult(candidate_id=candidate.candidate_id, label_status="unfillable",
                       outcome=OUTCOME_DATA_GAP, outcome_r=None, reason=REASON_DATA_GAP,
                       detail=detail)


def _depends_until(result: LabelResult, candidate: CandidateRecord,
                   config: LabelerConfig) -> int:
    """End of the span the outcome was read from; later bars cannot change it."""
    if result.outcome == "time" and result.fill_epoch is not None:
        return min(result.fill_epoch + config.time_barrier_s, candidate.available_from)
    return candidate.available_from if result.resolved_at is None else result.resolved_at


def _coarse_before(path: Sequence[PathBar], epoch: int) -> int:
    return sum(1 for bar in path if bar.coarse and bar.t < epoch)


def _simulate(candidate: CandidateRecord, direction: int, path: Sequence[PathBar],
              config: LabelerConfig) -> LabelResult:
    fill_until = decision_epoch(candidate) + config.pending_expiry_s
    fill_at = next((i for i, bar in enumerate(path)
                    if bar.t < fill_until and _fills(bar, candidate.entry, direction)), None)
    if fill_at is None:
        resolved = min(fill_until, candidate.available_from)
        return unfillable(candidate, REASON_UNFILLED, "limit not reached before expiry",
                          resolved, _coarse_before(path, resolved))
    outcome, exit_price, resolved = _walk_exit(candidate, direction, path, fill_at, config)
    gross = direction * (exit_price - candidate.entry)
    r = (gross - config.friction_price) / abs(candidate.entry - candidate.stop)
    return LabelResult(candidate_id=candidate.candidate_id, label_status="labeled",
                       outcome=outcome, outcome_r=round(r, R_DECIMALS), reason=REASON_BARRIER,
                       fill_epoch=path[fill_at].t, resolved_at=resolved,
                       coarse_bars=_coarse_before(path, resolved))


def label_candidate(candidate: CandidateRecord, m1: Sequence[Bar], m5: Sequence[Bar],
                    config: LabelerConfig = DEFAULT_LABELER_CONFIG, *,
                    refusal_codes: tuple[str, ...] = ()) -> LabelResult:
    """Pure label of one candidate; `m1`/`m5` must reach its `available_from`.

    A hole in the bars the outcome depends on yields DATA_GAP instead of a label.
    """
    if candidate.verdict == REFUSED_VERDICT:
        codes = ",".join(refusal_codes) or "unknown"
        return unfillable(candidate, REASON_EXIT_REFUSED, f"exit plan refused ({codes})")
    direction = _DIRECTION.get(candidate.side)
    if direction is None or not _geometry_ok(candidate, direction):
        return unfillable(candidate, REASON_BAD_GEOMETRY, "stored side/stop/target unusable")
    start, limit = decision_epoch(candidate), candidate.available_from
    result = _simulate(candidate, direction, build_price_path(m1, m5, start, limit, config),
                       config)
    holes = uncovered_buckets(m1, m5, start, _depends_until(result, candidate, config),
                              limit=limit, quote_gap=config.quote_gap)
    return _data_gap(candidate, holes) if holes else result
