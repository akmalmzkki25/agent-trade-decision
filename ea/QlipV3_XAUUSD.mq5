//+------------------------------------------------------------------+
//| QlipV3_XAUUSD.mq5                                                |
//| V3 Aggressive Mixed-Layering EA                                  |
//| - M1 trigger, 4 scenarios incl. MOMENTUM_M1                      |
//| - Mixed ladder per plan: 1 market + 2 limit + 2 stop             |
//| - Up to 2 baskets parallel, different sides                      |
//| - Slot A magic 250530..250534, Slot B magic 250540..250544       |
//| - Fundamental DXY/VIX bias (optional symbols)                    |
//+------------------------------------------------------------------+
#property copyright "Qlip"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>

//--- Inputs
input string  InpPlanUrl              = "http://127.0.0.1:8765/v3/plan";
input string  InpEventUrl             = "http://127.0.0.1:8765/v1/events/trade-transaction";
input int     InpHttpTimeoutMs        = 2000;
input bool    InpEnableTrading        = true;
input double  InpTotalRiskPctOverride = 0.0;     // 0 = server default (1%)
input long    InpBaseMagicA           = 250530;
input long    InpBaseMagicB           = 250540;
input int     InpMaxBasketLifetimeMin = 10;
input int     InpCooldownMinutes      = 2;       // per slot
input int     InpMaxPendingMinutes    = 10;
input string  InpDxySymbol            = "";      // e.g. "USDX" / "DXY" / "" to disable
input string  InpVixSymbol            = "";      // e.g. "VIX" / "" to disable

//--- Globals
CTrade        Trade;
int           h_adx_h1=-1, h_atr_h1=-1, h_ma50_h1=-1;
int           h_adx_m15=-1, h_atr_m15=-1, h_bb_m15=-1, h_ma50_m15=-1;
int           h_ema20_m5=-1, h_ema50_m5=-1, h_atr_m5=-1, h_rsi_m5=-1, h_bb_m5=-1;
int           h_rsi_m1=-1, h_bb_m1=-1, h_atr_m1=-1;
datetime      g_last_m1_bar = 0;

// Per-slot state.
struct SlotState
{
   bool      active;
   string    side;
   double    inv_price;
   double    tp_pct;
   double    start_eq;
   datetime  opened_at;
   datetime  cooldown_until;
   long      base_magic;
   // Staged exit state
   bool      stage1_done;
   bool      stage2_done;
   bool      stage3_done;
   // Parsed exit rules from /v3/plan response
   double    stage1_pips;
   int       stage1_close_n;
   double    stage1_sl_offset;
   bool      stage1_cancel_pendings;
   int       stage1_min_open;
   double    stage2_pips;
   double    stage2_sl_offset;
   double    stage3_pips;
   int       stage3_close_n;
   int       stage3_min_remaining;
   double    runner_cap;
};
SlotState SlotA, SlotB;

double PipSize()
{
   double pt = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   if(digits == 5 || digits == 3 || digits == 2) return pt * 10.0;
   return pt;
}

void SetExitRuleDefaults(SlotState &s)
{
   s.stage1_pips = 30.0;
   s.stage1_close_n = 3;
   s.stage1_sl_offset = -20.0;
   s.stage1_cancel_pendings = true;
   s.stage1_min_open = 3;
   s.stage2_pips = 50.0;
   s.stage2_sl_offset = 0.0;
   s.stage3_pips = 60.0;
   s.stage3_close_n = 1;
   s.stage3_min_remaining = 1;
   s.runner_cap = 100.0;
}

#define GV_PREFIX  "QlipV3_"

string GVName(const string slot, const string field) { return GV_PREFIX + slot + "_" + field; }

void SaveSlot(const string slot, const SlotState &s)
{
   GlobalVariableSet(GVName(slot, "ACT"),      s.active ? 1.0 : 0.0);
   GlobalVariableSet(GVName(slot, "SIDE"),     s.side == "buy" ? 1.0 : (s.side == "sell" ? -1.0 : 0.0));
   GlobalVariableSet(GVName(slot, "INV"),      s.inv_price);
   GlobalVariableSet(GVName(slot, "TPPCT"),    s.tp_pct);
   GlobalVariableSet(GVName(slot, "STARTEQ"),  s.start_eq);
   GlobalVariableSet(GVName(slot, "OPENED"),   (double)s.opened_at);
   GlobalVariableSet(GVName(slot, "COOLDOWN"), (double)s.cooldown_until);
   GlobalVariableSet(GVName(slot, "STG1"),     s.stage1_done ? 1.0 : 0.0);
   GlobalVariableSet(GVName(slot, "STG2"),     s.stage2_done ? 1.0 : 0.0);
   GlobalVariableSet(GVName(slot, "STG3"),     s.stage3_done ? 1.0 : 0.0);
}

void ClearSlot(const string slot, SlotState &s)
{
   s.active = false;
   s.side = "";
   s.inv_price = 0.0;
   s.tp_pct = 0.0;
   s.start_eq = 0.0;
   s.opened_at = 0;
   s.stage1_done = false;
   s.stage2_done = false;
   s.stage3_done = false;
   GlobalVariableDel(GVName(slot, "ACT"));
   GlobalVariableDel(GVName(slot, "SIDE"));
   GlobalVariableDel(GVName(slot, "INV"));
   GlobalVariableDel(GVName(slot, "TPPCT"));
   GlobalVariableDel(GVName(slot, "STARTEQ"));
   GlobalVariableDel(GVName(slot, "OPENED"));
   GlobalVariableDel(GVName(slot, "STG1"));
   GlobalVariableDel(GVName(slot, "STG2"));
   GlobalVariableDel(GVName(slot, "STG3"));
   // keep cooldown
}

