//+------------------------------------------------------------------+
//| QlipV6_XAUUSD.mq5                                                |
//| V6 execution plane, DEMO accounts only.                          |
//| - OnInit: HMAC self-test, key file, closed-bar backfill          |
//| - Every tick of the 1 s timer, before any HTTP: time barrier,    |
//|   pre-rollover flatten, local daily breaker, trade bookkeeping   |
//| - Every M15 close: v6.snapshot.1 to /v6/snapshot                 |
//| - Every 2 s: v6.poll.1; a signed intent passes the checks of     |
//|   docs/v6-wire-contract.md §8.1 before it is placed, and every   |
//|   outcome is reported through MQL5\Files\QlipV6\outbox.jsonl     |
//| Requests carry X-Qlip6-Ts / X-Qlip6-Sig once the key file        |
//| MQL5\Files\QlipV6\hmac.key is present (v6_sync_ea_key.py).       |
//| Magic 250570 (250570..250579 reserved), globals prefix QlipV6_.  |
//+------------------------------------------------------------------+
#property copyright   "Qlip"
#property version     "6.10"
#property description "Qlip V6 XAUUSD - executes signed adapter intents on DEMO accounts only; snapshots and backfill."

#include "QlipV6/Config.mqh"
#include "QlipV6/Json.mqh"
#include "QlipV6/Hmac.mqh"
#include "QlipV6/Http.mqh"
#include "QlipV6/Persist.mqh"
#include "QlipV6/Market.mqh"
#include "QlipV6/Calendar.mqh"
#include "QlipV6/Snapshot.mqh"
#include "QlipV6/Backfill.mqh"
#include "QlipV6/SelfTest.mqh"
#include "QlipV6/Outbox.mqh"
#include "QlipV6/Breaker.mqh"
#include "QlipV6/Track.mqh"
#include "QlipV6/Manage.mqh"
#include "QlipV6/Execute.mqh"
#include "QlipV6/Poll.mqh"

//--- inputs
input string InpAdapterBase        = "http://127.0.0.1:8765"; // Adapter base URL (allow it in WebRequest)
input bool   InpExecute            = true;    // Execute signed intents (DEMO accounts only)
input long   InpMagic              = 250570;  // V6 magic (250570..250579)
input double InpMaxLots            = 0.03;    // Lot cap per order (hard cap 0.03)
input double InpMaxRiskUsd         = 50.0;    // Max loss at the stop per order, account currency (<= 50)
input double InpDailyBreakerPct    = 3.0;     // Local breaker: equity drop from the server-day start, % (<= 3)
input string InpFlattenServerTime  = "22:55"; // Flatten V6 daily at this server time (HH:MM)
input string InpHmacKeyFile        = "QlipV6\\hmac.key"; // V6 key file under MQL5\Files
input int    InpHttpTimeoutMs      = 1500;    // Snapshot timeout, ms
input int    InpPollTimeoutMs      = 600;     // Poll timeout, ms
input int    InpOutboxTimeoutMs    = 1000;    // Report and basket-result timeout, ms
input int    InpBackfillTimeoutMs  = 3000;    // Backfill timeout per chunk, ms
input int    InpPollIntervalMs     = 2000;    // Poll interval, ms
input int    InpSnapshotRetryS     = 30;      // Keep retrying a failed snapshot for, s
input bool   InpBackfillEnabled    = true;    // Send history on start
input int    InpBackfillDaysM1     = 1;       // Backfill days, M1
input int    InpBackfillDaysM5     = 5;       // Backfill days, M5
input int    InpBackfillDaysM15    = 15;      // Backfill days, M15
input int    InpBackfillDaysH1     = 30;      // Backfill days, H1
input int    InpBackfillDaysD1     = 60;      // Backfill days, D1
input int    InpBackfillChunkRows  = 2500;    // Rows per backfill request (<= 2500)

//--- constants
#define TIMER_PERIOD_MS    1000
#define NO_LOGIN           (-1)
#define PATH_BACKFILL      "/v6/bars/backfill"
#define PATH_SNAPSHOT      "/v6/snapshot"

//--- state
CBackfill g_backfill;
datetime  g_last_m15_open = 0;    // server open time of the forming M15 bar
datetime  g_pending_bar = 0;      // closed M15 bar still waiting to be sent
datetime  g_pending_since = 0;    // UTC time the pending bar was detected
bool      g_probe_sent = false;   // probe block goes out once per EA session
long      g_state_login = NO_LOGIN;

//+------------------------------------------------------------------+
string AdapterUrl(const string path) { return InpAdapterBase + path; }

void ApplyConfig(void)
{
   g_cfg.adapter_base = InpAdapterBase;
   g_cfg.magic = InpMagic;
   g_cfg.execute_input = InpExecute;
   g_cfg.max_lots = InpMaxLots;
   g_cfg.max_risk_usd = InpMaxRiskUsd;
   g_cfg.breaker_pct = InpDailyBreakerPct;
   g_cfg.flatten_text = InpFlattenServerTime;
   g_cfg.key_file = InpHmacKeyFile;
   g_cfg.http_timeout_ms = InpHttpTimeoutMs;
   g_cfg.poll_timeout_ms = InpPollTimeoutMs;
   g_cfg.outbox_timeout_ms = InpOutboxTimeoutMs;
   g_cfg.backfill_timeout_ms = InpBackfillTimeoutMs;
   g_cfg.poll_interval_ms = InpPollIntervalMs;
   g_cfg.snapshot_retry_s = InpSnapshotRetryS;
   g_cfg.selftest_ok = false;
}

