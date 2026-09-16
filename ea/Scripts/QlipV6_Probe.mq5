//+------------------------------------------------------------------+
//| QlipV6_Probe.mq5 - V6 phase 0 measurements (read-only).          |
//| Prints what the broker reports for the chart symbol and writes   |
//| MQL5/Files/QlipV6/probe.json: contract size vs tick value,       |
//| symbol naming, depth, trade ticks, real volume, calendar, clock. |
//| Self-contained: the EA includes sit under Experts/, a different  |
//| relative path in the repo than in the terminal.                  |
//+------------------------------------------------------------------+
#property copyright   "Qlip"
#property version     "6.00"
#property description "Qlip V6 phase 0 probe: symbol spec, depth, volume, calendar, clock. Read-only."
#property script_show_inputs

input int InpBookSampleSeconds    = 5;    // Depth-of-market sampling time, s
input int InpBookSampleIntervalMs = 250;  // Depth sampling interval, ms
input int InpTradeTicksLookbackS  = 3600; // Trade-tick lookback, s
input int InpRealVolumeBars       = 100;  // M1 bars checked for real volume
input int InpTickVolumeSample     = 10;   // M1 tick volumes printed
input int InpCalendarDays         = 7;    // Calendar horizon, days
input int InpCalendarCodesShown   = 10;   // Calendar events listed

#define PROBE_FOLDER       "QlipV6"
#define PROBE_FILE         "QlipV6\\probe.json"
#define SPEC_DIGITS        10
#define MONEY_DIGITS       2
#define RATIO_DIGITS       6
#define HALF_HOUR_SECONDS  1800
#define SECONDS_PER_DAY    86400
#define MS_PER_SECOND      1000
#define PRINTABLE_FIRST    32
#define PRINTABLE_LAST     126
#define TEXT_MAX           80
#define SPEC_TOLERANCE     0.02
#define XAU_SYMBOLS_MAX    20
#define SESSIONS_PER_DAY   10
#define DAYS_PER_WEEK      7
#define BOOK_EPSILON       1e-9
#define SMALL_LOT          0.01
#define FULL_LOT           1.0
#define PRICE_MOVE         1.0     // $1 move used to ask MT5 for profit per lot
#define DEAL_LOOKBACK_DAYS 3
#define DEALS_EXAMINED     200     // closing deals inspected, newest first
#define DEALS_SHOWN        5
#define MIN_DEAL_MOVE      0.05    // smaller moves make the implied size too noisy

//--- minimal JSON writer -------------------------------------------

string Esc(const string text)
{
   string out = "";
   for(int i = 0; i < StringLen(text); i++)
   {
      ushort c = StringGetCharacter(text, i);
      if(c == '"' || c == '\\')
         StringAdd(out, "\\" + ShortToString(c));
      else if(c >= PRINTABLE_FIRST && c <= PRINTABLE_LAST)
         StringAdd(out, ShortToString(c));
      else
         StringAdd(out, StringFormat("\\u%04x", (int)c));
   }
   return out;
}

string Q(const string text) { return "\"" + Esc(text) + "\""; }
string N(const double value, const int digits) { return MathIsValidNumber(value) ? DoubleToString(value, digits) : "null"; }
string B(const bool value) { return value ? "true" : "false"; }
string I(const long value) { return IntegerToString(value); }

// Appends "key": raw to an object body and echoes it to the Experts log.
// Nested sections are not echoed again: their own keys already were.
void Put(string &body, const string section, const string key, const string raw)
{
   if(StringLen(body) > 0)
      StringAdd(body, ",");
   StringAdd(body, "\"" + key + "\":" + raw);
   if(StringGetCharacter(raw, 0) != '{')
      PrintFormat("probe %s.%s = %s", section, key, raw);
}

string Obj(const string body) { return "{" + body + "}"; }