void LoadSlot(const string slot, SlotState &s)
{
   SetExitRuleDefaults(s);
   if(GlobalVariableCheck(GVName(slot, "ACT")))
   {
      s.active = GlobalVariableGet(GVName(slot, "ACT")) > 0.5;
      double sv = GlobalVariableGet(GVName(slot, "SIDE"));
      s.side = (sv > 0.5) ? "buy" : (sv < -0.5 ? "sell" : "");
      s.inv_price  = GlobalVariableGet(GVName(slot, "INV"));
      s.tp_pct     = GlobalVariableGet(GVName(slot, "TPPCT"));
      s.start_eq   = GlobalVariableGet(GVName(slot, "STARTEQ"));
      s.opened_at  = (datetime)GlobalVariableGet(GVName(slot, "OPENED"));
   }
   if(GlobalVariableCheck(GVName(slot, "STG1"))) s.stage1_done = GlobalVariableGet(GVName(slot, "STG1")) > 0.5;
   if(GlobalVariableCheck(GVName(slot, "STG2"))) s.stage2_done = GlobalVariableGet(GVName(slot, "STG2")) > 0.5;
   if(GlobalVariableCheck(GVName(slot, "STG3"))) s.stage3_done = GlobalVariableGet(GVName(slot, "STG3")) > 0.5;
   if(GlobalVariableCheck(GVName(slot, "COOLDOWN")))
      s.cooldown_until = (datetime)GlobalVariableGet(GVName(slot, "COOLDOWN"));
}

//+------------------------------------------------------------------+
int OnInit()
{
   Trade.SetExpertMagicNumber(InpBaseMagicA);
   Trade.SetDeviationInPoints(20);
   Trade.SetTypeFilling(ORDER_FILLING_IOC);

   // Init indicator handles
   h_adx_h1   = iADX(_Symbol, PERIOD_H1, 14);
   h_atr_h1   = iATR(_Symbol, PERIOD_H1, 14);
   h_ma50_h1  = iMA(_Symbol, PERIOD_H1, 50, 0, MODE_EMA, PRICE_CLOSE);
   h_adx_m15  = iADX(_Symbol, PERIOD_M15, 14);
   h_atr_m15  = iATR(_Symbol, PERIOD_M15, 14);
   h_bb_m15   = iBands(_Symbol, PERIOD_M15, 20, 0, 2.0, PRICE_CLOSE);
   h_ma50_m15 = iMA(_Symbol, PERIOD_M15, 50, 0, MODE_EMA, PRICE_CLOSE);
   h_ema20_m5 = iMA(_Symbol, PERIOD_M5, 20, 0, MODE_EMA, PRICE_CLOSE);
   h_ema50_m5 = iMA(_Symbol, PERIOD_M5, 50, 0, MODE_EMA, PRICE_CLOSE);
   h_atr_m5   = iATR(_Symbol, PERIOD_M5, 14);
   h_rsi_m5   = iRSI(_Symbol, PERIOD_M5, 14, PRICE_CLOSE);
   h_bb_m5    = iBands(_Symbol, PERIOD_M5, 20, 0, 2.0, PRICE_CLOSE);
   h_rsi_m1   = iRSI(_Symbol, PERIOD_M1, 14, PRICE_CLOSE);
   h_bb_m1    = iBands(_Symbol, PERIOD_M1, 20, 0, 2.0, PRICE_CLOSE);
   h_atr_m1   = iATR(_Symbol, PERIOD_M1, 14);

   if(h_adx_h1==INVALID_HANDLE || h_atr_m1==INVALID_HANDLE)
   {
      Print("V3: indicator handle failure"); return INIT_FAILED;
   }

   SlotA.active = false; SlotA.base_magic = InpBaseMagicA;
   SlotB.active = false; SlotB.base_magic = InpBaseMagicB;
   LoadSlot("A", SlotA);
   LoadSlot("B", SlotB);

   PrintFormat("V3 EA initialized. SlotA active=%s side=%s | SlotB active=%s side=%s",
               SlotA.active?"true":"false", SlotA.side,
               SlotB.active?"true":"false", SlotB.side);

   EventSetTimer(2);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   IndicatorRelease(h_adx_h1); IndicatorRelease(h_atr_h1); IndicatorRelease(h_ma50_h1);
   IndicatorRelease(h_adx_m15); IndicatorRelease(h_atr_m15); IndicatorRelease(h_bb_m15); IndicatorRelease(h_ma50_m15);
   IndicatorRelease(h_ema20_m5); IndicatorRelease(h_ema50_m5); IndicatorRelease(h_atr_m5); IndicatorRelease(h_rsi_m5); IndicatorRelease(h_bb_m5);
   IndicatorRelease(h_rsi_m1); IndicatorRelease(h_bb_m1); IndicatorRelease(h_atr_m1);
}

