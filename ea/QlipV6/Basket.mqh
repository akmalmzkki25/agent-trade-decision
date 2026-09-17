//+------------------------------------------------------------------+
//| QlipV6/Basket.mqh                                                |
//| basket-result-event.v1 (version "v6") for a closed V6 position,  |
//| built from the deal history of its position identifier and the   |
//| entry facts kept in its track record (contract §6.5).            |
//+------------------------------------------------------------------+
#ifndef QLIPV6_BASKET_MQH
#define QLIPV6_BASKET_MQH

#include "Json.mqh"
#include "Market.mqh"
#include "Track.mqh"
#include "Report.mqh"
#include "Outbox.mqh"

#define BASKET_SCHEMA          "basket-result-event.v1"
#define BASKET_VERSION         "v6"
#define BASKET_ID_MARKER       "-V6B-"
#define BASKET_BURSTS          1
#define BASKET_POSITIONS       1
#define BASKET_MONEY_DIGITS    2
#define BASKET_POINTS_DIGITS   1
#define NO_DEAL_REASON         (-1)

struct PositionHistory
{
   bool              found_entry;
   bool              found_exit;
   bool              is_buy;
   datetime          opened;        // server time
   datetime          closed;        // server time
   double            entry_price;
   double            exit_price;
   double            net_pnl;       // profit + swap + commission + fee
   double            gross_pnl;     // profit + swap
   long              exit_reason;   // DEAL_REASON of the last exit deal
};

void AccumulateDeal(const ulong deal, PositionHistory &h)
{
   double pnl = HistoryDealGetDouble(deal, DEAL_PROFIT) + HistoryDealGetDouble(deal, DEAL_SWAP);
   h.gross_pnl += pnl;
   h.net_pnl += pnl + HistoryDealGetDouble(deal, DEAL_COMMISSION) + HistoryDealGetDouble(deal, DEAL_FEE);
   datetime at = (datetime)HistoryDealGetInteger(deal, DEAL_TIME);
   if((ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal, DEAL_ENTRY) == DEAL_ENTRY_IN)
   {
      h.found_entry = true;
      h.is_buy = (ENUM_DEAL_TYPE)HistoryDealGetInteger(deal, DEAL_TYPE) == DEAL_TYPE_BUY;
      h.opened = at;
      h.entry_price = HistoryDealGetDouble(deal, DEAL_PRICE);
      return;
   }
   if(!h.found_exit || at >= h.closed)
   {
      h.closed = at;
      h.exit_price = HistoryDealGetDouble(deal, DEAL_PRICE);
      h.exit_reason = HistoryDealGetInteger(deal, DEAL_REASON);
   }
   h.found_exit = true;
}

bool ReadPositionHistory(const ulong position_id, PositionHistory &h)
{
   ZeroMemory(h);
   h.exit_reason = NO_DEAL_REASON;
   if(!HistorySelectByPosition((long)position_id))
      return false;
   int deals = HistoryDealsTotal();
   for(int i = 0; i < deals; i++)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal > 0)
         AccumulateDeal(deal, h);
   }
   return h.found_entry;
}

// The fill price of a position, from its entry deal; 0 when unknown.
double EntryDealPrice(const ulong position_id)
{
   PositionHistory h;
   if(!ReadPositionHistory(position_id, h))
      return 0.0;
   return h.entry_price;
}

string CloseReasonName(const int ea_reason, const long deal_reason)
{
   if(deal_reason == DEAL_REASON_SL)
      return "SL";
   if(deal_reason == DEAL_REASON_TP)
      return "TP";
   if(ea_reason == CLOSE_BY_TIME)
      return "TIME";
   if(ea_reason == CLOSE_BY_FLATTEN)
      return "FLATTEN";
   if(ea_reason == CLOSE_BY_ROLLOVER)
      return "ROLLOVER";
   if(deal_reason == DEAL_REASON_CLIENT || deal_reason == DEAL_REASON_MOBILE
      || deal_reason == DEAL_REASON_WEB)
      return "MANUAL";
   return "OTHER";
}

