//+------------------------------------------------------------------+
//| QlipV4_XAUUSD.mq5                                                |
//| V4 Liquidity Zone Entry with Partial TP + Break-Even             |
//| - M1 trigger, single quality scenario (LIQUIDITY_ZONE_ENTRY)     |
//| - 2 layer buy/sell_limit inside H1 zone, max 50 pip SL           |
//| - Partial TP @+30 pip closes 50% + SL moved to entry (BE)        |
//| - Runner cap @+100 pip                                            |
//| - Single basket only, magic 250551..250552                       |
//+------------------------------------------------------------------+
#property copyright "Qlip"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>

//--- Inputs
input string  InpPlanUrl              = "http://127.0.0.1:8765/v4/plan";
input string  InpEventUrl             = "http://127.0.0.1:8765/v1/events/trade-transaction";
input int     InpHttpTimeoutMs        = 2000;
input bool    InpEnableTrading        = true;
input double  InpTotalRiskPctOverride = 0.0;
input long    InpBaseMagic            = 250550;
input int     InpMaxBasketLifetimeMin = 30;
input int     InpCooldownMinutes      = 3;
input int     InpMaxPendingMinutes    = 30;
input int     InpSwingLookbackH1      = 20;

//--- Globals
CTrade        Trade;
int           h_atr_h1=-1, h_atr_m15=-1, h_atr_m5=-1, h_atr_m1=-1;
int           h_ma50_h1=-1;
datetime      g_last_m1_bar = 0;
datetime      g_cooldown_until = 0;
datetime      g_basket_opened_at = 0;

#define GV_PREFIX "QlipV4_"
#define GV_BASKET_OPENED   GV_PREFIX "BASKET_OPENED"
#define GV_COOLDOWN_UNTIL  GV_PREFIX "COOLDOWN_UNTIL"

string GVPartialKey(ulong ticket) { return GV_PREFIX + "PARTIAL_" + IntegerToString((long)ticket); }
string GVOriginalLotsKey(ulong ticket) { return GV_PREFIX + "OLOTS_" + IntegerToString((long)ticket); }

//+------------------------------------------------------------------+
int OnInit()
{
   Trade.SetExpertMagicNumber(InpBaseMagic);
   Trade.SetDeviationInPoints(20);
   Trade.SetTypeFilling(ORDER_FILLING_IOC);

   h_atr_h1   = iATR(_Symbol, PERIOD_H1, 14);
   h_atr_m15  = iATR(_Symbol, PERIOD_M15, 14);
   h_atr_m5   = iATR(_Symbol, PERIOD_M5, 14);
   h_atr_m1   = iATR(_Symbol, PERIOD_M1, 14);
   h_ma50_h1  = iMA(_Symbol, PERIOD_H1, 50, 0, MODE_EMA, PRICE_CLOSE);

   if(h_atr_h1==INVALID_HANDLE || h_atr_m5==INVALID_HANDLE)
   { Print("V4: indicator init failed"); return INIT_FAILED; }

   if(GlobalVariableCheck(GV_BASKET_OPENED))
      g_basket_opened_at = (datetime)GlobalVariableGet(GV_BASKET_OPENED);
   if(GlobalVariableCheck(GV_COOLDOWN_UNTIL))
      g_cooldown_until = (datetime)GlobalVariableGet(GV_COOLDOWN_UNTIL);

   PrintFormat("V4 EA initialized. basket_opened=%s cooldown_until=%s",
               TimeToString(g_basket_opened_at, TIME_DATE|TIME_MINUTES),
               TimeToString(g_cooldown_until, TIME_DATE|TIME_MINUTES));

   EventSetTimer(2);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   IndicatorRelease(h_atr_h1); IndicatorRelease(h_atr_m15);
   IndicatorRelease(h_atr_m5); IndicatorRelease(h_atr_m1);
   IndicatorRelease(h_ma50_h1);
}

