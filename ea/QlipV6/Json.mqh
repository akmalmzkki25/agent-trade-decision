//+------------------------------------------------------------------+
//| QlipV6/Json.mqh                                                  |
//| JSON writing helpers and a reader for FLAT objects only.         |
//|                                                                  |
//| The adapter validates every payload strictly (unknown fields,    |
//| NaN and wrong number kinds are rejected), so integers are always |
//| written without a decimal point and doubles never as NaN/inf.   |
//+------------------------------------------------------------------+
#ifndef QLIPV6_JSON_MQH
#define QLIPV6_JSON_MQH

#define JSON_PRINTABLE_FIRST 32
#define JSON_PRINTABLE_LAST  126
#define JSON_HEX_RADIX       16
#define JSON_UNICODE_DIGITS  4
#define JSON_BACKSPACE       8
#define JSON_FORMFEED        12

//--- writing -------------------------------------------------------

// Everything outside printable ASCII leaves as \uXXXX, so a request body is
// pure ASCII and its byte length always equals its character length.
string JsonEscape(const string text)
{
   string out = "";
   int n = StringLen(text);
   for(int i = 0; i < n; i++)
   {
      ushort c = StringGetCharacter(text, i);
      if(c == '"')
         StringAdd(out, "\\\"");
      else if(c == '\\')
         StringAdd(out, "\\\\");
      else if(c >= JSON_PRINTABLE_FIRST && c <= JSON_PRINTABLE_LAST)
         StringAdd(out, ShortToString(c));
      else
         StringAdd(out, StringFormat("\\u%04x", (int)c));
   }
   return out;
}

string JStr(const string text) { return "\"" + JsonEscape(text) + "\""; }

// A non-finite value becomes 0, which the adapter's positive-field checks
// then reject loudly; it never travels as the invalid literal NaN.
string JNum(const double value, const int digits)
{
   if(!MathIsValidNumber(value))
      return DoubleToString(0.0, digits);
   return DoubleToString(value, digits);
}

string JInt(const long value) { return IntegerToString(value); }
string JBool(const bool value) { return value ? "true" : "false"; }

class CJsonObject
{
private:
   string            m_body;
   void              Add(const string key, const string raw)
     {
      if(StringLen(m_body) > 0)
         StringAdd(m_body, ",");
      StringAdd(m_body, "\"" + key + "\":" + raw);
     }
public:
                     CJsonObject(void) : m_body("") {}
   void              AddStr(const string key, const string value)  { Add(key, JStr(value)); }
   void              AddNum(const string key, const double value, const int digits) { Add(key, JNum(value, digits)); }
   void              AddInt(const string key, const long value)    { Add(key, JInt(value)); }
   void              AddBool(const string key, const bool value)   { Add(key, JBool(value)); }
   void              AddRaw(const string key, const string json)   { Add(key, json); }
   void              AddNull(const string key)                     { Add(key, "null"); }
   string            Text(void) const                              { return "{" + m_body + "}"; }
};

class CJsonArray
{
private:
   string            m_body;
   int               m_count;
public:
                     CJsonArray(void) : m_body(""), m_count(0) {}
   void              AddRaw(const string json)
     {
      if(m_count > 0)
         StringAdd(m_body, ",");
      StringAdd(m_body, json);
      m_count++;
     }
   int               Count(void) const { return m_count; }
   string            Text(void) const  { return "[" + m_body + "]"; }
};

//--- text sanitising -----------------------------------------------

// Broker-supplied text (server names, event names, order comments) is
// untrusted: only printable ASCII survives, cut to `max_len`.
string SanitizeAscii(const string text, const int max_len)
{
   string out = "";
   int n = StringLen(text);
   for(int i = 0; i < n && StringLen(out) < max_len; i++)
   {
      ushort c = StringGetCharacter(text, i);
      if(c >= JSON_PRINTABLE_FIRST && c <= JSON_PRINTABLE_LAST)
         StringAdd(out, ShortToString(c));
   }
   return out;
}

bool IsSlugChar(const ushort c)
{
   return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9');
}

