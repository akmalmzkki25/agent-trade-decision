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

#define INTENT_SCHEMA            "v6.intent.1"
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
#define INTENT_SOURCE_RULES      "rules"
#define INTENT_SOURCE_OPERATOR   "operator"
#define INTENT_SIDE_BUY          "buy"
#define INTENT_SIDE_SELL         "sell"
#define INTENT_BUY_LIMIT         "BUY_LIMIT"
#define INTENT_SELL_LIMIT        "SELL_LIMIT"
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

bool IsKnownCommand(const string command)
{
   return command == CMD_NONE || command == CMD_FLATTEN || command == CMD_CANCEL_PENDING;
}

bool IsLimitOrder(const string order_type)
{
   return order_type == INTENT_BUY_LIMIT || order_type == INTENT_SELL_LIMIT;
}

bool OrderTypeMatchesSide(const string side, const string order_type)
{
   if(side == INTENT_SIDE_BUY)
      return order_type == INTENT_BUY_LIMIT || order_type == INTENT_BUY;
   if(side == INTENT_SIDE_SELL)
      return order_type == INTENT_SELL_LIMIT || order_type == INTENT_SELL;
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
   if(!IsLimitOrder(reply.order_type))
      return (reply.pending_expiry_epoch == 0) ? "" : "a market order has no pending expiry";
   if(reply.pending_expiry_epoch < reply.valid_until_epoch + MIN_PENDING_LIFETIME_S)
      return "a limit order must expire at least 60 s after valid_until";
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
   return PendingExpiryProblem(reply);
}

#endif // QLIPV6_INTENT_MQH
