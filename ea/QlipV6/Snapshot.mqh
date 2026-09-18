//+------------------------------------------------------------------+
//| QlipV6/Snapshot.mqh                                              |
//| Builders for the v6.snapshot.1 and v6.poll.1 payloads.           |
//|                                                                  |
//| Field names and number kinds must match                          |
//| adapter/app/v6/schemas/{snapshot,intent}.py exactly: the adapter |
//| forbids unknown fields and never coerces float <-> int.          |
//| tests/v6/test_golden_contract.py keeps the two sides in sync.    |
//+------------------------------------------------------------------+
#ifndef QLIPV6_SNAPSHOT_MQH
#define QLIPV6_SNAPSHOT_MQH

#include "Json.mqh"
#include "Market.mqh"
#include "Calendar.mqh"
#include "Persist.mqh"
#include "Exposure.mqh"

#define V6_EA_VERSION        "6.3.0"
#define SHORT_TEXT_MAX       80
#define SPEC_DIGITS          10
#define CALENDAR_HORIZON_S   86400
// Mirrors EVENT_LOOKBACK_S in adapter/app/v6/market/calendar.py.
#define CALENDAR_LOOKBACK_S  7200
#define PROBE_CALENDAR_S     (7 * 86400)
#define PROBE_TRADE_TICKS_S  3600
#define PROBE_REAL_VOL_BARS  100
#define SPEC_CALC_LOTS       1.0
#define SPEC_CALC_MOVE       1.0

// Bars per timeframe in every snapshot (plan §5), all closed. M1 covers the snapshot's
// M15 bar and the one before (the operator packet shows the last 30 M1 bars), so a
// dropped snapshot leaves no hole in the adapter's M1 history.
#define SNAP_BARS_M1   30
#define SNAP_BARS_M5   48
#define SNAP_BARS_M15  16
#define SNAP_BARS_H1   8
#define SNAP_BARS_D1   3

// What the EA reports about itself in ea_state and in the poll (contract §8.2).
struct EaStatus
{
   bool              execute_enabled;   // would act on a signed intent (halts aside)
   bool              halted;            // QlipV6_HALT or AutoTrading off
   bool              breaker_tripped;   // local daily breaker
   int               outbox_pending;
   string            last_intent_id;    // newest processed intent, "" if none
};

//--- account and symbol --------------------------------------------

// Anything that is not explicitly demo or contest is reported as REAL, the
// most restricted mode on the adapter side. Account data that is not loaded
// yet reads as mode 0 (DEMO), so an unknown login is reported as REAL too.
string TradeModeName(void)
{
   if(AccountInfoInteger(ACCOUNT_LOGIN) <= 0)
      return "REAL";
   ENUM_ACCOUNT_TRADE_MODE mode = (ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE);
   if(mode == ACCOUNT_TRADE_MODE_DEMO)
      return "DEMO";
   if(mode == ACCOUNT_TRADE_MODE_CONTEST)
      return "CONTEST";
   return "REAL";
}

string LoginText(void) { return IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)); }
string ServerText(void) { return SanitizeAscii(AccountInfoString(ACCOUNT_SERVER), SHORT_TEXT_MAX); }

string AccountJson(void)
{
   int d = MoneyDigits();
   CJsonObject o;
   o.AddStr("login", LoginText());
   o.AddStr("trade_mode", TradeModeName());
   o.AddStr("server", ServerText());
   o.AddStr("currency", AccountInfoString(ACCOUNT_CURRENCY));
   o.AddInt("leverage", AccountInfoInteger(ACCOUNT_LEVERAGE));
   o.AddNum("balance", AccountInfoDouble(ACCOUNT_BALANCE), d);
   o.AddNum("equity", AccountInfoDouble(ACCOUNT_EQUITY), d);
   o.AddNum("margin", AccountInfoDouble(ACCOUNT_MARGIN), d);
   o.AddNum("free_margin", AccountInfoDouble(ACCOUNT_MARGIN_FREE), d);
   o.AddNum("margin_level", AccountInfoDouble(ACCOUNT_MARGIN_LEVEL), d);
   return o.Text();
}

// 0 when the terminal cannot price the margin; the sizer treats 0 as unknown.
double MarginPerLot(const ENUM_ORDER_TYPE type, const double price)
{
   double margin = 0.0;
   if(price <= 0.0)
      return 0.0;
   ResetLastError();
   if(!OrderCalcMargin(type, _Symbol, 1.0, price, margin))
   {
      PrintFormat("V6 spec: OrderCalcMargin failed (err=%d)", GetLastError());
      return 0.0;
   }
   return MathMax(margin, 0.0);
}

