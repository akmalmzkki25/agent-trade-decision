"""
The golden HMAC vectors the V6 EA embeds in its OnInit self-test.

Every value in golden/hmac_vectors.json is recomputed with app.v6.wire, and the
RFC 4231 results are also pinned here independently of the helper, so a wrong
helper cannot silently rewrite the file the EA trusts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest
from pydantic import SecretStr

from app.v6 import wire
from app.v6.config import ea_key_ok
from app.v6.schemas.intent import PollResponse

GOLDEN: Final[Path] = Path(__file__).resolve().parent / "golden" / "hmac_vectors.json"
VECTORS: Final[dict[str, Any]] = json.loads(GOLDEN.read_text(encoding="utf-8"))
TEST_KEY: Final[str] = VECTORS["test_key"]
# RFC 4231 section 4, HMAC-SHA-256 results.
RFC4231: Final[dict[str, str]] = {
    "rfc4231-tc1": "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7",
    "rfc4231-tc2": "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843",
    "rfc4231-tc6": "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54",
}


def _named(section: str) -> dict[str, dict[str, Any]]:
    return {row["name"]: row for row in VECTORS[section]}


def test_the_file_has_the_contract_shape() -> None:
    assert VECTORS["schema"] == "v6.hmac.vectors.1"
    assert set(_named("hmac")) == set(RFC4231)
    assert {"poll", "execution"} <= set(_named("requests"))
    assert {"idle", "cancel-pending", "buy-limit", "sell-market"} <= set(_named("intents"))
    assert ea_key_ok(SecretStr(TEST_KEY))


@pytest.mark.parametrize("name", sorted(RFC4231))
def test_rfc4231_vectors(name: str) -> None:
    row = _named("hmac")[name]
    key, data = bytes.fromhex(row["key_hex"]), bytes.fromhex(row["data_hex"])

    assert row["sig"] == RFC4231[name] == wire.hmac_hex(key, data)
    assert data.decode("ascii") == row["data_text"]
    if "key_text" in row:
        assert wire.hmac_hex(row["key_text"], data) == RFC4231[name]


def test_the_long_key_vector_needs_the_pre_hash_path() -> None:
    assert len(bytes.fromhex(_named("hmac")["rfc4231-tc6"]["key_hex"])) > 64


@pytest.mark.parametrize("name", ["poll", "execution"])
def test_request_vectors(name: str) -> None:
    row = _named("requests")[name]
    body = row["body"].encode("utf-8")
    payload = wire.request_signing_payload(row["ts"], row["method"], row["path"], body)

    assert payload.hex() == row["signing_payload_hex"]
    assert wire.sign_request(TEST_KEY, row["ts"], row["method"], row["path"], body) == row["sig"]
    check = wire.verify_request(
        key=SecretStr(TEST_KEY), ts_header=str(row["ts"]), sig_header=row["sig"],
        method=row["method"], path=row["path"], body=body, now=float(row["ts"]),
        cache=wire.ReplayCache())
    assert check.ok


@pytest.mark.parametrize("name", ["idle", "cancel-pending", "buy-limit", "sell-market"])
def test_intent_vectors(name: str) -> None:
    row = _named("intents")[name]
    response = PollResponse.model_validate_json(json.dumps(row["response"]))
    unsigned = response.model_copy(update={"sig": ""})

    assert response.sig == row["sig"]
    assert wire.intent_canonical(unsigned, row["point"]) == row["canonical"]
    assert wire.intent_signature(TEST_KEY, unsigned, row["point"]) == row["sig"]
    assert wire.sign_intent(SecretStr(TEST_KEY), unsigned, row["point"]) == response
    assert wire.verify_intent(SecretStr(TEST_KEY), response, row["point"])


def test_the_fingerprint_vector() -> None:
    assert wire.FINGERPRINT_LABEL.decode("ascii") == VECTORS["fingerprint_label"]
    assert wire.key_fingerprint(SecretStr(TEST_KEY)) == VECTORS["test_key_fingerprint"]


def test_the_contract_document_names_the_vectors() -> None:
    doc = (Path(__file__).resolve().parents[3] / "docs" / "v6-wire-contract.md").read_text(
        encoding="utf-8")

    assert "golden/hmac_vectors.json" in doc
    for field in wire.CANONICAL_FIELDS:
        assert f"`{field}`" in doc, field
    for header in (wire.TS_HEADER, wire.SIG_HEADER):
        assert header in doc
