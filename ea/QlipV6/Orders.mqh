//+------------------------------------------------------------------+
//| QlipV6/Orders.mqh                                                |
//| The only module that sends trade requests: new entries, full     |
//| closes and pending deletions, plus the guards every entry passes |
//| (demo account, trading permitted, execution ready). There is no  |
//| modify path at all: no break-even, no trailing, no partial close.|
//+------------------------------------------------------------------+
#ifndef QLIPV6_ORDERS_MQH
#define QLIPV6_ORDERS_MQH

#include "Config.mqh"
#include "Hmac.mqh"
#include "Persist.mqh"

#define FILLING_CANDIDATES      3
#define CLOSE_DEVIATION_POINTS  100

// Compiled rule, no input can change it: V6 trades DEMO accounts only.
// ACCOUNT_TRADE_MODE_DEMO is 0, the value of account data that is not loaded
// yet, so an unknown login never counts as demo.
bool AccountIsDemo(void)
{
   if(AccountInfoInteger(ACCOUNT_LOGIN) <= 0)
      return false;
   return (ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_DEMO;
}

// AutoTrading in the terminal and for this EA, and the account's trade rights.
bool TradingPermitted(void)
{
   return TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) != 0
          && MQLInfoInteger(MQL_TRADE_ALLOWED) != 0
          && AccountInfoInteger(ACCOUNT_TRADE_ALLOWED) != 0
          && AccountInfoInteger(ACCOUNT_TRADE_EXPERT) != 0;
}

// Contract §8.1 check 8: the QlipV6_HALT global variable, or AutoTrading off.
bool LocalHaltActive(void)
{
   return PersistHaltRequested() || !TradingPermitted();
}

// Would a signed intent be acted on (halts and breakers aside)?
bool ExecutionReady(void)
{
   return g_cfg.execute_input && g_hmac_key_loaded && g_cfg.selftest_ok && AccountIsDemo();
}

bool RetcodeIs(const uint retcode, const int code)
{
   return (int)retcode == code;
}

bool RetcodeSucceeded(const uint retcode)
{
   return RetcodeIs(retcode, TRADE_RETCODE_DONE) || RetcodeIs(retcode, TRADE_RETCODE_PLACED)
          || RetcodeIs(retcode, TRADE_RETCODE_DONE_PARTIAL);
}

// Filling modes to try: market orders IOC then FOK (SYMBOL_FILLING_MODE),
// pending orders RETURN first. OrderCheck decides which one the server takes.
int FillingCandidates(const bool market, ENUM_ORDER_TYPE_FILLING &modes[])
{
   long flags = SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   int n = 0;
   if(!market)
      modes[n++] = ORDER_FILLING_RETURN;
   if((flags & SYMBOL_FILLING_IOC) != 0)
      modes[n++] = ORDER_FILLING_IOC;
   if((flags & SYMBOL_FILLING_FOK) != 0)
      modes[n++] = ORDER_FILLING_FOK;
   if(n == 0)
      modes[n++] = ORDER_FILLING_RETURN;
   return n;
}

// OrderCheck with the first filling mode the server does not refuse as
// invalid. Returns the verdict for the last mode tried.
bool CheckWithFilling(MqlTradeRequest &req, MqlTradeCheckResult &check, const bool market)
{
   ENUM_ORDER_TYPE_FILLING modes[FILLING_CANDIDATES];
   int count = FillingCandidates(market, modes);
   bool ok = false;
   for(int i = 0; i < count; i++)
   {
      req.type_filling = modes[i];
      ZeroMemory(check);
      ResetLastError();
      ok = OrderCheck(req, check);
      if(ok || !RetcodeIs(check.retcode, TRADE_RETCODE_INVALID_FILL))
         return ok;
   }
   return ok;
}

// True only when the server accepted the request.
bool SendRequest(MqlTradeRequest &req, MqlTradeResult &res)
{
   ZeroMemory(res);
   ResetLastError();
   bool sent = OrderSend(req, res);
   return sent && RetcodeSucceeded(res.retcode);
}

// Entries go out only here, and only on a connected demo account.
bool SendEntryRequest(MqlTradeRequest &req, MqlTradeResult &res)
{
   ZeroMemory(res);
   if(!AccountIsDemo() || TerminalInfoInteger(TERMINAL_CONNECTED) == 0)
      return false;
   return SendRequest(req, res);
}

// Closes the whole position (never a part of it); SL/TP stay untouched.
bool ClosePositionByTicket(const ulong ticket, const string comment, double &requested, uint &retcode)
{
   retcode = 0;
   requested = 0.0;
   MqlTick tick;
   ZeroMemory(tick);
   if(!PositionSelectByTicket(ticket) || !SymbolInfoTick(_Symbol, tick))
      return false;
   bool is_buy = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
   MqlTradeRequest req;
   MqlTradeCheckResult check;
   MqlTradeResult res;
   ZeroMemory(req);
   req.action = TRADE_ACTION_DEAL;
   req.position = ticket;
   req.symbol = _Symbol;
   req.volume = PositionGetDouble(POSITION_VOLUME);
   req.type = is_buy ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
   req.price = is_buy ? tick.bid : tick.ask;
   req.deviation = (ulong)CLOSE_DEVIATION_POINTS;
   req.magic = (ulong)g_cfg.magic;
   req.comment = comment;
   // The check only picks the filling mode: a close is sent whatever it says,
   // the server has the final word and the caller logs a refusal (throttled).
   CheckWithFilling(req, check, true);
   requested = req.price;
   bool ok = SendRequest(req, res);
   retcode = res.retcode;
   return ok;
}

bool DeletePendingOrder(const ulong ticket, uint &retcode)
{
   MqlTradeRequest req;
   MqlTradeResult res;
   ZeroMemory(req);
   req.action = TRADE_ACTION_REMOVE;
   req.order = ticket;
   bool ok = SendRequest(req, res);
   retcode = res.retcode;
   return ok;
}

#endif // QLIPV6_ORDERS_MQH
