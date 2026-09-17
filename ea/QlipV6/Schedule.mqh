//+------------------------------------------------------------------+
//| QlipV6/Schedule.mqh                                              |
//| Server-clock rules: the daily pre-rollover flatten, the time     |
//| barrier, the symbol's trade sessions and the decision latency.   |
//| MetaQuotes-Demo quotes XAUUSD 01:00-23:00 server time; the       |
//| flatten time (default 22:55) sits just before that gap.          |
//+------------------------------------------------------------------+
#ifndef QLIPV6_SCHEDULE_MQH
#define QLIPV6_SCHEDULE_MQH

#include "Config.mqh"
#include "Market.mqh"
#include "Persist.mqh"
#include "Intent.mqh"
#include "Track.mqh"

#define SECONDS_PER_MINUTE   60
#define MAX_TRADE_SESSIONS   10

// The first daily flatten time (server time) after `server_time`.
datetime NextFlattenAfter(const datetime server_time)
{
   long now = (long)server_time;
   long flatten = now - now % SECONDS_PER_DAY + (long)g_cfg.flatten_minute * SECONDS_PER_MINUTE;
   if(now >= flatten)
      flatten += SECONDS_PER_DAY;
   return (datetime)flatten;
}

// From the flatten time to the end of the server day no entry is taken.
bool InFlattenWindow(const datetime server_time)
{
   long minute = ((long)server_time % SECONDS_PER_DAY) / SECONDS_PER_MINUTE;
   return minute >= g_cfg.flatten_minute;
}

// The stored barrier, or the adapter default when a record has none.
long BarrierSeconds(const long stored)
{
   if(stored <= 0)
      return DEFAULT_TIME_BARRIER_S;
   return MathMin(stored, (long)MAX_TIME_BARRIER_S);
}

// Session bounds are seconds after midnight; 24:00 comes back as a full day.
bool SecondInSession(const long second, const long from, const long to)
{
   long start = from % SECONDS_PER_DAY;
   long end = to % SECONDS_PER_DAY;
   if(end == 0 && to > from)
      end = SECONDS_PER_DAY;
   if(start <= end)
      return second >= start && second < end;
   return second >= start || second < end;
}

bool InTradeSession(const datetime now_server)
{
   MqlDateTime parts;
   TimeToStruct(now_server, parts);
   long second = (long)now_server % SECONDS_PER_DAY;
   datetime from = 0, to = 0;
   for(uint s = 0; s < (uint)MAX_TRADE_SESSIONS; s++)
   {
      if(!SymbolInfoSessionTrade(_Symbol, (ENUM_DAY_OF_WEEK)parts.day_of_week, s, from, to))
         return false;
      if(SecondInSession(second, (long)from, (long)to))
         return true;
   }
   return false;
}

// Milliseconds since the last M15 close, i.e. since the decision bar closed
// for an intent acted on within its own bar (the default operator deadline
// plus the intent TTL stay below 900 s).
long DecisionLatencyMs(void)
{
   return ((long)TimeGMT() % M15_SECONDS) * MS_PER_SECOND;
}

#endif // QLIPV6_SCHEDULE_MQH