//+------------------------------------------------------------------+
double GetBuf(int handle, int buffer, int shift)
{ double tmp[1]; if(CopyBuffer(handle, buffer, shift, 1, tmp) != 1) return EMPTY_VALUE; return tmp[0]; }

bool IsNewM1Bar()
{
   datetime t0 = (datetime)SeriesInfoInteger(_Symbol, PERIOD_M1, SERIES_LASTBAR_DATE);
   if(t0 == 0) return false;
   if(t0 != g_last_m1_bar) { g_last_m1_bar = t0; return true; }
   return false;
}

string IsoUtcNow()
{ MqlDateTime t; TimeToStruct(TimeGMT(), t); return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ", t.year, t.mon, t.day, t.hour, t.min, t.sec); }
string IsoUtcFromTime(datetime ts)
{ MqlDateTime t; TimeToStruct(ts, t); return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ", t.year, t.mon, t.day, t.hour, t.min, t.sec); }
string JNum(double v, int d=6) { return DoubleToString(v, d); }

double PipSize()
{
   double pt = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   // Standard convention: pip = 10 points on 5-digit FX / 2-digit metals.
   if(digits == 5 || digits == 3 || digits == 2) return pt * 10.0;
   return pt;
}

//+------------------------------------------------------------------+
//| Build V4 request                                                 |
//+------------------------------------------------------------------+
string BuildPlanRequestJson(const string request_id)
{
   double atr_h1 = GetBuf(h_atr_h1, 0, 1);
   double atr_m15= GetBuf(h_atr_m15, 0, 1);
   double atr_m5 = GetBuf(h_atr_m5, 0, 1);
   double atr_m1 = GetBuf(h_atr_m1, 0, 1);
   double ma50_h1= GetBuf(h_ma50_h1, 0, 1);

   if(atr_h1==EMPTY_VALUE || atr_m5==EMPTY_VALUE) return "";

   double swing_high_h1 = iHigh(_Symbol, PERIOD_H1, iHighest(_Symbol, PERIOD_H1, MODE_HIGH, InpSwingLookbackH1, 1));
   double swing_low_h1  = iLow(_Symbol, PERIOD_H1, iLowest(_Symbol, PERIOD_H1, MODE_LOW, InpSwingLookbackH1, 1));

   double close_h1 = iClose(_Symbol, PERIOD_H1, 1);
   double close_m15= iClose(_Symbol, PERIOD_M15, 1);
   double close_m5 = iClose(_Symbol, PERIOD_M5, 1);
   double close_m1 = iClose(_Symbol, PERIOD_M1, 1);

   double open_m5 = iOpen(_Symbol, PERIOD_M5, 1);
   double m5_direction = 0.0;
   if(close_m5 > open_m5) m5_direction = 1.0;
   else if(close_m5 < open_m5) m5_direction = -1.0;
   double m5_body_atr = (atr_m5 > 0) ? MathAbs(close_m5 - open_m5) / atr_m5 : 0.0;

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   long   spread_pts = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   long   stops_lvl = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   long   freeze_lvl= SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tick_value= SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double vol_step  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double vol_min   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vol_max   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double pip = PipSize();

   long   login   = AccountInfoInteger(ACCOUNT_LOGIN);
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   double freem   = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double mlevel  = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
   string curr    = AccountInfoString(ACCOUNT_CURRENCY);
   long   lev     = AccountInfoInteger(ACCOUNT_LEVERAGE);

   bool has_active = HasOurPositionOrOrder();

   double risk_pct = (InpTotalRiskPctOverride > 0.0) ? InpTotalRiskPctOverride : 0.0;

   string s = "{";
   s += "\"schema_version\":\"v4-plan-request.v1\",";
   s += "\"request_id\":\""+request_id+"\",";
   s += "\"mode\":\"live\",";
   s += "\"timestamp_utc\":\""+IsoUtcNow()+"\",";
   s += "\"symbol\":\""+_Symbol+"\",";
   s += "\"timeframe\":\"M1\",";
   s += "\"bar_index\":"+IntegerToString((long)g_last_m1_bar)+",";

   s += "\"market\":{";
   s += "\"bid\":"+JNum(bid,digits)+",";
   s += "\"ask\":"+JNum(ask,digits)+",";
   s += "\"last_close\":"+JNum(close_m1,digits)+",";
   s += "\"spread_points\":"+IntegerToString(spread_pts)+",";
   s += "\"digits\":"+IntegerToString(digits)+",";
   s += "\"stops_level_points\":"+IntegerToString(stops_lvl)+",";
   s += "\"freeze_level_points\":"+IntegerToString(freeze_lvl)+",";
   s += "\"tick_size\":"+JNum(tick_size,8)+",";
   s += "\"tick_value\":"+JNum(tick_value,6);
   s += "},";

   s += "\"account\":{";
   s += "\"login\":\""+IntegerToString(login)+"\",";
   s += "\"balance\":"+JNum(balance,2)+",";
   s += "\"equity\":"+JNum(equity,2)+",";
   s += "\"free_margin\":"+JNum(freem,2)+",";
   s += "\"margin_level\":"+JNum(mlevel,2)+",";
   s += "\"currency\":\""+curr+"\",";
   s += "\"leverage\":"+IntegerToString(lev);
   s += "},";

   s += "\"position\":{";
   s += "\"net_position\":0.0,\"avg_price\":0.0,\"floating_pnl\":0.0,";
   s += "\"open_positions_count\":"+IntegerToString(PositionsTotal())+",";
   s += "\"pending_orders_count\":"+IntegerToString(OrdersTotal())+",";
   s += "\"side\":\"flat\"";
   s += "},";

   s += "\"risk_state\":{";
   s += "\"max_risk_per_trade_pct\":1.0,";
   s += "\"max_symbol_exposure_lots\":0.50,";
   s += "\"daily_drawdown_pct\":0.0,";
   s += "\"consecutive_losses\":0,";
   s += "\"trading_halted\":false";
   s += "},";

   s += "\"features\":{";
   s += "\"context_tf\":{";
   s += "\"adx_strength\":0.0,";
   s += "\"di_balance\":0.0,";
   s += "\"atr_pct\":"+JNum(atr_h1/MathMax(close_h1,1e-9),6)+",";
   s += "\"atr_abs_h1\":"+JNum(atr_h1,digits)+",";
   s += "\"swing_high_h1\":"+JNum(swing_high_h1,digits)+",";
   s += "\"swing_low_h1\":"+JNum(swing_low_h1,digits);
   s += "},";

   s += "\"decision_tf\":{";
   s += "\"close_m1\":"+JNum(close_m1,digits);
   s += "},";

   s += "\"confirm_tf\":{";
   s += "\"close_m15\":"+JNum(close_m15,digits)+",";
   s += "\"close_m5\":"+JNum(close_m5,digits)+",";
   s += "\"m5_direction\":"+JNum(m5_direction,1)+",";
   s += "\"m5_body_atr\":"+JNum(m5_body_atr,4);
   s += "},";

   s += "\"execution_tf\":{";
   s += "\"spread_to_atr_ratio\":"+JNum((atr_m1>0)?(spread_pts*SymbolInfoDouble(_Symbol,SYMBOL_POINT))/atr_m1:0.0,4)+",";
   s += "\"atr_abs\":"+JNum(atr_m1,digits)+",";
   s += "\"atr_abs_m15\":"+JNum(atr_m15,digits)+",";
   s += "\"atr_abs_m5\":"+JNum(atr_m5,digits)+",";
   s += "\"atr_abs_m1\":"+JNum(atr_m1,digits)+",";
   s += "\"pip_size\":"+JNum(pip,5)+",";
   s += "\"volume_step\":"+JNum(vol_step,3)+",";
   s += "\"volume_min\":"+JNum(vol_min,3)+",";
   s += "\"volume_max\":"+JNum(vol_max,3);
   s += "}";
   s += "},";

   s += "\"has_active_basket\":"+(has_active?"true":"false");

   if(risk_pct > 0.0)
      s += ",\"total_risk_pct_override\":"+JNum(risk_pct,4);

   s += "}";
   return s;
}

//+------------------------------------------------------------------+
//| JSON parsing                                                     |
//+------------------------------------------------------------------+
string FindStringField(const string &j, const string &key, int from=0)
{
   string needle = "\"" + key + "\":\"";
   int p = StringFind(j, needle, from);
   if(p < 0) return "";
   int s = p + StringLen(needle);
   int e = StringFind(j, "\"", s);
   if(e < 0) return "";
   return StringSubstr(j, s, e - s);
}
double FindNumberField(const string &j, const string &key, double defv=0.0, int from=0)
{
   string needle = "\"" + key + "\":";
   int p = StringFind(j, needle, from);
   if(p < 0) return defv;
   int s = p + StringLen(needle);
   int e = s; int n = StringLen(j);
   while(e < n) { ushort c = StringGetCharacter(j, e); if(c==',' || c=='}' || c==']' || c=='\n' || c=='\r') break; e++; }
   string raw = StringSubstr(j, s, e - s);
   StringReplace(raw, " ", "");
   if(raw=="null" || raw=="") return defv;
   return StringToDouble(raw);
}

bool CallAdapterOnce(const string &url, const string &body, string &out, int &http_code)
{
   string headers = "Content-Type: application/json\r\nConnection: close\r\n";
   char post[]; char result[]; string rh;
   StringToCharArray(body, post, 0, StringLen(body), CP_UTF8);
   ResetLastError();
   http_code = WebRequest("POST", url, headers, InpHttpTimeoutMs, post, result, rh);
   if(http_code == -1) { PrintFormat("V4 WebRequest err=%d url=%s", GetLastError(), url); return false; }
   out = CharArrayToString(result, 0, -1, CP_UTF8);
   return http_code == 200;
}

bool CallAdapter(const string &url, const string &body, string &out)
{
   int code = 0;
   if(CallAdapterOnce(url, body, out, code)) return true;
   PrintFormat("V4 adapter retry (first try code=%d)", code);
   Sleep(150);
   if(CallAdapterOnce(url, body, out, code)) return true;
   PrintFormat("V4 adapter failed after retry (code=%d)", code);
   return false;
}

//+------------------------------------------------------------------+
//| Order placement                                                  |
//+------------------------------------------------------------------+
ENUM_ORDER_TYPE MapOrderType(const string &t)
{
   if(t=="buy_limit")  return ORDER_TYPE_BUY_LIMIT;
   if(t=="sell_limit") return ORDER_TYPE_SELL_LIMIT;
   if(t=="buy_stop")   return ORDER_TYPE_BUY_STOP;
   if(t=="sell_stop")  return ORDER_TYPE_SELL_STOP;
   return WRONG_VALUE;
}

bool PlaceLayer(const string &otype, double price, double lots, double sl, long magic)
{
   MqlTradeRequest req; MqlTradeResult res; MqlTradeCheckResult chk;
   ZeroMemory(req); ZeroMemory(res); ZeroMemory(chk);
   req.action       = TRADE_ACTION_PENDING;
   req.symbol       = _Symbol;
   req.volume       = lots;
   req.type         = MapOrderType(otype);
   req.price        = price;
   req.sl           = (sl > 0) ? sl : 0.0;
   req.tp           = 0.0;       // V4 no static TP; partial/runner managed by EA.
   req.deviation    = 20;
   req.magic        = magic;
   req.comment      = "qlip-v4";
   req.type_time    = ORDER_TIME_GTC;
   req.type_filling = ORDER_FILLING_RETURN;

   if(!OrderCheck(req, chk)) { PrintFormat("V4 OrderCheck fail magic=%d retcode=%u (%s)", magic, chk.retcode, chk.comment); return false; }
   if(!OrderSend(req, res))  { PrintFormat("V4 OrderSend fail magic=%d retcode=%u (%s)", magic, res.retcode, res.comment); return false; }
   PrintFormat("V4 layer placed magic=%d type=%s @%.2f lots=%.2f sl=%.2f", magic, otype, price, lots, sl);
   return true;
}

//+------------------------------------------------------------------+
//| State helpers                                                    |
//+------------------------------------------------------------------+
bool IsOurMagic(long m) { return m >= InpBaseMagic && m <= InpBaseMagic + 4; }

bool HasOurPositionOrOrder()
{
   for(int i=0; i<PositionsTotal(); i++)
   {
      ulong tk = PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionSelectByTicket(tk))
      {
         long m = PositionGetInteger(POSITION_MAGIC);
         if(IsOurMagic(m) && PositionGetString(POSITION_SYMBOL)==_Symbol) return true;
      }
   }
   for(int i=0; i<OrdersTotal(); i++)
   {
      ulong tk = OrderGetTicket(i);
      if(tk==0) continue;
      if(OrderSelect(tk))
      {
         long m = OrderGetInteger(ORDER_MAGIC);
         if(IsOurMagic(m) && OrderGetString(ORDER_SYMBOL)==_Symbol) return true;
      }
   }
   return false;
}

void ProcessPlanResponse(const string &resp)
{
   string status   = FindStringField(resp, "status");
   string scenario = FindStringField(resp, "scenario");
   string side     = FindStringField(resp, "side_bias");
   double conf     = FindNumberField(resp, "confidence", 0.0);

   PrintFormat("[V4 Plan] status=%s scen=%s side=%s conf=%.2f", status, scenario, side, conf);

   if(status != "ok") return;
   if(scenario == "NONE" || scenario == "") return;
   if(!InpEnableTrading) return;
   if(HasOurPositionOrOrder()) { Print("V4: basket already active; skip plan."); return; }

   int placed = 0;
   for(int i=1; i<=2; i++)
   {
      string nd = "\"layer_id\":"+IntegerToString(i);
      int p = StringFind(resp, nd);
      if(p < 0) break;
      int section_end = StringFind(resp, "}", p);
      if(section_end < 0) section_end = StringLen(resp);
      string section = StringSubstr(resp, p, section_end - p + 1);

      string otype = FindStringField(section, "order_type");
      double price = FindNumberField(section, "price", 0.0);
      double lots  = FindNumberField(section, "lots", 0.0);
      double sl    = FindNumberField(section, "sl", 0.0);
      long   magic = (long)FindNumberField(section, "magic", InpBaseMagic + i);

      if(otype == "" || price <= 0 || lots <= 0) continue;
      if(PlaceLayer(otype, price, lots, sl, magic)) placed++;
   }

   if(placed > 0)
   {
      g_basket_opened_at = TimeCurrent();
      GlobalVariableSet(GV_BASKET_OPENED, (double)g_basket_opened_at);
      PrintFormat("V4 basket opened: %d layers placed at %s", placed, TimeToString(g_basket_opened_at));
   }
}

//+------------------------------------------------------------------+
//| Per-position partial TP + BE                                     |
//+------------------------------------------------------------------+
double PositionProfitPips(const ulong ticket)
{
   if(!PositionSelectByTicket(ticket)) return 0.0;
   long type = PositionGetInteger(POSITION_TYPE);
   double entry = PositionGetDouble(POSITION_PRICE_OPEN);
   double cur = (type==POSITION_TYPE_BUY) ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                                          : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double diff = (type==POSITION_TYPE_BUY) ? (cur - entry) : (entry - cur);
   double pip = PipSize();
   return (pip > 0) ? diff / pip : 0.0;
}

void ManagePosition(const ulong ticket)
{
   if(!PositionSelectByTicket(ticket)) return;
   long m = PositionGetInteger(POSITION_MAGIC);
   if(!IsOurMagic(m)) return;
   if(PositionGetString(POSITION_SYMBOL) != _Symbol) return;

   double profit_pips = PositionProfitPips(ticket);
   double entry = PositionGetDouble(POSITION_PRICE_OPEN);
   double cur_sl = PositionGetDouble(POSITION_SL);
   double cur_tp = PositionGetDouble(POSITION_TP);
   double cur_volume = PositionGetDouble(POSITION_VOLUME);
   long type = PositionGetInteger(POSITION_TYPE);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

   bool partial_done = (GlobalVariableCheck(GVPartialKey(ticket)) && GlobalVariableGet(GVPartialKey(ticket)) > 0.5);

   // Runner cap
   if(profit_pips >= 100.0)
   { Trade.PositionClose(ticket); PrintFormat("V4 ticket=%I64u closed at +%.1f pip (runner cap)", ticket, profit_pips); return; }

   // Partial TP @ +30 pip
   if(!partial_done && profit_pips >= 30.0)
   {
      double vol_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
      double vol_min  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);

      // Remember original lots for tracking
      if(!GlobalVariableCheck(GVOriginalLotsKey(ticket)))
         GlobalVariableSet(GVOriginalLotsKey(ticket), cur_volume);

      double close_vol = MathFloor(cur_volume * 0.5 / vol_step) * vol_step;
      if(close_vol < vol_min) close_vol = vol_min;
      if(close_vol > cur_volume) close_vol = cur_volume;

      bool partial_ok = false;
      if(close_vol < cur_volume - 1e-9)
         partial_ok = Trade.PositionClosePartial(ticket, close_vol);
      else
         partial_ok = Trade.PositionClose(ticket);   // tiny position → close all

      if(partial_ok)
      {
         GlobalVariableSet(GVPartialKey(ticket), 1.0);
         PrintFormat("V4 ticket=%I64u partial closed %.2f lots at +%.1f pip", ticket, close_vol, profit_pips);

         // Move SL to entry (BE) on remaining position. Re-select since close may have changed state.
         if(PositionSelectByTicket(ticket))
         {
            double new_sl = NormalizeDouble(entry, digits);
            // Only modify if BE is better than current SL.
            bool improve = false;
            if(type==POSITION_TYPE_BUY && (cur_sl == 0.0 || new_sl > cur_sl)) improve = true;
            if(type==POSITION_TYPE_SELL && (cur_sl == 0.0 || new_sl < cur_sl)) improve = true;
            if(improve)
            {
               if(Trade.PositionModify(ticket, new_sl, cur_tp))
                  PrintFormat("V4 ticket=%I64u SL moved to BE @%.2f", ticket, new_sl);
               else
                  PrintFormat("V4 ticket=%I64u SL-to-BE failed retcode=%u", ticket, Trade.ResultRetcode());
            }
         }
      }
   }
}

