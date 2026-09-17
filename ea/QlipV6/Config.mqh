//+------------------------------------------------------------------+
//| QlipV6/Config.mqh                                                |
//| Run-time configuration copied from the EA inputs in OnInit, and  |
//| the hard ceilings no input can loosen (they mirror the adapter's |
//| app/v6/risk/limits.py; configuration may only tighten them).     |
//+------------------------------------------------------------------+
#ifndef QLIPV6_CONFIG_MQH
#define QLIPV6_CONFIG_MQH

#define V6_MAGIC_FIRST           250570
#define V6_MAGIC_LAST            250579
// limits.MAX_EXECUTE_LOTS: demo execution trades the minimum lot, never more.
#define V6_MAX_EXECUTE_LOTS      0.03
// 1 % of the $2,000 sizing basis, twice the adapter's $10 budget per trade.
#define V6_MAX_RISK_USD_CEILING  50.0
// limits.MAX_DAILY_LOSS_PCT.
#define V6_MAX_BREAKER_PCT       3.0
#define CONFIG_TIMEOUT_MIN_MS    100
#define CONFIG_TIMEOUT_MAX_MS    10000
#define MINUTES_PER_HOUR         60
#define HOURS_PER_DAY            24
#define CLOCK_TEXT_CHARS         5     // "HH:MM"
#define CLOCK_COLON_INDEX        2
#define CLOCK_MINUTES_INDEX      3
#define CLOCK_FIELD_CHARS        2

struct V6Config
{
   string            adapter_base;
   long              magic;
   bool              execute_input;      // InpExecute
   double            max_lots;
   double            max_risk_usd;
   double            breaker_pct;
   string            flatten_text;
   int               flatten_minute;     // minute of the server day
   string            key_file;           // relative to MQL5\Files
   int               http_timeout_ms;
   int               poll_timeout_ms;
   int               outbox_timeout_ms;
   int               backfill_timeout_ms;
   int               poll_interval_ms;
   int               snapshot_retry_s;
   bool              selftest_ok;        // set by the OnInit HMAC self-test
};

V6Config g_cfg;

bool ConfigCheck(const bool ok, const string message)
{
   if(!ok)
      Print("V6 input error: ", message);
   return ok;
}

bool MsInRange(const int value)
{
   return value >= CONFIG_TIMEOUT_MIN_MS && value <= CONFIG_TIMEOUT_MAX_MS;
}

bool IsDecimalField(const string text)
{
   if(StringLen(text) != CLOCK_FIELD_CHARS)
      return false;
   for(int i = 0; i < CLOCK_FIELD_CHARS; i++)
   {
      ushort c = StringGetCharacter(text, i);
      if(c < '0' || c > '9')
         return false;
   }
   return true;
}

// "HH:MM" on a 24-hour clock -> minute of the day.
bool ParseClockMinute(const string text, int &minute_of_day)
{
   if(StringLen(text) != CLOCK_TEXT_CHARS || StringGetCharacter(text, CLOCK_COLON_INDEX) != ':')
      return false;
   string hours = StringSubstr(text, 0, CLOCK_FIELD_CHARS);
   string minutes = StringSubstr(text, CLOCK_MINUTES_INDEX, CLOCK_FIELD_CHARS);
   if(!IsDecimalField(hours) || !IsDecimalField(minutes))
      return false;
   int h = (int)StringToInteger(hours);
   int m = (int)StringToInteger(minutes);
   if(h >= HOURS_PER_DAY || m >= MINUTES_PER_HOUR)
      return false;
   minute_of_day = h * MINUTES_PER_HOUR + m;
   return true;
}

bool LimitsAreValid(V6Config &c)
{
   bool ok = ConfigCheck(c.max_lots > 0.0 && c.max_lots <= V6_MAX_EXECUTE_LOTS,
                         "InpMaxLots must be within (0, 0.03]");
   ok = ConfigCheck(c.max_risk_usd > 0.0 && c.max_risk_usd <= V6_MAX_RISK_USD_CEILING,
                    "InpMaxRiskUsd must be within (0, 50]") && ok;
   ok = ConfigCheck(c.breaker_pct > 0.0 && c.breaker_pct <= V6_MAX_BREAKER_PCT,
                    "InpDailyBreakerPct must be within (0, 3]") && ok;
   ok = ConfigCheck(ParseClockMinute(c.flatten_text, c.flatten_minute),
                    "InpFlattenServerTime must be HH:MM on a 24-hour clock") && ok;
   return ok;
}

// Validates `c` and fills its derived fields (flatten_minute).
bool ConfigIsValid(V6Config &c)
{
   bool url_ok = StringFind(c.adapter_base, "http://") == 0 || StringFind(c.adapter_base, "https://") == 0;
   bool ok = ConfigCheck(url_ok, "InpAdapterBase must start with http:// or https://");
   ok = ConfigCheck(c.magic >= V6_MAGIC_FIRST && c.magic <= V6_MAGIC_LAST,
                    "InpMagic must be within 250570..250579") && ok;
   ok = LimitsAreValid(c) && ok;
   ok = ConfigCheck(c.key_file != "" && StringFind(c.key_file, "..") < 0,
                    "InpHmacKeyFile must be a file under MQL5\\Files") && ok;
   ok = ConfigCheck(MsInRange(c.http_timeout_ms) && MsInRange(c.poll_timeout_ms)
                    && MsInRange(c.outbox_timeout_ms) && MsInRange(c.backfill_timeout_ms)
                    && MsInRange(c.poll_interval_ms),
                    "timeouts and the poll interval must be within 100..10000 ms") && ok;
   ok = ConfigCheck(c.snapshot_retry_s >= 0, "InpSnapshotRetryS must not be negative") && ok;
   return ok;
}

#endif // QLIPV6_CONFIG_MQH