//+------------------------------------------------------------------+
//| Helpers                                                          |
//+------------------------------------------------------------------+
double GetBuf(int handle, int buffer, int shift)
{
   double tmp[1];
   if(CopyBuffer(handle, buffer, shift, 1, tmp) != 1) return EMPTY_VALUE;
   return tmp[0];
}

bool IsNewM1Bar()
{
   datetime t0 = (datetime)SeriesInfoInteger(_Symbol, PERIOD_M1, SERIES_LASTBAR_DATE);
   if(t0 == 0) return false;
   if(t0 != g_last_m1_bar) { g_last_m1_bar = t0; return true; }
   return false;
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
string JNum(double v, int d=6) { return DoubleToString(v, d); }

//+------------------------------------------------------------------+
//| Build V3 request JSON                                            |
//+------------------------------------------------------------------+
string BuildPlanRequestJson(const string request_id)
{
   double adx_h1 = GetBuf(h_adx_h1, 0, 1);
   double dip_h1 = GetBuf(h_adx_h1, 1, 1);
   double dim_h1 = GetBuf(h_adx_h1, 2, 1);
   double atr_h1 = GetBuf(h_atr_h1, 0, 1);
   double ma50_h1= GetBuf(h_ma50_h1, 0, 1);
   double close_h1 = iClose(_Symbol, PERIOD_H1, 1);

   double adx_m15= GetBuf(h_adx_m15, 0, 1);
   double atr_m15= GetBuf(h_atr_m15, 0, 1);
   double ma50_m15=GetBuf(h_ma50_m15, 0, 1);
   double bb_up15= GetBuf(h_bb_m15, 1, 1);
   double bb_dn15= GetBuf(h_bb_m15, 2, 1);
   double bb_mid15=GetBuf(h_bb_m15, 0, 1);
   double bb_width_m15 = (bb_mid15>0) ? (bb_up15 - bb_dn15)/bb_mid15 : 0.0;
   double bb_width_pct = MathMin(1.0, MathMax(0.0, bb_width_m15 / 0.02));

   double ema20_m5 = GetBuf(h_ema20_m5, 0, 1);
   double ema50_m5 = GetBuf(h_ema50_m5, 0, 1);
   double atr_m5   = GetBuf(h_atr_m5, 0, 1);
   double rsi_m5   = GetBuf(h_rsi_m5, 0, 1);
   double bb_up5   = GetBuf(h_bb_m5, 1, 1);
   double bb_dn5   = GetBuf(h_bb_m5, 2, 1);
   double bb_mid5  = GetBuf(h_bb_m5, 0, 1);
   double close_m5 = iClose(_Symbol, PERIOD_M5, 1);

   double rsi_m1   = GetBuf(h_rsi_m1, 0, 1);
   double bb_up1   = GetBuf(h_bb_m1, 1, 1);
   double bb_dn1   = GetBuf(h_bb_m1, 2, 1);
   double bb_mid1  = GetBuf(h_bb_m1, 0, 1);
   double atr_m1   = GetBuf(h_atr_m1, 0, 1);
   double close_m1 = iClose(_Symbol, PERIOD_M1, 1);

   if(adx_h1==EMPTY_VALUE || atr_h1==EMPTY_VALUE || atr_m15==EMPTY_VALUE ||
      atr_m5==EMPTY_VALUE || atr_m1==EMPTY_VALUE) return "";

   // 3-candle direction on M1 (closes 1..3 vs opens 1..3).
   int last3 = 0;
   {
      int up=0, dn=0;
      for(int s=1; s<=3; s++)
      {
         double c = iClose(_Symbol, PERIOD_M1, s);
         double o = iOpen(_Symbol, PERIOD_M1, s);
         if(c > o) up++; else if(c < o) dn++;
      }
      if(up == 3) last3 = 1;
      else if(dn == 3) last3 = -1;
   }

   // ATR percentile M1 (rough rolling proxy: current vs avg of 50 bars).
   double atr_m1_pct = 0.0;
   {
      double arr[];
      if(CopyBuffer(h_atr_m1, 0, 1, 50, arr) == 50)
      {
         int below = 0;
         for(int i=0;i<50;i++) if(arr[i] <= atr_m1) below++;
         atr_m1_pct = below / 50.0;
      }
   }

   // DXY/VIX optional context.
   double dxy_slope_z = 0.0;
   if(StringLen(InpDxySymbol) > 0)
   {
      double c_now = iClose(InpDxySymbol, PERIOD_M5, 1);
      double c_old = iClose(InpDxySymbol, PERIOD_M5, 30);
      if(c_now > 0 && c_old > 0)
      {
         // crude: slope normalized by std proxy = (c_now - c_old)/c_old * 100
         dxy_slope_z = (c_now - c_old) / c_old * 100.0;
      }
   }
   double vix_z = 0.0;
   if(StringLen(InpVixSymbol) > 0)
   {
      double v_now = iClose(InpVixSymbol, PERIOD_M5, 1);
      double v_avg = 0.0;
      int    n = 0;
      for(int i=1; i<=30; i++) { double v = iClose(InpVixSymbol, PERIOD_M5, i); if(v>0) { v_avg += v; n++; } }
      if(n>0 && v_now>0) vix_z = (v_now - v_avg/n) / MathMax(v_avg/n, 1e-9);
   }

   // Spread + market.
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

   long   login   = AccountInfoInteger(ACCOUNT_LOGIN);
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   double freem   = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double mlevel  = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
   string curr    = AccountInfoString(ACCOUNT_CURRENCY);
   long   lev     = AccountInfoInteger(ACCOUNT_LEVERAGE);

   // DI balance + BB pos
   double di_sum = dip_h1 + dim_h1;
   double di_balance = (di_sum > 1e-9) ? (dip_h1 - dim_h1) / di_sum : 0.0;
   double bb_pos_m5 = (close_m5 - bb_mid5) / MathMax((bb_up5 - bb_dn5)/2.0, 1e-9);
   double bb_pos_m1 = (close_m1 - bb_mid1) / MathMax((bb_up1 - bb_dn1)/2.0, 1e-9);

   // Active baskets array.
   string baskets = "[";
   bool first = true;
   if(SlotA.active && SlotA.side != "")
   {
      baskets += "{\"slot\":\"A\",\"side\":\""+SlotA.side+"\",\"opened_at_utc\":\""+IsoUtcFromTime(SlotA.opened_at)+"\"}";
      first = false;
   }
   if(SlotB.active && SlotB.side != "")
   {
      if(!first) baskets += ",";
      baskets += "{\"slot\":\"B\",\"side\":\""+SlotB.side+"\",\"opened_at_utc\":\""+IsoUtcFromTime(SlotB.opened_at)+"\"}";
   }
   baskets += "]";

   double risk_pct = (InpTotalRiskPctOverride > 0.0) ? InpTotalRiskPctOverride : 0.0;

   string s = "{";
   s += "\"schema_version\":\"v3-plan-request.v1\",";
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
   s += "\"adx_strength\":"+JNum(adx_h1/100.0,4)+",";
   s += "\"di_balance\":"+JNum(di_balance,4)+",";
   s += "\"atr_pct\":"+JNum(atr_h1/MathMax(close_h1,1e-9),6)+",";
   s += "\"atr_abs_h1\":"+JNum(atr_h1,digits)+",";
   s += "\"swing_high_h1\":"+JNum(iHigh(_Symbol, PERIOD_H1, iHighest(_Symbol, PERIOD_H1, MODE_HIGH, 20, 1)), digits)+",";
   s += "\"swing_low_h1\":"+JNum(iLow(_Symbol, PERIOD_H1, iLowest(_Symbol, PERIOD_H1, MODE_LOW, 20, 1)), digits);
   s += "},";

   s += "\"decision_tf\":{";
   s += "\"rsi_centered\":"+JNum((rsi_m1-50.0)/50.0,4)+",";
   s += "\"bb_pos\":"+JNum(bb_pos_m1,4)+",";
   s += "\"m1_atr_pct\":"+JNum(atr_m1_pct,4)+",";
   s += "\"last3_dir\":"+IntegerToString(last3);
   s += "},";

   s += "\"confirm_tf\":{";
   s += "\"rsi_centered\":"+JNum((rsi_m5-50.0)/50.0,4)+",";
   s += "\"bb_pos\":"+JNum(bb_pos_m5,4)+",";
   s += "\"ema20_m5\":"+JNum(ema20_m5,digits)+",";
   s += "\"ema50_m5\":"+JNum(ema50_m5,digits)+",";
   s += "\"ema50_m15\":"+JNum(ma50_m15,digits)+",";
   s += "\"adx_strength_m15\":"+JNum(adx_m15/100.0,4)+",";
   s += "\"bb_width_pct\":"+JNum(bb_width_pct,4)+",";
   s += "\"breakout_high_m15\":"+JNum(iHigh(_Symbol, PERIOD_M15, iHighest(_Symbol, PERIOD_M15, MODE_HIGH, 20, 1)), digits)+",";
   s += "\"breakout_low_m15\":"+JNum(iLow(_Symbol, PERIOD_M15, iLowest(_Symbol, PERIOD_M15, MODE_LOW, 20, 1)), digits);
   s += "},";

   s += "\"execution_tf\":{";
   s += "\"spread_to_atr_ratio\":"+JNum((atr_m1>0)?(spread_pts*SymbolInfoDouble(_Symbol,SYMBOL_POINT))/atr_m1:0.0,4)+",";
   s += "\"atr_abs\":"+JNum(atr_m1,digits)+",";
   s += "\"atr_abs_m15\":"+JNum(atr_m15,digits)+",";
   s += "\"atr_abs_m5\":"+JNum(atr_m5,digits)+",";
   s += "\"atr_abs_m1\":"+JNum(atr_m1,digits)+",";
   s += "\"volume_step\":"+JNum(vol_step,3)+",";
   s += "\"volume_min\":"+JNum(vol_min,3)+",";
   s += "\"volume_max\":"+JNum(vol_max,3);
   s += "}";
   s += "},";

   s += "\"active_baskets\":"+baskets+",";
   s += "\"dxy_features\":{\"slope_z\":"+JNum(dxy_slope_z,4)+"},";
   s += "\"vix_features\":{\"z_score\":"+JNum(vix_z,4)+"}";

   if(risk_pct > 0.0)
      s += ",\"total_risk_pct_override\":"+JNum(risk_pct,4);

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
   if(http_code == -1) { PrintFormat("WebRequest err=%d url=%s", GetLastError(), url); return false; }
   out = CharArrayToString(result, 0, -1, CP_UTF8);
   return http_code == 200;
}

bool CallAdapter(const string &url, const string &body, string &out)
{
   int code = 0;
   if(CallAdapterOnce(url, body, out, code)) return true;
   PrintFormat("V3 adapter retry (first try code=%d)", code);
   Sleep(150);
   if(CallAdapterOnce(url, body, out, code)) return true;
   PrintFormat("V3 adapter failed after retry (code=%d)", code);
   return false;
}

//+------------------------------------------------------------------+
ENUM_ORDER_TYPE MapOrderType(const string &t)
{
   if(t=="buy_limit")    return ORDER_TYPE_BUY_LIMIT;
   if(t=="sell_limit")   return ORDER_TYPE_SELL_LIMIT;
   if(t=="buy_stop")     return ORDER_TYPE_BUY_STOP;
   if(t=="sell_stop")    return ORDER_TYPE_SELL_STOP;
   if(t=="buy_market")   return ORDER_TYPE_BUY;
   if(t=="sell_market")  return ORDER_TYPE_SELL;
   return WRONG_VALUE;
}

bool IsMarketType(const string &t) { return t == "buy_market" || t == "sell_market"; }

bool PlaceLayer(const string &otype, double price, double lots, double sl, double tp, long magic)
{
   MqlTradeRequest req; MqlTradeResult res; MqlTradeCheckResult chk;
   ZeroMemory(req); ZeroMemory(res); ZeroMemory(chk);

   req.symbol       = _Symbol;
   req.volume       = lots;
   req.type         = MapOrderType(otype);
   req.sl           = (sl > 0) ? sl : 0.0;
   req.tp           = (tp > 0) ? tp : 0.0;
   req.deviation    = 20;
   req.magic        = magic;
   req.comment      = "qlip-v3";
   req.type_time    = ORDER_TIME_GTC;

   if(IsMarketType(otype))
   {
      req.action       = TRADE_ACTION_DEAL;
      req.price        = (otype == "buy_market") ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                                                 : SymbolInfoDouble(_Symbol, SYMBOL_BID);
      req.type_filling = ORDER_FILLING_IOC;
   }
   else
   {
      req.action       = TRADE_ACTION_PENDING;
      req.price        = price;
      req.type_filling = ORDER_FILLING_RETURN;
   }

   if(!OrderCheck(req, chk))
   {
      PrintFormat("OrderCheck fail magic=%d retcode=%u (%s)", magic, chk.retcode, chk.comment);
      return false;
   }
   if(!OrderSend(req, res))
   {
      PrintFormat("OrderSend fail magic=%d retcode=%u (%s)", magic, res.retcode, res.comment);
      return false;
   }
   PrintFormat("V3 layer placed magic=%d type=%s @%.2f lots=%.2f sl=%.2f",
               magic, otype, req.price, lots, sl);
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
   string slot     = FindStringField(resp, "basket_slot");
   double conf     = FindNumberField(resp, "confidence", 0.0);
   double basket_tp= FindNumberField(resp, "basket_tp_pct_equity", 0.0);
   double inv      = FindNumberField(resp, "scenario_invalidation_price", 0.0);
   double lifetime = FindNumberField(resp, "max_lifetime_seconds", 600);

   PrintFormat("[V3 Plan] status=%s scen=%s side=%s slot=%s conf=%.2f tp_pct=%.3f inv=%.2f",
               status, scenario, side, slot, conf, basket_tp, inv);

   if(status != "ok") return;
   if(scenario == "NONE" || scenario == "") return;
   if(slot == "") return;
   if(!InpEnableTrading) return;

   SlotState s; SlotState s_ref = (slot == "A") ? SlotA : SlotB;
   if(s_ref.active) { PrintFormat("Slot %s already active; ignoring plan.", slot); return; }

   // Parse layers (max 5).
   int placed = 0;
   bool anchor_placed = false;
   for(int i=1; i<=5; i++)
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
      double tp    = FindNumberField(section, "tp", 0.0);
      long   magic = (long)FindNumberField(section, "magic", s_ref.base_magic + i);
      bool   anchor= FindNumberField(section, "is_anchor", 0.0) > 0.5;

      if(otype == "" || lots <= 0) continue;
      if(!IsMarketType(otype) && price <= 0) continue;

      bool ok = PlaceLayer(otype, price, lots, sl, tp, magic);
      if(ok)
      {
         placed++;
         if(anchor) anchor_placed = true;
      }
      else if(anchor)
      {
         // Anchor failure → abort whole basket (don't keep stray pendings).
         Print("V3 anchor (market) failed; aborting basket.");
         return;
      }
   }

   if(placed > 0 && anchor_placed)
   {
      // Parse exit_rules from response (fall back to defaults if absent).
      double stg1_pips = FindNumberField(resp, "stage1_trigger_pips", 30.0);
      double stg1_close = FindNumberField(resp, "stage1_close_count", 3.0);
      double stg1_off  = FindNumberField(resp, "stage1_sl_offset_pips", -20.0);
      double stg1_min  = FindNumberField(resp, "stage1_min_open_positions", 3.0);
      string stg1_cancel_s = FindStringField(resp, "stage1_cancel_pendings");
      bool   stg1_cancel = (stg1_cancel_s == "true") || (FindNumberField(resp, "stage1_cancel_pendings", 1.0) > 0.5);
      double stg2_pips = FindNumberField(resp, "stage2_trigger_pips", 50.0);
      double stg2_off  = FindNumberField(resp, "stage2_sl_offset_pips", 0.0);
      double stg3_pips = FindNumberField(resp, "stage3_trigger_pips", 60.0);
      double stg3_close= FindNumberField(resp, "stage3_close_count", 1.0);
      double stg3_minr = FindNumberField(resp, "stage3_min_remaining_after", 1.0);
      double runner    = FindNumberField(resp, "runner_cap_pips", 100.0);

      // Activate slot.
      if(slot == "A")
      {
         SlotA.active = true;
         SlotA.side = side;
         SlotA.inv_price = inv;
         SlotA.tp_pct = basket_tp;
         SlotA.start_eq = AccountInfoDouble(ACCOUNT_EQUITY);
         SlotA.opened_at = TimeCurrent();
         SlotA.stage1_done = false;
         SlotA.stage2_done = false;
         SlotA.stage3_done = false;
         SlotA.stage1_pips = stg1_pips;
         SlotA.stage1_close_n = (int)stg1_close;
         SlotA.stage1_sl_offset = stg1_off;
         SlotA.stage1_cancel_pendings = stg1_cancel;
         SlotA.stage1_min_open = (int)stg1_min;
         SlotA.stage2_pips = stg2_pips;
         SlotA.stage2_sl_offset = stg2_off;
         SlotA.stage3_pips = stg3_pips;
         SlotA.stage3_close_n = (int)stg3_close;
         SlotA.stage3_min_remaining = (int)stg3_minr;
         SlotA.runner_cap = runner;
         SaveSlot("A", SlotA);
      }
      else
      {
         SlotB.active = true;
         SlotB.side = side;
         SlotB.inv_price = inv;
         SlotB.tp_pct = basket_tp;
         SlotB.start_eq = AccountInfoDouble(ACCOUNT_EQUITY);
         SlotB.opened_at = TimeCurrent();
         SlotB.stage1_done = false;
         SlotB.stage2_done = false;
         SlotB.stage3_done = false;
         SlotB.stage1_pips = stg1_pips;
         SlotB.stage1_close_n = (int)stg1_close;
         SlotB.stage1_sl_offset = stg1_off;
         SlotB.stage1_cancel_pendings = stg1_cancel;
         SlotB.stage1_min_open = (int)stg1_min;
         SlotB.stage2_pips = stg2_pips;
         SlotB.stage2_sl_offset = stg2_off;
         SlotB.stage3_pips = stg3_pips;
         SlotB.stage3_close_n = (int)stg3_close;
         SlotB.stage3_min_remaining = (int)stg3_minr;
         SlotB.runner_cap = runner;
         SaveSlot("B", SlotB);
      }
      PrintFormat("V3 basket slot %s activated: side=%s layers=%d inv=%.2f tp_pct=%.3f",
                  slot, side, placed, inv, basket_tp);
   }
}

