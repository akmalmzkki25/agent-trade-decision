"""
V6 operator plane (plan section 3.3; user decisions 2026-09-16).

    POST /v6/operator/wait      {"timeout_s"?: 0..25, "agent"?: OperatorAgent}
                                long-poll: {"pending": packet|null, "session", "armed", ...}
    POST /v6/operator/decision  one decision v3 (<= 64 KB):
                                202 accepted, 409 {"code"} refused, 422 {"code": "INVALID"}
    GET  /v6/operator/status    the queue status (no secrets, no free text)

The operator agents (Claude Code, Codex or Antigravity: config.OperatorAgent,
enabled by V6_OPERATOR_AGENTS) decide for DEMO accounts only. Every route
answers 404 unless V6 runs in this process with V6_BACKEND=operator and a
queue installed, and is guarded exactly like
/v6/control: loopback clients only, JSON bodies, no cross-site requests, capped
bodies and the V6_OPERATOR_TOKEN bearer token (503 until it is configured).
/wait and /decision answer 403 {"code": "APP-V6-DEMO-403"} unless the newest EA
report shows a DEMO account on a demo server with an allowed login; an agent
outside V6_OPERATOR_AGENTS asking for work gets 403 as well.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Annotated, Final, TypeVar

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from ..security import read_operator_body, require_operator
from ..v6.config import OperatorAgent, V6Settings
from ..v6.container import V6Container
from ..v6.providers.operator_queue import MAX_WAIT_S, REFUSE_INVALID, OperatorQueue
from ..v6.risk.policy import (
    DEMO_403_CODE, OPERATOR_SOURCE, POLICY_OK, POLICY_UNKNOWN_TRADE_MODE, PolicyDecision,
    check_operator_agent, evaluate_account_policy,
)
from ..v6.runtime.ea_state import EaStateView
from ..v6.runtime.sessions import ControlPlane, last_trade_mode, session_to_dict
from ..v6.schemas.operator import MAX_DECISION_BYTES
from .v6_control import (
    STORAGE_UNAVAILABLE, TOKEN_NOT_CONFIGURED, operator_token_configured, require_control,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v6/operator", tags=["v6-operator"])

QUEUE_STATE_KEY: Final[str] = "v6_operator_queue"
BACKEND_OFF_DETAIL: Final[str] = "V6 operator backend disabled"
NO_EA_REPORT: Final[str] = "no EA poll has reported the account yet"
MAX_REPORTED_ERRORS: Final[int] = 20
STATUS_ACCEPTED: Final[int] = 202
STATUS_FORBIDDEN: Final[int] = 403
STATUS_CONFLICT: Final[int] = 409
STATUS_INVALID: Final[int] = 422

ModelT = TypeVar("ModelT", bound=BaseModel)
ResultT = TypeVar("ResultT")


class WaitCommand(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, allow_inf_nan=False)

    timeout_s: float = Field(default=MAX_WAIT_S, ge=0.0, le=MAX_WAIT_S)
    agent: OperatorAgent | None = None


@dataclass(frozen=True)
class OperatorContext:
    container: V6Container
    plane: ControlPlane
    queue: OperatorQueue


# --- wiring --------------------------------------------------------------------------------
def install_operator_queue(app: FastAPI, queue: OperatorQueue | None) -> None:
    setattr(app.state, QUEUE_STATE_KEY, queue)


def operator_queue_for(app: FastAPI) -> OperatorQueue | None:
    return getattr(app.state, QUEUE_STATE_KEY, None)


def require_operator_plane(request: Request) -> OperatorContext:
    """FastAPI dependency: 404 unless V6 runs here with the operator backend and a queue."""
    control = require_control(request)
    queue = operator_queue_for(request.app)
    if control.container.settings.backend != OPERATOR_SOURCE or queue is None:
        raise HTTPException(status_code=404, detail=BACKEND_OFF_DETAIL)
    return OperatorContext(container=control.container, plane=control.plane, queue=queue)


Operator = Annotated[OperatorContext, Depends(require_operator_plane)]


# --- helpers -------------------------------------------------------------------------------
def _token(settings: V6Settings) -> SecretStr:
    if not operator_token_configured(settings):
        raise HTTPException(status_code=503, detail=TOKEN_NOT_CONFIGURED)
    return settings.operator_token


def _parse(model: type[ModelT], raw: bytes) -> ModelT:
    try:
        return model.model_validate_json(raw or b"{}")
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        raise HTTPException(status_code=400, detail=errors[:MAX_REPORTED_ERRORS]) from exc


async def _storage(call: Awaitable[ResultT]) -> ResultT:
    try:
        return await call
    except sqlite3.Error:
        logger.exception("v6 operator storage call failed")
        raise HTTPException(status_code=503, detail=STORAGE_UNAVAILABLE) from None


def account_refusal(view: EaStateView, settings: V6Settings) -> PolicyDecision | None:
    """Why operator agents may not act on the account the EA reports; None when they may.

    Trade mode: the newest EA poll or snapshot. Server and login: the newest poll.
    Without any poll the account is unknown and the answer is a refusal.
    """
    observed = view.last_poll
    if observed is None:
        return PolicyDecision(allowed=False, code=POLICY_UNKNOWN_TRADE_MODE, detail=NO_EA_REPORT)
    poll = observed.poll
    trade_mode = last_trade_mode(view) or poll.trade_mode
    decision = evaluate_account_policy(trade_mode, poll.server, poll.login, settings,
                                       source=OPERATOR_SOURCE)
    return None if decision.allowed else decision


def _forbidden(decision: PolicyDecision, code: str) -> JSONResponse:
    logger.warning("v6 operator request refused: %s (%s)", code, decision.code)
    return JSONResponse(status_code=STATUS_FORBIDDEN, content={
        "code": code, "policy": decision.code, "detail": decision.detail})


def _demo_refusal(ctx: OperatorContext) -> JSONResponse | None:
    refusal = account_refusal(ctx.container.ea_state.view(), ctx.container.settings)
    return None if refusal is None else _forbidden(refusal, DEMO_403_CODE)


# --- routes --------------------------------------------------------------------------------
@router.post("/wait")
async def operator_wait(request: Request, ctx: Operator) -> JSONResponse:
    settings = ctx.container.settings
    command = _parse(WaitCommand, await read_operator_body(request, _token(settings)))
    clock = ctx.container.clock
    if command.agent is not None:
        agent = check_operator_agent(command.agent, settings)
        if not agent.allowed:
            return _forbidden(agent, agent.code)
        ctx.queue.note_agent(command.agent, clock.now_epoch())
    refused = _demo_refusal(ctx)
    if refused is not None:
        return refused
    packet = await ctx.queue.take_pending(command.timeout_s)
    refused = _demo_refusal(ctx)          # the account may have changed during the wait
    if refused is not None:
        return refused
    session = await _storage(ctx.plane.sessions.active())
    return JSONResponse(content={
        "pending": None if packet is None else packet.model_dump(mode="json"),
        "session": None if session is None else session_to_dict(session),
        "armed": bool(session is not None and session.armed),
        "session_renewal_due": ctx.plane.sessions.renewal_due,
        "mode": settings.mode, "server_time_epoch": int(clock.now_epoch()),
    })


@router.post("/decision")
async def operator_decision(request: Request, ctx: Operator) -> JSONResponse:
    settings = ctx.container.settings
    raw = await read_operator_body(request, _token(settings), limit=MAX_DECISION_BYTES)
    refused = _demo_refusal(ctx)
    if refused is not None:
        return refused
    result = ctx.queue.submit(raw, ctx.container.clock.now_epoch())
    if result.accepted:
        return JSONResponse(status_code=STATUS_ACCEPTED, content=result.to_dict())
    status = STATUS_INVALID if result.code == REFUSE_INVALID else STATUS_CONFLICT
    return JSONResponse(status_code=status, content=result.to_dict())


@router.get("/status")
async def operator_status(request: Request, ctx: Operator) -> JSONResponse:
    settings = ctx.container.settings
    require_operator(request, _token(settings))
    now = ctx.container.clock.now_epoch()
    refusal = account_refusal(ctx.container.ea_state.view(), settings)
    return JSONResponse(content={
        **ctx.queue.status(now).to_dict(),
        "backend": settings.backend, "mode": settings.mode,
        "operator_agents": list(settings.operator_agents),
        "account_policy": POLICY_OK if refusal is None else refusal.code,
        "server_time_epoch": int(now),
    })
