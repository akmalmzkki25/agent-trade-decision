"""V4 liquidity-zone plan endpoint."""

from __future__ import annotations

import time
from datetime import datetime as _dt, timezone as _tz

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import ValidationError

from ..deps import ledger, logger, utcnow_iso
from ..layering import build_plan_v4
from ..models import (
    AdapterError,
    NewsContext,
    ResponseMeta,
    V4Plan,
    V4PlanRequest,
    V4PlanResponse,
)
from ..news import get_blackout
from ..scenarios import select_best_v4
from ..security import read_verified_body

router = APIRouter(tags=["v4"])


def _v4_empty_plan(reason: str) -> V4Plan:
    return V4Plan(
        scenario="NONE",
        side_bias="none",
        confidence=0.0,
        zone=None,
        max_lifetime_seconds=1800,
        valid_until_utc=utcnow_iso(),
        layers=[],
        reason_codes=[reason],
        rationale_short=reason[:240],
    )


def _v4_veto(req: V4PlanRequest, reason: str, code: str,
             news: NewsContext | None = None) -> V4PlanResponse:
    return V4PlanResponse(
        schema_version="v4-plan-response.v1",
        request_id=req.request_id,
        status="veto",
        plan=_v4_empty_plan(reason),
        news_context=news or NewsContext(),
        meta=ResponseMeta(model="v4_liquidity_zone"),
        error=AdapterError(code=code, message=reason, retryable=False),
    )


def _v4_degraded(req: V4PlanRequest, reason: str, code: str) -> V4PlanResponse:
    return V4PlanResponse(
        schema_version="v4-plan-response.v1",
        request_id=req.request_id,
        status="degraded",
        plan=_v4_empty_plan(reason),
        meta=ResponseMeta(model="v4_liquidity_zone"),
        error=AdapterError(code=code, message=reason, retryable=False),
    )


@router.post("/v4/plan", response_model=V4PlanResponse)
async def v4_plan(request: Request, x_internal_sig: str | None = Header(default=None)):
    raw = await read_verified_body(request, x_internal_sig)

    try:
        req = V4PlanRequest.model_validate_json(raw)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.errors())

    start = time.perf_counter()

    # 1) Hard safety.
    if req.risk_state.trading_halted:
        resp = _v4_veto(req, "Trading halted by risk_state.", "APP-SAFE-409")
        ledger.write_v4_plan(req, resp)
        return resp
    if req.openclaw_context.mode == "halt":
        resp = _v4_veto(req, "Trading halted by OpenClaw.", "APP-SAFE-409")
        ledger.write_v4_plan(req, resp)
        return resp

    # 2) V4 single basket only.
    if req.has_active_basket:
        resp = _v4_veto(req, "Basket already active (V4 single-basket).", "APP-CONC-409")
        ledger.write_v4_plan(req, resp)
        return resp

    # 3) News blackout (stub).
    bo = get_blackout(req.symbol, _dt.now(_tz.utc))
    news = NewsContext(blackout=bo.blackout, severity=bo.severity, event=bo.event)
    if bo.blackout and bo.severity >= 0.7:
        resp = _v4_veto(req, f"News blackout: {bo.event}", "APP-NEWS-409", news=news)
        ledger.write_v4_plan(req, resp)
        return resp

    # 4) Scenario selection (V4 = single LiquidityZoneEntry).
    try:
        winner = select_best_v4(req)
    except Exception as e:
        logger.exception("v4 scenario selection failed")
        resp = _v4_degraded(req, f"Scenario error: {type(e).__name__}", "APP-SCN-500")
        ledger.write_v4_plan(req, resp)
        return resp

    latency_ms = int((time.perf_counter() - start) * 1000)
    if winner is None:
        resp = V4PlanResponse(
            schema_version="v4-plan-response.v1",
            request_id=req.request_id,
            status="ok",
            plan=_v4_empty_plan("No valid liquidity zone."),
            news_context=news,
            meta=ResponseMeta(model="v4_liquidity_zone", latency_ms=latency_ms),
        )
        ledger.write_v4_plan(req, resp)
        return resp

    # 5) Build plan.
    try:
        plan = build_plan_v4(scenario=winner, req=req)
    except Exception as e:
        logger.exception("v4 planner failed")
        resp = _v4_degraded(req, f"Planner error: {type(e).__name__}", "APP-PLAN-500")
        ledger.write_v4_plan(req, resp)
        return resp

    resp = V4PlanResponse(
        schema_version="v4-plan-response.v1",
        request_id=req.request_id,
        status="ok",
        plan=plan,
        news_context=news,
        meta=ResponseMeta(model="v4_liquidity_zone", latency_ms=latency_ms),
    )
    ledger.write_v4_plan(req, resp)
    return resp

