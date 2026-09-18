//+------------------------------------------------------------------+
//| QlipV6/Manage.mqh                                                |
//| Order and position management without any network call. It runs |
//| first on every timer tick, so the time barrier, the pre-rollover |
//| flatten and the local breaker keep working while the adapter is  |
//| unreachable (SL/TP sit at the broker anyway).                    |
//|                                                                  |
//| Triggers only mark a record (close_reason / cancel_reason, both  |
//| persisted); ExecuteMarkedExits acts on the marks, with a retry   |
//| throttle, until the broker confirms. The SL+ ladder lives in     |
//| Plan.mqh and agent actions in Actions.mqh; nothing here trails   |
//| or closes part of a position.                                    |
//+------------------------------------------------------------------+
#ifndef QLIPV6_MANAGE_MQH
#define QLIPV6_MANAGE_MQH

#include "Config.mqh"
#include "Persist.mqh"
#include "Orders.mqh"
#include "Track.mqh"
#include "Report.mqh"
#include "Basket.mqh"
#include "Breaker.mqh"
#include "Schedule.mqh"
#include "Sync.mqh"

#define MANAGE_RETRY_MS          5000
#define MANAGE_CLOSED_RETRY_MS   60000
#define MANAGE_SYNC_MS           5000
#define MANAGE_LOG_THROTTLE_S    600   // a close waiting out a weekend logs every 10 min

bool  g_trade_event = false;     // set by OnTradeTransaction; nothing else happens there
ulong g_last_sync_ms = 0;

void ManageLog(const int i, const string text)
{
   if((long)TimeGMT() - (long)g_track[i].last_log < MANAGE_LOG_THROTTLE_S)
      return;
   g_track[i].last_log = TimeGMT();
   PrintFormat("V6 manage %I64u: %s", g_track[i].key, text);
}

//--- exit triggers ----------------------------------------------------

// Nothing V6 stays open inside the flatten window, nor past the first flatten
// time after it started (a restart can skip the window itself).
bool RolloverDue(const datetime started, const datetime now_server)
{
   return InFlattenWindow(now_server) || now_server >= NextFlattenAfter(started);
}

// Breaker first, then the daily flatten, then the time barrier.
void MarkPositionExit(const int i, const datetime now_server)
{
   if(!SelectPositionById(g_track[i].key))
      return;
   TrackExcursion(i, PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP));
   if(g_track[i].close_reason != CLOSE_BY_NONE)
      return;
   datetime opened = (datetime)PositionGetInteger(POSITION_TIME);
   int reason = CLOSE_BY_NONE;
   if(g_breaker.tripped)
      reason = CLOSE_BY_FLATTEN;
   else if(RolloverDue(opened, now_server))
      reason = CLOSE_BY_ROLLOVER;
   else if((long)now_server - (long)opened >= BarrierSeconds(g_track[i].barrier_s))
      reason = CLOSE_BY_TIME;
   if(reason != CLOSE_BY_NONE)
      TrackMarkClose(i, reason);
}

void MarkPendingExit(const int i, const bool halted, const datetime now_server)
{
   if(g_track[i].cancel_reason != CANCEL_BY_NONE || !OrderSelect(g_track[i].key))
      return;
   datetime placed = (datetime)OrderGetInteger(ORDER_TIME_SETUP);
   int reason = CANCEL_BY_NONE;
   if(g_breaker.tripped)
      reason = CANCEL_BY_BREAKER;
   else if(halted)
      reason = CANCEL_BY_HALT;
   else if(RolloverDue(placed, now_server))
      reason = CANCEL_BY_ROLLOVER;
   if(reason != CANCEL_BY_NONE)
      TrackMarkCancel(i, reason);
}

void MarkScheduledExits(void)
{
   bool halted = PersistHaltRequested();
   datetime now_server = TimeTradeServer();
   for(int i = 0; i < g_track_count; i++)
   {
      if(g_track[i].state == TRACK_STATE_OPEN)
         MarkPositionExit(i, now_server);
      else
         MarkPendingExit(i, halted, now_server);
   }
}

//--- acting on marks --------------------------------------------------

