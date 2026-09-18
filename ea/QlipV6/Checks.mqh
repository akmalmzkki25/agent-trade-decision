//+------------------------------------------------------------------+
//| QlipV6/Checks.mqh                                                |
//| The symbol and market facts an intent is judged against before   |
//| it reaches OrderCheck: tick grid (the SL+ ladder included),      |
//| volume step, occupancy, price drift, a pending price still on    |
//| its side (a passive LIMIT, a STOP beyond the quote), an open     |
//| session with a live quote, and the loss at the stop priced by    |
//| the terminal.                                                    |
//+------------------------------------------------------------------+
#ifndef QLIPV6_CHECKS_MQH
#define QLIPV6_CHECKS_MQH

#include "Config.mqh"
#include "Market.mqh"
#include "Intent.mqh"
#include "Exposure.mqh"
#include "Schedule.mqh"

#define QUOTE_STALE_S    60
#define GRID_EPSILON     1e-6
#define LADDER_LEVELS    4

struct EntryQuote
{
   bool              valid;
   double            bid;
   double            ask;
   double            point;
   long              spread_points;
   datetime          quote_time;       // server time of the last quote
   ulong             received_ms;      // when the poll reply arrived
};

bool ReadEntryQuote(EntryQuote &q, const ulong received_ms)
{
   MqlTick tick;
   ZeroMemory(tick);
   ZeroMemory(q);
   q.received_ms = received_ms;
   q.point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   q.valid = SymbolInfoTick(_Symbol, tick) && tick.bid > 0.0 && tick.ask >= tick.bid && q.point > 0.0;
   if(!q.valid)
      return false;
   q.bid = tick.bid;
   q.ask = tick.ask;
   q.quote_time = tick.time;
   q.spread_points = (long)MathRound((tick.ask - tick.bid) / q.point);
   return true;
}

//--- symbol rules -----------------------------------------------------

bool OnPriceGrid(const double price)
{
   double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tick_size <= 0.0 || price <= 0.0)
      return false;
   double steps = price / tick_size;
   return MathAbs(steps - MathRound(steps)) < GRID_EPSILON;
}

bool LotsOnStep(const double lots)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double min_lots = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_lots = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step <= 0.0 || lots < min_lots - GRID_EPSILON || lots > max_lots + GRID_EPSILON)
      return false;
   double steps = lots / step;
   return MathAbs(steps - MathRound(steps)) < GRID_EPSILON;
}

// Every ladder level the intent sets (0 = none) sits on the tick grid.
bool LadderOnGrid(const PollReply &p)
{
   double levels[LADDER_LEVELS];
   levels[0] = p.tp1;
   levels[1] = p.tp2;
   levels[2] = p.sl_after_tp1;
   levels[3] = p.sl_after_tp2;
   for(int i = 0; i < LADDER_LEVELS; i++)
   {
      if(levels[i] > 0.0 && !OnPriceGrid(levels[i]))
         return false;
   }
   return true;
}

string SymbolShapeProblem(const PollReply &p)
{
   if(p.magic != g_cfg.magic)
      return "magic does not match InpMagic";
   if(!OnPriceGrid(p.entry) || !OnPriceGrid(p.tp) || !OnPriceGrid(p.ref_price))
      return "a price is off the tick grid";
   if(!LadderOnGrid(p))
      return "a ladder level is off the tick grid";
   if(!LotsOnStep(p.lots))
      return "lots are off the volume step or outside the symbol limits";
   return "";
}

// Contract §8.1 check 5 (the stop loss is judged after NO_SL).
bool IntentShapeOk(const PollReply &p)
{
   string problem = IntentFieldsProblem(p);
   if(problem == "")
      problem = SymbolShapeProblem(p);
   if(problem != "")
      PrintFormat("V6 intent %s is malformed: %s", p.intent_id, problem);
   return problem == "";
}

// Contract §8.1 check 10: one V6 position or pending order at a time.
bool V6Occupied(void)
{
   int positions = 0, orders = 0;
   double floating = 0.0;
   V6Exposure(g_cfg.magic, positions, orders, floating);
   return positions > 0 || orders > 0;
}

//--- market rules -----------------------------------------------------

// |price - ref_price| in points.
long DriftPoints(const PollReply &p, const double price, const double point)
{
   long drift = PriceToPoints(price, point) - PriceToPoints(p.ref_price, point);
   return (drift < 0) ? -drift : drift;
}

long QuoteDriftPoints(const PollReply &p, const EntryQuote &q)
{
   return DriftPoints(p, (p.side == INTENT_SIDE_BUY) ? q.ask : q.bid, q.point);
}

// The slippage a market order may still take: the adapter sized the order so that
// a fill up to max_drift_points from ref_price fits the risk budget, and the quote
// has already used part of that.
long MarketDeviationPoints(const PollReply &p, const EntryQuote &q)
{
   return p.max_drift_points - QuoteDriftPoints(p, q);
}

// A market order needs some deviation left; a pending order only the drift limit.
bool WithinDrift(const PollReply &p, const EntryQuote &q)
{
   if(IsPendingOrder(p.order_type))
      return QuoteDriftPoints(p, q) <= p.max_drift_points;
   return MarketDeviationPoints(p, q) > 0;
}

// A limit price still rests on the passive side; a stop price still sits beyond the
// quote, both at least the broker's stops level away.
bool PendingStillValid(const PollReply &p, const EntryQuote &q)
{
   if(!IsPendingOrder(p.order_type))
      return true;
   long stops = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   long entry = PriceToPoints(p.entry, q.point);
   long ask = PriceToPoints(q.ask, q.point);
   long bid = PriceToPoints(q.bid, q.point);
   if(p.order_type == INTENT_BUY_LIMIT)
      return entry < ask - stops;
   if(p.order_type == INTENT_SELL_LIMIT)
      return entry > bid + stops;
   if(p.order_type == INTENT_BUY_STOP)
      return entry > ask + stops;
   return entry < bid - stops;
}

// Contract §8.1 check 14, plus a broker connection, a live quote and the
// pre-rollover window.
bool MarketOpenForEntry(const EntryQuote &q)
{
   if(TerminalInfoInteger(TERMINAL_CONNECTED) == 0
      || SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE) != SYMBOL_TRADE_MODE_FULL)
      return false;
   datetime now_server = TimeTradeServer();
   if(InFlattenWindow(now_server) || (long)now_server - (long)q.quote_time > QUOTE_STALE_S)
      return false;
   return InTradeSession(now_server);
}

// Contract §8.1 check 15: the loss at the stop, priced by OrderCalcProfit
// because this server reports a tick value 10x too small.
bool RiskWithinCap(const PollReply &p, const EntryQuote &q)
{
   bool is_buy = p.side == INTENT_SIDE_BUY;
   double entry = IsPendingOrder(p.order_type) ? p.entry : (is_buy ? q.ask : q.bid);
   double pnl = 0.0;
   ENUM_ORDER_TYPE type = is_buy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   ResetLastError();
   if(!OrderCalcProfit(type, _Symbol, p.lots, entry, p.sl, pnl) || !MathIsValidNumber(pnl))
   {
      PrintFormat("V6 intent %s: OrderCalcProfit failed (err=%d)", p.intent_id, GetLastError());
      return false;
   }
   return -pnl <= g_cfg.max_risk_usd;
}

#endif // QLIPV6_CHECKS_MQH
