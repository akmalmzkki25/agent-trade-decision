//+------------------------------------------------------------------+
//| QlipV6/Backfill.mqh                                              |
//| Closed-bar history for the adapter's bar store (v6.backfill.1).  |
//|                                                                  |
//| Runs once per EA start. History for a timeframe may still be     |
//| downloading when the EA loads, so each timeframe is retried a    |
//| few times before it is given up on.                              |
//+------------------------------------------------------------------+
#ifndef QLIPV6_BACKFILL_MQH
#define QLIPV6_BACKFILL_MQH

#include "Json.mqh"
#include "Http.mqh"
#include "Market.mqh"
#include "Persist.mqh"

#define BACKFILL_TF_COUNT        5
#define BACKFILL_MAX_CHUNK_ROWS  2500   // adapter accepts 3000; stays well under 200 KB
#define BACKFILL_MAX_ATTEMPTS    5
#define BACKFILL_RETRY_S         10
#define BACKFILL_HTTP_ATTEMPTS   2

string BuildBackfillJson(const ENUM_TIMEFRAMES tf, const MqlRates &rates[], const int from,
                         const int to, const int offset_s, int &written)
{
   CJsonObject o;
   o.AddStr("schema_version", "v6.backfill.1");
   o.AddStr("symbol", _Symbol);
   o.AddStr("tf", TimeframeName(tf));
   o.AddInt("sent_at_epoch", (long)TimeGMT());
   o.AddRaw("rows", BarRowsJson(rates, from, to, offset_s, _Digits, written));
   return o.Text();
}

class CBackfill
{
private:
   ENUM_TIMEFRAMES   m_tfs[BACKFILL_TF_COUNT];
   int               m_days[BACKFILL_TF_COUNT];
   bool              m_done[BACKFILL_TF_COUNT];
   int               m_attempts[BACKFILL_TF_COUNT];
   datetime          m_next_try;
   bool              m_finished;
   string            m_url;
   int               m_timeout_ms;
   int               m_chunk_rows;

   bool              Send(const ENUM_TIMEFRAMES tf, const int days);

public:
                     CBackfill(void) : m_next_try(0), m_finished(true), m_url(""),
                                       m_timeout_ms(0), m_chunk_rows(BACKFILL_MAX_CHUNK_ROWS) {}
   void              Configure(const string url, const int timeout_ms, const int chunk_rows,
                               const bool enabled, const int &days[]);
   void              Service(void);
};

// `days` holds M1, M5, M15, H1 and D1 in that order; 0 skips a timeframe.
void CBackfill::Configure(const string url, const int timeout_ms, const int chunk_rows,
                          const bool enabled, const int &days[])
{
   m_tfs[0] = PERIOD_M1;
   m_tfs[1] = PERIOD_M5;
   m_tfs[2] = PERIOD_M15;
   m_tfs[3] = PERIOD_H1;
   m_tfs[4] = PERIOD_D1;
   for(int i = 0; i < BACKFILL_TF_COUNT; i++)
   {
      m_days[i] = (i < ArraySize(days)) ? days[i] : 0;
      m_done[i] = false;
      m_attempts[i] = 0;
   }
   m_url = url;
   m_timeout_ms = timeout_ms;
   m_chunk_rows = MathMax(1, MathMin(chunk_rows, BACKFILL_MAX_CHUNK_ROWS));
   m_next_try = 0;
   m_finished = !enabled;
}

// Closed bars only: copying starts at shift 1, never the forming bar.
bool CBackfill::Send(const ENUM_TIMEFRAMES tf, const int days)
{
   int count = days * SECONDS_PER_DAY / PeriodSeconds(tf);
   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   ResetLastError();
   int copied = CopyRates(_Symbol, tf, 1, count, rates);
   if(copied <= 0)
   {
      PrintFormat("V6 backfill %s: history not ready (err=%d)", TimeframeName(tf), GetLastError());
      return false;
   }
   int offset = ServerGmtOffsetSeconds();
   int sent = 0;
   for(int from = 0; from < copied; from += m_chunk_rows)
   {
      int written = 0;
      string body = BuildBackfillJson(tf, rates, from, MathMin(from + m_chunk_rows, copied), offset, written);
      HttpResult result;
      if(!HttpPostJson(m_url, body, m_timeout_ms, BACKFILL_HTTP_ATTEMPTS, result))
      {
         Print(HttpDescribeFailure("backfill " + TimeframeName(tf), m_url, result));
         return false;
      }
      sent += written;
   }
   PrintFormat("V6 backfill %s: %d closed bars sent (%d copied, offset %d s)",
               TimeframeName(tf), sent, copied, offset);
   return true;
}

void CBackfill::Service(void)
{
   if(m_finished || TimeGMT() < m_next_try)
      return;
   bool retry_needed = false;
   for(int i = 0; i < BACKFILL_TF_COUNT; i++)
   {
      if(m_done[i] || m_days[i] <= 0 || m_attempts[i] >= BACKFILL_MAX_ATTEMPTS)
         continue;
      m_attempts[i]++;
      m_done[i] = Send(m_tfs[i], m_days[i]);
      if(!m_done[i] && m_attempts[i] >= BACKFILL_MAX_ATTEMPTS)
         PrintFormat("V6 backfill %s: giving up after %d attempts", TimeframeName(m_tfs[i]), m_attempts[i]);
      retry_needed = retry_needed || (!m_done[i] && m_attempts[i] < BACKFILL_MAX_ATTEMPTS);
   }
   m_finished = !retry_needed;
   m_next_try = TimeGMT() + BACKFILL_RETRY_S;
}

#endif // QLIPV6_BACKFILL_MQH
