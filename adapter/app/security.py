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

Operator and control routes (V6) add two more: the caller must be on this
machine, and it must present the operator bearer token (`read_operator_body`,
`require_operator`).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
from typing import Final

from fastapi import HTTPException, Request
from pydantic import SecretStr

from .settings import settings

_JSON_CONTENT_TYPES = ("application/json",)
_SAFE_FETCH_SITES = ("same-origin", "same-site", "none")
_LOOPBACK_NAMES: Final[frozenset[str]] = frozenset({"localhost"})
_BEARER_SCHEME: Final[str] = "bearer"
# Operator commands are a few dozen bytes; anything larger is not one of them.
OPERATOR_MAX_BODY_BYTES: Final[int] = 4096


def hmac_ok(raw_body: bytes, signature: str | None) -> bool:
    """Verify the HMAC-SHA256 signature of a request body (timing-safe)."""
    if not settings.hmac_required:
        return True
    if not signature:
        return False
    expected = hmac.new(
        settings.internal_hmac_key.get_secret_value().encode("utf-8"),
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


def _too_large() -> HTTPException:
    return HTTPException(status_code=413, detail="request body too large")


def reject_declared_oversize(request: Request, limit: int) -> None:
    """Refuse a body whose declared Content-Length already exceeds `limit`."""
    content_length = request.headers.get("content-length")
    if content_length is None:
        return
    try:
        declared = int(content_length)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid Content-Length") from None
    if declared > limit:
        raise _too_large()


async def read_capped_body(request: Request, limit: int) -> bytes:
    """
    Buffer the body, refusing it the moment it grows past `limit`.

    A chunked request carries no Content-Length, so the header check alone
    would let a local process stream an unbounded body into the single adapter
    worker before any size check ran. Counting while reading bounds the buffer
    to one server-sized chunk past the limit.
    """
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > limit:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


def basic_request_guards(request: Request, limit: int) -> None:
    """The header-only checks every JSON write endpoint runs before reading a byte."""
    require_json_content_type(request)
    reject_cross_site(request)
    reject_declared_oversize(request, limit)


async def read_verified_body(request: Request, signature: str | None) -> bytes:
    """
    Run every inbound-request guard, then return the raw body.

    Order matters: cheap header checks run before any body byte is read, so a
    forged or declared-oversized request is dropped before it costs memory.
    """
    basic_request_guards(request, settings.max_request_bytes)

    raw = await read_capped_body(request, settings.max_request_bytes)

    if not hmac_ok(raw, signature):
        raise HTTPException(status_code=401, detail="invalid signature")
    return raw


# --- operator / control routes ----------------------------------------------
def _is_loopback_host(host: str) -> bool:
    if host.lower() in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def require_loopback_client(request: Request) -> None:
    """Refuse any caller whose socket peer is not this machine."""
    client = request.client
    if client is None or not _is_loopback_host(client.host):
        raise HTTPException(status_code=403, detail="loopback clients only")


def _bearer_token(request: Request) -> str | None:
    scheme, _, credentials = (request.headers.get("authorization") or "").partition(" ")
    if scheme.lower() != _BEARER_SCHEME:
        return None
    return credentials.strip() or None


def _require_bearer(request: Request, expected_token: SecretStr) -> None:
    expected = expected_token.get_secret_value()
    presented = _bearer_token(request)
    # compare_digest runs even for a missing token so timing says nothing either way.
    matches = hmac.compare_digest(
        (presented or "").encode("utf-8"), expected.encode("utf-8"))
    if not (expected and presented and matches):
        raise HTTPException(status_code=401, detail="invalid operator token",
                            headers={"WWW-Authenticate": "Bearer"})


def require_operator(request: Request, expected_token: SecretStr) -> None:
    """Guards for a body-less operator request: loopback, same-site, bearer token."""
    require_loopback_client(request)
    reject_cross_site(request)
    _require_bearer(request, expected_token)


async def read_operator_body(
    request: Request, expected_token: SecretStr, limit: int = OPERATOR_MAX_BODY_BYTES
) -> bytes:
    """
    Guard an operator write (loopback, JSON, same-site, size, bearer token) and
    return its raw body. The token check runs after the header checks and
    before any body byte is read.
    """
    require_loopback_client(request)
    basic_request_guards(request, limit)
    _require_bearer(request, expected_token)
    return await read_capped_body(request, limit)
