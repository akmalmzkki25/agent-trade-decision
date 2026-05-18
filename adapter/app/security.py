from __future__ import annotations

import hashlib
import hmac

from .settings import settings


def hmac_ok(raw_body: bytes, signature: str | None) -> bool:
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
