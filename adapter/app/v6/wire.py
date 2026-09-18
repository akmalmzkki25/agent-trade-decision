"""
The V6 EA <-> adapter signing contract (docs/v6-wire-contract.md).

Requests (EA -> adapter) carry X-Qlip6-Ts (unix seconds) and X-Qlip6-Sig, the
lowercase hex HMAC-SHA256(key, ts "\\n" METHOD "\\n" PATH "\\n" raw body).
`verify_request` checks both formats, the +/-30 s window and the signature (in
constant time), then refuses a replay of the same (ts, sig) inside the window.

Responses (adapter -> EA): `PollResponse.sig` is the hex HMAC-SHA256 of
`intent_canonical`, which joins the fields with "|" and turns prices into
integer points and lots into integer hundredths, so no float text is signed.
Intent v2 signs the SL+ ladder and the management action as well, so a command
that closes or modifies a trade is as tamper-evident as an entry.

The key is V6_EA_HMAC_KEY (`config.ea_key_ok`), used as its ASCII bytes. Nothing
here logs, prints or stores a key or a computed signature.
"""

from __future__ import annotations

import hashlib
import heapq
import hmac
import math
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from threading import Lock
from typing import Final

from pydantic import SecretStr

from .config import ea_key_ok
from .schemas.intent import PollResponse

TS_HEADER: Final[str] = "X-Qlip6-Ts"
SIG_HEADER: Final[str] = "X-Qlip6-Sig"
REQUEST_WINDOW_S: Final[int] = 30
REPLAY_CACHE_MAX: Final[int] = 4096
CANONICAL_SEPARATOR: Final[str] = "|"
LOTS_SCALE: Final[int] = 100
GRID_TOLERANCE: Final[Decimal] = Decimal("0.000001")
KEY_ENCODING: Final[str] = "ascii"
FINGERPRINT_LABEL: Final[bytes] = b"qlip-v6-key-fingerprint"
FINGERPRINT_CHARS: Final[int] = 12
TS_PATTERN: Final[re.Pattern[str]] = re.compile(r"^(0|[1-9][0-9]{0,11})$")
SIG_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
METHOD_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{3,7}$")
PATH_PATTERN: Final[re.Pattern[str]] = re.compile(r"^/[A-Za-z0-9/_.-]{0,200}$")
# The canonical intent string, in order ("*_points" and "lots_hundredths" are integers).
CANONICAL_FIELDS: Final[tuple[str, ...]] = (
    "schema_version", "server_time_epoch", "command", "has_intent", "intent_id", "source",
    "require_demo", "side", "order_type", "entry_points", "sl_points", "tp_points",
    "lots_hundredths", "ref_points", "max_drift_points", "max_spread_points",
    "valid_until_epoch", "pending_expiry_epoch", "time_barrier_s", "magic",
    "tp1_points", "tp2_points", "sl_after_tp1_points", "sl_after_tp2_points",
    "action_id", "action_ticket", "action_sl_points", "action_tp_points",
    "action_tp1_points", "action_tp2_points", "action_sl1_points", "action_sl2_points",
    "action_price_points", "action_expiry_epoch", "action_barrier_s", "action_issued_epoch",
)

# RequestCheck.code values; every one but CHECK_OK means HTTP 401.
CHECK_OK: Final[str] = "SIG_OK"
CHECK_NO_KEY: Final[str] = "SIG_NO_KEY"
CHECK_MISSING: Final[str] = "SIG_MISSING"
CHECK_BAD_TS: Final[str] = "SIG_BAD_TS"
CHECK_BAD_SIG: Final[str] = "SIG_BAD_FORMAT"
CHECK_STALE: Final[str] = "SIG_STALE"
CHECK_MISMATCH: Final[str] = "SIG_MISMATCH"
CHECK_REPLAY: Final[str] = "SIG_REPLAY"
CHECK_REPLAY_FULL: Final[str] = "SIG_REPLAY_FULL"
CHECK_CODES: Final[frozenset[str]] = frozenset({
    CHECK_OK, CHECK_NO_KEY, CHECK_MISSING, CHECK_BAD_TS, CHECK_BAD_SIG, CHECK_STALE,
    CHECK_MISMATCH, CHECK_REPLAY, CHECK_REPLAY_FULL,
})

KeyMaterial = SecretStr | str | bytes


