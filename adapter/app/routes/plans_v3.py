"""V3 aggressive mixed-ladder plan endpoint."""

from __future__ import annotations

import time
from datetime import datetime as _dt, timezone as _tz

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import ValidationError

from ..deps import ledger, logger, utcnow_iso
from ..layering import assign_slot, build_plan_v3
from ..models import (
    AdapterError,
    FundamentalContext,
    NewsContext,
    ResponseMeta,
    V3Plan,
    V3PlanRequest,
    V3PlanResponse,
)
from ..news import get_bias_modifier, get_blackout
from ..scenarios import select_best_v3
from ..security import read_verified_body

router = APIRouter(tags=["v3"])


def _v3_empty_plan(reason: str, slot=None) -> V3Plan:
    return V3Plan(
        scenario="NONE",
        side_bias="none",
        confidence=0.0,
        basket_slot=slot,
        basket_tp_pct_equity=0.0,
        scenario_invalidation_price=None,
        max_lifetime_seconds=600,
        valid_until_utc=utcnow_iso(),
        layers=[],
        reason_codes=[reason],
        rationale_short=reason[:240],
    )


def _v3_veto(req: V3PlanRequest, reason: str, code: str,
             news: NewsContext | None = None,
             fund: FundamentalContext | None = None) -> V3PlanResponse:
    return V3PlanResponse(
        schema_version="v3-plan-response.v1",
        request_id=req.request_id,
        status="veto",
        plan=_v3_empty_plan(reason),
        news_context=news or NewsContext(),
        fundamental_context=fund or FundamentalContext(),
        meta=ResponseMeta(model="v3_aggressive_mixed"),
        error=AdapterError(code=code, message=reason, retryable=False),
    )


def _v3_degraded(req: V3PlanRequest, reason: str, code: str) -> V3PlanResponse:
    return V3PlanResponse(
        schema_version="v3-plan-response.v1",
        request_id=req.request_id,
        status="degraded",
        plan=_v3_empty_plan(reason),
        meta=ResponseMeta(model="v3_aggressive_mixed"),
        error=AdapterError(code=code, message=reason, retryable=False),
    )


@router.post("/v3/plan", response_model=V3PlanResponse)
async def v3_plan(request: Request, x_internal_sig: str | None = Header(default=None)):
    raw = await read_verified_body(request, x_internal_sig)

    try:
        req = V3PlanRequest.model_validate_json(raw)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.errors())

    start = time.perf_counter()

    # 1) Hard safety.
    if req.risk_state.trading_halted:
        resp = _v3_veto(req, "Trading halted by risk_state.", "APP-SAFE-409")
        ledger.write_v3_plan(req, resp)
        return resp
    if req.openclaw_context.mode == "halt":
        resp = _v3_veto(req, "Trading halted by OpenClaw.", "APP-SAFE-409")
        ledger.write_v3_plan(req, resp)
        return resp

    # 2) Spread guard (M1-based, slightly tighter than V2).
    atr_m1 = float(req.features.execution_tf.get("atr_abs_m1", req.features.execution_tf.get("atr_abs", 0.0)))
    if atr_m1 > 0:
        point = req.market.tick_size if req.market.tick_size > 0 else 0.01
        spread_price = req.market.spread_points * point
        if spread_price / atr_m1 > 0.30:
            resp = _v3_veto(req, "Spread too wide vs ATR_M1.", "APP-SPREAD-409")
            ledger.write_v3_plan(req, resp)
            return resp

    # 3) News blackout (stub for now).
    bo = get_blackout(req.symbol, _dt.now(_tz.utc))
    news = NewsContext(blackout=bo.blackout, severity=bo.severity, event=bo.event)
    if bo.blackout and bo.severity >= 0.7:
        resp = _v3_veto(req, f"News blackout: {bo.event}", "APP-NEWS-409", news=news)
        ledger.write_v3_plan(req, resp)
        return resp

    # 4) Fundamental bias (DXY/VIX).
    fund = get_bias_modifier(req.symbol, req.dxy_features, req.vix_features)

    # 5) Scenario selection (V3 includes MOMENTUM_M1; threshold 0.40).
    try:
        winner = select_best_v3(req)
    except Exception as e:
        logger.exception("v3 scenario selection failed")
        resp = _v3_degraded(req, f"Scenario error: {type(e).__name__}", "APP-SCN-500")
        ledger.write_v3_plan(req, resp)
        return resp

    latency_ms = int((time.perf_counter() - start) * 1000)
    if winner is None:
        resp = V3PlanResponse(
            schema_version="v3-plan-response.v1",
            request_id=req.request_id,
            status="ok",
            plan=_v3_empty_plan("No scenario above V3 threshold."),
            news_context=news,
            fundamental_context=fund,
            meta=ResponseMeta(model="v3_aggressive_mixed", latency_ms=latency_ms),
        )
        ledger.write_v3_plan(req, resp)
        return resp

    # 6) Slot assignment (concurrency control).
    slot, slot_reason = assign_slot(req.active_baskets, winner.side)
    if slot is None:
        msg = (
            "Same-side basket already active." if slot_reason == "APP-CONC-409"
            else "All basket slots full."
        )
        resp = _v3_veto(req, msg, slot_reason, news=news, fund=fund)
        ledger.write_v3_plan(req, resp)
        return resp

    # 7) Build plan.
    try:
        plan = build_plan_v3(scenario=winner, req=req, slot=slot, gold_bias=fund.gold_bias)
    except Exception as e:
        logger.exception("v3 planner failed")
        resp = _v3_degraded(req, f"Planner error: {type(e).__name__}", "APP-PLAN-500")
        ledger.write_v3_plan(req, resp)
        return resp

    resp = V3PlanResponse(
        schema_version="v3-plan-response.v1",
        request_id=req.request_id,
        status="ok",
        plan=plan,
        news_context=news,
        fundamental_context=fund,
        meta=ResponseMeta(model="v3_aggressive_mixed", latency_ms=latency_ms),
    )
    ledger.write_v3_plan(req, resp)
    return resp

