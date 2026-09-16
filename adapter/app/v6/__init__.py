"""
V6: multi-agent XAUUSD decision system.

Four agents (price action, news risk, liquidity, structure) plus a chief
deliberate on every M15 bar close. Language models only choose among options
that deterministic code has already computed; direction, price levels, lot size
and every risk limit are owned by `app.v6.risk`.

See `knowledge/15-implikasi-untuk-v6.md` for the evidence behind each limit.
"""