// Lowercase [a-z0-9-] with single dashes and none at either end, as the
// calendar `code` field requires. May return "" when nothing survives.
string SlugCode(const string text, const int max_len)
{
   string lower = text;
   StringToLower(lower);
   string out = "";
   bool pending_dash = false;
   int n = StringLen(lower);
   for(int i = 0; i < n && StringLen(out) < max_len; i++)
   {
      ushort c = StringGetCharacter(lower, i);
      if(!IsSlugChar(c))
      {
         pending_dash = StringLen(out) > 0;
         continue;
      }
      if(pending_dash)
      {
         // A dash is only worth writing if the next word still fits after it.
         if(StringLen(out) + 2 > max_len)
            break;
         StringAdd(out, "-");
      }
      pending_dash = false;
      StringAdd(out, ShortToString(c));
   }
   return out;
}

//--- flat-object reader --------------------------------------------

bool IsJsonSpace(const ushort c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n'; }

int SkipJsonSpace(const string json, int pos)
{
   int n = StringLen(json);
   while(pos < n && IsJsonSpace(StringGetCharacter(json, pos)))
      pos++;
   return pos;
}

int HexDigitValue(const ushort c)
{
   if(c >= '0' && c <= '9') return (int)(c - '0');
   if(c >= 'a' && c <= 'f') return (int)(c - 'a') + 10;
   if(c >= 'A' && c <= 'F') return (int)(c - 'A') + 10;
   return -1;
}

// Decodes \uXXXX at `pos` (pointing at the first hex digit); -1 on error.
int ReadUnicodeEscape(const string json, const int pos)
{
   if(pos + JSON_UNICODE_DIGITS > StringLen(json))
      return -1;
   int code = 0;
   for(int i = 0; i < JSON_UNICODE_DIGITS; i++)
   {
      int digit = HexDigitValue(StringGetCharacter(json, pos + i));
      if(digit < 0)
         return -1;
      code = code * JSON_HEX_RADIX + digit;
   }
   return code;
}

string SimpleEscapeValue(const ushort c)
{
   switch(c)
   {
      case '"':  return "\"";
      case '\\': return "\\";
      case '/':  return "/";
      case 'b':  return ShortToString((ushort)JSON_BACKSPACE);
      case 'f':  return ShortToString((ushort)JSON_FORMFEED);
      case 'n':  return "\n";
      case 'r':  return "\r";
      case 't':  return "\t";
   }
   return "";
}

// Reads a string literal starting at the opening quote. Returns the index
// just past the closing quote, or -1 when the literal is malformed.
int ReadJsonString(const string json, const int start, string &value)
{
   value = "";
   int n = StringLen(json);
   if(start >= n || StringGetCharacter(json, start) != '"')
      return -1;
   for(int i = start + 1; i < n; i++)
   {
      ushort c = StringGetCharacter(json, i);
      if(c == '"')
         return i + 1;
      if(c != '\\')
      {
         StringAdd(value, ShortToString(c));
         continue;
      }
      if(++i >= n)
         return -1;
      ushort e = StringGetCharacter(json, i);
      if(e == 'u')
      {
         int code = ReadUnicodeEscape(json, i + 1);
         if(code < 0)
            return -1;
         StringAdd(value, ShortToString((ushort)code));
         i += JSON_UNICODE_DIGITS;
         continue;
      }
      string simple = SimpleEscapeValue(e);
      if(simple == "")
         return -1;
      StringAdd(value, simple);
   }
   return -1;
}

// Reads a bare literal (number, true, false, null) up to the next delimiter.
int ReadJsonLiteral(const string json, const int start, string &value)
{
   int n = StringLen(json);
   int i = start;
   while(i < n)
   {
      ushort c = StringGetCharacter(json, i);
      if(c == ',' || c == '}' || IsJsonSpace(c))
         break;
      if(c == '{' || c == '[' || c == '"')
         return -1;
      i++;
   }
   value = StringSubstr(json, start, i - start);
   return (i > start) ? i : -1;
}

// Reads `"member": value` at `pos` plus a trailing comma if present.
// Returns the index of the next member (or of the closing brace), -1 on error.
int ReadJsonMember(const string json, int pos, string &member, string &value, bool &quoted)
{
   int n = StringLen(json);
   pos = ReadJsonString(json, pos, member);
   if(pos < 0)
      return -1;
   pos = SkipJsonSpace(json, pos);
   if(pos >= n || StringGetCharacter(json, pos) != ':')
      return -1;
   pos = SkipJsonSpace(json, pos + 1);
   quoted = pos < n && StringGetCharacter(json, pos) == '"';
   pos = quoted ? ReadJsonString(json, pos, value) : ReadJsonLiteral(json, pos, value);
   if(pos < 0)
      return -1;
   pos = SkipJsonSpace(json, pos);
   if(pos < n && StringGetCharacter(json, pos) == ',')
   {
      pos = SkipJsonSpace(json, pos + 1);
      if(pos < n && StringGetCharacter(json, pos) == '}')
         return -1;
   }
   else if(pos >= n || StringGetCharacter(json, pos) != '}')
      return -1;
   return pos;
}

// Finds `key` among the members of a flat object. The whole document is
// read first: a nested object or array anywhere, or broken syntax, makes it
// unreadable on purpose, because the V6 intent response is flat by contract.
bool JsonFindRaw(const string json, const string key, string &raw, bool &is_string)
{
   int n = StringLen(json);
   int pos = SkipJsonSpace(json, 0);
   if(pos >= n || StringGetCharacter(json, pos) != '{')
      return false;
   pos = SkipJsonSpace(json, pos + 1);
   bool found = false;
   while(pos < n && StringGetCharacter(json, pos) != '}')
   {
      string member = "", value = "";
      bool quoted = false;
      pos = ReadJsonMember(json, pos, member, value, quoted);
      if(pos < 0)
         return false;
      if(!found && member == key)
      {
         raw = value;
         is_string = quoted;
         found = true;
      }
   }
   if(pos >= n || SkipJsonSpace(json, pos + 1) != n)
      return false;
   return found;
}

bool JsonGetString(const string json, const string key, string &value)
{
   string raw = "";
   bool is_string = false;
   if(!JsonFindRaw(json, key, raw, is_string) || !is_string)
      return false;
   value = raw;
   return true;
}

bool JsonGetBool(const string json, const string key, bool &value)
{
   string raw = "";
   bool is_string = false;
   if(!JsonFindRaw(json, key, raw, is_string) || is_string)
      return false;
   if(raw != "true" && raw != "false")
      return false;
   value = (raw == "true");
   return true;
}

bool IsJsonNumberText(const string raw)
{
   int n = StringLen(raw);
   if(n == 0)
      return false;
   for(int i = 0; i < n; i++)
   {
      ushort c = StringGetCharacter(raw, i);
      bool ok = (c >= '0' && c <= '9') || c == '-' || c == '+' || c == '.' || c == 'e' || c == 'E';
      if(!ok)
         return false;
   }
   return true;
}

bool JsonGetNumber(const string json, const string key, double &value)
{
   string raw = "";
   bool is_string = false;
   if(!JsonFindRaw(json, key, raw, is_string) || is_string || !IsJsonNumberText(raw))
      return false;
   value = StringToDouble(raw);
   return MathIsValidNumber(value);
}

// Run once at start-up: a reader that silently misparses the poll response
// is worse than an EA that refuses to load.
bool JsonSelfTest(void)
{
   string doc = "{ \"command\" : \"FLATTEN\", \"has_intent\":false,\"entry\":4535.25,"
                "\"note\":\"a\\\"b\\u0041\"}";
   string command = "", note = "";
   bool has_intent = true;
   double entry = 0.0;
   bool ok = JsonGetString(doc, "command", command) && command == "FLATTEN"
             && JsonGetBool(doc, "has_intent", has_intent) && !has_intent
             && JsonGetNumber(doc, "entry", entry) && MathAbs(entry - 4535.25) < 1e-9
             && JsonGetString(doc, "note", note) && note == "a\"bA"
             && !JsonGetString(doc, "has_intent", command)
             && !JsonGetBool("{\"x\":{\"has_intent\":true}}", "has_intent", has_intent)
             && JsonEscape("q\"\\\n") == "q\\\"\\\\\\u000a"
             && SlugCode("  Nonfarm Payrolls (NFP)! ", 60) == "nonfarm-payrolls-nfp";
   return ok;
}

#endif // QLIPV6_JSON_MQH
