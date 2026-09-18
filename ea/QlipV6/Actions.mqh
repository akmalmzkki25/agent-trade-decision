//+------------------------------------------------------------------+
//| QlipV6/Actions.mqh                                               |
//| Management commands from a signed poll reply (spec section 3.3): |
//| CLOSE_POSITION, MODIFY_POSITION and MODIFY_PENDING. Every rule   |
//| is checked again against the live quote; each action id is       |
//| applied once and reported to /v6/action through the outbox.      |
//| A close only marks the record: Manage.mqh closes it like any     |
//| other exit, retrying until the broker confirms.                  |
//+------------------------------------------------------------------+
#ifndef QLIPV6_ACTIONS_MQH
#define QLIPV6_ACTIONS_MQH

#include "Config.mqh"
#include "Intent.mqh"
#include "Orders.mqh"
#include "Sync.mqh"
#include "Track.mqh"
#include "Report.mqh"
#include "Manage.mqh"
#include "Plan.mqh"
#include "Schedule.mqh"

#define ACTION_MEMORY         32
#define ACTION_PRICE_EPSILON  1e-9

string g_action_seen[ACTION_MEMORY];
int    g_action_next = 0;

bool ActionSeen(const string id)
{
   for(int i = 0; i < ACTION_MEMORY; i++)
   {
      if(g_action_seen[i] == id)
         return true;
   }
   return false;
}

void RememberAction(const string id)
{
   g_action_seen[g_action_next] = id;
   g_action_next = (g_action_next + 1) % ACTION_MEMORY;
}

// The record of `ticket`: an order ticket, or a position ticket whose identifier differs.
int ActionTrackIndex(const ulong ticket)
{
   int i = TrackFind(ticket);
   if(i >= 0 || !PositionSelectByTicket(ticket))
      return i;
   return TrackFind((ulong)PositionGetInteger(POSITION_IDENTIFIER));
}

// Demo, fresh, no halt, and a tracked record in the expected state.
string ActionGuard(const PollReply &p, const int state, int &index)
{
   index = -1;
   if(!AccountIsDemo())
      return ACT_REASON_DEMO_REQUIRED;
   long age = (long)TimeGMT() - p.action_issued_epoch;
   if(age > ACTION_MAX_AGE_S || age < -ACTION_MAX_AGE_S)
      return ACT_REASON_STALE;
   if(LocalHaltActive())
      return ACT_REASON_HALTED;
   index = ActionTrackIndex((ulong)p.action_ticket);
   if(index < 0 || g_track[index].state != state)
      return ACT_REASON_UNKNOWN_TICKET;
   return "";
}

string ApplyClose(const PollReply &p)
{
   int i = -1;
   string refusal = ActionGuard(p, TRACK_STATE_OPEN, i);
   if(refusal != "")
      return refusal;
   if(g_track[i].close_reason == CLOSE_BY_NONE)
      TrackMarkClose(i, CLOSE_BY_AGENT);
   ExecuteMarkedExits();
   return "";
}

bool PriceMoved(const double wanted, const double current)
{
   return MathAbs(NormalizePrice(wanted) - current) > ACTION_PRICE_EPSILON;
}

// A stop only toward safety and outside the modify distance; a new target beyond it.
string PositionLevelsRefusal(const PollReply &p, const bool buy, const double current_sl,
                             const double current_tp)
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double d = ModifyDistance();
   double sl = NormalizePrice(p.action_sl);
   double tp = NormalizePrice(p.action_tp);
   if(PriceMoved(sl, current_sl) && !SaferStop(buy, sl, current_sl))
      return ACT_REASON_SL_WIDER;
   if(PriceMoved(sl, current_sl) && !OutsideModifyDistance(buy, sl, bid, ask))
      return ACT_REASON_TOO_CLOSE;
   if(PriceMoved(tp, current_tp) && (buy ? tp < bid + d : tp > ask - d))
      return ACT_REASON_TOO_CLOSE;
   return "";
}

// The ladder steps not yet taken follow the action; taken steps are history.
void StorePositionPlan(const int i, const PollReply &p)
{
   g_track[i].sl = NormalizePrice(p.action_sl);
   g_track[i].tp = NormalizePrice(p.action_tp);
   g_track[i].barrier_s = p.action_barrier_s;
   if(g_track[i].plan_step < 1)
   {
      g_track[i].tp1 = NormalizePrice(p.action_tp1);
      g_track[i].step_sl1 = NormalizePrice(p.action_sl1);
   }
   if(g_track[i].plan_step < 2)
   {
      g_track[i].tp2 = NormalizePrice(p.action_tp2);
      g_track[i].step_sl2 = NormalizePrice(p.action_sl2);
   }
   TrackSave(g_track[i]);
   GlobalVariablesFlush();
}

