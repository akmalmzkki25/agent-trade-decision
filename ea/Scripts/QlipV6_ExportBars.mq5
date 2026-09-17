//+------------------------------------------------------------------+
//| QlipV6_ExportBars.mq5 - closed-bar export for the V6 replay.     |
//| Read-only: writes MQL5/Files/QlipV6/export/<SYMBOL>_<TF>.csv     |
//| (header t,o,h,l,c,tick_volume,spread; t = bar open, UTC epoch s) |
//| and <SYMBOL>_export.json (offset and row counts) for the chart   |
//| symbol. Read by adapter/scripts/v6_replay.py --csv-dir.          |
//|                                                                  |
//| Server time becomes UTC with the V6 EA's rule (Market.mqh): one  |
//| offset, TimeTradeServer() - TimeGMT() rounded to 30 minutes, for |
//| every bar. Bars from before a server DST switch are therefore    |
//| shifted by the DST difference; export spans that stay inside one |
//| DST period when that matters.                                    |
//| Self-contained: the EA includes sit under Experts/.              |
//+------------------------------------------------------------------+
#property copyright   "Qlip"
#property version     "6.00"
#property description "Qlip V6: export closed M1/M5/M15/H1/D1 bars (UTC) to CSV for the replay. Read-only."
#property script_show_inputs

input int InpDaysM1  = 30;   // M1 history, days
input int InpDaysM5  = 90;   // M5 history, days
input int InpDaysM15 = 180;  // M15 history, days
input int InpDaysH1  = 365;  // H1 history, days
input int InpDaysD1  = 730;  // D1 history, days

#define EXPORT_FOLDER      "QlipV6\\export"
#define CSV_HEADER         "t,o,h,l,c,tick_volume,spread"
#define HALF_HOUR_SECONDS  1800
#define SECONDS_PER_DAY    86400
#define MAX_EXPORT_DAYS    3650
#define TF_COUNT           5

//--- clock (same rule as ea/QlipV6/Market.mqh) ----------------------

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

//--- rows (same checks as the EA and the adapter) --------------------

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

string CsvRow(const MqlRates &bar, const int offset_s, const int digits)
{
   return IntegerToString(ServerToUtc(bar.time, offset_s))
          + "," + DoubleToString(bar.open, digits) + "," + DoubleToString(bar.high, digits)
          + "," + DoubleToString(bar.low, digits) + "," + DoubleToString(bar.close, digits)
          + "," + IntegerToString(bar.tick_volume) + "," + IntegerToString(MathMax(bar.spread, 0));
}

//--- export ----------------------------------------------------------

// Writes the closed bars of one timeframe; returns the rows written, -1 on failure.
int ExportTimeframe(const ENUM_TIMEFRAMES tf, const int days, const int offset_s)
{
   string name = TimeframeName(tf);
   int count = MathMin(days, MAX_EXPORT_DAYS) * SECONDS_PER_DAY / PeriodSeconds(tf);
   if(count <= 0)
      return 0;
   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   ResetLastError();
   // Shift 1: the forming bar is never exported.
   int copied = CopyRates(_Symbol, tf, 1, count, rates);
   if(copied <= 0)
   {
      PrintFormat("export %s: history not ready (err=%d)", name, GetLastError());
      return -1;
   }
   string path = EXPORT_FOLDER + "\\" + _Symbol + "_" + name + ".csv";
   ResetLastError();
   int handle = FileOpen(path, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
   {
      PrintFormat("export %s: cannot open MQL5/Files/%s (err=%d)", name, path, GetLastError());
      return -1;
   }
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   FileWriteString(handle, CSV_HEADER + "\r\n");
   int written = 0;
   for(int i = 0; i < copied; i++)
   {
      if(!BarRowIsValid(rates[i]))
         continue;
      FileWriteString(handle, CsvRow(rates[i], offset_s, digits) + "\r\n");
      written++;
   }
   FileClose(handle);
   PrintFormat("export %s: %d closed bars written (%d copied, %d requested) to MQL5/Files/%s",
               name, written, copied, count, path);
   return written;
}

bool WriteMeta(const int offset_s, const string counts)
{
   string path = EXPORT_FOLDER + "\\" + _Symbol + "_export.json";
   ResetLastError();
   int handle = FileOpen(path, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
   {
      PrintFormat("export: cannot open MQL5/Files/%s (err=%d)", path, GetLastError());
      return false;
   }
   FileWriteString(handle, "{\"schema_version\":\"v6.export.1\",\"generated_at_utc\":"
                   + IntegerToString((long)TimeGMT()) + ",\"server_gmt_offset_s\":"
                   + IntegerToString(offset_s) + ",\"rows\":{" + counts + "}}");
   FileClose(handle);
   return true;
}

void OnStart(void)
{
   ENUM_TIMEFRAMES frames[TF_COUNT] = {PERIOD_M1, PERIOD_M5, PERIOD_M15, PERIOD_H1, PERIOD_D1};
   int days[TF_COUNT];
   days[0] = InpDaysM1;
   days[1] = InpDaysM5;
   days[2] = InpDaysM15;
   days[3] = InpDaysH1;
   days[4] = InpDaysD1;
   FolderCreate("QlipV6");
   FolderCreate(EXPORT_FOLDER);
   int offset = ServerGmtOffsetSeconds();
   string counts = "";
   for(int i = 0; i < TF_COUNT; i++)
   {
      int written = ExportTimeframe(frames[i], days[i], offset);
      if(StringLen(counts) > 0)
         StringAdd(counts, ",");
      StringAdd(counts, "\"" + TimeframeName(frames[i]) + "\":" + IntegerToString(written));
   }
   if(WriteMeta(offset, counts))
      PrintFormat("export: done for %s, server offset %d s", _Symbol, offset);
}
//+------------------------------------------------------------------+
