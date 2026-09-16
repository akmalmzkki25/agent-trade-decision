//+------------------------------------------------------------------+
//| QlipV5_XAUUSD.mq5                                                |
//| V5 High-Frequency Scalping Burst Layering                        |
//| - Tick-driven (OnTick) with min-interval throttle                |
//| - 3 micro-market orders per burst (vol_min × MICRO_MULT)         |
//| - Up to 3 bursts per basket (averaging-in)                       |
//| - Basket TP $5 / 0.05% equity, basket SL $30 / 0.30% equity      |
//| - Max basket lifetime 2 min, cooldown 30 sec                     |
//| - Margin level guard (>= 300%) before any burst                  |
//| - Magic 250560..250569                                           |
//+------------------------------------------------------------------+
#property copyright "Qlip"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>

//--- Inputs
input string  InpBurstUrl             = "http://127.0.0.1:8765/v5/burst";
input string  InpEventUrl             = "http://127.0.0.1:8765/v1/events/trade-transaction";
input string  InpBasketResultUrl      = "http://127.0.0.1:8765/v1/events/basket-result";
input int     InpHttpTimeoutMs        = 1500;     // shorter — scalper can't wait
input bool    InpEnableTrading        = true;
input long    InpBaseMagic            = 250560;
input int     InpMagicSlots           = 10;        // reserve 250560..250569
input int     InpMinBurstIntervalMs   = 250;       // self-throttle
input int     InpMaxBasketLifetimeSec = 120;       // 2 min
input int     InpCooldownSec          = 30;
input double  InpMinMarginLevelPct    = 300.0;     // skip burst if below
input double  InpBasketTpUsd          = 5.0;
input double  InpBasketTpPctEquity    = 0.05;
input double  InpBasketSlUsd          = 30.0;
input double  InpBasketSlPctEquity    = 0.30;
input int     InpMaxBurstsPerBasket   = 3;
input int     InpMaxSpreadPoints      = 30;
input int     InpDeviationPoints      = 30;        // slippage tolerance for market
input int     InpSkipLogSeconds       = 60;        // how often to restate why it is idle

//--- Globals
CTrade        Trade;
int           h_bb_m1=-1, h_rsi_m1=-1, h_adx_h1=-1, h_atr_m1=-1;
ulong         g_last_burst_ms = 0;
datetime      g_basket_opened_at = 0;
int           g_basket_bursts = 0;
double        g_basket_start_eq = 0.0;
datetime      g_cooldown_until = 0;

// Tick momentum tracking
double        g_tick_buf[5];
int           g_tick_count = 0;
double        g_vol_buf[50];
int           g_vol_count = 0;

// Basket analytics (fed to /v1/events/basket-result on close)
string        g_basket_id = "";
string        g_basket_side = "";
int           g_basket_positions = 0;
double        g_basket_max_float_dd = 0.0;   // most negative floating PnL seen
double        g_basket_slippage_sum = 0.0;   // points
int           g_basket_slippage_n = 0;
double        g_basket_spread_sum = 0.0;     // points
int           g_basket_spread_n = 0;
int           g_basket_last_latency_ms = 0;

#define GV_PREFIX            "QlipV5_"
#define GV_BASKET_OPENED     GV_PREFIX "BOPEN"
#define GV_BASKET_BURSTS     GV_PREFIX "BURSTS"
#define GV_BASKET_STARTEQ    GV_PREFIX "STARTEQ"
#define GV_COOLDOWN          GV_PREFIX "COOLDOWN"
#define GV_LAST_BURST_MS     GV_PREFIX "LASTMS"