//+------------------------------------------------------------------+
//| Basket evaluation per slot                                       |
//+------------------------------------------------------------------+
double SlotFloatingPnl(long base_magic)
{
   double pnl = 0.0;
   for(int i=0; i<PositionsTotal(); i++)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionSelectByTicket(tk))
      {
         long m = PositionGetInteger(POSITION_MAGIC);
         if(m >= base_magic && m <= base_magic + 5 &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
         {
            pnl += PositionGetDouble(POSITION_PROFIT);
            pnl += PositionGetDouble(POSITION_SWAP);
         }
      }
   }
   return pnl;
}

bool SlotHasAnything(long base_magic)
{
   for(int i=0; i<PositionsTotal(); i++)
   {
      ulong tk = PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionSelectByTicket(tk))
      {
         long m = PositionGetInteger(POSITION_MAGIC);
         if(m >= base_magic && m <= base_magic + 5 && PositionGetString(POSITION_SYMBOL) == _Symbol) return true;
      }
   }
   for(int i=0; i<OrdersTotal(); i++)
   {
      ulong tk = OrderGetTicket(i);
      if(tk==0) continue;
      if(OrderSelect(tk))
      {
         long m = OrderGetInteger(ORDER_MAGIC);
         if(m >= base_magic && m <= base_magic + 5 && OrderGetString(ORDER_SYMBOL) == _Symbol) return true;
      }
   }
   return false;
}

