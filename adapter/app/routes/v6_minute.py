"""
POST /v6/minute (spec section 4.1): one minute snapshot per closed M1 bar.

The body passes `read_ea_body` (JSON only, no cross-site requests, a size cap and, in
execute mode, the V6 request signature), then the strict `v6.minute.1` model. A repeated
snapshot id answers 200 duplicate. The closed M1 bar goes into the BarStore before the
minute is queued for the minute worker (one slot, the newest wins), so a minute that is
never processed still leaves its bar.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..v6.container import V6Container, require_active_container
from ..v6.runtime.ea_state import MinuteItem, minute_cycle_id_for
from ..v6.schemas.minute import MinuteSnapshot
from .v6_ea_auth import read_ea_body

logger = logging.getLogger(__name__)

router = APIRouter(tags=["v6"])

ActiveContainer = Annotated[V6Container, Depends(require_active_container)]
MAX_REPORTED_ERRORS: Final[int] = 20
STORAGE_UNAVAILABLE: Final[str] = "V6 storage unavailable"


def _parse(raw: bytes) -> MinuteSnapshot:
    try:
        return MinuteSnapshot.model_validate_json(raw)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        raise HTTPException(status_code=400, detail=errors[:MAX_REPORTED_ERRORS]) from exc


async def _store_bar(container: V6Container, minute: MinuteSnapshot) -> None:
    try:
        await asyncio.to_thread(container.bar_store.ingest, "M1", (minute.to_bar(),))
    except sqlite3.Error:
        logger.exception("v6 minute %s: the bar was not stored", minute.snapshot_id)
        raise HTTPException(status_code=503, detail=STORAGE_UNAVAILABLE) from None


@router.post("/v6/minute", status_code=202)
async def v6_minute(request: Request, container: ActiveContainer) -> JSONResponse:
    raw = await read_ea_body(request, container)
    minute = _parse(raw)
    now = container.clock.now_epoch()
    state = container.ea_state
    state.touch(now)
    cycle_id = minute_cycle_id_for(minute.snapshot_id)
    if not state.first_minute(minute.snapshot_id):
        return JSONResponse(status_code=200, content={
            "accepted": False, "duplicate": True, "cycle_id": cycle_id})
    await _store_bar(container, minute)
    if state.offer_minute(MinuteItem(cycle_id=cycle_id, minute=minute, received_at=now)):
        logger.info("v6 minute %s superseded an unprocessed one", minute.snapshot_id)
    return JSONResponse(status_code=202, content={
        "accepted": True, "cycle_id": cycle_id, "server_time_epoch": int(now)})
