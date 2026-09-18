"""app.v6.wire: request signatures, the replay cache and the signed intent."""

from __future__ import annotations

import hashlib
import hmac
import logging
import math
import threading
from typing import Any, Final

import pytest
from pydantic import SecretStr, ValidationError

from app.v6 import wire
from app.v6.schemas.intent import PollResponse

KEY: Final[str] = "wire-test-key-" + "w" * 30
SECRET: Final[SecretStr] = SecretStr(KEY)
TS: Final[int] = 1_789_565_407
PATH: Final[str] = "/v6/intent/poll"
BODY: Final[bytes] = b'{"schema_version":"v6.poll.1"}'
POINT: Final[float] = 0.01


def _live(**changes: Any) -> PollResponse:
    fields: dict[str, Any] = dict(
        server_time_epoch=TS, has_intent=True, intent_id="k7w2m4pq3xza", source="operator",
        side="buy", order_type="BUY_LIMIT", entry=4535.07, sl=4528.07, tp=4549.07, lots=0.01,
        ref_price=4535.35, max_drift_points=200, max_spread_points=35,
        valid_until_epoch=TS + 120, pending_expiry_epoch=TS + 1800, time_barrier_s=7200,
        magic=250570)
    return PollResponse(**{**fields, **changes})


def _verify(ts_header: str | None = str(TS), sig_header: str | None = None, *,
            key: SecretStr = SECRET, body: bytes = BODY, now: float = TS + 1.0,
            cache: wire.ReplayCache | None = None, path: str = PATH) -> wire.RequestCheck:
    signature = sig_header
    if signature is None:
        signature = wire.sign_request(KEY, TS, "POST", PATH, BODY)
    return wire.verify_request(key=key, ts_header=ts_header, sig_header=signature,
                               method="POST", path=path, body=body, now=now,
                               cache=wire.ReplayCache() if cache is None else cache)


# --- primitives --------------------------------------------------------------------
def test_request_payload_layout_and_signature() -> None:
    payload = wire.request_signing_payload(TS, "POST", PATH, BODY)

    assert payload == f"{TS}\nPOST\n{PATH}\n".encode("ascii") + BODY
    expected = hmac.new(KEY.encode("ascii"), payload, hashlib.sha256).hexdigest()
    assert wire.sign_request(SECRET, TS, "POST", PATH, BODY) == expected
    assert wire.hmac_hex(KEY.encode("ascii"), payload) == expected == expected.lower()


@pytest.mark.parametrize(("args", "error"), [
    ((-1, "POST", PATH, BODY), ValueError),
    ((True, "POST", PATH, BODY), ValueError),
    ((1.5, "POST", PATH, BODY), ValueError),
    ((TS, "post", PATH, BODY), ValueError),
    ((TS, "POST", "v6/poll", BODY), ValueError),
    ((TS, "POST", "/v6/poll?x=1", BODY), ValueError),
    ((TS, "POST", PATH, "text"), TypeError),
])
def test_request_payload_refuses_bad_parts(args: tuple[Any, ...], error: type) -> None:
    with pytest.raises(error):
        wire.request_signing_payload(*args)


@pytest.mark.parametrize("key", ["", b"", SecretStr(""), "kéy-not-ascii", 42])
def test_hmac_needs_a_usable_key(key: Any) -> None:
    with pytest.raises(ValueError) as caught:
        wire.hmac_hex(key, b"data")
    assert "kéy" not in str(caught.value)


def test_hmac_needs_bytes() -> None:
    with pytest.raises(TypeError):
        wire.hmac_hex(KEY, "text")  # type: ignore[arg-type]
    assert wire.hmac_hex(KEY, bytearray(b"x")) == wire.hmac_hex(KEY, b"x")


def test_key_fingerprint_is_short_stable_and_needs_a_valid_key() -> None:
    fingerprint = wire.key_fingerprint(SECRET)

    assert len(fingerprint) == wire.FINGERPRINT_CHARS and fingerprint in wire.hmac_hex(
        KEY, wire.FINGERPRINT_LABEL)
    assert KEY not in fingerprint
    assert wire.key_fingerprint(SecretStr("short")) == ""


# --- request verification ---------------------------------------------------------------
def test_a_good_request_passes_once() -> None:
    cache = wire.ReplayCache()

    first = _verify(cache=cache)
    again = _verify(cache=cache)

    assert (first.ok, first.code, first.detail) == (True, wire.CHECK_OK, "")
    assert (again.ok, again.code) == (False, wire.CHECK_REPLAY)
    assert cache.size == 1


