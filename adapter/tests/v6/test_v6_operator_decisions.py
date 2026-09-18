"""scripts/v6_operator.py template and submit, against the real operator route and queue."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.v6.clock import FakeClock
from app.v6.schemas import operator as schema
from app.v6.schemas.operator import OperatorPacket
from app.v6.schemas.operator_plan import MODIFY_FIELDS

from .operator_cli_fixtures_v6 import (
    PACKET_NOW, TOKEN, ManualClock, RoutedTransport, cli, json_reply, operator_app,
    packet_document, queue_of, replies, run_cli, status_document, to_json,
)
from .cycle_fixtures_v6 import pa_payload
from .operator_fixtures_v6 import AGENT_ID, EVENT_ID, EXPIRES, managed_packet, packet, plan_v3
from .test_v6_dashboard import LOOPBACK, record_demo_poll

decisions = cli.decisions
DECISION = ("POST", "/v6/operator/decision")
STATUS = ("GET", "/v6/status")


@dataclass(frozen=True)
class Live:
    client: TestClient
    packet: OperatorPacket
    clock: FakeClock


@pytest.fixture
def live(tmp_path: Path) -> Iterator[Live]:
    built = operator_app(tmp_path)
    with TestClient(built.app, client=LOOPBACK) as client:
        assert built.container is not None
        record_demo_poll(built.container)
        offered = packet()
        queue_of(built).offer(offered)
        clock = built.container.clock
        assert isinstance(clock, FakeClock)
        yield Live(client=client, packet=offered, clock=clock)


@pytest.fixture
def files(tmp_path: Path) -> dict[str, Path]:
    packet_file = tmp_path / "packet.json"
    packet_file.write_text(json.dumps(packet_document(), indent=2), encoding="utf-8")
    return {"packet": packet_file, "decision": tmp_path / "decision.json"}


def template_args(files: dict[str, Path], *extra: str) -> list[str]:
    return ["template", "--packet", str(files["packet"]), "--out", str(files["decision"]),
            *extra]


def submit_args(files: dict[str, Path], agent: str = "claude_code") -> list[str]:
    return ["--env-file", "absent.env", "submit", "--agent", agent,
            "--file", str(files["decision"]), "--packet", str(files["packet"])]


def make_template(files: dict[str, Path]) -> dict[str, Any]:
    assert run_cli(template_args(files), RoutedTransport(), environ={}).code == 0
    return json.loads(files["decision"].read_text(encoding="utf-8"))


UP_BIAS = {"direction": "up", "levels": [4526.4, 4541.0], "invalidation": 4520.0,
           "scenario": "higher lows above the pivot"}


def enter_own_plan(document: dict[str, Any]) -> dict[str, Any]:
    """The v3 template answered ENTER: Price Action takes the agent entry id, own plan."""
    views = {**document["views"], "price_action": pa_payload(AGENT_ID, conviction=0.8),
             "news_risk": {**document["views"]["news_risk"], "event_ids": [EVENT_ID]}}
    return {**document, "action": "ENTER", "views": views, "entry_plan": plan_v3(),
            "m15_bias": UP_BIAS}


def write(files: dict[str, Path], document: object) -> None:
    files["decision"].write_text(json.dumps(document, indent=2), encoding="utf-8")


# --- template ------------------------------------------------------------------------
def test_template_is_the_packet_template_without_an_agent(files: dict[str, Path]) -> None:
    result = run_cli(template_args(files), RoutedTransport(), environ={})
    report = result.out_json()
    written = json.loads(files["decision"].read_text(encoding="utf-8"))
    expected = packet().decision_template.model_dump(mode="json")
    assert result.code == 0 and written == {**expected, "agent": None}
    assert list(written) == list(expected)
    assert report["written"] == str(files["decision"]) and report["expired"] is False
    assert report["seconds_left"] == EXPIRES - int(PACKET_NOW)
    assert report["candidate_ids"] == list(packet().allowed.candidate_ids)
    assert report["next"] == decisions.NEXT_EDIT


BIAS = {"direction": "range", "levels": [4526.0, 4541.0], "invalidation": None,
        "scenario": "fade the box"}


@pytest.mark.parametrize("sealed", [
    packet(), packet(baseline_views={"price_action": None, "news_risk": None,
                                     "liquidity": None, "structure": None}),
    packet(last_bias=BIAS, last_bias_at_epoch=EXPIRES - 1200),
    managed_packet("position"), managed_packet("pending")])
def test_the_fallback_template_matches_the_adapter(sealed: OperatorPacket) -> None:
    document = sealed.model_dump(mode="json")
    del document["decision_template"]
    expected = schema.decision_template(sealed, sealed.packet_hash).model_dump(mode="json")
    assert decisions.build_template(document) == {**expected, "agent": None}


def test_fallback_constants_match_the_adapter() -> None:
    assert dict(decisions.ABSTAIN_VIEW) == schema.ABSTAIN_VIEW.model_dump(mode="json")
    assert decisions._plain(decisions.UNKNOWN_VIEWS) == {
        "news_risk": schema.UNKNOWN_NEWS_VIEW.model_dump(mode="json"),
        "liquidity": schema.UNKNOWN_LIQUIDITY_VIEW.model_dump(mode="json"),
        "structure": schema.UNKNOWN_STRUCTURE_VIEW.model_dump(mode="json")}
    assert decisions._plain(decisions.UNCLEAR_BIAS) == schema.UNCLEAR_BIAS.model_dump(
        mode="json")
    assert decisions.MANAGE_FIELDS == MODIFY_FIELDS
    assert decisions.DECISION_SCHEMAS == schema.DECISION_SCHEMAS
    assert decisions.DECISION_SCHEMA == schema.DECISION_SCHEMA
    assert decisions.MAX_DECISION_BYTES == schema.MAX_DECISION_BYTES
    assert cli.waiting.PACKET_SCHEMA == schema.PACKET_SCHEMA


def test_template_keeps_an_edited_decision_unless_forced(files: dict[str, Path]) -> None:
    edited = enter_own_plan(make_template(files))
    assert make_template(files)["action"] == "HOLD"        # unchanged file: rewritten
    write(files, edited)

    refused = run_cli(template_args(files), RoutedTransport(), environ={})
    assert refused.code == cli.EXIT_USAGE and "--force" in refused.err_json()["detail"]
    assert json.loads(files["decision"].read_text(encoding="utf-8")) == edited

    forced = run_cli(template_args(files, "--force"), RoutedTransport(), environ={})
    assert forced.code == 0
    assert json.loads(files["decision"].read_text(encoding="utf-8"))["action"] == "HOLD"


@pytest.mark.parametrize("stale", [
    {"cycle_id": "c-older", "agent": "codex"},
    {"cycle_id": packet().cycle_id, "packet_hash": "0" * 64, "agent": "codex"},
])
def test_template_replaces_a_decision_for_another_packet(files: dict[str, Path],
                                                         stale: dict[str, Any]) -> None:
    write(files, stale)
    written = make_template(files)
    assert written["cycle_id"] == packet().cycle_id and written["agent"] is None
    assert written["packet_hash"] == packet().packet_hash


def test_template_refuses_an_expired_packet(files: dict[str, Path]) -> None:
    result = run_cli(template_args(files), RoutedTransport(), environ={},
                     clock=ManualClock(now=float(EXPIRES)))
    report = result.out_json()
    assert result.code == cli.EXIT_ERROR and report["expired"] is True
    assert report["written"] is None and not files["decision"].exists()


@pytest.mark.parametrize("content", [None, "{", '["a"]', '{"a": NaN}', '{"a": 1, "a": 2}'])
def test_template_needs_a_readable_packet(files: dict[str, Path], content: str | None) -> None:
    if content is None:
        files["packet"].unlink()
    else:
        files["packet"].write_text(content, encoding="utf-8")
    result = run_cli(template_args(files), RoutedTransport(), environ={})
    assert result.code == cli.EXIT_USAGE and result.err_json()["error"] == "usage"
    assert not files["decision"].exists()


# --- submit to the real queue -------------------------------------------------------------
def test_an_entry_is_accepted_by_the_queue(live: Live, files: dict[str, Path]) -> None:
    write(files, enter_own_plan(make_template(files)))
    transport = RoutedTransport(live.client)
    clock = ManualClock()

    result = run_cli(submit_args(files), transport, clock=clock)

    report = result.out_json()
    assert result.code == cli.EXIT_OK, result.text
    assert (report["accepted"], report["http_status"], report["code"]) == (True, 202, "ACCEPTED")
    assert (report["decision_action"], report["plan_order_type"]) == ("ENTER", "LIMIT")
    assert "chief_action" not in report
    assert (report["agent"], report["flagged"], report["codes"]) == (
        "claude_code", [], ["ACCEPTED"])
    assert report["result"] is None and report["action"] is None
    assert report["next"] == decisions.NEXT_RESULT_UNKNOWN and report["warnings"] == []
    sent = transport.sent[0]
    assert sent.headers["Authorization"] == f"Bearer {TOKEN}"
    assert sent.body is not None and b"\n" not in sent.body
    assert json.loads(sent.body)["agent"] == "claude_code"
    assert transport.paths().count(STATUS[1]) == decisions.RESULT_POLLS
    assert clock.slept == [decisions.RESULT_POLL_S] * decisions.RESULT_POLLS
    assert TOKEN not in result.text

    again = run_cli(submit_args(files, "claude_code"), RoutedTransport(live.client))
    assert again.code == cli.EXIT_ERROR and again.out_json()["code"] == "ALREADY_DECIDED"


def test_the_untouched_template_is_a_valid_hold(live: Live, files: dict[str, Path]) -> None:
    make_template(files)
    result = run_cli(submit_args(files, "codex"), RoutedTransport(live.client))
    report = result.out_json()
    assert result.code == 0 and (report["decision_action"], report["plan_order_type"]) == (
        "HOLD", None)


def test_invalid_risk_desks_are_flagged_not_refused(live: Live,
                                                    files: dict[str, Path]) -> None:
    document = make_template(files)
    document["views"] = {**document["views"], "liquidity": {"stance": "WHATEVER"}}
    write(files, document)
    report = run_cli(submit_args(files), RoutedTransport(live.client)).out_json()
    assert report["accepted"] is True and report["flagged"] == ["liquidity"]


@pytest.mark.parametrize(("change", "status", "codes"), [
    ({"action": "MANAGE"}, 422, ["INVALID", "DECISION_MANAGE"]),
    ({"packet_hash": "0" * 64}, 409, ["HASH_MISMATCH", "DECISION_STALE_PACKET"]),
    ({"cycle_id": "c-unknown"}, 409, ["UNKNOWN_CYCLE"]),
    ({"m15_bias": {"direction": "sideways"}}, 422, ["INVALID", "DECISION_BIAS"]),
    ({"chief": schema.HOLD_DECISION.model_dump(mode="json")}, 422, ["INVALID",
                                                                    "DECISION_SCHEMA"]),
    ({"views": {}}, 422, ["INVALID", "DECISION_SCHEMA"]),
])
def test_refusals_come_back_with_their_codes(live: Live, files: dict[str, Path],
                                             change: dict[str, Any], status: int,
                                             codes: list[str]) -> None:
    write(files, {**make_template(files), **change})
    result = run_cli(submit_args(files), RoutedTransport(live.client))
    report = result.out_json()
    assert result.code == cli.EXIT_ERROR and report["accepted"] is False
    assert report["codes"][:len(codes)] == codes and report["http_status"] == status
    assert report["next"] == decisions.NEXT_REFUSED and "result" not in report


def test_a_late_decision_is_refused_and_warned(live: Live, files: dict[str, Path]) -> None:
    make_template(files)
    late = float(EXPIRES + 30)
    live.clock.epoch = late
    result = run_cli(submit_args(files), RoutedTransport(live.client),
                     clock=ManualClock(now=late))
    report = result.out_json()
    # The packet is closed, and this adapter has no daily session: exit 4.
    assert result.code == cli.EXIT_NO_SESSION and report["code"] == "EXPIRED"
    assert report["warnings"] == ["the packet expired 30 s ago (DECISION_EXPIRED)"]


@pytest.mark.parametrize(("policy", "code"), [
    ("POLICY_OPERATOR_DEMO_ONLY", cli.EXIT_NOT_DEMO), ("POLICY_SERVER_NOT_DEMO", cli.EXIT_NOT_DEMO),
    ("POLICY_UNKNOWN_TRADE_MODE", cli.EXIT_ERROR), ("POLICY_LOGIN_NOT_ALLOWED", cli.EXIT_ERROR)])
def test_demo_refusals(files: dict[str, Path], policy: str, code: int) -> None:
    make_template(files)
    transport = RoutedTransport(routes={DECISION: replies(json_reply(403, {
        "code": "APP-V6-DEMO-403", "policy": policy, "detail": "x"}))})
    result = run_cli(submit_args(files), transport)
    assert result.code == code and result.out_json()["accepted"] is False


# --- scripted answers -----------------------------------------------------------------------
def test_the_cycle_result_is_reported_when_it_appears(files: dict[str, Path]) -> None:
    make_template(files)
    cycle = {"cycle_id": packet().cycle_id, "status": "ENTER", "hold_reason": None,
             "hold_detail": "", "failed_gates": [], "shadow_intent": None, "backend": "operator"}
    status = replies(json_reply(200, status_document(last_cycle={"cycle_id": "c-old"})),
                     json_reply(200, status_document(last_cycle=cycle)))
    transport = RoutedTransport(routes={
        DECISION: replies(json_reply(202, {"accepted": True, "code": "ACCEPTED",
                                           "flagged": []})),
        STATUS: status})
    report = run_cli(submit_args(files), transport).out_json()
    assert report["action"] == "ENTER" and report["next"] == decisions.NEXT_ACCEPTED
    assert report["result"] == {key: cycle[key] for key in decisions.RESULT_KEYS if key in cycle}


def test_the_result_poll_stops_when_the_adapter_goes_away(files: dict[str, Path]) -> None:
    make_template(files)
    transport = RoutedTransport(routes={
        DECISION: replies(json_reply(202, {"accepted": True, "code": "ACCEPTED"})),
        STATUS: replies(cli.TransportError("ConnectionResetError"))})
    report = run_cli(submit_args(files), transport).out_json()
    assert report["result"] is None and transport.paths().count(STATUS[1]) == 1


@pytest.mark.parametrize(("reply", "code"), [
    (json_reply(409, {"detail": "APP-V6-NO-SESSION"}), cli.EXIT_ERROR),
    (json_reply(200, {"accepted": False, "code": "DECISION_DUPLICATE"}), cli.EXIT_ERROR),
    ((200, b"<html>"), cli.EXIT_ERROR),
    (json_reply(500, {"detail": "boom"}), cli.EXIT_ERROR),
])
def test_other_answers(files: dict[str, Path], reply: tuple[int, bytes], code: int) -> None:
    make_template(files)
    result = run_cli(submit_args(files), RoutedTransport(routes={DECISION: replies(reply)}))
    assert result.code == code and result.out_json()["accepted"] is False


WITHDRAWN = json_reply(409, {"code": "EXPIRED", "error": "DECISION_EXPIRED",
                             "detail": "closed without a decision (cancelled)"})


@pytest.mark.parametrize(("status", "code", "next_step"), [
    (json_reply(200, status_document(session=None)), cli.EXIT_NO_SESSION,
     cli.waiting.NEXT_NO_SESSION),
    (json_reply(200, status_document()), cli.EXIT_ERROR, decisions.NEXT_CLOSED),
    (cli.TransportError("TimeoutError"), cli.EXIT_ERROR, decisions.NEXT_CLOSED),
])
def test_a_withdrawn_packet_is_closed_and_a_stopped_session_exits_4(
        files: dict[str, Path], status: Any, code: int, next_step: str) -> None:
    make_template(files)
    transport = RoutedTransport(routes={DECISION: replies(WITHDRAWN), STATUS: replies(status)})
    result = run_cli(submit_args(files), transport)
    assert (result.code, result.out_json()["next"]) == (code, next_step)


def test_other_refusals_leave_the_packet_open(files: dict[str, Path]) -> None:
    make_template(files)
    refused = json_reply(422, {"code": "INVALID", "error": "DECISION_SCHEMA"})
    gone = json_reply(200, status_document(session=None))
    transport = RoutedTransport(routes={DECISION: replies(refused), STATUS: replies(gone)})
    result = run_cli(submit_args(files), transport)
    assert (result.code, result.out_json()["next"]) == (cli.EXIT_ERROR, decisions.NEXT_REFUSED)
    assert STATUS[1] not in transport.paths()


def test_an_unreachable_adapter(files: dict[str, Path]) -> None:
    make_template(files)
    transport = RoutedTransport(routes={DECISION: replies(cli.TransportError("TimeoutError"))})
    result = run_cli(submit_args(files), transport)
    assert result.code == cli.EXIT_UNREACHABLE and result.err_json()["error"] == "unreachable"


def test_the_decision_can_come_from_stdin(live: Live, files: dict[str, Path]) -> None:
    decision = {**live.packet.decision_template.model_dump(mode="json"), "agent": None}
    argv = ["--env-file", "absent.env", "submit", "--agent", "codex", "--file", "-",
            "--packet", str(files["packet"])]
    result = run_cli(argv, RoutedTransport(live.client), stdin=to_json(decision))
    assert result.code == 0 and result.out_json()["agent"] == "codex"


def test_warnings_for_a_decision_of_another_packet(files: dict[str, Path]) -> None:
    other = packet(cycle_id="c-feedfacefeedface")
    write(files, {**other.decision_template.model_dump(mode="json"), "agent": None})
    transport = RoutedTransport(routes={DECISION: replies(json_reply(409, {
        "accepted": False, "code": "UNKNOWN_CYCLE"}))})
    report = run_cli(submit_args(files, "codex"), transport).out_json()
    assert report["warnings"] == ["the decision answers another packet than the packet file"]
    assert decisions.submission_warnings({"schema_version": "x"}, None, 0.0) == [
        f"schema_version is not {decisions.DECISION_SCHEMA}: {decisions.RETIRED_WARNING}"]


@pytest.mark.parametrize(("changes", "warning"), [
    ({"action": "ENTER"}, "action ENTER without an entry_plan"),
    ({"action": "MANAGE"}, "action MANAGE without manage"),
    ({"action": "ENTER", "entry_plan": {"side": "buy"}}, "action ENTER with an unclear m15_bias"),
])
def test_v3_warnings(changes: dict[str, Any], warning: str) -> None:
    template = decisions.build_template(packet().model_dump(mode="json"))
    assert warning in decisions.submission_warnings({**template, **changes}, None, 0.0)
    assert decisions.submission_warnings(template, None, 0.0) == []
    legacy = {**template, "schema_version": "v6.operator.decision.2"}
    assert decisions.submission_warnings(legacy, None, 0.0) == [
        f"schema_version is not {decisions.DECISION_SCHEMA}: {decisions.RETIRED_WARNING}"]


def test_examples_follow_the_packet_state() -> None:
    flat = packet().model_dump(mode="json")
    example = decisions.agent_entry_example(flat)
    assert example["action"] == "ENTER" and "STOP" in example["entry_plan"]["order_type"]
    assert decisions.manage_example(flat) is None
    managed = managed_packet("position").model_dump(mode="json")
    assert decisions.agent_entry_example(managed) is None
    assert decisions.manage_example(managed)["manage"]["op"] == "KEEP|CLOSE|MODIFY"
    pending = managed_packet("pending").model_dump(mode="json")
    assert decisions.manage_example(pending)["manage"]["ticket"] == 77


OVERSIZE = "<65537 bytes>"


@pytest.mark.parametrize(("content", "detail"), [
    (None, "decision file cannot be read"),
    ("[]", "decision must hold a JSON object"),
    ('{"agent": Infinity}', "decision is not valid JSON"),
    ('{"agent": "codex"}', "the decision names agent 'codex' but --agent is claude_code"),
    (OVERSIZE, "decision file is 65537 bytes; the limit is 65536"),
])
def test_bad_decision_files_send_nothing(files: dict[str, Path], content: str | None,
                                         detail: str) -> None:
    if content is not None:
        text = "x" * (64 * 1024 + 1) if content == OVERSIZE else content
        files["decision"].write_text(text, encoding="utf-8")
    transport = RoutedTransport()
    result = run_cli(submit_args(files), transport)
    assert result.code == cli.EXIT_USAGE and result.err_json()["detail"].startswith(detail)
    assert transport.sent == []


def test_an_oversized_encoded_decision_is_refused() -> None:
    with pytest.raises(cli.UsageError, match="the limit is 65536"):
        decisions.encode_decision({"note": "n" * (64 * 1024)})
    no_stdin = cli.Context(client=cli.Client("http://127.0.0.1:1", RoutedTransport()),
                           stdout=None, stderr=None)  # type: ignore[arg-type]
    with pytest.raises(cli.UsageError, match="stdin"):
        decisions.read_decision(no_stdin, Path("-"))


def test_submit_arguments(files: dict[str, Path], capsys: pytest.CaptureFixture) -> None:
    transport = RoutedTransport()
    for argv in (["submit"], ["submit", "--agent", "gpt"],
                 ["submit", "--agent", "codex", "--file", "decision.txt"]):
        assert run_cli(argv, transport).code == cli.EXIT_USAGE
    missing_token = run_cli(submit_args(files), transport, environ={})
    assert missing_token.code == cli.EXIT_USAGE and transport.sent == []
    with pytest.raises(cli.UsageError, match="--agent"):
        decisions.with_agent({}, "gemini")
    assert "usage" in capsys.readouterr().err
