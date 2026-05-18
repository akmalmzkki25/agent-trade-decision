//+------------------------------------------------------------------+
//| QlipBulkLayer_XAUUSD.mq5                                         |
//| Phase 2 EA: M1 trigger, multi-timeframe (M1+M5+M15+H1),          |
//| bulk layering pending orders, basket TP + invalidation.          |
//|                                                                  |
//| Magic base = 250519, per-layer magic = 250520..250524.            |
//| WebRequest URL allowlist required: http://127.0.0.1:8765         |
//+------------------------------------------------------------------+
#property copyright "Qlip"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>

//--- Inputs
input string  InpPlanUrl             = "http://127.0.0.1:8765/v2/plan";
input string  InpEventUrl            = "http://127.0.0.1:8765/v1/events/trade-transaction";
input int     InpHttpTimeoutMs       = 2000;
input bool    InpEnableTrading       = true;
input double  InpTotalRiskPctOverride= 0.0;     // 0 = use server default (1%)
input long    InpBaseMagic           = 250519;
input int     InpCooldownMinutes     = 5;
input int     InpBreakoutLookbackM15 = 20;
input int     InpSwingLookbackH1     = 20;
input int     InpMaxPendingMinutes   = 30;   // cancel stale pendings older than this

//--- Globals
CTrade        Trade;
int           h_adx_h1=-1, h_atr_h1=-1, h_ma50_h1=-1;
int           h_adx_m15=-1, h_atr_m15=-1, h_bb_m15=-1, h_ma50_m15=-1;
int           h_ema20_m5=-1, h_ema50_m5=-1, h_atr_m5=-1, h_rsi_m5=-1, h_bb_m5=-1;
int           h_rsi_m1=-1, h_bb_m1=-1, h_atr_m1=-1;
datetime      g_last_m1_bar = 0;
datetime      g_cooldown_until = 0;

//--- Basket state (persisted via GlobalVariable)
double        g_basket_inv_price = 0.0;
double        g_basket_tp_pct    = 0.0;
double        g_basket_start_eq  = 0.0;
bool          g_basket_active    = false;
string        g_basket_side      = "";

#define GV_INVPRICE "QlipBulkLayer_InvPrice"
#define GV_TPPCT    "QlipBulkLayer_TpPct"
#define GV_STARTEQ  "QlipBulkLayer_StartEq"
#define GV_SIDE     "QlipBulkLayer_Side"

