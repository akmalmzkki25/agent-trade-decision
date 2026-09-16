"""Health check plus the event intake endpoints EAs post back to."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import ValidationError

from .. import __version__
from ..deps import decider_name, ledger
from ..ledger import now_utc
from ..models import BasketResultEvent, TradeTransactionEvent
from ..security import read_verified_body

router = APIRouter(tags=["events"])


@router.get("/v1/healthz")
async def healthz():
    return {"ok": True, "version": __version__, "decider": decider_name(), "time_utc": now_utc()}



@router.post("/v1/events/trade-transaction")
async def trade_transaction(request: Request, x_internal_sig: str | None = Header(default=None)):
    raw = await read_verified_body(request, x_internal_sig)
    try:
        event = TradeTransactionEvent.model_validate_json(raw)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.errors())
    ledger.write_trade_event(event)
    return {"ok": True}


@router.post("/v1/events/basket-result")
async def basket_result(request: Request, x_internal_sig: str | None = Header(default=None)):
    """EAs post here when a basket closes; feeds the performance metrics."""
    raw = await read_verified_body(request, x_internal_sig)
    try:
        event = BasketResultEvent.model_validate_json(raw)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.errors())
    ledger.write_basket_result(event)
    return {"ok": True}
