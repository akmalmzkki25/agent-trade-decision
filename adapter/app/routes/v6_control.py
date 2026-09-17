"""
V6 operator control plane (plan sections 4b, 6 and 9).

    POST /v6/control/session   {"action": "start"|"stop", "reason"?}   bearer token
    GET  /v6/control/session                                            bearer token
    POST /v6/control/halt      {"reason"?}   bearer token, or the dashboard CSRF nonce
    POST /v6/control/resume    {"reason"?}   bearer token; refused while a breaker is tripped
    POST /v6/control/breaker/reset  {"scope", "period_key", "reason"?}   bearer token;
                               judged on a fresh evaluation from the newest EA poll and
                               refused while that scope is still in breach

Every route answers 404 while V6 is not running in this process, serves
loopback clients only, and refuses non-JSON, cross-site and oversized
requests. Token routes answer 503 until V6_OPERATOR_TOKEN is configured.

HALT alone also accepts an unauthenticated same-origin JSON POST carrying the
per-process CSRF nonce rendered into /v6, because stopping must always be
possible, token or not. Resuming never is.

With an execution desk (the V6 runtime's control plane): a session start arms an
execute session when every arming check passes; HALT disarms the session,
cancels undelivered intents, withdraws a pending operator packet and queues
CANCEL_PENDING (after the sentinel is written, so a failure there never blocks
the halt); resume re-arms an active execute session when the checks pass again.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import sqlite3
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Final, Literal, TypeVar

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

from ..security import (
    OPERATOR_MAX_BODY_BYTES, basic_request_guards, read_capped_body, read_operator_body,
    require_loopback_client, require_operator,
)
from ..v6.config import V6Settings
from ..v6.container import DISABLED_DETAIL, V6Container, require_active_container
from ..v6.risk.breakers import BreakerEvaluation, reset_breaker
from ..v6.runtime.arming import DISARM_HALTED
from ..v6.runtime.sessions import ControlPlane

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v6/control", tags=["v6-control"])

CONTROL_STATE_KEY: Final[str] = "v6_control"
CSRF_HEADER: Final[str] = "X-V6-CSRF"
ACTOR_OPERATOR: Final[str] = "operator"
ACTOR_DASHBOARD: Final[str] = "dashboard"
ACTION_HALT: Final[str] = "halt"
ACTION_RESUME: Final[str] = "resume"
ACTION_BREAKER_RESET: Final[str] = "breaker_reset"
REASON_PATTERN: Final[str] = r"^[A-Za-z0-9._:-]{1,64}$"
# risk.breakers.period_key per scope: YYYY-MM-DD, YYYY-Www, YYYY-MM.
PERIOD_KEY_PATTERNS: Final[Mapping[str, re.Pattern[str]]] = MappingProxyType({
    "daily": re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}"),
    "weekly": re.compile(r"[0-9]{4}-W[0-9]{2}"),
    "monthly": re.compile(r"[0-9]{4}-[0-9]{2}"),
})
MAX_PERIOD_KEY_CHARS: Final[int] = 10
NO_FRESH_POLL: Final[str] = "no EA poll recent enough to evaluate the breakers"
NO_DAY_ANCHOR: Final[str] = "no day-start equity known for the polling account yet"
REFUSE_RESUME_BREAKER: Final[str] = "APP-V6-RESUME-BREAKER"
TOKEN_NOT_CONFIGURED: Final[str] = "V6_OPERATOR_TOKEN is not configured"
STORAGE_UNAVAILABLE: Final[str] = "V6 storage unavailable"
HALT_WRITE_FAILED: Final[str] = "halt file could not be written"
HALT_REMOVE_FAILED: Final[str] = "halt file could not be removed"
INVALID_CSRF: Final[str] = "invalid CSRF token"
MAX_REPORTED_ERRORS: Final[int] = 20

ModelT = TypeVar("ModelT", bound=BaseModel)
ResultT = TypeVar("ResultT")
Reason = Annotated[str, Field(pattern=REASON_PATTERN)]


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, allow_inf_nan=False)


class SessionCommand(_Strict):
    action: Literal["start", "stop"]
    reason: Reason = "operator"


class HaltCommand(_Strict):
    reason: Reason = "operator"


class ResumeCommand(_Strict):
    reason: Reason = "operator"


class BreakerResetCommand(_Strict):
    scope: Literal["daily", "weekly", "monthly"]
    period_key: Annotated[str, Field(max_length=MAX_PERIOD_KEY_CHARS)]
    reason: Reason = "operator"

    @model_validator(mode="after")
    def _key_matches_scope(self) -> "BreakerResetCommand":
        if not PERIOD_KEY_PATTERNS[self.scope].fullmatch(self.period_key):
            raise ValueError(f"period_key is not a {self.scope} key")
        return self


@dataclass(frozen=True)
class ControlContext:
    container: V6Container
    plane: ControlPlane


# --- wiring --------------------------------------------------------------------
def install_control_plane(app: FastAPI, plane: ControlPlane | None) -> None:
    setattr(app.state, CONTROL_STATE_KEY, plane)


def control_plane_for(app: FastAPI) -> ControlPlane | None:
    return getattr(app.state, CONTROL_STATE_KEY, None)


def require_control(request: Request) -> ControlContext:
    """FastAPI dependency: container and control plane, or 404 while V6 is not running."""
    container = require_active_container(request)
    plane = control_plane_for(request.app)
    if plane is None:
        raise HTTPException(status_code=404, detail=DISABLED_DETAIL)
    return ControlContext(container=container, plane=plane)


Control = Annotated[ControlContext, Depends(require_control)]


def operator_token_configured(settings: V6Settings) -> bool:
    """Same rule as the settings validator (length, no placeholder)."""
    return settings.operator_token_ok


def _configured_token(settings: V6Settings) -> SecretStr:
    if not operator_token_configured(settings):
        raise HTTPException(status_code=503, detail=TOKEN_NOT_CONFIGURED)
    return settings.operator_token


# --- helpers -------------------------------------------------------------------
def _parse(model: type[ModelT], raw: bytes) -> ModelT:
    try:
        # An empty body means "all defaults"; the content type was still checked.
        return model.model_validate_json(raw or b"{}")
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        raise HTTPException(status_code=400, detail=errors[:MAX_REPORTED_ERRORS]) from exc


async def _storage(call: Awaitable[ResultT]) -> ResultT:
    try:
        return await call
    except sqlite3.Error:
        logger.exception("v6 control storage call failed")
        raise HTTPException(status_code=503, detail=STORAGE_UNAVAILABLE) from None


async def _audit(container: V6Container, actor: str, action: str,
                 detail: Mapping[str, object]) -> None:
    """The audit trail must never block a HALT, so a failed write is only logged."""
    try:
        await asyncio.to_thread(container.ledger_v6.log_control, actor, action, detail)
    except (sqlite3.Error, ValueError):
        logger.exception("v6 control log write failed for %s", action)


async def _halted(container: V6Container) -> bool:
    return await asyncio.to_thread(container.halt_path.exists)


def _create_halt_file(path: Path, content: str) -> bool:
    """True when this call created the sentinel; False when it already existed."""
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(content)
    except FileExistsError:
        return False
    return True


def _remove_halt_file(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


async def _stand_down(ctx: ControlContext, now: float) -> None:
    """HALT's execution side: disarm, cancel undelivered intents, CANCEL_PENDING."""
    plane = ctx.plane
    if plane.desk is None:
        return
    plane.commands.request_cancel_pending(ACTION_HALT, now)
    try:
        session = await plane.sessions.active()
        await plane.desk.disarm(None if session is None else session.session_id,
                                reason=DISARM_HALTED, cancel_reason=DISARM_HALTED, now=now)
    except (sqlite3.Error, ValueError, LookupError) as exc:
        # The sentinel is written: the watchdog disarms on its next tick anyway.
        logger.error("v6 HALT: disarm failed (%s); the watchdog retries", type(exc).__name__)


