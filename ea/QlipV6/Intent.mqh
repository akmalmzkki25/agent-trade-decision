//+------------------------------------------------------------------+
//| QlipV6/Intent.mqh                                                |
//| The flat v6.intent.1 poll reply: strict parsing, the canonical   |
//| string of app/v6/wire.py (intent_canonical) and its HMAC check,  |
//| plus the symbol-independent intent rules of contract §6.3.       |
//|                                                                  |
//| Prices are signed as integer points and lots as integer          |
//| hundredths, computed from the parsed numbers, so no float text   |
//| is ever signed.                                                  |
//+------------------------------------------------------------------+
#ifndef QLIPV6_INTENT_MQH
#define QLIPV6_INTENT_MQH

#include "Json.mqh"
#include "Hmac.mqh"

#define INTENT_SCHEMA            "v6.intent.2"
#define INTENT_ID_CHARS          12
#define CANONICAL_SEPARATOR      "|"
#define LOTS_SCALE               100.0
#define POINTS_LIMIT             1.0e15
#define JSON_MAX_INT_DIGITS      18
#define REQUIRE_DEMO             1
#define MAX_TIME_BARRIER_S       14400
#define MIN_PENDING_LIFETIME_S   60
#define CMD_NONE                 "NONE"
#define CMD_FLATTEN              "FLATTEN"
#define CMD_CANCEL_PENDING       "CANCEL_PENDING"
#define CMD_CLOSE_POSITION       "CLOSE_POSITION"
#define CMD_MODIFY_POSITION      "MODIFY_POSITION"
#define CMD_MODIFY_PENDING       "MODIFY_PENDING"
#define ACTION_MAX_AGE_S         30
#define INTENT_SOURCE_RULES      "rules"
#define INTENT_SOURCE_OPERATOR   "operator"
#define INTENT_SIDE_BUY          "buy"
#define INTENT_SIDE_SELL         "sell"
#define INTENT_BUY_LIMIT         "BUY_LIMIT"
#define INTENT_SELL_LIMIT        "SELL_LIMIT"
#define INTENT_BUY_STOP          "BUY_STOP"
#define INTENT_SELL_STOP         "SELL_STOP"
#define INTENT_BUY               "BUY"
#define INTENT_SELL              "SELL"

// PollResponse (app/v6/schemas/intent.py), field for field.
struct PollReply
{
   string            schema_version;
   long              server_time_epoch;
   string            command;
   bool              has_intent;
   string            intent_id;
   string            source;
   long              require_demo;
   string            side;
   string            order_type;
   double            entry;
   double            sl;
   double            tp;
   double            lots;
   double            ref_price;
   long              max_drift_points;
   long              max_spread_points;
   long              valid_until_epoch;
   long              pending_expiry_epoch;
   long              time_barrier_s;
   long              magic;
   double            tp1;                     // SL+ trigger 1 (0 = none)
   double            tp2;                     // SL+ trigger 2 (0 = none)
   double            sl_after_tp1;            // stop after tp1
   double            sl_after_tp2;            // stop after tp2
   string            action_id;               // management action (spec 3.3)
   long              action_ticket;
   double            action_sl;
   double            action_tp;
   double            action_tp1;
   double            action_tp2;
   double            action_sl1;
   double            action_sl2;
   double            action_price;
   long              action_expiry_epoch;
   long              action_barrier_s;
   long              action_issued_epoch;
   string            sig;
};

//--- strict readers ------------------------------------------------

// An integer literal: optional minus and digits only (7200.0 is refused).
bool IsJsonIntegerText(const string raw)
{
   int n = StringLen(raw);
   int start = (n > 0 && StringGetCharacter(raw, 0) == '-') ? 1 : 0;
   if(n <= start || n - start > JSON_MAX_INT_DIGITS)
      return false;
   for(int i = start; i < n; i++)
   {
      ushort c = StringGetCharacter(raw, i);
      if(c < '0' || c > '9')
         return false;
   }
   return true;
}