//+------------------------------------------------------------------+
int OnInit()
{
   Trade.SetExpertMagicNumber(InpBaseMagic);
   Trade.SetDeviationInPoints(20);
   Trade.SetTypeFilling(ORDER_FILLING_IOC);

   // H1
   h_adx_h1   = iADX(_Symbol, PERIOD_H1, 14);
   h_atr_h1   = iATR(_Symbol, PERIOD_H1, 14);
   h_ma50_h1  = iMA(_Symbol, PERIOD_H1, 50, 0, MODE_EMA, PRICE_CLOSE);
   // M15
   h_adx_m15  = iADX(_Symbol, PERIOD_M15, 14);
   h_atr_m15  = iATR(_Symbol, PERIOD_M15, 14);
   h_bb_m15   = iBands(_Symbol, PERIOD_M15, 20, 0, 2.0, PRICE_CLOSE);
   h_ma50_m15 = iMA(_Symbol, PERIOD_M15, 50, 0, MODE_EMA, PRICE_CLOSE);
   // M5
   h_ema20_m5 = iMA(_Symbol, PERIOD_M5, 20, 0, MODE_EMA, PRICE_CLOSE);
   h_ema50_m5 = iMA(_Symbol, PERIOD_M5, 50, 0, MODE_EMA, PRICE_CLOSE);
   h_atr_m5   = iATR(_Symbol, PERIOD_M5, 14);
   h_rsi_m5   = iRSI(_Symbol, PERIOD_M5, 14, PRICE_CLOSE);
   h_bb_m5    = iBands(_Symbol, PERIOD_M5, 20, 0, 2.0, PRICE_CLOSE);
   // M1
   h_rsi_m1   = iRSI(_Symbol, PERIOD_M1, 14, PRICE_CLOSE);
   h_bb_m1    = iBands(_Symbol, PERIOD_M1, 20, 0, 2.0, PRICE_CLOSE);
   h_atr_m1   = iATR(_Symbol, PERIOD_M1, 14);

   if(h_adx_h1==INVALID_HANDLE || h_atr_h1==INVALID_HANDLE || h_ma50_h1==INVALID_HANDLE ||
      h_adx_m15==INVALID_HANDLE || h_atr_m15==INVALID_HANDLE || h_bb_m15==INVALID_HANDLE ||
      h_ma50_m15==INVALID_HANDLE || h_ema20_m5==INVALID_HANDLE || h_ema50_m5==INVALID_HANDLE ||
      h_atr_m5==INVALID_HANDLE || h_rsi_m5==INVALID_HANDLE || h_bb_m5==INVALID_HANDLE ||
      h_rsi_m1==INVALID_HANDLE || h_bb_m1==INVALID_HANDLE || h_atr_m1==INVALID_HANDLE)
   {
      Print("Failed to create indicator handles.");
      return(INIT_FAILED);
   }

   // Restore basket state if any.
   if(GlobalVariableCheck(GV_INVPRICE))
   {
      g_basket_inv_price = GlobalVariableGet(GV_INVPRICE);
      g_basket_tp_pct    = GlobalVariableGet(GV_TPPCT);
      g_basket_start_eq  = GlobalVariableGet(GV_STARTEQ);
      g_basket_side      = (GlobalVariableGet(GV_SIDE) > 0) ? "buy" : "sell";
      g_basket_active    = (g_basket_inv_price > 0.0);
      PrintFormat("Restored basket state: active=%s inv=%.2f tp_pct=%.3f side=%s",
                  g_basket_active?"true":"false", g_basket_inv_price, g_basket_tp_pct, g_basket_side);
   }

   long mode = AccountInfoInteger(ACCOUNT_MARGIN_MODE);
   PrintFormat("QlipBulkLayer initialized. ACCOUNT_MARGIN_MODE=%d", (int)mode);

   EventSetTimer(2);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   IndicatorRelease(h_adx_h1); IndicatorRelease(h_atr_h1); IndicatorRelease(h_ma50_h1);
   IndicatorRelease(h_adx_m15); IndicatorRelease(h_atr_m15); IndicatorRelease(h_bb_m15); IndicatorRelease(h_ma50_m15);
   IndicatorRelease(h_ema20_m5); IndicatorRelease(h_ema50_m5); IndicatorRelease(h_atr_m5);
   IndicatorRelease(h_rsi_m5); IndicatorRelease(h_bb_m5);
   IndicatorRelease(h_rsi_m1); IndicatorRelease(h_bb_m1); IndicatorRelease(h_atr_m1);
}

//+------------------------------------------------------------------+
//| Helpers                                                          |
//+------------------------------------------------------------------+
double GetBuf(int handle, int buffer, int shift)
{
   double tmp[1];
   if(CopyBuffer(handle, buffer, shift, 1, tmp) != 1) return(EMPTY_VALUE);
   return tmp[0];
}

bool IsNewM1Bar()
{
   datetime t0 = (datetime)SeriesInfoInteger(_Symbol, PERIOD_M1, SERIES_LASTBAR_DATE);
   if(t0 == 0) return false;
   if(t0 != g_last_m1_bar)
   {
      g_last_m1_bar = t0;
      return true;
   }
   return false;
}

double GetHighestM15(int shift, int count)
{
   double rates[];
   if(CopyHigh(_Symbol, PERIOD_M15, shift, count, rates) <= 0) return 0.0;
   double mx = rates[0];
   for(int i=1;i<ArraySize(rates);i++) if(rates[i] > mx) mx = rates[i];
   return mx;
}
double GetLowestM15(int shift, int count)
{
   double rates[];
   if(CopyLow(_Symbol, PERIOD_M15, shift, count, rates) <= 0) return 0.0;
   double mn = rates[0];
   for(int i=1;i<ArraySize(rates);i++) if(rates[i] < mn) mn = rates[i];
   return mn;
}
double GetHighestH1(int shift, int count)
{
   double rates[];
   if(CopyHigh(_Symbol, PERIOD_H1, shift, count, rates) <= 0) return 0.0;
   double mx = rates[0];
   for(int i=1;i<ArraySize(rates);i++) if(rates[i] > mx) mx = rates[i];
   return mx;
}
double GetLowestH1(int shift, int count)
{
   double rates[];
   if(CopyLow(_Symbol, PERIOD_H1, shift, count, rates) <= 0) return 0.0;
   double mn = rates[0];
   for(int i=1;i<ArraySize(rates);i++) if(rates[i] < mn) mn = rates[i];
   return mn;
}

