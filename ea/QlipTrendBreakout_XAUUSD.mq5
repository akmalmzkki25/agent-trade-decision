//+------------------------------------------------------------------+
//| QlipTrendBreakout_XAUUSD.mq5                                     |
//| Phase 1 MVP EA: XAUUSD H1 context + M15 decision                 |
//|                                                                  |
//| Architecture: EA owns execution; FastAPI adapter (localhost)     |
//| owns decision (dummy trend-breakout for Phase 1, Claude Phase 2).|
//| WebRequest URL allowlist required: http://127.0.0.1:8765         |
//+------------------------------------------------------------------+
#property copyright "Qlip"
#property version   "0.10"
#property strict

#include <Trade/Trade.mqh>

//--- Inputs
input string  InpAdapterUrl          = "http://127.0.0.1:8765/v1/decision";
input string  InpEventUrl            = "http://127.0.0.1:8765/v1/events/trade-transaction";
input int     InpHttpTimeoutMs       = 2000;
input double  InpMaxRiskPerTradePct  = 0.5;     // % of equity per trade (passed to adapter)
input double  InpMaxSymbolExposure   = 0.30;    // hard lots cap
input int     InpBreakoutLookback    = 20;      // bars (M15)
input bool    InpEnableTrading       = true;
input long    InpMagic               = 250518;

//--- Globals
CTrade        Trade;
int           h_ma_h1=-1, h_adx_h1=-1, h_atr_h1=-1;
int           h_ma_m15=-1, h_rsi_m15=-1, h_atr_m15=-1, h_bb_m15=-1;
datetime      g_last_bar_time = 0;
bool          g_halt_runtime  = false;