bool JsonGetLong(const string json, const string key, long &value)
{
   string raw = "";
   bool is_string = false;
   if(!JsonFindRaw(json, key, raw, is_string) || is_string || !IsJsonIntegerText(raw))
      return false;
   value = StringToInteger(raw);
   return true;
}

// Every field must be present with its contract kind, or the reply is unusable.
bool ParsePollReply(const string json, PollReply &p)
{
   return JsonGetString(json, "schema_version", p.schema_version)
          && JsonGetLong(json, "server_time_epoch", p.server_time_epoch)
          && JsonGetString(json, "command", p.command)
          && JsonGetBool(json, "has_intent", p.has_intent)
          && JsonGetString(json, "intent_id", p.intent_id)
          && JsonGetString(json, "source", p.source)
          && JsonGetLong(json, "require_demo", p.require_demo)
          && JsonGetString(json, "side", p.side)
          && JsonGetString(json, "order_type", p.order_type)
          && JsonGetNumber(json, "entry", p.entry)
          && JsonGetNumber(json, "sl", p.sl)
          && JsonGetNumber(json, "tp", p.tp)
          && JsonGetNumber(json, "lots", p.lots)
          && JsonGetNumber(json, "ref_price", p.ref_price)
          && JsonGetLong(json, "max_drift_points", p.max_drift_points)
          && JsonGetLong(json, "max_spread_points", p.max_spread_points)
          && JsonGetLong(json, "valid_until_epoch", p.valid_until_epoch)
          && JsonGetLong(json, "pending_expiry_epoch", p.pending_expiry_epoch)
          && JsonGetLong(json, "time_barrier_s", p.time_barrier_s)
          && JsonGetLong(json, "magic", p.magic)
          && JsonGetNumber(json, "tp1", p.tp1)
          && JsonGetNumber(json, "tp2", p.tp2)
          && JsonGetNumber(json, "sl_after_tp1", p.sl_after_tp1)
          && JsonGetNumber(json, "sl_after_tp2", p.sl_after_tp2)
          && JsonGetString(json, "action_id", p.action_id)
          && JsonGetLong(json, "action_ticket", p.action_ticket)
          && JsonGetNumber(json, "action_sl", p.action_sl)
          && JsonGetNumber(json, "action_tp", p.action_tp)
          && JsonGetNumber(json, "action_tp1", p.action_tp1)
          && JsonGetNumber(json, "action_tp2", p.action_tp2)
          && JsonGetNumber(json, "action_sl1", p.action_sl1)
          && JsonGetNumber(json, "action_sl2", p.action_sl2)
          && JsonGetNumber(json, "action_price", p.action_price)
          && JsonGetLong(json, "action_expiry_epoch", p.action_expiry_epoch)
          && JsonGetLong(json, "action_barrier_s", p.action_barrier_s)
          && JsonGetLong(json, "action_issued_epoch", p.action_issued_epoch)
          && JsonGetString(json, "sig", p.sig);
}

//--- identity and vocabulary ---------------------------------------

bool IsBase32Char(const ushort c)
{
   return (c >= 'a' && c <= 'z') || (c >= '2' && c <= '7');
}

// schemas.intent.INTENT_ID_PATTERN: ^[a-z2-7]{12}$
bool IsIntentId(const string id)
{
   if(StringLen(id) != INTENT_ID_CHARS)
      return false;
   for(int i = 0; i < INTENT_ID_CHARS; i++)
   {
      if(!IsBase32Char(StringGetCharacter(id, i)))
         return false;
   }
   return true;
}

// A signed reply carries a management action instead of an intent (spec 3.3).
bool IsActionCommand(const string command)
{
   return command == CMD_CLOSE_POSITION || command == CMD_MODIFY_POSITION
          || command == CMD_MODIFY_PENDING;
}

bool IsKnownCommand(const string command)
{
   return command == CMD_NONE || command == CMD_FLATTEN || command == CMD_CANCEL_PENDING
          || IsActionCommand(command);
}

