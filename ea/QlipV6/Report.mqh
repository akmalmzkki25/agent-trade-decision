//+------------------------------------------------------------------+
//| QlipV6/Report.mqh                                                |
//| v6.execution.1 reports: what the EA did with one intent, and     |
//| v6.action.1 reports: what it did with a management action or an  |
//| SL+ step it took. A report is built once (sent_at_epoch is its   |
//| build time), queued in the outbox and resent unchanged until the |
//| adapter answers.                                                 |
//+------------------------------------------------------------------+
#ifndef QLIPV6_REPORT_MQH
#define QLIPV6_REPORT_MQH

#include "Json.mqh"
#include "Intent.mqh"
#include "Outbox.mqh"

#define EXECUTION_SCHEMA          "v6.execution.1"
#define SLIPPAGE_DIGITS           1

// ExecutionStatus in app/v6/schemas/intent.py.
#define STATUS_PLACED             "placed"
#define STATUS_FILLED             "filled"
#define STATUS_REJECTED_LOCAL     "rejected_local"
#define STATUS_EXPIRED            "expired"
#define STATUS_CANCELLED          "cancelled"
#define STATUS_FAILED             "failed"
#define STATUS_DRY_RUN            "dry_run"

// ExecutionReason in app/v6/schemas/intent.py. DUPLICATE is reserved: a
// re-delivered intent is ignored without a report (contract §8.1 check 2).
#define REASON_NONE               "NONE"
#define REASON_EXPIRED            "EXPIRED"
#define REASON_DRIFT              "DRIFT"
#define REASON_SPREAD             "SPREAD"
#define REASON_DEMO_REQUIRED      "DEMO_REQUIRED"
#define REASON_LOT_CAP            "LOT_CAP"
#define REASON_RISK_CAP           "RISK_CAP"
#define REASON_BREAKER            "BREAKER"
#define REASON_HALTED             "HALTED"
#define REASON_OCCUPIED           "OCCUPIED"
#define REASON_ORDER_CHECK        "ORDER_CHECK"
#define REASON_DUPLICATE          "DUPLICATE"
#define REASON_BAD_SIGNATURE      "BAD_SIGNATURE"
#define REASON_BAD_INTENT         "BAD_INTENT"
#define REASON_NO_SL              "NO_SL"
#define REASON_MARKET_CLOSED      "MARKET_CLOSED"
#define REASON_BROKER_ERROR       "BROKER_ERROR"
#define REASON_EXECUTE_DISABLED   "EXECUTE_DISABLED"
#define REASON_COMMAND            "COMMAND"

// ActionReport in app/v6/schemas/intent.py: ActionKind and ActionReason.
#define ACTION_SCHEMA               "v6.action.1"
#define ACTION_KIND_APPLIED         "APPLIED"
#define ACTION_KIND_REJECTED        "REJECTED"
#define ACTION_KIND_FAILED          "FAILED"
#define ACTION_KIND_PLAN_STEP       "PLAN_STEP"
#define ACT_REASON_NONE             "NONE"
#define ACT_REASON_UNKNOWN_TICKET   "UNKNOWN_TICKET"
#define ACT_REASON_STALE            "STALE"
#define ACT_REASON_SL_WIDER         "SL_WIDER"
#define ACT_REASON_TOO_CLOSE        "TOO_CLOSE"
#define ACT_REASON_BARRIER          "BARRIER"
#define ACT_REASON_DEMO_REQUIRED    "DEMO_REQUIRED"
#define ACT_REASON_HALTED           "HALTED"
#define ACT_REASON_MARKET_CLOSED    "MARKET_CLOSED"
#define ACT_REASON_BROKER_ERROR     "BROKER_ERROR"
#define ACT_REASON_BAD_ACTION       "BAD_ACTION"

struct ExecReport
{
   string            intent_id;
   string            status;
   string            reason_code;
   long              ticket;
   long              retcode;
   double            requested_price;
   double            fill_price;
   double            slippage_points;
   long              spread_points;
   long              latency_ms;
   long              sent_at_epoch;
};

long CurrentSpreadPoints(void)
{
   MqlTick tick;
   ZeroMemory(tick);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(!SymbolInfoTick(_Symbol, tick) || point <= 0.0 || tick.bid <= 0.0 || tick.ask < tick.bid)
      return 0;
   return (long)MathRound((tick.ask - tick.bid) / point);
}

long ElapsedMs(const ulong since_ms)
{
   ulong now_ms = GetTickCount64();
   if(since_ms == 0 || now_ms < since_ms)
      return 0;
   return (long)(now_ms - since_ms);
}

// Signed slippage in points, positive when the fill was worse than asked.
double AdverseSlippagePoints(const bool is_buy, const double requested, const double filled,
                             const double point)
{
   if(requested <= 0.0 || filled <= 0.0 || point <= 0.0)
      return 0.0;
   double worse = is_buy ? filled - requested : requested - filled;
   return worse / point;
}

