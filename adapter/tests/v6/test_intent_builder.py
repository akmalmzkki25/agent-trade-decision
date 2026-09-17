"""risk.intent_builder: the last adapter-side authority before an intent reaches the EA."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Any, Final

import pytest
from pydantic import SecretStr

from app.v6 import wire
from app.v6.config import V6Settings
from app.v6.cycle_types import MarketContext, ProtocolDecision
from app.v6.ledger_cycles_schema import SessionRecord
from app.v6.market.sessions import session_state
from app.v6.risk import intent_builder as ib
from app.v6.risk.intent_builder import IntentDraft, ReferenceQuote, build_intent, to_poll_response
from app.v6.risk.policy import (
    POLICY_AGENT_NOT_ALLOWED, POLICY_EXECUTE_LOT_CAP, POLICY_EXECUTE_NEEDS_KEY,
    POLICY_EXECUTE_NEEDS_OPERATOR, POLICY_LOGIN_NOT_ALLOWED, POLICY_OPERATOR_DEMO_ONLY,
    POLICY_SERVER_NOT_DEMO,
)
from app.v6.schemas.intent import INTENT_ID_PATTERN
from app.v6.types import Candidate, ExitPlan, Refusal, SizingResult

from .cycle_fixtures_v6 import CYCLE_ID, calendar
from .payloads_v6 import BAR_OPEN, M15, as_poll, as_snapshot, poll_payload, snapshot_payload

TOKEN: Final[str] = "operator-token-" + "t" * 40
EA_KEY: Final[str] = "ea-hmac-key-" + "e" * 40
CLOSE: Final[int] = BAR_OPEN + M15
NOW: Final[float] = CLOSE + 10.5
CID: Final[str] = f"displacement-buy-{BAR_OPEN}"
SESSION_ID: Final[str] = "a1b2c3d4e5f6"
FIXED_ID: Final[str] = "k7w2m4pq3xza"
POINT: Final[float] = 0.01
SIGN: Final[dict[str, int]] = {"buy": 1, "sell": -1}


def settings(**changes: Any) -> V6Settings:
    base = dict(enabled=True, mode="execute", backend="operator", operator_token=TOKEN,
                ea_hmac_key=EA_KEY)
    return V6Settings(_env_file=None, **{**base, **changes})


def context(*, bid: float = 4300.0, ask: float = 4300.2, trade_mode: str = "DEMO",
            server: str = "Broker-Demo", **spec: Any) -> MarketContext:
    payload = snapshot_payload(trade_mode=trade_mode)
    payload["account"]["server"] = server
    payload["quote"] = {"bid": bid, "ask": ask, "spread_points": round((ask - bid) / POINT),
                        "time_msc": 1}
    payload["symbol_spec"].update(spec)
    return MarketContext.from_snapshot(
        as_snapshot(payload), cycle_id=CYCLE_ID, received_at=CLOSE + 1.0, bars={},
        session=session_state(CLOSE), calendar=calendar(), features={})


def spec_context(**changes: Any) -> MarketContext:
    own = context()  # a spec that skipped the snapshot validators
    return replace(own, spec=replace(own.spec, **changes))


def poll_quote(observed_at: float = NOW - 1.0, **changes: Any) -> ReferenceQuote:
    return ReferenceQuote.from_poll(as_poll({**poll_payload(), **changes}), observed_at)


def candidate(side: str = "buy", entry: float = 4298.0) -> Candidate:
    return Candidate(candidate_id=CID, setup="displacement", side=side,  # type: ignore[arg-type]
                     entry=entry, invalidation=round(entry - SIGN[side] * 7.0, 2), bar_t=BAR_OPEN)


def plan(side: str = "buy", entry: float = 4298.0, stop: float = 7.0, reward_r: float = 2.0,
         **changes: Any) -> ExitPlan:
    sign = SIGN[side]
    fields = dict(side=side, entry=entry, sl=round(entry - sign * stop, 2),
                  tp=round(entry + sign * stop * reward_r, 2), stop_distance=stop,
                  reward_r=reward_r, time_barrier_s=7200)
    return ExitPlan(**{**fields, **changes})


def sizing(stop: float = 7.0, lots: float = 0.01, **changes: Any) -> SizingResult:
    loss = round((stop + 0.4) * 100, 2)
    fields = dict(lots=lots, risk_usd=round(loss * lots, 2), risk_budget_usd=10.0,
                  loss_per_lot=loss, notional_usd=4300.0, margin_usd=8.6)
    return SizingResult(**{**fields, **changes})


@dataclass(frozen=True)
class Resolved:
    """A duck-typed resolution: EITHER is the liquidity desk's market-or-limit preference."""

    action: str = "ENTER"
    candidate_id: str | None = CID
    order_style: str = "EITHER"


