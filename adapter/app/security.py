"""
Request authentication and cross-site request forgery defence.

The adapter binds to localhost and is called by a MetaTrader 5 Expert Advisor
via WebRequest(). "Localhost only" is NOT by itself a security boundary: any
web page the operator happens to open can issue a cross-origin POST to
127.0.0.1 without a CORS preflight, as long as the request qualifies as a
"simple request" (e.g. Content-Type: text/plain). If the server ignores the
declared content type, such a forged request is indistinguishable from a real
one and can poison the ledger or trigger decision work.

Three layers guard against that:

1. require_json_content_type — a forged simple request cannot set
   `Content-Type: application/json` without triggering a preflight the browser
   will block, so demanding it rejects drive-by POSTs outright.
2. reject_cross_site — browsers attach Sec-Fetch-Site / Origin on requests they
   initiate. MT5's WebRequest() sends neither, so anything that looks
   browser-initiated and cross-site is refused.
3. hmac_ok — optional shared-secret signature, mandatory whenever the service
   is bound to a non-loopback address (enforced in settings).
"""

from __future__ import annotations

import hashlib
import hmac

from fastapi import HTTPException, Request

from .settings import settings

_JSON_CONTENT_TYPES = ("application/json",)
_SAFE_FETCH_SITES = ("same-origin", "same-site", "none")


def hmac_ok(raw_body: bytes, signature: str | None) -> bool:
    """Verify the HMAC-SHA256 signature of a request body (timing-safe)."""
    if not settings.hmac_required:
        return True
    if not signature:
        return False
    expected = hmac.new(
        settings.internal_hmac_key.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def require_json_content_type(request: Request) -> None:
    """Reject bodies not declared as JSON. Blocks CSRF via simple requests."""
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type not in _JSON_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Content-Type must be application/json",
        )


def reject_cross_site(request: Request) -> None:
    """
    Refuse requests a browser marked as cross-site.

    MT5's WebRequest() sends neither Sec-Fetch-Site nor Origin, so legitimate EA
    traffic passes untouched. A browser-originated cross-site POST carries
    Sec-Fetch-Site: cross-site (or an Origin that is not our own) and is denied.
    """
    fetch_site = (request.headers.get("sec-fetch-site") or "").lower()
    if fetch_site and fetch_site not in _SAFE_FETCH_SITES:
        raise HTTPException(status_code=403, detail="cross-site request rejected")

    origin = request.headers.get("origin")
    if origin:
        allowed = {
            f"http://{settings.adapter_host}:{settings.adapter_port}",
            f"http://127.0.0.1:{settings.adapter_port}",
            f"http://localhost:{settings.adapter_port}",
        }
        if origin not in allowed:
            raise HTTPException(status_code=403, detail="cross-site request rejected")


async def read_verified_body(request: Request, signature: str | None) -> bytes:
    """
    Run every inbound-request guard, then return the raw body.

    Order matters: cheap header checks run before the body is buffered so a
    forged or oversized request is dropped before it costs memory.
    """
    require_json_content_type(request)
    reject_cross_site(request)

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > settings.max_request_bytes:
                raise HTTPException(status_code=413, detail="request body too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid Content-Length")

    raw = await request.body()
    if len(raw) > settings.max_request_bytes:
        raise HTTPException(status_code=413, detail="request body too large")

    if not hmac_ok(raw, signature):
        raise HTTPException(status_code=401, detail="invalid signature")
    return raw
