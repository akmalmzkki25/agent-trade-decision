"""V5 scalping burst endpoint (tick-driven, basket-managed)."""

from __future__ import annotations

import time
from datetime import datetime as _dt, timezone as _tz

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import ValidationError

from ..deps import ledger, logger, utcnow_iso
from ..layering import build_burst_v5
from ..models import (
    AdapterError,
    NewsContext,
    ResponseMeta,
    V5Burst,
    V5BurstRequest,
    V5BurstResponse,
    V5ExitRules,
)
from ..news import get_blackout
from ..scenarios import MIN_SCENARIO_SCORE_V5, evaluate_v5
from ..security import read_verified_body

router = APIRouter(tags=["v5"])


def _v5_empty_burst(reason: str) -> V5Burst:
    return V5Burst(
        scenario="NONE",
        side_bias="none",
        confidence=0.0,
        valid_until_utc=utcnow_iso(),
        layers=[],
        reason_codes=[reason],
        rationale_short=reason[:240],
    )


def _v5_veto(req: V5BurstRequest, reason: str, code: str,
             news: NewsContext | None = None) -> V5BurstResponse:
    return V5BurstResponse(
        schema_version="v5-burst-response.v1",
        request_id=req.request_id,
        status="veto",
        burst=_v5_empty_burst(reason),
        news_context=news or NewsContext(),
        meta=ResponseMeta(model="v5_scalp_micro"),
        error=AdapterError(code=code, message=reason, retryable=False),
    )


def _v5_degraded(req: V5BurstRequest, reason: str, code: str) -> V5BurstResponse:
    return V5BurstResponse(
        schema_version="v5-burst-response.v1",
        request_id=req.request_id,
        status="degraded",
        burst=_v5_empty_burst(reason),
        meta=ResponseMeta(model="v5_scalp_micro"),
        error=AdapterError(code=code, message=reason, retryable=False),
    )


_V5_DEFAULTS = V5ExitRules()  # singleton for veto checks


@router.post("/v5/burst", response_model=V5BurstResponse)
async def v5_burst(request: Request, x_internal_sig: str | None = Header(default=None)):
    raw = await read_verified_body(request, x_internal_sig)

    try:
        req = V5BurstRequest.model_validate_json(raw)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.errors())

    start = time.perf_counter()

    # 1) Hard safety.
    if req.risk_state.trading_halted:
        resp = _v5_veto(req, "Trading halted by risk_state.", "APP-SAFE-409")
        ledger.write_v5_burst(req, resp)
        return resp
    if req.openclaw_context.mode == "halt":
        resp = _v5_veto(req, "Trading halted by OpenClaw.", "APP-SAFE-409")
        ledger.write_v5_burst(req, resp)
        return resp

    # 2) Margin guard.
    if req.margin_level_pct > 0 and req.margin_level_pct < 300.0:
        resp = _v5_veto(req, f"Margin level too low: {req.margin_level_pct:.0f}% < 300%", "APP-MARGIN-409")
        ledger.write_v5_burst(req, resp)
        return resp

    # 3) Rate-limit gate (per-EA self-throttle — also enforced server-side here).
    if req.last_burst_ms_ago < _V5_DEFAULTS.min_burst_interval_ms:
        resp = _v5_veto(req, f"Rate limit: last burst {req.last_burst_ms_ago}ms ago", "APP-RATE-429")
        ledger.write_v5_burst(req, resp)
        return resp

    # 4) Basket capacity gate.
    if req.active_basket_bursts >= _V5_DEFAULTS.max_bursts_per_basket:
        resp = _v5_veto(req, "Basket capacity reached.", "APP-CAPACITY-409")
        ledger.write_v5_burst(req, resp)
        return resp

    # 5) News blackout (stub).
    bo = get_blackout(req.symbol, _dt.now(_tz.utc))
    news = NewsContext(blackout=bo.blackout, severity=bo.severity, event=bo.event)
    if bo.blackout and bo.severity >= 0.7:
        resp = _v5_veto(req, f"News blackout: {bo.event}", "APP-NEWS-409", news=news)
        ledger.write_v5_burst(req, resp)
        return resp

    # 6) Scenario. Evaluate once and keep the full result: when it declines,
    # its reason codes are the answer to "why isn't the bot trading?".
    try:
        assessment = evaluate_v5(req)
    except Exception as e:
        logger.exception("v5 scenario selection failed")
        resp = _v5_degraded(req, f"Scenario error: {type(e).__name__}", "APP-SCN-500")
        ledger.write_v5_burst(req, resp)
        return resp

    latency_ms = int((time.perf_counter() - start) * 1000)
    tradeable = assessment.score >= MIN_SCENARIO_SCORE_V5 and assessment.side != "none"
    if not tradeable:
        declined = _v5_empty_burst("No valid scalp setup.")
        # Surface the specific gate that blocked, not just a generic "no setup".
        declined.reason_codes = assessment.reason_codes or ["NO_SETUP"]
        declined.confidence = assessment.confidence
        declined.rationale_short = (
            f"declined at score {assessment.score:.2f} "
            f"(needs {MIN_SCENARIO_SCORE_V5}): {', '.join(declined.reason_codes)}"
        )[:240]
        resp = V5BurstResponse(
            schema_version="v5-burst-response.v1",
            request_id=req.request_id,
            status="ok",
            burst=declined,
            news_context=news,
            meta=ResponseMeta(model="v5_scalp_micro", latency_ms=latency_ms),
        )
        ledger.write_v5_burst(req, resp)
        return resp

    winner = assessment

    # 7) Build burst.
    try:
        burst = build_burst_v5(scenario=winner, req=req)
    except Exception as e:
        logger.exception("v5 planner failed")
        resp = _v5_degraded(req, f"Planner error: {type(e).__name__}", "APP-PLAN-500")
        ledger.write_v5_burst(req, resp)
        return resp

    resp = V5BurstResponse(
        schema_version="v5-burst-response.v1",
        request_id=req.request_id,
        status="ok",
        burst=burst,
        news_context=news,
        meta=ResponseMeta(model="v5_scalp_micro", latency_ms=latency_ms),
    )
    ledger.write_v5_burst(req, resp)
    return resp