string IsoUtcNow()
{
   MqlDateTime t; TimeToStruct(TimeGMT(), t);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ", t.year, t.mon, t.day, t.hour, t.min, t.sec);
}
string IsoUtcFromTime(datetime ts)
{
   MqlDateTime t; TimeToStruct(ts, t);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ", t.year, t.mon, t.day, t.hour, t.min, t.sec);
}
string JsonNum(double v, int digits=6) { return DoubleToString(v, digits); }

//+------------------------------------------------------------------+
//| Build plan request JSON                                          |
//+------------------------------------------------------------------+
string BuildPlanRequestJson(const string request_id)
{
   // H1
   double adx_h1   = GetBuf(h_adx_h1, 0, 1);
   double dip_h1   = GetBuf(h_adx_h1, 1, 1);
   double dim_h1   = GetBuf(h_adx_h1, 2, 1);
   double atr_h1   = GetBuf(h_atr_h1, 0, 1);
   double ma50_h1  = GetBuf(h_ma50_h1, 0, 1);
   double close_h1 = iClose(_Symbol, PERIOD_H1, 1);
   double swing_high_h1 = GetHighestH1(1, InpSwingLookbackH1);
   double swing_low_h1  = GetLowestH1(1, InpSwingLookbackH1);

   // M15
   double adx_m15  = GetBuf(h_adx_m15, 0, 1);
   double dip_m15  = GetBuf(h_adx_m15, 1, 1);
   double dim_m15  = GetBuf(h_adx_m15, 2, 1);
   double atr_m15  = GetBuf(h_atr_m15, 0, 1);
   double bb_up_m15= GetBuf(h_bb_m15, 1, 1);
   double bb_dn_m15= GetBuf(h_bb_m15, 2, 1);
   double bb_mid_m15=GetBuf(h_bb_m15, 0, 1);
   double ma50_m15 = GetBuf(h_ma50_m15, 0, 1);
   double brk_high = GetHighestM15(1, InpBreakoutLookbackM15);
   double brk_low  = GetLowestM15(1, InpBreakoutLookbackM15);
   double bb_width_m15 = (bb_mid_m15>0) ? (bb_up_m15 - bb_dn_m15)/bb_mid_m15 : 0.0;
   // Rough rolling percentile proxy: scale width by ATR/Close (lower = compressed).
   double bb_width_pct = MathMin(1.0, MathMax(0.0, bb_width_m15 / 0.02)); // empirical scaling

   // M5
   double ema20_m5 = GetBuf(h_ema20_m5, 0, 1);
   double ema50_m5 = GetBuf(h_ema50_m5, 0, 1);
   double atr_m5   = GetBuf(h_atr_m5, 0, 1);
   double rsi_m5   = GetBuf(h_rsi_m5, 0, 1);
   double bb_up_m5 = GetBuf(h_bb_m5, 1, 1);
   double bb_dn_m5 = GetBuf(h_bb_m5, 2, 1);
   double bb_mid_m5= GetBuf(h_bb_m5, 0, 1);
   double close_m5 = iClose(_Symbol, PERIOD_M5, 1);

   // M1
   double rsi_m1   = GetBuf(h_rsi_m1, 0, 1);
   double bb_up_m1 = GetBuf(h_bb_m1, 1, 1);
   double bb_dn_m1 = GetBuf(h_bb_m1, 2, 1);
   double bb_mid_m1= GetBuf(h_bb_m1, 0, 1);
   double atr_m1   = GetBuf(h_atr_m1, 0, 1);
   double close_m1 = iClose(_Symbol, PERIOD_M1, 1);

   if(adx_h1==EMPTY_VALUE || atr_h1==EMPTY_VALUE || atr_m15==EMPTY_VALUE ||
      atr_m5==EMPTY_VALUE || atr_m1==EMPTY_VALUE)
      return "";

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   long spread_pts = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   long stops_lvl  = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   long freeze_lvl = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   double tick_size  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double vol_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double vol_min  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vol_max  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   long   login    = AccountInfoInteger(ACCOUNT_LOGIN);
   double balance  = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity   = AccountInfoDouble(ACCOUNT_EQUITY);
   double freem    = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double mlevel   = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
   string curr     = AccountInfoString(ACCOUNT_CURRENCY);
   long   lev      = AccountInfoInteger(ACCOUNT_LEVERAGE);

   // DI balance & ratios
   double di_sum_h1 = dip_h1 + dim_h1;
   double di_balance_h1 = (di_sum_h1 > 1e-9) ? (dip_h1 - dim_h1) / di_sum_h1 : 0.0;
   double half_bb_m5 = MathMax((bb_up_m5 - bb_dn_m5)/2.0, 1e-9);
   double bb_pos_m5  = (close_m5 - bb_mid_m5) / half_bb_m5;
   double half_bb_m1 = MathMax((bb_up_m1 - bb_dn_m1)/2.0, 1e-9);
   double bb_pos_m1  = (close_m1 - bb_mid_m1) / half_bb_m1;

   double risk_pct = (InpTotalRiskPctOverride > 0.0) ? InpTotalRiskPctOverride : 0.0;

   string s = "{";
   s += "\"schema_version\":\"layer-plan-request.v1\",";
   s += "\"request_id\":\""+request_id+"\",";
   s += "\"mode\":\"live\",";
   s += "\"timestamp_utc\":\""+IsoUtcNow()+"\",";
   s += "\"symbol\":\""+_Symbol+"\",";
   s += "\"timeframe\":\"M1\",";
   s += "\"bar_index\":"+IntegerToString((long)g_last_m1_bar)+",";

   s += "\"market\":{";
   s += "\"bid\":"+JsonNum(bid,digits)+",";
   s += "\"ask\":"+JsonNum(ask,digits)+",";
   s += "\"last_close\":"+JsonNum(close_m1,digits)+",";
   s += "\"spread_points\":"+IntegerToString(spread_pts)+",";
   s += "\"digits\":"+IntegerToString(digits)+",";
   s += "\"stops_level_points\":"+IntegerToString(stops_lvl)+",";
   s += "\"freeze_level_points\":"+IntegerToString(freeze_lvl)+",";
   s += "\"tick_size\":"+JsonNum(tick_size,8)+",";
   s += "\"tick_value\":"+JsonNum(tick_value,6);
   s += "},";

   s += "\"account\":{";
   s += "\"login\":\""+IntegerToString(login)+"\",";
   s += "\"balance\":"+JsonNum(balance,2)+",";
   s += "\"equity\":"+JsonNum(equity,2)+",";
   s += "\"free_margin\":"+JsonNum(freem,2)+",";
   s += "\"margin_level\":"+JsonNum(mlevel,2)+",";
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
   s += "\"adx_strength\":"+JsonNum(adx_h1/100.0,4)+",";
   s += "\"di_balance\":"+JsonNum(di_balance_h1,4)+",";
   s += "\"ma_gap_atr\":"+JsonNum((close_h1-ma50_h1)/atr_h1,4)+",";
   s += "\"atr_pct\":"+JsonNum(atr_h1/MathMax(close_h1,1e-9),6)+",";
   s += "\"atr_abs_h1\":"+JsonNum(atr_h1,digits)+",";
   s += "\"swing_high_h1\":"+JsonNum(swing_high_h1,digits)+",";
   s += "\"swing_low_h1\":"+JsonNum(swing_low_h1,digits);
   s += "},";

   s += "\"decision_tf\":{";
   s += "\"rsi_centered\":"+JsonNum((rsi_m1-50.0)/50.0,4)+",";
   s += "\"bb_pos\":"+JsonNum(bb_pos_m1,4);
   s += "},";

   s += "\"confirm_tf\":{";
   s += "\"rsi_centered\":"+JsonNum((rsi_m5-50.0)/50.0,4)+",";
   s += "\"bb_pos\":"+JsonNum(bb_pos_m5,4)+",";
   s += "\"ema20_m5\":"+JsonNum(ema20_m5,digits)+",";
   s += "\"ema50_m5\":"+JsonNum(ema50_m5,digits)+",";
   s += "\"ema50_m15\":"+JsonNum(ma50_m15,digits)+",";
   s += "\"adx_strength_m15\":"+JsonNum(adx_m15/100.0,4)+",";
   s += "\"bb_width_pct\":"+JsonNum(bb_width_pct,4)+",";
   s += "\"breakout_high_m15\":"+JsonNum(brk_high,digits)+",";
   s += "\"breakout_low_m15\":"+JsonNum(brk_low,digits);
   s += "},";

   s += "\"execution_tf\":{";
   s += "\"spread_to_atr_ratio\":"+JsonNum((atr_m1>0)?(spread_pts*SymbolInfoDouble(_Symbol,SYMBOL_POINT))/atr_m1:0.0,4)+",";
   s += "\"atr_abs\":"+JsonNum(atr_m15,digits)+",";
   s += "\"atr_abs_m15\":"+JsonNum(atr_m15,digits)+",";
   s += "\"atr_abs_m5\":"+JsonNum(atr_m5,digits)+",";
   s += "\"atr_abs_m1\":"+JsonNum(atr_m1,digits)+",";
   s += "\"volume_step\":"+JsonNum(vol_step,3)+",";
   s += "\"volume_min\":"+JsonNum(vol_min,3)+",";
   s += "\"volume_max\":"+JsonNum(vol_max,3);
   s += "}";
   s += "}";

   if(risk_pct > 0.0)
      s += ",\"total_risk_pct_override\":"+JsonNum(risk_pct,4);

   s += "}";
   return s;
}