//+------------------------------------------------------------------+
int OnInit()
{
   Trade.SetExpertMagicNumber(InpBaseMagic);
   Trade.SetDeviationInPoints(InpDeviationPoints);
   Trade.SetTypeFilling(ORDER_FILLING_IOC);

   h_bb_m1  = iBands(_Symbol, PERIOD_M1, 20, 0, 2.0, PRICE_CLOSE);
   h_rsi_m1 = iRSI(_Symbol, PERIOD_M1, 14, PRICE_CLOSE);
   h_adx_h1 = iADX(_Symbol, PERIOD_H1, 14);
   h_atr_m1 = iATR(_Symbol, PERIOD_M1, 14);
   if(h_bb_m1==INVALID_HANDLE || h_rsi_m1==INVALID_HANDLE || h_atr_m1==INVALID_HANDLE)
   { Print("V5: indicator init failed"); return INIT_FAILED; }

   ArrayInitialize(g_tick_buf, 0.0);
   ArrayInitialize(g_vol_buf, 0.0);

   // Subscribe to market depth. Most retail FX/CFD feeds refuse; DomImbalance()
   // then returns 0.0 and the scenario simply ignores the order-book term.
   if(MarketBookAdd(_Symbol))
      Print("V5: market depth subscribed (DOM imbalance active)");
   else
      Print("V5: market depth unavailable — using tick-volume proxy only");

   // Restore basket state
   if(GlobalVariableCheck(GV_BASKET_OPENED)) g_basket_opened_at = (datetime)GlobalVariableGet(GV_BASKET_OPENED);
   if(GlobalVariableCheck(GV_BASKET_BURSTS)) g_basket_bursts    = (int)GlobalVariableGet(GV_BASKET_BURSTS);
   if(GlobalVariableCheck(GV_BASKET_STARTEQ)) g_basket_start_eq = GlobalVariableGet(GV_BASKET_STARTEQ);
   if(GlobalVariableCheck(GV_COOLDOWN)) g_cooldown_until = (datetime)GlobalVariableGet(GV_COOLDOWN);

   PrintFormat("V5 EA initialized. magic=%d..%d basket_open=%s bursts=%d start_eq=%.2f cooldown=%s",
               InpBaseMagic, InpBaseMagic + InpMagicSlots - 1,
               TimeToString(g_basket_opened_at, TIME_DATE|TIME_MINUTES),
               g_basket_bursts, g_basket_start_eq,
               TimeToString(g_cooldown_until, TIME_DATE|TIME_MINUTES));

   EventSetMillisecondTimer(100);  // 100ms tick for basket eval (faster than 1s)
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   MarketBookRelease(_Symbol);
   IndicatorRelease(h_bb_m1); IndicatorRelease(h_rsi_m1);
   IndicatorRelease(h_adx_h1); IndicatorRelease(h_atr_m1);
}

//+------------------------------------------------------------------+
//| Micro-volatility: ATR M1 percentile over its own recent history.  |
//| Returns 0..1. The scenario only trades inside the sweet spot.     |
//+------------------------------------------------------------------+
double AtrM1Percentile(int lookback = 50)
{
   double atr[];
   if(CopyBuffer(h_atr_m1, 0, 1, lookback, atr) != lookback) return 0.5;
   double current = atr[lookback - 1];   // most recent of the copied window
   int below = 0;
   for(int i = 0; i < lookback; i++) if(atr[i] <= current) below++;
   return (double)below / (double)lookback;
}

//+------------------------------------------------------------------+
//| Volume Spread Analysis inputs: z-score of the last closed M1 bar's |
//| volume and its range (high-low), each vs the prior `lookback` bars.|
//+------------------------------------------------------------------+
void VsaZScores(double &out_volume_z, double &out_range_z, int lookback = 50)
{
   out_volume_z = 0.0;
   out_range_z = 0.0;

   long   vols[];
   double highs[], lows[];
   if(CopyTickVolume(_Symbol, PERIOD_M1, 1, lookback, vols) != lookback) return;
   if(CopyHigh(_Symbol, PERIOD_M1, 1, lookback, highs) != lookback) return;
   if(CopyLow(_Symbol, PERIOD_M1, 1, lookback, lows) != lookback) return;

   double v_sum = 0.0, r_sum = 0.0;
   double ranges[];
   ArrayResize(ranges, lookback);
   for(int i = 0; i < lookback; i++)
   {
      v_sum += (double)vols[i];
      ranges[i] = highs[i] - lows[i];
      r_sum += ranges[i];
   }
   double v_mean = v_sum / lookback;
   double r_mean = r_sum / lookback;

   double v_var = 0.0, r_var = 0.0;
   for(int i = 0; i < lookback; i++)
   {
      double dv = (double)vols[i] - v_mean;  v_var += dv * dv;
      double dr = ranges[i] - r_mean;        r_var += dr * dr;
   }
   double v_std = MathSqrt(v_var / MathMax(1, lookback - 1));
   double r_std = MathSqrt(r_var / MathMax(1, lookback - 1));

   int last = lookback - 1;   // most recent closed bar in the copied window
   if(v_std > 1e-9) out_volume_z = ((double)vols[last] - v_mean) / v_std;
   if(r_std > 1e-9) out_range_z  = (ranges[last] - r_mean) / r_std;
}

