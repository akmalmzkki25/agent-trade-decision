"""Server-rendered dashboard pages and the read-only JSON API behind them."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import dashboard_db, metrics
from ..deps import templates, template_globals

router = APIRouter(tags=["dashboard"])


@router.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/dashboard")


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_view(request: Request):
    ctx = {
        "request": request,
        "active_page": "dashboard",
        "stats": dashboard_db.get_stats(),
        "decisions": dashboard_db.recent_decisions(limit=25),
        "plans": dashboard_db.recent_plans(limit=15),
        "trade_events": dashboard_db.recent_trade_events(limit=10),
        "action_distribution": dashboard_db.action_distribution(),
        "perf": metrics.get_performance_metrics(),
        "equity_curve": metrics.get_equity_curve(limit=200),
        **template_globals(),
    }
    return templates.TemplateResponse(request, "dashboard.html", ctx)


@router.get("/chatbot", response_class=HTMLResponse, include_in_schema=False)
async def chatbot_view(request: Request):
    ctx = {
        "request": request,
        "active_page": "chatbot",
        **template_globals(),
    }
    return templates.TemplateResponse(request, "chatbot.html", ctx)


@router.get("/api/dashboard/stats")
async def api_stats():
    return dashboard_db.get_stats()


@router.get("/api/dashboard/decisions")
async def api_decisions(limit: int = 25):
    limit = max(1, min(limit, 200))
    return dashboard_db.recent_decisions(limit=limit)


@router.get("/api/dashboard/plans")
async def api_plans(limit: int = 25):
    limit = max(1, min(limit, 200))
    return dashboard_db.recent_plans(limit=limit)


@router.get("/api/dashboard/trade-events")
async def api_trade_events(limit: int = 25):
    limit = max(1, min(limit, 200))
    return dashboard_db.recent_trade_events(limit=limit)


@router.get("/api/dashboard/action-distribution")
async def api_action_distribution():
    return dashboard_db.action_distribution()


@router.get("/api/dashboard/metrics")
async def api_metrics(version: str | None = None, limit: int = 5000):
    """Profit factor, win rate, EV, latency, slippage, max floating DD."""
    return metrics.get_performance_metrics(version=version, limit=limit)


@router.get("/api/dashboard/equity-curve")
async def api_equity_curve(version: str | None = None, limit: int = 500):
    return metrics.get_equity_curve(version=version, limit=limit)


@router.get("/api/dashboard/basket-results")
async def api_basket_results(version: str | None = None, limit: int = 25):
    return metrics.recent_basket_results(version=version, limit=limit)

