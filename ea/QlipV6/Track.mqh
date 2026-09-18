//+------------------------------------------------------------------+
//| QlipV6/Track.mqh                                                 |
//| One record per V6 pending order or position, keyed by the order |
//| ticket, which MT5 also uses as the identifier of the position    |
//| the order opens. Records live in memory and in global variables |
//| QlipV6_<login>_P<key>_<field>, so a restart keeps the time       |
//| barrier, requested exits, the SL+ plan (triggers, stops, the     |
//| last step taken) and the entry facts of every trade.             |
//+------------------------------------------------------------------+
#ifndef QLIPV6_TRACK_MQH
#define QLIPV6_TRACK_MQH

#include "Persist.mqh"
#include "Intent.mqh"

#define TRACK_MAX                32
#define TRACK_STATE_PENDING      1
#define TRACK_STATE_OPEN         2
#define TRACK_KEY_PREFIX         "P"
#define TRACK_STATE_FIELD        "ST"
#define TRACK_SIDE_BUY           1
#define TRACK_SIDE_SELL          (-1)
#define DEFAULT_TIME_BARRIER_S   7200      // adapter default: 8 x M15
#define ORDER_COMMENT_PREFIX     "Q6:"
// Why the EA closes a position (basket close_reason) or deletes an order.
#define CLOSE_BY_NONE            0
#define CLOSE_BY_TIME            1
#define CLOSE_BY_FLATTEN         2
#define CLOSE_BY_ROLLOVER        3
#define CLOSE_BY_AGENT           4
#define CANCEL_BY_NONE           0
#define CANCEL_BY_COMMAND        1
#define CANCEL_BY_HALT           2
#define CANCEL_BY_BREAKER        3
#define CANCEL_BY_ROLLOVER       4

struct TrackRecord
{
   ulong             key;              // order ticket == position identifier
   int               state;
   int               side;             // TRACK_SIDE_*
   long              id_high;          // intent id, see IntentIdToHalves
   long              id_low;
   long              barrier_s;
   double            requested;        // entry price asked for
   double            sl;
   double            tp;
   long              latency_ms;       // decision bar close -> order sent
   double            equity_open;
   double            entry_spread;
   double            entry_slippage;
   double            worst_pnl;        // <= 0, account currency
   double            best_pnl;         // >= 0
   int               close_reason;     // CLOSE_BY_*, set means "close it"
   int               cancel_reason;    // CANCEL_BY_*, set means "delete it"
   double            exit_requested;
   double            exit_spread;
   double            tp1;              // SL+ trigger 1 (0 = none)
   double            tp2;              // SL+ trigger 2 (0 = none)
   double            step_sl1;         // the stop after tp1 (0 = none)
   double            step_sl2;         // the stop after tp2 (0 = none)
   int               plan_step;        // 0, 1 or 2: the last step taken
   ulong             plan_next_ms;     // memory only: SL+ retry throttle
   long              missing_since;    // memory only: UTC time first not found
   ulong             next_try_ms;      // memory only: exit retry throttle
   datetime          last_log;         // memory only: log throttle
};

TrackRecord g_track[];
int         g_track_count = 0;

string TrackName(const ulong key, const string field)
{
   return PersistKey(TRACK_KEY_PREFIX + IntegerToString((long)key) + "_" + field);
}

void TrackSave(const TrackRecord &r)
{
   PersistSet(TrackName(r.key, TRACK_STATE_FIELD), (double)r.state);
   PersistSet(TrackName(r.key, "SD"), (double)r.side);
   PersistSet(TrackName(r.key, "IH"), (double)r.id_high);
   PersistSet(TrackName(r.key, "IL"), (double)r.id_low);
   PersistSet(TrackName(r.key, "BAR"), (double)r.barrier_s);
   PersistSet(TrackName(r.key, "REQ"), r.requested);
   PersistSet(TrackName(r.key, "SL"), r.sl);
   PersistSet(TrackName(r.key, "TP"), r.tp);
   PersistSet(TrackName(r.key, "LAT"), (double)r.latency_ms);
   PersistSet(TrackName(r.key, "EQ"), r.equity_open);
   PersistSet(TrackName(r.key, "SPR"), r.entry_spread);
   PersistSet(TrackName(r.key, "SLP"), r.entry_slippage);
   PersistSet(TrackName(r.key, "MAE"), r.worst_pnl);
   PersistSet(TrackName(r.key, "MFE"), r.best_pnl);
   PersistSet(TrackName(r.key, "CR"), (double)r.close_reason);
   PersistSet(TrackName(r.key, "CXR"), (double)r.cancel_reason);
   PersistSet(TrackName(r.key, "XRQ"), r.exit_requested);
   PersistSet(TrackName(r.key, "XSP"), r.exit_spread);
   PersistSet(TrackName(r.key, "T1"), r.tp1);
   PersistSet(TrackName(r.key, "T2"), r.tp2);
   PersistSet(TrackName(r.key, "S1"), r.step_sl1);
   PersistSet(TrackName(r.key, "S2"), r.step_sl2);
   PersistSet(TrackName(r.key, "PS"), (double)r.plan_step);
}