//+------------------------------------------------------------------+
//| Order Book Imbalance from real DOM when the broker supplies it.   |
//| Returns 0.0 when depth is unavailable (most retail FX/CFD feeds). |
//| Range -1..+1: positive = bid-heavy (buy pressure).                |
//+------------------------------------------------------------------+
double DomImbalance(int levels = 5)
{
   MqlBookInfo book[];
   if(!MarketBookGet(_Symbol, book)) return 0.0;
   int n = ArraySize(book);
   if(n == 0) return 0.0;

   double bid_vol = 0.0, ask_vol = 0.0;
   int bids = 0, asks = 0;
   for(int i = 0; i < n; i++)
   {
      if((book[i].type == BOOK_TYPE_BUY || book[i].type == BOOK_TYPE_BUY_MARKET) && bids < levels)
      { bid_vol += (double)book[i].volume; bids++; }
      else if((book[i].type == BOOK_TYPE_SELL || book[i].type == BOOK_TYPE_SELL_MARKET) && asks < levels)
      { ask_vol += (double)book[i].volume; asks++; }
   }
   double total = bid_vol + ask_vol;
   if(total < 1e-9) return 0.0;
   return (bid_vol - ask_vol) / total;
}

//+------------------------------------------------------------------+
double GetBuf(int handle, int buffer, int shift)
{ double tmp[1]; if(CopyBuffer(handle, buffer, shift, 1, tmp) != 1) return EMPTY_VALUE; return tmp[0]; }

ulong NowMs() { return (ulong)GetTickCount(); }

bool IsOurMagic(long m) { return m >= InpBaseMagic && m <= InpBaseMagic + InpMagicSlots - 1; }

//+------------------------------------------------------------------+
//| Basket helpers                                                   |
//+------------------------------------------------------------------+
int BasketOpenCount()
{
   int n = 0;
   for(int i=0; i<PositionsTotal(); i++)
   {
      ulong tk = PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionSelectByTicket(tk))
      {
         long m = PositionGetInteger(POSITION_MAGIC);
         if(IsOurMagic(m) && PositionGetString(POSITION_SYMBOL)==_Symbol) n++;
      }
   }
   return n;
}

double BasketFloatingPnl()
{
   double pnl = 0.0;
   for(int i=0; i<PositionsTotal(); i++)
   {
      ulong tk = PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionSelectByTicket(tk))
      {
         long m = PositionGetInteger(POSITION_MAGIC);
         if(IsOurMagic(m) && PositionGetString(POSITION_SYMBOL)==_Symbol)
         {
            pnl += PositionGetDouble(POSITION_PROFIT);
            pnl += PositionGetDouble(POSITION_SWAP);
         }
      }
   }
   return pnl;
}

//+------------------------------------------------------------------+
//| Split basket PnL into winners and losers before closing, so the   |
//| adapter can compute profit factor without per-deal history reads. |
//+------------------------------------------------------------------+
void BasketGrossSplit(double &out_gross_profit, double &out_gross_loss)
{
   out_gross_profit = 0.0;
   out_gross_loss = 0.0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionSelectByTicket(tk))
      {
         long m = PositionGetInteger(POSITION_MAGIC);
         if(IsOurMagic(m) && PositionGetString(POSITION_SYMBOL) == _Symbol)
         {
            double p = PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
            if(p >= 0) out_gross_profit += p;
            else       out_gross_loss += p;
         }
      }
   }
}

