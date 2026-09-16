//+------------------------------------------------------------------+
//| QlipV6/Persist.mqh                                               |
//| State that must survive an EA restart, kept in terminal global   |
//| variables under the QlipV6_ prefix (magic range 250570-250579).  |
//+------------------------------------------------------------------+
#ifndef QLIPV6_PERSIST_MQH
#define QLIPV6_PERSIST_MQH

#define PERSIST_PREFIX       "QlipV6_"
// Terminal-wide kill switch (plan §6): any value >= 0.5 halts the EA.
#define PERSIST_HALT_NAME    "QlipV6_HALT"
#define PERSIST_HALT_ON      0.5
#define SECONDS_PER_DAY      86400

// Keys carry the login because global variables are shared by every
// account the terminal has ever been logged into.
string PersistKey(const string name)
{
   return PERSIST_PREFIX + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) + "_" + name;
}

bool PersistHaltRequested(void)
{
   if(!GlobalVariableCheck(PERSIST_HALT_NAME))
      return false;
   return GlobalVariableGet(PERSIST_HALT_NAME) >= PERSIST_HALT_ON;
}

long UtcDayNumber(const datetime utc_time)
{
   return (long)utc_time / SECONDS_PER_DAY;
}

datetime UtcDayStart(const datetime utc_time)
{
   return (datetime)(UtcDayNumber(utc_time) * SECONDS_PER_DAY);
}

bool PersistSet(const string key, const double value)
{
   if(GlobalVariableSet(key, value) > 0)
      return true;
   PrintFormat("V6 persist: could not store %s (err=%d)", key, GetLastError());
   return false;
}

// Equity at the first sighting of the current UTC day. Stored so that a
// restart mid-day keeps the original baseline for the daily loss figure.
double PersistDayStartEquity(const datetime now_utc, const double equity)
{
   string day_key = PersistKey("DAYKEY");
   string equity_key = PersistKey("DAYEQ");
   long today = UtcDayNumber(now_utc);
   bool same_day = GlobalVariableCheck(day_key)
                   && (long)GlobalVariableGet(day_key) == today
                   && GlobalVariableCheck(equity_key);
   if(same_day)
      return GlobalVariableGet(equity_key);
   PersistSet(equity_key, equity);
   PersistSet(day_key, (double)today);
   return equity;
}

// Results waiting to be re-sent. Data-only mode never queues anything; the
// execution phase replaces this with the MQL5/Files/QlipV6 outbox.
int PersistOutboxPending(void)
{
   return 0;
}

#endif // QLIPV6_PERSIST_MQH