bool TrackLoad(const ulong key, TrackRecord &r)
{
   ZeroMemory(r);
   r.key = key;
   r.state = (int)PersistGet(TrackName(key, TRACK_STATE_FIELD), 0.0);
   r.side = (int)PersistGet(TrackName(key, "SD"), 0.0);
   r.id_high = (long)PersistGet(TrackName(key, "IH"), (double)ID_EMPTY);
   r.id_low = (long)PersistGet(TrackName(key, "IL"), (double)ID_EMPTY);
   r.barrier_s = (long)PersistGet(TrackName(key, "BAR"), (double)DEFAULT_TIME_BARRIER_S);
   r.requested = PersistGet(TrackName(key, "REQ"), 0.0);
   r.sl = PersistGet(TrackName(key, "SL"), 0.0);
   r.tp = PersistGet(TrackName(key, "TP"), 0.0);
   r.latency_ms = (long)PersistGet(TrackName(key, "LAT"), 0.0);
   r.equity_open = PersistGet(TrackName(key, "EQ"), 0.0);
   r.entry_spread = PersistGet(TrackName(key, "SPR"), 0.0);
   r.entry_slippage = PersistGet(TrackName(key, "SLP"), 0.0);
   r.worst_pnl = PersistGet(TrackName(key, "MAE"), 0.0);
   r.best_pnl = PersistGet(TrackName(key, "MFE"), 0.0);
   r.close_reason = (int)PersistGet(TrackName(key, "CR"), 0.0);
   r.cancel_reason = (int)PersistGet(TrackName(key, "CXR"), 0.0);
   r.exit_requested = PersistGet(TrackName(key, "XRQ"), 0.0);
   r.exit_spread = PersistGet(TrackName(key, "XSP"), 0.0);
   r.tp1 = PersistGet(TrackName(key, "T1"), 0.0);
   r.tp2 = PersistGet(TrackName(key, "T2"), 0.0);
   r.step_sl1 = PersistGet(TrackName(key, "S1"), 0.0);
   r.step_sl2 = PersistGet(TrackName(key, "S2"), 0.0);
   r.plan_step = (int)PersistGet(TrackName(key, "PS"), 0.0);
   return r.state == TRACK_STATE_PENDING || r.state == TRACK_STATE_OPEN;
}

int TrackFind(const ulong key)
{
   for(int i = 0; i < g_track_count; i++)
   {
      if(g_track[i].key == key)
         return i;
   }
   return -1;
}

bool TrackAppend(const TrackRecord &r)
{
   if(g_track_count >= TRACK_MAX)
   {
      PrintFormat("V6 track: %d records already; ticket %I64u is NOT tracked", TRACK_MAX, r.key);
      return false;
   }
   ArrayResize(g_track, g_track_count + 1, TRACK_MAX);
   g_track[g_track_count] = r;
   g_track_count++;
   return true;
}

// Stores a new record and flushes it to disk at once.
bool TrackAdd(const TrackRecord &r)
{
   if(TrackFind(r.key) >= 0)
      return true;
   if(!TrackAppend(r))
      return false;
   TrackSave(r);
   GlobalVariablesFlush();
   return true;
}

