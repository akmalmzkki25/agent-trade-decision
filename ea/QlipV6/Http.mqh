//+------------------------------------------------------------------+
//| QlipV6/Http.mqh                                                  |
//| JSON POST to the local adapter with a bounded retry.             |
//|                                                                  |
//| WebRequest blocks the EA thread, so every call carries its own   |
//| timeout and only transport failures or 5xx are retried: a 4xx    |
//| means the payload itself was refused and resending cannot help.  |
//+------------------------------------------------------------------+
#ifndef QLIPV6_HTTP_MQH
#define QLIPV6_HTTP_MQH

#include "Json.mqh"

#define HTTP_TRANSPORT_FAILED  (-1)
#define HTTP_SUCCESS_MIN       200
#define HTTP_SUCCESS_MAX       299
#define HTTP_SERVER_ERROR_MIN  500
#define HTTP_RETRY_PAUSE_MS    150
#define HTTP_MAX_ATTEMPTS      3
#define HTTP_BODY_LOG_CHARS    160
#define HTTP_JSON_HEADERS      "Content-Type: application/json\r\nAccept: application/json\r\n"

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

// The body is converted without its terminating NUL: WebRequest would send
// that byte as part of the payload and the adapter would reject the JSON.
void HttpPostOnce(const string url, const string body, const int timeout_ms, HttpResult &result)
{
   char payload[];
   char response[];
   string response_headers = "";
   int converted = StringToCharArray(body, payload, 0, WHOLE_ARRAY, CP_UTF8);
   if(converted > 0 && payload[converted - 1] == 0)
      ArrayResize(payload, converted - 1);

   ResetLastError();
   result.status = WebRequest("POST", url, HTTP_JSON_HEADERS, timeout_ms,
                              payload, response, response_headers);
   result.error = (result.status == HTTP_TRANSPORT_FAILED) ? GetLastError() : 0;
   result.body = (ArraySize(response) > 0)
                 ? CharArrayToString(response, 0, WHOLE_ARRAY, CP_UTF8) : "";
}

// Posts `body`, retrying transport failures and 5xx up to `attempts` times.
bool HttpPostJson(const string url, const string body, const int timeout_ms,
                  const int attempts, HttpResult &result)
{
   int limit = MathMax(1, MathMin(attempts, HTTP_MAX_ATTEMPTS));
   result.status = HTTP_TRANSPORT_FAILED;
   result.error = 0;
   result.attempts = 0;
   result.body = "";
   for(int i = 0; i < limit; i++)
   {
      if(i > 0)
         Sleep(HTTP_RETRY_PAUSE_MS);
      result.attempts = i + 1;
      HttpPostOnce(url, body, timeout_ms, result);
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
