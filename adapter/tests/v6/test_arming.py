"""runtime.arming: when an execute session may publish intents, and when it must stop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import pytest
from pydantic import SecretStr

from app.v6.config import V6Settings
from app.v6.ledger_cycles_schema import SessionRecord
from app.v6.risk.breakers import BreakerStatus
from app.v6.risk.policy import PolicyDecision
from app.v6.runtime import arming as am
from app.v6.runtime.arming import ArmDecision, arm_action, decide_arm
from app.v6.runtime.ea_state import EaStateView, PollObservation, SnapshotMeta
from app.v6.runtime.watchdog import BreakerSummary

from .payloads_v6 import as_poll, poll_payload

TOKEN: Final[str] = "operator-token-" + "t" * 40
EA_KEY: Final[str] = "ea-hmac-key-" + "e" * 40
NOW: Final[float] = 1_789_565_408.0
REFUSED: Final[PolicyDecision] = PolicyDecision(allowed=False, code="POLICY_SERVER_NOT_DEMO")
ALLOWED: Final[PolicyDecision] = PolicyDecision(allowed=True, code="POLICY_OK")


@dataclass(frozen=True)
class Breakers:
    tripped: bool = False


def settings(**changes: Any) -> V6Settings:
    base = dict(enabled=True, mode="execute", backend="operator", operator_token=TOKEN,
                ea_hmac_key=EA_KEY)
    return V6Settings(_env_file=None, **{**base, **changes})


def session(**changes: Any) -> SessionRecord:
    fields = dict(session_id="a1b2c3d4e5f6", trading_day="2026-09-17", backend="operator",
                  mode="execute", started_at=NOW - 60, stopped_at=None, stop_reason=None,
                  armed=False)
    return SessionRecord(**{**fields, **changes})


def view(*, age: float | None = 1.0, snapshot_mode: str | None = None,
         **poll_changes: Any) -> EaStateView:
    poll = None
    if age is not None:
        poll = PollObservation(as_poll({**poll_payload(), "server": "MetaQuotes-Demo",
                                        **poll_changes}), NOW - age)
    snapshot = None if snapshot_mode is None else SnapshotMeta(
        snapshot_id="Q6S-12345-1789564500", cycle_id="c-1", bar_open_epoch=1_789_564_500,
        received_at=NOW - 500, trade_mode=snapshot_mode, clock_skew_s=0.4)
    return EaStateView(last_poll=poll, last_snapshot=snapshot,
                       last_seen_at=None if poll is None else poll.received_at,
                       superseded=0, inbox_pending=0)


def decide(**overrides: Any) -> ArmDecision:
    args: dict[str, Any] = dict(settings=settings(), session=session(), policy=ALLOWED,
                                breakers=Breakers(), ea_view=view(), now=NOW, halted=False)
    return decide_arm(**{**args, **overrides})


def test_a_healthy_demo_execute_session_is_armed() -> None:
    decision = decide()

    assert (decision.armed, decision.reason) == (True, am.ARMED)
    assert decide(ea_view=view(snapshot_mode="DEMO"), policy=None).armed


@pytest.mark.parametrize(("overrides", "reason"), [
    ({"settings": settings(mode="shadow")}, am.DISARM_MODE),
    ({"settings": settings(mode="shadow", backend="operator")}, am.DISARM_MODE),
    ({"settings": settings().model_copy(update={"backend": "rules"})}, am.DISARM_SETTINGS),
    ({"settings": settings().model_copy(update={"ea_hmac_key": SecretStr("")})},
     am.DISARM_SETTINGS),
    ({"settings": settings().model_copy(update={"max_lots": 0.02})}, am.DISARM_SETTINGS),
    ({"session": None}, am.DISARM_NO_SESSION),
    ({"session": session(stopped_at=NOW - 1, stop_reason="operator")}, am.DISARM_NO_SESSION),
    ({"session": session(mode="shadow")}, am.DISARM_SESSION_MODE),
    ({"halted": True}, am.DISARM_HALTED),
    ({"breakers": Breakers(tripped=True)}, am.DISARM_BREAKER),
    ({"breakers": None}, am.DISARM_BREAKER),
    ({"breakers": BreakerStatus()}, am.DISARM_BREAKER),
    ({"ea_view": view(age=None)}, am.DISARM_EA_NOT_SEEN),
    ({"ea_view": view(age=30.5)}, am.DISARM_EA_STALE),
    ({"ea_view": view(age=-5.5)}, am.DISARM_EA_STALE),
    ({"ea_view": view(trade_mode="REAL")}, am.DISARM_NOT_DEMO),
    ({"ea_view": view(trade_mode="CONTEST")}, am.DISARM_NOT_DEMO),
    ({"ea_view": view(snapshot_mode="REAL")}, am.DISARM_NOT_DEMO),
    ({"policy": REFUSED}, am.DISARM_POLICY),
    ({"ea_view": view(server="Broker-Live")}, am.DISARM_POLICY),
    ({"settings": settings(allowed_logins_csv="777")}, am.DISARM_POLICY),
    ({"ea_view": view(local_halt=True)}, am.DISARM_EA_HALT),
])
def test_every_condition_disarms(overrides: dict[str, Any], reason: str) -> None:
    decision = decide(**overrides)

    assert (decision.armed, decision.reason) == (False, reason)
    assert decision.detail


def test_the_first_failed_condition_is_reported() -> None:
    everything_wrong = decide(settings=settings(mode="shadow"), session=None, halted=True,
                              breakers=None, ea_view=view(age=None), policy=REFUSED)
    unsafe_and_stale = decide(halted=True, ea_view=view(age=99, trade_mode="REAL"))

    assert everything_wrong.reason == am.DISARM_MODE
    assert unsafe_and_stale.reason == am.DISARM_HALTED


def test_breaker_summaries_from_the_watchdog_are_understood() -> None:
    assert decide(breakers=BreakerSummary()).armed
    tripped = BreakerSummary(tripped=True, labels=("daily:2026-09-17",))
    assert decide(breakers=tripped).reason == am.DISARM_BREAKER


def test_the_limit_of_ea_staleness_is_inclusive() -> None:
    limit = settings().ea_stale_s

    assert decide(ea_view=view(age=limit)).armed
    assert decide(ea_view=view(age=-settings().max_clock_skew_s)).armed


# --- decisions and actions ---------------------------------------------------------------------
@pytest.mark.parametrize(("armed", "reason"), [
    (True, am.DISARM_HALTED), (False, am.ARMED), (False, "SOMETHING_ELSE"),
])
def test_a_decision_is_armed_exactly_when_its_reason_says_so(armed: bool, reason: str) -> None:
    with pytest.raises(ValueError):
        ArmDecision(armed=armed, reason=reason)


@pytest.mark.parametrize(("record", "armed", "may_arm", "action"), [
    (None, True, True, "KEEP"),
    (session(stopped_at=NOW, stop_reason="x"), True, True, "KEEP"),
    (session(armed=False), True, True, "ARM"),
    (session(armed=False), True, False, "KEEP"),
    (session(armed=False), False, True, "KEEP"),
    (session(armed=True, armed_at=NOW), False, False, "DISARM"),
    (session(armed=True, armed_at=NOW), False, True, "DISARM"),
    (session(armed=True, armed_at=NOW), True, False, "KEEP"),
])
def test_arm_action_arms_only_when_allowed_and_always_disarms(
        record: SessionRecord | None, armed: bool, may_arm: bool, action: str) -> None:
    decision = (ArmDecision(armed=True, reason=am.ARMED) if armed
                else ArmDecision(armed=False, reason=am.DISARM_HALTED, detail="halt file"))

    assert arm_action(record, decision, may_arm=may_arm) == action


def test_decisions_are_frozen_values() -> None:
    decision = decide()

    assert decision == ArmDecision(armed=True, reason=am.ARMED, detail=decision.detail)
    with pytest.raises(AttributeError):
        decision.armed = False  # type: ignore[misc]
