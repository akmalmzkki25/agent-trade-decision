//+------------------------------------------------------------------+
//| QlipV6/Report.mqh                                                |
//| v6.execution.1 reports: what the EA did with one intent. A       |
//| report is built once (sent_at_epoch is its build time), queued   |
//| in the outbox and resent unchanged until the adapter answers.    |
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

#endif // QLIPV6_REPORT_MQH