//+------------------------------------------------------------------+
int OnInit()
{
   Trade.SetExpertMagicNumber(InpMagic);
   Trade.SetDeviationInPoints(20);
   Trade.SetTypeFilling(ORDER_FILLING_IOC);

   // Account margin mode (informational)
   long mode = AccountInfoInteger(ACCOUNT_MARGIN_MODE);
   PrintFormat("ACCOUNT_MARGIN_MODE=%d (0=netting, 2=hedging)", (int)mode);

   // Indicators
   h_ma_h1   = iMA(_Symbol, PERIOD_H1,  50, 0, MODE_EMA, PRICE_CLOSE);
   h_adx_h1  = iADX(_Symbol, PERIOD_H1, 14);
   h_atr_h1  = iATR(_Symbol, PERIOD_H1, 14);
   h_ma_m15  = iMA(_Symbol, PERIOD_M15, 20, 0, MODE_EMA, PRICE_CLOSE);
   h_rsi_m15 = iRSI(_Symbol, PERIOD_M15, 14, PRICE_CLOSE);
   h_atr_m15 = iATR(_Symbol, PERIOD_M15, 14);
   h_bb_m15  = iBands(_Symbol, PERIOD_M15, 20, 0, 2.0, PRICE_CLOSE);

   if(h_ma_h1==INVALID_HANDLE || h_adx_h1==INVALID_HANDLE || h_atr_h1==INVALID_HANDLE ||
      h_ma_m15==INVALID_HANDLE || h_rsi_m15==INVALID_HANDLE || h_atr_m15==INVALID_HANDLE ||
      h_bb_m15==INVALID_HANDLE)
   {
      Print("Failed to create indicator handles.");
      return(INIT_FAILED);
   }

   EventSetTimer(15);
   Print("QlipTrendBreakout_XAUUSD initialized.");
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   if(h_ma_h1!=INVALID_HANDLE)   IndicatorRelease(h_ma_h1);
   if(h_adx_h1!=INVALID_HANDLE)  IndicatorRelease(h_adx_h1);
   if(h_atr_h1!=INVALID_HANDLE)  IndicatorRelease(h_atr_h1);
   if(h_ma_m15!=INVALID_HANDLE)  IndicatorRelease(h_ma_m15);
   if(h_rsi_m15!=INVALID_HANDLE) IndicatorRelease(h_rsi_m15);
   if(h_atr_m15!=INVALID_HANDLE) IndicatorRelease(h_atr_m15);
   if(h_bb_m15!=INVALID_HANDLE)  IndicatorRelease(h_bb_m15);
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

bool IsNewCompletedM15Bar()
{
   datetime t0 = (datetime)SeriesInfoInteger(_Symbol, PERIOD_M15, SERIES_LASTBAR_DATE);
   if(t0 == 0) return false;
   if(t0 != g_last_bar_time)
   {
      g_last_bar_time = t0;
      return true;
   }
   return false;
}

double GetHighest(int shift, int count)
{
   double rates[];
   if(CopyHigh(_Symbol, PERIOD_M15, shift, count, rates) <= 0) return 0.0;
   double mx = rates[0];
   for(int i=1;i<ArraySize(rates);i++) if(rates[i] > mx) mx = rates[i];
   return mx;
}
double GetLowest(int shift, int count)
{
   double rates[];
   if(CopyLow(_Symbol, PERIOD_M15, shift, count, rates) <= 0) return 0.0;
   double mn = rates[0];
   for(int i=1;i<ArraySize(rates);i++) if(rates[i] < mn) mn = rates[i];
   return mn;
}

string JsonNum(double v, int digits=6)
{
   return DoubleToString(v, digits);
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

//+------------------------------------------------------------------+
//| Build features (shift=1, closed bar)                             |
//+------------------------------------------------------------------+
bool BuildFeatures(double &out_adx_strength, double &out_di_balance,
                   double &out_ma_gap_atr,   double &out_atr_pct_h1,
                   double &out_rsi_centered, double &out_bb_pos,
                   double &out_price_vs_ma_fast_atr,
                   double &out_brk_up, double &out_brk_dn,
                   double &out_spread_to_atr, double &out_last_bar_body_atr,
                   double &out_atr_abs)
{
   // H1 buffers
   double adx_h1  = GetBuf(h_adx_h1, 0, 1);
   double dip_h1  = GetBuf(h_adx_h1, 1, 1);
   double dim_h1  = GetBuf(h_adx_h1, 2, 1);
   double ma_h1   = GetBuf(h_ma_h1, 0, 1);
   double atr_h1  = GetBuf(h_atr_h1, 0, 1);

   // M15 buffers
   double rsi     = GetBuf(h_rsi_m15, 0, 1);
   double ma_m15  = GetBuf(h_ma_m15, 0, 1);
   double atr_m15 = GetBuf(h_atr_m15, 0, 1);
   double bb_mid  = GetBuf(h_bb_m15, 0, 1);
   double bb_up   = GetBuf(h_bb_m15, 1, 1);
   double bb_dn   = GetBuf(h_bb_m15, 2, 1);

   double close_h1  = iClose(_Symbol, PERIOD_H1, 1);
   double close_m15 = iClose(_Symbol, PERIOD_M15, 1);
   double open_m15  = iOpen(_Symbol, PERIOD_M15, 1);

   if(adx_h1==EMPTY_VALUE || dip_h1==EMPTY_VALUE || dim_h1==EMPTY_VALUE ||
      ma_h1==EMPTY_VALUE  || atr_h1==EMPTY_VALUE || rsi==EMPTY_VALUE ||
      ma_m15==EMPTY_VALUE || atr_m15==EMPTY_VALUE || bb_mid==EMPTY_VALUE ||
      bb_up==EMPTY_VALUE  || bb_dn==EMPTY_VALUE || atr_m15 <= 0 || atr_h1 <= 0)
      return false;

   double di_sum = dip_h1 + dim_h1;
   out_adx_strength       = adx_h1 / 100.0;
   out_di_balance         = (di_sum > 1e-9) ? (dip_h1 - dim_h1) / di_sum : 0.0;
   out_ma_gap_atr         = (close_h1 - ma_h1) / atr_h1;
   out_atr_pct_h1         = (close_h1 > 0) ? atr_h1 / close_h1 : 0.0;

   out_rsi_centered          = (rsi - 50.0) / 50.0;
   double half_bb            = MathMax((bb_up - bb_dn) / 2.0, 1e-9);
   out_bb_pos                = (close_m15 - bb_mid) / half_bb;
   out_price_vs_ma_fast_atr  = (close_m15 - ma_m15) / atr_m15;

   double hh = GetHighest(2, InpBreakoutLookback);
   double ll = GetLowest(2, InpBreakoutLookback);
   out_brk_up = (hh > 0 && close_m15 > hh) ? 1.0 : 0.0;
   out_brk_dn = (ll > 0 && close_m15 < ll) ? 1.0 : 0.0;

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   long   spread_pts_long = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   double spread_price    = spread_pts_long * point;
   out_spread_to_atr      = (atr_m15 > 0) ? spread_price / atr_m15 : 0.0;
   out_last_bar_body_atr  = (atr_m15 > 0) ? MathAbs(close_m15 - open_m15) / atr_m15 : 0.0;
   out_atr_abs            = atr_m15;
   return true;
}

//+------------------------------------------------------------------+
//| Build request JSON                                               |
//+------------------------------------------------------------------+
string BuildDecisionRequestJson(const string request_id,
                                double adx_strength, double di_balance,
                                double ma_gap_atr,   double atr_pct,
                                double rsi_centered, double bb_pos,
                                double price_vs_ma_fast_atr,
                                double brk_up, double brk_dn,
                                double spread_to_atr, double last_bar_body_atr,
                                double atr_abs)
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double last_close = iClose(_Symbol, PERIOD_M15, 1);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   long spread_pts = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   long stops_lvl  = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   long freeze_lvl = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL);
   double tick_size  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);

   long   login    = AccountInfoInteger(ACCOUNT_LOGIN);
   double balance  = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity   = AccountInfoDouble(ACCOUNT_EQUITY);
   double freem    = AccountInfoDouble(ACCOUNT_FREEMARGIN);
   double mlevel   = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
   string curr     = AccountInfoString(ACCOUNT_CURRENCY);
   long   lev      = AccountInfoInteger(ACCOUNT_LEVERAGE);

   int open_pos = 0;
   double net_pos = 0.0;
   for(int i=0;i<PositionsTotal();i++)
   {
      ulong tk = PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionSelectByTicket(tk) && PositionGetString(POSITION_SYMBOL)==_Symbol)
      {
         open_pos++;
         double v = PositionGetDouble(POSITION_VOLUME);
         long type = PositionGetInteger(POSITION_TYPE);
         net_pos += (type==POSITION_TYPE_BUY) ? v : -v;
      }
   }
   string side = (net_pos > 0) ? "long" : ((net_pos < 0) ? "short" : "flat");

   string halted = g_halt_runtime ? "true" : "false";

   string s = "{";
   s += "\"schema_version\":\"trade-decision-request.v1\",";
   s += "\"request_id\":\""+request_id+"\",";
   s += "\"mode\":\"live\",";
   s += "\"timestamp_utc\":\""+IsoUtcNow()+"\",";
   s += "\"symbol\":\""+_Symbol+"\",";
   s += "\"timeframe\":\"M15\",";
   s += "\"bar_index\":"+IntegerToString((long)g_last_bar_time)+",";

   s += "\"market\":{";
   s += "\"bid\":"+JsonNum(bid,digits)+",";
   s += "\"ask\":"+JsonNum(ask,digits)+",";
   s += "\"last_close\":"+JsonNum(last_close,digits)+",";
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
   s += "\"net_position\":"+JsonNum(net_pos,2)+",";
   s += "\"avg_price\":0.0,";
   s += "\"floating_pnl\":0.0,";
   s += "\"open_positions_count\":"+IntegerToString(open_pos)+",";
   s += "\"pending_orders_count\":0,";
   s += "\"side\":\""+side+"\"";
   s += "},";

   s += "\"risk_state\":{";
   s += "\"max_risk_per_trade_pct\":"+JsonNum(InpMaxRiskPerTradePct,4)+",";
   s += "\"max_symbol_exposure_lots\":"+JsonNum(InpMaxSymbolExposure,2)+",";
   s += "\"daily_drawdown_pct\":0.0,";
   s += "\"consecutive_losses\":0,";
   s += "\"trading_halted\":"+halted;
   s += "},";

   s += "\"features\":{";
   s += "\"context_tf\":{";
   s += "\"adx_strength\":"+JsonNum(adx_strength,4)+",";
   s += "\"di_balance\":"+JsonNum(di_balance,4)+",";
   s += "\"ma_gap_atr\":"+JsonNum(ma_gap_atr,4)+",";
   s += "\"atr_pct\":"+JsonNum(atr_pct,6);
   s += "},";
   s += "\"decision_tf\":{";
   s += "\"rsi_centered\":"+JsonNum(rsi_centered,4)+",";
   s += "\"bb_pos\":"+JsonNum(bb_pos,4)+",";
   s += "\"price_vs_ma_fast_atr\":"+JsonNum(price_vs_ma_fast_atr,4)+",";
   s += "\"breakout_up_flag\":"+JsonNum(brk_up,1)+",";
   s += "\"breakout_dn_flag\":"+JsonNum(brk_dn,1);
   s += "},";
   s += "\"execution_tf\":{";
   s += "\"spread_to_atr_ratio\":"+JsonNum(spread_to_atr,4)+",";
   s += "\"last_bar_body_atr\":"+JsonNum(last_bar_body_atr,4)+",";
   s += "\"atr_abs\":"+JsonNum(atr_abs,digits);
   s += "}";
   s += "}";
   s += "}";
   return s;
}

