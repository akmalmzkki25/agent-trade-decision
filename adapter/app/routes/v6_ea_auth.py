"""
The guard every V6 EA route runs (docs/v6-wire-contract.md sections 1 and 3).

First the generic body guards: JSON content type (415), no cross-site request
(403) and the size cap (413), before and while the body is read. Then, in execute
mode, the V6 request signature: X-Qlip6-Ts and X-Qlip6-Sig over ts, method, path
and the raw body, inside the +/-30 s window and never twice; anything else is
401 {"detail": "<SIG_* code>"}. In shadow the headers are ignored.

The V1-V5 X-Internal-Sig / HMAC_REQUIRED never apply to V6 routes (the V5 EA does
not sign, the V6 EA signs with its own key). Neither the key nor a signature is
ever logged.
"""

from __future__ import annotations

import logging
from typing import Final

from fastapi import HTTPException, Request

from ..security import basic_request_guards, read_capped_body
from ..settings import settings as adapter_settings
from ..v6.container import V6Container
from ..v6.wire import SIG_HEADER, TS_HEADER, verify_request

logger = logging.getLogger(__name__)

SIGNING_REQUIRED: Final[str] = "required"
STATUS_UNAUTHORIZED: Final[int] = 401


async def read_ea_body(request: Request, container: V6Container) -> bytes:
    """The raw body of a V6 EA request that passed every guard."""
    limit = adapter_settings.max_request_bytes
    basic_request_guards(request, limit)
    raw = await read_capped_body(request, limit)
    settings = container.settings
    if settings.ea_signing != SIGNING_REQUIRED:
        return raw
    check = verify_request(
        key=settings.ea_hmac_key, ts_header=request.headers.get(TS_HEADER),
        sig_header=request.headers.get(SIG_HEADER), method=request.method,
        path=request.url.path, body=raw, now=container.clock.now_epoch(),
        cache=container.parts.replay_cache)
    if not check.ok:
        logger.warning("v6 EA request refused: %s %s (%s)", request.method,
                       request.url.path, check.code)
        raise HTTPException(status_code=STATUS_UNAUTHORIZED, detail=check.code)
    return raw
