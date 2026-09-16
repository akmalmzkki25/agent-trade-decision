"""
MT5 trading adapter — application wiring only.

Each API surface lives in its own router under `app/routes/`:

    /v1/decision                V1 single-decision       routes/decision.py
    /v1/healthz, /v1/events/*   health + EA event intake routes/events.py
    /v2/plan                    bulk layering            routes/plans_v2.py
    /v3/plan                    aggressive mixed ladder  routes/plans_v3.py
    /v4/plan                    liquidity-zone entry     routes/plans_v4.py
    /v5/burst                   scalping burst           routes/burst_v5.py
    /v6/*                       V6 EA data plane         routes/v6_ea.py
    /v6/control/*               V6 operator controls     routes/v6_control.py
    /v6, /v6/api/overview       V6 dashboard             routes/v6_dashboard.py
    /dashboard, /api/dashboard  analytics UI + JSON      routes/dashboard.py

Shared singletons (ledger, decider, templates) live in `app/deps.py` so routers
can import them without a circular import back through this module.

Run with a single worker and no reload (`--workers 1`): the V6 runtime is an
in-process task guarded by a database lock.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Final

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from . import __version__
from .deps import decider_name, ledger, logger
from .host_guard import LOOPBACK_HOSTS, HostGuardMiddleware, normalise_host
from .routes import (
    burst_v5, dashboard, decision, events, plans_v2, plans_v3, plans_v4, v6_control,
    v6_dashboard, v6_ea,
)
from .routes.v6_control import install_control_plane
from .settings import WILDCARD_BIND_HOSTS, settings
from .v6.clock import Clock
from .v6.config import V6Settings
from .v6.container import APP_STATE_KEY, container_for_app, v6_lifespan

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("adapter starting (version=%s, decider=%s)", __version__, decider_name())
    if not settings.hmac_required:
        logger.warning("HMAC check DISABLED (dev mode). Set HMAC_REQUIRED=true for production.")
    async with v6_lifespan(app):
        yield
    logger.info("adapter shutting down")


# Sent on every response. The dashboard loads Tailwind and Chart.js from CDNs,
# so script-src names them explicitly instead of allowing everything.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "object-src 'none'"
    ),
}


def allowed_hosts() -> list[str]:
    """DNS-rebinding defence (plan section 9): loopback names, the bind host when it
    names one address, and ALLOWED_HOSTS. Wildcard binds are never a host name."""
    named_bind = () if settings.adapter_host in WILDCARD_BIND_HOSTS else (settings.adapter_host,)
    hosts = [*LOOPBACK_HOSTS]
    for host in map(normalise_host, (*named_bind, *settings.extra_allowed_hosts)):
        if host not in hosts:
            hosts.append(host)
    return hosts


ROUTERS: Final[tuple[APIRouter, ...]] = (
    events.router,
    decision.router,
    plans_v2.router,
    plans_v3.router,
    plans_v4.router,
    burst_v5.router,
    v6_ea.router,
    v6_control.router,
    v6_dashboard.router,
    dashboard.router,
)


def _install_middleware(application: FastAPI) -> None:
    # Added first, so the security-headers middleware below wraps its 400 as well.
    application.add_middleware(HostGuardMiddleware, allowed_hosts=allowed_hosts())

    @application.middleware("http")
    async def add_security_headers(request, call_next):
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response


def create_app(
    v6_settings: V6Settings | None = None,
    clock: Clock | None = None,
    v6_db_path: str | None = None,
    v6_tasks: bool = True,
) -> FastAPI:
    """Build the FastAPI application and mount every router.

    The V6 arguments exist for isolated test apps; `None` means V6 settings
    from the environment, the system clock and the adapter database.
    `v6_tasks=False` keeps the V6 worker and watchdog off (data-plane tests).
    """
    application = FastAPI(
        title="mt5-claude-adapter",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url="/redoc" if settings.enable_docs else None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )
    container = container_for_app(v6_settings, v6_db_path or settings.db_path, clock,
                                  run_tasks=v6_tasks)
    setattr(application.state, APP_STATE_KEY, container)
    install_control_plane(application, None if container is None else container.parts.control)
    _install_middleware(application)
    for router in ROUTERS:
        application.include_router(router)

    @application.exception_handler(HTTPException)
    async def http_error(_, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return application


app = create_app()

# Re-exported for tests and scripts that reach for the ledger directly.
__all__ = ["app", "create_app", "ledger"]
