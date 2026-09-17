//+------------------------------------------------------------------+
//| QlipV6/Breaker.mqh                                               |
//| Local daily breaker (plan §6, contract §8.2): when account       |
//| equity falls InpDailyBreakerPct below its level at the start of  |
//| the trade-server day, every V6 position is flattened and no new  |
//| entry is taken until the next server day. The server day begins |
//| at 00:00 server time, inside the daily quote gap, so the reset   |
//| never lands in a trading session. The trip survives restarts.    |
//+------------------------------------------------------------------+
#ifndef QLIPV6_BREAKER_MQH
#define QLIPV6_BREAKER_MQH

#include "Persist.mqh"

#define BREAKER_DAY_NAME     "BRKDAY"
#define BREAKER_EQUITY_NAME  "BRKEQ"
#define BREAKER_TRIP_NAME    "BRKTRIP"
#define PERCENT_SCALE        100.0
#define BREAKER_NO_DAY       (-1)

// Uncomment for drill 5 of plan §12 only (a test build): the input shifts the
// equity the breaker sees, so a -3 % day can be staged on a demo account.
// #define QLIPV6_BREAKER_DRILL
#ifdef QLIPV6_BREAKER_DRILL
input double InpDrillEquityOffset = 0.0;  // DRILL BUILD: added to equity for the breaker
#endif

struct BreakerState
{
   long              day;           // server day number the baseline belongs to
   double            day_equity;    // equity at the first sighting of that day
   bool              tripped;
};

BreakerState g_breaker;

long ServerDayNumber(void)
{
   return (long)TimeTradeServer() / SECONDS_PER_DAY;
}

double BreakerEquity(void)
{
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
#ifdef QLIPV6_BREAKER_DRILL
   equity += InpDrillEquityOffset;
#endif
   return equity;
}

void BreakerLoad(void)
{
   g_breaker.day = (long)PersistGet(PersistKey(BREAKER_DAY_NAME), (double)BREAKER_NO_DAY);
   g_breaker.day_equity = PersistGet(PersistKey(BREAKER_EQUITY_NAME), 0.0);
   long trip_day = (long)PersistGet(PersistKey(BREAKER_TRIP_NAME), (double)BREAKER_NO_DAY);
   g_breaker.tripped = g_breaker.day != BREAKER_NO_DAY && trip_day == g_breaker.day;
}

// A new server day starts from the first known equity; a trip ends with it.
bool BreakerStartDay(const long today, const double equity)
{
   if(equity <= 0.0)
      return false;
   g_breaker.day = today;
   g_breaker.day_equity = equity;
   g_breaker.tripped = false;
   PersistSet(PersistKey(BREAKER_DAY_NAME), (double)today);
   PersistSet(PersistKey(BREAKER_EQUITY_NAME), equity);
   return true;
}

// Returns true on the tick the breaker trips. Reaching the limit exactly trips.
bool BreakerUpdate(const double pct)
{
   long today = ServerDayNumber();
   double equity = BreakerEquity();
   if(g_breaker.day != today && !BreakerStartDay(today, equity))
      return false;
   if(g_breaker.tripped || g_breaker.day_equity <= 0.0)
      return false;
   double limit = g_breaker.day_equity * (1.0 - pct / PERCENT_SCALE);
   if(equity > limit)
      return false;
   g_breaker.tripped = true;
   PersistSet(PersistKey(BREAKER_TRIP_NAME), (double)today);
   GlobalVariablesFlush();
   PrintFormat("V6 local breaker TRIPPED: equity %.2f <= %.2f (%.1f%% below the server-day start %.2f); "
               "flattening V6 and refusing entries until the next server day",
               equity, limit, pct, g_breaker.day_equity);
   return true;
}

#endif // QLIPV6_BREAKER_MQH
