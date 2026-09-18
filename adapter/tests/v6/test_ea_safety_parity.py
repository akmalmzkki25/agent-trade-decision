"""
The V6 EA's routes, reasons and section 8 safety rules against the wire contract.

Demo-only, contract reasons and check order, no break-even/trailing/partial close, a
hard 0.01 lot cap, a durable outbox that only drops refused payloads, market orders
that cannot slip past the drift the adapter sized them for, and small files.
"""

from __future__ import annotations

import re
from typing import get_args

from app.v6 import wire
from app.v6.risk import limits
from app.v6.schemas.intent import ExecutionReport

from .ea_source_fixtures_v6 import (
    CONTRACT_DOC, EA_DIR, EA_MAIN, FORBIDDEN_TRADE_CALLS, MAX_FUNCTION_LINES, MODIFY_ACTIONS,
    MAX_INCLUDE_LINES, MAX_MAIN_LINES, PRINTERS, all_files, all_sources, define_string,
    ea_defines, function_body, include, ea_inputs, read,
)


# --- routes, reasons and statuses -------------------------------------------------
def test_ea_routes_are_contract_routes() -> None:
    doc = read(CONTRACT_DOC)
    paths = set(re.findall(r'"(/v6/[a-z/_-]+)"', all_sources()))

    assert paths == {"/v6/bars/backfill", "/v6/snapshot", "/v6/intent/poll", "/v6/action",
                     "/v6/execution", "/v6/basket-result"}
    for path in paths:
        assert f"`{path}`" in doc, path


def _prefixed_defines(prefix: str) -> dict[str, str]:
    return {name: define_string(name) for name in ea_defines() if name.startswith(prefix)}


def test_execution_statuses_and_reasons_are_contract_values() -> None:
    reasons = set(_prefixed_defines("REASON_").values())
    statuses = set(_prefixed_defines("STATUS_").values())

    assert reasons == set(get_args(ExecutionReport.model_fields["reason_code"].annotation))
    assert statuses == set(get_args(ExecutionReport.model_fields["status"].annotation))


def test_every_refusal_reason_is_used_and_duplicate_stays_reserved() -> None:
    code = "\n".join(read(path) for path in all_files() if path.name != "Report.mqh")
    unused = sorted(name for name in _prefixed_defines("REASON_")
                    if not re.search(rf"\b{name}\b", code))

    assert unused == ["REASON_DUPLICATE"]


def test_refusals_follow_the_contract_check_order() -> None:
    body = function_body(include("Execute.mqh"), "string EntryRefusal(")
    order = ["REASON_EXECUTE_DISABLED", "REASON_DEMO_REQUIRED", "REASON_BAD_INTENT", "REASON_NO_SL",
             "REASON_EXPIRED", "REASON_HALTED", "REASON_BREAKER", "REASON_OCCUPIED",
             "REASON_LOT_CAP", "MarketRefusal("]
    positions = [body.index(token) for token in order]

    assert positions == sorted(positions)
    market = function_body(include("Execute.mqh"), "string MarketRefusal(")
    tail = ["REASON_SPREAD", "REASON_DRIFT", "REASON_MARKET_CLOSED", "REASON_RISK_CAP"]
    assert [market.index(t) for t in tail] == sorted(market.index(t) for t in tail)


def test_contract_doc_lists_the_canonical_fields_in_order() -> None:
    doc = read(CONTRACT_DOC)
    section = doc[doc.index("## 4. Intent signature"):doc.index("## 5. Golden vectors")]
    rows = re.findall(r"^\|\s*(\d+)\s*\|\s*`(\w+)`\s*\|", section, re.M)

    assert [name for _, name in rows] == list(wire.CANONICAL_FIELDS)


# --- safety rules -------------------------------------------------------------------
def test_demo_only_is_compiled_in() -> None:
    execute = include("Execute.mqh")
    orders = include("Orders.mqh")

    assert "ACCOUNT_TRADE_MODE_DEMO" in function_body(orders, "bool AccountIsDemo(")
    refusal = function_body(execute, "string EntryRefusal(")
    assert "p.require_demo != REQUIRE_DEMO" in refusal and "!AccountIsDemo()" in refusal
    assert "!AccountIsDemo()" in function_body(execute, "void SendEntry(")
    assert "AccountIsDemo()" in function_body(orders, "bool ExecutionReady(")
    assert not [name for name in ea_inputs() if re.search("demo|real|contest", name, re.I)]


