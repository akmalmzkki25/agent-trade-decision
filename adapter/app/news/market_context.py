"""
Fundamental market context for V3 (DXY / VIX bias modifier).

Inputs (from EA, optional — may be empty dicts):
- dxy_features:
    slope_z       : z-score of DXY slope over 60-bar M5
- vix_features:
    z_score       : z-score of VIX vs 60-bar mean

Rules (for XAUUSD class symbols):
- DXY slope z > +1.0   -> gold_bias = -0.5 (strong USD => gold weak)
- DXY slope z < -1.0   -> gold_bias = +0.5
- VIX z   > +1.5       -> gold_bias += 0.3 (risk-off => gold safe haven)
- Otherwise            -> 0.0 contribution

Returned `gold_bias` is clamped to [-1.0, +1.0].

Integration in V3 ranker (planner side):
    aligned = (+bias) if scenario.side == "buy" else (-bias)
    final_score = base_score + 0.10 * clamp(aligned, -1, 1)
"""

from __future__ import annotations

from ..models import FundamentalContext

_XAU_SYMBOLS = ("XAUUSD", "XAUEUR", "XAU/USD", "GOLD", "GOLDUSD")


def _is_xau(symbol: str) -> bool:
    s = symbol.upper().replace(" ", "")
    return any(s.startswith(x) for x in _XAU_SYMBOLS)


def get_bias_modifier(
    symbol: str,
    dxy_features: dict[str, float] | None = None,
    vix_features: dict[str, float] | None = None,
) -> FundamentalContext:
    dxy = dxy_features or {}
    vix = vix_features or {}

    dxy_slope = float(dxy.get("slope_z", 0.0))
    vix_z = float(vix.get("z_score", 0.0))

    if not _is_xau(symbol):
        return FundamentalContext(
            dxy_slope=dxy_slope,
            vix_zscore=vix_z,
            gold_bias=0.0,
            reason_codes=[],
        )

    bias = 0.0
    codes: list[str] = []

    if dxy_slope > 1.0:
        bias -= 0.5
        codes.append("DXY_BULLISH_USD")
    elif dxy_slope < -1.0:
        bias += 0.5
        codes.append("DXY_BEARISH_USD")

    if vix_z > 1.5:
        bias += 0.3
        codes.append("VIX_RISK_OFF")

    bias = max(-1.0, min(1.0, bias))

    return FundamentalContext(
        dxy_slope=dxy_slope,
        vix_zscore=vix_z,
        gold_bias=bias,
        reason_codes=codes,
    )


def apply_bias_to_score(base_score: float, side: str, bias: float, weight: float = 0.10) -> float:
    """Returns adjusted score (clamped 0..1)."""
    aligned = bias if side == "buy" else (-bias)
    aligned = max(-1.0, min(1.0, aligned))
    return max(0.0, min(1.0, base_score + weight * aligned))