void CloseSlot(const string slot_name, SlotState &s, const string reason)
{
   PrintFormat("V3 closing slot %s: %s", slot_name, reason);
   long base = s.base_magic;
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionSelectByTicket(tk))
      {
         long m = PositionGetInteger(POSITION_MAGIC);
         if(m >= base && m <= base + 5 && PositionGetString(POSITION_SYMBOL) == _Symbol)
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
         if(m >= base && m <= base + 5 && OrderGetString(ORDER_SYMBOL) == _Symbol)
            Trade.OrderDelete(tk);
      }
   }
   s.cooldown_until = TimeCurrent() + InpCooldownMinutes * 60;
   GlobalVariableSet(GVName(slot_name, "COOLDOWN"), (double)s.cooldown_until);
   ClearSlot(slot_name, s);
}

//+------------------------------------------------------------------+
//| V3 staged exit helpers                                           |
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

double SlotMaxProfitPips(long base_magic)
{
   double max_p = -1e18;
   bool found = false;
   for(int i=0; i<PositionsTotal(); i++)
   {
      ulong tk = PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionSelectByTicket(tk))
      {
         long m = PositionGetInteger(POSITION_MAGIC);
         if(m >= base_magic && m <= base_magic + 5 &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
         {
            double p = PositionProfitPips(tk);
            if(!found || p > max_p) { max_p = p; found = true; }
         }
      }
   }
   return found ? max_p : 0.0;
}

