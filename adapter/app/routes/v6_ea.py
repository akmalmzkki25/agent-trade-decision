"""
V6 EA-facing endpoints (docs/v6-wire-contract.md).

    POST /v6/bars/backfill   closed-bar history at EA start
    POST /v6/snapshot        one snapshot per M15 close, idempotent per snapshot_id; the
                             intent ledger is reconciled with its V6 orders and positions
    POST /v6/intent/poll     heartbeat: the pending command (FLATTEN, CANCEL_PENDING) or,
                             in execute mode for an armed session, the signed intent
    POST /v6/execution       what the EA did with an intent; moves the intent along
    POST /v6/action          what the EA did with a management action, or an SL+ step it
                             took (`v6.action.1`); settles the action, stores the new plan
    POST /v6/basket-result   a closed V6 position (version "v6"); closes its intent
    GET  /v6/status          runtime, session, operator, active intent, EA, bar coverage

Every POST passes `read_ea_body`: JSON only, no cross-site requests, a size cap
and, in execute mode, the V6 request signature (401 otherwise). Every poll answer
is signed when V6_EA_HMAC_KEY is set. Blocking SQLite work runs in worker threads
so a slow write never stalls the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable, Mapping
from functools import partial
from typing import Annotated, Any, Final, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from ..v6.container import V6Container, require_active_container
from ..v6.ledger_baskets import received_at_utc
from ..v6.ledger_v6 import AccountMark, SnapshotRecord
from ..v6.learning.outcomes import outcome_for_event
from ..v6.market.bar_store import BarStore
from ..v6.runtime.ea_state import InboxItem, SnapshotMeta, cycle_id_for
from ..v6.runtime.poll_reply import PollFacts
from ..v6.runtime.reconcile import Exposure
from ..v6.runtime.sessions import session_to_dict
from ..v6.schemas.basket import V6BasketResultEvent
from ..v6.schemas.intent import ActionReport, ExecutionReport, PollRequest, PollResponse
from ..v6.schemas.snapshot import BackfillRequest, BarRow, V6Snapshot, rows_to_bars
from . import v6_status_view as view
from .v6_ea_auth import read_ea_body

logger = logging.getLogger(__name__)

router = APIRouter(tags=["v6"])

MAX_REPORTED_ERRORS: Final[int] = 20
STORAGE_UNAVAILABLE: Final[str] = "V6 storage unavailable"
# A racing lifecycle move (IllegalIntentTransition, UnknownIntent) is logged, not fatal.
LIFECYCLE_ERRORS: Final[tuple[type[Exception], ...]] = (ValueError, LookupError)

ModelT = TypeVar("ModelT", bound=BaseModel)
ResultT = TypeVar("ResultT")

ActiveContainer = Annotated[V6Container, Depends(require_active_container)]


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


def _ingest_blocks(store: BarStore, blocks: Mapping[str, list[BarRow]]) -> int:
    return sum(store.ingest(tf, rows_to_bars(rows)) for tf, rows in blocks.items())


def _duplicate(cycle_id: str) -> JSONResponse:
    return JSONResponse(status_code=200,
                        content={"accepted": False, "duplicate": True, "cycle_id": cycle_id})


# --- backfill ----------------------------------------------------------------
@router.post("/v6/bars/backfill")
async def v6_backfill(request: Request, container: ActiveContainer) -> dict[str, int]:
    backfill = _parse(BackfillRequest, await read_ea_body(request, container))
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


async def _reconcile(container: V6Container, snapshot: V6Snapshot, now: float) -> None:
    """The intent ledger against the snapshot's V6 orders; a failure never drops the bar."""
    book = container.parts.intent_book
    exposure = Exposure.from_snapshot(snapshot, now)
    try:
        await asyncio.to_thread(partial(book.reconcile_with, exposure, now,
                                        magic=container.settings.magic))
    except (sqlite3.Error, *LIFECYCLE_ERRORS) as exc:
        logger.error("v6 snapshot %s: intent reconciliation failed (%s)",
                     snapshot.snapshot_id, type(exc).__name__)


@router.post("/v6/snapshot", status_code=202)
async def v6_snapshot(request: Request, container: ActiveContainer) -> JSONResponse:
    raw = await read_ea_body(request, container)
    snapshot = _parse(V6Snapshot, raw)
    now = container.clock.now_epoch()
    cycle_id = cycle_id_for(snapshot.snapshot_id)
    container.ea_state.touch(now)
    if await _storage(container.ledger_v6.snapshot_exists, snapshot.snapshot_id):
        return _duplicate(cycle_id)
    if not await _store_snapshot(container, snapshot, raw, now):
        return _duplicate(cycle_id)
    await _reconcile(container, snapshot, now)
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


