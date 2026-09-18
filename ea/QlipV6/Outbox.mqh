//+------------------------------------------------------------------+
//| QlipV6/Outbox.mqh                                                |
//| Durable queue for execution reports, action reports and basket   |
//| results.                                                         |
//|                                                                  |
//| MQL5\Files\QlipV6\outbox.jsonl holds one {"path","body"} object  |
//| per line and is only appended to; a byte offset in a global      |
//| variable marks the oldest unsent line, and the file is emptied   |
//| once every line is sent. One line goes out per timer tick: a 2xx |
//| consumes it; a transport failure, a 5xx or a refusal the adapter |
//| may lift later (401 key/clock, 404 V6 inactive, 408, 409, 429)   |
//| keeps it and backs off; any other 4xx (the payload itself was    |
//| refused, e.g. 400/413/415/422) is logged and dropped. The file   |
//| never grows past OUTBOX_MAX_BYTES; when unsent lines fill it the |
//| new entry is refused and logged.                                 |
//+------------------------------------------------------------------+
#ifndef QLIPV6_OUTBOX_MQH
#define QLIPV6_OUTBOX_MQH

#include "Json.mqh"
#include "Hmac.mqh"
#include "Http.mqh"
#include "Persist.mqh"

#define PATH_EXECUTION          "/v6/execution"
#define PATH_ACTION             "/v6/action"
#define PATH_BASKET_RESULT      "/v6/basket-result"
#define OUTBOX_DIR              "QlipV6"
#define OUTBOX_FILE             "QlipV6\\outbox.jsonl"
#define OUTBOX_TEMP_FILE        "QlipV6\\outbox.tmp"
#define OUTBOX_HEAD_NAME        "QlipV6_OUTBOX_HEAD"
#define OUTBOX_MAX_BYTES        262144
#define OUTBOX_MAX_LINE_BYTES   16384
#define OUTBOX_NEWLINE          10
#define OUTBOX_BACKOFF_FIRST_S  2
#define OUTBOX_BACKOFF_MAX_S    60
#define OUTBOX_READ_OK          0
#define OUTBOX_READ_EMPTY       1
#define OUTBOX_READ_BAD         2
#define OUTBOX_SHARE            (FILE_SHARE_READ | FILE_SHARE_WRITE)
#define HTTP_UNAUTHORIZED       401
#define HTTP_NOT_FOUND          404
#define HTTP_REQUEST_TIMEOUT    408
#define HTTP_CONFLICT           409
#define HTTP_TOO_MANY_REQUESTS  429

// Whether an unsent entry is kept for a later try. A lost report or basket result
// hides a trade from the adapter's breakers, so only a refused payload is dropped.
bool OutboxIsRetryable(const int status)
{
   if(HttpIsRetryable(status))
      return true;
   return status == HTTP_UNAUTHORIZED || status == HTTP_NOT_FOUND
          || status == HTTP_REQUEST_TIMEOUT || status == HTTP_CONFLICT
          || status == HTTP_TOO_MANY_REQUESTS;
}

class COutbox
{
private:
   string            m_base;
   int               m_timeout_ms;
   long              m_head;          // byte offset of the oldest unsent line
   int               m_pending;
   datetime          m_next_try;
   int               m_backoff_s;

   long              FileBytes(void);
   int               ReadBytes(const long from, const int count, uchar &buf[]);
   bool              Append(const uchar &line[]);
   bool              Truncate(void);
   bool              Compact(const long size);
   bool              MakeRoom(const int bytes);
   int               CountLines(const long from, const long size);
   void              CloseFragment(const long size);
   int               ReadHead(string &path, string &body, long &next_head);
   void              Consume(const long next_head);
   void              SetHead(const long head);
   void              Backoff(const HttpResult &result, const string path);

public:
                     COutbox(void) : m_base(""), m_timeout_ms(0), m_head(0), m_pending(0),
                                     m_next_try(0), m_backoff_s(0) {}
   bool              Init(const string base, const int timeout_ms);
   bool              Enqueue(const string path, const string body);
   void              Service(void);
   int               Pending(void) const { return m_pending; }
};

COutbox g_outbox;