async def _rearm(ctx: ControlContext, now: float) -> dict[str, object]:
    """Resume's execution side: re-arm the active execute session when allowed."""
    plane = ctx.plane
    session = None if plane.desk is None else await plane.sessions.active()
    if plane.desk is None or session is None:
        return {}
    session, decision = await plane.desk.try_arm(session, now)
    if decision is None:
        return {}
    return {"armed": session.armed,
            "arm": {"armed": decision.armed, "reason": decision.reason,
                    "detail": decision.detail}}


# --- session -------------------------------------------------------------------
@router.post("/session")
async def control_session(request: Request, ctx: Control) -> JSONResponse:
    raw = await read_operator_body(request, _configured_token(ctx.container.settings))
    command = _parse(SessionCommand, raw)
    now = ctx.container.clock.now_epoch()
    sessions = ctx.plane.sessions
    if command.action == "stop":
        stopped = await _storage(sessions.stop(actor=ACTOR_OPERATOR, reason=command.reason,
                                               now=now))
        return JSONResponse(content=stopped.to_dict())
    settings = ctx.container.settings
    started = await _storage(sessions.start(backend=settings.backend, mode=settings.mode,
                                            actor=ACTOR_OPERATOR, now=now))
    return JSONResponse(status_code=409 if started.refused else 200, content=started.to_dict())


@router.get("/session")
async def control_session_status(request: Request, ctx: Control) -> JSONResponse:
    require_operator(request, _configured_token(ctx.container.settings))
    container = ctx.container
    status = await _storage(ctx.plane.sessions.status(container.clock.now_epoch()))
    return JSONResponse(content={
        **status.to_dict(), "mode": container.settings.mode,
        "backend": container.settings.backend, "halted": await _halted(container)})


