"""scripts/v6_replay.py and scripts/v6replay: the tier-0 replay and its baseline counts."""

from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.v6.cycle_codes import GATE_OCCUPANCY, GATE_SESSION, GATE_TRADES_TODAY
from app.v6.types import Bar

from . import replay_fixtures_v6 as rf
from .fixtures_v6 import DEFAULT_START_EPOCH

from v6replay import data, evaluate, render, runner, synth, tally  # noqa: E402,I001
import v6_replay  # noqa: E402

M15 = 900
EVAL_WINDOW = runner.ReplayWindow(start=rf.EVAL_DAY_START, end=rf.EVAL_DAY_START + rf.DAY)


@pytest.fixture(scope="module")
def bars() -> data.BarSet:
    return rf.history()


@pytest.fixture
def buying(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(evaluate, "detect_all", rf.buy_detector)


def _record(**changes: object) -> evaluate.BarRecord:
    bar_t = int(changes.pop("bar_t", rf.EVAL_DAY_START + 12 * 3600))  # type: ignore[arg-type]
    base = evaluate.BarRecord(bar_t=bar_t, as_of=bar_t + M15, spread_points=30)
    return replace(base, **changes)


def _item(**changes: object) -> evaluate.CandidateRecord:
    return replace(evaluate.CandidateRecord(candidate_id="c1", setup="orb", side="buy",
                                            entry=4300.0), **changes)


# --- no look-ahead ----------------------------------------------------------------------
@pytest.mark.usefixtures("buying")
def test_bar_record_does_not_change_when_later_bars_are_appended(bars: data.BarSet) -> None:
    settings = synth.replay_settings()
    chosen = [b for b in bars.bars("M15") if EVAL_WINDOW.contains(b.t + M15)][44:60:3]
    for bar in chosen:
        as_of = bar.t + M15
        full = evaluate.evaluate_bar(bar, bars, (), settings, synth.Exposure())
        cut = evaluate.evaluate_bar(bar, bars.truncated(as_of), (), settings, synth.Exposure())
        assert full == cut
        assert full.evaluated


@pytest.mark.usefixtures("buying")
def test_replay_prefix_is_stable_under_appended_history(bars: data.BarSet) -> None:
    settings = synth.replay_settings()
    end = rf.EVAL_DAY_START + 14 * 3600
    short = runner.run_replay(bars.truncated(end), (), settings, EVAL_WINDOW)
    longer = runner.run_replay(bars, (), settings, EVAL_WINDOW)
    assert short == longer[:len(short)]
    assert any(record.traded for record in short)


def test_cut_reader_never_returns_an_unclosed_bar(bars: data.BarSet) -> None:
    as_of = rf.EVAL_DAY_START + 3600
    reader = synth.CutReader(bars, as_of)
    for tf, step in (("M5", 300), ("M15", 900), ("H1", 3600), ("D1", 86_400)):
        latest = reader.latest(tf, 5000)
        assert latest and latest[-1].t + step <= as_of
    assert bars.closed_by("M5", as_of)[-1].t == as_of - 300


# --- simulation --------------------------------------------------------------------------
@pytest.mark.usefixtures("buying")
def test_simulation_occupies_the_slot_and_caps_trades_per_day(bars: data.BarSet) -> None:
    settings = synth.replay_settings({"max_trades_per_day": "2"})
    records = runner.run_replay(bars, (), settings, EVAL_WINDOW)
    trades = [r for r in records if r.traded]
    assert len(trades) == 2
    first = trades[0]
    barrier = first.as_of + settings.time_barrier_s
    held = [r for r in records if first.as_of < r.as_of < barrier]
    assert held and all(r.open_position and GATE_OCCUPANCY in r.failed_gates for r in held)
    later = [r for r in records if r.as_of > trades[1].as_of + settings.time_barrier_s]
    assert any(GATE_TRADES_TODAY in r.failed_gates for r in later)
    assert all(r.packet_if_flat for r in later if r.failed_gates == (GATE_TRADES_TODAY,))


def test_trades_reset_on_a_new_utc_day() -> None:
    exposure = synth.Exposure(trades_today=3)
    same = runner._for_day(exposure, rf.EVAL_DAY_START + 60, rf.EVAL_DAY_START + 960)
    assert same.trades_today == 3
    assert runner._for_day(exposure, rf.EVAL_DAY_START - 60, rf.EVAL_DAY_START).trades_today == 0
    assert runner._for_day(exposure, None, rf.EVAL_DAY_START) == exposure


def test_advance_ignores_bars_without_a_trade() -> None:
    settings = synth.replay_settings()
    exposure = synth.Exposure()
    assert runner.advance(exposure, _record(failed_gates=(GATE_SESSION,),
                                            candidates=(_item(in_packet=True),)),
                          settings) == exposure
    moved = runner.advance(exposure, _record(candidates=(_item(in_packet=True),)), settings)
    assert moved.trades_today == 1 and moved.side == "buy" and moved.entry == 4300.0


def test_without_candidates_safe_bars_still_get_a_packet(
        bars: data.BarSet, monkeypatch: pytest.MonkeyPatch) -> None:
    """The agent may design its own entry: a packet does not need a suggestion."""
    monkeypatch.setattr(evaluate, "detect_all", rf.no_detector)
    records = runner.run_replay(bars, (), synth.replay_settings(), EVAL_WINDOW)
    assert records and not any(r.candidates or r.suggestion_if_flat or r.traded
                               for r in records)
    assert all(r.packet_if_flat == r.gates_pass_if_flat for r in records)
    assert any(r.packet_if_flat for r in records)
    assert tally.tally(records)["packet_refusals"] == {}


# --- data sufficiency ---------------------------------------------------------------------
def test_short_history_is_reported_not_dropped() -> None:
    short = rf.history(days=3)
    records = runner.run_replay(short, (), synth.replay_settings(),
                                runner.ReplayWindow(end=DEFAULT_START_EPOCH + 2 * rf.DAY))
    assert records and all(not r.evaluated for r in records)
    assert all(evaluate.INSUFFICIENT_M15 in r.insufficient for r in records)
    assert evaluate.INSUFFICIENT_M5 in records[0].insufficient
    counts = tally.tally(records)
    assert counts["insufficient_bars"] == len(records) and counts["evaluated"] == 0


def test_missing_m5_bar_at_the_decision_is_insufficient(bars: data.BarSet) -> None:
    as_of = rf.EVAL_DAY_START + 12 * 3600
    series = dict(bars.series)
    series["M5"] = tuple(b for b in series["M5"] if b.t != as_of - 300)
    reasons = evaluate.insufficiency(data.BarSet(series=series), as_of)
    assert reasons == (evaluate.INSUFFICIENT_M5_GAP,)
    assert evaluate.insufficiency(bars, as_of) == ()


# --- CSV and SQLite ---------------------------------------------------------------------------
def test_csv_rows_are_validated_sorted_and_deduplicated(tmp_path: Path) -> None:
    good = (Bar(t=900, o=10, h=11, l=9, c=10.5, tv=3, spr=20),
            Bar(t=0, o=10, h=11, l=9, c=10, tv=3, spr=20))
    rf.write_csv(tmp_path, {"M15": good}, extra=(
        "1800,10,9,8,10,1,20",           # high below open
        "2700,abc,11,9,10,1,20",         # unparsable
        "1000,10,11,9,10,1,20",          # off the M15 grid
        "900,10,12,9,11,4,21",           # duplicate time: the last one wins
    ))
    loaded = data.load_csv_dir(tmp_path, "XAUUSD")
    assert [b.t for b in loaded.bars("M15")] == [0, 900]
    assert loaded.bars("M15")[1].h == 12
    assert loaded.dropped["M15"] == 3
    assert loaded.source == data.SOURCE_CSV
    assert loaded.coverage()["M15"] == {"bars": 2, "first_t": 0, "last_t": 900}


def test_csv_problems_are_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        data.load_csv_dir(tmp_path / "missing", "XAUUSD")
    with pytest.raises(ValueError, match="no XAUUSD"):
        data.load_csv_dir(tmp_path, "XAUUSD")
    (tmp_path / "XAUUSD_M5.csv").write_text("t,o,h\n1,2,3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing columns"):
        data.load_csv_dir(tmp_path, "XAUUSD")


def test_sqlite_bars_and_stored_events(tmp_path: Path) -> None:
    event = {"event_id": 7, "time_epoch": rf.EVAL_DAY_START + 45_000, "currency": "USD",
             "importance": "HIGH", "code": "cpi-yy", "name": "CPI", "actual": None,
             "forecast": 2.9, "previous": 2.8}
    payloads = (json.dumps({"calendar": [event, {"event_id": -1}]}),
                json.dumps({"calendar": [event]}), "not json", json.dumps({"calendar": 5}))
    db = tmp_path / "ledger.db"
    rf.write_ledger(db, {"M15": rf.history_series(1)["M15"]}, payloads)
    loaded = data.load_sqlite(db)
    assert len(loaded.bars("M15")) == 96 and loaded.bars("M5") == ()
    events = data.load_stored_events(db)
    assert [e.event_id for e in events] == [7]


def test_sqlite_problems(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        data.load_sqlite(tmp_path / "none.db")
    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")
    with pytest.raises(ValueError, match="no v6_bars"):
        data.load_sqlite(empty)
    assert data.load_stored_events(empty) == ()


# --- settings and synthesis -------------------------------------------------------------------
def test_overrides_accept_field_and_env_names() -> None:
    parsed = synth.parse_overrides(["V6_MAX_SPREAD_POINTS=30", "tp_r_multiple = 1.5",
                                    "V6_OPERATOR_AGENTS=codex"])
    assert parsed == {"max_spread_points": "30", "tp_r_multiple": "1.5",
                      "operator_agents_csv": "codex"}
    settings = synth.replay_settings(parsed)
    assert settings.effective_max_spread_points == 30 and settings.tp_r_multiple == 1.5
    assert settings.operator_agents == ("codex",)


@pytest.mark.parametrize("pair", ["NOPE=1", "V6_OPERATOR_TOKEN=x", "ea_hmac_key=y", "MAX_LOTS"])
def test_bad_overrides_are_refused(pair: str) -> None:
    with pytest.raises(ValueError):
        synth.parse_overrides([pair])


def test_settings_ignore_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V6_SIZING_EQUITY_BASIS_USD", "123")
    monkeypatch.setenv("V6_MAX_SPREAD_POINTS", "10")
    settings = synth.replay_settings()
    assert settings.sizing_equity_basis_usd == 5000.0 and settings.max_spread_points is None
    assert settings.backend == "operator" and settings.mode == "shadow"
    view = synth.settings_view(settings)
    assert "operator_token" not in view and "ea_hmac_key" not in view


def test_snapshot_carries_exposure_calendar_and_spread_fallback() -> None:
    bar = Bar(t=rf.EVAL_DAY_START, o=4300, h=4301, l=4299, c=4300.5, tv=10, spr=0)
    stored = synth.STATIC_BLOCKS[:1]
    exposure = synth.Exposure(trades_today=2, open_until=bar.t + 2 * M15, entry=4290.0)
    snapshot = synth.synth_snapshot(bar, stored, exposure)
    assert snapshot.quote.spread_points == synth.FALLBACK_SPREAD_POINTS
    assert snapshot.quote.ask == pytest.approx(4300.77)
    assert len(snapshot.positions) == 1 and snapshot.day.trades_today == 2
    assert synth.synth_snapshot(bar, stored, synth.Exposure()).positions == []
    index = next(i for i, block in enumerate(synth.STATIC_BLOCKS)
                 if block.code == "fomc-statement")
    statement = synth.STATIC_BLOCKS[index]
    listed = [item["event_id"] for item in synth.calendar_block((statement,),
                                                                statement.time_epoch)]
    # the stored statement is not listed twice; the press conference 30 min later is added
    assert listed == [statement.event_id, synth.STATIC_BLOCKS[index + 1].event_id]


# --- counts and rendering ---------------------------------------------------------------------
def test_tally_counts_on_hand_made_records() -> None:
    traded = _record(candidates=(_item(in_packet=True, offered=True),
                                 _item(candidate_id="c2", setup="retest", offered=True,
                                       sizing_codes=("MIN_LOT_WALL",))))
    flat_only = _record(bar_t=traded.bar_t + M15, failed_gates=(GATE_OCCUPANCY,),
                        candidates=(_item(in_packet=True),))
    refused = _record(bar_t=traded.bar_t + 2 * M15, packet_refusal="PACKET_NO_SIZED_CANDIDATE",
                      candidates=(_item(exit_codes=("STOP_BELOW_FLOOR",)),
                                  _item(candidate_id="c3", offered=True)))
    blocked = _record(bar_t=traded.bar_t - 6 * 3600, failed_gates=(GATE_SESSION, GATE_OCCUPANCY))
    missing = _record(bar_t=traded.bar_t - 12 * 3600, insufficient=("M5_HISTORY",))
    summary = tally.summarise([missing, blocked, traded, flat_only, refused])
    total = summary["total"]
    assert (total["bars"], total["evaluated"], total["gates_pass"]) == (5, 4, 2)
    # Packets: every flat-safe bar (traded, flat_only, refused); suggestions: two of them.
    assert (total["packets_if_flat"], total["trades"], total["offerable"]) == (3, 1, 1)
    assert total["suggestions_if_flat"] == 2
    assert total["gate_fail"] == {"OCCUPANCY": 2, "SESSION": 1}
    assert total["first_fail"] == {"OCCUPANCY": 1, "SESSION": 1}
    assert total["candidates"] == {"orb": 4, "retest": 1}
    assert total["candidates_gates_pass"] == {"orb": 3, "retest": 1}
    assert total["offerable_by_setup"] == {"orb": 1}
    assert total["exit_refusals"] == {"STOP_BELOW_FLOOR": 1}
    assert total["sizing_refusals"] == {"MIN_LOT_WALL": 1}
    assert total["packet_refusals"] == {"PACKET_NO_SIZED_CANDIDATE": 1}
    assert total["insufficient"] == {"M5_HISTORY": 1}
    day = summary["days"][rf.EVAL_DAY]
    assert day["bands"]["11-17"]["trades"] == 1 and day["bands"]["00-07"]["evaluated"] == 1
    assert summary["core_07_20"]["bars"] == 3 and summary["bands"]["20-24"]["bars"] == 0
    view = tally.record_view(traded)
    assert view["traded"] and view["band"] == "11-17" and view["candidates"][1]["setup"] == "retest"


def test_bands_follow_the_bar_close() -> None:
    edges = {6 * 3600 + 1800: "00-07", 7 * 3600 - M15: "07-11", 19 * 3600 + 2700: "20-24",
             17 * 3600: "17-20", 23 * 3600 + 2700: "00-07"}
    for offset, band in edges.items():
        assert _record(bar_t=rf.EVAL_DAY_START + offset).band == band


def test_markdown_lists_every_section() -> None:
    summary = tally.summarise([_record(candidates=(_item(in_packet=True),))])
    meta = {"source": "test", "first_close": "a", "last_close": "b", "overrides": ""}
    text = render.render_markdown(summary, meta, evaluate.gate_order())
    for title in ("Per UTC day", "Per UTC hour band", "Gate failures", "Candidates by setup",
                  "Exit-plan refusals", "Sizing refusals", "Simulated trades"):
        assert title in text
    assert "| orb | 1 | 1 | 1 |" in text and "overrides: none" in text


# --- command line -----------------------------------------------------------------------------
def _main(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    return v6_replay.main(argv, stdout=out, stderr=err), out.getvalue(), err.getvalue()


@pytest.mark.usefixtures("buying")
def test_cli_runs_on_a_ledger_and_writes_json(tmp_path: Path) -> None:
    db = tmp_path / "ledger.db"
    rf.write_ledger(db, rf.history_series())
    out_json = tmp_path / "out" / "replay.json"
    code, out, err = _main(["--db", str(db), "--from", rf.EVAL_DAY, "--to", rf.EVAL_DAY,
                            "--set", "V6_MAX_TRADES_PER_DAY=3", "--json", str(out_json)])
    assert code == v6_replay.EXIT_OK and err == ""
    assert out.startswith("# V6 tier-0 replay") and "V6_MAX_TRADES_PER_DAY=3" in out
    document = json.loads(out_json.read_text(encoding="utf-8"))
    assert document["schema"] == v6_replay.SCHEMA
    assert document["settings"]["max_trades_per_day"] == 3
    assert document["summary"]["total"]["bars"] == 96 == len(document["records"])
    assert document["summary"]["total"]["trades"] == 3
    assert document["meta"]["assumptions"]


def test_cli_reads_csv_and_can_skip_records(tmp_path: Path) -> None:
    rf.write_csv(tmp_path / "csv", rf.history_series())
    events = tmp_path / "events.db"
    rf.write_ledger(events, {})
    out_json = tmp_path / "r.json"
    code, out, _ = _main(["--csv-dir", str(tmp_path / "csv"), "--events-db",
                          str(tmp_path / "missing.db"), "--from", rf.EVAL_DAY])
    assert code == v6_replay.EXIT_USAGE
    code, out, _ = _main(["--csv-dir", str(tmp_path / "csv"), "--events-db", str(events),
                          "--from", rf.EVAL_DAY, "--to", rf.EVAL_DAY, "--json", str(out_json),
                          "--no-records"])
    assert code == v6_replay.EXIT_OK and "source: csv" in out
    assert "records" not in json.loads(out_json.read_text(encoding="utf-8"))


@pytest.mark.parametrize("argv", [["--set", "V6_TP_R_MULTIPLE=9"], ["--set", "BOGUS=1"],
                                  ["--db", "does-not-exist.db"], ["--from", "2026-13-01"]])
def test_cli_usage_problems_exit_2(argv: list[str]) -> None:
    code, out, err = _main(argv)
    assert code == v6_replay.EXIT_USAGE and out == "" and err.startswith("v6_replay:")