def test_no_partial_close_and_modifications_only_in_orders() -> None:
    offenders = [(path.name, call) for path in all_files() for call in FORBIDDEN_TRADE_CALLS
                 if re.search(rf"\b{re.escape(call)}\b", read(path))]
    modifiers = sorted({path.name for path in all_files() for action in MODIFY_ACTIONS
                        if re.search(rf"\b{action}\b", read(path))})

    assert offenders == []
    assert modifiers == ["Orders.mqh"]
    orders = include("Orders.mqh")
    for name in ("bool ModifyPositionStops(", "bool ModifyPendingOrder("):
        body = function_body(orders, name)
        assert "!AccountIsDemo()" in body and "req.volume" not in body
    close = function_body(include("Orders.mqh"), "bool ClosePositionByTicket(")
    assert "req.volume = PositionGetDouble(POSITION_VOLUME);" in close
    assert "req.position = ticket;" in close


def test_order_send_lives_in_one_module() -> None:
    users = sorted(path.name for path in all_files()
                   if re.search(r"\bOrderSend\s*\(", read(path)))

    assert users == ["Orders.mqh"]


def test_limits_and_defaults_match_the_contract() -> None:
    defines, inputs, doc = ea_defines(), ea_inputs(), read(CONTRACT_DOC)

    assert float(defines["V6_MAX_EXECUTE_LOTS"]) == limits.MAX_EXECUTE_LOTS
    assert float(defines["V6_MAX_BREAKER_PCT"]) == limits.MAX_DAILY_LOSS_PCT
    assert int(defines["MAX_TIME_BARRIER_S"]) == limits.MAX_TIME_BARRIER_S
    assert (int(defines["V6_MAGIC_FIRST"]), int(defines["V6_MAGIC_LAST"])) == (
        limits.V6_MAGIC_FIRST, limits.V6_MAGIC_LAST)
    assert float(inputs["InpMaxLots"]) == limits.MAX_EXECUTE_LOTS
    assert float(inputs["InpMaxRiskUsd"]) == float(defines["V6_MAX_RISK_USD_CEILING"]) == 50.0
    assert "`InpMaxRiskUsd` (default 50.0" in doc
    assert float(inputs["InpDailyBreakerPct"]) == limits.MAX_DAILY_LOSS_PCT
    assert inputs["InpExecute"] == "true"
    assert inputs["InpBackfillDaysM5"] == "5"
    assert inputs["InpFlattenServerTime"] == '"22:55"'
    assert inputs["InpHmacKeyFile"] == r'"QlipV6\\hmac.key"'
    assert inputs["InpMagic"] == str(limits.V6_MAGIC_FIRST)
    assert int(defines["MIN_PENDING_LIFETIME_S"]) == 60


def test_file_locations_match_the_contract() -> None:
    doc = read(CONTRACT_DOC)

    assert define_string("OUTBOX_FILE") == "QlipV6\\outbox.jsonl"
    assert r"MQL5\Files\QlipV6\outbox.jsonl" in doc
    assert r"MQL5\Files\QlipV6\hmac.key" in doc


def test_intent_identity_matches_the_schema() -> None:
    assert define_string("INTENT_SCHEMA") == "v6.intent.2"
    assert define_string("ORDER_COMMENT_PREFIX") == "Q6:"
    assert int(ea_defines()["INTENT_ID_CHARS"]) == 12
    body = function_body(include("Intent.mqh"), "bool IsBase32Char(")
    assert "'a'" in body and "'z'" in body and "'2'" in body and "'7'" in body


def test_the_key_is_never_printed() -> None:
    leaks = []
    for path in all_files():
        for line in read(path).splitlines():
            printing = any(re.search(rf"\b{p}\s*\(", line) for p in PRINTERS)
            if printing and re.search(r"g_hmac_key\b|\bkey_text\b|\braw_key\b", line):
                leaks.append((path.name, line.strip()))

    assert leaks == []