//+------------------------------------------------------------------+
//| JSON helpers                                                     |
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
   while(e < n)
   {
      ushort c = StringGetCharacter(j, e);
      if(c==',' || c=='}' || c==']' || c=='\n' || c=='\r') break;
      e++;
   }
   string raw = StringSubstr(j, s, e - s);
   StringReplace(raw, " ", "");
   if(raw=="null" || raw=="") return defv;
   return StringToDouble(raw);
}

//+------------------------------------------------------------------+
//| HTTP                                                             |
//+------------------------------------------------------------------+
bool CallAdapterOnce(const string &url, const string &body, string &out, int &http_code)
{
   string headers = "Content-Type: application/json\r\nConnection: close\r\n";
   char post[]; char result[]; string rh;
   StringToCharArray(body, post, 0, StringLen(body), CP_UTF8);
   ResetLastError();
   http_code = WebRequest("POST", url, headers, InpHttpTimeoutMs, post, result, rh);
   if(http_code == -1)
   {
      PrintFormat("WebRequest failed err=%d url=%s", GetLastError(), url);
      return false;
   }
   out = CharArrayToString(result, 0, -1, CP_UTF8);
   return http_code == 200;
}

bool CallAdapter(const string &url, const string &body, string &out)
{
   int code = 0;
   if(CallAdapterOnce(url, body, out, code)) return true;
   // Retry once for intermittent failures (HTTP 1003, transient -1, etc).
   PrintFormat("Adapter call retry (first try code=%d body=%s)", code, out);
   Sleep(150);
   if(CallAdapterOnce(url, body, out, code)) return true;
   PrintFormat("Adapter call failed after retry (code=%d body=%s)", code, out);
   return false;
}