string Printable(const string text)
{
   string out = "";
   for(int i = 0; i < StringLen(text) && StringLen(out) < TEXT_MAX; i++)
   {
      ushort c = StringGetCharacter(text, i);
      if(c >= PRINTABLE_FIRST && c <= PRINTABLE_LAST)
         StringAdd(out, ShortToString(c));
   }
   return out;
}

//--- symbol --------------------------------------------------------

string SymbolIdentity(void)
{
   string s = "";
   Put(s, "symbol", "name", Q(_Symbol));
   Put(s, "symbol", "description", Q(Printable(SymbolInfoString(_Symbol, SYMBOL_DESCRIPTION))));
   Put(s, "symbol", "path", Q(Printable(SymbolInfoString(_Symbol, SYMBOL_PATH))));
   Put(s, "symbol", "currency_base", Q(SymbolInfoString(_Symbol, SYMBOL_CURRENCY_BASE)));
   Put(s, "symbol", "currency_profit", Q(SymbolInfoString(_Symbol, SYMBOL_CURRENCY_PROFIT)));
   Put(s, "symbol", "currency_margin", Q(SymbolInfoString(_Symbol, SYMBOL_CURRENCY_MARGIN)));
   ENUM_SYMBOL_CALC_MODE calc = (ENUM_SYMBOL_CALC_MODE)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_CALC_MODE);
   Put(s, "symbol", "calc_mode", Q(EnumToString(calc)));
   ENUM_SYMBOL_TRADE_MODE trade = (ENUM_SYMBOL_TRADE_MODE)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE);
   Put(s, "symbol", "trade_mode", Q(EnumToString(trade)));
   return Obj(s);
}

// V5 logged tick_value=0.1 with tick_size=0.01. With a 100 oz contract the
// value should be 1.0, so the ratio below shows which of the three is off.
string SymbolTickSpec(void)
{
   double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double contract = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   double expected = tick_size * contract;
   double ratio = (expected > 0.0) ? tick_value / expected : 0.0;
   string s = "";
   Put(s, "spec", "digits", I(SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)));
   Put(s, "spec", "point", N(SymbolInfoDouble(_Symbol, SYMBOL_POINT), SPEC_DIGITS));
   Put(s, "spec", "tick_size", N(tick_size, SPEC_DIGITS));
   Put(s, "spec", "tick_value", N(tick_value, SPEC_DIGITS));
   Put(s, "spec", "tick_value_profit", N(SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_PROFIT), SPEC_DIGITS));
   Put(s, "spec", "tick_value_loss", N(SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS), SPEC_DIGITS));
   Put(s, "spec", "contract_size", N(contract, SPEC_DIGITS));
   Put(s, "spec", "expected_tick_value", N(expected, SPEC_DIGITS));
   Put(s, "spec", "tick_value_ratio", N(ratio, RATIO_DIGITS));
   Put(s, "spec", "spec_consistent", B(MathAbs(ratio - 1.0) <= SPEC_TOLERANCE));
   return Obj(s);
}

string SymbolTradeLimits(void)
{
   string s = "";
   Put(s, "limits", "volume_min", N(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN), SPEC_DIGITS));
   Put(s, "limits", "volume_step", N(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP), SPEC_DIGITS));
   Put(s, "limits", "volume_max", N(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX), SPEC_DIGITS));
   Put(s, "limits", "stops_level", I(SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL)));
   Put(s, "limits", "freeze_level", I(SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL)));
   Put(s, "limits", "filling_mode", I(SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE)));
   Put(s, "limits", "expiration_mode", I(SymbolInfoInteger(_Symbol, SYMBOL_EXPIRATION_MODE)));
   Put(s, "limits", "order_mode", I(SymbolInfoInteger(_Symbol, SYMBOL_ORDER_MODE)));
   Put(s, "limits", "spread_now", I(SymbolInfoInteger(_Symbol, SYMBOL_SPREAD)));
   Put(s, "limits", "spread_float", B(SymbolInfoInteger(_Symbol, SYMBOL_SPREAD_FLOAT) != 0));
   ENUM_DAY_OF_WEEK triple = (ENUM_DAY_OF_WEEK)SymbolInfoInteger(_Symbol, SYMBOL_SWAP_ROLLOVER3DAYS);
   Put(s, "limits", "swap_rollover3days", Q(EnumToString(triple)));
   Put(s, "limits", "book_depth", I(SymbolInfoInteger(_Symbol, SYMBOL_TICKS_BOOKDEPTH)));
   return Obj(s);
}

