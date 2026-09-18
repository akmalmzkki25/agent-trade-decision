"""
The V6 EA's messages and signatures against the wire contract (docs/v6-wire-contract.md).

- execution reports, basket results and the poll-reply parser name every
  model field, in model order, with the right number kind;
- the canonical intent string follows `wire.CANONICAL_FIELDS`, with prices as
  integer points and lots as integer hundredths;
- requests are signed as the contract says;
- the vectors of the OnInit self-test equal golden/hmac_vectors.json.
"""

from __future__ import annotations

import json
import re

import pytest
from pydantic import BaseModel

from app.models import BasketResultEvent
from app.v6 import wire
from app.v6.schemas.intent import ExecutionReport, PollResponse

from .ea_source_fixtures_v6 import (
    CANONICAL_SOURCE, EA_MAIN, PRICE_FIELDS, STRING_LITERAL, VECTORS, all_sources,
    define_string, ea_defines, emitted, function_body, include, named, read, reader_for,
    unescape, writer_kind,
)


# --- messages the EA writes ----------------------------------------------------
@pytest.mark.parametrize(
    ("header", "model"),
    [("string ExecutionReportJson(", ExecutionReport),
     ("string BasketResultJson(", BasketResultEvent)],
)
def test_ea_messages_emit_every_model_field_in_order(header: str, model: type[BaseModel]) -> None:
    body = function_body(all_sources(), header)

    assert [name for name, _ in emitted(body)] == list(model.model_fields)


def test_execution_report_order_matches_the_golden_request_body() -> None:
    body = json.loads(named("requests")["execution"]["body"])
    source = function_body(all_sources(), "string ExecutionReportJson(")

    assert [name for name, _ in emitted(source)] == list(body)


@pytest.mark.parametrize(
    ("header", "model"),
    [("string ExecutionReportJson(", ExecutionReport),
     ("string BasketResultJson(", BasketResultEvent)],
)
def test_ea_messages_write_contract_number_kinds(header: str, model: type[BaseModel]) -> None:
    body = function_body(all_sources(), header)
    wrong = {}
    for name, kind in emitted(body):
        expected = writer_kind(model.model_fields[name].annotation)
        # "positions" is written raw (JInt) so the snapshot kind scan keeps its array meaning.
        if kind not in expected and not (name == "positions" and kind == "Raw"):
            wrong[name] = (kind, sorted(expected))

    assert wrong == {}


def test_basket_positions_is_written_as_an_integer() -> None:
    body = function_body(all_sources(), "string BasketResultJson(")

    assert re.search(r'AddRaw\(\s*"positions"\s*,\s*JInt\(', body)


def test_basket_result_identity_matches_the_contract() -> None:
    assert define_string("BASKET_SCHEMA") == "basket-result-event.v1"
    assert define_string("BASKET_VERSION") == "v6"
    assert define_string("BASKET_ID_MARKER") == "-V6B-"
    assert define_string("PATH_BASKET_RESULT") == "/v6/basket-result"
    assert define_string("PATH_EXECUTION") == "/v6/execution"
    assert define_string("EXECUTION_SCHEMA") == "v6.execution.1"
    body = function_body(all_sources(), "string BasketResultJson(")
    assert "_Symbol + BASKET_ID_MARKER + intent_id" in body


# --- the poll reply --------------------------------------------------------------
def test_poll_reply_parser_reads_every_field_in_order_with_a_strict_getter() -> None:
    body = function_body(include("Intent.mqh"), "bool ParsePollReply(")
    calls = re.findall(r'(JsonGet\w+)\(\s*json\s*,\s*"(\w+)"', body)
    expected = [(reader_for(info.annotation), name)
                for name, info in PollResponse.model_fields.items()]

    assert calls == expected


def test_poll_reply_struct_declares_every_field() -> None:
    source = include("Intent.mqh")
    body = function_body(source, "struct PollReply")
    declared = re.findall(r"^\s*\w+\s+(\w+);", body, re.M)

    assert declared == list(PollResponse.model_fields)


def test_canonical_string_follows_wire_canonical_fields() -> None:
    body = function_body(include("Intent.mqh"), "string IntentCanonical(")
    used = re.findall(r"\bp\.(\w+)", body)
    expected = [CANONICAL_SOURCE.get(field, field) for field in wire.CANONICAL_FIELDS]

    assert used == expected
    assert define_string("CANONICAL_SEPARATOR") == wire.CANONICAL_SEPARATOR


def test_canonical_string_signs_integers_not_float_text() -> None:
    body = function_body(include("Intent.mqh"), "string IntentCanonical(")

    for field in PRICE_FIELDS:
        assert re.search(rf"PriceToPoints\(\s*p\.{field}\s*,\s*point\s*\)", body), field
    assert re.search(r"LotsToHundredths\(\s*p\.lots\s*\)", body)
    assert "DoubleToString" not in body
    assert re.search(r'p\.has_intent\s*\?\s*"1"\s*:\s*"0"', body)
    assert "MathRound(price / point)" in include("Intent.mqh")
    assert "MathRound(lots * LOTS_SCALE)" in include("Intent.mqh")
    assert float(ea_defines()["LOTS_SCALE"]) == wire.LOTS_SCALE


def test_the_reply_is_verified_before_any_command_or_intent() -> None:
    body = function_body(include("Poll.mqh"), "void HandlePollResponse(")

    trusted = body.index("ReplyTrusted(")
    assert trusted < body.index("ManageApplyCommand(")
    assert trusted < body.index("ExecuteIntent(")
    assert "PollReplySignatureOk(" in function_body(include("Poll.mqh"), "bool ReplyTrusted(")


