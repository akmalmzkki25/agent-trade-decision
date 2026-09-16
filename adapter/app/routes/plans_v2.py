"""V2 bulk-layering plan endpoint."""

from __future__ import annotations

import time
from datetime import datetime as _dt, timezone as _tz

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import ValidationError

from ..deps import ledger, logger, utcnow_iso
from ..layering import build_plan
from ..models import (
    AdapterError,
    LayerPlan,
    LayerPlanRequest,
    LayerPlanResponse,
    NewsContext,
    ResponseMeta,
)
from ..news import get_blackout
from ..scenarios import select_best
from ..security import read_verified_body

router = APIRouter(tags=["v2"])


def _empty_plan(reason: str) -> LayerPlan:
    return LayerPlan(
        scenario="NONE",
        side_bias="none",
        confidence=0.0,
        basket_tp_pct_equity=0.0,
        scenario_invalidation_price=None,
        valid_until_utc=utcnow_iso(),
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


@router.post("/v2/plan", response_model=LayerPlanResponse)
async def plan(request: Request, x_internal_sig: str | None = Header(default=None)):
    raw = await read_verified_body(request, x_internal_sig)

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

