//+------------------------------------------------------------------+
//| QlipV6/Market.mqh                                                |
//| Clock conversion, closed-bar rows, tick statistics over a window |
//| (the M15 bar or the M1 bar) and a depth-of-market liveness       |
//| tracker.                                                         |
//|                                                                  |
//| Every time that leaves the EA is UTC epoch seconds. Bar and tick |
//| times in MT5 are server time, so they are shifted by the server  |
//| offset before they are written.                                  |
//+------------------------------------------------------------------+
#ifndef QLIPV6_MARKET_MQH
#define QLIPV6_MARKET_MQH

#include "Json.mqh"

#define HALF_HOUR_SECONDS   1800
#define M15_SECONDS         900
#define M1_SECONDS          60
#define MS_PER_SECOND       1000
#define TICK_WINDOW_SECONDS 900
#define SPREAD_P50          0.50
#define SPREAD_P95          0.95
#define SPREAD_DIGITS       1
#define RV_DIGITS           12
#define DOM_VOLUME_EPSILON  1e-9

//--- clock ---------------------------------------------------------

// TimeTradeServer() and TimeGMT() are both derived from the local clock and
// are read a moment apart, so the raw difference can be a second off. Real
// server zones sit on the half-hour grid, which the adapter also enforces.
int ServerGmtOffsetSeconds(void)
{
   long raw = (long)TimeTradeServer() - (long)TimeGMT();
   long steps = (long)MathRound((double)raw / HALF_HOUR_SECONDS);
   return (int)(steps * HALF_HOUR_SECONDS);
}

long ServerToUtc(const datetime server_time, const int offset_s)
{
   return (long)server_time - offset_s;
}

string TimeframeName(const ENUM_TIMEFRAMES tf)
{
   switch(tf)
   {
      case PERIOD_M1:  return "M1";
      case PERIOD_M5:  return "M5";
      case PERIOD_M15: return "M15";
      case PERIOD_H1:  return "H1";
      case PERIOD_D1:  return "D1";
      default:         return "";
   }
}

//--- bars ----------------------------------------------------------

// Mirrors the adapter's row validation, so one bad broker bar is dropped
// here instead of getting the whole request rejected there.
bool BarRowIsValid(const MqlRates &bar)
{
   if(!MathIsValidNumber(bar.open) || !MathIsValidNumber(bar.high)
      || !MathIsValidNumber(bar.low) || !MathIsValidNumber(bar.close))
      return false;
   if(bar.low <= 0.0 || bar.open <= 0.0 || bar.close <= 0.0)
      return false;
   if(bar.high < MathMax(bar.open, bar.close) || bar.low > MathMin(bar.open, bar.close))
      return false;
   return bar.tick_volume >= 0;
}

// One row: [t_utc, o, h, l, c, tick_volume, spread_points].
string BarRowJson(const MqlRates &bar, const int offset_s, const int digits)
{
   return "[" + JInt(ServerToUtc(bar.time, offset_s))
          + "," + JNum(bar.open, digits) + "," + JNum(bar.high, digits)
          + "," + JNum(bar.low, digits) + "," + JNum(bar.close, digits)
          + "," + JInt(bar.tick_volume) + "," + JInt(MathMax(bar.spread, 0)) + "]";
}

// Writes rates[from..to) as a JSON array, skipping invalid or out-of-order
// rows so the result is always strictly increasing in time.
string BarRowsJson(const MqlRates &rates[], const int from, const int to,
                   const int offset_s, const int digits, int &written)
{
   CJsonArray rows;
   datetime previous = 0;
   for(int i = from; i < to; i++)
   {
      if(!BarRowIsValid(rates[i]) || rates[i].time <= previous)
         continue;
      rows.AddRaw(BarRowJson(rates[i], offset_s, digits));
      previous = rates[i].time;
   }
   written = rows.Count();
   return rows.Text();
}

// Bars that had closed by `close_server`: the newest allowed bar opens one
// full period earlier. Copying by time instead of by shift keeps a late timer
// from slipping a bar that opened after the M15 close into the snapshot.
int CopyClosedRates(const ENUM_TIMEFRAMES tf, const datetime close_server,
                    const int count, MqlRates &rates[])
{
   ArraySetAsSeries(rates, false);
   datetime newest_open = close_server - PeriodSeconds(tf);
   ResetLastError();
   int copied = CopyRates(_Symbol, tf, newest_open, count, rates);
   if(copied < 0)
      PrintFormat("V6 bars %s: CopyRates failed (err=%d)", TimeframeName(tf), GetLastError());
   return MathMax(copied, 0);
}

string ClosedBarsJson(const ENUM_TIMEFRAMES tf, const datetime close_server, const int count,
                      const int offset_s, const int digits)
{
   MqlRates rates[];
   int copied = CopyClosedRates(tf, close_server, count, rates);
   int written = 0;
   return BarRowsJson(rates, 0, copied, offset_s, digits, written);
}

// The closed bar of `tf` that ends at `close_server` as one row [t,o,h,l,c,tv,spr],
// or "" when that bar is not in the history yet (or failed validation).
string ClosedBarRowJson(const ENUM_TIMEFRAMES tf, const datetime close_server,
                        const int offset_s, const int digits)
{
   MqlRates rates[];
   int copied = CopyClosedRates(tf, close_server, 1, rates);
   if(copied != 1 || rates[0].time != close_server - PeriodSeconds(tf)
      || !BarRowIsValid(rates[0]))
      return "";
   return BarRowJson(rates[0], offset_s, digits);
}

//--- tick statistics -----------------------------------------------

struct TickStats
{
   int               window_s;
   int               quote_count;
   long              max_gap_ms;
   double            spread_p50;
   double            spread_p95;
   double            mid_rv;
};

