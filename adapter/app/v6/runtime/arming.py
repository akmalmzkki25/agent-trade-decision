"""
Arming: may the active daily session publish intents right now? (plan sections 4, 4b, 6)

`decide_arm` is pure. Session start ("Mulai trading skrg") and an operator resume use
it to ARM an execute session; the watchdog calls it on every tick to DISARM, never to
re-arm (resuming is the operator's call). The first failed check is the reason:

  MODE_NOT_EXECUTE     V6_MODE is not execute (shadow never arms)
  SETTINGS_POLICY      `check_settings_policy` refuses (operator backend, V6 EA key, lot cap)
  NO_SESSION           no active daily session
  SESSION_NOT_EXECUTE  the session was started in another mode
  HALTED               a kill switch is on (halt file, dashboard, operator)
  BREAKER              a breaker is tripped, or the breakers were not evaluated (None)
  EA_NOT_SEEN          the EA has not polled since the adapter started
  EA_STALE             the newest poll is older than V6_EA_STALE_S (or ahead of the clock
                       by more than V6_MAX_CLOCK_SKEW_S)
  NOT_DEMO             the newest poll or snapshot is not from a DEMO account
  ACCOUNT_POLICY       the caller's policy decision, or the operator policy for the polled
                       account (demo server pattern, allowed logins), refuses
  EA_LOCAL_HALT        the EA reports its own halt (QlipV6_HALT, AutoTrading off)
Otherwise ARMED.

After a DISARM (`arm_action`) the caller sets the session disarmed with the reason,
cancels undelivered intents (`IntentBook.cancel_all`) and queues CANCEL_PENDING; open
positions keep their broker-side SL/TP and the EA's time barrier.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, Protocol

from ..config import V6Settings
from ..ledger_cycles_schema import SessionRecord
from ..risk.policy import (
    DEMO_TRADE_MODE, EXECUTE_MODE, OPERATOR_SOURCE, PolicyDecision, check_settings_policy,
    evaluate_account_policy,
)
from .ea_state import EaStateView

ARMED: Final[str] = "ARMED"
DISARM_MODE: Final[str] = "MODE_NOT_EXECUTE"
DISARM_SETTINGS: Final[str] = "SETTINGS_POLICY"
DISARM_NO_SESSION: Final[str] = "NO_SESSION"
DISARM_SESSION_MODE: Final[str] = "SESSION_NOT_EXECUTE"
DISARM_HALTED: Final[str] = "HALTED"
DISARM_BREAKER: Final[str] = "BREAKER"
DISARM_EA_NOT_SEEN: Final[str] = "EA_NOT_SEEN"
DISARM_EA_STALE: Final[str] = "EA_STALE"
DISARM_NOT_DEMO: Final[str] = "NOT_DEMO"
DISARM_POLICY: Final[str] = "ACCOUNT_POLICY"
DISARM_EA_HALT: Final[str] = "EA_LOCAL_HALT"
ARM_REASONS: Final[frozenset[str]] = frozenset({
    ARMED, DISARM_MODE, DISARM_SETTINGS, DISARM_NO_SESSION, DISARM_SESSION_MODE, DISARM_HALTED,
    DISARM_BREAKER, DISARM_EA_NOT_SEEN, DISARM_EA_STALE, DISARM_NOT_DEMO, DISARM_POLICY,
    DISARM_EA_HALT,
})

ArmAction = Literal["ARM", "DISARM", "KEEP"]
ARM: Final[ArmAction] = "ARM"
DISARM: Final[ArmAction] = "DISARM"
KEEP: Final[ArmAction] = "KEEP"


@dataclass(frozen=True)
class ArmDecision:
    """`armed` exactly when `reason` is ARMED; `reason` doubles as the disarm reason."""

    armed: bool
    reason: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.reason not in ARM_REASONS or self.armed != (self.reason == ARMED):
            raise ValueError("an arm decision is armed exactly when its reason is ARMED")


class BreakerView(Protocol):
    """`risk.breakers.BreakerStatus` or `runtime.watchdog.BreakerSummary`."""

    @property
    def tripped(self) -> bool: ...


def _refused(reason: str, detail: str) -> ArmDecision:
    return ArmDecision(armed=False, reason=reason, detail=detail)


def _setup_refusal(settings: V6Settings, session: SessionRecord | None) -> ArmDecision | None:
    if settings.mode != EXECUTE_MODE:
        return _refused(DISARM_MODE, f"V6_MODE={settings.mode} never publishes intents")
    decision = check_settings_policy(settings)
    if not decision.allowed:
        return _refused(DISARM_SETTINGS, decision.code)
    if session is None or not session.is_active:
        return _refused(DISARM_NO_SESSION, "no active daily session")
    if session.mode != EXECUTE_MODE:
        return _refused(DISARM_SESSION_MODE,
                        f"session {session.session_id} was started in {session.mode} mode")
    return None


def _safety_refusal(halted: bool, breakers: BreakerView | None) -> ArmDecision | None:
    if halted:
        return _refused(DISARM_HALTED, "a kill switch is on")
    if breakers is None or breakers.tripped:
        return _refused(DISARM_BREAKER, "a breaker is tripped or was not evaluated")
    return None


def _ea_refusal(settings: V6Settings, policy: PolicyDecision | None, view: EaStateView,
                now: float) -> ArmDecision | None:
    observed = view.last_poll
    if observed is None:
        return _refused(DISARM_EA_NOT_SEEN, "the EA has not polled yet")
    age = now - observed.received_at
    if not -settings.max_clock_skew_s <= age <= settings.ea_stale_s:
        return _refused(DISARM_EA_STALE, f"the newest EA poll is {age:.1f} s old")
    poll, snapshot = observed.poll, view.last_snapshot
    modes = frozenset({poll.trade_mode} | ({snapshot.trade_mode} if snapshot else set()))
    if modes != {DEMO_TRADE_MODE}:
        return _refused(DISARM_NOT_DEMO, "the EA reports " + ",".join(sorted(modes)))
    polled = evaluate_account_policy(poll.trade_mode, poll.server, poll.login, settings,
                                     source=OPERATOR_SOURCE)
    for decision in (policy, polled):
        if decision is not None and not decision.allowed:
            return _refused(DISARM_POLICY, decision.code)
    if poll.local_halt:
        return _refused(DISARM_EA_HALT, "the EA reports its local halt")
    return None


def decide_arm(settings: V6Settings, session: SessionRecord | None,
               policy: PolicyDecision | None, breakers: BreakerView | None,
               ea_view: EaStateView, *, now: float, halted: bool) -> ArmDecision:
    """May `session` publish intents now? See the module docstring for the checks.

    `policy`: an account policy the caller already evaluated (None: none); the
    operator policy for the newest polled account is always evaluated as well.
    `breakers`: None when they could not be evaluated (fails closed). `halted`: any
    adapter-side kill switch.
    """
    refusal = (_setup_refusal(settings, session) or _safety_refusal(halted, breakers)
               or _ea_refusal(settings, policy, ea_view, now))
    if refusal is not None:
        return refusal
    return ArmDecision(armed=True, reason=ARMED, detail="the execute session may publish")


def arm_action(session: SessionRecord | None, decision: ArmDecision, *,
               may_arm: bool) -> ArmAction:
    """What to do with the session: always DISARM on a refusal, ARM only when `may_arm`.

    Session start and resume pass may_arm=True; the watchdog passes False.
    """
    if session is None or not session.is_active:
        return KEEP
    if session.armed and not decision.armed:
        return DISARM
    if not session.armed and decision.armed and may_arm:
        return ARM
    return KEEP