@pytest.mark.parametrize(("kwargs", "code"), [
    ({"key": SecretStr("")}, wire.CHECK_NO_KEY),
    ({"key": SecretStr("change-me-" + "x" * 30)}, wire.CHECK_NO_KEY),
    ({"ts_header": None}, wire.CHECK_MISSING),
    ({"ts_header": ""}, wire.CHECK_MISSING),
    ({"sig_header": ""}, wire.CHECK_MISSING),
    ({"ts_header": "01789565407"}, wire.CHECK_BAD_TS),
    ({"ts_header": "-5"}, wire.CHECK_BAD_TS),
    ({"ts_header": "1789565407.0"}, wire.CHECK_BAD_TS),
    ({"sig_header": "A" * 64}, wire.CHECK_BAD_SIG),
    ({"sig_header": "0" * 63}, wire.CHECK_BAD_SIG),
    ({"now": TS + 31.0}, wire.CHECK_STALE),
    ({"now": TS - 30.5}, wire.CHECK_STALE),
    ({"now": math.nan}, wire.CHECK_STALE),
    ({"sig_header": "0" * 64}, wire.CHECK_MISMATCH),
    ({"body": BODY + b" "}, wire.CHECK_MISMATCH),
    ({"path": "/v6/execution"}, wire.CHECK_MISMATCH),
    ({"key": SecretStr("another-key-" + "a" * 30)}, wire.CHECK_MISMATCH),
])
def test_bad_requests_are_refused(kwargs: dict[str, Any], code: str) -> None:
    check = _verify(**kwargs)

    assert (check.ok, check.code) == (False, code)
    assert KEY not in check.detail and "0" * 64 not in check.detail


@pytest.mark.parametrize("now", [TS + 30.0, TS - 30.0])
def test_the_window_edges_are_inclusive(now: float) -> None:
    assert _verify(now=now).ok


def test_the_replay_cache_forgets_pairs_outside_the_window() -> None:
    cache = wire.ReplayCache(window_s=30)

    assert cache.admit(TS, "a" * 64, now=TS) == wire.CHECK_OK
    assert cache.admit(TS, "a" * 64, now=TS + 30) == wire.CHECK_REPLAY
    assert cache.admit(TS + 40, "b" * 64, now=TS + 31) == wire.CHECK_OK
    assert cache.size == 1, "the first pair expired"


def test_a_full_replay_cache_fails_closed() -> None:
    cache = wire.ReplayCache(window_s=30, max_entries=2)
    assert cache.admit(TS, "a" * 64, now=TS) == wire.CHECK_OK
    assert cache.admit(TS, "b" * 64, now=TS) == wire.CHECK_OK

    assert cache.admit(TS, "c" * 64, now=TS) == wire.CHECK_REPLAY_FULL
    assert _verify(cache=cache).code == wire.CHECK_REPLAY_FULL


def test_the_replay_cache_is_thread_safe() -> None:
    cache = wire.ReplayCache()
    results: list[str] = []

    def admit() -> None:
        results.append(cache.admit(TS, "d" * 64, now=TS))

    threads = [threading.Thread(target=admit) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results).count(wire.CHECK_OK) == 1


@pytest.mark.parametrize("args", [(0, 10), (30, 0), (True, 10), (1.5, 10)])
def test_replay_cache_arguments_are_checked(args: tuple[Any, Any]) -> None:
    with pytest.raises(ValueError):
        wire.ReplayCache(*args)


def test_request_check_is_consistent() -> None:
    with pytest.raises(ValueError):
        wire.RequestCheck(ok=True, code=wire.CHECK_REPLAY)
    with pytest.raises(ValueError):
        wire.RequestCheck(ok=False, code="SOMETHING")


def test_verification_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        _verify()
        _verify(sig_header="0" * 64)
    assert caplog.text == ""


# --- points and lots ---------------------------------------------------------------------
@pytest.mark.parametrize(("price", "point", "points"), [
    (4535.07, 0.01, 453507), (4300.07, 0.01, 430007), (0.0, 0.01, 0), (1.10005, 0.00001, 110005),
    (4535, 0.01, 453500), (2345.5, 0.5, 4691),
])
def test_price_to_points(price: float, point: float, points: int) -> None:
    assert wire.price_to_points(price, point) == points


@pytest.mark.parametrize(("price", "point"), [
    (4535.075, 0.01), (-1.0, 0.01), (math.inf, 0.01), (math.nan, 0.01), (True, 0.01),
    ("4535.07", 0.01), (4535.07, 0.0), (4535.07, -0.01), (4535.07, math.nan),
])
def test_price_to_points_refuses_bad_values(price: Any, point: Any) -> None:
    with pytest.raises(ValueError):
        wire.price_to_points(price, point)