void PostBasketResult(const string &reason, double net_pnl,
                      double gross_profit, double gross_loss, double equity_at_close)
{
   if(g_basket_id == "") return;

   double avg_slip = (g_basket_slippage_n > 0)
                     ? g_basket_slippage_sum / g_basket_slippage_n : 0.0;
   double avg_spread = (g_basket_spread_n > 0)
                       ? g_basket_spread_sum / g_basket_spread_n : 0.0;

   string body = "{";
   body += "\"schema_version\":\"basket-result-event.v1\",";
   body += "\"basket_id\":\""+g_basket_id+"\",";
   body += "\"version\":\"v5\",";
   body += "\"symbol\":\""+_Symbol+"\",";
   body += "\"side\":\""+(g_basket_side == "" ? "none" : g_basket_side)+"\",";
   body += "\"opened_at_utc\":\""+IsoUtcFromTime(g_basket_opened_at)+"\",";
   body += "\"closed_at_utc\":\""+IsoUtcNow()+"\",";
   body += "\"close_reason\":\""+reason+"\",";
   body += "\"bursts\":"+IntegerToString(g_basket_bursts)+",";
   body += "\"positions\":"+IntegerToString(g_basket_positions)+",";
   body += "\"gross_profit\":"+JNum(gross_profit,2)+",";
   body += "\"gross_loss\":"+JNum(gross_loss,2)+",";
   body += "\"net_pnl\":"+JNum(net_pnl,2)+",";
   body += "\"max_floating_dd\":"+JNum(g_basket_max_float_dd,2)+",";
   body += "\"avg_slippage_points\":"+JNum(avg_slip,2)+",";
   body += "\"avg_spread_points\":"+JNum(avg_spread,2)+",";
   body += "\"decision_latency_ms\":"+IntegerToString(g_basket_last_latency_ms)+",";
   body += "\"equity_at_open\":"+JNum(g_basket_start_eq,2)+",";
   body += "\"equity_at_close\":"+JNum(equity_at_close,2);
   body += "}";

   string headers = "Content-Type: application/json\r\n";
   char post[]; char res[]; string rh;
   StringToCharArray(body, post, 0, StringLen(body), CP_UTF8);
   ResetLastError();
   WebRequest("POST", InpBasketResultUrl, headers, 1000, post, res, rh);
}

void ResetBasketState()
{
   g_basket_opened_at = 0;
   g_basket_bursts = 0;
   g_basket_start_eq = 0.0;
   g_basket_id = "";
   g_basket_side = "";
   g_basket_positions = 0;
   g_basket_max_float_dd = 0.0;
   g_basket_slippage_sum = 0.0;
   g_basket_slippage_n = 0;
   g_basket_spread_sum = 0.0;
   g_basket_spread_n = 0;
   GlobalVariableDel(GV_BASKET_OPENED);
   GlobalVariableDel(GV_BASKET_BURSTS);
   GlobalVariableDel(GV_BASKET_STARTEQ);
}

void CloseBasket(const string &reason)
{
   double net_pnl = BasketFloatingPnl();
   double gross_profit = 0.0, gross_loss = 0.0;
   BasketGrossSplit(gross_profit, gross_loss);

   PrintFormat("V5 closing basket: %s (pnl=%.2f)", reason, net_pnl);
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionSelectByTicket(tk))
      {
         long m = PositionGetInteger(POSITION_MAGIC);
         if(IsOurMagic(m) && PositionGetString(POSITION_SYMBOL)==_Symbol)
            Trade.PositionClose(tk);
      }
   }

   PostBasketResult(reason, net_pnl, gross_profit, gross_loss,
                    AccountInfoDouble(ACCOUNT_EQUITY));

   ResetBasketState();
   g_cooldown_until = TimeCurrent() + InpCooldownSec;
   GlobalVariableSet(GV_COOLDOWN, (double)g_cooldown_until);
}

