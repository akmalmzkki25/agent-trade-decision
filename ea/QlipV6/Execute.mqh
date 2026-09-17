//+------------------------------------------------------------------+
//| QlipV6/Execute.mqh                                               |
//| Acting on one signed intent: the refusal checks of contract      |
//| §8.1 in their order, then OrderCheck and the order itself        |
//| through Orders.mqh. Every outcome is reported through the        |
//| outbox; an intent processed before is ignored without a report.  |
//+------------------------------------------------------------------+
#ifndef QLIPV6_EXECUTE_MQH
#define QLIPV6_EXECUTE_MQH

#include "Config.mqh"
#include "Json.mqh"
#include "Market.mqh"
#include "Persist.mqh"
#include "Intent.mqh"
#include "Orders.mqh"
#include "Report.mqh"
#include "Track.mqh"
#include "Breaker.mqh"
#include "Basket.mqh"
#include "Schedule.mqh"
#include "Checks.mqh"
#include "Manage.mqh"

#define BROKER_TEXT_CHARS   80

//--- the refusal chain (contract §8.1, in order) ------------------------

string MarketRefusal(const PollReply &p, const EntryQuote &q)
{
   if(q.valid && q.spread_points > p.max_spread_points)
      return REASON_SPREAD;
   if(q.valid && (!WithinDrift(p, q) || !LimitStillPassive(p, q)))
      return REASON_DRIFT;
   if(!q.valid || !MarketOpenForEntry(q))
      return REASON_MARKET_CLOSED;
   if(!RiskWithinCap(p, q))
      return REASON_RISK_CAP;
   return "";
}

// "" when the intent may go on to OrderCheck.
string EntryRefusal(const PollReply &p, const EntryQuote &q)
{
   if(!g_cfg.execute_input)
      return REASON_EXECUTE_DISABLED;
   if(!AccountIsDemo())
      return REASON_DEMO_REQUIRED;
   if(p.require_demo != REQUIRE_DEMO || !IntentShapeOk(p))
      return REASON_BAD_INTENT;
   if(p.sl <= 0.0)
      return REASON_NO_SL;
   if(!StopLossOnItsSide(p) || !OnPriceGrid(p.sl))
      return REASON_BAD_INTENT;
   if((long)TimeGMT() >= p.valid_until_epoch)
      return REASON_EXPIRED;
   if(LocalHaltActive())
      return REASON_HALTED;
   if(g_breaker.tripped)
      return REASON_BREAKER;
   if(V6Occupied())
      return REASON_OCCUPIED;
   if(LotsToHundredths(p.lots) > LotsToHundredths(g_cfg.max_lots))
      return REASON_LOT_CAP;
   return MarketRefusal(p, q);
}

//--- the order ----------------------------------------------------------

double NormalizePrice(const double price)
{
   double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tick_size <= 0.0)
      return NormalizeDouble(price, _Digits);
   return NormalizeDouble(MathRound(price / tick_size) * tick_size, _Digits);
}

double NormalizeLots(const double lots)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0.0)
      return lots;
   int digits = (int)MathMax(0.0, MathCeil(-MathLog10(step) - GRID_EPSILON));
   return NormalizeDouble(MathRound(lots / step) * step, digits);
}

ENUM_ORDER_TYPE OrderTypeFor(const string order_type)
{
   if(order_type == INTENT_BUY_LIMIT)
      return ORDER_TYPE_BUY_LIMIT;
   if(order_type == INTENT_SELL_LIMIT)
      return ORDER_TYPE_SELL_LIMIT;
   if(order_type == INTENT_BUY)
      return ORDER_TYPE_BUY;
   return ORDER_TYPE_SELL;
}

// SL and TP travel with the order. A limit order expires at
// pending_expiry_epoch (UTC) converted to server time.
void BuildEntryRequest(const PollReply &p, const EntryQuote &q, MqlTradeRequest &req)
{
   bool limit = IsLimitOrder(p.order_type);
   bool is_buy = p.side == INTENT_SIDE_BUY;
   ZeroMemory(req);
   req.action = limit ? TRADE_ACTION_PENDING : TRADE_ACTION_DEAL;
   req.symbol = _Symbol;
   req.magic = (ulong)g_cfg.magic;
   req.volume = NormalizeLots(p.lots);
   req.type = OrderTypeFor(p.order_type);
   req.price = limit ? NormalizePrice(p.entry) : (is_buy ? q.ask : q.bid);
   req.sl = NormalizePrice(p.sl);
   req.tp = NormalizePrice(p.tp);
   req.deviation = (ulong)(limit ? p.max_drift_points : MarketDeviationPoints(p, q));
   req.comment = ORDER_COMMENT_PREFIX + p.intent_id;
   req.type_time = limit ? ORDER_TIME_SPECIFIED : ORDER_TIME_GTC;
   req.expiration = limit ? (datetime)(p.pending_expiry_epoch + ServerGmtOffsetSeconds()) : (datetime)0;
}

// The order ticket, which is also the identifier of the position it opens.
ulong EntryKey(const MqlTradeResult &res)
{
   if(res.order > 0)
      return res.order;
   if(res.deal > 0 && HistoryDealSelect(res.deal))
      return (ulong)HistoryDealGetInteger(res.deal, DEAL_POSITION_ID);
   return 0;
}

double MarketFillPrice(const MqlTradeResult &res, const ulong key)
{
   if(res.price > 0.0)
      return res.price;
   if(res.deal > 0 && HistoryDealSelect(res.deal))
      return HistoryDealGetDouble(res.deal, DEAL_PRICE);
   return EntryDealPrice(key);
}

