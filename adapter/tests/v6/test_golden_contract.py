"""
Keeps the V6 EA and the adapter wire contract in sync.

`golden/*.json` hold exactly the shape `ea/QlipV6_XAUUSD.mq5` emits. They are
validated with `model_validate_json`, the same path the raw request body takes,
and the EA sources are scanned so a renamed field on either side fails here
instead of on the first live M15 close.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from types import UnionType
from typing import Any, Final, Literal, Union, get_args, get_origin

import pytest
from pydantic import BaseModel, ValidationError

from app.v6.schemas.intent import PollRequest
from app.v6.schemas.operator_parts import M15_PACKET_M1_BARS
from app.v6.schemas.snapshot import BackfillRequest, V6Snapshot
from app.v6.types import TIMEFRAME_SECONDS

GOLDEN_DIR: Final[Path] = Path(__file__).resolve().parent / "golden"
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
EA_MAIN: Final[Path] = REPO_ROOT / "ea" / "QlipV6_XAUUSD.mq5"
EA_INCLUDE_DIR: Final[Path] = REPO_ROOT / "ea" / "QlipV6"
PROBE_SCRIPT: Final[Path] = REPO_ROOT / "ea" / "Scripts" / "QlipV6_Probe.mq5"

# Bars the EA puts in every snapshot, per timeframe (plan §5).
EXPECTED_BAR_COUNTS: Final[dict[str, int]] = {"M1": 30, "M5": 48, "M15": 16, "H1": 8, "D1": 3}
MAX_EA_MAIN_LINES: Final[int] = 300
MAX_INCLUDE_LINES: Final[int] = 400
# The probe is a standalone, self-contained script; plan section 5 caps EA files at 800.
MAX_PROBE_LINES: Final[int] = 800
TRADING_CALLS: Final[tuple[str, ...]] = (
    "OrderSend", "OrderSendAsync", "CTrade", "Trade.mqh", "PositionClose", "OrderModify",
)
ORDERS_MODULE: Final[str] = "Orders.mqh"


def _read_golden(name: str) -> str:
    return (GOLDEN_DIR / name).read_text(encoding="utf-8")


def _ea_sources() -> str:
    parts = [EA_MAIN.read_text(encoding="utf-8")]
    parts.extend(p.read_text(encoding="utf-8") for p in sorted(EA_INCLUDE_DIR.glob("*.mqh")))
    return "\n".join(parts)


def _model_field_names(model: type[BaseModel]) -> set[str]:
    """Every field name in `model` and in the nested models it references."""
    names: set[str] = set()
    for name, info in model.model_fields.items():
        names.add(name)
        for arg in _nested_models(info.annotation):
            names |= _model_field_names(arg)
    return names


def _nested_models(annotation: Any) -> list[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    found: list[type[BaseModel]] = []
    for arg in getattr(annotation, "__args__", ()):
        found.extend(_nested_models(arg))
    return found


def _with_field(raw: str, dotted: str, value: Any) -> str:
    """Return a new JSON text with one field replaced; the golden file is untouched."""
    data = json.loads(raw)
    target = data
    *parents, leaf = dotted.split(".")
    for key in parents:
        target = target[key]
    target[leaf] = value
    return json.dumps(data)


# --- golden files validate --------------------------------------------------


def test_snapshot_golden_validates_against_v6_snapshot() -> None:
    snapshot = V6Snapshot.model_validate_json(_read_golden("snapshot_sample.json"))

    assert snapshot.schema_version == "v6.snapshot.1"
    assert snapshot.probe is not None


def test_poll_golden_validates_against_poll_request() -> None:
    poll = PollRequest.model_validate_json(_read_golden("poll_sample.json"))

    assert poll.schema_version == "v6.poll.1"
    assert poll.last_intent_id == ""


def test_backfill_golden_validates_against_backfill_request() -> None:
    backfill = BackfillRequest.model_validate_json(_read_golden("backfill_sample.json"))

    assert backfill.schema_version == "v6.backfill.1"
    assert len(backfill.rows) > 0


def test_snapshot_without_probe_validates() -> None:
    """Every snapshot after the first in an EA session carries `probe: null`."""
    raw = _with_field(_read_golden("snapshot_sample.json"), "probe", None)

    assert V6Snapshot.model_validate_json(raw).probe is None


# --- golden files carry exactly the EA's shape ------------------------------


@pytest.mark.parametrize(
    ("golden", "model"),
    [
        ("snapshot_sample.json", V6Snapshot),
        ("poll_sample.json", PollRequest),
        ("backfill_sample.json", BackfillRequest),
    ],
)
def test_golden_emits_every_top_level_field(golden: str, model: type[BaseModel]) -> None:
    """The EA never relies on defaults: every field is always present."""
    data = json.loads(_read_golden(golden))

    assert set(data) == set(model.model_fields)


def _snapshot_with_exposure() -> dict[str, Any]:
    """The data-only sample has no V6 exposure; rows come from their own golden file."""
    data = json.loads(_read_golden("snapshot_sample.json"))
    exposure = json.loads(_read_golden("exposure_sample.json"))
    return {**data, "positions": exposure["positions"], "pending_orders": exposure["pending_orders"]}


def test_snapshot_with_exposure_rows_validates() -> None:
    snapshot = V6Snapshot.model_validate_json(json.dumps(_snapshot_with_exposure()))

    assert len(snapshot.positions) == 1
    assert len(snapshot.pending_orders) == 1


def test_snapshot_golden_blocks_emit_every_field() -> None:
    data = _snapshot_with_exposure()
    blocks = ("account", "symbol_spec", "quote", "ticks", "day", "probe", "ea_state")

    for block in blocks:
        model = _nested_models(V6Snapshot.model_fields[block].annotation)[0]
        assert set(data[block]) == set(model.model_fields), block
    for block in ("positions", "pending_orders", "calendar"):
        model = _nested_models(V6Snapshot.model_fields[block].annotation)[0]
        assert data[block], block
        for row in data[block]:
            assert set(row) == set(model.model_fields), block


def test_snapshot_golden_bar_counts_match_the_ea() -> None:
    snapshot = V6Snapshot.model_validate_json(_read_golden("snapshot_sample.json"))

    assert {tf: len(rows) for tf, rows in snapshot.bars.items()} == EXPECTED_BAR_COUNTS


def test_the_ea_sends_the_expected_bar_counts() -> None:
    source = (EA_INCLUDE_DIR / "Snapshot.mqh").read_text(encoding="utf-8")
    counts = {tf: int(n) for tf, n in re.findall(r"#define SNAP_BARS_(\w+)\s+(\d+)", source)}

    assert counts == EXPECTED_BAR_COUNTS
    # The m15 packet's M1 window comes whole from the newest snapshot.
    assert counts["M1"] >= M15_PACKET_M1_BARS


def test_snapshot_golden_ids_and_times_are_consistent() -> None:
    snapshot = V6Snapshot.model_validate_json(_read_golden("snapshot_sample.json"))
    close_epoch = snapshot.bar_open_epoch + TIMEFRAME_SECONDS["M15"]

    assert snapshot.snapshot_id == f"Q6S-{snapshot.account.login}-{snapshot.bar_open_epoch}"
    assert snapshot.bars["M15"][-1][0] == snapshot.bar_open_epoch
    assert snapshot.bars["M1"][-1][0] + TIMEFRAME_SECONDS["M1"] == close_epoch
    assert snapshot.sent_at_epoch >= close_epoch
    assert snapshot.quote.time_msc // 1000 >= close_epoch
    assert all(event.time_epoch >= close_epoch for event in snapshot.calendar)


def test_snapshot_golden_is_data_only() -> None:
    snapshot = V6Snapshot.model_validate_json(_read_golden("snapshot_sample.json"))

    assert snapshot.ea_state.ea_version == "6.0.0"
    assert snapshot.ea_state.execute_enabled is False
    assert snapshot.ea_state.last_intent_id == ""


def test_golden_spec_matches_the_probe_findings() -> None:
    """MetaQuotes-Demo probe (2026-09-16): the server reports tick_value 0.1, but
    OrderCalcProfit and realised deals both pay $100 per 1.00 move per lot."""
    block = V6Snapshot.model_validate_json(_read_golden("snapshot_sample.json")).symbol_spec

    assert block.contract_size == 100.0
    assert block.tick_value == 0.1
    assert block.calc_profit_per_price == block.calc_loss_per_price == 100.0
    assert math.isclose(block.margin_per_lot_buy, 4535.35 * 100 / 200, rel_tol=1e-4)


def test_golden_spec_derived_tick_value_passes_the_spec_rule() -> None:
    """The sizing spec satisfies tick_value == tick_size x contract_size; the
    broker's reported value stays available and is still 10x off."""
    snapshot = V6Snapshot.model_validate_json(_read_golden("snapshot_sample.json"))
    spec = snapshot.symbol_spec.to_spec()

    assert math.isclose(spec.tick_value, spec.tick_size * spec.contract_size, rel_tol=1e-9)
    assert math.isclose(spec.tick_value_loss, spec.tick_size * spec.contract_size, rel_tol=1e-9)
    assert spec.tick_value_source == "order_calc"
    assert spec.reported_tick_value == 0.1
    assert spec.reported_tick_value_loss == 0.1