def session(**changes: Any) -> SessionRecord:
    fields = dict(session_id=SESSION_ID, trading_day="2026-09-16", backend="operator",
                  mode="execute", started_at=CLOSE - 600.0, stopped_at=None, stop_reason=None,
                  armed=True, armed_at=CLOSE - 600.0)
    return SessionRecord(**{**fields, **changes})


def build(*, side: str = "buy", entry: float = 4298.0, stop: float = 7.0,
          style: str = "LIMIT", reward_r: float = 2.0, **overrides: Any) -> IntentDraft | Refusal:
    resolution = ProtocolDecision(action="ENTER", hold_reason=None, candidate_id=CID,
                                  size_multiplier=1.0, order_style=style)  # type: ignore[arg-type]
    args: dict[str, Any] = dict(
        resolution=resolution, candidate=candidate(side, entry),
        exit_plan=plan(side, entry, stop, reward_r), sizing=sizing(stop), context=context(),
        settings=settings(), session=session(), now=NOW, agent="claude_code",
        new_id=lambda: FIXED_ID)
    return build_intent(**{**args, **overrides})


def drafted(result: IntentDraft | Refusal) -> IntentDraft:
    assert isinstance(result, IntentDraft), result
    return result


def refused(result: IntentDraft | Refusal) -> tuple[str, ...]:
    assert isinstance(result, Refusal), result
    return result.codes


# --- order types ------------------------------------------------------------------------
def test_a_passive_buy_becomes_a_buy_limit_with_every_ea_limit() -> None:
    draft = drafted(build())
    row = draft.row

    assert (row.intent_id, row.cycle_id, row.session_id, draft.candidate_id) == (
        FIXED_ID, CYCLE_ID, SESSION_ID, CID)
    assert (row.source, row.agent, draft.require_demo, draft.magic) == (
        "operator", "claude_code", 1, 250570)
    assert (row.side, row.order_type, row.entry, row.sl, row.tp, row.lots, row.risk_usd) == (
        "buy", "BUY_LIMIT", 4298.0, 4291.0, 4312.0, 0.01, 7.4)
    assert (draft.ref_price, draft.max_drift_points, draft.max_spread_points) == (4300.2, 140, 50)
    assert (row.valid_until_epoch, row.pending_expiry_epoch) == (
        math.floor(NOW) + 120, CLOSE + 1800)
    assert (row.time_barrier_s, row.created_at) == (7200, NOW)


def test_a_passive_sell_becomes_a_sell_limit_referenced_to_the_bid() -> None:
    draft = drafted(build(side="sell", entry=4302.0))

    assert (draft.row.order_type, draft.row.sl, draft.row.tp, draft.ref_price) == (
        "SELL_LIMIT", 4309.0, 4288.0, 4300.0)


def test_an_entry_at_the_ask_becomes_a_market_buy_at_the_ask() -> None:
    draft = drafted(build(entry=4300.2, style="MARKET"))
    row = draft.row

    assert (row.order_type, row.entry, draft.ref_price, row.pending_expiry_epoch) == (
        "BUY", 4300.2, 4300.2, 0)
    assert (row.sl, row.tp, draft.max_drift_points) == (4293.2, 4314.2, 140)
    # The worst fill the EA accepts: 7.00 stop + 1.40 drift + 0.40 friction per 0.01 lot.
    assert (row.risk_usd, row.valid_until_epoch) == (8.8, math.floor(NOW) + 120)


def test_either_style_allows_a_market_sell_at_the_bid() -> None:
    row = drafted(build(side="sell", entry=4300.0, resolution=Resolved())).row

    assert (row.order_type, row.entry, row.sl, row.tp) == ("SELL", 4300.0, 4307.0, 4286.0)