@pytest.mark.parametrize(("lots", "hundredths"), [(0.01, 1), (0.0, 0), (0.1, 10), (1, 100)])
def test_lots_to_hundredths(lots: float, hundredths: int) -> None:
    assert wire.lots_to_hundredths(lots) == hundredths


@pytest.mark.parametrize("lots", [0.015, 0.001, -0.01, math.inf, None])
def test_lots_to_hundredths_refuses_bad_values(lots: Any) -> None:
    with pytest.raises(ValueError):
        wire.lots_to_hundredths(lots)


# --- intent signature --------------------------------------------------------------------
def test_canonical_field_order_matches_the_contract() -> None:
    parts = wire.intent_canonical(_live(), POINT).split(wire.CANONICAL_SEPARATOR)

    assert len(parts) == len(wire.CANONICAL_FIELDS) == 36
    assert dict(zip(wire.CANONICAL_FIELDS, parts)) == {
        "schema_version": "v6.intent.2", "server_time_epoch": str(TS), "command": "NONE",
        "has_intent": "1", "intent_id": "k7w2m4pq3xza", "source": "operator",
        "require_demo": "1", "side": "buy", "order_type": "BUY_LIMIT",
        "entry_points": "453507", "sl_points": "452807", "tp_points": "454907",
        "lots_hundredths": "1", "ref_points": "453535", "max_drift_points": "200",
        "max_spread_points": "35", "valid_until_epoch": str(TS + 120),
        "pending_expiry_epoch": str(TS + 1800), "time_barrier_s": "7200", "magic": "250570",
        "tp1_points": "0", "tp2_points": "0", "sl_after_tp1_points": "0",
        "sl_after_tp2_points": "0", "action_id": "", "action_ticket": "0",
        "action_sl_points": "0", "action_tp_points": "0", "action_tp1_points": "0",
        "action_tp2_points": "0", "action_sl1_points": "0", "action_sl2_points": "0",
        "action_price_points": "0", "action_expiry_epoch": "0", "action_barrier_s": "0",
        "action_issued_epoch": "0"}


def test_sign_and_verify_round_trip() -> None:
    signed = wire.sign_intent(SECRET, _live(), POINT)

    assert signed.sig == wire.intent_signature(KEY, _live(), POINT)
    assert wire.verify_intent(SECRET, signed, POINT)
    assert not wire.verify_intent(SecretStr("other-key-" + "o" * 30), signed, POINT)
    assert not wire.verify_intent(SECRET, signed, 0.1), "a different point breaks it"
    tampered = signed.model_copy(update={"lots": 0.02, "sig": signed.sig})
    assert not wire.verify_intent(SECRET, tampered, POINT)


def test_every_field_is_covered_by_the_signature() -> None:
    signed = wire.sign_intent(SECRET, _live(), POINT)
    changes = {"server_time_epoch": TS + 1, "intent_id": "b5n6r7t2vw3y", "entry": 4535.08,
               "sl": 4528.08, "tp": 4549.08, "ref_price": 4535.36, "max_drift_points": 201,
               "max_spread_points": 34, "valid_until_epoch": TS + 121,
               "pending_expiry_epoch": TS + 1801, "time_barrier_s": 7199, "magic": 250571}
    for name, value in changes.items():
        altered = signed.model_copy(update={name: value})
        assert not wire.verify_intent(SECRET, altered, POINT), name
    command = PollResponse(server_time_epoch=TS, command="FLATTEN")
    idle = wire.sign_intent(SECRET, PollResponse(server_time_epoch=TS), POINT)
    assert not wire.verify_intent(SECRET, command.model_copy(update={"sig": idle.sig}), POINT)


def test_no_key_means_an_unsigned_response() -> None:
    unsigned = wire.sign_intent(SecretStr(""), _live(), POINT)

    assert unsigned.sig == "" and unsigned == _live()
    assert not wire.verify_intent(SECRET, unsigned, POINT)
    assert not wire.verify_intent(SecretStr(""), unsigned, POINT)


def test_a_signed_response_still_validates_as_json() -> None:
    signed = wire.sign_intent(SECRET, _live(), POINT)

    again = PollResponse.model_validate_json(signed.model_dump_json())
    assert again == signed and wire.verify_intent(SECRET, again, POINT)
    with pytest.raises(ValidationError):
        PollResponse.model_validate_json(signed.model_dump_json().replace(signed.sig, "X" * 64))
