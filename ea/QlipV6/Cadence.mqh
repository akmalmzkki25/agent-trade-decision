//+------------------------------------------------------------------+
//| QlipV6/Cadence.mqh                                               |
//| When snapshots go out:                                           |
//| - one v6.snapshot.1 per closed M15 bar, retried for              |
//|   InpSnapshotRetryS seconds                                      |
//| - one v6.minute.1 per closed M1 bar, sent once and never         |
//|   retried: the next minute replaces it                           |
//| The M15 snapshot goes first, so at an M15 close the minute       |
//| snapshot never delays it.                                        |
//| Included by the main file after its inputs and AdapterUrl().     |
//+------------------------------------------------------------------+
#ifndef QLIPV6_CADENCE_MQH
#define QLIPV6_CADENCE_MQH

#include "Http.mqh"
#include "Market.mqh"
#include "Snapshot.mqh"
#include "Poll.mqh"

#define PATH_SNAPSHOT       "/v6/snapshot"
#define PATH_MINUTE         "/v6/minute"
#define MINUTE_LOG_EVERY_S  900      // at most one minute-failure line per 15 minutes

datetime g_last_m15_open = 0;        // server open time of the forming M15 bar
datetime g_pending_bar = 0;          // closed M15 bar still waiting to be sent
datetime g_pending_since = 0;        // UTC time the pending bar was detected
bool     g_probe_sent = false;       // probe block goes out once per EA session
datetime g_last_m1_open = 0;         // server open time of the forming M1 bar
datetime g_minute_log_at = 0;        // UTC time of the last minute-failure line

//--- M15 snapshot --------------------------------------------------

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

//--- M1 minute snapshot --------------------------------------------

// The M1 bar that just closed, or 0: first sighting, no new bar yet, or a
// previous bar that closed more than a minute ago (quiet market, gap).
datetime DetectClosedMinute(void)
{
   datetime forming = iTime(_Symbol, PERIOD_M1, 0);
   if(forming == 0 || forming == g_last_m1_open)
      return 0;
   bool first_sighting = (g_last_m1_open == 0);
   g_last_m1_open = forming;
   if(first_sighting)
      return 0;
   datetime closed = iTime(_Symbol, PERIOD_M1, 1);
   if(closed == 0 || (long)TimeTradeServer() - ((long)closed + M1_SECONDS) > M1_SECONDS)
      return 0;
   return closed;
}

// A dead adapter would otherwise log one line a minute.
void LogMinuteFailure(const HttpResult &result)
{
   datetime now = TimeGMT();
   if((long)now - (long)g_minute_log_at < MINUTE_LOG_EVERY_S)
      return;
   g_minute_log_at = now;
   Print(HttpDescribeFailure("minute", AdapterUrl(PATH_MINUTE), result));
}

void ServiceMinute(void)
{
   if(!InpMinuteSnapshots)
      return;
   datetime closed = DetectClosedMinute();
   if(closed == 0)
      return;
   EaStatus status;
   FillEaStatus(status);
   string body = BuildMinuteJson(InpMagic, closed, status);
   if(body == "")
      return;
   HttpResult result;
   if(!HttpPostJson(AdapterUrl(PATH_MINUTE), body, InpMinuteTimeoutMs, 1, result))
      LogMinuteFailure(result);
}

#endif // QLIPV6_CADENCE_MQH