//+------------------------------------------------------------------+
//| Minimal JSON value extractors (flat lookups)                     |
//+------------------------------------------------------------------+
string FindStringField(const string &json, const string &key)
{
   string needle = "\"" + key + "\":\"";
   int p = StringFind(json, needle);
   if(p < 0) return "";
   int start = p + StringLen(needle);
   int end = StringFind(json, "\"", start);
   if(end < 0) return "";
   return StringSubstr(json, start, end - start);
}

double FindNumberField(const string &json, const string &key, double defv=0.0)
{
   string needle = "\"" + key + "\":";
   int p = StringFind(json, needle);
   if(p < 0) return defv;
   int start = p + StringLen(needle);
   int end = start;
   int n = StringLen(json);
   while(end < n)
   {
      ushort c = StringGetCharacter(json, end);
      if(c==',' || c=='}' || c==']' || c=='\n' || c=='\r') break;
      end++;
   }
   string raw = StringSubstr(json, start, end - start);
   StringReplace(raw, " ", "");
   if(raw=="null" || raw=="") return defv;
   return StringToDouble(raw);
}

//+------------------------------------------------------------------+
//| HTTP call                                                        |
//+------------------------------------------------------------------+
bool CallAdapter(const string &url, const string &body, string &out_response)
{
   string headers = "Content-Type: application/json\r\n";
   char post[]; char result[]; string result_headers;
   StringToCharArray(body, post, 0, StringLen(body), CP_UTF8);
   ResetLastError();
   int code = WebRequest("POST", url, headers, InpHttpTimeoutMs, post, result, result_headers);
   if(code == -1)
   {
      PrintFormat("WebRequest failed err=%d url=%s (allowlist URL in Tools->Options->Expert Advisors).",
                  GetLastError(), url);
      return false;
   }
   out_response = CharArrayToString(result, 0, -1, CP_UTF8);
   if(code != 200)
   {
      PrintFormat("Adapter HTTP %d: %s", code, out_response);
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| Execution                                                        |
//+------------------------------------------------------------------+
void TryExecute(const string &json_response)
{
   string status = FindStringField(json_response, "status");
   string action = FindStringField(json_response, "action");
   string side   = FindStringField(json_response, "side");
   string otype  = FindStringField(json_response, "order_type");
   double lots   = FindNumberField(json_response, "lots", 0.0);
   double sl     = FindNumberField(json_response, "sl", 0.0);
   double tp     = FindNumberField(json_response, "tp", 0.0);
   double conf   = FindNumberField(json_response, "confidence", 0.0);
   int    dev    = (int)FindNumberField(json_response, "max_deviation_points", 20);

   PrintFormat("[Decision] status=%s action=%s side=%s lots=%.2f sl=%.2f tp=%.2f conf=%.2f",
               status, action, side, lots, sl, tp, conf);

   if(status != "ok") return;
   if(action != "open") return;
   if(!InpEnableTrading) { Print("Trading disabled by input."); return; }
   if(lots <= 0) return;

   // Single-position guard
   for(int i=0;i<PositionsTotal();i++)
   {
      ulong tk = PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionSelectByTicket(tk) && PositionGetString(POSITION_SYMBOL)==_Symbol &&
         PositionGetInteger(POSITION_MAGIC)==InpMagic)
      {
         Print("Already have position on symbol; skipping.");
         return;
      }
   }

   Trade.SetDeviationInPoints(dev);

   double price = (side=="buy") ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                                : SymbolInfoDouble(_Symbol, SYMBOL_BID);

   bool ok = false;
   if(side=="buy")  ok = Trade.Buy(lots, _Symbol, price, sl, tp, "qlip-mvp");
   else if(side=="sell") ok = Trade.Sell(lots, _Symbol, price, sl, tp, "qlip-mvp");

   if(!ok)
   {
      PrintFormat("Order failed: retcode=%u (%s)", Trade.ResultRetcode(), Trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
void OnTimer()
{
   if(!IsNewCompletedM15Bar()) return;
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) return;
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED)) return;

   double adx_strength, di_balance, ma_gap_atr, atr_pct;
   double rsi_centered, bb_pos, price_vs_ma_fast_atr;
   double brk_up, brk_dn;
   double spread_to_atr, last_bar_body_atr, atr_abs;

   if(!BuildFeatures(adx_strength, di_balance, ma_gap_atr, atr_pct,
                     rsi_centered, bb_pos, price_vs_ma_fast_atr,
                     brk_up, brk_dn, spread_to_atr, last_bar_body_atr, atr_abs))
   {
      Print("Feature build failed (warming up?)");
      return;
   }

   string rid = StringFormat("%s-M15-%s", _Symbol, IsoUtcFromTime(g_last_bar_time));
   string body = BuildDecisionRequestJson(rid, adx_strength, di_balance, ma_gap_atr, atr_pct,
                                          rsi_centered, bb_pos, price_vs_ma_fast_atr,
                                          brk_up, brk_dn, spread_to_atr, last_bar_body_atr, atr_abs);

   string resp;
   if(!CallAdapter(InpAdapterUrl, body, resp))
   {
      // Fail-closed: do nothing; remain flat.
      return;
   }

   TryExecute(resp);
}

//+------------------------------------------------------------------+
//| OnTradeTransaction: light reconciliation + POST event back       |
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
   int code = WebRequest("POST", InpEventUrl, headers, 1000, post, res, rh);
   if(code == -1)
      PrintFormat("Event POST failed err=%d", GetLastError());
}

//+------------------------------------------------------------------+
void OnTick() { /* timer-driven; do nothing on tick */ }
//+------------------------------------------------------------------+