def test_snapshot_and_poll_report_the_ea_state() -> None:
    snapshot = include("Snapshot.mqh")
    state = function_body(snapshot, "string EaStateJson(")
    poll = function_body(snapshot, "string BuildPollJson(")

    assert 'AddBool("execute_enabled", status.execute_enabled)' in state
    assert 'AddStr("local_breaker", status.breaker_tripped ? "daily" : "none")' in state
    assert 'AddInt("outbox_pending", status.outbox_pending)' in state
    assert 'AddStr("last_intent_id", status.last_intent_id)' in state
    assert 'AddStr("last_intent_id", status.last_intent_id)' in poll
    assert 'AddBool("local_halt", status.halted)' in poll


def test_management_runs_before_any_http_call() -> None:
    timer = function_body(read(EA_MAIN), "void OnTimer(")
    manage = include("Manage.mqh")

    assert timer.index("ManageTick()") < min(
        timer.index(call) for call in ("ServiceSnapshot()", "ServicePoll(", "g_outbox.Service()"))
    assert "WebRequest" not in manage and "HttpPostJson" not in manage
    transaction = function_body(read(EA_MAIN), "void OnTradeTransaction(")
    assert [line.strip() for line in transaction.strip().splitlines()] == ["g_trade_event = true;"]


def test_the_outbox_keeps_entries_the_adapter_may_accept_later() -> None:
    outbox = include("Outbox.mqh")
    retryable = function_body(outbox, "bool OutboxIsRetryable(")
    kept = {name: int(ea_defines()[name]) for name in (
        "HTTP_UNAUTHORIZED", "HTTP_NOT_FOUND", "HTTP_REQUEST_TIMEOUT", "HTTP_CONFLICT",
        "HTTP_TOO_MANY_REQUESTS")}

    assert set(kept.values()) == {401, 404, 408, 409, 429}
    assert "HttpIsRetryable(status)" in retryable
    assert all(f"status == {name}" in retryable for name in kept)
    service = function_body(outbox, "void COutbox::Service(")
    assert "OutboxIsRetryable(result.status)" in service and "HttpIsRetryable" not in service


def test_a_market_order_cannot_slip_past_its_drift_budget() -> None:
    checks, execute = include("Checks.mqh"), include("Execute.mqh")
    deviation = function_body(checks, "long MarketDeviationPoints(")
    within = function_body(checks, "bool WithinDrift(")

    assert "p.max_drift_points - QuoteDriftPoints(p, q)" in deviation
    assert "return MarketDeviationPoints(p, q) > 0;" in within
    request = function_body(execute, "void BuildEntryRequest(")
    assert ("req.deviation = (ulong)(pending ? p.max_drift_points : "
            "MarketDeviationPoints(p, q));") in request
    assert "FillDriftBreach(p, r, q.point)" in function_body(execute, "void AcceptEntry(")
    breach = function_body(execute, "string FillDriftBreach(")
    assert "DriftPoints(p, r.fill_price, point)" in breach


# --- size rules ------------------------------------------------------------------------
def _long_functions(text: str) -> list[tuple[str, int]]:
    """Top-level functions (brace on its own line at column 0) longer than the limit."""
    lines = text.splitlines()
    found = []
    for index, line in enumerate(lines):
        if line != "{" or index == 0 or re.match(r"(struct|class|enum)\b", lines[index - 1]):
            continue
        end = lines.index("}", index)
        if end - index - 1 >= MAX_FUNCTION_LINES:
            found.append((lines[index - 1].strip(), end - index - 1))
    return found


def test_ea_files_and_functions_stay_small() -> None:
    assert len(read(EA_MAIN).splitlines()) <= MAX_MAIN_LINES
    oversized = [path.name for path in EA_DIR.glob("*.mqh")
                 if len(read(path).splitlines()) > MAX_INCLUDE_LINES]
    assert oversized == []
    long_functions = [(path.name, header, size) for path in all_files()
                      for header, size in _long_functions(read(path))]
    assert long_functions == []