long COutbox::FileBytes(void)
{
   if(!FileIsExist(OUTBOX_FILE))
      return 0;
   int handle = FileOpen(OUTBOX_FILE, FILE_READ | FILE_BIN | OUTBOX_SHARE);
   if(handle == INVALID_HANDLE)
      return -1;
   long size = (long)FileSize(handle);
   FileClose(handle);
   return size;
}

int COutbox::ReadBytes(const long from, const int count, uchar &buf[])
{
   ArrayResize(buf, 0);
   int handle = FileOpen(OUTBOX_FILE, FILE_READ | FILE_BIN | OUTBOX_SHARE);
   if(handle == INVALID_HANDLE)
      return -1;
   int n = 0;
   if(count > 0 && FileSeek(handle, from, SEEK_SET))
   {
      ArrayResize(buf, count);
      n = (int)FileReadArray(handle, buf, 0, count);
      ArrayResize(buf, MathMax(n, 0));
   }
   FileClose(handle);
   return n;
}

bool COutbox::Append(const uchar &line[])
{
   bool folder_ready = FileIsExist(OUTBOX_FILE) || FolderCreate(OUTBOX_DIR);
   ResetLastError();
   int handle = FileOpen(OUTBOX_FILE, FILE_READ | FILE_WRITE | FILE_BIN | FILE_SHARE_READ);
   if(handle == INVALID_HANDLE)
   {
      PrintFormat("V6 outbox: cannot open MQL5\\Files\\%s (err=%d, folder %s)", OUTBOX_FILE,
                  GetLastError(), folder_ready ? "ready" : "not created");
      return false;
   }
   int n = ArraySize(line);
   bool ok = FileSeek(handle, 0, SEEK_END) && (int)FileWriteArray(handle, line, 0, n) == n;
   FileClose(handle);
   return ok;
}

bool COutbox::Truncate(void)
{
   int handle = FileOpen(OUTBOX_FILE, FILE_WRITE | FILE_BIN);
   if(handle == INVALID_HANDLE)
      return false;
   FileClose(handle);
   return true;
}

// Moves the unsent tail to a fresh file. The head is reset before the move, so
// a crash in between can only resend lines, never lose them.
bool COutbox::Compact(const long size)
{
   long from = m_head;
   uchar tail[];
   int n = ReadBytes(from, (int)(size - from), tail);
   if(n != (int)(size - from))
      return false;
   int handle = FileOpen(OUTBOX_TEMP_FILE, FILE_WRITE | FILE_BIN);
   if(handle == INVALID_HANDLE)
      return false;
   bool written = (n == 0) || (int)FileWriteArray(handle, tail, 0, n) == n;
   FileClose(handle);
   if(!written)
      return false;
   SetHead(0);
   if(FileMove(OUTBOX_TEMP_FILE, 0, OUTBOX_FILE, FILE_REWRITE))
      return true;
   SetHead(from);
   return false;
}

bool COutbox::MakeRoom(const int bytes)
{
   long size = FileBytes();
   if(size < 0)
      return false;
   if(size + bytes <= OUTBOX_MAX_BYTES)
      return true;
   if(m_head > 0 && m_head <= size && Compact(size))
      size = FileBytes();
   if(size >= 0 && size + bytes <= OUTBOX_MAX_BYTES)
      return true;
   PrintFormat("V6 outbox: full (%I64d bytes, %d unsent entries); the new entry is dropped", size, m_pending);
   return false;
}

int COutbox::CountLines(const long from, const long size)
{
   if(size <= from)
      return 0;
   uchar buf[];
   int n = ReadBytes(from, (int)(size - from), buf);
   int lines = 0;
   for(int i = 0; i < n; i++)
   {
      if(buf[i] == OUTBOX_NEWLINE)
         lines++;
   }
   return lines;
}

// A crash during an append can leave a line without its newline; closing it
// turns it into one unreadable line that Service skips.
void COutbox::CloseFragment(const long size)
{
   uchar last[];
   if(size <= 0 || ReadBytes(size - 1, 1, last) != 1 || last[0] == OUTBOX_NEWLINE)
      return;
   uchar newline[1];
   newline[0] = (uchar)OUTBOX_NEWLINE;
   if(!Append(newline))
      Print("V6 outbox: could not close a partial line");
}

