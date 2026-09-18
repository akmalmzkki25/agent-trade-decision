"""
Body-size cap of the shared request guard that every V6 EA route runs.

A local process can send a chunked body with no Content-Length, which the
header pre-check never sees. The cap therefore has to hold while the body is
being read: checking the length only after `request.body()` would let one
request buffer an unbounded body and take the single adapter worker (and with
it V1-V5) down.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Final

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.types import Message

from app.main import create_app
from app.security import read_verified_body
from app.settings import settings as adapter_settings
from app.v6.clock import FakeClock
from app.v6.config import V6Settings

from .payloads_v6 import RECEIVED_AT, execution_payload

CHUNK_BYTES: Final[int] = 64 * 1024
FLOOD_CHUNKS: Final[int] = 64          # ~4 MB, far past the cap
SMALL_PIECE_BYTES: Final[int] = 7
JSON_HEADERS: Final[dict[str, str]] = {"Content-Type": "application/json"}
V6_POST_PATHS: Final[tuple[str, ...]] = (
    "/v6/bars/backfill", "/v6/snapshot", "/v6/intent/poll", "/v6/execution", "/v6/action",
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class ChunkedReceive:
    """ASGI `receive` that streams fixed chunks and counts how many were pulled."""

    def __init__(self, chunks: Sequence[bytes]) -> None:
        self._chunks = tuple(chunks)
        self.pulled = 0

    async def __call__(self) -> Message:
        index = self.pulled
        self.pulled += 1
        if index >= len(self._chunks):
            return {"type": "http.disconnect"}
        return {"type": "http.request", "body": self._chunks[index],
                "more_body": index + 1 < len(self._chunks)}


def _request(receive: ChunkedReceive, content_length: str | None = None) -> Request:
    headers = [(b"content-type", b"application/json")]
    if content_length is None:
        headers.append((b"transfer-encoding", b"chunked"))
    else:
        headers.append((b"content-length", content_length.encode("ascii")))
    scope = {"type": "http", "method": "POST", "path": "/v6/snapshot",
             "headers": headers, "query_string": b""}
    return Request(scope, receive)


def _flood() -> Iterator[bytes]:
    for _ in range(FLOOD_CHUNKS):
        yield b" " * CHUNK_BYTES


# --- guard unit --------------------------------------------------------------
@pytest.mark.anyio
async def test_a_chunked_flood_is_refused_before_it_is_buffered() -> None:
    receive = ChunkedReceive([b" " * CHUNK_BYTES] * FLOOD_CHUNKS)
    with pytest.raises(HTTPException) as caught:
        await read_verified_body(_request(receive), None)
    assert caught.value.status_code == 413
    # At most one chunk past the cap may ever be read into memory.
    assert receive.pulled <= adapter_settings.max_request_bytes // CHUNK_BYTES + 1


@pytest.mark.anyio
async def test_a_chunked_body_within_the_cap_is_returned_whole() -> None:
    body = json.dumps(execution_payload()).encode("utf-8")
    pieces = [body[i:i + SMALL_PIECE_BYTES] for i in range(0, len(body), SMALL_PIECE_BYTES)]
    receive = ChunkedReceive(pieces)
    assert await read_verified_body(_request(receive), None) == body
    assert receive.pulled == len(pieces)


@pytest.mark.anyio
async def test_a_body_exactly_at_the_cap_is_accepted() -> None:
    limit = adapter_settings.max_request_bytes
    half = limit // 2
    receive = ChunkedReceive([b" " * half, b" " * (limit - half)])
    assert len(await read_verified_body(_request(receive), None)) == limit


@pytest.mark.anyio
async def test_a_declared_oversize_is_refused_without_reading_the_body() -> None:
    receive = ChunkedReceive([b"{}"])
    declared = str(adapter_settings.max_request_bytes + 1)
    with pytest.raises(HTTPException) as caught:
        await read_verified_body(_request(receive, declared), None)
    assert caught.value.status_code == 413
    assert receive.pulled == 0


@pytest.mark.anyio
async def test_a_malformed_content_length_is_400() -> None:
    receive = ChunkedReceive([b"{}"])
    with pytest.raises(HTTPException) as caught:
        await read_verified_body(_request(receive, "twelve"), None)
    assert caught.value.status_code == 400
    assert receive.pulled == 0


# --- routes ------------------------------------------------------------------
@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(
        v6_settings=V6Settings(_env_file=None, enabled=True, halt_file=str(tmp_path / "V6_HALT")),
        clock=FakeClock(epoch=RECEIVED_AT),
        v6_db_path=str(tmp_path / "v6_limits.db"),
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize("path", V6_POST_PATHS)
def test_routes_refuse_an_oversized_body_sent_without_content_length(
        client: TestClient, path: str) -> None:
    r = client.post(path, content=_flood(), headers=JSON_HEADERS)
    assert "content-length" not in r.request.headers
    assert r.status_code == 413


def test_routes_accept_a_chunked_body_within_the_cap(client: TestClient) -> None:
    body = json.dumps(execution_payload()).encode("utf-8")
    pieces = iter([body[:SMALL_PIECE_BYTES], body[SMALL_PIECE_BYTES:]])
    r = client.post("/v6/execution", content=pieces, headers=JSON_HEADERS)
    assert "content-length" not in r.request.headers
    assert r.status_code == 200
    assert r.json() == {"ok": True}