// Is the gold symbol suffixed (XAUUSDm, XAUUSD.r ...)? List every XAU name.
string XauSymbols(void)
{
   string items = "";
   int shown = 0;
   for(int i = 0; i < SymbolsTotal(false) && shown < XAU_SYMBOLS_MAX; i++)
   {
      string name = SymbolName(i, false);
      string upper = name;
      StringToUpper(upper);
      if(StringFind(upper, "XAU") < 0)
         continue;
      StringAdd(items, (shown > 0 ? "," : "") + Q(name));
      shown++;
   }
   PrintFormat("probe xau_symbols = [%s]", items);
   return "[" + items + "]";
}

//--- depth of market and volume ------------------------------------

double BookSignature(const MqlBookInfo &book[])
{
   double signature = 0.0;
   for(int i = 0; i < ArraySize(book); i++)
      signature += (i + 1) * book[i].volume_real;
   return signature;
}

// Samples the book for a few seconds: a feed whose per-level volumes never
// change is a synthetic ladder, not an order book.
string BookSection(void)
{
   bool subscribed = MarketBookAdd(_Symbol);
   int samples = 0, with_book = 0, max_levels = 0, changes = 0;
   double previous = 0.0;
   int rounds = InpBookSampleSeconds * MS_PER_SECOND / MathMax(InpBookSampleIntervalMs, 1);
   for(int i = 0; i < rounds && subscribed && !IsStopped(); i++)
   {
      Sleep(InpBookSampleIntervalMs);
      samples++;
      MqlBookInfo book[];
      if(!MarketBookGet(_Symbol, book) || ArraySize(book) == 0)
         continue;
      double signature = BookSignature(book);
      if(with_book > 0 && MathAbs(signature - previous) > BOOK_EPSILON)
         changes++;
      previous = signature;
      with_book++;
      max_levels = MathMax(max_levels, ArraySize(book));
   }
   if(subscribed)
      MarketBookRelease(_Symbol);
   string s = "";
   Put(s, "book", "market_book_add", B(subscribed));
   Put(s, "book", "samples", I(samples));
   Put(s, "book", "samples_with_book", I(with_book));
   Put(s, "book", "max_levels", I(max_levels));
   Put(s, "book", "volume_changes", I(changes));
   Put(s, "book", "volumes_change", B(changes > 0));
   return Obj(s);
}

string TickVolumeSample(void)
{
   long volumes[];
   int copied = CopyTickVolume(_Symbol, PERIOD_M1, 1, InpTickVolumeSample, volumes);
   string items = "";
   for(int i = 0; i < copied; i++)
      StringAdd(items, (i > 0 ? "," : "") + I(volumes[i]));
   return "[" + items + "]";
}

string VolumeSection(void)
{
   MqlTick last, trades[];
   long to_msc = SymbolInfoTick(_Symbol, last) ? (long)last.time_msc : (long)TimeTradeServer() * MS_PER_SECOND;
   long from_msc = to_msc - (long)InpTradeTicksLookbackS * MS_PER_SECOND;
   ResetLastError();
   int trade_count = CopyTicksRange(_Symbol, trades, COPY_TICKS_TRADE, (ulong)from_msc, (ulong)to_msc);
   int trade_error = GetLastError();
   long real[];
   int real_copied = CopyRealVolume(_Symbol, PERIOD_M1, 1, InpRealVolumeBars, real);
   int real_nonzero = 0;
   for(int i = 0; i < real_copied; i++)
      real_nonzero += (real[i] > 0) ? 1 : 0;
   string s = "";
   Put(s, "volume", "trade_ticks_last_hour", I(trade_count));
   Put(s, "volume", "trade_ticks_error", I(trade_error));
   Put(s, "volume", "real_volume_m1_copied", I(real_copied));
   Put(s, "volume", "real_volume_m1_nonzero", I(real_nonzero));
   Put(s, "volume", "tick_volume_m1_sample", TickVolumeSample());
   return Obj(s);
}