void ReportStart(ExecReport &r, const string intent_id, const string status, const string reason)
{
   r.intent_id = intent_id;
   r.status = status;
   r.reason_code = reason;
   r.ticket = 0;
   r.retcode = 0;
   r.requested_price = 0.0;
   r.fill_price = 0.0;
   r.slippage_points = 0.0;
   r.spread_points = CurrentSpreadPoints();
   r.latency_ms = 0;
   r.sent_at_epoch = (long)TimeGMT();
}

// The rules ExecutionReport enforces, so a report is never refused with a 400.
bool ReportIsConsistent(const ExecReport &r)
{
   bool accepted = r.status == STATUS_PLACED || r.status == STATUS_FILLED;
   bool refused = r.status == STATUS_REJECTED_LOCAL || r.status == STATUS_FAILED
                  || r.status == STATUS_DRY_RUN;
   if(accepted && (r.reason_code != REASON_NONE || r.ticket <= 0))
      return false;
   if(refused && r.reason_code == REASON_NONE)
      return false;
   return IsIntentId(r.intent_id);
}

string ExecutionReportJson(const ExecReport &r)
{
   CJsonObject o;
   o.AddStr("schema_version", EXECUTION_SCHEMA);
   o.AddStr("intent_id", r.intent_id);
   o.AddStr("status", r.status);
   o.AddStr("reason_code", r.reason_code);
   o.AddInt("ticket", MathMax(r.ticket, (long)0));
   o.AddInt("retcode", MathMax(r.retcode, (long)0));
   o.AddNum("requested_price", MathMax(r.requested_price, 0.0), _Digits);
   o.AddNum("fill_price", MathMax(r.fill_price, 0.0), _Digits);
   o.AddNum("slippage_points", r.slippage_points, SLIPPAGE_DIGITS);
   o.AddInt("spread_points", MathMax(r.spread_points, (long)0));
   o.AddInt("latency_ms", MathMax(r.latency_ms, (long)0));
   o.AddInt("sent_at_epoch", MathMax(r.sent_at_epoch, (long)0));
   return o.Text();
}

bool QueueExecutionReport(const ExecReport &r)
{
   PrintFormat("V6 intent %s: %s/%s ticket=%I64d retcode=%I64d requested=%.2f fill=%.2f "
               "slippage=%.1f spread=%I64d latency=%I64d ms", r.intent_id, r.status, r.reason_code,
               r.ticket, r.retcode, r.requested_price, r.fill_price, r.slippage_points,
               r.spread_points, r.latency_ms);
   if(!ReportIsConsistent(r))
   {
      Print("V6 report NOT queued: it breaks the v6.execution.1 rules (internal error)");
      return false;
   }
   return g_outbox.Enqueue(PATH_EXECUTION, ExecutionReportJson(r));
}

//--- v6.action.1 ---------------------------------------------------------

string ActionReportJson(const string kind, const string action_id, const string command,
                        const string intent_id, const long ticket, const string reason,
                        const long retcode, const int step, const double old_sl,
                        const double new_sl, const double price)
{
   CJsonObject o;
   o.AddStr("schema_version", ACTION_SCHEMA);
   o.AddStr("kind", kind);
   o.AddStr("action_id", action_id);
   o.AddStr("command", command);
   o.AddStr("intent_id", intent_id);
   o.AddInt("ticket", MathMax(ticket, (long)0));
   o.AddStr("reason_code", reason);
   o.AddInt("retcode", MathMax(retcode, (long)0));
   o.AddInt("step", step);
   o.AddNum("old_sl", MathMax(old_sl, 0.0), _Digits);
   o.AddNum("new_sl", MathMax(new_sl, 0.0), _Digits);
   o.AddNum("price", MathMax(price, 0.0), _Digits);
   o.AddInt("sent_at_epoch", (long)TimeGMT());
   return o.Text();
}

// What the EA did with a management action (the new stop is the action's).
bool QueueActionReport(const string kind, const PollReply &p, const string reason,
                       const uint retcode, const double old_sl)
{
   string json = ActionReportJson(kind, p.action_id, p.command, "", p.action_ticket, reason,
                                  (long)retcode, 0, old_sl, p.action_sl, p.action_price);
   return g_outbox.Enqueue(PATH_ACTION, json);
}

// An SL+ step the EA took on its own.
bool QueuePlanStepReport(const string intent_id, const long ticket, const int step,
                         const double old_sl, const double new_sl, const double price)
{
   string json = ActionReportJson(ACTION_KIND_PLAN_STEP, "", CMD_NONE, intent_id, ticket,
                                  ACT_REASON_NONE, 0, step, old_sl, new_sl, price);
   return g_outbox.Enqueue(PATH_ACTION, json);
}

#endif // QLIPV6_REPORT_MQH
