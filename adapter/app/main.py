from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from datetime import datetime as _dt, timezone as _tz


def _utcnow_iso() -> str:
    return _dt.now(_tz.utc).isoformat()

from . import __version__
from .deciders import get_decider
from .layering import BASKET_RR, DEFAULT_TOTAL_RISK_PCT, EXPIRY_MINUTES, build_plan
from .ledger import Ledger, now_utc
from .models import (
    AdapterError,
    DecisionRequest,
    DecisionResponse,
    LayerPlan,
    LayerPlanRequest,
    LayerPlanResponse,
    NewsContext,
    ResponseMeta,
    TradeDecision,
    TradeTransactionEvent,
)
from . import dashboard_db
from .news import get_blackout
from .scenarios import select_best
from .security import hmac_ok
from .settings import settings

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _template_globals() -> dict:
    return {
        "adapter_version": __version__,
        "decider_name": getattr(decider, "name", "?"),
        "db_path": settings.db_path,
    }

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


# ============================================================================
# v2 — Bulk Layering plan endpoint
# ============================================================================


def _empty_plan(reason: str) -> LayerPlan:
    return LayerPlan(
        scenario="NONE",
        side_bias="none",
        confidence=0.0,
        basket_tp_pct_equity=0.0,
        scenario_invalidation_price=None,
        valid_until_utc=_utcnow_iso(),
        layers=[],
        reason_codes=[reason],
        rationale_short=reason[:240],
    )


def _veto_response(req: LayerPlanRequest, reason: str, code: str, news: NewsContext) -> LayerPlanResponse:
    return LayerPlanResponse(
        schema_version="layer-plan-response.v1",
        request_id=req.request_id,
        status="veto",
        plan=_empty_plan(reason),
        news_context=news,
        meta=ResponseMeta(model="bulk_layering_ta"),
        error=AdapterError(code=code, message=reason, retryable=False),
    )


def _degraded_response(req: LayerPlanRequest, reason: str, code: str) -> LayerPlanResponse:
    return LayerPlanResponse(
        schema_version="layer-plan-response.v1",
        request_id=req.request_id,
        status="degraded",
        plan=_empty_plan(reason),
        meta=ResponseMeta(model="bulk_layering_ta"),
        error=AdapterError(code=code, message=reason, retryable=False),
    )


@app.post("/v2/plan", response_model=LayerPlanResponse)
async def plan(request: Request, x_internal_sig: str | None = Header(default=None)):
    raw = await request.body()
    if not hmac_ok(raw, x_internal_sig):
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        req = LayerPlanRequest.model_validate_json(raw)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.errors())

    start = time.perf_counter()

    # 1) Hard safety / risk gating.
    if req.risk_state.trading_halted:
        resp = _veto_response(req, "Trading halted by risk_state.", "APP-SAFE-409", NewsContext())
        ledger.write_plan(req, resp)
        return resp
    if req.openclaw_context.mode == "halt":
        resp = _veto_response(req, "Trading halted by OpenClaw.", "APP-SAFE-409", NewsContext())
        ledger.write_plan(req, resp)
        return resp

    # 2) Spread guard.
    atr_m1 = float(req.features.execution_tf.get("atr_abs_m1", req.features.execution_tf.get("atr_abs", 0.0)))
    if atr_m1 > 0:
        point = req.market.tick_size if req.market.tick_size > 0 else 0.01
        spread_price = req.market.spread_points * point
        if spread_price / atr_m1 > 0.35:
            resp = _veto_response(req, "Spread too wide vs ATR_M1.", "APP-SPREAD-409", NewsContext())
            ledger.write_plan(req, resp)
            return resp

    # 3) News blackout (stub).
    bo = get_blackout(req.symbol, _dt.now(_tz.utc))
    news = NewsContext(blackout=bo.blackout, severity=bo.severity, event=bo.event)
    if bo.blackout and bo.severity >= 0.7:
        resp = _veto_response(req, f"News blackout: {bo.event}", "APP-NEWS-409", news)
        ledger.write_plan(req, resp)
        return resp

    # 4) Scenario selection.
    try:
        winner = select_best(req)
    except Exception as e:
        logger.exception("scenario selection failed")
        resp = _degraded_response(req, f"Scenario error: {type(e).__name__}", "APP-SCN-500")
        ledger.write_plan(req, resp)
        return resp

    latency_ms = int((time.perf_counter() - start) * 1000)
    if winner is None:
        resp = LayerPlanResponse(
            schema_version="layer-plan-response.v1",
            request_id=req.request_id,
            status="ok",
            plan=_empty_plan("No scenario above threshold."),
            news_context=news,
            meta=ResponseMeta(model="bulk_layering_ta", latency_ms=latency_ms),
            error=None,
        )
        ledger.write_plan(req, resp)
        return resp

    # 5) Build plan.
    try:
        layer_plan = build_plan(scenario=winner, req=req)
    except Exception as e:
        logger.exception("planner failed")
        resp = _degraded_response(req, f"Planner error: {type(e).__name__}", "APP-PLAN-500")
        ledger.write_plan(req, resp)
        return resp

    if not layer_plan.layers:
        resp = LayerPlanResponse(
            schema_version="layer-plan-response.v1",
            request_id=req.request_id,
            status="ok",
            plan=layer_plan,
            news_context=news,
            meta=ResponseMeta(model="bulk_layering_ta", latency_ms=latency_ms),
            error=None,
        )
        ledger.write_plan(req, resp)
        return resp

    resp = LayerPlanResponse(
        schema_version="layer-plan-response.v1",
        request_id=req.request_id,
        status="ok",
        plan=layer_plan,
        news_context=news,
        meta=ResponseMeta(model="bulk_layering_ta", latency_ms=latency_ms),
        error=None,
    )
    ledger.write_plan(req, resp)
    return resp


# ============================================================================
# Dashboard (HTML) + read-only JSON helpers
# ============================================================================


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_view(request: Request):
    ctx = {
        "request": request,
        "active_page": "dashboard",
        "stats": dashboard_db.get_stats(),
        "decisions": dashboard_db.recent_decisions(limit=25),
        "plans": dashboard_db.recent_plans(limit=15),
        "trade_events": dashboard_db.recent_trade_events(limit=10),
        "action_distribution": dashboard_db.action_distribution(),
        **_template_globals(),
    }
    return templates.TemplateResponse(request, "dashboard.html", ctx)


@app.get("/chatbot", response_class=HTMLResponse, include_in_schema=False)
async def chatbot_view(request: Request):
    ctx = {
        "request": request,
        "active_page": "chatbot",
        **_template_globals(),
    }
    return templates.TemplateResponse(request, "chatbot.html", ctx)


@app.get("/api/dashboard/stats")
async def api_stats():
    return dashboard_db.get_stats()


@app.get("/api/dashboard/decisions")
async def api_decisions(limit: int = 25):
    limit = max(1, min(limit, 200))
    return dashboard_db.recent_decisions(limit=limit)


@app.get("/api/dashboard/plans")
async def api_plans(limit: int = 25):
    limit = max(1, min(limit, 200))
    return dashboard_db.recent_plans(limit=limit)


@app.get("/api/dashboard/trade-events")
async def api_trade_events(limit: int = 25):
    limit = max(1, min(limit, 200))
    return dashboard_db.recent_trade_events(limit=limit)


@app.get("/api/dashboard/action-distribution")
async def api_action_distribution():
    return dashboard_db.action_distribution()


@app.exception_handler(HTTPException)
async def http_error(_, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