# --- kill switch ---------------------------------------------------------------
async def _read_dashboard_body(request: Request, nonce: str) -> bytes:
    require_loopback_client(request)
    basic_request_guards(request, OPERATOR_MAX_BODY_BYTES)
    presented = request.headers.get(CSRF_HEADER, "")
    if not (nonce and hmac.compare_digest(presented.encode("utf-8"), nonce.encode("utf-8"))):
        raise HTTPException(status_code=403, detail=INVALID_CSRF)
    return await read_capped_body(request, OPERATOR_MAX_BODY_BYTES)


async def _read_halt_body(request: Request, ctx: ControlContext) -> tuple[bytes, str]:
    """Token path when an Authorization header is sent, dashboard (CSRF) path otherwise."""
    if request.headers.get("authorization") is not None:
        token = _configured_token(ctx.container.settings)
        return await read_operator_body(request, token), ACTOR_OPERATOR
    return await _read_dashboard_body(request, ctx.plane.csrf_nonce), ACTOR_DASHBOARD


@router.post("/halt")
async def control_halt(request: Request, ctx: Control) -> JSONResponse:
    raw, actor = await _read_halt_body(request, ctx)
    command = _parse(HaltCommand, raw)
    container = ctx.container
    now = container.clock.now_epoch()
    content = json.dumps({"actor": actor, "reason": command.reason, "requested_at": now})
    try:
        created = await asyncio.to_thread(_create_halt_file, container.halt_path, content + "\n")
    except OSError:
        logger.exception("v6 HALT FAILED: sentinel %s could not be written", container.halt_path)
        raise HTTPException(status_code=503, detail=HALT_WRITE_FAILED) from None
    logger.warning("v6 HALT requested by %s (%s, new=%s)", actor, command.reason, created)
    await _stand_down(ctx, now)
    await _audit(container, actor, ACTION_HALT, {"reason": command.reason, "created": created})
    return JSONResponse(content={"halted": True, "created": created, "actor": actor,
                                 "halt_file": container.halt_path.name})


@router.post("/resume")
async def control_resume(request: Request, ctx: Control) -> JSONResponse:
    raw = await read_operator_body(request, _configured_token(ctx.container.settings))
    command = _parse(ResumeCommand, raw)
    container = ctx.container
    breakers = await _storage(asyncio.to_thread(ctx.plane.ledger.active_breakers))
    if breakers:
        tripped = ", ".join(f"{b.scope}:{b.period_key}" for b in breakers)
        logger.warning("v6 resume refused: breaker tripped (%s)", tripped)
        return JSONResponse(status_code=409, content={
            "refusal": REFUSE_RESUME_BREAKER, "detail": f"breaker tripped: {tripped}",
            "halted": await _halted(container)})
    try:
        removed = await asyncio.to_thread(_remove_halt_file, container.halt_path)
    except OSError:
        logger.exception("v6 resume: sentinel %s could not be removed", container.halt_path)
        raise HTTPException(status_code=503, detail=HALT_REMOVE_FAILED) from None
    logger.warning("v6 resume by operator (%s, removed=%s)", command.reason, removed)
    await _audit(container, ACTOR_OPERATOR, ACTION_RESUME,
                 {"reason": command.reason, "removed": removed})
    arming = await _storage(_rearm(ctx, container.clock.now_epoch()))
    return JSONResponse(content={"halted": False, "removed": removed, **arming})


# --- breaker reset ---------------------------------------------------------------
async def _fresh_evaluation(container: V6Container,
                            now: float) -> tuple[BreakerEvaluation | None, str]:
    """(evaluation on the newest EA poll, why there is none); breakers persist new trips."""
    observed = container.ea_state.view().last_poll
    if observed is None or not 0 <= now - observed.received_at <= container.settings.ea_stale_s:
        return None, NO_FRESH_POLL
    parts = container.parts
    status = await _storage(parts.breakers.for_poll(observed.poll, now, parts.worker.carry.day))
    if status is None:
        return None, NO_DAY_ANCHOR
    return status.evaluation, status.problem


@router.post("/breaker/reset")
async def control_breaker_reset(request: Request, ctx: Control) -> JSONResponse:
    raw = await read_operator_body(request, _configured_token(ctx.container.settings))
    command = _parse(BreakerResetCommand, raw)
    container = ctx.container
    now = container.clock.now_epoch()
    evaluation, problem = await _fresh_evaluation(container, now)
    result = await _storage(asyncio.to_thread(partial(
        reset_breaker, ctx.plane.ledger, command.scope, command.period_key,
        actor=ACTOR_OPERATOR, evaluation=evaluation, now=now)))
    await _audit(container, ACTOR_OPERATOR, ACTION_BREAKER_RESET, {
        "scope": command.scope, "period_key": command.period_key,
        "reason": command.reason, "result": result.code})
    return JSONResponse(status_code=200 if result.reset else 409, content={
        "reset": result.reset, "code": result.code,
        "detail": result.detail if evaluation is not None else problem,
        "scope": command.scope, "period_key": command.period_key})