def test_ea_emits_symbol_spec_fields_in_model_order() -> None:
    """SymbolSpecJson writes the block in the order the model and golden list it."""
    source = (EA_INCLUDE_DIR / "Snapshot.mqh").read_text(encoding="utf-8")
    body = source[source.index("string SymbolSpecJson("):]
    body = body[:body.index("return o.Text();")]
    emitted = re.findall(r'\.Add(?:Str|Num|Int|Bool|Raw|Null)\(\s*"(\w+)"', body)
    golden = list(json.loads(_read_golden("snapshot_sample.json"))["symbol_spec"])
    fields = list(V6Snapshot.model_fields["symbol_spec"].annotation.model_fields)

    assert emitted == fields == golden
    assert "OrderCalcProfit(ORDER_TYPE_BUY" in source


# --- the contract stays strict -----------------------------------------------


@pytest.mark.parametrize(
    ("dotted", "value"),
    [
        ("unexpected", 1),
        ("account.login", 10000001),
        ("account.leverage", 200.0),
        ("quote.spread_points", 17.0),
        ("ea_state.ea_version", "6.0"),
        ("calendar", [{"event_id": 1}]),
    ],
)
def test_snapshot_rejects_shapes_the_ea_must_not_emit(dotted: str, value: Any) -> None:
    raw = _with_field(_read_golden("snapshot_sample.json"), dotted, value)

    with pytest.raises(ValidationError):
        V6Snapshot.model_validate_json(raw)