void EvaluateBasket()
{
   if(g_basket_opened_at <= 0 || BasketOpenCount() == 0)
   {
      if(g_basket_opened_at > 0 && BasketOpenCount() == 0)
      {
         // Positions all gone (closed externally/manually). Still report the
         // basket so metrics stay complete, then reset.
         PostBasketResult("ExternalClose", 0.0, 0.0, 0.0,
                          AccountInfoDouble(ACCOUNT_EQUITY));
         ResetBasketState();
         g_cooldown_until = TimeCurrent() + InpCooldownSec;
         GlobalVariableSet(GV_COOLDOWN, (double)g_cooldown_until);
      }
      return;
   }

   double pnl = BasketFloatingPnl();
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double pct = (g_basket_start_eq > 0) ? (pnl / g_basket_start_eq) * 100.0 : 0.0;

   // Track the worst floating PnL this basket ever saw (max floating drawdown).
   if(pnl < g_basket_max_float_dd) g_basket_max_float_dd = pnl;

   // Basket TP — whichever threshold (USD or %) hits first
   if(pnl >= InpBasketTpUsd || pct >= InpBasketTpPctEquity)
   {
      CloseBasket(StringFormat("TP $%.2f (%.3f%%)", pnl, pct));
      return;
   }
   // Basket SL
   if(pnl <= -InpBasketSlUsd || pct <= -InpBasketSlPctEquity)
   {
      CloseBasket(StringFormat("SL $%.2f (%.3f%%)", pnl, pct));
      return;
   }
   // Max lifetime
   if(TimeCurrent() - g_basket_opened_at >= InpMaxBasketLifetimeSec)
   {
      CloseBasket("Max lifetime");
      return;
   }
}

//+------------------------------------------------------------------+
//| Tick momentum + volume tracker                                   |
//+------------------------------------------------------------------+
void OnTickUpdateBuffers()
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   long vol = SymbolInfoInteger(_Symbol, SYMBOL_VOLUME);
   // shift price buffer
   for(int i=ArraySize(g_tick_buf)-1; i>0; i--) g_tick_buf[i] = g_tick_buf[i-1];
   g_tick_buf[0] = bid;
   if(g_tick_count < ArraySize(g_tick_buf)) g_tick_count++;
   // shift volume buffer
   for(int i=ArraySize(g_vol_buf)-1; i>0; i--) g_vol_buf[i] = g_vol_buf[i-1];
   g_vol_buf[0] = (double)vol;
   if(g_vol_count < ArraySize(g_vol_buf)) g_vol_count++;
}

int TickMomentumSigned()
{
   if(g_tick_count < 4) return 0;
   int up=0, dn=0;
   for(int i=0; i<3; i++)
   {
      double d = g_tick_buf[i] - g_tick_buf[i+1];
      if(d > 0) up++;
      else if(d < 0) dn++;
   }
   if(up == 3) return +1;
   if(dn == 3) return -1;
   return 0;
}

double TickVolumeZ()
{
   if(g_vol_count < 10) return 0.0;
   double sum = 0.0; int n = MathMin(g_vol_count, ArraySize(g_vol_buf));
   for(int i=0; i<n; i++) sum += g_vol_buf[i];
   double mean = sum / n;
   double var = 0.0;
   for(int i=0; i<n; i++) { double d = g_vol_buf[i] - mean; var += d*d; }
   double std = MathSqrt(var / MathMax(1, n-1));
   if(std < 1e-9) return 0.0;
   return (g_vol_buf[0] - mean) / std;
}

//+------------------------------------------------------------------+
//| Request payload                                                  |
//+------------------------------------------------------------------+
string IsoUtcFromTime(datetime ts)
{
   MqlDateTime t; TimeToStruct(ts, t);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ",
                       t.year, t.mon, t.day, t.hour, t.min, t.sec);
}
string IsoUtcNow() { return IsoUtcFromTime(TimeGMT()); }
string JNum(double v, int d=6) { return DoubleToString(v, d); }