bool TryClosePosition(const int i, uint &retcode)
{
   retcode = 0;
   if(!SelectPositionById(g_track[i].key))
      return true;   // already gone; the next sync reports it
   ulong ticket = (ulong)PositionGetInteger(POSITION_TICKET);
   long spread = CurrentSpreadPoints();
   double requested = 0.0;
   if(!ClosePositionByTicket(ticket, TrackComment(g_track[i]), requested, retcode))
      return false;
   g_track[i].exit_requested = requested;
   g_track[i].exit_spread = (double)spread;
   TrackSave(g_track[i]);
   PrintFormat("V6 manage: position %I64u closed (%s)", ticket,
               CloseReasonName(g_track[i].close_reason, NO_DEAL_REASON));
   return true;
}

bool TryDeleteOrder(const int i, uint &retcode)
{
   retcode = 0;
   if(!OrderSelect(g_track[i].key))
      return true;
   if(!DeletePendingOrder(g_track[i].key, retcode))
      return false;
   PrintFormat("V6 manage: pending order %I64u deleted (%s)", g_track[i].key,
               CancelReasonCode(g_track[i].cancel_reason));
   return true;
}

void TryExit(const int i, const bool close, const ulong now_ms)
{
   g_track[i].next_try_ms = now_ms + MANAGE_RETRY_MS;
   if(!TradingPermitted())
   {
      ManageLog(i, "AutoTrading is off; the requested exit waits");
      return;
   }
   uint retcode = 0;
   bool done = close ? TryClosePosition(i, retcode) : TryDeleteOrder(i, retcode);
   if(done)
   {
      g_trade_event = true;
      return;
   }
   if(RetcodeIs(retcode, TRADE_RETCODE_MARKET_CLOSED))
      g_track[i].next_try_ms = now_ms + MANAGE_CLOSED_RETRY_MS;
   ManageLog(i, StringFormat("%s refused (retcode %u); retrying", close ? "close" : "delete", retcode));
}

void ExecuteMarkedExits(void)
{
   ulong now_ms = GetTickCount64();
   for(int i = 0; i < g_track_count; i++)
   {
      if(now_ms < g_track[i].next_try_ms)
         continue;
      bool close = g_track[i].state == TRACK_STATE_OPEN && g_track[i].close_reason != CLOSE_BY_NONE;
      bool cancel = g_track[i].state == TRACK_STATE_PENDING && g_track[i].cancel_reason != CANCEL_BY_NONE;
      if(close || cancel)
         TryExit(i, close, now_ms);
   }
}

//--- entry points -------------------------------------------------------

// FLATTEN and CANCEL_PENDING from a signed poll reply: FLATTEN also closes
// every V6 position, CANCEL_PENDING leaves positions to SL/TP and the barrier.
void ManageApplyCommand(const string command)
{
   if(command != CMD_FLATTEN && command != CMD_CANCEL_PENDING)
      return;
   AdoptUntracked();
   int marked = 0;
   for(int i = 0; i < g_track_count; i++)
   {
      bool cancel = g_track[i].state == TRACK_STATE_PENDING && g_track[i].cancel_reason == CANCEL_BY_NONE;
      bool close = command == CMD_FLATTEN && g_track[i].state == TRACK_STATE_OPEN
                   && g_track[i].close_reason == CLOSE_BY_NONE;
      if(cancel)
         TrackMarkCancel(i, CANCEL_BY_COMMAND);
      if(close)
         TrackMarkClose(i, CLOSE_BY_FLATTEN);
      marked += (cancel || close) ? 1 : 0;
   }
   if(marked == 0)
      return;
   PrintFormat("V6 manage: %s marked %d order(s)/position(s)", command, marked);
   ExecuteMarkedExits();
}

void ManageTick(void)
{
   BreakerUpdate(g_cfg.breaker_pct);
   AdoptUntracked();
   ulong now_ms = GetTickCount64();
   if(g_trade_event || now_ms - g_last_sync_ms >= MANAGE_SYNC_MS)
   {
      g_trade_event = false;
      g_last_sync_ms = now_ms;
      SyncRecords();
   }
   MarkScheduledExits();
   ExecuteMarkedExits();
}

#endif // QLIPV6_MANAGE_MQH