//+------------------------------------------------------------------+
//| Layer placement                                                  |
//+------------------------------------------------------------------+
ENUM_ORDER_TYPE MapOrderType(const string &t)
{
   if(t=="buy_limit") return ORDER_TYPE_BUY_LIMIT;
   if(t=="sell_limit") return ORDER_TYPE_SELL_LIMIT;
   if(t=="buy_stop") return ORDER_TYPE_BUY_STOP;
   if(t=="sell_stop") return ORDER_TYPE_SELL_STOP;
   if(t=="buy_market") return ORDER_TYPE_BUY;
   if(t=="sell_market") return ORDER_TYPE_SELL;
   return WRONG_VALUE;
}

bool PlaceLayer(const string &otype, double price, double lots, double sl, double tp,
                long magic, datetime expiration)
{
   MqlTradeRequest req; MqlTradeResult res; MqlTradeCheckResult chk;
   ZeroMemory(req); ZeroMemory(res); ZeroMemory(chk);
   req.action       = TRADE_ACTION_PENDING;
   req.symbol       = _Symbol;
   req.volume       = lots;
   req.type         = MapOrderType(otype);
   req.price        = price;
   req.sl           = (sl > 0) ? sl : 0.0;
   req.tp           = (tp > 0) ? tp : 0.0;
   req.deviation    = 20;
   req.magic        = magic;
   req.comment      = "qlip-layer";
   // Use GTC; EA enforces max-age cleanup via CleanupStalePendings().
   req.type_time    = ORDER_TIME_GTC;
   req.type_filling = ORDER_FILLING_RETURN;  // recommended for pending orders

   if(!OrderCheck(req, chk))
   {
      PrintFormat("OrderCheck fail layer magic=%d retcode=%u (%s)", magic, chk.retcode, chk.comment);
      return false;
   }
   if(!OrderSend(req, res))
   {
      PrintFormat("OrderSend fail layer magic=%d retcode=%u (%s)", magic, res.retcode, res.comment);
      return false;
   }
   PrintFormat("Layer placed magic=%d type=%s @%.2f lots=%.2f sl=%.2f exp=%s",
               magic, otype, price, lots, sl, TimeToString(expiration, TIME_DATE|TIME_MINUTES));
   return true;
}