bool IsLimitOrder(const string order_type)
{
   return order_type == INTENT_BUY_LIMIT || order_type == INTENT_SELL_LIMIT;
}

bool IsStopOrder(const string order_type)
{
   return order_type == INTENT_BUY_STOP || order_type == INTENT_SELL_STOP;
}

bool IsPendingOrder(const string order_type)
{
   return IsLimitOrder(order_type) || IsStopOrder(order_type);
}

bool OrderTypeMatchesSide(const string side, const string order_type)
{
   if(side == INTENT_SIDE_BUY)
      return order_type == INTENT_BUY_LIMIT || order_type == INTENT_BUY_STOP
             || order_type == INTENT_BUY;
   if(side == INTENT_SIDE_SELL)
      return order_type == INTENT_SELL_LIMIT || order_type == INTENT_SELL_STOP
             || order_type == INTENT_SELL;
   return false;
}

//--- the canonical string and its signature --------------------------

// wire.price_to_points: round(price / point); -1 when it cannot be expressed.
long PriceToPoints(const double price, const double point)
{
   if(point <= 0.0 || !MathIsValidNumber(price) || MathAbs(price / point) > POINTS_LIMIT)
      return -1;
   return (long)MathRound(price / point);
}

// wire.lots_to_hundredths: round(lots x 100).
long LotsToHundredths(const double lots)
{
   if(!MathIsValidNumber(lots) || MathAbs(lots * LOTS_SCALE) > POINTS_LIMIT)
      return -1;
   return (long)MathRound(lots * LOTS_SCALE);
}

// wire.intent_canonical, fields in wire.CANONICAL_FIELDS order.
string IntentCanonical(const PollReply &p, const double point)
{
   string s = p.schema_version;
   s += CANONICAL_SEPARATOR + IntegerToString(p.server_time_epoch);
   s += CANONICAL_SEPARATOR + p.command;
   s += CANONICAL_SEPARATOR + (p.has_intent ? "1" : "0");
   s += CANONICAL_SEPARATOR + p.intent_id;
   s += CANONICAL_SEPARATOR + p.source;
   s += CANONICAL_SEPARATOR + IntegerToString(p.require_demo);
   s += CANONICAL_SEPARATOR + p.side;
   s += CANONICAL_SEPARATOR + p.order_type;
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.entry, point));
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.sl, point));
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.tp, point));
   s += CANONICAL_SEPARATOR + IntegerToString(LotsToHundredths(p.lots));
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.ref_price, point));
   s += CANONICAL_SEPARATOR + IntegerToString(p.max_drift_points);
   s += CANONICAL_SEPARATOR + IntegerToString(p.max_spread_points);
   s += CANONICAL_SEPARATOR + IntegerToString(p.valid_until_epoch);
   s += CANONICAL_SEPARATOR + IntegerToString(p.pending_expiry_epoch);
   s += CANONICAL_SEPARATOR + IntegerToString(p.time_barrier_s);
   s += CANONICAL_SEPARATOR + IntegerToString(p.magic);
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.tp1, point));
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.tp2, point));
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.sl_after_tp1, point));
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.sl_after_tp2, point));
   s += CANONICAL_SEPARATOR + p.action_id;
   s += CANONICAL_SEPARATOR + IntegerToString(p.action_ticket);
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.action_sl, point));
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.action_tp, point));
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.action_tp1, point));
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.action_tp2, point));
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.action_sl1, point));
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.action_sl2, point));
   s += CANONICAL_SEPARATOR + IntegerToString(PriceToPoints(p.action_price, point));
   s += CANONICAL_SEPARATOR + IntegerToString(p.action_expiry_epoch);
   s += CANONICAL_SEPARATOR + IntegerToString(p.action_barrier_s);
   s += CANONICAL_SEPARATOR + IntegerToString(p.action_issued_epoch);
   return s;
}

