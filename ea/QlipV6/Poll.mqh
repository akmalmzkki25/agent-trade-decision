//+------------------------------------------------------------------+
//| QlipV6/Poll.mqh                                                  |
//| The heartbeat to /v6/intent/poll and what the EA does with the   |
//| reply: nothing at all unless its signature verifies (contract    |
//| §8.1 check 1). A signed reply may carry a command (FLATTEN,      |
//| CANCEL_PENDING) or one intent, never both.                       |
//+------------------------------------------------------------------+
#ifndef QLIPV6_POLL_MQH
#define QLIPV6_POLL_MQH

#include "Config.mqh"
#include "Json.mqh"
#include "Hmac.mqh"
#include "Http.mqh"
#include "Persist.mqh"
#include "Intent.mqh"
#include "Snapshot.mqh"
#include "Outbox.mqh"
#include "Report.mqh"
#include "Orders.mqh"
#include "Breaker.mqh"
#include "Manage.mqh"
#include "Execute.mqh"

#define PATH_POLL              "/v6/intent/poll"
#define POLL_LOG_THROTTLE_S    60
#define COMMAND_WINDOW_S       30
#define BAD_SIG_MEMORY         16

ulong    g_last_poll_ms = 0;
datetime g_last_poll_problem = 0;
string   g_poll_state = "";
string   g_bad_sig_ids[BAD_SIG_MEMORY];
int      g_bad_sig_next = 0;

void FillEaStatus(EaStatus &status)
{
   status.execute_enabled = ExecutionReady();
   status.halted = LocalHaltActive();
   status.breaker_tripped = g_breaker.tripped;
   status.outbox_pending = g_outbox.Pending();
   status.last_intent_id = LastSeenIntentId();
}

void LogPollProblem(const string message)
{
   if((long)TimeGMT() - (long)g_last_poll_problem < POLL_LOG_THROTTLE_S)
      return;
   g_last_poll_problem = TimeGMT();
   Print(message);
}

// Contract §8.1 check 1: key loaded, self-test passed, signature verified.
bool ReplyTrusted(const PollReply &reply)
{
   if(!g_hmac_key_loaded || !g_cfg.selftest_ok)
      return false;
   return PollReplySignatureOk(reply, g_hmac_key, SymbolInfoDouble(_Symbol, SYMBOL_POINT));
}

string SignatureState(const PollReply &reply, const bool trusted)
{
   if(trusted)
      return "verified";
   if(!g_hmac_key_loaded)
      return "not checked (no key)";
   if(!g_cfg.selftest_ok)
      return "not checked (self-test failed)";
   return (reply.sig == "") ? "missing" : "INVALID";
}

// One log line whenever the kind of reply changes, not on every poll.
void NotePollState(const PollReply &reply, const bool trusted)
{
   string state = StringFormat("command=%s has_intent=%s signature=%s", reply.command,
                               reply.has_intent ? "true" : "false", SignatureState(reply, trusted));
   if(state == g_poll_state)
      return;
   g_poll_state = state;
   Print("V6 poll: ", state, trusted ? "" : "; the reply is ignored");
}

bool BadSignatureSeen(const string id)
{
   for(int i = 0; i < BAD_SIG_MEMORY; i++)
   {
      if(g_bad_sig_ids[i] == id)
         return true;
   }
   return false;
}

// Reported once per id; unverified ids never enter the persisted set.
void ReportUntrustedIntent(const PollReply &reply, const ulong received_ms)
{
   if(!reply.has_intent || !IsIntentId(reply.intent_id) || IntentSeen(reply.intent_id)
      || BadSignatureSeen(reply.intent_id))
      return;
   g_bad_sig_ids[g_bad_sig_next] = reply.intent_id;
   g_bad_sig_next = (g_bad_sig_next + 1) % BAD_SIG_MEMORY;
   ExecReport r;
   ReportStart(r, reply.intent_id, STATUS_REJECTED_LOCAL, REASON_BAD_SIGNATURE);
   r.latency_ms = ElapsedMs(received_ms);
   QueueExecutionReport(r);
}

// A signed command is only acted on while the reply is recent.
bool CommandFresh(const PollReply &reply)
{
   if(reply.command == CMD_NONE)
      return false;
   long skew = (long)TimeGMT() - reply.server_time_epoch;
   if(skew <= COMMAND_WINDOW_S && skew >= -COMMAND_WINDOW_S)
      return true;
   LogPollProblem(StringFormat("V6 poll: %s ignored, the reply is %I64d s away from this clock",
                               reply.command, skew));
   return false;
}

void HandlePollResponse(const string json, const ulong received_ms)
{
   PollReply reply;
   if(!ParsePollReply(json, reply) || !IsKnownCommand(reply.command))
   {
      LogPollProblem("V6 poll: the reply is not a flat v6.intent.1 object; ignored");
      return;
   }
   bool trusted = ReplyTrusted(reply);
   NotePollState(reply, trusted);
   if(!trusted)
   {
      ReportUntrustedIntent(reply, received_ms);
      return;
   }
   if(CommandFresh(reply))
      ManageApplyCommand(reply.command);
   if(reply.has_intent)
      ExecuteIntent(reply, received_ms);
}

void ServicePoll(void)
{
   ulong now_ms = GetTickCount64();
   if(g_last_poll_ms != 0 && now_ms - g_last_poll_ms < (ulong)g_cfg.poll_interval_ms)
      return;
   g_last_poll_ms = now_ms;
   EaStatus status;
   FillEaStatus(status);
   string body = BuildPollJson(g_cfg.magic, status);
   if(body == "")
      return;
   string url = g_cfg.adapter_base + PATH_POLL;
   HttpResult result;
   if(!HttpPostJson(url, body, g_cfg.poll_timeout_ms, 1, result))
   {
      LogPollProblem(HttpDescribeFailure("poll", url, result));
      return;
   }
   HandlePollResponse(result.body, GetTickCount64());
}

#endif // QLIPV6_POLL_MQH
