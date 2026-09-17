//+------------------------------------------------------------------+
//| QlipV6/Sync.mqh                                                  |
//| Reconciles the track records with the broker, without any        |
//| network call: V6 orders and positions without a record are       |
//| adopted, a pending order that filled is reported as filled, one  |
//| that expired or was cancelled is reported and dropped, and a     |
//| closed position becomes a basket result.                         |
//+------------------------------------------------------------------+
#ifndef QLIPV6_SYNC_MQH
#define QLIPV6_SYNC_MQH

#include "Config.mqh"
#include "Exposure.mqh"
#include "Track.mqh"
#include "Report.mqh"
#include "Basket.mqh"

#define HISTORY_WAIT_S   3600

// Selects the V6 position with this identifier.
bool SelectPositionById(const ulong id)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(PositionGetTicket(i) > 0 && (ulong)PositionGetInteger(POSITION_IDENTIFIER) == id)
         return IsV6Position(g_cfg.magic);
   }
   return false;
}

//--- records for what V6 owns ----------------------------------------

// Expects the position to be selected.
void AdoptPosition(const ulong id)
{
   TrackRecord r;
   ZeroMemory(r);
   r.key = id;
   r.state = TRACK_STATE_OPEN;
   r.side = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? TRACK_SIDE_BUY : TRACK_SIDE_SELL;
   TrackSetIntentId(r, IntentIdFromComment(PositionGetString(POSITION_COMMENT)));
   r.barrier_s = DEFAULT_TIME_BARRIER_S;
   r.requested = PositionGetDouble(POSITION_PRICE_OPEN);
   r.sl = PositionGetDouble(POSITION_SL);
   r.tp = PositionGetDouble(POSITION_TP);
   r.equity_open = AccountInfoDouble(ACCOUNT_EQUITY);
   if(TrackAdd(r))
      PrintFormat("V6 sync: adopted position %I64u (intent '%s') with the default %d s barrier",
                  id, TrackIntentId(r), DEFAULT_TIME_BARRIER_S);
}

// Expects the order to be selected.
void AdoptOrder(const ulong ticket)
{
   TrackRecord r;
   ZeroMemory(r);
   r.key = ticket;
   r.state = TRACK_STATE_PENDING;
   ENUM_ORDER_TYPE type = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
   r.side = (type == ORDER_TYPE_BUY_LIMIT || type == ORDER_TYPE_BUY_STOP) ? TRACK_SIDE_BUY : TRACK_SIDE_SELL;
   TrackSetIntentId(r, IntentIdFromComment(OrderGetString(ORDER_COMMENT)));
   r.barrier_s = DEFAULT_TIME_BARRIER_S;
   r.requested = OrderGetDouble(ORDER_PRICE_OPEN);
   r.sl = OrderGetDouble(ORDER_SL);
   r.tp = OrderGetDouble(ORDER_TP);
   if(TrackAdd(r))
      PrintFormat("V6 sync: adopted pending order %I64u (intent '%s')", ticket, TrackIntentId(r));
}

// Every V6 position and pending order gets a record: a stored one if it
// exists, otherwise defaults (intent id from the comment, default barrier).
void AdoptUntracked(void)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(PositionGetTicket(i) == 0 || !IsV6Position(g_cfg.magic))
         continue;
      ulong id = (ulong)PositionGetInteger(POSITION_IDENTIFIER);
      if(TrackFind(id) < 0 && !TrackRestore(id))
         AdoptPosition(id);
   }
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0 || !IsV6Order(g_cfg.magic) || TrackFind(ticket) >= 0)
         continue;
      if(!TrackRestore(ticket))
         AdoptOrder(ticket);
   }
}

//--- outcomes ------------------------------------------------------------

void QueueOutcome(const int i, const string status, const string reason, const double fill_price,
                  const double slippage)
{
   string intent_id = TrackIntentId(g_track[i]);
   if(intent_id == "")
      return;
   ExecReport r;
   ReportStart(r, intent_id, status, reason);
   r.ticket = (long)g_track[i].key;
   r.requested_price = g_track[i].requested;
   r.fill_price = fill_price;
   r.slippage_points = slippage;
   QueueExecutionReport(r);
}