//+------------------------------------------------------------------+
//| Process plan response                                            |
//+------------------------------------------------------------------+
void ProcessPlanResponse(const string &resp)
{
   string status   = FindStringField(resp, "status");
   string scenario = FindStringField(resp, "scenario");
   string side     = FindStringField(resp, "side_bias");
   double conf     = FindNumberField(resp, "confidence", 0.0);
   double basket_tp= FindNumberField(resp, "basket_tp_pct_equity", 0.0);
   double inv      = FindNumberField(resp, "scenario_invalidation_price", 0.0);

   PrintFormat("[Plan] status=%s scenario=%s side=%s conf=%.2f basket_tp_pct=%.3f inv=%.2f",
               status, scenario, side, conf, basket_tp, inv);

   if(status != "ok") return;
   if(scenario == "NONE" || scenario == "") return;
   if(!InpEnableTrading) { Print("Trading disabled by input."); return; }

   // Parse layers array.
   int placed = 0;
   int from = 0;
   for(int i=1; i<=5; i++)
   {
      // Find next "layer_id": i
      string nd = "\"layer_id\":"+IntegerToString(i);
      int p = StringFind(resp, nd, from);
      if(p < 0) break;
      // Anchor parsing at this offset.
      int section_end = StringFind(resp, "}", p);
      if(section_end < 0) section_end = StringLen(resp);
      string section = StringSubstr(resp, p, section_end - p + 1);

      string otype = FindStringField(section, "order_type");
      double price = FindNumberField(section, "price", 0.0);
      double lots  = FindNumberField(section, "lots", 0.0);
      double sl    = FindNumberField(section, "sl", 0.0);
      double tp    = FindNumberField(section, "tp", 0.0);
      long   magic = (long)FindNumberField(section, "magic", InpBaseMagic + i);
      string exp_iso = FindStringField(section, "expiration_utc");
      datetime exp = TimeGMT() + 30*60;
      // best-effort parse of expiration_utc; if blank, default 30 minutes.

      if(otype == "" || price <= 0 || lots <= 0) { from = section_end; continue; }

      if(PlaceLayer(otype, price, lots, sl, tp, magic, exp))
         placed++;
      from = section_end;
   }

   if(placed > 0)
   {
      g_basket_inv_price = inv;
      g_basket_tp_pct    = basket_tp;
      g_basket_start_eq  = AccountInfoDouble(ACCOUNT_EQUITY);
      g_basket_side      = side;
      g_basket_active    = true;
      GlobalVariableSet(GV_INVPRICE, g_basket_inv_price);
      GlobalVariableSet(GV_TPPCT,    g_basket_tp_pct);
      GlobalVariableSet(GV_STARTEQ,  g_basket_start_eq);
      GlobalVariableSet(GV_SIDE,     (side=="buy")?1.0:-1.0);
      PrintFormat("Basket activated: %d layers placed, side=%s, inv=%.2f, tp_pct=%.3f",
                  placed, side, inv, basket_tp);
   }
   else
   {
      Print("No layers placed (all rejected).");
   }
}