def test_snapshot_rejects_a_bar_row_with_float_tick_volume() -> None:
    data = json.loads(_read_golden("snapshot_sample.json"))
    row = data["bars"]["M5"][0]
    data["bars"]["M5"][0] = [*row[:5], float(row[5]), row[6]]

    with pytest.raises(ValidationError):
        V6Snapshot.model_validate_json(json.dumps(data))


def test_snapshot_rejects_a_bar_that_is_still_forming() -> None:
    data = json.loads(_read_golden("snapshot_sample.json"))
    data["bar_open_epoch"] -= TIMEFRAME_SECONDS["M15"]

    with pytest.raises(ValidationError):
        V6Snapshot.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    ("dotted", "value"),
    [("last_intent_id", "ABC"), ("login", "12a"), ("spread_points", 1.5), ("extra", True)],
)
def test_poll_rejects_shapes_the_ea_must_not_emit(dotted: str, value: Any) -> None:
    raw = _with_field(_read_golden("poll_sample.json"), dotted, value)

    with pytest.raises(ValidationError):
        PollRequest.model_validate_json(raw)


# --- the EA sources name every contract field --------------------------------


@pytest.mark.parametrize("model", [V6Snapshot, PollRequest, BackfillRequest])
def test_ea_sources_emit_every_contract_field(model: type[BaseModel]) -> None:
    source = _ea_sources()
    missing = sorted(name for name in _model_field_names(model) if f'"{name}"' not in source)

    assert missing == []


