"""runtime.commands: FLATTEN and CANCEL_PENDING for the EA, and their precedence."""

from __future__ import annotations

from app.v6.runtime.commands import COMMAND_TTL_S, CommandBoard, PendingCommand, poll_command

T0 = 1_789_650_001.0


def test_flatten_is_done_only_when_the_ea_shows_no_v6_exposure() -> None:
    flatten = PendingCommand(command="FLATTEN", reason="breaker_trip", requested_at=T0,
                             expires_at=T0 + COMMAND_TTL_S)
    cancel = PendingCommand(command="CANCEL_PENDING", reason="operator", requested_at=T0,
                            expires_at=T0 + COMMAND_TTL_S)
    assert [flatten.done(0, 1), flatten.done(1, 0), flatten.done(0, 0)] == [False, False, True]
    assert [cancel.done(0, 1), cancel.done(1, 0)] == [True, False]


def test_a_cancel_request_never_replaces_a_pending_flatten() -> None:
    board = CommandBoard()
    flatten = board.request_flatten("breaker_trip", T0)
    assert board.request_cancel_pending("session_stop", T0 + 1) == flatten
    assert board.command_for_poll(0, T0 + 2, open_v6_positions=1) == "FLATTEN"
    assert board.command_for_poll(0, T0 + 4, open_v6_positions=1) == "FLATTEN"
    assert board.command_for_poll(0, T0 + 6, open_v6_positions=0) == "NONE"
    assert board.request_cancel_pending("session_stop", T0 + 7).command == "CANCEL_PENDING"


def test_a_flatten_replaces_a_pending_cancel_and_expires_like_it() -> None:
    board = CommandBoard()
    board.request_cancel_pending("halt", T0)
    board.request_flatten("breaker_trip", T0 + 1)
    assert board.command_for_poll(2, T0 + 2, open_v6_positions=1) == "FLATTEN"
    assert board.command_for_poll(2, T0 + 1 + COMMAND_TTL_S, open_v6_positions=1) == "NONE"


def test_poll_command_puts_flatten_before_the_halt() -> None:
    board = CommandBoard()
    board.request_flatten("breaker_trip", T0)
    assert poll_command(board, halted=True, pending_v6_orders=0, now=T0 + 1,
                        open_v6_positions=1) == "FLATTEN"
    assert poll_command(board, halted=True, pending_v6_orders=0, now=T0 + 2) == "CANCEL_PENDING"
    assert poll_command(board, halted=False, pending_v6_orders=0, now=T0 + 3) == "NONE"
