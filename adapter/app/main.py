from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from . import __version__
from .deciders import get_decider
from .ledger import Ledger, now_utc
from .models import (
    AdapterError,
    DecisionRequest,
    DecisionResponse,
    ResponseMeta,
    TradeDecision,
    TradeTransactionEvent,
)
from .security import hmac_ok
from .settings import settings

logger = logging.getLogger("adapter")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

ledger = Ledger(settings.db_path)
decider = get_decider()


def _hold_response(request_id: str, code: str, message: str, retryable: bool, latency_ms: int = 0) -> DecisionResponse:
    return DecisionResponse(
        schema_version="trade-decision-response.v1",
        request_id=request_id,
        status="degraded",
        decision=TradeDecision(
            action="hold",
            side="flat",
            order_type="none",
            lots=0.0,
            entry_price=None,
            sl=None,
            tp=None,
            max_deviation_points=settings.adapter_default_max_deviation_points,
            valid_until_utc=datetime.now(timezone.utc).isoformat(),
            confidence=0.0,
            rationale_short="Hold karena adapter/dependency error.",
            reason_codes=[code],
            risk_note=message[:240],
        ),
        meta=ResponseMeta(model=getattr(decider, "name", "unknown"), latency_ms=latency_ms),
        error=AdapterError(code=code, message=message, retryable=retryable),
    )


def _local_precheck(req: DecisionRequest) -> DecisionResponse | None:
    if req.risk_state.trading_halted:
        return _hold_response(req.request_id, "APP-SAFE-409", "Trading halted by risk_state.", False)
    if req.openclaw_context.mode == "halt":
        return _hold_response(req.request_id, "APP-SAFE-409", "Trading halted by OpenClaw.", False)
    if req.market.spread_points <= 0:
        return _hold_response(req.request_id, "APP-VAL-400", "Invalid spread_points.", False)
    if req.market.ask <= 0 or req.market.bid <= 0:
        return _hold_response(req.request_id, "APP-VAL-400", "Invalid bid/ask.", False)
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("adapter starting (version=%s, decider=%s)", __version__, getattr(decider, "name", "?"))
    if not settings.hmac_required:
        logger.warning("HMAC check DISABLED (dev mode). Set HMAC_REQUIRED=true for production.")
    yield
    logger.info("adapter shutting down")


app = FastAPI(title="mt5-claude-adapter", version=__version__, lifespan=lifespan)


@app.get("/v1/healthz")
async def healthz():
    return {"ok": True, "version": __version__, "decider": getattr(decider, "name", "?"), "time_utc": now_utc()}


@app.post("/v1/decision", response_model=DecisionResponse)
async def decision(request: Request, x_internal_sig: str | None = Header(default=None)):
    raw = await request.body()
    if not hmac_ok(raw, x_internal_sig):
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        req = DecisionRequest.model_validate_json(raw)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.errors())

    start = time.perf_counter()
    pre = _local_precheck(req)
    if pre is not None:
        ledger.write_decision(req, pre)
        return pre

    try:
        resp = decider.decide(req)
    except NotImplementedError as e:
        latency = int((time.perf_counter() - start) * 1000)
        resp = _hold_response(req.request_id, "APP-DEC-501", str(e), False, latency)
        ledger.write_decision(req, resp)
        return resp
    except Exception as e:
        latency = int((time.perf_counter() - start) * 1000)
        logger.exception("decider raised")
        resp = _hold_response(req.request_id, "APP-DEC-500", f"Decider error: {type(e).__name__}", False, latency)
        ledger.write_decision(req, resp)
        return resp

    if resp.meta.latency_ms == 0:
        resp.meta.latency_ms = int((time.perf_counter() - start) * 1000)
    ledger.write_decision(req, resp)
    return resp


@app.post("/v1/events/trade-transaction")
async def trade_transaction(request: Request, x_internal_sig: str | None = Header(default=None)):
    raw = await request.body()
    if not hmac_ok(raw, x_internal_sig):
        raise HTTPException(status_code=401, detail="invalid signature")
    try:
        event = TradeTransactionEvent.model_validate_json(raw)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.errors())
    ledger.write_trade_event(event)
    return {"ok": True}


@app.exception_handler(HTTPException)
async def http_error(_, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