int SlotOpenCount(long base_magic)
{
   int n = 0;
   for(int i=0; i<PositionsTotal(); i++)
   {
      ulong tk = PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionSelectByTicket(tk))
      {
         long m = PositionGetInteger(POSITION_MAGIC);
         if(m >= base_magic && m <= base_magic + 5 &&
            PositionGetString(POSITION_SYMBOL) == _Symbol) n++;
      }
   }
   return n;
}

void SlotCancelPendings(long base_magic)
{
   for(int i = OrdersTotal()-1; i >= 0; i--)
   {
      ulong tk = OrderGetTicket(i);
      if(tk==0) continue;
      if(OrderSelect(tk))
      {
         long m = OrderGetInteger(ORDER_MAGIC);
         if(m >= base_magic && m <= base_magic + 5 &&
            OrderGetString(ORDER_SYMBOL) == _Symbol)
            Trade.OrderDelete(tk);
      }
   }
}

// Collect open position tickets in the slot, sorted by open time ASC (oldest first).
int SlotCollectOpenTicketsOldestFirst(long base_magic, ulong &out_tickets[])
{
   ArrayResize(out_tickets, 0);
   ulong tk_arr[]; datetime t_arr[];
   for(int i=0; i<PositionsTotal(); i++)
   {
      ulong tk = PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionSelectByTicket(tk))
      {
         long m = PositionGetInteger(POSITION_MAGIC);
         if(m >= base_magic && m <= base_magic + 5 &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
         {
            int sz = ArraySize(tk_arr);
            ArrayResize(tk_arr, sz+1);
            ArrayResize(t_arr,  sz+1);
            tk_arr[sz] = tk;
            t_arr[sz]  = (datetime)PositionGetInteger(POSITION_TIME);
         }
      }
   }
   // Simple selection sort by t_arr ASC.
   int n = ArraySize(tk_arr);
   for(int i=0; i<n-1; i++)
   {
      int idx_min = i;
      for(int j=i+1; j<n; j++) if(t_arr[j] < t_arr[idx_min]) idx_min = j;
      if(idx_min != i)
      {
         datetime tt = t_arr[i]; t_arr[i] = t_arr[idx_min]; t_arr[idx_min] = tt;
         ulong tu = tk_arr[i]; tk_arr[i] = tk_arr[idx_min]; tk_arr[idx_min] = tu;
      }
   }
   ArrayResize(out_tickets, n);
   for(int i=0; i<n; i++) out_tickets[i] = tk_arr[i];
   return n;
}