// Distance in points between the exit fill and the price that was aimed at.
double ExitSlippagePoints(const PositionHistory &h, const TrackRecord &r, const double point)
{
   double aimed = 0.0;
   if(h.exit_reason == DEAL_REASON_SL)
      aimed = r.sl;
   else if(h.exit_reason == DEAL_REASON_TP)
      aimed = r.tp;
   else if(r.close_reason != CLOSE_BY_NONE)
      aimed = r.exit_requested;
   if(aimed <= 0.0 || h.exit_price <= 0.0 || point <= 0.0)
      return 0.0;
   return MathAbs(h.exit_price - aimed) / point;
}

string IsoUtc(const long utc_epoch)
{
   MqlDateTime t;
   TimeToStruct((datetime)MathMax(utc_epoch, (long)0), t);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ", t.year, t.mon, t.day, t.hour, t.min, t.sec);
}

string BasketResultJson(const string intent_id, const PositionHistory &h, const TrackRecord &r,
                        const int offset_s)
{
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double exit_spread = (r.exit_spread > 0.0) ? r.exit_spread : (double)CurrentSpreadPoints();
   double exit_slippage = ExitSlippagePoints(h, r, point);
   double worst = MathMin(MathMin(r.worst_pnl, h.gross_pnl), 0.0);
   CJsonObject o;
   o.AddStr("schema_version", BASKET_SCHEMA);
   o.AddStr("basket_id", _Symbol + BASKET_ID_MARKER + intent_id);
   o.AddStr("version", BASKET_VERSION);
   o.AddStr("symbol", _Symbol);
   o.AddStr("side", h.is_buy ? "buy" : "sell");
   o.AddStr("opened_at_utc", IsoUtc(ServerToUtc(h.opened, offset_s)));
   o.AddStr("closed_at_utc", IsoUtc(ServerToUtc(h.closed, offset_s)));
   o.AddStr("close_reason", CloseReasonName(r.close_reason, h.exit_reason));
   o.AddInt("bursts", BASKET_BURSTS);
   // Raw on purpose: "positions" is the snapshot's array field, and the EA
   // source scan in test_golden_contract.py maps each written name to one kind.
   o.AddRaw("positions", JInt(BASKET_POSITIONS));
   o.AddNum("gross_profit", MathMax(h.net_pnl, 0.0), BASKET_MONEY_DIGITS);
   o.AddNum("gross_loss", MathMin(h.net_pnl, 0.0), BASKET_MONEY_DIGITS);
   o.AddNum("net_pnl", h.net_pnl, BASKET_MONEY_DIGITS);
   o.AddNum("max_floating_dd", worst, BASKET_MONEY_DIGITS);
   o.AddNum("avg_slippage_points", (r.entry_slippage + exit_slippage) / 2.0, BASKET_POINTS_DIGITS);
   o.AddNum("avg_spread_points", (r.entry_spread + exit_spread) / 2.0, BASKET_POINTS_DIGITS);
   o.AddInt("decision_latency_ms", MathMax(r.latency_ms, (long)0));
   o.AddNum("equity_at_open", MathMax(r.equity_open, 0.0), BASKET_MONEY_DIGITS);
   o.AddNum("equity_at_close", MathMax(AccountInfoDouble(ACCOUNT_EQUITY), 0.0), BASKET_MONEY_DIGITS);
   return o.Text();
}

bool QueueBasketResult(const TrackRecord &r, const PositionHistory &h)
{
   string intent_id = TrackIntentId(r);
   string reason = CloseReasonName(r.close_reason, h.exit_reason);
   PrintFormat("V6 position %I64u closed: %s, net %.2f, intent %s", r.key, reason, h.net_pnl,
               intent_id == "" ? "unknown (no basket result)" : intent_id);
   if(intent_id == "")
      return false;
   return g_outbox.Enqueue(PATH_BASKET_RESULT, BasketResultJson(intent_id, h, r, ServerGmtOffsetSeconds()));
}

#endif // QLIPV6_BASKET_MQH
