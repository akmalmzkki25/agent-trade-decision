//+------------------------------------------------------------------+
//| QlipV6/Plan.mqh                                                  |
//| The SL+ ladder of every V6 position, run locally on every tick   |
//| and timer beat (spec section 3.4). When the bid (buy) or the ask |
//| (sell) reaches TP1 or TP2, the stop moves to that step's level:  |
//| only toward safety and never inside the modify distance. The     |
//| step is saved before it is reported, so a restart resumes it.    |
//| A step whose stop cannot move yet is retried, not skipped.       |
//+------------------------------------------------------------------+
#ifndef QLIPV6_PLAN_MQH
#define QLIPV6_PLAN_MQH

#include "Config.mqh"
#include "Orders.mqh"
#include "Sync.mqh"
#include "Track.mqh"
#include "Report.mqh"

#define PLAN_RETRY_MS         1000
#define PLAN_BUFFER_PRICE     0.10     // risk/limits.MODIFY_BUFFER_PRICE

// The adapter's d_min: max(stops, freeze) x point + spread + buffer.
double ModifyDistance(void)
{
   long stops = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   long freeze = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double spread = SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID);
   return (double)MathMax(stops, freeze) * point + MathMax(spread, 0.0) + PLAN_BUFFER_PRICE;
}

// The furthest step the market has reached: 2 past TP2, 1 past TP1, else 0.
int PlanStepReached(const TrackRecord &r, const double bid, const double ask)
{
   bool buy = r.side == TRACK_SIDE_BUY;
   double price = buy ? bid : ask;
   double sign = buy ? 1.0 : -1.0;
   if(r.tp2 > 0.0 && sign * (price - r.tp2) >= 0.0)
      return 2;
   if(r.tp1 > 0.0 && sign * (price - r.tp1) >= 0.0)
      return 1;
   return 0;
}

// The stop a step asks for (a step without its own stop keeps the earlier one).
double PlanStepSl(const TrackRecord &r, const int step)
{
   if(step >= 2 && r.step_sl2 > 0.0)
      return r.step_sl2;
   if(step >= 1 && r.step_sl1 > 0.0)
      return r.step_sl1;
   return 0.0;
}

bool SaferStop(const bool buy, const double target, const double current)
{
   if(buy)
      return target > current;
   return current <= 0.0 || target < current;
}

bool OutsideModifyDistance(const bool buy, const double target, const double bid,
                           const double ask)
{
   double d = ModifyDistance();
   return buy ? target <= bid - d : target >= ask + d;
}

void PlanMarkStep(const int i, const int step, const double old_sl, const double new_sl,
                  const double price)
{
   g_track[i].plan_step = step;
   g_track[i].sl = new_sl;
   TrackSave(g_track[i]);
   GlobalVariablesFlush();
   QueuePlanStepReport(TrackIntentId(g_track[i]), (long)g_track[i].key, step, old_sl, new_sl,
                       price);
   PrintFormat("V6 plan %I64u: step %d, stop %s -> %s", g_track[i].key, step,
               DoubleToString(old_sl, _Digits), DoubleToString(new_sl, _Digits));
}

void PlanStepFor(const int i, const double bid, const double ask, const ulong now_ms)
{
   if(g_track[i].state != TRACK_STATE_OPEN || now_ms < g_track[i].plan_next_ms)
      return;
   int reached = PlanStepReached(g_track[i], bid, ask);
   if(reached <= g_track[i].plan_step || !SelectPositionById(g_track[i].key))
      return;
   bool buy = g_track[i].side == TRACK_SIDE_BUY;
   double current = PositionGetDouble(POSITION_SL);
   double target = NormalizePrice(PlanStepSl(g_track[i], reached));
   double price = buy ? bid : ask;
   if(target <= 0.0 || !SaferStop(buy, target, current))
   {
      PlanMarkStep(i, reached, current, current, price);   // nothing to move
      return;
   }
   uint retcode = 0;
   ulong ticket = (ulong)PositionGetInteger(POSITION_TICKET);
   if(!OutsideModifyDistance(buy, target, bid, ask)
      || !ModifyPositionStops(ticket, target, PositionGetDouble(POSITION_TP), retcode))
   {
      g_track[i].plan_next_ms = now_ms + PLAN_RETRY_MS;
      return;
   }
   PlanMarkStep(i, reached, current, target, price);
}

// On every tick and timer beat; a halt or AutoTrading off leaves stops as they are.
void PlanTick(void)
{
   if(!TradingPermitted() || LocalHaltActive())
      return;
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   if(bid <= 0.0 || ask < bid)
      return;
   ulong now_ms = GetTickCount64();
   for(int i = 0; i < g_track_count; i++)
      PlanStepFor(i, bid, ask, now_ms);
}

#endif // QLIPV6_PLAN_MQH