int SlotCloseNOldest(long base_magic, int n)
{
   if(n <= 0) return 0;
   ulong tickets[];
   int total = SlotCollectOpenTicketsOldestFirst(base_magic, tickets);
   int to_close = MathMin(n, total);
   int closed = 0;
   for(int i=0; i<to_close; i++)
   {
      if(Trade.PositionClose(tickets[i])) closed++;
   }
   return closed;
}

void SlotTrailSLAll(long base_magic, const string side, double offset_pips)
{
   double pip = PipSize();
   for(int i=0; i<PositionsTotal(); i++)
   {
      ulong tk = PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionSelectByTicket(tk))
      {
         long m = PositionGetInteger(POSITION_MAGIC);
         if(!(m >= base_magic && m <= base_magic + 5 &&
              PositionGetString(POSITION_SYMBOL) == _Symbol)) continue;
         double entry = PositionGetDouble(POSITION_PRICE_OPEN);
         double cur_sl = PositionGetDouble(POSITION_SL);
         double cur_tp = PositionGetDouble(POSITION_TP);
         long type = PositionGetInteger(POSITION_TYPE);
         double new_sl = (type==POSITION_TYPE_BUY) ? (entry + offset_pips * pip)
                                                   : (entry - offset_pips * pip);
         int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
         new_sl = NormalizeDouble(new_sl, digits);
         // Only modify if improves (don't move backward).
         bool improve = false;
         if(type==POSITION_TYPE_BUY  && (cur_sl == 0.0 || new_sl > cur_sl)) improve = true;
         if(type==POSITION_TYPE_SELL && (cur_sl == 0.0 || new_sl < cur_sl)) improve = true;
         if(improve)
            Trade.PositionModify(tk, new_sl, cur_tp);
      }
   }
}

void SlotApplyRunnerCap(long base_magic, double cap_pips)
{
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionSelectByTicket(tk))
      {
         long m = PositionGetInteger(POSITION_MAGIC);
         if(m >= base_magic && m <= base_magic + 5 &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
         {
            if(PositionProfitPips(tk) >= cap_pips)
            {
               Trade.PositionClose(tk);
               PrintFormat("V3 ticket=%I64u closed at runner cap +%.1fpip", tk, cap_pips);
            }
         }
      }
   }
}

