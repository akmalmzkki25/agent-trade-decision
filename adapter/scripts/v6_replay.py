#!/usr/bin/env python3
"""
V6 tier-0 replay: how many packets would reach the operator agent, and what stops the rest.

    python scripts/v6_replay.py [--db trade_ledger.db | --csv-dir DIR [--symbol XAUUSD]]
                                [--events-db DB] [--from YYYY-MM-DD] [--to YYYY-MM-DD]
                                [--set KEY=VALUE ...] [--json OUT.json] [--no-records]

Every closed M15 bar is rebuilt as the live engine would see it at its close,
from bars closed by then only, and run through the real tier-0 modules (see
scripts/v6replay). Settings come from explicit values only (never adapter/.env):
backend operator, mode shadow, V6_SIZING_EQUITY_BASIS_USD=5000, plus --set
overrides such as `--set V6_MAX_SPREAD_POINTS=30`. The ledger is opened
read-only. Prints a markdown summary; --json writes the full counts and the
per-bar records. Exit codes: 0 ok, 2 usage or input problem.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Final, TextIO

ADAPTER_DIR: Final[Path] = Path(__file__).resolve().parents[1]
SCRIPTS_DIR: Final[Path] = Path(__file__).resolve().parent
for _path in (str(ADAPTER_DIR), str(SCRIPTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from pydantic import ValidationError  # noqa: E402

from v6replay.data import BarSet, load_csv_dir, load_sqlite, load_stored_events  # noqa: E402
from v6replay.evaluate import gate_order  # noqa: E402
from v6replay.render import render_markdown  # noqa: E402
from v6replay.runner import ReplayWindow, run_replay  # noqa: E402
from v6replay.synth import (  # noqa: E402
    ASSUMPTIONS, parse_overrides, replay_settings, settings_view,
)
from v6replay.tally import record_view, summarise  # noqa: E402

EXIT_OK: Final[int] = 0
EXIT_USAGE: Final[int] = 2
DEFAULT_DB: Final[Path] = ADAPTER_DIR / "trade_ledger.db"
SECONDS_PER_DAY: Final[int] = 86_400
SCHEMA: Final[str] = "v6.replay.1"


def _day_epoch(text: str) -> int:
    day = date.fromisoformat(text)
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="V6 tier-0 replay over stored bars.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--db", type=Path, default=None, help="ledger (read-only)")
    source.add_argument("--csv-dir", type=Path, default=None,
                        help="folder with QlipV6_ExportBars <SYMBOL>_<TF>.csv files")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--events-db", type=Path, default=None,
                        help="ledger whose v6_snapshots calendar events are used "
                             "(default: --db, or the adapter ledger if it exists)")
    parser.add_argument("--from", dest="start", default=None, help="first UTC day (bar close)")
    parser.add_argument("--to", dest="end", default=None, help="last UTC day, inclusive")
    parser.add_argument("--set", dest="overrides", action="append", default=[],
                        metavar="KEY=VALUE", help="V6 setting override (repeatable)")
    parser.add_argument("--json", dest="json_path", type=Path, default=None)
    parser.add_argument("--no-records", action="store_true",
                        help="leave the per-bar records out of the JSON")
    return parser


def _window(args: argparse.Namespace) -> ReplayWindow:
    start = None if args.start is None else _day_epoch(args.start)
    end = None if args.end is None else _day_epoch(args.end) + SECONDS_PER_DAY
    return ReplayWindow(start=start, end=end)


def _load(args: argparse.Namespace) -> tuple[BarSet, tuple[Any, ...], str]:
    if args.csv_dir is not None:
        bars = load_csv_dir(args.csv_dir, args.symbol)
        label = f"csv {args.csv_dir}"
    else:
        db = args.db or DEFAULT_DB
        bars = load_sqlite(db)
        label = f"sqlite {db.name}"
    events_db = args.events_db or args.db or (DEFAULT_DB if DEFAULT_DB.is_file() else None)
    events = () if events_db is None else load_stored_events(events_db)
    return bars, events, label


def _iso(epoch: int | None) -> str:
    if epoch is None:
        return "-"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _meta(args: argparse.Namespace, label: str, bars: BarSet, records: Sequence[Any],
          events: Sequence[Any]) -> dict[str, Any]:
    return {
        "source": label, "overrides": ", ".join(args.overrides),
        "first_close": _iso(records[0].as_of if records else None),
        "last_close": _iso(records[-1].as_of if records else None),
        "coverage": bars.coverage(), "dropped_rows": dict(bars.dropped),
        "stored_calendar_events": len(events), "assumptions": list(ASSUMPTIONS),
    }


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=1, sort_keys=False), encoding="utf-8")


def main(argv: Sequence[str] | None = None, stdout: TextIO = sys.stdout,
         stderr: TextIO = sys.stderr) -> int:
    logging.basicConfig(level=logging.ERROR)
    args = build_parser().parse_args(argv)
    try:
        overrides = parse_overrides(args.overrides)
        settings = replay_settings(overrides)
        window = _window(args)
        bars, events, label = _load(args)
    except (ValueError, OSError, ValidationError) as exc:
        stderr.write(f"v6_replay: {type(exc).__name__}: {str(exc)[:300]}\n")
        return EXIT_USAGE
    records = run_replay(bars, events, settings, window)
    summary = summarise(records)
    meta = _meta(args, label, bars, records, events)
    stdout.write(render_markdown(summary, meta, gate_order()) + "\n")
    if args.json_path is not None:
        document = {"schema": SCHEMA, "meta": meta, "settings": settings_view(settings),
                    "summary": summary}
        if not args.no_records:
            document["records"] = [record_view(record) for record in records]
        _write_json(args.json_path, document)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