void OnPendingFilled(const int i, const double fill_price)
{
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double slippage = AdverseSlippagePoints(g_track[i].side == TRACK_SIDE_BUY, g_track[i].requested,
                                           fill_price, point);
   g_track[i].state = TRACK_STATE_OPEN;
   g_track[i].equity_open = AccountInfoDouble(ACCOUNT_EQUITY);
   g_track[i].entry_spread = (double)CurrentSpreadPoints();
   g_track[i].entry_slippage = MathAbs(slippage);
   g_track[i].missing_since = 0;
   TrackSave(g_track[i]);
   GlobalVariablesFlush();
   QueueOutcome(i, STATUS_FILLED, REASON_NONE, fill_price, slippage);
}

string CancelReasonCode(const int reason)
{
   switch(reason)
   {
      case CANCEL_BY_COMMAND:  return REASON_COMMAND;
      case CANCEL_BY_HALT:     return REASON_HALTED;
      case CANCEL_BY_BREAKER:  return REASON_BREAKER;
      case CANCEL_BY_ROLLOVER: return REASON_MARKET_CLOSED;
      default:                 return REASON_NONE;
   }
}

// Keeps a record whose order or position is not visible yet, for a while.
void WaitForHistory(const int i)
{
   long now = (long)TimeGMT();
   if(g_track[i].missing_since == 0)
   {
      g_track[i].missing_since = now;
      return;
   }
   if(now - g_track[i].missing_since < HISTORY_WAIT_S)
      return;
   PrintFormat("V6 sync: ticket %I64u has no order, position or history after %d s; record dropped",
               g_track[i].key, HISTORY_WAIT_S);
   TrackRemove(i);
}

void ResolveGonePending(const int i, const ENUM_ORDER_STATE state)
{
   if(state == ORDER_STATE_FILLED || state == ORDER_STATE_PARTIAL)
   {
      OnPendingFilled(i, EntryDealPrice(g_track[i].key));
      return;
   }
   if(state == ORDER_STATE_EXPIRED)
      QueueOutcome(i, STATUS_EXPIRED, REASON_EXPIRED, 0.0, 0.0);
   else if(state == ORDER_STATE_CANCELED)
      QueueOutcome(i, STATUS_CANCELLED, CancelReasonCode(g_track[i].cancel_reason), 0.0, 0.0);
   else if(state == ORDER_STATE_REJECTED)
      QueueOutcome(i, STATUS_FAILED, REASON_BROKER_ERROR, 0.0, 0.0);
   else
   {
      WaitForHistory(i);   // the server is still processing the order
      return;
   }
   TrackRemove(i);
}

void SyncPending(const int i)
{
   ulong key = g_track[i].key;
   if(OrderSelect(key))
   {
      g_track[i].missing_since = 0;
      return;
   }
   if(SelectPositionById(key))
   {
      OnPendingFilled(i, PositionGetDouble(POSITION_PRICE_OPEN));
      return;
   }
   if(!HistoryOrderSelect(key))
   {
      WaitForHistory(i);
      return;
   }
   ResolveGonePending(i, (ENUM_ORDER_STATE)HistoryOrderGetInteger(key, ORDER_STATE));
}

void SyncOpen(const int i)
{
   if(SelectPositionById(g_track[i].key))
   {
      g_track[i].missing_since = 0;
      return;
   }
   PositionHistory h;
   if(!ReadPositionHistory(g_track[i].key, h) || !h.found_exit)
   {
      WaitForHistory(i);
      return;
   }
   QueueBasketResult(g_track[i], h);
   TrackRemove(i);
}

// Backwards, because a finished record is removed in place.
void SyncRecords(void)
{
   for(int i = g_track_count - 1; i >= 0; i--)
   {
      if(g_track[i].state == TRACK_STATE_PENDING)
         SyncPending(i);
      else
         SyncOpen(i);
   }
}

#endif // QLIPV6_SYNC_MQH