bool InputsAreValid(void)
{
   ApplyConfig();
   bool ok = ConfigIsValid(g_cfg);
   return ConfigCheck(InpBackfillChunkRows >= 1 && InpBackfillChunkRows <= BACKFILL_MAX_CHUNK_ROWS,
                      "InpBackfillChunkRows must be within 1..2500") && ok;
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

// Why the EA will or will not place orders; part of the startup line.
string ExecutionModeText(void)
{
   if(!InpExecute)
      return "DATA-ONLY: InpExecute=false, intents are answered with dry_run";
   if(AccountInfoInteger(ACCOUNT_LOGIN) <= 0)
      return "DATA-ONLY until a DEMO account is logged in";
   if(!AccountIsDemo())
      return "DATA-ONLY: the account is " + TradeModeName() + ", V6 executes on DEMO accounts only";
   if(!g_hmac_key_loaded)
      return "DATA-ONLY: " + g_key_problem;
   if(!g_cfg.selftest_ok)
      return "DATA-ONLY: the HMAC self-test failed at " + g_selftest_failed;
   return "EXECUTE";
}

// Processed intents, tracked orders and the breaker belong to one account.
void EnsureAccountState(void)
{
   long login = AccountInfoInteger(ACCOUNT_LOGIN);
   if(login == g_state_login)
      return;
   g_state_login = login;
   SeenLoad();
   TrackLoadAll();
   BreakerLoad();
   PrintFormat("V6 state for login %I64d (%s): %d tracked order(s)/position(s), breaker %s, last intent '%s'",
               login, ExecutionModeText(), g_track_count, g_breaker.tripped ? "TRIPPED" : "clear",
               LastSeenIntentId());
}

void LoadKeyAndSelfTest(void)
{
   g_cfg.selftest_ok = HmacSelfTest();
   if(!g_cfg.selftest_ok)
      PrintFormat("V6: HMAC self-test FAILED at vector '%s'; execution stays disabled", g_selftest_failed);
   if(!LoadHmacKey(InpHmacKeyFile))
   {
      Print("V6: requests are sent unsigned: ", g_key_problem);
      return;
   }
   string fingerprint = KeyFingerprint(g_hmac_key);
   PrintFormat("V6: HMAC key loaded, fingerprint %s", fingerprint);
}

void LogStartup(void)
{
   PrintFormat("V6 EA %s started (%s). symbol=%s magic=%I64d login=%s mode=%s offset=%d s adapter=%s",
               V6_EA_VERSION, ExecutionModeText(), _Symbol, InpMagic, LoginText(), TradeModeName(),
               ServerGmtOffsetSeconds(), InpAdapterBase);
   PrintFormat("V6 limits: lots<=%.2f risk<=%.2f breaker=%.1f%% flatten=%s server time; outbox %d pending; "
               "AutoTrading %s", InpMaxLots, InpMaxRiskUsd, InpDailyBreakerPct, InpFlattenServerTime,
               g_outbox.Pending(), TradingPermitted() ? "on" : "OFF (the EA can neither enter nor exit)");
}

//+------------------------------------------------------------------+
//| Snapshot on every M15 close                                      |
//+------------------------------------------------------------------+
bool TrySendSnapshot(const datetime bar_open_server)
{
   EaStatus status;
   FillEaStatus(status);
   string body = BuildSnapshotJson(InpMagic, bar_open_server, !g_probe_sent, status);
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
   LoadKeyAndSelfTest();
   g_state_login = NO_LOGIN;
   EnsureAccountState();
   g_outbox.Init(InpAdapterBase, InpOutboxTimeoutMs);
   ConfigureBackfill();
   g_last_m15_open = iTime(_Symbol, PERIOD_M15, 0);
   g_pending_bar = 0;
   g_probe_sent = false;

   DomTrackerStart();
   if(!EventSetMillisecondTimer(TIMER_PERIOD_MS))
   {
      PrintFormat("V6: timer could not be started (err=%d)", GetLastError());
      return INIT_FAILED;
   }
   LogStartup();
   g_backfill.Service();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   DomTrackerStop();
   GlobalVariablesFlush();
   WipeHmacKey();
   PrintFormat("V6 EA stopped (reason=%d, outbox %d pending)", reason, g_outbox.Pending());
}

// Management first: it needs no network and must never wait behind a call.
void OnTimer(void)
{
   EnsureAccountState();
   ManageTick();
   DomTrackerSample();
   ServiceSnapshot();
   ServicePoll();
   g_outbox.Service();
   g_backfill.Service();
}

// Only a flag: reports and basket results are built in the timer.
void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   g_trade_event = true;
}

void OnBookEvent(const string &symbol)
{
   if(symbol == _Symbol)
      DomTrackerSample();
}
//+------------------------------------------------------------------+
