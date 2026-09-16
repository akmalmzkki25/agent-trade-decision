"""
MT5 trading adapter — application wiring only.

Each API surface lives in its own router under `app/routes/`:

    /v1/decision                V1 single-decision       routes/decision.py
    /v1/healthz, /v1/events/*   health + EA event intake routes/events.py
    /v2/plan                    bulk layering            routes/plans_v2.py
    /v3/plan                    aggressive mixed ladder  routes/plans_v3.py
    /v4/plan                    liquidity-zone entry     routes/plans_v4.py
    /v5/burst                   scalping burst           routes/burst_v5.py
    /dashboard, /api/dashboard  analytics UI + JSON      routes/dashboard.py

Shared singletons (ledger, decider, templates) live in `app/deps.py` so routers
can import them without a circular import back through this module.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from . import __version__
from .deps import decider_name, ledger, logger
from .routes import burst_v5, dashboard, decision, events, plans_v2, plans_v3, plans_v4
from .settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("adapter starting (version=%s, decider=%s)", __version__, decider_name())
    if not settings.hmac_required:
        logger.warning("HMAC check DISABLED (dev mode). Set HMAC_REQUIRED=true for production.")
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


def create_app() -> FastAPI:
    """Build the FastAPI application and mount every router."""
    application = FastAPI(
        title="mt5-claude-adapter",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url="/redoc" if settings.enable_docs else None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )

    @application.middleware("http")
    async def add_security_headers(request, call_next):
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    for router in (
        events.router,
        decision.router,
        plans_v2.router,
        plans_v3.router,
        plans_v4.router,
        burst_v5.router,
        dashboard.router,
    ):
        application.include_router(router)

    @application.exception_handler(HTTPException)
    async def http_error(_, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return application


app = create_app()

# Re-exported for tests and scripts that reach for the ledger directly.
__all__ = ["app", "create_app", "ledger"]