void EvaluateSlot(const string slot_name, SlotState &s)
{
   if(!s.active) return;

   double max_pips = SlotMaxProfitPips(s.base_magic);
   int open_n = SlotOpenCount(s.base_magic);

   // Stage 1 → partial TP + trail SL
   if(!s.stage1_done && max_pips >= s.stage1_pips && open_n >= s.stage1_min_open)
   {
      int n = MathMin(s.stage1_close_n, open_n);
      int closed = SlotCloseNOldest(s.base_magic, n);
      if(s.stage1_cancel_pendings) SlotCancelPendings(s.base_magic);
      SlotTrailSLAll(s.base_magic, s.side, s.stage1_sl_offset);
      s.stage1_done = true;
      GlobalVariableSet(GVName(slot_name, "STG1"), 1.0);
      PrintFormat("V3 slot %s STAGE1 done: closed=%d/cancel_pendings=%s/trail_SL=entry%+0.1fpip",
                  slot_name, closed, s.stage1_cancel_pendings?"yes":"no", s.stage1_sl_offset);
   }
   // Stage 2 → trail SL to BEP (or stage2_sl_offset)
   else if(s.stage1_done && !s.stage2_done && max_pips >= s.stage2_pips)
   {
      SlotTrailSLAll(s.base_magic, s.side, s.stage2_sl_offset);
      s.stage2_done = true;
      GlobalVariableSet(GVName(slot_name, "STG2"), 1.0);
      PrintFormat("V3 slot %s STAGE2 done: trail_SL=entry%+0.1fpip (BEP-style)",
                  slot_name, s.stage2_sl_offset);
   }
   // Stage 3 → close more, keep runners
   else if(s.stage2_done && !s.stage3_done && max_pips >= s.stage3_pips)
   {
      int keep = MathMax(s.stage3_min_remaining, 1);
      int max_closable = MathMax(0, open_n - keep);
      int n = MathMin(s.stage3_close_n, max_closable);
      if(n > 0)
      {
         int closed = SlotCloseNOldest(s.base_magic, n);
         PrintFormat("V3 slot %s STAGE3 done: closed=%d (keep %d runner)",
                     slot_name, closed, keep);
      }
      s.stage3_done = true;
      GlobalVariableSet(GVName(slot_name, "STG3"), 1.0);
   }

   // Runner cap (per-position)
   if(s.runner_cap > 0) SlotApplyRunnerCap(s.base_magic, s.runner_cap);

   // Invalidation
   if(s.inv_price > 0.0 && s.side != "")
   {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      if(s.side == "buy"  && bid <= s.inv_price) { CloseSlot(slot_name, s, "Invalidation hit"); return; }
      if(s.side == "sell" && ask >= s.inv_price) { CloseSlot(slot_name, s, "Invalidation hit"); return; }
   }

   // Max lifetime
   if(s.opened_at > 0 && (TimeCurrent() - s.opened_at) >= InpMaxBasketLifetimeMin * 60)
   { CloseSlot(slot_name, s, "Max lifetime reached"); return; }

   // If nothing left (all closed by SL/TP/manual), reset state + cooldown.
   if(!SlotHasAnything(s.base_magic))
   { ClearSlot(slot_name, s); s.cooldown_until = TimeCurrent() + InpCooldownMinutes * 60;
     GlobalVariableSet(GVName(slot_name, "COOLDOWN"), (double)s.cooldown_until); }
}

void CleanupStalePendings(long base_magic)
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
         if(m >= base_magic && m <= base_magic + 5 &&
            OrderGetString(ORDER_SYMBOL) == _Symbol &&
            setup > 0 && setup < cutoff)
         {
            Trade.OrderDelete(tk);
         }
      }
   }
}

//+------------------------------------------------------------------+
void OnTimer()
{
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) return;
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED)) return;

   CleanupStalePendings(InpBaseMagicA);
   CleanupStalePendings(InpBaseMagicB);

   EvaluateSlot("A", SlotA);
   EvaluateSlot("B", SlotB);

   if(!IsNewM1Bar()) return;

   // Skip if both slots full or both in cooldown.
   bool slotA_avail = !SlotA.active && TimeCurrent() >= SlotA.cooldown_until;
   bool slotB_avail = !SlotB.active && TimeCurrent() >= SlotB.cooldown_until;
   if(!slotA_avail && !slotB_avail) return;

   string rid = StringFormat("%s-V3M1-%s", _Symbol, IsoUtcFromTime(g_last_m1_bar));
   string body = BuildPlanRequestJson(rid);
   if(body == "") { Print("V3 request build failed"); return; }

   string resp;
   if(!CallAdapter(InpPlanUrl, body, resp)) return;
   ProcessPlanResponse(resp);
}

void OnTick()
{
   EvaluateSlot("A", SlotA);
   EvaluateSlot("B", SlotB);
}

//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction& trans,
                        const MqlTradeRequest&     request,
                        const MqlTradeResult&      result)
{
   string trans_type = EnumToString((ENUM_TRADE_TRANSACTION_TYPE)trans.type);
   string body = "{";
   body += "\"schema_version\":\"trade-transaction-event.v1\",";
   body += "\"request_id\":\""+_Symbol+"-v3tx-"+IntegerToString((long)TimeGMT())+"\",";
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