void EvaluateAllOurPositions()
{
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionSelectByTicket(tk))
      {
         long m = PositionGetInteger(POSITION_MAGIC);
         if(IsOurMagic(m) && PositionGetString(POSITION_SYMBOL)==_Symbol)
            ManagePosition(tk);
      }
   }
}

void CloseBasket(const string &reason)
{
   PrintFormat("V4 closing basket: %s", reason);
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionSelectByTicket(tk))
      {
         long m = PositionGetInteger(POSITION_MAGIC);
         if(IsOurMagic(m) && PositionGetString(POSITION_SYMBOL)==_Symbol)
            Trade.PositionClose(tk);
      }
   }
   for(int i = OrdersTotal()-1; i >= 0; i--)
   {
      ulong tk = OrderGetTicket(i);
      if(tk == 0) continue;
      if(OrderSelect(tk))
      {
         long m = OrderGetInteger(ORDER_MAGIC);
         if(IsOurMagic(m) && OrderGetString(ORDER_SYMBOL)==_Symbol)
            Trade.OrderDelete(tk);
      }
   }
   g_cooldown_until = TimeCurrent() + InpCooldownMinutes * 60;
   GlobalVariableSet(GV_COOLDOWN_UNTIL, (double)g_cooldown_until);
   g_basket_opened_at = 0;
   GlobalVariableDel(GV_BASKET_OPENED);
}