def test_a_market_order_cuts_its_drift_to_the_budget_or_is_refused() -> None:
    # 10.00 budget - 0.40 friction - 9.00 stop leaves 0.60: 60 points instead of 180.
    draft = drafted(build(entry=4300.2, stop=9.0, style="MARKET"))

    assert (draft.max_drift_points, draft.row.risk_usd) == (60, 10.0)
    assert refused(build(entry=4300.2, stop=9.55, style="MARKET")) == (ib.RISK_OVER_BUDGET,)


@pytest.mark.parametrize(("entry", "style"), [(4300.2, "LIMIT"), (4302.0, "MARKET")])
def test_an_entry_that_is_not_passive_needs_market_style_and_little_drift(
        entry: float, style: str) -> None:
    assert refused(build(entry=entry, style=style)) == (ib.LIMIT_NOT_PASSIVE,)


def test_a_market_order_keeps_the_floor_and_one_r_from_the_reference_price() -> None:
    below_floor = build(entry=4300.2, stop=6.1, style="MARKET",
                        context=context(bid=4299.7, ask=4299.9))
    short_target = build(entry=4299.9, reward_r=1.0, style="MARKET",
                         context=context(stops_level=50))

    assert refused(below_floor) == (ib.MARKET_STOP_BELOW_FLOOR,)
    assert refused(short_target) == (ib.MARKET_REWARD_BELOW_1R,)


def test_limits_honour_the_stops_level_and_a_coarser_tick_grid() -> None:
    levels, coarse = context(stops_level=50), context(tick_size=0.05)

    assert drafted(build(entry=4299.6, context=levels)).row.order_type == "BUY_LIMIT"
    assert refused(build(entry=4299.7, context=levels)) == (ib.LIMIT_NOT_PASSIVE,)
    assert drafted(build(context=coarse)).row.entry == 4298.0
    assert refused(build(entry=4298.01, context=coarse)) == (ib.OFF_GRID,)


# --- authority ----------------------------------------------------------------------------
@pytest.mark.parametrize(("cfg", "code"), [
    (lambda: settings(mode="shadow"), ib.NOT_EXECUTE_MODE),
    (lambda: settings().model_copy(update={"backend": "rules"}), POLICY_EXECUTE_NEEDS_OPERATOR),
    (lambda: settings().model_copy(update={"ea_hmac_key": SecretStr("")}),
     POLICY_EXECUTE_NEEDS_KEY),
    (lambda: settings().model_copy(update={"max_lots": 0.02}), POLICY_EXECUTE_LOT_CAP),
    (lambda: settings(allowed_logins_csv="777"), POLICY_LOGIN_NOT_ALLOWED),
])
def test_only_valid_execute_settings_build_intents(cfg: Any, code: str) -> None:
    assert refused(build(settings=cfg())) == (code,)


@pytest.mark.parametrize(("ctx", "code"), [
    (lambda: context(trade_mode="REAL"), POLICY_OPERATOR_DEMO_ONLY),
    (lambda: context(trade_mode="CONTEST"), POLICY_OPERATOR_DEMO_ONLY),
    (lambda: context(server="Broker-Live"), POLICY_SERVER_NOT_DEMO),
])
def test_the_operator_decides_for_demo_accounts_only(ctx: Any, code: str) -> None:
    assert refused(build(context=ctx())) == (code,)


@pytest.mark.parametrize("agent", ["codex", "gpt", ""])
def test_only_configured_agents_may_decide(agent: str) -> None:
    result = build(agent=agent, settings=settings(operator_agents_csv="claude_code"))

    assert refused(result) == (POLICY_AGENT_NOT_ALLOWED,)
    assert isinstance(build(agent=agent), IntentDraft) is (agent == "codex")


@pytest.mark.parametrize(("record", "code"), [
    (None, ib.SESSION_INACTIVE),
    (session(stopped_at=CLOSE - 1.0, stop_reason="operator"), ib.SESSION_INACTIVE),
    (session(armed=False), ib.SESSION_NOT_ARMED),
    (session(mode="shadow"), ib.SESSION_NOT_ARMED),
])
def test_only_an_active_armed_execute_session_publishes(record: SessionRecord | None,
                                                        code: str) -> None:
    assert refused(build(session=record)) == (code,)


# --- decision and quote ---------------------------------------------------------------------
def test_the_fresher_of_poll_and_snapshot_quote_is_the_reference() -> None:
    fresh = build(quote=poll_quote(bid=4300.3, ask=4300.5))
    old = build(quote=poll_quote(observed_at=CLOSE, bid=4300.3, ask=4300.5))

    assert (drafted(fresh).ref_price, drafted(old).ref_price) == (4300.5, 4300.2)


