"""
The one pending EA command (plan sections 4b and 6; wire contract section 6.3).

CANCEL_PENDING follows a session stop, a halt and every disarm; FLATTEN follows a
new breaker trip (plan section 6, gate 10: HALTED + FLATTEN + CANCEL_PENDING).
FLATTEN outranks CANCEL_PENDING: the EA's FLATTEN also deletes the pending
orders, so a later CANCEL_PENDING request never replaces a FLATTEN in flight.

A command is repeated on every poll until a poll after its first delivery shows
it done (no V6 pending order; for FLATTEN no V6 position either), or until its
TTL passes. Everything here runs on the event loop thread; each change replaces
the frozen value.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Final

from ..schemas.intent import Command

CANCEL_PENDING: Final[Command] = "CANCEL_PENDING"
FLATTEN: Final[Command] = "FLATTEN"
NO_COMMAND: Final[Command] = "NONE"
# The EA polls every ~2 s; a command nobody acknowledges is dropped after this.
COMMAND_TTL_S: Final[float] = 600.0
CANCEL_PENDING_TTL_S: Final[float] = COMMAND_TTL_S


@dataclass(frozen=True)
class PendingCommand:
    command: Command
    reason: str
    requested_at: float
    expires_at: float
    delivered_at: float | None = None

    def done(self, pending_v6_orders: int, open_v6_positions: int) -> bool:
        """Whether a poll showing these counts proves the command was carried out."""
        if self.command == FLATTEN:
            return pending_v6_orders == 0 and open_v6_positions == 0
        return pending_v6_orders == 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class CommandBoard:
    """At most one pending EA command; FLATTEN is never downgraded."""

    def __init__(self) -> None:
        self._pending: PendingCommand | None = None

    def _set(self, command: Command, reason: str, now: float) -> PendingCommand:
        self._pending = PendingCommand(command=command, reason=reason, requested_at=now,
                                       expires_at=now + COMMAND_TTL_S)
        return self._pending

    def request_cancel_pending(self, reason: str, now: float) -> PendingCommand:
        """Queue CANCEL_PENDING; a FLATTEN still pending is kept (it cancels orders too)."""
        pending = self.current(now)
        if pending is not None and pending.command == FLATTEN:
            return pending
        return self._set(CANCEL_PENDING, reason, now)

    def request_flatten(self, reason: str, now: float) -> PendingCommand:
        """Queue FLATTEN: close every V6 position and delete every V6 pending order."""
        return self._set(FLATTEN, reason, now)

    def current(self, now: float) -> PendingCommand | None:
        if self._pending is not None and now >= self._pending.expires_at:
            self._pending = None
        return self._pending

    def command_for_poll(self, pending_v6_orders: int, now: float,
                         open_v6_positions: int = 0) -> Command:
        """Command for this poll; cleared once a poll after delivery shows it done."""
        pending = self.current(now)
        if pending is None:
            return NO_COMMAND
        if pending.delivered_at is not None and pending.done(pending_v6_orders,
                                                             open_v6_positions):
            self._pending = None
            return NO_COMMAND
        if pending.delivered_at is None:
            self._pending = replace(pending, delivered_at=now)
        return pending.command


def poll_command(board: CommandBoard, *, halted: bool, pending_v6_orders: int, now: float,
                 open_v6_positions: int = 0) -> Command:
    """What `/v6/intent/poll` should answer. The board is consulted on every poll."""
    queued = board.command_for_poll(pending_v6_orders, now, open_v6_positions)
    if queued == FLATTEN:
        return FLATTEN
    return CANCEL_PENDING if halted else queued