//--- account, clock, calendar, margin ------------------------------

string TradeModeName(void)
{
   ENUM_ACCOUNT_TRADE_MODE mode = (ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE);
   if(mode == ACCOUNT_TRADE_MODE_DEMO)
      return "DEMO";
   return (mode == ACCOUNT_TRADE_MODE_CONTEST) ? "CONTEST" : "REAL";
}

string AccountSection(void)
{
   ENUM_ACCOUNT_MARGIN_MODE margin_mode = (ENUM_ACCOUNT_MARGIN_MODE)AccountInfoInteger(ACCOUNT_MARGIN_MODE);
   string s = "";
   Put(s, "account", "login", Q(IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))));
   Put(s, "account", "trade_mode", Q(TradeModeName()));
   Put(s, "account", "server", Q(Printable(AccountInfoString(ACCOUNT_SERVER))));
   Put(s, "account", "company", Q(Printable(AccountInfoString(ACCOUNT_COMPANY))));
   Put(s, "account", "currency", Q(AccountInfoString(ACCOUNT_CURRENCY)));
   Put(s, "account", "leverage", I(AccountInfoInteger(ACCOUNT_LEVERAGE)));
   Put(s, "account", "margin_mode", Q(EnumToString(margin_mode)));
   Put(s, "account", "balance", N(AccountInfoDouble(ACCOUNT_BALANCE), MONEY_DIGITS));
   Put(s, "account", "equity", N(AccountInfoDouble(ACCOUNT_EQUITY), MONEY_DIGITS));
   return Obj(s);
}

// Trade sessions per weekday, in server time; shows the rollover break.
string SessionsJson(void)
{
   string days = "";
   for(int d = 0; d < DAYS_PER_WEEK; d++)
   {
      string ranges = "";
      datetime from = 0, to = 0;
      for(uint k = 0; k < SESSIONS_PER_DAY && SymbolInfoSessionTrade(_Symbol, (ENUM_DAY_OF_WEEK)d, k, from, to); k++)
         StringAdd(ranges, (k > 0 ? "," : "") + Q(TimeToString(from, TIME_MINUTES) + "-" + TimeToString(to, TIME_MINUTES)));
      StringAdd(days, (d > 0 ? "," : "") + Q(EnumToString((ENUM_DAY_OF_WEEK)d)) + ":[" + ranges + "]");
   }
   return "{" + days + "}";
}

string ClockSection(void)
{
   datetime server = TimeTradeServer();
   datetime gmt = TimeGMT();
   long raw = (long)server - (long)gmt;
   long snapped = (long)MathRound((double)raw / HALF_HOUR_SECONDS) * HALF_HOUR_SECONDS;
   string s = "";
   Put(s, "clock", "server_time", Q(TimeToString(server, TIME_DATE | TIME_SECONDS)));
   Put(s, "clock", "gmt_time", Q(TimeToString(gmt, TIME_DATE | TIME_SECONDS)));
   Put(s, "clock", "local_time", Q(TimeToString(TimeLocal(), TIME_DATE | TIME_SECONDS)));
   Put(s, "clock", "gmt_epoch", I((long)gmt));
   Put(s, "clock", "server_minus_gmt_raw_s", I(raw));
   Put(s, "clock", "server_gmt_offset_s", I(snapped));
   Put(s, "clock", "local_dst_s", I(TimeDaylightSavings()));
   Put(s, "clock", "local_gmt_offset_s", I(TimeGMTOffset()));
   Put(s, "clock", "trade_sessions_server", SessionsJson());
   return Obj(s);
}