@router.post("/v6/intent/poll", response_model=PollResponse)
async def v6_poll(request: Request, container: ActiveContainer) -> PollResponse:
    """The pending command, or (execute mode, armed session) the intent; always signed
    when a V6 EA key is configured."""
    poll = _parse(PollRequest, await read_ea_body(request, container))
    now = container.clock.now_epoch()
    container.ea_state.record_poll(poll, now)
    await _record_mark(container, poll, now)
    parts = container.parts
    facts = PollFacts(halted=await parts.desk.halted(),
                      breaker_tripped=parts.watchdog.state.breakers_tripped,
                      point=parts.worker.carry.point)
    return await parts.poll_replier.reply(poll, now, facts)


# --- execution ---------------------------------------------------------------
@router.post("/v6/execution")
async def v6_execution(request: Request, container: ActiveContainer) -> dict[str, bool]:
    report = _parse(ExecutionReport, await read_ea_body(request, container))
    now = container.clock.now_epoch()
    container.ea_state.touch(now)
    if not await _storage(container.ledger_v6.insert_execution, report, now):
        logger.info("v6 execution report for %s/%s was a resend", report.intent_id,
                    report.status)
    try:
        await _storage(container.parts.intent_book.apply_execution, report, now)
    except LIFECYCLE_ERRORS as exc:
        logger.error("v6 execution report for %s not applied (%s)", report.intent_id,
                     type(exc).__name__)
    return {"ok": True}


# --- management actions --------------------------------------------------------
@router.post("/v6/action")
async def v6_action(request: Request, container: ActiveContainer) -> dict[str, bool]:
    report = _parse(ActionReport, await read_ea_body(request, container))
    now = container.clock.now_epoch()
    container.ea_state.touch(now)
    parts = container.parts
    if report.action_id:
        parts.actions.settle(report.action_id)
    try:
        result = await _storage(parts.action_desk.apply, report, now)
    except LIFECYCLE_ERRORS as exc:
        logger.error("v6 action report %s/%s not applied (%s)", report.kind,
                     report.action_id or report.ticket, type(exc).__name__)
        return {"ok": True}
    logger.info("v6 action report %s %s ticket=%s -> %s", report.kind,
                report.action_id or "-", report.ticket, result)
    return {"ok": True}


# --- basket result -----------------------------------------------------------
def _outcome_line(container: V6Container, event: V6BasketResultEvent, now: float) -> str:
    """Blocking: the linked outcome of a stored result, as one log-safe line."""
    poll = container.ea_state.view().last_poll
    record = outcome_for_event(container.ledger_cycles, event,
                               received_at_utc=received_at_utc(now),
                               login=None if poll is None else poll.poll.login)
    r_multiple = record.r_multiple
    return (f"basket={event.basket_id} intent={record.result.intent_id or '-'} "
            f"linked={record.intent is not None} kind={record.kind} "
            f"net_pnl={event.net_pnl:.2f} "
            f"r={'-' if r_multiple is None else f'{r_multiple:.3f}'}")


@router.post("/v6/basket-result")
async def v6_basket_result(request: Request, container: ActiveContainer) -> dict[str, bool]:
    event = _parse(V6BasketResultEvent, await read_ea_body(request, container))
    now = container.clock.now_epoch()
    container.ea_state.touch(now)
    parts = container.parts
    if not await _storage(parts.baskets.record, event):
        logger.info("v6 basket result %s was a resend", event.basket_id)
    try:
        await _storage(partial(parts.intent_book.close_from_basket, basket_id=event.basket_id,
                               net_pnl=event.net_pnl, now=now))
        logger.info("v6 outcome %s", await _storage(_outcome_line, container, event, now))
    except LIFECYCLE_ERRORS as exc:
        logger.error("v6 basket result %s not linked (%s)", event.basket_id,
                     type(exc).__name__)
    return {"ok": True}


# --- status ------------------------------------------------------------------
@router.get("/v6/status")
async def v6_status(container: ActiveContainer) -> dict[str, Any]:
    """Built field by field from non-secret values; settings are never dumped whole."""
    now = container.clock.now_epoch()
    coverage, warm = await _storage(view.read_market, container.bar_store, int(now))
    ledger = await _storage(view.read_ledger, container.ledger_cycles)
    ea = container.ea_state.view()
    settings, parts = container.settings, container.parts
    halted = await parts.desk.halted()
    session = ledger.session
    return {
        "enabled": container.active, "mode": settings.mode, "backend": settings.backend,
        "operator_agents": list(settings.operator_agents),
        "operator_ready": settings.operator_token_ok, "ea_signing": settings.ea_signing,
        "server_time_epoch": int(now), "ea_last_seen_age_s": view.age(now, ea.last_seen_at),
        **view.account_status(ea),
        "last_snapshot": view.snapshot_status(ea.last_snapshot, now),
        "bar_coverage": coverage, "warm": warm,
        "superseded": ea.superseded, "inbox_pending": ea.inbox_pending,
        "halt_file_present": halted,
        "runtime": view.runtime_status(container, ea, now, halted, ledger.breakers),
        "session": None if session is None else session_to_dict(session),
        "armed": bool(session is not None and session.armed),
        "operator": view.operator_status(parts.operator_queue, now),
        "active_intent": None if ledger.active_intent is None
        else ledger.active_intent.to_dict(),
        "last_cycle": view.cycle_status(ledger.last_cycle),
    }
