"""
V6 EA-facing endpoints (data plane).

    POST /v6/bars/backfill   closed-bar history at EA start
    POST /v6/snapshot        one snapshot per M15 close, idempotent per snapshot_id
    POST /v6/intent/poll     heartbeat; no intents exist yet (execution is a later phase)
    POST /v6/execution       EA outcome of an intent
    GET  /v6/status          runtime, EA and bar-coverage summary (no secrets)

Every POST body passes `read_verified_body`, so the JSON-only, cross-site,
size and optional HMAC guards apply exactly as on /v5/burst. Blocking SQLite
work runs in worker threads so a slow write never stalls the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import asdict
from typing import Annotated, Any, Final, TypeVar

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from ..security import read_verified_body
from ..v6.container import V6Container, require_active_container
from ..v6.ledger_v6 import AccountMark, SnapshotRecord
from ..v6.market.bar_store import BarStore
from ..v6.runtime.ea_state import InboxItem, SnapshotMeta, cycle_id_for
from ..v6.schemas.intent import ExecutionReport, PollRequest, PollResponse
from ..v6.schemas.snapshot import BackfillRequest, BarRow, V6Snapshot, rows_to_bars
from ..v6.types import TIMEFRAME_SECONDS

logger = logging.getLogger(__name__)

router = APIRouter(tags=["v6"])

MAX_REPORTED_ERRORS: Final[int] = 20
AGE_DECIMALS: Final[int] = 3
STORAGE_UNAVAILABLE: Final[str] = "V6 storage unavailable"

ModelT = TypeVar("ModelT", bound=BaseModel)
ResultT = TypeVar("ResultT")

ActiveContainer = Annotated[V6Container, Depends(require_active_container)]
SignatureHeader = Annotated[str | None, Header()]


def _parse(model: type[ModelT], raw: bytes) -> ModelT:
    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        # Context and input are dropped: they may hold exception objects that do
        # not serialise, and echoing a large body back helps nobody.
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        raise HTTPException(status_code=400, detail=errors[:MAX_REPORTED_ERRORS]) from exc


async def _storage(fn: Callable[..., ResultT], *args: Any) -> ResultT:
    try:
        return await asyncio.to_thread(fn, *args)
    except sqlite3.Error:
        logger.exception("v6 storage call %s failed", getattr(fn, "__name__", "unknown"))
        raise HTTPException(status_code=503, detail=STORAGE_UNAVAILABLE) from None


def _age(now: float, then: float | None) -> float | None:
    return None if then is None else round(now - then, AGE_DECIMALS)


def _ingest_blocks(store: BarStore, blocks: Mapping[str, list[BarRow]]) -> int:
    return sum(store.ingest(tf, rows_to_bars(rows)) for tf, rows in blocks.items())


def _duplicate(cycle_id: str) -> JSONResponse:
    return JSONResponse(status_code=200,
                        content={"accepted": False, "duplicate": True, "cycle_id": cycle_id})


# --- backfill ----------------------------------------------------------------
@router.post("/v6/bars/backfill")
async def v6_backfill(request: Request, container: ActiveContainer,
                      x_internal_sig: SignatureHeader = None) -> dict[str, int]:
    raw = await read_verified_body(request, x_internal_sig)
    backfill = _parse(BackfillRequest, raw)
    container.ea_state.touch(container.clock.now_epoch())
    accepted = await _storage(container.bar_store.ingest, backfill.tf,
                              rows_to_bars(backfill.rows))
    return {"accepted": accepted}


# --- snapshot ----------------------------------------------------------------
async def _store_snapshot(container: V6Container, snapshot: V6Snapshot, raw: bytes,
                          now: float) -> bool:
    """Bars first, then the snapshot row; False when the id was stored meanwhile."""
    await _storage(_ingest_blocks, container.bar_store, snapshot.bars)
    record = SnapshotRecord.from_snapshot(snapshot, raw, now)
    return await _storage(container.ledger_v6.insert_snapshot, record)


@router.post("/v6/snapshot", status_code=202)
async def v6_snapshot(request: Request, container: ActiveContainer,
                      x_internal_sig: SignatureHeader = None) -> JSONResponse:
    raw = await read_verified_body(request, x_internal_sig)
    snapshot = _parse(V6Snapshot, raw)
    now = container.clock.now_epoch()
    cycle_id = cycle_id_for(snapshot.snapshot_id)
    container.ea_state.touch(now)
    if await _storage(container.ledger_v6.snapshot_exists, snapshot.snapshot_id):
        return _duplicate(cycle_id)
    if not await _store_snapshot(container, snapshot, raw, now):
        return _duplicate(cycle_id)
    state = container.ea_state
    state.record_snapshot(SnapshotMeta.from_snapshot(snapshot, cycle_id, now))
    if state.offer_snapshot(InboxItem(cycle_id=cycle_id, snapshot=snapshot, received_at=now)):
        logger.info("v6 snapshot %s superseded an unprocessed one", snapshot.snapshot_id)
    return JSONResponse(status_code=202, content={
        "accepted": True, "cycle_id": cycle_id, "server_time_epoch": int(now)})


# --- poll --------------------------------------------------------------------
async def _record_mark(container: V6Container, poll: PollRequest, now: float) -> None:
    """One equity mark per minute. A failed write is logged and retried on the next
    poll: the poll is the EA heartbeat and must not fail over bookkeeping."""
    mark = AccountMark.from_poll(poll, now)
    if not container.ea_state.mark_due(mark.minute_epoch):
        return
    try:
        await asyncio.to_thread(container.ledger_v6.record_account_mark, mark)
    except sqlite3.Error:
        logger.exception("v6 account mark write failed; retrying on the next poll")
        return
    container.ea_state.mark_done(mark.minute_epoch)


async def _halt_requested(container: V6Container) -> bool:
    return await asyncio.to_thread(container.halt_path.exists)


@router.post("/v6/intent/poll", response_model=PollResponse)
async def v6_poll(request: Request, container: ActiveContainer,
                  x_internal_sig: SignatureHeader = None) -> PollResponse:
    raw = await read_verified_body(request, x_internal_sig)
    poll = _parse(PollRequest, raw)
    now = container.clock.now_epoch()
    container.ea_state.record_poll(poll, now)
    await _record_mark(container, poll, now)
    command = "CANCEL_PENDING" if await _halt_requested(container) else "NONE"
    return PollResponse(server_time_epoch=int(now), command=command)


# --- execution ---------------------------------------------------------------
@router.post("/v6/execution")
async def v6_execution(request: Request, container: ActiveContainer,
                       x_internal_sig: SignatureHeader = None) -> dict[str, bool]:
    raw = await read_verified_body(request, x_internal_sig)
    report = _parse(ExecutionReport, raw)
    now = container.clock.now_epoch()
    container.ea_state.touch(now)
    if not await _storage(container.ledger_v6.insert_execution, report, now):
        logger.info("v6 execution report for %s/%s was a resend", report.intent_id,
                    report.status)
    return {"ok": True}


# --- status ------------------------------------------------------------------
def _market_status(store: BarStore, as_of: int) -> tuple[dict[str, dict[str, Any]], bool]:
    coverage = {
        tf: {**asdict(store.coverage(tf, as_of)), "cached": store.cached_count(tf)}
        for tf in TIMEFRAME_SECONDS
    }
    return coverage, store.is_warm(as_of)


def _snapshot_status(meta: SnapshotMeta | None, now: float) -> dict[str, Any] | None:
    if meta is None:
        return None
    return {"snapshot_id": meta.snapshot_id, "cycle_id": meta.cycle_id,
            "bar_open_epoch": meta.bar_open_epoch, "age_s": _age(now, meta.received_at)}


@router.get("/v6/status")
async def v6_status(container: ActiveContainer) -> dict[str, Any]:
    """Built field by field from non-secret values; settings are never dumped whole."""
    now = container.clock.now_epoch()
    coverage, warm = await _storage(_market_status, container.bar_store, int(now))
    view = container.ea_state.view()
    settings = container.settings
    return {
        "enabled": container.active,
        "mode": settings.mode,
        "backend": settings.backend,
        "available_backends": list(settings.available_backends),
        "server_time_epoch": int(now),
        "ea_last_seen_age_s": _age(now, view.last_seen_at),
        "trade_mode": None if view.last_poll is None else view.last_poll.poll.trade_mode,
        "last_snapshot": _snapshot_status(view.last_snapshot, now),
        "bar_coverage": coverage,
        "warm": warm,
        "superseded": view.superseded,
        "inbox_pending": view.inbox_pending,
        "halt_file_present": await _halt_requested(container),
    }