//+------------------------------------------------------------------+
//| Basket lifecycle                                                 |
//+------------------------------------------------------------------+
bool HasActiveBasket()
{
   if(g_basket_active) return true;
   // Detect pendings or positions with magic in our range.
   for(int i=0; i<PositionsTotal(); i++)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionSelectByTicket(tk))
      {
         long m = PositionGetInteger(POSITION_MAGIC);
         if(m >= InpBaseMagic && m <= InpBaseMagic + 5 &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
            return true;
      }
   }
   for(int i=0; i<OrdersTotal(); i++)
   {
      ulong tk = OrderGetTicket(i);
      if(tk == 0) continue;
      if(OrderSelect(tk))
      {
         long m = OrderGetInteger(ORDER_MAGIC);
         if(m >= InpBaseMagic && m <= InpBaseMagic + 5 &&
            OrderGetString(ORDER_SYMBOL) == _Symbol)
            return true;
      }
   }
   return false;
}

double GetBasketFloatingPnl()
{
   double pnl = 0.0;
   for(int i=0; i<PositionsTotal(); i++)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionSelectByTicket(tk))
      {
         long m = PositionGetInteger(POSITION_MAGIC);
         if(m >= InpBaseMagic && m <= InpBaseMagic + 5 &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
         {
            pnl += PositionGetDouble(POSITION_PROFIT);
            pnl += PositionGetDouble(POSITION_SWAP);
         }
      }
   }
   return pnl;
}

void CloseBasket(const string &reason)
{
   PrintFormat("Closing basket: %s", reason);
   // Close positions.
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionSelectByTicket(tk))
      {
         long m = PositionGetInteger(POSITION_MAGIC);
         if(m >= InpBaseMagic && m <= InpBaseMagic + 5 &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
         {
            Trade.PositionClose(tk);
         }
      }
   }
   // Cancel pendings.
   for(int i = OrdersTotal()-1; i >= 0; i--)
   {
      ulong tk = OrderGetTicket(i);
      if(tk == 0) continue;
      if(OrderSelect(tk))
      {
         long m = OrderGetInteger(ORDER_MAGIC);
         if(m >= InpBaseMagic && m <= InpBaseMagic + 5 &&
            OrderGetString(ORDER_SYMBOL) == _Symbol)
         {
            Trade.OrderDelete(tk);
         }
      }
   }

   g_basket_active = false;
   g_basket_inv_price = 0.0;
   g_basket_tp_pct    = 0.0;
   g_basket_side      = "";
   GlobalVariableDel(GV_INVPRICE);
   GlobalVariableDel(GV_TPPCT);
   GlobalVariableDel(GV_STARTEQ);
   GlobalVariableDel(GV_SIDE);

   g_cooldown_until = TimeCurrent() + InpCooldownMinutes * 60;
}

bool HasOpenPositionsWithMagic()
{
   for(int i=0; i<PositionsTotal(); i++)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionSelectByTicket(tk))
      {
         long m = PositionGetInteger(POSITION_MAGIC);
         if(m >= InpBaseMagic && m <= InpBaseMagic + 5 &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
            return true;
      }
   }
   return false;
}