// wire.verify_intent: recompute and compare in constant time.
bool PollReplySignatureOk(const PollReply &reply, const uchar &key[], const double point)
{
   if(ArraySize(key) == 0 || StringLen(reply.sig) != HMAC_HEX_CHARS)
      return false;
   string expected = SignText(key, IntentCanonical(reply, point));
   return StringLen(expected) == HMAC_HEX_CHARS && ConstantTimeEquals(expected, reply.sig);
}

//--- intent rules that need no symbol data (contract §6.3) -------------

bool TakeProfitOnItsSide(const PollReply &reply)
{
   if(reply.side == INTENT_SIDE_BUY)
      return reply.tp > reply.entry;
   return reply.tp < reply.entry;
}

bool StopLossOnItsSide(const PollReply &reply)
{
   if(reply.side == INTENT_SIDE_BUY)
      return reply.sl < reply.entry;
   return reply.sl > reply.entry;
}

string PendingExpiryProblem(const PollReply &reply)
{
   if(!IsPendingOrder(reply.order_type))
      return (reply.pending_expiry_epoch == 0) ? "" : "a market order has no pending expiry";
   if(reply.pending_expiry_epoch < reply.valid_until_epoch + MIN_PENDING_LIFETIME_S)
      return "a pending order must expire at least 60 s after valid_until";
   return "";
}

// schemas.intent._ladder_checks: the SL+ ladder, when the intent carries one.
string LadderProblem(const PollReply &p)
{
   if(p.tp1 == 0.0 && p.tp2 == 0.0 && p.sl_after_tp1 == 0.0 && p.sl_after_tp2 == 0.0)
      return "";
   double sign = (p.side == INTENT_SIDE_BUY) ? 1.0 : -1.0;
   if(p.tp1 <= 0.0 || p.tp2 <= 0.0 || sign * (p.tp1 - p.entry) <= 0.0
      || sign * (p.tp2 - p.tp1) <= 0.0 || sign * (p.tp - p.tp2) <= 0.0)
      return "the TP ladder must advance: entry, tp1, tp2, tp";
   if(p.sl_after_tp1 > 0.0 && (sign * (p.sl_after_tp1 - p.sl) <= 0.0
                               || sign * (p.tp1 - p.sl_after_tp1) <= 0.0))
      return "sl_after_tp1 must sit between sl and tp1";
   double floor_sl = (p.sl_after_tp1 > 0.0) ? p.sl_after_tp1 : p.sl;
   if(p.sl_after_tp2 > 0.0 && (sign * (p.sl_after_tp2 - floor_sl) < 0.0
                               || sign * (p.sl_after_tp2 - p.sl) <= 0.0
                               || sign * (p.tp2 - p.sl_after_tp2) <= 0.0))
      return "sl_after_tp2 must sit between the stop before it and tp2";
   return "";
}

// "" when the intent is well formed; sl is judged separately (NO_SL first).
string IntentFieldsProblem(const PollReply &reply)
{
   if(reply.schema_version != INTENT_SCHEMA || reply.command != CMD_NONE)
      return "schema or command";
   if(!IsIntentId(reply.intent_id))
      return "intent id";
   if(reply.source != INTENT_SOURCE_RULES && reply.source != INTENT_SOURCE_OPERATOR)
      return "source";
   if(!OrderTypeMatchesSide(reply.side, reply.order_type))
      return "order_type does not match side";
   if(reply.entry <= 0.0 || reply.tp <= 0.0 || reply.lots <= 0.0 || reply.ref_price <= 0.0)
      return "prices and lots must be positive";
   if(!TakeProfitOnItsSide(reply))
      return "tp is on the wrong side of entry";
   if(reply.max_drift_points <= 0 || reply.max_spread_points <= 0)
      return "drift and spread limits must be positive";
   if(reply.time_barrier_s <= 0 || reply.time_barrier_s > MAX_TIME_BARRIER_S)
      return "time barrier out of range";
   if(reply.valid_until_epoch <= reply.server_time_epoch)
      return "valid_until is not after the server time";
   string ladder = LadderProblem(reply);
   if(ladder != "")
      return ladder;
   return PendingExpiryProblem(reply);
}

#endif // QLIPV6_INTENT_MQH