# --- request signing -------------------------------------------------------------
def test_requests_are_signed_with_the_contract_headers() -> None:
    http = include("Http.mqh")

    assert define_string("HTTP_TS_HEADER") == wire.TS_HEADER
    assert define_string("HTTP_SIG_HEADER") == wire.SIG_HEADER
    assert define_string("HTTP_METHOD") == "POST"
    assert "SigningPayload(" in function_body(http, "string HttpHeaders(")
    assert "X-Internal-Sig" not in all_sources()


def test_signing_payload_is_ts_method_path_body() -> None:
    body = function_body(include("Hmac.mqh"), "void SigningPayload(")

    assert re.search(r'IntegerToString\(ts\)\s*\+\s*"\\n"\s*\+\s*method\s*\+\s*"\\n"\s*\+\s*'
                     r'path\s*\+\s*"\\n"', body)


def test_signed_retries_wait_for_a_new_second() -> None:
    http = include("Http.mqh")

    assert int(ea_defines()["HTTP_SIGNED_RETRY_PAUSE_MS"]) >= 1000
    assert "TimeGMT() <= last_ts" in function_body(http, "void HttpRetryPause(")


def test_hmac_uses_the_standard_construction() -> None:
    defines = ea_defines()

    assert int(defines["HMAC_BLOCK_BYTES"]) == 64
    assert int(defines["HMAC_IPAD"], 16) == 0x36
    assert int(defines["HMAC_OPAD"], 16) == 0x5C
    assert "CRYPT_HASH_SHA256" in include("Hmac.mqh")
    assert define_string("FINGERPRINT_LABEL") == wire.FINGERPRINT_LABEL.decode("ascii")
    assert int(defines["FINGERPRINT_CHARS"]) == wire.FINGERPRINT_CHARS
    assert (int(defines["KEY_MIN_CHARS"]), int(defines["KEY_MAX_CHARS"])) == (32, 256)


# --- the OnInit self-test vectors -----------------------------------------------
def test_self_test_key_and_fingerprint_match_the_golden_file() -> None:
    assert define_string("SELFTEST_KEY") == VECTORS["test_key"]
    assert define_string("SELFTEST_KEY_FINGERPRINT") == VECTORS["test_key_fingerprint"]


def test_self_test_hmac_rows_match_the_golden_file() -> None:
    pattern = (r'HmacRowOk\(\s*"([^"]+)"\s*,\s*"([0-9a-f]*)"\s*,\s*"([0-9a-f]*)"\s*,'
               r'\s*"([0-9a-f]{64})"\s*\)')
    rows = re.findall(pattern, include("SelfTest.mqh"))
    golden = [(row["name"], row["key_hex"], row["data_hex"], row["sig"])
              for row in VECTORS["hmac"]]

    assert rows == golden
    assert "rfc4231-tc2" in {row[0] for row in rows}


def test_self_test_request_rows_match_the_golden_file() -> None:
    pattern = (r'RequestRowOk\(\s*"([^"]+)"\s*,\s*(\d+)\s*,\s*"([A-Z]+)"\s*,\s*"([^"]+)"\s*,\s*'
               + STRING_LITERAL + r'\s*,\s*"([0-9a-f]{64})"\s*\)')
    rows = [(name, int(ts), method, path, unescape(body), sig)
            for name, ts, method, path, body, sig in re.findall(pattern, include("SelfTest.mqh"))]
    golden = [(row["name"], row["ts"], row["method"], row["path"], row["body"], row["sig"])
              for row in VECTORS["requests"]]

    assert rows == golden


def test_self_test_intent_rows_match_the_golden_file() -> None:
    pattern = (r'IntentRowOk\(\s*"([^"]+)"\s*,\s*' + STRING_LITERAL
               + r'\s*,\s*([0-9.]+)\s*,\s*"([^"]*)"\s*,\s*"([0-9a-f]{64})"\s*\)')
    rows = re.findall(pattern, include("SelfTest.mqh"))
    golden = named("intents")

    assert [row[0] for row in rows] == [row["name"] for row in VECTORS["intents"]]
    for name, response, point, canonical, sig in rows:
        row = golden[name]
        assert json.loads(unescape(response)) == row["response"], name
        assert float(point) == row["point"], name
        assert canonical == row["canonical"], name
        assert sig == row["sig"] == row["response"]["sig"], name


def test_self_test_runs_every_row_and_gates_execution() -> None:
    selftest = include("SelfTest.mqh")
    body = function_body(selftest, "bool HmacSelfTest(")
    groups = ("bool HmacVectorsOk(", "bool RequestVectorsOk(", "bool IntentVectorsOk(")

    rows = sum(function_body(selftest, group).count("&& ok;") for group in groups)
    assert rows >= sum(len(VECTORS[section]) for section in ("hmac", "requests", "intents"))
    for group in groups:
        assert group.removeprefix("bool ") in body
    assert "IntentTamperRejected()" in body and "FingerprintOk()" in body
    main = read(EA_MAIN)
    assert "HmacSelfTest()" in main
    assert "g_cfg.selftest_ok" in function_body(include("Orders.mqh"), "bool ExecutionReady(")
    assert "g_cfg.selftest_ok" in function_body(include("Poll.mqh"), "bool ReplyTrusted(")
