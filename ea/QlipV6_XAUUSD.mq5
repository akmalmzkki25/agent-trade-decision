//+------------------------------------------------------------------+
//| QlipV6_XAUUSD.mq5                                                |
//| V6 data plane (phase 1, DATA-ONLY).                              |
//| - OnInit: closed-bar backfill to /v6/bars/backfill               |
//| - Every M15 close: v6.snapshot.1 to /v6/snapshot                 |
//| - Every 2 s: v6.poll.1 to /v6/intent/poll, response only logged  |
//| This build contains no order code at all; execution arrives in   |
//| phase 5 behind the adapter's demo-only policy.                   |
//| Magic 250570 (250570..250579 reserved), globals prefix QlipV6_.  |
//+------------------------------------------------------------------+
#property copyright   "Qlip"
#property version     "6.00"
#property description "Qlip V6 XAUUSD - data-only: backfill, M15 snapshots and intent polling. Never trades."

#include "QlipV6/Json.mqh"
#include "QlipV6/Http.mqh"
#include "QlipV6/Persist.mqh"
#include "QlipV6/Market.mqh"
#include "QlipV6/Calendar.mqh"
#include "QlipV6/Snapshot.mqh"
#include "QlipV6/Backfill.mqh"

//--- inputs
input string InpAdapterBase        = "http://127.0.0.1:8765"; // Adapter base URL (allow it in WebRequest)
input int    InpHttpTimeoutMs      = 1500;   // Snapshot timeout, ms
input int    InpPollTimeoutMs      = 600;    // Poll timeout, ms
input int    InpBackfillTimeoutMs  = 3000;   // Backfill timeout per chunk, ms
input long   InpMagic              = 250570; // V6 magic (250570..250579)
input int    InpPollIntervalMs     = 2000;   // Poll interval, ms
input int    InpSnapshotRetryS     = 30;     // Keep retrying a failed snapshot for, s
input bool   InpBackfillEnabled    = true;   // Send history on start
input int    InpBackfillDaysM1     = 1;      // Backfill days, M1
input int    InpBackfillDaysM5     = 3;      // Backfill days, M5
input int    InpBackfillDaysM15    = 15;     // Backfill days, M15
input int    InpBackfillDaysH1     = 30;     // Backfill days, H1
input int    InpBackfillDaysD1     = 60;     // Backfill days, D1
input int    InpBackfillChunkRows  = 2500;   // Rows per backfill request (<= 2500)

//--- constants
#define TIMER_PERIOD_MS    1000
#define MAGIC_FIRST        250570
#define MAGIC_LAST         250579
#define TIMEOUT_MIN_MS     100
#define TIMEOUT_MAX_MS     10000
#define LOG_THROTTLE_S     60
#define COMMAND_LOG_CHARS  20
#define PATH_BACKFILL      "/v6/bars/backfill"
#define PATH_SNAPSHOT      "/v6/snapshot"
#define PATH_POLL          "/v6/intent/poll"

//--- state
CBackfill g_backfill;
datetime  g_last_m15_open = 0;    // server open time of the forming M15 bar
datetime  g_pending_bar = 0;      // closed M15 bar still waiting to be sent
datetime  g_pending_since = 0;    // UTC time the pending bar was detected
bool      g_probe_sent = false;   // probe block goes out once per EA session

ulong     g_last_poll_ms = 0;
bool      g_poll_seen = false;
string    g_last_command = "";
bool      g_last_has_intent = false;
datetime  g_last_poll_error_log = 0;

//+------------------------------------------------------------------+
string AdapterUrl(const string path) { return InpAdapterBase + path; }

bool CheckInput(const bool ok, const string message)
{
   if(!ok)
      Print("V6 input error: ", message);
   return ok;
}

bool MsInRange(const int value) { return value >= TIMEOUT_MIN_MS && value <= TIMEOUT_MAX_MS; }

bool InputsAreValid(void)
{
   bool url_ok = StringFind(InpAdapterBase, "http://") == 0 || StringFind(InpAdapterBase, "https://") == 0;
   bool ok = CheckInput(url_ok, "InpAdapterBase must start with http:// or https://");
   ok = CheckInput(InpMagic >= MAGIC_FIRST && InpMagic <= MAGIC_LAST, "InpMagic must be within 250570..250579") && ok;
   ok = CheckInput(InpBackfillChunkRows >= 1 && InpBackfillChunkRows <= BACKFILL_MAX_CHUNK_ROWS,
                   "InpBackfillChunkRows must be within 1..2500") && ok;
   ok = CheckInput(MsInRange(InpHttpTimeoutMs) && MsInRange(InpPollTimeoutMs)
                   && MsInRange(InpBackfillTimeoutMs) && MsInRange(InpPollIntervalMs),
                   "timeouts and the poll interval must be within 100..10000 ms") && ok;
   ok = CheckInput(InpSnapshotRetryS >= 0, "InpSnapshotRetryS must not be negative") && ok;
   return ok;
}

void ConfigureBackfill(void)
{
   int days[BACKFILL_TF_COUNT];
   days[0] = InpBackfillDaysM1;
   days[1] = InpBackfillDaysM5;
   days[2] = InpBackfillDaysM15;
   days[3] = InpBackfillDaysH1;
   days[4] = InpBackfillDaysD1;
   g_backfill.Configure(AdapterUrl(PATH_BACKFILL), InpBackfillTimeoutMs, InpBackfillChunkRows,
                        InpBackfillEnabled, days);
}

