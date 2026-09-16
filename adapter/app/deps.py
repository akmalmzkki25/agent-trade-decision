"""
Shared application singletons and helpers used across routers.

Kept separate from `main` so routers can import them without creating a circular
import back through the FastAPI app object.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi.templating import Jinja2Templates

from . import __version__
from .deciders import get_decider
from .ledger import Ledger
from .settings import settings

logger = logging.getLogger("adapter")

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

ledger = Ledger(settings.db_path)
decider = get_decider()


def utcnow_iso() -> str:
    """Timezone-aware UTC timestamp in ISO-8601."""
    return datetime.now(timezone.utc).isoformat()


def decider_name() -> str:
    return getattr(decider, "name", "unknown")


def template_globals() -> dict[str, str]:
    """Values every server-rendered page needs in its footer/header."""
    return {
        "adapter_version": __version__,
        "decider_name": decider_name(),
        "db_path": settings.db_path,
    }