string BuildBurstRequestJson(const string request_id)
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   long   spread_pts = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double vol_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double vol_min = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vol_max = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   long   login = AccountInfoInteger(ACCOUNT_LOGIN);
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double freem = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double mlevel = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
   string curr = AccountInfoString(ACCOUNT_CURRENCY);
   long lev = AccountInfoInteger(ACCOUNT_LEVERAGE);

   double rsi_m1 = GetBuf(h_rsi_m1, 0, 1);
   double bb_up = GetBuf(h_bb_m1, 1, 1);
   double bb_dn = GetBuf(h_bb_m1, 2, 1);
   double bb_mid = GetBuf(h_bb_m1, 0, 1);
   double close_m1 = iClose(_Symbol, PERIOD_M1, 1);
   double bb_pos = (close_m1 - bb_mid) / MathMax((bb_up - bb_dn)/2.0, 1e-9);
   double rsi_centered = (rsi_m1 - 50.0) / 50.0;

   int tick_mom = TickMomentumSigned();
   double tick_vol_z = TickVolumeZ();
   double atr_pctl = AtrM1Percentile();
   double vsa_vol_z = 0.0, vsa_range_z = 0.0;
   VsaZScores(vsa_vol_z, vsa_range_z);
   double dom_imb = DomImbalance();

   double adx_h1 = GetBuf(h_adx_h1, 0, 1);
   double dip_h1 = GetBuf(h_adx_h1, 1, 1);
   double dim_h1 = GetBuf(h_adx_h1, 2, 1);
   double di_sum = dip_h1 + dim_h1;
   double di_balance = (di_sum > 1e-9) ? (dip_h1 - dim_h1) / di_sum : 0.0;

   ulong now_ms = NowMs();
   int last_burst_ms_ago = (g_last_burst_ms > 0) ? (int)(now_ms - g_last_burst_ms) : 999999;

   string s = "{";
   s += "\"schema_version\":\"v5-burst-request.v1\",";
   s += "\"request_id\":\""+request_id+"\",";
   s += "\"mode\":\"live\",";
   s += "\"timestamp_utc\":\""+IsoUtcNow()+"\",";
   s += "\"symbol\":\""+_Symbol+"\",";
   s += "\"timeframe\":\"TICK\",";
   s += "\"bar_index\":0,";

   s += "\"market\":{";
   s += "\"bid\":"+JNum(bid,digits)+",";
   s += "\"ask\":"+JNum(ask,digits)+",";
   s += "\"last_close\":"+JNum(close_m1,digits)+",";
   s += "\"spread_points\":"+IntegerToString(spread_pts)+",";
   s += "\"digits\":"+IntegerToString(digits)+",";
   s += "\"stops_level_points\":0,\"freeze_level_points\":0,";
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

   s += "\"position\":{\"net_position\":0.0,\"avg_price\":0.0,\"floating_pnl\":0.0,\"open_positions_count\":";
   s += IntegerToString(PositionsTotal())+",\"pending_orders_count\":0,\"side\":\"flat\"},";

   s += "\"risk_state\":{\"max_risk_per_trade_pct\":0.5,\"max_symbol_exposure_lots\":0.30,";
   s += "\"daily_drawdown_pct\":0.0,\"consecutive_losses\":0,\"trading_halted\":false},";

   s += "\"features\":{";
   s += "\"context_tf\":{\"adx_strength\":"+JNum(adx_h1/100.0,4)+",\"di_balance\":"+JNum(di_balance,4)+",\"atr_pct\":0.0},";
   s += "\"decision_tf\":{\"rsi_centered\":"+JNum(rsi_centered,4)+",\"bb_pos\":"+JNum(bb_pos,4)+",";
   s += "\"tick_momentum_signed\":"+IntegerToString(tick_mom)+",";
   s += "\"tick_volume_z\":"+JNum(tick_vol_z,4)+",";
   s += "\"atr_m1_percentile\":"+JNum(atr_pctl,4)+",";
   s += "\"vsa_volume_z\":"+JNum(vsa_vol_z,4)+",";
   s += "\"vsa_range_z\":"+JNum(vsa_range_z,4)+",";
   s += "\"dom_imbalance\":"+JNum(dom_imb,4)+"},";
   s += "\"execution_tf\":{\"spread_to_atr_ratio\":0.0,\"atr_abs\":0.0,";
   s += "\"volume_step\":"+JNum(vol_step,3)+",\"volume_min\":"+JNum(vol_min,3)+",\"volume_max\":"+JNum(vol_max,3);
   s += "}";
   s += "},";

   s += "\"active_basket_bursts\":"+IntegerToString(g_basket_bursts)+",";
   s += "\"last_burst_ms_ago\":"+IntegerToString(last_burst_ms_ago)+",";
   s += "\"margin_level_pct\":"+JNum(mlevel,2);
   s += "}";
   return s;
}