@pytest.mark.parametrize(("changes", "code"), [
    ({"login": "99999"}, ib.QUOTE_ACCOUNT),
    ({"trade_mode": "REAL"}, ib.QUOTE_ACCOUNT),
    ({"server": "Other-Demo"}, ib.QUOTE_ACCOUNT),
    ({"bid": 4300.4, "ask": 4300.2}, ib.QUOTE_UNUSABLE),
    ({"ask": 4300.205}, ib.QUOTE_UNUSABLE),
    ({"observed_at": NOW + 6.0}, ib.QUOTE_STALE),
])
def test_a_poll_quote_must_be_this_accounts_usable_and_current(changes: dict[str, Any],
                                                               code: str) -> None:
    assert refused(build(quote=poll_quote(**changes))) == (code,)


def test_reference_quotes_come_from_snapshots_and_polls() -> None:
    own = ReferenceQuote.from_context(context())
    polled = poll_quote(observed_at=5.0)

    assert (own.bid, own.ask, own.observed_at, own.login, own.trade_mode, own.server) == (
        4300.0, 4300.2, CLOSE + 1.0, "12345", "DEMO", "Broker-Demo")
    assert (own.side_price("buy"), own.side_price("sell")) == (4300.2, 4300.0)
    assert (polled.server, polled.observed_at) == ("Broker-Demo", 5.0)


# --- inputs, geometry, lots and risk ------------------------------------------------------
@pytest.mark.parametrize("overrides", [
    {"exit_plan": plan(entry=math.nan)},
    {"candidate": candidate(entry=math.nan)},
    {"exit_plan": replace(plan(), side="flat")},
    {"exit_plan": plan(time_barrier_s=0)},
    {"sizing": sizing(lots=0.0)},
    {"sizing": sizing(risk_budget_usd=math.inf)},
    {"now": math.nan},
    {"context": spec_context(stops_level=-1)},
    {"context": spec_context(tick_value_loss=0.0)},
])
def test_unusable_numbers_are_refused(overrides: dict[str, Any]) -> None:
    assert refused(build(**overrides)) == (ib.BAD_INPUT,)


@pytest.mark.parametrize(("overrides", "code"), [
    ({"resolution": Resolved(action="HOLD", candidate_id=None)}, ib.NOT_ENTER),
    ({"resolution": Resolved(candidate_id="orb-buy-1")}, ib.CANDIDATE_MISMATCH),
    ({"candidate": candidate("sell")}, ib.CANDIDATE_MISMATCH),
    ({"candidate": candidate(entry=4297.0)}, ib.CANDIDATE_MISMATCH),
    ({"now": CLOSE + 32.0}, ib.QUOTE_STALE),
    ({"exit_plan": plan(sl=4299.0)}, ib.BAD_GEOMETRY),
    ({"exit_plan": plan(tp=4290.0)}, ib.BAD_GEOMETRY),
    ({"candidate": candidate(entry=4298.005), "exit_plan": plan(entry=4298.005, sl=4291.005)},
     ib.OFF_GRID),
    ({"sizing": sizing(lots=0.02)}, ib.LOT_LIMIT),
    ({"settings": settings(max_lots=0.005)}, ib.LOT_LIMIT),
    ({"context": context(volume_min=0.02)}, ib.LOT_LIMIT),
    ({"sizing": sizing(lots=0.005), "context": context(volume_min=0.001, volume_step=0.005)},
     ib.LOT_LIMIT),
    ({"sizing": sizing(risk_usd=10.01)}, ib.RISK_OVER_BUDGET),
    ({"exit_plan": plan(stop=12.0)}, ib.RISK_OVER_BUDGET),
    ({"stop": 9.1, "quote": poll_quote(bid=4300.0, ask=4301.0)}, ib.RISK_OVER_BUDGET),
])
def test_an_order_must_match_its_decision_and_fit_every_limit(overrides: dict[str, Any],
                                                              code: str) -> None:
    assert refused(build(**overrides)) == (code,)


def test_a_live_spread_only_counts_when_it_exceeds_the_friction() -> None:
    assert drafted(build(stop=9.1)).row.risk_usd == 9.5