bool COutbox::Init(const string base, const int timeout_ms)
{
   m_base = base;
   m_timeout_ms = timeout_ms;
   m_next_try = 0;
   m_backoff_s = 0;
   m_head = (long)PersistGet(OUTBOX_HEAD_NAME, 0.0);
   long size = FileBytes();
   if(size < 0)
   {
      Print("V6 outbox: MQL5\\Files\\" + OUTBOX_FILE + " is not readable; reports cannot be queued");
      m_pending = 0;
      return false;
   }
   if(m_head < 0 || m_head > size)
      SetHead(0);
   CloseFragment(size);
   m_pending = CountLines(m_head, FileBytes());
   return true;
}

bool COutbox::Enqueue(const string path, const string body)
{
   CJsonObject entry;
   entry.AddStr("path", path);
   entry.AddStr("body", body);
   uchar line[];
   int n = TextBytes(entry.Text() + "\n", line);
   bool queued = n <= OUTBOX_MAX_LINE_BYTES && MakeRoom(n) && Append(line);
   if(!queued)
   {
      PrintFormat("V6 outbox: %s entry of %d bytes NOT queued", path, n);
      return false;
   }
   m_pending++;
   return true;
}

int COutbox::ReadHead(string &path, string &body, long &next_head)
{
   uchar buf[];
   int n = ReadBytes(m_head, OUTBOX_MAX_LINE_BYTES + 1, buf);
   if(n <= 0)
      return OUTBOX_READ_EMPTY;
   int end = -1;
   for(int i = 0; i < n && end < 0; i++)
   {
      if(buf[i] == OUTBOX_NEWLINE)
         end = i;
   }
   next_head = m_head + ((end < 0) ? n : end + 1);
   if(end <= 0)
      return OUTBOX_READ_BAD;
   string line = CharArrayToString(buf, 0, end, CP_UTF8);
   bool known = JsonGetString(line, "path", path) && JsonGetString(line, "body", body)
                && (path == PATH_EXECUTION || path == PATH_ACTION
                    || path == PATH_BASKET_RESULT);
   return known ? OUTBOX_READ_OK : OUTBOX_READ_BAD;
}

// Flushed at once: a head lost in a terminal crash would resend sent lines.
void COutbox::SetHead(const long head)
{
   m_head = head;
   if(GlobalVariableSet(OUTBOX_HEAD_NAME, (double)head) == 0)
      PrintFormat("V6 outbox: head offset not stored (err=%d)", GetLastError());
   GlobalVariablesFlush();
}

// The file is emptied before the head is reset: a crash in between leaves a
// head past the end, which Init treats as zero.
void COutbox::Consume(const long next_head)
{
   m_pending = MathMax(m_pending - 1, 0);
   long size = FileBytes();
   if(size >= 0 && next_head >= size && Truncate())
   {
      SetHead(0);
      m_pending = 0;
      return;
   }
   SetHead(next_head);
}

void COutbox::Backoff(const HttpResult &result, const string path)
{
   bool first = (m_backoff_s == 0);
   m_backoff_s = first ? OUTBOX_BACKOFF_FIRST_S : MathMin(m_backoff_s * 2, OUTBOX_BACKOFF_MAX_S);
   m_next_try = TimeGMT() + m_backoff_s;
   if(first || m_backoff_s == OUTBOX_BACKOFF_MAX_S)
      PrintFormat("%s; %d entries kept, next try in %d s",
                  HttpDescribeFailure("outbox " + path, m_base + path, result), m_pending, m_backoff_s);
}

void COutbox::Service(void)
{
   if(m_pending <= 0 || TimeGMT() < m_next_try)
      return;
   string path = "", body = "";
   long next_head = m_head;
   int read = ReadHead(path, body, next_head);
   if(read == OUTBOX_READ_EMPTY)
   {
      m_pending = 0;
      return;
   }
   if(read == OUTBOX_READ_BAD)
   {
      PrintFormat("V6 outbox: unreadable entry at byte %I64d skipped", m_head);
      Consume(next_head);
      return;
   }
   HttpResult result;
   bool sent = HttpPostJson(m_base + path, body, m_timeout_ms, 1, result);
   if(!sent && OutboxIsRetryable(result.status))
   {
      Backoff(result, path);
      return;
   }
   if(!sent)
      Print(HttpDescribeFailure("outbox " + path, m_base + path, result),
            "; payload refused, entry dropped");
   m_backoff_s = 0;
   Consume(next_head);
}

#endif // QLIPV6_OUTBOX_MQH