// Account currency per 1.0 price unit for a BUY of 1.00 lot moved `move` from
// `ask`: the profit for move > 0, the absolute loss for move < 0. Some servers
// report a SYMBOL_TRADE_TICK_VALUE that is 10x off, so the adapter sizes from
// these. 0 when the terminal cannot price it (or the sign is wrong); the
// adapter then falls back to the reported tick value.
double PnlPerPriceUnit(const double ask, const double move)
{
   double pnl = 0.0;
   if(ask <= 0.0 || move == 0.0 || ask + move <= 0.0)
      return 0.0;
   ResetLastError();
   if(!OrderCalcProfit(ORDER_TYPE_BUY, _Symbol, SPEC_CALC_LOTS, ask, ask + move, pnl))
   {
      PrintFormat("V6 spec: OrderCalcProfit failed (err=%d)", GetLastError());
      return 0.0;
   }
   double gain = (move > 0.0) ? pnl : -pnl;
   if(!MathIsValidNumber(gain) || gain <= 0.0)
      return 0.0;
   return gain / MathAbs(move);
}

string SymbolSpecJson(const MqlTick &tick)
{
   CJsonObject o;
   o.AddInt("digits", SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));
   o.AddNum("point", SymbolInfoDouble(_Symbol, SYMBOL_POINT), SPEC_DIGITS);
   o.AddNum("tick_size", SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE), SPEC_DIGITS);
   o.AddNum("tick_value", SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE), SPEC_DIGITS);
   o.AddNum("tick_value_loss", SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS), SPEC_DIGITS);
   o.AddNum("contract_size", SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE), SPEC_DIGITS);
   o.AddNum("volume_min", SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN), VOLUME_DIGITS);
   o.AddNum("volume_step", SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP), VOLUME_DIGITS);
   o.AddNum("volume_max", SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX), VOLUME_DIGITS);
   o.AddInt("stops_level", SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL));
   o.AddInt("freeze_level", SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL));
   o.AddNum("margin_per_lot_buy", MarginPerLot(ORDER_TYPE_BUY, tick.ask), MoneyDigits());
   o.AddNum("margin_per_lot_sell", MarginPerLot(ORDER_TYPE_SELL, tick.bid), MoneyDigits());
   o.AddInt("filling_modes", SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE));
   o.AddInt("expiration_modes", SymbolInfoInteger(_Symbol, SYMBOL_EXPIRATION_MODE));
   o.AddNum("calc_profit_per_price", PnlPerPriceUnit(tick.ask, SPEC_CALC_MOVE), SPEC_DIGITS);
   o.AddNum("calc_loss_per_price", PnlPerPriceUnit(tick.ask, -SPEC_CALC_MOVE), SPEC_DIGITS);
   return o.Text();
}

int SpreadPoints(const MqlTick &tick)
{
   if(_Point <= 0.0 || tick.ask < tick.bid)
      return 0;
   return (int)MathRound((tick.ask - tick.bid) / _Point);
}

string QuoteJson(const MqlTick &tick, const int offset_s)
{
   CJsonObject o;
   o.AddNum("bid", tick.bid, _Digits);
   o.AddNum("ask", tick.ask, _Digits);
   o.AddInt("spread_points", SpreadPoints(tick));
   o.AddInt("time_msc", MathMax((long)tick.time_msc - (long)offset_s * MS_PER_SECOND, 0));
   return o.Text();
}

//--- day, probe and EA state ---------------------------------------

// Realised P&L and the number of entries since the UTC day started, V6
// magic only. Entry deals add only their commission and fee, which brokers
// charge at the fill.
void TodayDealStats(const long magic, const datetime day_start_server, double &realized, int &entries)
{
   realized = 0.0;
   entries = 0;
   if(!HistorySelect(day_start_server, TimeTradeServer() + PeriodSeconds(PERIOD_M1)))
   {
      PrintFormat("V6 day: HistorySelect failed (err=%d)", GetLastError());
      return;
   }
   for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0 || HistoryDealGetInteger(deal, DEAL_MAGIC) != magic
         || HistoryDealGetString(deal, DEAL_SYMBOL) != _Symbol)
         continue;
      realized += HistoryDealGetDouble(deal, DEAL_COMMISSION) + HistoryDealGetDouble(deal, DEAL_FEE);
      if((ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal, DEAL_ENTRY) == DEAL_ENTRY_IN)
      {
         entries++;
         continue;
      }
      realized += HistoryDealGetDouble(deal, DEAL_PROFIT) + HistoryDealGetDouble(deal, DEAL_SWAP);
   }
}