void TrackEntry(const PollReply &p, const EntryQuote &q, const MqlTradeRequest &req,
                const ExecReport &r, const ulong key)
{
   TrackRecord t;
   ZeroMemory(t);
   t.key = key;
   t.state = IsLimitOrder(p.order_type) ? TRACK_STATE_PENDING : TRACK_STATE_OPEN;
   t.side = (p.side == INTENT_SIDE_BUY) ? TRACK_SIDE_BUY : TRACK_SIDE_SELL;
   TrackSetIntentId(t, p.intent_id);
   t.barrier_s = p.time_barrier_s;
   t.requested = req.price;
   t.sl = req.sl;
   t.tp = req.tp;
   t.latency_ms = DecisionLatencyMs();
   if(t.state == TRACK_STATE_OPEN)
   {
      t.equity_open = AccountInfoDouble(ACCOUNT_EQUITY);
      t.entry_spread = (double)q.spread_points;
      t.entry_slippage = MathAbs(r.slippage_points);
   }
   TrackAdd(t);
}

void FinishReport(ExecReport &r, const EntryQuote &q, const string detail)
{
   r.latency_ms = ElapsedMs(q.received_ms);
   if(detail != "")
      PrintFormat("V6 intent %s: %s", r.intent_id, SanitizeAscii(detail, BROKER_TEXT_CHARS));
   QueueExecutionReport(r);
}

// A market fill beyond max_drift_points from ref_price exceeds the risk budget the
// adapter sized the order to (market-execution symbols ignore the deviation).
string FillDriftBreach(const PollReply &p, const ExecReport &r, const double point)
{
   if(IsLimitOrder(p.order_type) || r.fill_price <= 0.0)
      return "";
   long drift = DriftPoints(p, r.fill_price, point);
   if(drift <= p.max_drift_points)
      return "";
   return StringFormat("RISK BREACH: filled %I64d points from ref_price, limit %I64d",
                       drift, p.max_drift_points);
}

void AcceptEntry(const PollReply &p, const EntryQuote &q, const MqlTradeRequest &req,
                 const MqlTradeResult &res, ExecReport &r)
{
   bool limit = IsLimitOrder(p.order_type);
   ulong key = EntryKey(res);
   r.ticket = (long)key;
   r.reason_code = REASON_NONE;
   r.status = limit ? STATUS_PLACED : STATUS_FILLED;
   if(!limit)
   {
      r.fill_price = MarketFillPrice(res, key);
      r.slippage_points = AdverseSlippagePoints(p.side == INTENT_SIDE_BUY, req.price, r.fill_price, q.point);
   }
   string breach = FillDriftBreach(p, r, q.point);
   if(breach != "")
      PrintFormat("V6 intent %s: %s", r.intent_id, breach);
   string detail = "";
   if(key == 0)
   {
      r.status = STATUS_FAILED;
      r.reason_code = REASON_BROKER_ERROR;
      detail = "the broker accepted the order without a ticket; it will be adopted by its comment";
   }
   else
      TrackEntry(p, q, req, r, key);
   g_trade_event = true;
   FinishReport(r, q, detail);
}

// Contract §8.1 checks 16 and 17.
void SendEntry(const PollReply &p, const EntryQuote &q)
{
   MqlTradeRequest req;
   BuildEntryRequest(p, q, req);
   ExecReport r;
   ReportStart(r, p.intent_id, STATUS_REJECTED_LOCAL, REASON_ORDER_CHECK);
   r.requested_price = req.price;
   r.spread_points = q.spread_points;
   MqlTradeCheckResult check;
   ZeroMemory(check);
   if(!CheckWithFilling(req, check, !IsLimitOrder(p.order_type)))
   {
      r.retcode = (long)check.retcode;
      FinishReport(r, q, "OrderCheck refused: " + check.comment);
      return;
   }
   if(!AccountIsDemo())   // compiled last-moment guard; no input can bypass it
   {
      r.reason_code = REASON_DEMO_REQUIRED;
      FinishReport(r, q, "the account is not DEMO");
      return;
   }
   MqlTradeResult res;
   bool sent = SendEntryRequest(req, res);
   r.retcode = (long)res.retcode;
   if(!sent)
   {
      r.status = STATUS_FAILED;
      r.reason_code = REASON_BROKER_ERROR;
      FinishReport(r, q, "the broker refused the order: " + res.comment);
      return;
   }
   AcceptEntry(p, q, req, res, r);
}

void QueueRefusal(const string intent_id, const string reason, const EntryQuote &q)
{
   string status = (reason == REASON_EXECUTE_DISABLED) ? STATUS_DRY_RUN : STATUS_REJECTED_LOCAL;
   ExecReport r;
   ReportStart(r, intent_id, status, reason);
   if(q.valid)
      r.spread_points = q.spread_points;
   FinishReport(r, q, "");
}

// One signed intent from a poll reply that arrived at `received_ms`.
void ExecuteIntent(const PollReply &p, const ulong received_ms)
{
   if(!IsIntentId(p.intent_id))
   {
      Print("V6: a signed intent with a malformed id was ignored");
      return;
   }
   if(IntentSeen(p.intent_id))
      return;
   EntryQuote q;
   ReadEntryQuote(q, received_ms);
   if(!MarkIntentSeen(p.intent_id))
   {
      Print("V6: the processed-intent set could not be stored; the intent is refused");
      QueueRefusal(p.intent_id, REASON_HALTED, q);
      return;
   }
   string reason = EntryRefusal(p, q);
   if(reason != "")
   {
      QueueRefusal(p.intent_id, reason, q);
      return;
   }
   SendEntry(p, q);
}

#endif // QLIPV6_EXECUTE_MQH
