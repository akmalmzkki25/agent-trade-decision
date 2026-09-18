//+------------------------------------------------------------------+
//| QlipV6/Exposure.mqh                                              |
//| Open positions and pending orders that belong to V6, i.e. this   |
//| symbol and the V6 magic. Everything else on the account is not  |
//| reported: other EAs and manual trades are not V6's to manage.    |
//+------------------------------------------------------------------+
#ifndef QLIPV6_EXPOSURE_MQH
#define QLIPV6_EXPOSURE_MQH

#include "Json.mqh"
#include "Market.mqh"
#include "Track.mqh"

#define COMMENT_TEXT_MAX  31
#define V6_MAX_ROWS       20   // contract cap on positions and pending orders
#define VOLUME_DIGITS     8
#define POINTS_DIGITS     1

int MoneyDigits(void) { return (int)AccountInfoInteger(ACCOUNT_CURRENCY_DIGITS); }

bool IsV6Position(const long magic)
{
   return PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == magic;
}

bool IsV6Order(const long magic)
{
   return OrderGetString(ORDER_SYMBOL) == _Symbol && OrderGetInteger(ORDER_MAGIC) == magic;
}

// Worst and best excursion since the fill, from bid-based M1 highs and lows.
// Recomputed from history every time, so a restart loses nothing; the fill
// minute counts in full, which can overstate either figure slightly.
void PositionExcursions(const bool is_buy, const double open_price, const datetime opened,
                        double &mae_pts, double &mfe_pts)
{
   mae_pts = 0.0;
   mfe_pts = 0.0;
   double highs[], lows[];
   long minute = PeriodSeconds(PERIOD_M1);
   datetime first_minute = (datetime)((long)opened - (long)opened % minute);
   datetime now = TimeTradeServer();
   if(CopyHigh(_Symbol, PERIOD_M1, first_minute, now, highs) <= 0
      || CopyLow(_Symbol, PERIOD_M1, first_minute, now, lows) <= 0 || _Point <= 0.0)
      return;
   double high = highs[ArrayMaximum(highs)];
   double low = lows[ArrayMinimum(lows)];
   double favourable = is_buy ? high - open_price : open_price - low;
   double adverse = is_buy ? open_price - low : high - open_price;
   mfe_pts = MathMax(favourable / _Point, 0.0);
   mae_pts = MathMax(adverse / _Point, 0.0);
}

// Expects the position to be selected already.
string PositionJson(const int offset_s)
{
   bool is_buy = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
   double open_price = PositionGetDouble(POSITION_PRICE_OPEN);
   datetime opened = (datetime)PositionGetInteger(POSITION_TIME);
   double mae = 0.0, mfe = 0.0;
   PositionExcursions(is_buy, open_price, opened, mae, mfe);
   long open_utc = MathMax(ServerToUtc(opened, offset_s), (long)0);
   int step = 0;
   long limit_epoch = 0;
   TrackPlanFacts((ulong)PositionGetInteger(POSITION_IDENTIFIER), open_utc, step, limit_epoch);
   CJsonObject o;
   o.AddInt("ticket", PositionGetInteger(POSITION_TICKET));
   o.AddInt("magic", PositionGetInteger(POSITION_MAGIC));
   o.AddStr("side", is_buy ? "buy" : "sell");
   o.AddNum("volume", PositionGetDouble(POSITION_VOLUME), VOLUME_DIGITS);
   o.AddNum("price_open", open_price, _Digits);
   o.AddNum("sl", PositionGetDouble(POSITION_SL), _Digits);
   o.AddNum("tp", PositionGetDouble(POSITION_TP), _Digits);
   o.AddNum("profit", PositionGetDouble(POSITION_PROFIT), MoneyDigits());
   o.AddNum("swap", PositionGetDouble(POSITION_SWAP), MoneyDigits());
   o.AddInt("open_epoch", open_utc);
   o.AddStr("comment", SanitizeAscii(PositionGetString(POSITION_COMMENT), COMMENT_TEXT_MAX));
   o.AddNum("mae_points", mae, POINTS_DIGITS);
   o.AddNum("mfe_points", mfe, POINTS_DIGITS);
   o.AddInt("plan_step", step);
   o.AddInt("time_limit_epoch", limit_epoch);
   return o.Text();
}

string PositionsJson(const long magic, const int offset_s)
{
   CJsonArray rows;
   for(int i = PositionsTotal() - 1; i >= 0 && rows.Count() < V6_MAX_ROWS; i--)
   {
      if(PositionGetTicket(i) == 0 || !IsV6Position(magic))
         continue;
      rows.AddRaw(PositionJson(offset_s));
   }
   return rows.Text();
}

string PendingTypeName(const ENUM_ORDER_TYPE type)
{
   switch(type)
   {
      case ORDER_TYPE_BUY_LIMIT:  return "BUY_LIMIT";
      case ORDER_TYPE_SELL_LIMIT: return "SELL_LIMIT";
      case ORDER_TYPE_BUY_STOP:   return "BUY_STOP";
      case ORDER_TYPE_SELL_STOP:  return "SELL_STOP";
      default:                    return "";
   }
}

// Expects the order to be selected already.
string PendingOrderJson(const string type_name, const int offset_s)
{
   datetime expires = (datetime)OrderGetInteger(ORDER_TIME_EXPIRATION);
   long expires_utc = 0;
   if(expires > 0)
      expires_utc = MathMax(ServerToUtc(expires, offset_s), (long)0);
   CJsonObject o;
   o.AddInt("ticket", OrderGetInteger(ORDER_TICKET));
   o.AddInt("magic", OrderGetInteger(ORDER_MAGIC));
   o.AddStr("order_type", type_name);
   o.AddNum("price", OrderGetDouble(ORDER_PRICE_OPEN), _Digits);
   o.AddNum("sl", OrderGetDouble(ORDER_SL), _Digits);
   o.AddNum("tp", OrderGetDouble(ORDER_TP), _Digits);
   o.AddNum("volume", OrderGetDouble(ORDER_VOLUME_CURRENT), VOLUME_DIGITS);
   o.AddInt("expiration_epoch", expires_utc);
   o.AddStr("comment", SanitizeAscii(OrderGetString(ORDER_COMMENT), COMMENT_TEXT_MAX));
   return o.Text();
}

// Stop-limit orders have no slot in the contract and are left out.
string PendingOrdersJson(const long magic, const int offset_s)
{
   CJsonArray rows;
   for(int i = OrdersTotal() - 1; i >= 0 && rows.Count() < V6_MAX_ROWS; i--)
   {
      if(OrderGetTicket(i) == 0 || !IsV6Order(magic))
         continue;
      string type_name = PendingTypeName((ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE));
      if(type_name != "")
         rows.AddRaw(PendingOrderJson(type_name, offset_s));
   }
   return rows.Text();
}

// Counts for the poll heartbeat; floating P&L includes swap.
void V6Exposure(const long magic, int &positions, int &orders, double &floating)
{
   positions = 0;
   orders = 0;
   floating = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(PositionGetTicket(i) == 0 || !IsV6Position(magic))
         continue;
      positions++;
      floating += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
   }
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(OrderGetTicket(i) > 0 && IsV6Order(magic))
         orders++;
   }
}

#endif // QLIPV6_EXPOSURE_MQH