string CalendarItem(MqlCalendarValue &value, const MqlCalendarEvent &event, const long offset_s)
{
   string item = "";
   StringAdd(item, "{\"code\":" + Q(event.event_code));
   StringAdd(item, ",\"name\":" + Q(Printable(event.name)));
   StringAdd(item, ",\"importance\":" + Q(EnumToString(event.importance)));
   StringAdd(item, ",\"time_utc_epoch\":" + I((long)value.time - offset_s) + "}");
   return item;
}

string CalendarSection(const long offset_s)
{
   MqlCalendarValue values[];
   datetime now = TimeTradeServer();
   ResetLastError();
   bool ok = CalendarValueHistory(values, now, now + InpCalendarDays * SECONDS_PER_DAY, NULL, "USD");
   int error = GetLastError();
   int high = 0, shown = 0;
   string items = "";
   for(int i = 0; i < ArraySize(values); i++)
   {
      MqlCalendarEvent event;
      if(!CalendarEventById(values[i].event_id, event))
         continue;
      high += (event.importance == CALENDAR_IMPORTANCE_HIGH) ? 1 : 0;
      if(shown >= InpCalendarCodesShown)
         continue;
      StringAdd(items, (shown > 0 ? "," : "") + CalendarItem(values[i], event, offset_s));
      shown++;
   }
   string s = "";
   Put(s, "calendar", "ok", B(ok));
   Put(s, "calendar", "error", I(error));
   Put(s, "calendar", "usd_events", I(ArraySize(values)));
   Put(s, "calendar", "usd_high_events", I(high));
   Put(s, "calendar", "first_events", "[" + items + "]");
   return Obj(s);
}

string MarginFor(const double lots, const double price)
{
   double margin = 0.0;
   ResetLastError();
   if(!OrderCalcMargin(ORDER_TYPE_BUY, _Symbol, lots, price, margin))
      return "{\"ok\":false,\"error\":" + I(GetLastError()) + "}";
   return "{\"ok\":true,\"margin\":" + N(margin, MONEY_DIGITS) + "}";
}

string MarginSection(void)
{
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   string s = "";
   Put(s, "margin", "ask", N(ask, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)));
   Put(s, "margin", "buy_0_01_lot", MarginFor(SMALL_LOT, ask));
   Put(s, "margin", "buy_1_lot", MarginFor(FULL_LOT, ask));
   return Obj(s);
}

//--- profit per lot: which of tick_value and contract_size is right --

string ProfitSection(void)
{
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double profit = 0.0;
   ResetLastError();
   bool ok = OrderCalcProfit(ORDER_TYPE_BUY, _Symbol, FULL_LOT, ask, ask + PRICE_MOVE, profit);
   int error = ok ? 0 : GetLastError();
   string s = "";
   Put(s, "profit", "ok", B(ok));
   Put(s, "profit", "error", I(error));
   Put(s, "profit", "buy_1_lot_plus_1_usd", N(profit, MONEY_DIGITS));
   Put(s, "profit", "implied_contract_size", N(profit / PRICE_MOVE, RATIO_DIGITS));
   return Obj(s);
}

double EntryPriceOf(const long position_id, const int count)
{
   for(int i = 0; i < count; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(HistoryDealGetInteger(ticket, DEAL_POSITION_ID) == position_id
         && HistoryDealGetInteger(ticket, DEAL_ENTRY) == DEAL_ENTRY_IN)
         return HistoryDealGetDouble(ticket, DEAL_PRICE);
   }
   return 0.0;
}