def _expected_kinds(annotation: Any) -> set[str]:
    """How the EA must write a value of this type (see ea/QlipV6/Json.mqh)."""
    origin = get_origin(annotation)
    if origin is Literal:
        return {"Str"}
    if origin in (Union, UnionType):
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if args == [float]:
            return {"OptNum"}
        return set().union(*(_expected_kinds(arg) for arg in args)) | {"Null"}
    if origin in (list, dict, tuple) or _nested_models(annotation):
        return {"Raw"}
    for kind, python_type in (("Bool", bool), ("Int", int), ("Num", float), ("Str", str)):
        if annotation is python_type:
            return {kind}
    raise AssertionError(f"no EA writer for {annotation!r}")


def _expected_field_kinds(model: type[BaseModel]) -> dict[str, set[str]]:
    kinds: dict[str, set[str]] = {}
    for name, info in model.model_fields.items():
        kinds.setdefault(name, set()).update(_expected_kinds(info.annotation))
        for nested in _nested_models(info.annotation):
            for key, value in _expected_field_kinds(nested).items():
                kinds.setdefault(key, set()).update(value)
    return kinds


def _emitted_field_kinds() -> dict[str, set[str]]:
    source = _ea_sources()
    kinds: dict[str, set[str]] = {}
    for kind, name in re.findall(r'\.Add(Str|Num|Int|Bool|Raw|Null)\(\s*"(\w+)"', source):
        kinds.setdefault(name, set()).add(kind)
    for name in re.findall(r'AddOptionalValue\(\s*\w+\s*,\s*"(\w+)"', source):
        kinds.setdefault(name, set()).add("OptNum")
    return kinds


@pytest.mark.parametrize("model", [V6Snapshot, PollRequest, BackfillRequest])
def test_ea_writes_every_field_with_the_contract_number_kind(model: type[BaseModel]) -> None:
    """An int written as 17.0 (or a float as 17) passes review but fails strict validation."""
    emitted = _emitted_field_kinds()
    wrong = {
        name: (sorted(emitted.get(name, set())), sorted(expected))
        for name, expected in _expected_field_kinds(model).items()
        if not emitted.get(name) or not emitted[name] <= expected
    }

    assert wrong == {}


def test_ea_emits_the_contract_schema_versions() -> None:
    source = _ea_sources()

    for version in ("v6.snapshot.1", "v6.backfill.1", "v6.poll.1"):
        assert f'"{version}"' in source


def test_ea_reads_the_poll_response_fields() -> None:
    source = _ea_sources()

    for field in ("command", "has_intent"):
        assert re.search(rf'JsonGet\w+\([^;]*"{field}"', source), field


def test_ea_trading_calls_live_only_in_the_orders_module() -> None:
    """Phase 5 executes on demo: only Orders.mqh sends orders (synchronous OrderSend);
    nothing uses the async, CTrade, close-by-helper or modify paths, and the probe
    script stays data-only."""
    sources = {path.name: path.read_text(encoding="utf-8") for path in
               [EA_MAIN, PROBE_SCRIPT, *sorted(EA_INCLUDE_DIR.glob("*.mqh"))]}

    offenders = [(name, call) for name, text in sources.items() for call in TRADING_CALLS
                 if re.search(rf"\b{re.escape(call)}\b", text)
                 and (name, call) != (ORDERS_MODULE, "OrderSend")]
    assert offenders == []
    assert re.search(r"\bOrderSend\b", sources[ORDERS_MODULE])


def test_ea_files_stay_within_size_limits() -> None:
    assert len(EA_MAIN.read_text(encoding="utf-8").splitlines()) <= MAX_EA_MAIN_LINES
    assert len(PROBE_SCRIPT.read_text(encoding="utf-8").splitlines()) <= MAX_PROBE_LINES
    for path in EA_INCLUDE_DIR.glob("*.mqh"):
        assert len(path.read_text(encoding="utf-8").splitlines()) <= MAX_INCLUDE_LINES, path.name