string DayJson(const long magic, const datetime now_utc, const int offset_s)
{
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   datetime day_start_utc = UtcDayStart(now_utc);
   double realized = 0.0;
   int entries = 0;
   TodayDealStats(magic, (datetime)((long)day_start_utc + offset_s), realized, entries);
   CJsonObject o;
   o.AddNum("day_start_equity", PersistDayStartEquity(now_utc, equity), MoneyDigits());
   o.AddNum("realized_today", realized, MoneyDigits());
   o.AddInt("trades_today", entries);
   return o.Text();
}

int NonZeroRealVolumeCount(void)
{
   long volumes[];
   int copied = CopyRealVolume(_Symbol, PERIOD_M1, 1, PROBE_REAL_VOL_BARS, volumes);
   int nonzero = 0;
   for(int i = 0; i < copied; i++)
   {
      if(volumes[i] > 0)
         nonzero++;
   }
   return nonzero;
}

int TradeTickCount(const MqlTick &tick)
{
   MqlTick trades[];
   long to_msc = (long)tick.time_msc;
   long from_msc = to_msc - (long)PROBE_TRADE_TICKS_S * MS_PER_SECOND;
   int n = CopyTicksRange(_Symbol, trades, COPY_TICKS_TRADE, (ulong)from_msc, (ulong)to_msc);
   return MathMax(n, 0);
}

string ProbeJson(const MqlTick &tick, const int offset_s)
{
   CJsonObject o;
   o.AddInt("book_depth", SymbolInfoInteger(_Symbol, SYMBOL_TICKS_BOOKDEPTH));
   o.AddInt("trade_ticks_count", TradeTickCount(tick));
   o.AddInt("real_volume_count", NonZeroRealVolumeCount());
   o.AddBool("dom_synthetic", DomLooksSynthetic());
   o.AddInt("gmt_offset_s", offset_s);
   o.AddBool("dst_active", TimeDaylightSavings() != 0);
   o.AddInt("calendar_events_seen", CalendarUsdEventCount(TimeTradeServer(), PROBE_CALENDAR_S));
   return o.Text();
}

string EaStateJson(const EaStatus &status)
{
   CJsonObject o;
   o.AddStr("ea_version", V6_EA_VERSION);
   o.AddBool("execute_enabled", status.execute_enabled);
   o.AddBool("halted", status.halted);
   o.AddStr("local_breaker", status.breaker_tripped ? "daily" : "none");
   o.AddInt("outbox_pending", status.outbox_pending);
   o.AddStr("last_intent_id", status.last_intent_id);
   return o.Text();
}

//--- payloads ------------------------------------------------------

string SnapshotBarsJson(const datetime close_server, const int offset_s)
{
   CJsonObject o;
   o.AddRaw("M1", ClosedBarsJson(PERIOD_M1, close_server, SNAP_BARS_M1, offset_s, _Digits));
   o.AddRaw("M5", ClosedBarsJson(PERIOD_M5, close_server, SNAP_BARS_M5, offset_s, _Digits));
   o.AddRaw("M15", ClosedBarsJson(PERIOD_M15, close_server, SNAP_BARS_M15, offset_s, _Digits));
   o.AddRaw("H1", ClosedBarsJson(PERIOD_H1, close_server, SNAP_BARS_H1, offset_s, _Digits));
   o.AddRaw("D1", ClosedBarsJson(PERIOD_D1, close_server, SNAP_BARS_D1, offset_s, _Digits));
   return o.Text();
}

