"""
What the EA reported about a management action or an SL+ step (`v6.action.1`).

  APPLIED     the action becomes final in v6_actions; a MODIFY stores its ladder and
              holding time on the intent, a MODIFY_PENDING also the order's new levels
              (the initial risk of the later position is measured from them)
  REJECTED    the action becomes final; the intent keeps its plan
  FAILED      the same
  PLAN_STEP   an SL+ step the EA took on its own, recorded once per (ticket, step) and
              raised on the intent (a step never goes back)

A report for an action that is already final (a resend) changes nothing. Blocking:
call `apply` in a worker thread.
"""

from __future__ import annotations

from typing import Final

from ..ledger_actions import ActionRow
from ..ledger_cycles import LedgerCycles
from ..schemas.intent import ActionReport

RESULT_DUPLICATE: Final[str] = "DUPLICATE"
RESULT_STEP: Final[str] = "STEP"
MODIFY_COMMANDS: Final[frozenset[str]] = frozenset({"MODIFY_POSITION", "MODIFY_PENDING"})
PENDING_COMMAND: Final[str] = "MODIFY_PENDING"


class ActionDesk:
    def __init__(self, ledger: LedgerCycles) -> None:
        self._ledger = ledger

    def apply(self, report: ActionReport, now: float) -> str:
        """The report's effect: its kind, STEP, or DUPLICATE for a resend."""
        if report.kind == "PLAN_STEP":
            return self._step(report, now)
        detail = f"{report.reason_code} retcode {report.retcode}"
        if not self._ledger.actions.mark(report.action_id, report.kind, detail, now):
            return RESULT_DUPLICATE
        if report.kind == "APPLIED":
            row = self._ledger.actions.get(report.action_id)
            if row is not None:
                self._store_plan(row)
        return report.kind

    def _intent_id(self, report: ActionReport) -> str:
        if report.intent_id:
            return report.intent_id
        record = self._ledger.intents.by_ticket(report.ticket)
        return "" if record is None else record.intent_id

    def _step(self, report: ActionReport, now: float) -> str:
        intent_id = self._intent_id(report)
        recorded = self._ledger.actions.record_step(
            intent_id, report.ticket, report.step, report.old_sl, report.new_sl,
            report.price, now)
        if intent_id:
            self._ledger.intents.set_plan_step(intent_id, report.step)
        return RESULT_STEP if recorded else RESULT_DUPLICATE

    def _store_plan(self, row: ActionRow) -> None:
        if row.command not in MODIFY_COMMANDS or not row.intent_id:
            return
        levels = row.payload
        intents = self._ledger.intents
        intents.update_plan(
            row.intent_id, tp1=float(levels["tp1"]), tp2=float(levels["tp2"]),
            sl_after_tp1=float(levels["sl_after_tp1"]),
            sl_after_tp2=float(levels["sl_after_tp2"]),
            time_barrier_s=int(levels["barrier_s"]))
        if row.command == PENDING_COMMAND:
            intents.update_levels(row.intent_id, entry=float(levels["price"]),
                                  sl=float(levels["sl"]), tp=float(levels["tp"]))