void CleanupStalePendings()
{
   datetime cutoff = TimeCurrent() - InpMaxPendingMinutes * 60;
   int removed = 0;
   for(int i = OrdersTotal()-1; i >= 0; i--)
   {
      ulong tk = OrderGetTicket(i);
      if(tk == 0) continue;
      if(OrderSelect(tk))
      {
         long m = OrderGetInteger(ORDER_MAGIC);
         datetime setup = (datetime)OrderGetInteger(ORDER_TIME_SETUP);
         if(m >= InpBaseMagic && m <= InpBaseMagic + 5 &&
            OrderGetString(ORDER_SYMBOL) == _Symbol &&
            setup > 0 && setup < cutoff)
         {
            if(Trade.OrderDelete(tk))
            {
               removed++;
               PrintFormat("Stale pending deleted ticket=%I64u magic=%d age>%dmin",
                           tk, m, InpMaxPendingMinutes);
            }
         }
      }
   }
   // If after cleanup no positions AND no pendings remain, reset basket state.
   if(removed > 0 && g_basket_active && !HasOpenPositionsWithMagic())
   {
      bool any_pending_left = false;
      for(int i=0; i<OrdersTotal(); i++)
      {
         ulong tk = OrderGetTicket(i);
         if(tk == 0) continue;
         if(OrderSelect(tk))
         {
            long m = OrderGetInteger(ORDER_MAGIC);
            if(m >= InpBaseMagic && m <= InpBaseMagic + 5 &&
               OrderGetString(ORDER_SYMBOL) == _Symbol)
            { any_pending_left = true; break; }
         }
      }
      if(!any_pending_left)
      {
         Print("All layers expired without fill; resetting basket state.");
         g_basket_active = false;
         g_basket_inv_price = 0.0;
         g_basket_tp_pct = 0.0;
         g_basket_side = "";
         GlobalVariableDel(GV_INVPRICE);
         GlobalVariableDel(GV_TPPCT);
         GlobalVariableDel(GV_STARTEQ);
         GlobalVariableDel(GV_SIDE);
         g_cooldown_until = TimeCurrent() + InpCooldownMinutes * 60;
      }
   }
}

void EvaluateBasket()
{
   if(!HasActiveBasket()) return;

   // Basket TP check.
   if(g_basket_tp_pct > 0.0 && g_basket_start_eq > 0.0)
   {
      double pnl = GetBasketFloatingPnl();
      double pct = (pnl / g_basket_start_eq) * 100.0;
      if(pct >= g_basket_tp_pct)
      {
         CloseBasket(StringFormat("Basket TP hit (%.3f%% >= %.3f%%)", pct, g_basket_tp_pct));
         return;
      }
   }

   // Invalidation price check.
   if(g_basket_inv_price > 0.0 && g_basket_side != "")
   {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      bool hit = false;
      if(g_basket_side == "buy" && bid <= g_basket_inv_price) hit = true;
      if(g_basket_side == "sell" && ask >= g_basket_inv_price) hit = true;
      if(hit)
      {
         CloseBasket(StringFormat("Invalidation level hit @ %.2f", g_basket_inv_price));
         return;
      }
   }
}

//+------------------------------------------------------------------+
//| OnTimer / OnTick                                                 |
//+------------------------------------------------------------------+
void OnTimer()
{
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) return;
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED)) return;

   if(TimeCurrent() < g_cooldown_until) return;

   // Periodically prune stale pendings — runs on every timer tick (cheap).
   CleanupStalePendings();

   if(!IsNewM1Bar()) return;

   // Don't request a new plan if a basket is already active.
   if(HasActiveBasket())
   {
      EvaluateBasket();
      return;
   }

   string rid = StringFormat("%s-M1-%s", _Symbol, IsoUtcFromTime(g_last_m1_bar));
   string body = BuildPlanRequestJson(rid);
   if(body == "") { Print("Plan request build failed (warming up?)"); return; }

   string resp;
   if(!CallAdapter(InpPlanUrl, body, resp))
   {
      return; // fail-closed
   }
   ProcessPlanResponse(resp);
}

void OnTick()
{
   if(HasActiveBasket())
      EvaluateBasket();
}

//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction& trans,
                        const MqlTradeRequest&     request,
                        const MqlTradeResult&      result)
{
   string trans_type = EnumToString((ENUM_TRADE_TRANSACTION_TYPE)trans.type);
   string body = "{";
   body += "\"schema_version\":\"trade-transaction-event.v1\",";
   body += "\"request_id\":\""+_Symbol+"-tx-"+IntegerToString((long)TimeGMT())+"\",";
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