void TrackRemove(const int index)
{
   if(GlobalVariablesDeleteAll(TrackName(g_track[index].key, "")) <= 0)
      PrintFormat("V6 track: ticket %I64u had no stored fields", g_track[index].key);
   for(int i = index; i < g_track_count - 1; i++)
      g_track[i] = g_track[i + 1];
   g_track_count--;
   ArrayResize(g_track, g_track_count, TRACK_MAX);
}

// "QlipV6_<login>_P<digits>_ST" -> <digits>
bool TrackKeyFromName(const string name, const string prefix, const string suffix, ulong &key)
{
   int start = StringLen(prefix);
   int digits = StringLen(name) - start - StringLen(suffix);
   if(digits <= 0 || StringFind(name, prefix) != 0 || StringSubstr(name, start + digits) != suffix)
      return false;
   for(int i = start; i < start + digits; i++)
   {
      ushort c = StringGetCharacter(name, i);
      if(c < '0' || c > '9')
         return false;
   }
   key = (ulong)StringToInteger(StringSubstr(name, start, digits));
   return key > 0;
}

// A record still stored for `key` (after a restart, or missed by a scan while
// another EA changed global variables) wins over adoption defaults.
bool TrackRestore(const ulong key)
{
   TrackRecord r;
   if(TrackFind(key) >= 0 || !TrackLoad(key, r))
      return false;
   return TrackAppend(r);
}

void TrackLoadAll(void)
{
   ArrayResize(g_track, 0, TRACK_MAX);
   g_track_count = 0;
   string prefix = PersistKey(TRACK_KEY_PREFIX);
   string suffix = "_" + TRACK_STATE_FIELD;
   for(int i = GlobalVariablesTotal() - 1; i >= 0; i--)
   {
      ulong key = 0;
      if(TrackKeyFromName(GlobalVariableName(i), prefix, suffix, key))
         TrackRestore(key);
   }
}

// The last SL+ step taken and the time-limit epoch (UTC) of the record keyed by
// `key`; false (0 and 0) when V6 does not track it.
bool TrackPlanFacts(const ulong key, const long open_utc, int &step, long &limit_epoch)
{
   int i = TrackFind(key);
   step = 0;
   limit_epoch = 0;
   if(i < 0)
      return false;
   step = g_track[i].plan_step;
   long barrier = (g_track[i].barrier_s > 0) ? g_track[i].barrier_s : DEFAULT_TIME_BARRIER_S;
   limit_epoch = open_utc + MathMin(barrier, (long)MAX_TIME_BARRIER_S);
   return true;
}

string TrackIntentId(const TrackRecord &r)
{
   return IntentIdFromHalves(r.id_high, r.id_low);
}

void TrackSetIntentId(TrackRecord &r, const string id)
{
   if(IsIntentId(id) && IntentIdToHalves(id, r.id_high, r.id_low))
      return;
   r.id_high = ID_EMPTY;
   r.id_low = ID_EMPTY;
}

// "Q6:<intent_id>" -> intent_id, "" for any other comment.
string IntentIdFromComment(const string comment)
{
   if(StringFind(comment, ORDER_COMMENT_PREFIX) != 0)
      return "";
   string id = StringSubstr(comment, StringLen(ORDER_COMMENT_PREFIX));
   return IsIntentId(id) ? id : "";
}

string TrackComment(const TrackRecord &r)
{
   return ORDER_COMMENT_PREFIX + TrackIntentId(r);
}

void TrackMarkClose(const int i, const int reason)
{
   g_track[i].close_reason = reason;
   PersistSet(TrackName(g_track[i].key, "CR"), (double)reason);
   GlobalVariablesFlush();
}

void TrackMarkCancel(const int i, const int reason)
{
   g_track[i].cancel_reason = reason;
   PersistSet(TrackName(g_track[i].key, "CXR"), (double)reason);
   GlobalVariablesFlush();
}

// Worst and best floating P&L (profit + swap) of the selected position.
void TrackExcursion(const int i, const double pnl)
{
   if(pnl < g_track[i].worst_pnl)
   {
      g_track[i].worst_pnl = pnl;
      PersistSet(TrackName(g_track[i].key, "MAE"), pnl);
   }
   if(pnl > g_track[i].best_pnl)
   {
      g_track[i].best_pnl = pnl;
      PersistSet(TrackName(g_track[i].key, "MFE"), pnl);
   }
}

#endif // QLIPV6_TRACK_MQH
