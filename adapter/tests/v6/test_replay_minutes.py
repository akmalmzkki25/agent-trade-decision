"""scripts/v6replay/minutes.py: the minute replay counts m1 packets and times each minute."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from app.v6.types import Bar

from . import replay_fixtures_v6 as rf
from .fixtures_v6 import random_walk_bars

from v6replay import data, minutes, runner, synth  # noqa: E402,I001
import v6_replay  # noqa: E402

M1 = 60
M15 = 900
# One M1 step of sigma 2.2 gives M5 ranges like the M5 walk of the M15 replay tests.
M1_SIGMA = 2.2
M1_WICK = 1.0
# Two hours of the evaluation day, in the London session: eight M15 closes.
WINDOW = runner.ReplayWindow(start=rf.EVAL_DAY_START + 9 * 3600,
                             end=rf.EVAL_DAY_START + 11 * 3600)


@pytest.fixture(scope="module")
def bars() -> data.BarSet:
    """One M1 random walk, aggregated so every timeframe agrees."""
    raw = random_walk_bars(rf.HISTORY_DAYS * rf.DAY // M1, seed=rf.SEED, sigma=M1_SIGMA,
                           wick=M1_WICK, tf="M1")
    m1 = tuple(Bar(t=b.t, o=b.o, h=b.h, l=b.l, c=b.c, tv=b.tv, spr=rf.SPREAD_POINTS)
               for b in raw)
    return data.BarSet(series={"M1": m1, "M5": rf.aggregate(m1, 300),
                               "M15": rf.aggregate(m1, M15), "H1": rf.aggregate(m1, 3600),
                               "D1": rf.aggregate(m1, rf.DAY)})


def test_every_minute_between_two_m15_closes_is_counted(bars: data.BarSet) -> None:
    tally = minutes.run_minute_replay(bars, (), synth.replay_settings(), WINDOW)

    assert tally.minutes == 8 * minutes.MINUTES_PER_M15      # the M15 close minute is m15
    assert 0 < tally.packets <= tally.minutes
    assert sum(tally.per_day.values()) == tally.packets
    assert sum(tally.blocked_by.values()) == tally.minutes - tally.packets
    assert 0 < tally.build_ms_p50 <= tally.build_ms_p95


def test_a_minute_never_reads_a_later_bar(bars: data.BarSet) -> None:
    settings = synth.replay_settings()
    end = WINDOW.end or 0
    full = minutes.run_minute_replay(bars, (), settings, WINDOW)
    cut = minutes.run_minute_replay(bars.truncated(end + 14 * M1), (), settings, WINDOW)
    assert (full.minutes, full.packets, full.blocked_by, full.per_day) == (
        cut.minutes, cut.packets, cut.blocked_by, cut.per_day)


def test_without_m1_history_nothing_is_evaluated(bars: data.BarSet) -> None:
    no_m1 = data.BarSet(series={tf: bars.bars(tf) for tf in ("M5", "M15", "H1", "D1")})
    tally = minutes.run_minute_replay(no_m1, (), synth.replay_settings(), WINDOW)
    assert (tally.minutes, tally.packets, tally.build_ms_p95) == (0, 0, 0.0)


TALLY = minutes.MinuteTally(minutes=28, packets=20, blocked_by={"SESSION": 8},
                            per_day={"2026-09-24": 20}, build_ms_p50=3.1, build_ms_p95=7.9)


def test_the_cli_prints_and_writes_the_minute_rhythm(tmp_path: Path, bars: data.BarSet,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    windows: list[runner.ReplayWindow] = []

    def replay_minutes(_bars: data.BarSet, _events: object, _settings: object,
                       window: runner.ReplayWindow) -> minutes.MinuteTally:
        windows.append(window)
        return TALLY

    monkeypatch.setattr(v6_replay, "_load", lambda _args: (bars, (), "synthetic"))
    monkeypatch.setattr(v6_replay, "run_replay", lambda *_args: ())
    monkeypatch.setattr(v6_replay, "run_minute_replay", replay_minutes)
    out, err = io.StringIO(), io.StringIO()
    target = tmp_path / "replay.json"
    code = v6_replay.main(["--from", "2026-09-24", "--to", "2026-09-24", "--minutes",
                           "--json", str(target)], stdout=out, stderr=err)

    assert code == v6_replay.EXIT_OK, err.getvalue()
    assert windows == [runner.ReplayWindow(start=rf.EVAL_DAY_START,
                                           end=rf.EVAL_DAY_START + rf.DAY)]
    assert out.getvalue().split("## Minute rhythm\n")[1].splitlines() == [
        "- minutes evaluated: 28, m1 packets: 20",
        "- m1 packets per day: 2026-09-24 20",
        "- first failed gate: SESSION 8",
        "- adapter time per minute: p50 3.1 ms, p95 7.9 ms"]
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["minutes"] == {"minutes": 28, "packets": 20, "blocked_by": {"SESSION": 8},
                                   "per_day": {"2026-09-24": 20}, "build_ms_p50": 3.1,
                                   "build_ms_p95": 7.9}


def test_without_the_flag_there_is_no_minute_section(tmp_path: Path, bars: data.BarSet,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(v6_replay, "_load", lambda _args: (bars, (), "synthetic"))
    monkeypatch.setattr(v6_replay, "run_replay", lambda *_args: ())
    out, target = io.StringIO(), tmp_path / "replay.json"
    assert v6_replay.main(["--json", str(target)], stdout=out, stderr=io.StringIO()) == 0
    assert "Minute rhythm" not in out.getvalue()
    assert "minutes" not in json.loads(target.read_text(encoding="utf-8"))
