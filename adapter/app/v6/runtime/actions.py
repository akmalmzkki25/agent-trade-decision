"""
The management action waiting for the EA (spec section 3.3).

At most one action waits. It rides on every poll answer until the EA reports it
(`POST /v6/action` settles it) or it is older than ACTION_MAX_AGE_S, after which the EA
would refuse it anyway; the desk's supervise step then marks it EXPIRED. A newer action
replaces one still waiting (the EA applies each action id once). Everything here runs
on the event loop thread.
"""

from __future__ import annotations

from ..deliberation.publication import ManagementAction
from ..risk.limits import ACTION_MAX_AGE_S
from ..schemas.intent import PollResponse


class ActionBoard:
    def __init__(self) -> None:
        self._pending: ManagementAction | None = None

    def queue(self, action: ManagementAction) -> ManagementAction | None:
        """Wait with `action`; returns the unreported action it replaced, if any."""
        replaced, self._pending = self._pending, action
        return replaced

    def _fresh(self, now: float) -> bool:
        return self._pending is not None and now - self._pending.issued_at <= ACTION_MAX_AGE_S

    def for_poll(self, now: float) -> ManagementAction | None:
        """The action to serve on this poll (None once it is too old)."""
        return self._pending if self._fresh(now) else None

    def settle(self, action_id: str) -> bool:
        """The EA reported `action_id`: stop serving it."""
        if self._pending is None or self._pending.action_id != action_id:
            return False
        self._pending = None
        return True

    def pop_expired(self, now: float) -> ManagementAction | None:
        """The waiting action once it is too old to serve (it stops waiting)."""
        if self._pending is None or self._fresh(now):
            return None
        expired, self._pending = self._pending, None
        return expired


def action_response(action: ManagementAction, server_time: int) -> PollResponse:
    """The flat v6.intent.2 answer that carries `action` (unsigned; the replier signs it).

    Raises ValueError (pydantic) when the action breaks the wire rules of its command.
    """
    return PollResponse(
        server_time_epoch=server_time, command=action.command, action_id=action.action_id,
        action_ticket=action.ticket, action_sl=action.sl, action_tp=action.tp,
        action_tp1=action.tp1, action_tp2=action.tp2, action_sl1=action.sl_after_tp1,
        action_sl2=action.sl_after_tp2, action_price=action.price,
        action_expiry_epoch=action.expiry_epoch, action_barrier_s=action.barrier_s,
        action_issued_epoch=action.issued_at)