//+------------------------------------------------------------------+
//| HTTP                                                             |
//+------------------------------------------------------------------+
bool CallAdapter(const string &url, const string &body, string &out)
{
   string headers = "Content-Type: application/json\r\nConnection: close\r\n";
   char post[]; char result[]; string rh;
   StringToCharArray(body, post, 0, StringLen(body), CP_UTF8);
   ResetLastError();
   int code = WebRequest("POST", url, headers, InpHttpTimeoutMs, post, result, rh);
   if(code == -1) { PrintFormat("V5 WebRequest err=%d", GetLastError()); return false; }
   out = CharArrayToString(result, 0, -1, CP_UTF8);
   return code == 200;
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

//+------------------------------------------------------------------+
//| Burst execution                                                  |
//+------------------------------------------------------------------+
bool SendMarket(const string &side, double lots, long magic)
{
   MqlTradeRequest req; MqlTradeResult res; MqlTradeCheckResult chk;
   ZeroMemory(req); ZeroMemory(res); ZeroMemory(chk);
   req.action       = TRADE_ACTION_DEAL;
   req.symbol       = _Symbol;
   req.volume       = lots;
   req.type         = (side == "buy") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   req.price        = (side == "buy") ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                                      : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   req.deviation    = InpDeviationPoints;
   req.magic        = magic;
   req.comment      = "qlip-v5";
   req.type_filling = ORDER_FILLING_IOC;

   if(!OrderCheck(req, chk))
   {
      PrintFormat("V5 OrderCheck fail magic=%d retcode=%u (%s)", magic, chk.retcode, chk.comment);
      return false;
   }

   double requested_price = req.price;
   long   spread_at_send = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);

   if(!OrderSend(req, res))
   {
      PrintFormat("V5 OrderSend fail magic=%d retcode=%u (%s)", magic, res.retcode, res.comment);
      return false;
   }

   // Slippage: how far the fill landed from the price we asked for, in points.
   if(res.price > 0 && requested_price > 0)
   {
      double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
      if(point > 0)
      {
         double slip_points = MathAbs(res.price - requested_price) / point;
         g_basket_slippage_sum += slip_points;
         g_basket_slippage_n++;
      }
   }
   g_basket_spread_sum += (double)spread_at_send;
   g_basket_spread_n++;
   g_basket_positions++;
   return true;
}

void ProcessBurstResponse(const string &resp)
{
   string status   = FindStringField(resp, "status");
   string scenario = FindStringField(resp, "scenario");
   string side     = FindStringField(resp, "side_bias");

   string rationale = FindStringField(resp, "rationale_short");

   if(status != "ok")
   { ReportSkip(StringFormat("adapter %s: %s", status, rationale)); return; }

   // The adapter explains which gate declined (ATR band, VSA climax, spread).
   if(scenario == "NONE" || scenario == "")
   { ReportSkip(StringFormat("no setup — %s", rationale)); return; }

   if(!InpEnableTrading)
   { ReportSkip("InpEnableTrading=false (dry run)"); return; }

   int placed = 0;
   for(int i=1; i<=20; i++)
   {
      string nd = "\"layer_id\":"+IntegerToString(i);
      int p = StringFind(resp, nd);
      if(p < 0) continue;
      int section_end = StringFind(resp, "}", p);
      if(section_end < 0) section_end = StringLen(resp);
      string section = StringSubstr(resp, p, section_end - p + 1);

      string otype = FindStringField(section, "order_type");
      double lots  = FindNumberField(section, "lots", 0.0);
      long   magic = (long)FindNumberField(section, "magic", InpBaseMagic);
      if(lots <= 0) continue;

      string send_side = (otype == "buy_market") ? "buy" : ((otype == "sell_market") ? "sell" : "");
      if(send_side == "") continue;
      if(SendMarket(send_side, lots, magic)) placed++;
   }

   if(placed > 0)
   {
      // First burst opens the basket; subsequent bursts accumulate.
      if(g_basket_opened_at == 0)
      {
         g_basket_opened_at = TimeCurrent();
         g_basket_start_eq = AccountInfoDouble(ACCOUNT_EQUITY);
         g_basket_id = StringFormat("%s-V5B-%I64u", _Symbol, NowMs());
         g_basket_side = side;
         g_basket_max_float_dd = 0.0;
         GlobalVariableSet(GV_BASKET_OPENED, (double)g_basket_opened_at);
         GlobalVariableSet(GV_BASKET_STARTEQ, g_basket_start_eq);
      }
      g_basket_last_latency_ms = (int)FindNumberField(resp, "latency_ms", 0);
      g_basket_bursts++;
      g_last_burst_ms = NowMs();
      GlobalVariableSet(GV_BASKET_BURSTS, (double)g_basket_bursts);
      GlobalVariableSet(GV_LAST_BURST_MS, (double)g_last_burst_ms);
      PrintFormat("V5 burst#%d placed: side=%s layers=%d (basket pnl now=%.2f)",
                  g_basket_bursts, side, placed, BasketFloatingPnl());
   }
}

