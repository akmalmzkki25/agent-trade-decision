//+------------------------------------------------------------------+
//| QlipV6/Http.mqh                                                  |
//| JSON POST to the local adapter with a bounded retry.             |
//|                                                                  |
//| WebRequest blocks the EA thread, so every call carries its own   |
//| timeout and only transport failures or 5xx are retried: a 4xx    |
//| means the payload itself was refused and resending cannot help.  |
//| With a key loaded every attempt is signed (X-Qlip6-Ts and        |
//| X-Qlip6-Sig over the exact body bytes, contract section 3) and a |
//| retry waits for a new second, since an identical (ts, sig) pair  |
//| inside the window is refused as a replay.                        |
//+------------------------------------------------------------------+
#ifndef QLIPV6_HTTP_MQH
#define QLIPV6_HTTP_MQH

#include "Json.mqh"
#include "Hmac.mqh"

#define HTTP_TRANSPORT_FAILED       (-1)
#define HTTP_SUCCESS_MIN            200
#define HTTP_SUCCESS_MAX            299
#define HTTP_SERVER_ERROR_MIN       500
#define HTTP_RETRY_PAUSE_MS         150
#define HTTP_SIGNED_RETRY_PAUSE_MS  1000
#define HTTP_CLOCK_STEP_MS          50
#define HTTP_CLOCK_WAIT_MS          1000
#define HTTP_MAX_ATTEMPTS           3
#define HTTP_BODY_LOG_CHARS         160
#define HTTP_SCHEME_MARK            "://"
#define HTTP_SCHEME_MARK_CHARS      3
#define HTTP_METHOD                 "POST"
#define HTTP_TS_HEADER              "X-Qlip6-Ts"
#define HTTP_SIG_HEADER             "X-Qlip6-Sig"
#define HTTP_JSON_HEADERS           "Content-Type: application/json\r\nAccept: application/json\r\n"

struct HttpResult
{
   int               status;     // HTTP status, or HTTP_TRANSPORT_FAILED
   int               error;      // GetLastError() when WebRequest itself failed
   int               attempts;
   string            body;
};

bool HttpIsSuccess(const int status)
{
   return status >= HTTP_SUCCESS_MIN && status <= HTTP_SUCCESS_MAX;
}

bool HttpIsRetryable(const int status)
{
   return status == HTTP_TRANSPORT_FAILED || status >= HTTP_SERVER_ERROR_MIN;
}

// The request path as sent: no scheme, host or query.
string HttpUrlPath(const string url)
{
   int scheme = StringFind(url, HTTP_SCHEME_MARK);
   int host_start = (scheme < 0) ? 0 : scheme + HTTP_SCHEME_MARK_CHARS;
   int slash = StringFind(url, "/", host_start);
   if(slash < 0)
      return "/";
   string path = StringSubstr(url, slash);
   int query = StringFind(path, "?");
   return (query < 0) ? path : StringSubstr(path, 0, query);
}

// Headers for one attempt: signed whenever a key is loaded, in any mode.
string HttpHeaders(const string url, const uchar &body[], const long ts)
{
   if(!g_hmac_key_loaded)
      return HTTP_JSON_HEADERS;
   uchar payload[];
   SigningPayload(ts, HTTP_METHOD, HttpUrlPath(url), body, payload);
   string signature = SignBytes(g_hmac_key, payload);
   if(signature == "")
      return HTTP_JSON_HEADERS;
   return HTTP_JSON_HEADERS + HTTP_TS_HEADER + ": " + IntegerToString(ts) + "\r\n"
          + HTTP_SIG_HEADER + ": " + signature + "\r\n";
}

// The body goes out as exactly the bytes that were signed (no trailing NUL,
// which the adapter would reject as part of the JSON).
void HttpPostOnce(const string url, const uchar &body[], const int timeout_ms,
                  const long ts, HttpResult &result)
{
   int n = ArraySize(body);
   char payload[];
   ArrayResize(payload, n);
   for(int i = 0; i < n; i++)
      payload[i] = (char)body[i];
   char response[];
   string response_headers = "";
   string headers = HttpHeaders(url, body, ts);
   ResetLastError();
   result.status = WebRequest(HTTP_METHOD, url, headers, timeout_ms,
                              payload, response, response_headers);
   result.error = (result.status == HTTP_TRANSPORT_FAILED) ? GetLastError() : 0;
   result.body = (ArraySize(response) > 0)
                 ? CharArrayToString(response, 0, WHOLE_ARRAY, CP_UTF8) : "";
}

// A signed retry must not reuse the previous X-Qlip6-Ts.
void HttpRetryPause(const long last_ts)
{
   if(!g_hmac_key_loaded)
   {
      Sleep(HTTP_RETRY_PAUSE_MS);
      return;
   }
   Sleep(HTTP_SIGNED_RETRY_PAUSE_MS);
   int waited = 0;
   while((long)TimeGMT() <= last_ts && waited < HTTP_CLOCK_WAIT_MS)
   {
      Sleep(HTTP_CLOCK_STEP_MS);
      waited += HTTP_CLOCK_STEP_MS;
   }
}

// Posts `body`, retrying transport failures and 5xx up to `attempts` times.
bool HttpPostJson(const string url, const string body, const int timeout_ms,
                  const int attempts, HttpResult &result)
{
   uchar bytes[];
   TextBytes(body, bytes);
   int limit = MathMax(1, MathMin(attempts, HTTP_MAX_ATTEMPTS));
   result.status = HTTP_TRANSPORT_FAILED;
   result.error = 0;
   result.attempts = 0;
   result.body = "";
   long last_ts = 0;
   for(int i = 0; i < limit; i++)
   {
      if(i > 0)
         HttpRetryPause(last_ts);
      last_ts = (long)TimeGMT();
      result.attempts = i + 1;
      HttpPostOnce(url, bytes, timeout_ms, last_ts, result);
      if(HttpIsSuccess(result.status))
         return true;
      if(!HttpIsRetryable(result.status))
         return false;
   }
   return false;
}

// One log line for a failed call; the response body is truncated and
// sanitised because it comes from outside the terminal.
string HttpDescribeFailure(const string what, const string url, const HttpResult &result)
{
   if(result.status != HTTP_TRANSPORT_FAILED)
   {
      string snippet = SanitizeAscii(result.body, HTTP_BODY_LOG_CHARS);
      return StringFormat("V6 %s refused: HTTP %d after %d attempt(s): %s",
                          what, result.status, result.attempts, snippet);
   }
   if(result.error == ERR_FUNCTION_NOT_ALLOWED)
      return StringFormat("V6 %s: WebRequest is not allowed for %s. Add the adapter base URL in "
                          "Tools > Options > Expert Advisors > Allow WebRequest.", what, url);
   return StringFormat("V6 %s failed: WebRequest error %d after %d attempt(s) (%s)",
                       what, result.error, result.attempts, url);
}

#endif // QLIPV6_HTTP_MQH