void CheckBasketLifetime()
{
   if(g_basket_opened_at <= 0) return;
   if(!HasOurPositionOrOrder())
   {
      g_basket_opened_at = 0;
      GlobalVariableDel(GV_BASKET_OPENED);
      return;
   }
   if(TimeCurrent() - g_basket_opened_at >= InpMaxBasketLifetimeMin * 60)
      CloseBasket("Max lifetime reached");
}

void CleanupStalePendings()
{
   datetime cutoff = TimeCurrent() - InpMaxPendingMinutes * 60;
   for(int i = OrdersTotal()-1; i >= 0; i--)
   {
      ulong tk = OrderGetTicket(i);
      if(tk==0) continue;
      if(OrderSelect(tk))
      {
         long m = OrderGetInteger(ORDER_MAGIC);
         datetime setup = (datetime)OrderGetInteger(ORDER_TIME_SETUP);
         if(IsOurMagic(m) && OrderGetString(ORDER_SYMBOL)==_Symbol && setup > 0 && setup < cutoff)
            Trade.OrderDelete(tk);
      }
   }
}

//+------------------------------------------------------------------+
void OnTimer()
{
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) return;
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED)) return;

   EvaluateAllOurPositions();
   CleanupStalePendings();
   CheckBasketLifetime();

   if(TimeCurrent() < g_cooldown_until) return;
   if(!IsNewM1Bar()) return;
   if(HasOurPositionOrOrder()) return;   // single basket only

   string rid = StringFormat("%s-V4M1-%s", _Symbol, IsoUtcFromTime(g_last_m1_bar));
   string body = BuildPlanRequestJson(rid);
   if(body == "") { Print("V4 request build failed"); return; }

   string resp;
   if(!CallAdapter(InpPlanUrl, body, resp)) return;
   ProcessPlanResponse(resp);
}

