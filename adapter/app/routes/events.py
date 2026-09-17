"""Health check plus the event intake endpoints EAs post back to.

V6 basket results are refused here: they arrive signed on /v6/basket-result
(routes/v6_ea.py), where they also close their intent. Accepting them unsigned on
this route would let anyone on the machine rewrite the P&L the V6 breakers read, so
a V6 basket id is refused here whatever version the body claims.
"""

from __future__ import annotations

from typing import Final

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import ValidationError

from .. import __version__
from ..deps import decider_name, ledger
from ..ledger import now_utc
from ..models import BasketResultEvent, TradeTransactionEvent
from ..security import read_verified_body

router = APIRouter(tags=["events"])

V6_VERSION: Final[str] = "v6"
# The V6 EA names its baskets "<symbol>-V6B-<intent_id>" (schemas/intent.basket_id_for).
V6_BASKET_MARKER: Final[str] = "-V6B-"
V6_ROUTE_DETAIL: Final[str] = "V6 basket results go to the signed /v6/basket-result route"


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
    if event.version == V6_VERSION or V6_BASKET_MARKER in event.basket_id:
        raise HTTPException(status_code=400, detail=V6_ROUTE_DETAIL)
    ledger.write_basket_result(event)
    return {"ok": True}