string ApplyModifyPosition(const PollReply &p, uint &retcode, double &old_sl)
{
   int i = -1;
   string refusal = ActionGuard(p, TRACK_STATE_OPEN, i);
   if(refusal != "" || !SelectPositionById(g_track[i].key))
      return (refusal != "") ? refusal : ACT_REASON_UNKNOWN_TICKET;
   bool buy = g_track[i].side == TRACK_SIDE_BUY;
   old_sl = PositionGetDouble(POSITION_SL);
   double current_tp = PositionGetDouble(POSITION_TP);
   refusal = PositionLevelsRefusal(p, buy, old_sl, current_tp);
   long age = (long)TimeTradeServer() - (long)PositionGetInteger(POSITION_TIME);
   if(refusal == "" && (p.action_barrier_s > MAX_TIME_BARRIER_S || p.action_barrier_s <= age))
      refusal = ACT_REASON_BARRIER;
   if(refusal != "")
      return refusal;
   bool stops_move = PriceMoved(p.action_sl, old_sl) || PriceMoved(p.action_tp, current_tp);
   ulong ticket = (ulong)PositionGetInteger(POSITION_TICKET);
   if(stops_move && !ModifyPositionStops(ticket, p.action_sl, p.action_tp, retcode))
      return ACT_REASON_BROKER_ERROR;
   StorePositionPlan(i, p);
   return "";
}

// Expects the order to be selected: the levels on their sides, the price still on its
// side of the quote, the expiry ahead, and the loss at the stop within InpMaxRiskUsd.
string PendingLevelsRefusal(const PollReply &p, const ENUM_ORDER_TYPE type)
{
   bool buy = type == ORDER_TYPE_BUY_LIMIT || type == ORDER_TYPE_BUY_STOP;
   double sign = buy ? 1.0 : -1.0;
   if(sign * (p.action_price - p.action_sl) <= 0.0 || sign * (p.action_tp - p.action_price) <= 0.0
      || p.action_expiry_epoch <= (long)TimeGMT())
      return ACT_REASON_BAD_ACTION;
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double stops = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * point;
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   bool ok = (type == ORDER_TYPE_BUY_LIMIT) ? p.action_price < ask - stops
             : (type == ORDER_TYPE_SELL_LIMIT) ? p.action_price > bid + stops
             : (type == ORDER_TYPE_BUY_STOP) ? p.action_price > ask + stops
             : p.action_price < bid - stops;
   if(!ok)
      return ACT_REASON_TOO_CLOSE;
   double pnl = 0.0;
   ENUM_ORDER_TYPE side = buy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double volume = OrderGetDouble(ORDER_VOLUME_CURRENT);
   if(!OrderCalcProfit(side, _Symbol, volume, p.action_price, p.action_sl, pnl)
      || -pnl > g_cfg.max_risk_usd)
      return ACT_REASON_BAD_ACTION;
   return "";
}

void StorePendingPlan(const int i, const PollReply &p)
{
   g_track[i].requested = NormalizePrice(p.action_price);
   g_track[i].sl = NormalizePrice(p.action_sl);
   g_track[i].tp = NormalizePrice(p.action_tp);
   g_track[i].barrier_s = p.action_barrier_s;
   g_track[i].tp1 = NormalizePrice(p.action_tp1);
   g_track[i].tp2 = NormalizePrice(p.action_tp2);
   g_track[i].step_sl1 = NormalizePrice(p.action_sl1);
   g_track[i].step_sl2 = NormalizePrice(p.action_sl2);
   TrackSave(g_track[i]);
   GlobalVariablesFlush();
}

string ApplyModifyPending(const PollReply &p, uint &retcode, double &old_sl)
{
   int i = -1;
   string refusal = ActionGuard(p, TRACK_STATE_PENDING, i);
   if(refusal != "" || !OrderSelect(g_track[i].key))
      return (refusal != "") ? refusal : ACT_REASON_UNKNOWN_TICKET;
   old_sl = OrderGetDouble(ORDER_SL);
   refusal = PendingLevelsRefusal(p, (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE));
   if(refusal == "" && (p.action_barrier_s <= 0 || p.action_barrier_s > MAX_TIME_BARRIER_S))
      refusal = ACT_REASON_BARRIER;
   if(refusal != "")
      return refusal;
   datetime expiry = (datetime)(p.action_expiry_epoch + ServerGmtOffsetSeconds());
   if(!ModifyPendingOrder(g_track[i].key, p.action_price, p.action_sl, p.action_tp, expiry,
                          retcode))
      return ACT_REASON_BROKER_ERROR;
   StorePendingPlan(i, p);
   return "";
}

// One signed management command; each action id is acted on and reported once.
void ApplyActionCommand(const PollReply &p)
{
   if(!IsActionCommand(p.command) || ActionSeen(p.action_id))
      return;
   RememberAction(p.action_id);
   AdoptUntracked();
   uint retcode = 0;
   double old_sl = 0.0;
   string refusal = ACT_REASON_BAD_ACTION;
   if(p.command == CMD_CLOSE_POSITION)
      refusal = ApplyClose(p);
   else if(p.command == CMD_MODIFY_POSITION)
      refusal = ApplyModifyPosition(p, retcode, old_sl);
   else
      refusal = ApplyModifyPending(p, retcode, old_sl);
   if(refusal == ACT_REASON_BROKER_ERROR && RetcodeIs(retcode, TRADE_RETCODE_MARKET_CLOSED))
      refusal = ACT_REASON_MARKET_CLOSED;
   string kind = (refusal == "") ? ACTION_KIND_APPLIED
                 : (refusal == ACT_REASON_BROKER_ERROR) ? ACTION_KIND_FAILED
                 : ACTION_KIND_REJECTED;
   QueueActionReport(kind, p, (refusal == "") ? ACT_REASON_NONE : refusal, retcode, old_sl);
   PrintFormat("V6 action %s %s ticket %I64d: %s %s", p.action_id, p.command,
               p.action_ticket, kind, refusal);
}

#endif // QLIPV6_ACTIONS_MQH
