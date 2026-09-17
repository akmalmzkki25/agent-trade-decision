"""
The V6 basket result (wire contract section 6.5).

Same body as the V1-V5 `BasketResultEvent`, checked more strictly for the signed
/v6/basket-result route: version "v6" only, a real side, no unknown fields and
no NaN or infinity. The basket id is kept even when it names no intent (the
result then counts in P&L unlinked), so it only has to be short, safe text.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import ConfigDict, StringConstraints

from ...models import BasketResultEvent

BASKET_ID_PATTERN: Final[str] = r"^[A-Za-z0-9._#+:-]{1,64}$"
TEXT_PATTERN: Final[str] = r"^[A-Za-z0-9._#+:\- TZ]{0,64}$"

ShortText = Annotated[str, StringConstraints(pattern=TEXT_PATTERN)]


class V6BasketResultEvent(BasketResultEvent):
    """A closed V6 position, as the V6 EA posts it."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, allow_inf_nan=False)

    basket_id: Annotated[str, StringConstraints(pattern=BASKET_ID_PATTERN)]
    version: Literal["v6"]
    symbol: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._#+-]{3,20}$")]
    side: Literal["buy", "sell"]
    opened_at_utc: ShortText
    closed_at_utc: ShortText
    close_reason: Annotated[str, StringConstraints(pattern=r"^[A-Z_]{1,24}$")]