//+------------------------------------------------------------------+
//| Main loop                                                        |
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
//| Skip reporting.                                                   |
//|                                                                   |
//| TryBurst runs on every tick and most ticks are correctly skipped. |
//| Logging each one would flood the journal, but staying silent      |
//| leaves "the bot isn't trading" impossible to diagnose. So report   |
//| a reason when it changes, and re-state the current one            |
//| periodically so the log shows the bot is alive and why it waits.  |
//+------------------------------------------------------------------+
string   g_last_skip_reason = "";
datetime g_last_skip_log = 0;

void ReportSkip(const string reason)
{
   bool changed = (reason != g_last_skip_reason);
   bool due = (TimeCurrent() - g_last_skip_log) >= InpSkipLogSeconds;
   if(changed || due)
   {
      PrintFormat("[V5 idle] %s", reason);
      g_last_skip_reason = reason;
      g_last_skip_log = TimeCurrent();
   }
}

void TryBurst()
{
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
   { ReportSkip("AutoTrading disabled in terminal (toolbar button)"); return; }
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))
   { ReportSkip("Algo trading not allowed for this EA (chart properties)"); return; }

   if(TimeCurrent() < g_cooldown_until)
   { ReportSkip(StringFormat("cooldown for %d more sec",
                             (int)(g_cooldown_until - TimeCurrent()))); return; }

   if(NowMs() - g_last_burst_ms < (ulong)InpMinBurstIntervalMs) return;  // sub-second, not worth logging

   if(g_basket_bursts >= InpMaxBurstsPerBasket)
   { ReportSkip(StringFormat("basket full (%d/%d bursts)",
                             g_basket_bursts, InpMaxBurstsPerBasket)); return; }

   long spread_pts = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(spread_pts > InpMaxSpreadPoints)
   { ReportSkip(StringFormat("spread %d pts > limit %d", (int)spread_pts,
                             InpMaxSpreadPoints)); return; }

   double mlevel = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
   if(mlevel > 0 && mlevel < InpMinMarginLevelPct)
   { ReportSkip(StringFormat("margin level %.0f%% < %.0f%%", mlevel,
                             InpMinMarginLevelPct)); return; }

   string rid = StringFormat("%s-V5-%I64u", _Symbol, NowMs());
   string body = BuildBurstRequestJson(rid);
   if(body == "")
   { ReportSkip("indicator data not ready (warming up)"); return; }

   string resp;
   if(!CallAdapter(InpBurstUrl, body, resp))
   { ReportSkip("adapter unreachable — check it is running and the URL is allowlisted"); return; }

   ProcessBurstResponse(resp);
}

//+------------------------------------------------------------------+
void OnTimer()
{
   EvaluateBasket();
}

void OnTick()
{
   OnTickUpdateBuffers();
   EvaluateBasket();
   TryBurst();
}

//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction& trans,
                        const MqlTradeRequest&     request,
                        const MqlTradeResult&      result)
{
   string trans_type = EnumToString((ENUM_TRADE_TRANSACTION_TYPE)trans.type);
   string body = "{";
   body += "\"schema_version\":\"trade-transaction-event.v1\",";
   body += "\"request_id\":\""+_Symbol+"-v5tx-"+IntegerToString((long)TimeGMT())+"\",";
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
   WebRequest("POST", InpEventUrl, headers, 800, post, res, rh);
}
//+------------------------------------------------------------------+