//+------------------------------------------------------------------+
//| Snapshot on every M15 close                                      |
//+------------------------------------------------------------------+
bool TrySendSnapshot(const datetime bar_open_server)
{
   string body = BuildSnapshotJson(InpMagic, bar_open_server, !g_probe_sent, PersistHaltRequested());
   if(body == "")
      return false;
   HttpResult result;
   if(!HttpPostJson(AdapterUrl(PATH_SNAPSHOT), body, InpHttpTimeoutMs, 1, result))
   {
      Print(HttpDescribeFailure("snapshot", AdapterUrl(PATH_SNAPSHOT), result));
      return false;
   }
   PrintFormat("V6 snapshot sent: bar %s (server) HTTP %d, %d bytes%s",
               TimeToString(bar_open_server, TIME_DATE | TIME_MINUTES), result.status,
               StringLen(body), g_probe_sent ? "" : ", with probe");
   g_probe_sent = true;
   return true;
}

// A new forming bar means the previous one closed. After a gap (weekend,
// feed outage) the previous bar closed long ago and is not reported.
void DetectClosedBar(void)
{
   datetime forming = iTime(_Symbol, PERIOD_M15, 0);
   if(forming == 0 || forming == g_last_m15_open)
      return;
   bool first_sighting = (g_last_m15_open == 0);
   g_last_m15_open = forming;
   if(first_sighting)
      return;
   datetime closed = iTime(_Symbol, PERIOD_M15, 1);
   if(closed == 0 || (long)TimeTradeServer() - ((long)closed + M15_SECONDS) > M15_SECONDS)
   {
      PrintFormat("V6 snapshot skipped: previous M15 bar %s is not the one that just closed",
                  TimeToString(closed, TIME_DATE | TIME_MINUTES));
      return;
   }
   g_pending_bar = closed;
   g_pending_since = TimeGMT();
}

void ServiceSnapshot(void)
{
   DetectClosedBar();
   if(g_pending_bar == 0)
      return;
   if((long)TimeGMT() - (long)g_pending_since > InpSnapshotRetryS)
   {
      PrintFormat("V6 snapshot dropped: bar %s could not be delivered within %d s",
                  TimeToString(g_pending_bar, TIME_DATE | TIME_MINUTES), InpSnapshotRetryS);
      g_pending_bar = 0;
      return;
   }
   if(TrySendSnapshot(g_pending_bar))
      g_pending_bar = 0;
}

//+------------------------------------------------------------------+
//| Intent poll (logged only in the data-only phase)                 |
//+------------------------------------------------------------------+
bool IsKnownCommand(const string command)
{
   return command == "NONE" || command == "FLATTEN" || command == "CANCEL_PENDING";
}

void LogPollProblem(const string message)
{
   if((long)TimeGMT() - (long)g_last_poll_error_log < LOG_THROTTLE_S)
      return;
   g_last_poll_error_log = TimeGMT();
   Print(message);
}

void HandlePollResponse(const string json)
{
   string command = "";
   bool has_intent = false;
   if(!JsonGetString(json, "command", command) || !JsonGetBool(json, "has_intent", has_intent))
   {
      LogPollProblem("V6 poll: response is not a flat v6.intent.1 object; ignored");
      return;
   }
   if(!IsKnownCommand(command))
   {
      LogPollProblem("V6 poll: unknown command '" + SanitizeAscii(command, COMMAND_LOG_CHARS) + "'; ignored");
      return;
   }
   if(g_poll_seen && command == g_last_command && has_intent == g_last_has_intent)
      return;
   PrintFormat("V6 poll: command=%s has_intent=%s (data-only build: nothing is executed)",
               command, has_intent ? "true" : "false");
   g_poll_seen = true;
   g_last_command = command;
   g_last_has_intent = has_intent;
}

void ServicePoll(void)
{
   ulong now_ms = GetTickCount64();
   if(g_last_poll_ms != 0 && now_ms - g_last_poll_ms < (ulong)InpPollIntervalMs)
      return;
   g_last_poll_ms = now_ms;
   string body = BuildPollJson(InpMagic, PersistHaltRequested());
   if(body == "")
      return;
   HttpResult result;
   if(!HttpPostJson(AdapterUrl(PATH_POLL), body, InpPollTimeoutMs, 1, result))
   {
      LogPollProblem(HttpDescribeFailure("poll", AdapterUrl(PATH_POLL), result));
      return;
   }
   HandlePollResponse(result.body);
}

//+------------------------------------------------------------------+
//| Event handlers                                                   |
//+------------------------------------------------------------------+
int OnInit(void)
{
   if(!InputsAreValid())
      return INIT_PARAMETERS_INCORRECT;
   if(!JsonSelfTest())
   {
      Print("V6: JSON self-test failed; refusing to start");
      return INIT_FAILED;
   }
   ConfigureBackfill();
   g_last_m15_open = iTime(_Symbol, PERIOD_M15, 0);
   g_pending_bar = 0;
   g_probe_sent = false;
   g_poll_seen = false;

   DomTrackerStart();
   if(!EventSetMillisecondTimer(TIMER_PERIOD_MS))
   {
      PrintFormat("V6: timer could not be started (err=%d)", GetLastError());
      return INIT_FAILED;
   }
   PrintFormat("V6 EA %s started (DATA-ONLY). symbol=%s magic=%I64d login=%s mode=%s offset=%d s adapter=%s",
               V6_EA_VERSION, _Symbol, InpMagic, LoginText(), TradeModeName(),
               ServerGmtOffsetSeconds(), InpAdapterBase);
   g_backfill.Service();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   DomTrackerStop();
   PrintFormat("V6 EA stopped (reason=%d)", reason);
}

// Snapshot first: it is the time-critical message of the cycle.
void OnTimer(void)
{
   DomTrackerSample();
   ServiceSnapshot();
   ServicePoll();
   g_backfill.Service();
}

void OnBookEvent(const string &symbol)
{
   if(symbol == _Symbol)
      DomTrackerSample();
}
//+------------------------------------------------------------------+