# --- primitives ------------------------------------------------------------------
def _key_bytes(key: KeyMaterial) -> bytes:
    raw = key.get_secret_value() if isinstance(key, SecretStr) else key
    if isinstance(raw, str):
        try:
            raw = raw.encode(KEY_ENCODING)
        except UnicodeEncodeError:
            raise ValueError("the HMAC key must be ASCII") from None
    if not isinstance(raw, bytes) or not raw:
        raise ValueError("an HMAC key is required")
    return raw


def hmac_hex(key: KeyMaterial, data: bytes) -> str:
    """Lowercase hex HMAC-SHA256 of `data` (keys longer than 64 bytes are hashed first)."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data must be bytes")
    return hmac.new(_key_bytes(key), bytes(data), hashlib.sha256).hexdigest()


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def request_signing_payload(ts: int, method: str, path: str, body: bytes) -> bytes:
    """`ts` "\\n" `METHOD` "\\n" `PATH` "\\n" followed by the raw body bytes."""
    if not _is_int(ts) or ts < 0:
        raise ValueError("ts must be a non-negative int")
    if not isinstance(method, str) or not METHOD_PATTERN.match(method):
        raise ValueError("method must be an upper-case HTTP method")
    if not isinstance(path, str) or not PATH_PATTERN.match(path):
        raise ValueError("path must be an absolute path without a query")
    if not isinstance(body, (bytes, bytearray)):
        raise TypeError("body must be bytes")
    return f"{ts}\n{method}\n{path}\n".encode("ascii") + bytes(body)


def sign_request(key: KeyMaterial, ts: int, method: str, path: str, body: bytes) -> str:
    """The X-Qlip6-Sig value the EA sends (tests and tools emulate the EA with it)."""
    return hmac_hex(key, request_signing_payload(ts, method, path, body))


def key_fingerprint(key: SecretStr) -> str:
    """12 hex characters both sides may log to show they hold the same key; "" without one."""
    if not ea_key_ok(key):
        return ""
    return hmac_hex(key, FINGERPRINT_LABEL)[:FINGERPRINT_CHARS]


# --- request verification ---------------------------------------------------------
@dataclass(frozen=True)
class RequestCheck:
    """`ok` exactly when `code` is CHECK_OK; `detail` never holds a key or signature."""

    ok: bool
    code: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.code not in CHECK_CODES or self.ok != (self.code == CHECK_OK):
            raise ValueError("inconsistent request check")


class ReplayCache:
    """(ts, sig) pairs accepted within the window. Bounded and thread-safe.

    A pair is forgotten once its timestamp has left the window (the window check
    refuses it from then on). When the cache is full, new pairs are refused
    (fail closed): only correctly signed requests are ever added.
    """

    def __init__(self, window_s: int = REQUEST_WINDOW_S,
                 max_entries: int = REPLAY_CACHE_MAX) -> None:
        if not (_is_int(window_s) and window_s > 0 and _is_int(max_entries) and max_entries > 0):
            raise ValueError("window_s and max_entries must be positive ints")
        self._window_s = window_s
        self._max_entries = max_entries
        self._expiry: list[tuple[int, int, str]] = []
        self._seen: set[tuple[int, str]] = set()
        self._lock = Lock()

    @property
    def size(self) -> int:
        """Pairs currently remembered (a property, so an empty cache is never falsy)."""
        with self._lock:
            return len(self._seen)

    def _purge(self, now: float) -> None:
        while self._expiry and self._expiry[0][0] < now:
            _, ts, sig = heapq.heappop(self._expiry)
            self._seen.discard((ts, sig))

    def admit(self, ts: int, sig: str, now: float) -> str:
        """CHECK_OK (and remember the pair), CHECK_REPLAY or CHECK_REPLAY_FULL."""
        with self._lock:
            self._purge(now)
            if (ts, sig) in self._seen:
                return CHECK_REPLAY
            if len(self._seen) >= self._max_entries:
                return CHECK_REPLAY_FULL
            self._seen.add((ts, sig))
            heapq.heappush(self._expiry, (ts + self._window_s, ts, sig))
            return CHECK_OK


def _refused(code: str, detail: str) -> RequestCheck:
    return RequestCheck(ok=False, code=code, detail=detail)


def _header_problem(ts_header: str | None, sig_header: str | None) -> RequestCheck | None:
    if not ts_header or not sig_header:
        return _refused(CHECK_MISSING, f"{TS_HEADER} and {SIG_HEADER} are required")
    if not TS_PATTERN.match(ts_header):
        return _refused(CHECK_BAD_TS, f"{TS_HEADER} must be unix seconds")
    if not SIG_PATTERN.match(sig_header):
        return _refused(CHECK_BAD_SIG, f"{SIG_HEADER} must be 64 lowercase hex characters")
    return None


def verify_request(*, key: SecretStr, ts_header: str | None, sig_header: str | None,
                   method: str, path: str, body: bytes, now: float, cache: ReplayCache,
                   window_s: int = REQUEST_WINDOW_S) -> RequestCheck:
    """Check one signed EA request; the route answers 401 unless `.ok`.

    `path` is the request path without a query; `body` the raw bytes as received.
    """
    if not ea_key_ok(key):
        return _refused(CHECK_NO_KEY, "V6_EA_HMAC_KEY is not configured")
    problem = _header_problem(ts_header, sig_header)
    if problem is not None:
        return problem
    ts = int(ts_header)  # type: ignore[arg-type]  # checked by _header_problem
    if not math.isfinite(now) or abs(now - ts) > window_s:
        return _refused(CHECK_STALE, f"{TS_HEADER} is outside the {window_s} s window")
    expected = sign_request(key, ts, method, path, body)
    if not hmac.compare_digest(expected, sig_header):  # type: ignore[arg-type]
        return _refused(CHECK_MISMATCH, "signature does not match")
    admitted = cache.admit(ts, sig_header, now)  # type: ignore[arg-type]
    if admitted != CHECK_OK:
        return _refused(admitted, "request already seen" if admitted == CHECK_REPLAY
                        else "replay cache is full")
    return RequestCheck(ok=True, code=CHECK_OK)


# --- intent signature ---------------------------------------------------------------
def _decimal(name: str, value: object, *, positive: bool) -> Decimal:
    if not (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value)):
        raise ValueError(f"{name} must be a finite number")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'non-negative'}")
    return Decimal(repr(float(value)))


def _on_grid(quotient: Decimal, what: str) -> int:
    whole = quotient.to_integral_value(rounding=ROUND_HALF_EVEN)
    if abs(quotient - whole) > GRID_TOLERANCE:
        raise ValueError(f"{what} is not on its grid")
    return int(whole)


def price_to_points(price: float, point: float) -> int:
    """round(price / point) for a price already on the point grid; 0 stays 0."""
    quotient = _decimal("price", price, positive=False) / _decimal("point", point, positive=True)
    return _on_grid(quotient, "price")


def lots_to_hundredths(lots: float) -> int:
    """round(lots x 100) for lots on the 0.01 grid; 0 stays 0."""
    return _on_grid(_decimal("lots", lots, positive=False) * LOTS_SCALE, "lots")


def intent_canonical(response: PollResponse, point: float) -> str:
    """The signed text of a poll response, fields in CANONICAL_FIELDS order."""
    r = response

    def points(price: float) -> int:
        return price_to_points(price, point)

    values = (
        r.schema_version, r.server_time_epoch, r.command, 1 if r.has_intent else 0,
        r.intent_id, r.source, r.require_demo, r.side, r.order_type,
        points(r.entry), points(r.sl), points(r.tp), lots_to_hundredths(r.lots),
        points(r.ref_price), r.max_drift_points, r.max_spread_points,
        r.valid_until_epoch, r.pending_expiry_epoch, r.time_barrier_s, r.magic,
        points(r.tp1), points(r.tp2), points(r.sl_after_tp1), points(r.sl_after_tp2),
        r.action_id, r.action_ticket, points(r.action_sl), points(r.action_tp),
        points(r.action_tp1), points(r.action_tp2), points(r.action_sl1),
        points(r.action_sl2), points(r.action_price), r.action_expiry_epoch,
        r.action_barrier_s, r.action_issued_epoch,
    )
    return CANONICAL_SEPARATOR.join(str(value) for value in values)


def intent_signature(key: KeyMaterial, response: PollResponse, point: float) -> str:
    return hmac_hex(key, intent_canonical(response, point).encode("ascii"))


def sign_intent(key: SecretStr, response: PollResponse, point: float) -> PollResponse:
    """A copy of `response` carrying its signature; unsigned ("") when no key is configured."""
    signature = intent_signature(key, response, point) if ea_key_ok(key) else ""
    return response.model_copy(update={"sig": signature})


def verify_intent(key: SecretStr, response: PollResponse, point: float) -> bool:
    """What the EA does: recompute and compare in constant time (tests use it too)."""
    if not ea_key_ok(key) or not response.sig:
        return False
    try:
        expected = intent_signature(key, response, point)
    except ValueError:  # off-grid price or lots: nothing a valid signature can cover
        return False
    return hmac.compare_digest(expected, response.sig)