# --- timing ---------------------------------------------------------------------------------
def test_late_decisions_and_limit_validity_before_the_pending_expiry() -> None:
    cfg = settings().model_copy(update={"pending_expiry_bars": 1, "operator_deadline_s": 840})
    late = build(now=CLOSE + 301.0, quote=poll_quote(observed_at=CLOSE + 300.0))
    clipped = drafted(build(settings=cfg, now=CLOSE + 800.0,
                            quote=poll_quote(observed_at=CLOSE + 799.0))).row
    short = build(settings=cfg, now=CLOSE + 835.0, quote=poll_quote(observed_at=CLOSE + 834.0))

    assert (clipped.valid_until_epoch, clipped.pending_expiry_epoch) == (CLOSE + 840, CLOSE + 900)
    assert refused(short) == refused(late) == (ib.TOO_LATE,)


@pytest.mark.parametrize(("cfg", "barrier", "expected"), [
    ({"time_barrier_bars": 4}, 7200, 3600), ({}, 1800, 1800), ({}, 14_400, 7200),
])
def test_the_time_barrier_only_tightens(cfg: dict[str, Any], barrier: int, expected: int) -> None:
    result = build(settings=settings(**cfg), exit_plan=plan(time_barrier_s=barrier))

    assert drafted(result).row.time_barrier_s == expected


@pytest.mark.parametrize(("cfg", "expected"), [
    ({"max_spread_points": 25}, 25), ({"account_type": "raw"}, 20),
])
def test_the_spread_limit_is_the_effective_gate(cfg: dict[str, Any], expected: int) -> None:
    assert drafted(build(settings=settings(**cfg))).max_spread_points == expected


# --- the draft and its poll response ----------------------------------------------------------
@pytest.mark.parametrize("change", [
    lambda d: replace(d, row=replace(d.row, source="rules", agent="")),
    lambda d: replace(d, row=replace(d.row, lots=0.02)),
    lambda d: replace(d, row=replace(d.row, agent="gpt")),
    lambda d: replace(d, row="not a row"),
    lambda d: replace(d, candidate_id=7),
    lambda d: replace(d, require_demo=0),
    lambda d: replace(d, magic=1),
    lambda d: replace(d, max_drift_points=0),
    lambda d: replace(d, max_drift_points=True),
    lambda d: replace(d, max_spread_points=0),
    lambda d: replace(d, ref_price=math.nan),
])
def test_a_draft_refuses_anything_the_ea_contract_forbids(change: Any) -> None:
    draft = drafted(build())

    with pytest.raises(ValueError):
        change(draft)


def test_ids_come_from_the_csprng_and_a_bad_id_is_refused() -> None:
    assert re.fullmatch(INTENT_ID_PATTERN, drafted(build(new_id=ib.new_intent_id)).row.intent_id)
    assert refused(build(new_id=lambda: "NOT-AN-ID")) == (ib.BAD_INPUT,)


def test_the_poll_response_is_flat_and_signed() -> None:
    key = SecretStr(EA_KEY)

    response = to_poll_response(drafted(build()), int(NOW), key, POINT)

    assert response.has_intent and response.command == "NONE"
    assert (response.intent_id, response.order_type, response.entry, response.ref_price) == (
        FIXED_ID, "BUY_LIMIT", 4298.0, 4300.2)
    assert (response.source, response.max_drift_points, response.magic) == (
        "operator", 140, 250570)
    assert wire.verify_intent(key, response, POINT)
    assert not wire.verify_intent(key, response.model_copy(update={"lots": 0.0}), POINT)
    market = to_poll_response(drafted(build(entry=4300.2, style="MARKET")), int(NOW), key, POINT)
    assert (market.order_type, market.pending_expiry_epoch) == ("BUY", 0)
    assert wire.verify_intent(key, market, POINT)


@pytest.mark.parametrize("key", [None, SecretStr(""), SecretStr("short")])
def test_without_a_valid_key_the_response_is_unsigned(key: SecretStr | None) -> None:
    assert to_poll_response(drafted(build()), int(NOW), key, POINT).sig == ""


@pytest.mark.parametrize(("server_time", "point"), [
    (math.floor(NOW) + 120, POINT), (int(NOW), 0.03),
])
def test_an_expired_or_off_grid_response_is_never_produced(server_time: int, point: float) -> None:
    with pytest.raises(ValueError):
        to_poll_response(drafted(build()), server_time, SecretStr(EA_KEY), point)