// Longest silence in the window, counting both edges: a feed that stopped
// halfway must not look continuous just because its ticks were dense.
long MaxQuoteGapMs(const MqlTick &ticks[], const int n, const long from_msc, const long to_msc)
{
   if(n <= 0)
      return to_msc - from_msc;
   long gap = MathMax((long)ticks[0].time_msc - from_msc, to_msc - (long)ticks[n - 1].time_msc);
   for(int i = 1; i < n; i++)
      gap = MathMax(gap, (long)ticks[i].time_msc - (long)ticks[i - 1].time_msc);
   return MathMax(gap, 0);
}

double SortedPercentile(const double &sorted[], const double p)
{
   int n = ArraySize(sorted);
   if(n == 0)
      return 0.0;
   int rank = (int)MathCeil(p * n) - 1;
   return sorted[MathMax(0, MathMin(rank, n - 1))];
}

void SpreadPercentiles(const MqlTick &ticks[], const int n, const double point,
                       double &p50, double &p95)
{
   double spreads[];
   ArrayResize(spreads, 0, n);
   for(int i = 0; i < n && point > 0.0; i++)
   {
      if(ticks[i].bid <= 0.0 || ticks[i].ask < ticks[i].bid)
         continue;
      int k = ArraySize(spreads);
      ArrayResize(spreads, k + 1, n);
      spreads[k] = (ticks[i].ask - ticks[i].bid) / point;
   }
   ArraySort(spreads);
   p50 = SortedPercentile(spreads, SPREAD_P50);
   p95 = SortedPercentile(spreads, SPREAD_P95);
}

// Sum of squared log returns of the mid quote, tick to tick.
double MidRealizedVariance(const MqlTick &ticks[], const int n)
{
   double rv = 0.0;
   double previous = 0.0;
   for(int i = 0; i < n; i++)
   {
      if(ticks[i].bid <= 0.0 || ticks[i].ask <= 0.0)
         continue;
      double mid = (ticks[i].bid + ticks[i].ask) / 2.0;
      if(previous > 0.0)
      {
         double r = MathLog(mid / previous);
         rv += r * r;
      }
      previous = mid;
   }
   return MathIsValidNumber(rv) ? rv : 0.0;
}

// Quote activity over the `window_s` seconds before `close_server`,
// [close - window_s, close).
void ComputeTickStatsOver(const datetime close_server, const int window_s, TickStats &stats)
{
   stats.window_s = window_s;
   long to_msc = (long)close_server * MS_PER_SECOND - 1;
   long from_msc = ((long)close_server - window_s) * MS_PER_SECOND;
   MqlTick ticks[];
   ResetLastError();
   int n = CopyTicksRange(_Symbol, ticks, COPY_TICKS_INFO, (ulong)from_msc, (ulong)to_msc);
   if(n < 0)
   {
      PrintFormat("V6 ticks: CopyTicksRange failed (err=%d)", GetLastError());
      n = 0;
   }
   stats.quote_count = n;
   stats.max_gap_ms = MaxQuoteGapMs(ticks, n, from_msc, to_msc);
   SpreadPercentiles(ticks, n, _Point, stats.spread_p50, stats.spread_p95);
   stats.mid_rv = MidRealizedVariance(ticks, n);
}

// Quote activity of the M15 bar that just closed, [close - 900 s, close).
void ComputeTickStats(const datetime close_server, TickStats &stats)
{
   ComputeTickStatsOver(close_server, TICK_WINDOW_SECONDS, stats);
}

string TickStatsJson(const TickStats &stats)
{
   CJsonObject o;
   o.AddInt("window_s", stats.window_s);
   o.AddInt("quote_count", stats.quote_count);
   o.AddInt("max_gap_ms", stats.max_gap_ms);
   o.AddNum("spread_p50_points", stats.spread_p50, SPREAD_DIGITS);
   o.AddNum("spread_p95_points", stats.spread_p95, SPREAD_DIGITS);
   o.AddNum("mid_rv", stats.mid_rv, RV_DIGITS);
   return o.Text();
}

//--- depth of market -----------------------------------------------

// A depth feed whose per-level volumes never change is a price ladder with
// fixed sizes rather than an order book, so it is reported as synthetic.
bool   g_dom_subscribed = false;
bool   g_dom_seen = false;
bool   g_dom_moved = false;
double g_dom_signature = 0.0;

void DomTrackerStart(void)
{
   g_dom_subscribed = MarketBookAdd(_Symbol);
   PrintFormat("V6 depth of market %s", g_dom_subscribed ? "subscribed" : "unavailable");
}

void DomTrackerStop(void)
{
   if(g_dom_subscribed)
      MarketBookRelease(_Symbol);
   g_dom_subscribed = false;
}

double DomVolumeSignature(const MqlBookInfo &book[])
{
   double signature = 0.0;
   int n = ArraySize(book);
   for(int i = 0; i < n; i++)
      signature += (i + 1) * book[i].volume_real;
   return signature;
}

// Book events can arrive hundreds of times a second; once movement has been
// seen the per-session answer is settled, so sampling stops there.
void DomTrackerSample(void)
{
   if(!g_dom_subscribed || g_dom_moved)
      return;
   MqlBookInfo book[];
   if(!MarketBookGet(_Symbol, book) || ArraySize(book) == 0)
      return;
   double signature = DomVolumeSignature(book);
   if(g_dom_seen && MathAbs(signature - g_dom_signature) > DOM_VOLUME_EPSILON)
      g_dom_moved = true;
   g_dom_seen = true;
   g_dom_signature = signature;
}

bool DomLooksSynthetic(void)
{
   return !(g_dom_seen && g_dom_moved);
}

#endif // QLIPV6_MARKET_MQH
