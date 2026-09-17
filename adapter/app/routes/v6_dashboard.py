"""
Read-only V6 dashboard (plan section 8).

    GET /v6                       page (templates/v6.html), always rendered
    GET /v6/api/overview          JSON polled by the page every 5 s; 404 while V6 is off
    GET /v6/static/v6_render.js   the DOM renderer (window.QlipV6Render), from app/static
    GET /v6/static/v6.js          the page script, loaded after the renderer

The page carries the per-process CSRF nonce its HALT button needs, and every
overview repeats it (same-origin JSON; the host guard blocks DNS rebinding), so
an open page follows an adapter restart. It renders no ledger data server-side:
static/v6.js builds the DOM from the overview JSON with textContent only.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from functools import partial
from pathlib import Path
from typing import Final

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ..deps import template_globals, templates
from ..v6.container import APP_STATE_KEY, V6Container
from ..v6.dashboard_queries import (
    OperatorActivity, OverviewState, build_overview, read_tables,
)
from .v6_control import STORAGE_UNAVAILABLE, Control, control_plane_for

logger = logging.getLogger(__name__)

router = APIRouter(tags=["v6-dashboard"])

STATIC_DIR: Final[Path] = Path(__file__).resolve().parents[1] / "static"
SCRIPT_PATH: Final[Path] = STATIC_DIR / "v6.js"
RENDER_SCRIPT_PATH: Final[Path] = STATIC_DIR / "v6_render.js"
SCRIPT_HEADERS: Final[dict[str, str]] = {"Cache-Control": "no-cache"}
TEMPLATE_NAME: Final[str] = "v6.html"
POLL_INTERVAL_MS: Final[int] = 5000
NO_STORE: Final[dict[str, str]] = {"Cache-Control": "no-store"}


@router.get("/v6", response_class=HTMLResponse, include_in_schema=False)
async def v6_page(request: Request) -> HTMLResponse:
    container = getattr(request.app.state, APP_STATE_KEY, None)
    plane = control_plane_for(request.app)
    enabled = container is not None and container.active and plane is not None
    context = {
        "request": request,
        "active_page": "v6",
        "v6_enabled": enabled,
        "v6_mode": container.settings.mode if container is not None else "off",
        "csrf_nonce": plane.csrf_nonce if enabled and plane is not None else "",
        "poll_interval_ms": POLL_INTERVAL_MS,
        **template_globals(),
    }
    response = templates.TemplateResponse(request, TEMPLATE_NAME, context)
    response.headers.update({**NO_STORE, "X-Frame-Options": "DENY"})
    return response


def operator_activity(container: V6Container, now: float) -> OperatorActivity:
    """What the operator queue last saw (agent, pending cycle and its deadline)."""
    status = container.parts.operator_queue.status(now)
    agent, pending = status.last_agent, status.pending
    return OperatorActivity(
        last_agent=None if agent is None else agent.agent,
        last_seen_at=None if agent is None else agent.at,
        pending_cycle_id=None if pending is None else pending.cycle_id,
        pending_deadline=None if pending is None else float(pending.expires_at))


@router.get("/v6/api/overview")
async def v6_overview(ctx: Control) -> JSONResponse:
    container, plane = ctx.container, ctx.plane
    now = container.clock.now_epoch()
    state = OverviewState(
        now=now, active=container.active, settings=container.settings,
        ea=container.ea_state.view(), command=plane.commands.current(now),
        watchdog_breaker_tripped=container.parts.watchdog.state.breakers_tripped,
        csrf_nonce=plane.csrf_nonce, operator=operator_activity(container, now))
    try:
        tables = await asyncio.to_thread(partial(
            read_tables, plane.ledger, db_path=container.ledger_v6.path,
            halt_path=container.halt_path, now=now))
    except sqlite3.Error:
        logger.exception("v6 dashboard overview read failed")
        raise HTTPException(status_code=503, detail=STORAGE_UNAVAILABLE) from None
    return JSONResponse(content=build_overview(tables, state), headers=NO_STORE)


@router.get("/v6/static/v6.js", include_in_schema=False)
async def v6_script() -> FileResponse:
    return FileResponse(SCRIPT_PATH, media_type="text/javascript", headers=SCRIPT_HEADERS)


@router.get("/v6/static/v6_render.js", include_in_schema=False)
async def v6_render_script() -> FileResponse:
    return FileResponse(RENDER_SCRIPT_PATH, media_type="text/javascript",
                        headers=SCRIPT_HEADERS)
