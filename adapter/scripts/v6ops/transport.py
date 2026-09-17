"""
Loopback-only HTTP for the operator CLI.

The bearer token is only ever sent to plain http on this machine: the base URL
must name a loopback host, proxies from the environment are ignored and a
redirect is never followed. `Client` keeps the token out of its repr.
"""

from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

TOKEN_ENV: Final[str] = "V6_OPERATOR_TOKEN"
BASE_URL_ENV: Final[str] = "V6_ADAPTER_URL"
DEFAULT_BASE_URL: Final[str] = "http://127.0.0.1:8765"
DEFAULT_TIMEOUT_S: Final[float] = 10.0
MAX_RESPONSE_BYTES: Final[int] = 1_000_000
LOOPBACK_NAMES: Final[frozenset[str]] = frozenset({"localhost"})
HTTP_OK_MIN: Final[int] = 200
HTTP_OK_MAX: Final[int] = 299
HTTP_SERVER_ERROR_MIN: Final[int] = 500
JSON_CONTENT_TYPE: Final[str] = "application/json"
ERROR_FIELDS: Final[tuple[str, ...]] = ("detail", "refusal", "code", "halted")


class UsageError(Exception):
    """Bad arguments or configuration; nothing was sent."""


class TransportError(Exception):
    """The adapter could not be reached; `str()` names the failure class only."""


@dataclass(frozen=True)
class HttpReply:
    status: int
    body: bytes

    @property
    def ok(self) -> bool:
        return HTTP_OK_MIN <= self.status <= HTTP_OK_MAX

    @property
    def server_error(self) -> bool:
        return self.status >= HTTP_SERVER_ERROR_MIN

    def json(self) -> Any:
        return decode_json(self.body)


Transport = Callable[[str, str, bytes | None, Mapping[str, str], float], HttpReply]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the bearer token elsewhere; surface it as an error."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def urllib_transport(method: str, url: str, body: bytes | None, headers: Mapping[str, str],
                     timeout: float) -> HttpReply:
    # An empty ProxyHandler ignores HTTP(S)_PROXY: the token stays on this machine.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with opener.open(request, timeout=timeout) as response:
            return HttpReply(response.status, response.read(MAX_RESPONSE_BYTES))
    except urllib.error.HTTPError as exc:
        payload = exc.read(MAX_RESPONSE_BYTES) if exc.fp is not None else b""
        return HttpReply(exc.code, payload)
    except (urllib.error.URLError, OSError) as exc:
        raise TransportError(type(exc).__name__) from exc


def _is_loopback(host: str) -> bool:
    if host.lower() in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def resolve_base_url(raw: str) -> str:
    """Only plain http to this machine: the token must never leave it."""
    parts = urllib.parse.urlsplit(raw.strip())
    host = parts.hostname or ""
    if parts.scheme != "http" or not _is_loopback(host):
        raise UsageError("base URL must be http:// on a loopback host")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise UsageError("base URL must not carry credentials, a query or a fragment")
    if parts.path not in ("", "/"):
        raise UsageError("base URL must not carry a path")
    return f"http://{parts.netloc}"


def request_headers(token: str | None, has_body: bool) -> dict[str, str]:
    headers = {"Accept": JSON_CONTENT_TYPE}
    if has_body:
        headers["Content-Type"] = JSON_CONTENT_TYPE
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def decode_json(body: bytes) -> Any:
    """{} for an empty body, None for anything that is not UTF-8 JSON."""
    try:
        return json.loads(body.decode("utf-8")) if body else {}
    except (UnicodeDecodeError, ValueError):
        return None


def error_document(reply: HttpReply) -> dict[str, object]:
    decoded = reply.json()
    if not isinstance(decoded, Mapping):
        return {"error": reply.status, "detail": "non-JSON response"}
    return {"error": reply.status, **{key: decoded[key] for key in ERROR_FIELDS if key in decoded}}


@dataclass(frozen=True)
class Client:
    """One adapter; `token` is None for commands that need none (and never in a repr)."""

    base_url: str
    transport: Transport
    timeout: float = DEFAULT_TIMEOUT_S
    token: str | None = field(default=None, repr=False)

    def send(self, method: str, path: str, body: bytes | None = None, *, auth: bool = True,
             timeout: float | None = None) -> HttpReply:
        """Raises TransportError when unreachable, UsageError when auth has no token."""
        if auth and self.token is None:
            raise UsageError(f"{TOKEN_ENV} is not set")
        headers = request_headers(self.token if auth else None, body is not None)
        return self.transport(method, self.base_url + path, body, headers,
                              self.timeout if timeout is None else timeout)