// Realised profit / (price move x volume) for one closing deal; 0 when unusable.
double ImpliedFromDeal(const ulong ticket, const int count)
{
   double entry = EntryPriceOf(HistoryDealGetInteger(ticket, DEAL_POSITION_ID), count);
   double exit_price = HistoryDealGetDouble(ticket, DEAL_PRICE);
   double volume = HistoryDealGetDouble(ticket, DEAL_VOLUME);
   // A closing SELL ends a long position, which gains when price rises.
   double direction = (HistoryDealGetInteger(ticket, DEAL_TYPE) == DEAL_TYPE_SELL) ? 1.0 : -1.0;
   double move = (exit_price - entry) * direction;
   if(entry <= 0.0 || volume <= 0.0 || MathAbs(move) < MIN_DEAL_MOVE)
      return 0.0;
   return HistoryDealGetDouble(ticket, DEAL_PROFIT) / (move * volume);
}

bool IsSymbolExit(const ulong ticket)
{
   return HistoryDealGetString(ticket, DEAL_SYMBOL) == _Symbol
          && HistoryDealGetInteger(ticket, DEAL_ENTRY) == DEAL_ENTRY_OUT;
}

string DealsSection(void)
{
   datetime now = TimeCurrent();
   bool ok = HistorySelect(now - DEAL_LOOKBACK_DAYS * SECONDS_PER_DAY, now);
   int count = ok ? HistoryDealsTotal() : 0;
   double implied[];
   string samples = "";
   int examined = 0;
   for(int i = count - 1; i >= 0 && examined < DEALS_EXAMINED; i--)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(!IsSymbolExit(ticket))
         continue;
      examined++;
      double value = ImpliedFromDeal(ticket, count);
      if(value == 0.0)
         continue;
      int n = ArraySize(implied);
      ArrayResize(implied, n + 1);
      implied[n] = value;
      if(n < DEALS_SHOWN)
         StringAdd(samples, (n > 0 ? "," : "") + N(value, RATIO_DIGITS));
   }
   int used = ArraySize(implied);
   ArraySort(implied);
   string s = "";
   Put(s, "deals", "history_ok", B(ok));
   Put(s, "deals", "exits_examined", I(examined));
   Put(s, "deals", "exits_used", I(used));
   Put(s, "deals", "implied_contract_size_median", used > 0 ? N(implied[used / 2], RATIO_DIGITS) : "null");
   Put(s, "deals", "implied_samples", "[" + samples + "]");
   return Obj(s);
}

//--- output --------------------------------------------------------

bool WriteProbeFile(const string json)
{
   FolderCreate(PROBE_FOLDER);
   ResetLastError();
   int handle = FileOpen(PROBE_FILE, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
   {
      PrintFormat("probe: cannot open MQL5/Files/%s (err=%d)", PROBE_FILE, GetLastError());
      return false;
   }
   FileWriteString(handle, json);
   FileClose(handle);
   return true;
}

void OnStart(void)
{
   long offset = (long)MathRound(((double)TimeTradeServer() - (double)TimeGMT()) / HALF_HOUR_SECONDS) * HALF_HOUR_SECONDS;
   string body = "";
   Put(body, "probe", "schema_version", Q("v6.probe.1"));
   Put(body, "probe", "generated_at_utc", I((long)TimeGMT()));
   Put(body, "probe", "symbol", SymbolIdentity());
   Put(body, "probe", "spec", SymbolTickSpec());
   Put(body, "probe", "limits", SymbolTradeLimits());
   Put(body, "probe", "xau_symbols", XauSymbols());
   Put(body, "probe", "book", BookSection());
   Put(body, "probe", "volume", VolumeSection());
   Put(body, "probe", "account", AccountSection());
   Put(body, "probe", "clock", ClockSection());
   Put(body, "probe", "calendar", CalendarSection(offset));
   Put(body, "probe", "margin", MarginSection());
   Put(body, "probe", "profit", ProfitSection());
   Put(body, "probe", "deals", DealsSection());
   if(WriteProbeFile(Obj(body)))
      PrintFormat("probe: written to MQL5/Files/%s", PROBE_FILE);
}
//+------------------------------------------------------------------+
