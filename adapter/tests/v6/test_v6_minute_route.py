"""POST /v6/minute: the minute bar is stored, the minute is queued once."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import create_app
from app.v6.clock import FakeClock
from app.v6.config import V6Settings

GOLDEN = Path(__file__).resolve().parent / "golden" / "minute_sample.json"
JSON_HEADERS = {"Content-Type": "application/json"}
NOW = 1789565462.0


def sample(**changes: Any) -> dict[str, Any]:
    return {**json.loads(GOLDEN.read_text(encoding="utf-8")), **changes}


def build(tmp_path: Path, db_path: Path, **overrides: Any) -> FastAPI:
    settings = V6Settings(_env_file=None, **({"enabled": True,
                                              "halt_file": str(tmp_path / "V6_HALT")}
                                             | overrides))
    return create_app(v6_settings=settings, clock=FakeClock(epoch=NOW),
                      v6_db_path=str(db_path), v6_tasks=False)


def post(client: TestClient, body: dict[str, Any] | bytes):
    content = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    return client.post("/v6/minute", content=content, headers=JSON_HEADERS)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "minute.db"


@pytest.fixture
def client(tmp_path: Path, db_path: Path) -> Iterator[TestClient]:
    with TestClient(build(tmp_path, db_path)) as test_client:
        yield test_client


def test_a_minute_is_accepted_and_its_bar_stored(client: TestClient, db_path: Path) -> None:
    reply = post(client, sample())
    assert reply.status_code == 202
    body = reply.json()
    assert body["accepted"] is True and body["cycle_id"].startswith("m-")
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT t, c FROM v6_bars WHERE tf = 'M1'").fetchall()
    assert rows == [(1789565400, 4535.84)]


def test_a_repeated_minute_is_a_duplicate(client: TestClient) -> None:
    assert post(client, sample()).status_code == 202
    again = post(client, sample())
    assert again.status_code == 200 and again.json()["duplicate"] is True


def test_an_invalid_minute_is_refused(client: TestClient) -> None:
    reply = post(client, sample(bar_open_epoch=1789565430))
    assert reply.status_code == 400


def test_the_route_is_404_when_v6_is_disabled(tmp_path: Path, db_path: Path) -> None:
    with TestClient(build(tmp_path, db_path, enabled=False)) as disabled:
        assert post(disabled, sample()).status_code == 404
