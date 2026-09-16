"""
Host-header allowlist: the DNS-rebinding defence of plan section 9.

A page on an attacker's domain that is re-pointed at 127.0.0.1 still sends its
own name in the Host header, so only names that can reach this machine by
design are accepted. Starlette's TrustedHostMiddleware splits the header on
the first ':', which turns a bracketed IPv6 host such as `[::1]:8765` into `[`
and can never match; this guard parses the header properly. Anything else is
answered with 400 before a route runs.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from typing import Final

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# Test hosts such as Starlette's "testserver" are never built in; tests opt in
# through ALLOWED_HOSTS like any other extra name.
LOOPBACK_HOSTS: Final[tuple[str, ...]] = ("127.0.0.1", "localhost", "[::1]")
GUARDED_SCOPES: Final[frozenset[str]] = frozenset({"http", "websocket"})
INVALID_HOST_STATUS: Final[int] = 400
INVALID_HOST_BODY: Final[str] = "Invalid host header"
WILDCARD_MARK: Final[str] = "*"


def host_name(header: str) -> str:
    """Host part of a Host header value; a bracketed IPv6 literal keeps its brackets."""
    value = header.strip()
    if value.startswith("["):
        end = value.find("]")
        return value[:end + 1] if end > 0 else ""
    return value.split(":", 1)[0]


def normalise_host(name: str) -> str:
    """Comparable form: lower case, IP literals canonical, IPv6 literals bracketed."""
    value = name.strip().lower()
    try:
        address = ipaddress.ip_address(value.removeprefix("[").removesuffix("]"))
    except ValueError:
        return value
    return f"[{address.compressed}]" if address.version == 6 else address.compressed


class HostGuardMiddleware:
    """Pure ASGI middleware: 400 unless the Host header names an allowed host."""

    def __init__(self, app: ASGIApp, allowed_hosts: Iterable[str]) -> None:
        allowed = frozenset(normalise_host(host) for host in allowed_hosts)
        if not allowed or any(not host or WILDCARD_MARK in host for host in allowed):
            raise ValueError("allowed hosts must be explicit, non-empty names")
        self.app = app
        self.allowed = allowed

    def accepts(self, header: str) -> bool:
        name = host_name(header)
        return bool(name) and normalise_host(name) in self.allowed

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in GUARDED_SCOPES:
            await self.app(scope, receive, send)
            return
        if self.accepts(Headers(scope=scope).get("host", "")):
            await self.app(scope, receive, send)
            return
        response = PlainTextResponse(INVALID_HOST_BODY, status_code=INVALID_HOST_STATUS)
        await response(scope, receive, send)