void OnTick()
{
   EvaluateAllOurPositions();
}

//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction& trans,
                        const MqlTradeRequest&     request,
                        const MqlTradeResult&      result)
{
   string trans_type = EnumToString((ENUM_TRADE_TRANSACTION_TYPE)trans.type);
   string body = "{";
   body += "\"schema_version\":\"trade-transaction-event.v1\",";
   body += "\"request_id\":\""+_Symbol+"-v4tx-"+IntegerToString((long)TimeGMT())+"\",";
   body += "\"symbol\":\""+_Symbol+"\",";
   body += "\"trans_type\":\""+trans_type+"\",";
   body += "\"order\":"+IntegerToString((long)trans.order)+",";
   body += "\"deal\":"+IntegerToString((long)trans.deal)+",";
   body += "\"position\":"+IntegerToString((long)trans.position)+",";
   body += "\"retcode\":"+IntegerToString((long)result.retcode)+",";
   body += "\"comment\":\""+request.comment+"\",";
   body += "\"time_utc\":\""+IsoUtcNow()+"\"";
   body += "}";
   string headers = "Content-Type: application/json\r\n";
   char post[]; char res[]; string rh;
   StringToCharArray(body, post, 0, StringLen(body), CP_UTF8);
   ResetLastError();
   WebRequest("POST", InpEventUrl, headers, 1000, post, res, rh);
}
//+------------------------------------------------------------------+
