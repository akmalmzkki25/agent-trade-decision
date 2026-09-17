//+------------------------------------------------------------------+
//| QlipV6/Persist.mqh                                               |
//| State that must survive an EA restart, kept in terminal global   |
//| variables under the QlipV6_ prefix (magic range 250570-250579):  |
//| the day-start equity and the set of intents already processed.   |
//+------------------------------------------------------------------+
#ifndef QLIPV6_PERSIST_MQH
#define QLIPV6_PERSIST_MQH

#define PERSIST_PREFIX       "QlipV6_"
// Terminal-wide kill switch (plan §6): any value >= 0.5 halts the EA.
#define PERSIST_HALT_NAME    "QlipV6_HALT"
#define PERSIST_HALT_ON      0.5
#define SECONDS_PER_DAY      86400
// Contract §8.1 check 2: a bounded ring of the newest processed intent ids.
#define SEEN_CAPACITY        32
#define SEEN_NEXT_NAME       "SEENNEXT"
// An intent id is 12 base32 characters (60 bits): two 30-bit halves, each
// exact in a double.
#define ID_HALF_CHARS        6
#define ID_HALF_LIMIT        1073741824
#define BASE32_RADIX         32
#define BASE32_DIGIT_BASE    26          // '2' encodes 26 (RFC 4648 alphabet)
#define ID_EMPTY             (-1)

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

double PersistGet(const string key, const double fallback)
{
   double value = 0.0;
   if(GlobalVariableGet(key, value))
      return value;
   return fallback;
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

//--- intent ids as numbers -------------------------------------------

int Base32Value(const ushort c)
{
   if(c >= 'a' && c <= 'z')
      return (int)(c - 'a');
   if(c >= '2' && c <= '7')
      return (int)(c - '2') + BASE32_DIGIT_BASE;
   return -1;
}

bool IntentIdToHalves(const string id, long &high, long &low)
{
   if(StringLen(id) != 2 * ID_HALF_CHARS)
      return false;
   long parts[2] = {0, 0};
   for(int i = 0; i < 2 * ID_HALF_CHARS; i++)
   {
      int digit = Base32Value(StringGetCharacter(id, i));
      if(digit < 0)
         return false;
      int half = (i < ID_HALF_CHARS) ? 0 : 1;
      parts[half] = parts[half] * BASE32_RADIX + digit;
   }
   high = parts[0];
   low = parts[1];
   return true;
}

string HalfToChars(const long half)
{
   string out = "";
   long rest = half;
   for(int i = 0; i < ID_HALF_CHARS; i++)
   {
      int digit = (int)(rest % BASE32_RADIX);
      rest /= BASE32_RADIX;
      int code = (digit < BASE32_DIGIT_BASE) ? 'a' + digit : '2' + digit - BASE32_DIGIT_BASE;
      out = ShortToString((ushort)code) + out;
   }
   return out;
}

// "" when either half is empty or out of range.
string IntentIdFromHalves(const long high, const long low)
{
   if(high < 0 || low < 0 || high >= ID_HALF_LIMIT || low >= ID_HALF_LIMIT)
      return "";
   return HalfToChars(high) + HalfToChars(low);
}

//--- processed intents -----------------------------------------------

long g_seen_high[SEEN_CAPACITY];
long g_seen_low[SEEN_CAPACITY];
int  g_seen_next = 0;

string SeenKey(const int slot, const string half)
{
   return PersistKey("SEEN" + IntegerToString(slot) + half);
}

void SeenLoad(void)
{
   g_seen_next = (int)PersistGet(PersistKey(SEEN_NEXT_NAME), 0.0);
   if(g_seen_next < 0 || g_seen_next >= SEEN_CAPACITY)
      g_seen_next = 0;
   for(int i = 0; i < SEEN_CAPACITY; i++)
   {
      g_seen_high[i] = (long)PersistGet(SeenKey(i, "H"), (double)ID_EMPTY);
      g_seen_low[i] = (long)PersistGet(SeenKey(i, "L"), (double)ID_EMPTY);
   }
}

bool IntentSeen(const string id)
{
   long high = 0, low = 0;
   if(!IntentIdToHalves(id, high, low))
      return false;
   for(int i = 0; i < SEEN_CAPACITY; i++)
   {
      if(g_seen_high[i] == high && g_seen_low[i] == low)
         return true;
   }
   return false;
}

// Stored and flushed to disk before anything is sent to the broker, so a
// crash between OrderCheck and the report can never trade an intent twice.
bool MarkIntentSeen(const string id)
{
   long high = 0, low = 0;
   if(!IntentIdToHalves(id, high, low))
      return false;
   int slot = g_seen_next;
   g_seen_high[slot] = high;
   g_seen_low[slot] = low;
   g_seen_next = (slot + 1) % SEEN_CAPACITY;
   bool ok = PersistSet(SeenKey(slot, "H"), (double)high);
   ok = PersistSet(SeenKey(slot, "L"), (double)low) && ok;
   ok = PersistSet(PersistKey(SEEN_NEXT_NAME), (double)g_seen_next) && ok;
   GlobalVariablesFlush();
   return ok;
}

// The newest processed intent, "" if none.
string LastSeenIntentId(void)
{
   int slot = (g_seen_next + SEEN_CAPACITY - 1) % SEEN_CAPACITY;
   return IntentIdFromHalves(g_seen_high[slot], g_seen_low[slot]);
}

#endif // QLIPV6_PERSIST_MQH