// Snapshot for the M15 bar that opened at `bar_open_server` and has closed.
// Returns "" when there is no quote to report.
string BuildSnapshotJson(const long magic, const datetime bar_open_server,
                         const bool with_probe, const EaStatus &status)
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick) || tick.bid <= 0.0 || tick.ask <= 0.0)
      return "";
   int offset = ServerGmtOffsetSeconds();
   datetime now_utc = TimeGMT();
   datetime close_server = bar_open_server + M15_SECONDS;
   long bar_open_utc = ServerToUtc(bar_open_server, offset);
   TickStats stats;
   ComputeTickStats(close_server, stats);

   CJsonObject o;
   o.AddStr("schema_version", "v6.snapshot.1");
   o.AddStr("snapshot_id", "Q6S-" + LoginText() + "-" + JInt(bar_open_utc));
   o.AddStr("symbol", _Symbol);
   o.AddInt("sent_at_epoch", (long)now_utc);
   o.AddInt("server_gmt_offset_s", offset);
   o.AddStr("bar_tf", "M15");
   o.AddInt("bar_open_epoch", bar_open_utc);
   o.AddRaw("account", AccountJson());
   o.AddRaw("symbol_spec", SymbolSpecJson(tick));
   o.AddRaw("quote", QuoteJson(tick, offset));
   o.AddRaw("bars", SnapshotBarsJson(close_server, offset));
   o.AddRaw("ticks", TickStatsJson(stats));
   o.AddRaw("positions", PositionsJson(magic, offset));
   o.AddRaw("pending_orders", PendingOrdersJson(magic, offset));
   o.AddRaw("day", DayJson(magic, now_utc, offset));
   o.AddRaw("calendar", CalendarHighUsdJson(TimeTradeServer(), CALENDAR_LOOKBACK_S,
                                            CALENDAR_HORIZON_S, offset));
   if(with_probe)
      o.AddRaw("probe", ProbeJson(tick, offset));
   else
      o.AddNull("probe");
   o.AddRaw("ea_state", EaStateJson(status));
   return o.Text();
}

// Heartbeat for /v6/intent/poll. Returns "" when there is no quote.
// Minute snapshot for the M1 bar that opened at `bar_open_server` and has closed.
// Returns "" when there is no quote or the bar is not in the history yet.
string BuildMinuteJson(const long magic, const datetime bar_open_server, const EaStatus &status)
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick) || tick.bid <= 0.0 || tick.ask <= 0.0)
      return "";
   int offset = ServerGmtOffsetSeconds();
   datetime close_server = bar_open_server + M1_SECONDS;
   string bar = ClosedBarRowJson(PERIOD_M1, close_server, offset, _Digits);
   if(bar == "")
      return "";
   datetime now_utc = TimeGMT();
   long bar_open_utc = ServerToUtc(bar_open_server, offset);
   TickStats stats;
   ComputeTickStatsOver(close_server, M1_SECONDS, stats);

   CJsonObject o;
   o.AddStr("schema_version", "v6.minute.1");
   o.AddStr("snapshot_id", "Q6M-" + LoginText() + "-" + JInt(bar_open_utc));
   o.AddStr("symbol", _Symbol);
   o.AddInt("sent_at_epoch", (long)now_utc);
   o.AddInt("server_gmt_offset_s", offset);
   o.AddInt("bar_open_epoch", bar_open_utc);
   o.AddRaw("bar", bar);
   o.AddRaw("account", AccountJson());
   o.AddRaw("quote", QuoteJson(tick, offset));
   o.AddRaw("ticks", TickStatsJson(stats));
   o.AddRaw("positions", PositionsJson(magic, offset));
   o.AddRaw("pending_orders", PendingOrdersJson(magic, offset));
   o.AddRaw("day", DayJson(magic, now_utc, offset));
   o.AddRaw("ea_state", EaStateJson(status));
   return o.Text();
}

string BuildPollJson(const long magic, const EaStatus &status)
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick) || tick.bid <= 0.0 || tick.ask <= 0.0)
      return "";
   int positions = 0, orders = 0;
   double floating = 0.0;
   V6Exposure(magic, positions, orders, floating);
   int d = MoneyDigits();
   CJsonObject o;
   o.AddStr("schema_version", "v6.poll.1");
   o.AddStr("login", LoginText());
   o.AddStr("trade_mode", TradeModeName());
   o.AddStr("server", ServerText());
   o.AddInt("sent_at_epoch", (long)TimeGMT());
   o.AddNum("balance", AccountInfoDouble(ACCOUNT_BALANCE), d);
   o.AddNum("equity", AccountInfoDouble(ACCOUNT_EQUITY), d);
   o.AddNum("free_margin", AccountInfoDouble(ACCOUNT_MARGIN_FREE), d);
   o.AddNum("bid", tick.bid, _Digits);
   o.AddNum("ask", tick.ask, _Digits);
   o.AddInt("spread_points", SpreadPoints(tick));
   o.AddInt("open_v6_positions", MathMin(positions, V6_MAX_ROWS));
   o.AddInt("pending_v6_orders", MathMin(orders, V6_MAX_ROWS));
   o.AddNum("floating_pnl_v6", floating, d);
   o.AddStr("last_intent_id", status.last_intent_id);
   o.AddBool("local_halt", status.halted);
   return o.Text();
}

#endif // QLIPV6_SNAPSHOT_MQH
