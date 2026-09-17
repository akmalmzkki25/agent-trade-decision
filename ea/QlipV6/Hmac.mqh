//+------------------------------------------------------------------+
//| QlipV6/Hmac.mqh                                                  |
//| HMAC-SHA256 (RFC 2104) on top of CryptEncode's SHA-256, the V6   |
//| key file and the request signing payload                         |
//| (docs/v6-wire-contract.md sections 2-4).                         |
//|                                                                  |
//| The key bytes live only in g_hmac_key. They are never printed,   |
//| logged or sent; only the 12-character fingerprint may be logged. |
//+------------------------------------------------------------------+
#ifndef QLIPV6_HMAC_MQH
#define QLIPV6_HMAC_MQH

#include "Json.mqh"

#define SHA256_BYTES          32
#define HMAC_BLOCK_BYTES      64
#define HMAC_IPAD             0x36
#define HMAC_OPAD             0x5c
#define HMAC_HEX_CHARS        64
#define KEY_MIN_CHARS         32
#define KEY_MAX_CHARS         256
#define KEY_FILE_MAX_BYTES    1024
#define KEY_CHAR_FIRST        0x21   // printable ASCII without the space
#define KEY_CHAR_LAST         0x7e
#define KEY_READ_MISSING      (-1)
#define KEY_READ_TOO_LARGE    (-2)
#define PLACEHOLDER_COUNT     4
#define FINGERPRINT_LABEL     "qlip-v6-key-fingerprint"
#define FINGERPRINT_CHARS     12

uchar  g_hmac_key[];
bool   g_hmac_key_loaded = false;
string g_key_problem = "no HMAC key loaded";

//--- bytes ---------------------------------------------------------

// UTF-8 bytes of `text` without the terminating NUL (V6 texts are ASCII).
int TextBytes(const string text, uchar &out[])
{
   int n = StringToCharArray(text, out, 0, WHOLE_ARRAY, CP_UTF8);
   if(n > 0 && out[n - 1] == 0)
      n--;
   n = MathMax(n, 0);
   ArrayResize(out, n);
   return n;
}

void ZeroBytes(uchar &bytes[])
{
   int n = ArraySize(bytes);
   for(int i = 0; i < n; i++)
      bytes[i] = 0;
}

string BytesToHex(const uchar &bytes[])
{
   string out = "";
   int n = ArraySize(bytes);
   for(int i = 0; i < n; i++)
      StringAdd(out, StringFormat("%02x", (int)bytes[i]));
   return out;
}

bool HexToBytes(const string hex, uchar &out[])
{
   int n = StringLen(hex);
   if(n % 2 != 0)
      return false;
   ArrayResize(out, n / 2);
   for(int i = 0; i < n / 2; i++)
   {
      int high = HexDigitValue(StringGetCharacter(hex, 2 * i));
      int low = HexDigitValue(StringGetCharacter(hex, 2 * i + 1));
      if(high < 0 || low < 0)
         return false;
      out[i] = (uchar)(high * JSON_HEX_RADIX + low);
   }
   return true;
}

// Same length and same characters, examined in full whatever differs.
bool ConstantTimeEquals(const string a, const string b)
{
   int n = StringLen(a);
   if(n != StringLen(b))
      return false;
   int diff = 0;
   for(int i = 0; i < n; i++)
      diff |= (int)StringGetCharacter(a, i) ^ (int)StringGetCharacter(b, i);
   return diff == 0;
}

//--- SHA-256 and HMAC ----------------------------------------------

// CryptEncode ignores the key argument for hash methods; a one-byte array is
// passed rather than an empty one. HmacSelfTest proves the digest is standard.
bool Sha256(const uchar &data[], uchar &digest[])
{
   uchar unused_key[1];
   unused_key[0] = 0;
   ArrayResize(digest, 0);
   ResetLastError();
   int written = CryptEncode(CRYPT_HASH_SHA256, data, unused_key, digest);
   return written == SHA256_BYTES && ArraySize(digest) == SHA256_BYTES;
}

// The 64-byte key block: short keys are zero-padded, long keys hashed first.
bool HmacKeyBlock(const uchar &key[], uchar &block[])
{
   int n = ArraySize(key);
   ArrayResize(block, HMAC_BLOCK_BYTES);
   ZeroBytes(block);
   if(n <= 0)
      return false;
   if(n > HMAC_BLOCK_BYTES)
   {
      uchar digest[];
      if(!Sha256(key, digest))
         return false;
      for(int i = 0; i < SHA256_BYTES; i++)
         block[i] = digest[i];
      return true;
   }
   for(int i = 0; i < n; i++)
      block[i] = key[i];
   return true;
}

// (block XOR pad) followed by `tail`.
void PadAndAppend(const uchar &block[], const uchar pad, const uchar &tail[], uchar &out[])
{
   int n = ArraySize(tail);
   ArrayResize(out, HMAC_BLOCK_BYTES + n);
   for(int i = 0; i < HMAC_BLOCK_BYTES; i++)
      out[i] = (uchar)(block[i] ^ pad);
   for(int i = 0; i < n; i++)
      out[HMAC_BLOCK_BYTES + i] = tail[i];
}

// H((K ^ opad) || H((K ^ ipad) || message))
bool HmacSha256(const uchar &key[], const uchar &message[], uchar &mac[])
{
   uchar block[], inner[], inner_digest[], outer[];
   bool ok = HmacKeyBlock(key, block);
   if(ok)
   {
      PadAndAppend(block, (uchar)HMAC_IPAD, message, inner);
      ok = Sha256(inner, inner_digest);
   }
   if(ok)
   {
      PadAndAppend(block, (uchar)HMAC_OPAD, inner_digest, outer);
      ok = Sha256(outer, mac);
   }
   ZeroBytes(block);
   ZeroBytes(inner);
   ZeroBytes(outer);
   return ok;
}

// Lowercase hex HMAC-SHA256 of `data`; "" when hashing failed.
string SignBytes(const uchar &key[], const uchar &data[])
{
   uchar mac[];
   if(!HmacSha256(key, data, mac))
      return "";
   return BytesToHex(mac);
}

string SignText(const uchar &key[], const string text)
{
   uchar data[];
   TextBytes(text, data);
   return SignBytes(key, data);
}

// wire.request_signing_payload: ts "\n" METHOD "\n" PATH "\n" + the raw body.
void SigningPayload(const long ts, const string method, const string path,
                    const uchar &body[], uchar &payload[])
{
   string head = IntegerToString(ts) + "\n" + method + "\n" + path + "\n";
   int head_len = TextBytes(head, payload);
   int body_len = ArraySize(body);
   ArrayResize(payload, head_len + body_len);
   for(int i = 0; i < body_len; i++)
      payload[head_len + i] = body[i];
}

// wire.key_fingerprint: lets an operator compare keys without seeing them.
string KeyFingerprint(const uchar &key[])
{
   return StringSubstr(SignText(key, FINGERPRINT_LABEL), 0, FINGERPRINT_CHARS);
}

//--- the key file --------------------------------------------------

bool IsKeyTrailer(const uchar c)
{
   return c == '\r' || c == '\n' || c == ' ' || c == '\t';
}

// File bytes without trailing CR/LF/space/tab, or a KEY_READ_* code.
int ReadKeyFile(const string file_name, uchar &bytes[], int &error)
{
   ResetLastError();
   int handle = FileOpen(file_name, FILE_READ | FILE_BIN | FILE_SHARE_READ);
   error = GetLastError();
   if(handle == INVALID_HANDLE)
      return KEY_READ_MISSING;
   ulong size = FileSize(handle);
   int n = KEY_READ_TOO_LARGE;
   if(size <= (ulong)KEY_FILE_MAX_BYTES)
   {
      ArrayResize(bytes, (int)size);
      n = (size > 0) ? (int)FileReadArray(handle, bytes, 0, (int)size) : 0;
   }
   FileClose(handle);
   while(n > 0 && IsKeyTrailer(bytes[n - 1]))
      n--;
   return n;
}

bool KeyBytesPrintable(const uchar &bytes[], const int n)
{
   for(int i = 0; i < n; i++)
   {
      if(bytes[i] < KEY_CHAR_FIRST || bytes[i] > KEY_CHAR_LAST)
         return false;
   }
   return true;
}

// Mirrors PLACEHOLDER_FRAGMENTS in adapter/app/v6/config.py.
bool KeyLooksLikePlaceholder(const uchar &bytes[], const int n)
{
   string lowered = CharArrayToString(bytes, 0, n, CP_UTF8);
   StringToLower(lowered);
   string fragments[PLACEHOLDER_COUNT] = {"change-me", "changeme", "your-key", "placeholder"};
   bool found = false;
   for(int i = 0; i < PLACEHOLDER_COUNT && !found; i++)
      found = StringFind(lowered, fragments[i]) >= 0;
   lowered = "";
   return found;
}

// Why the key cannot be used ("" when it can); never includes the key itself.
string KeyProblem(const string file_name, const uchar &bytes[], const int n, const int error)
{
   if(n == KEY_READ_MISSING)
      return StringFormat("no key file MQL5\\Files\\%s (err=%d)", file_name, error);
   if(n == KEY_READ_TOO_LARGE)
      return StringFormat("key file MQL5\\Files\\%s is larger than %d bytes", file_name, KEY_FILE_MAX_BYTES);
   if(n < KEY_MIN_CHARS || n > KEY_MAX_CHARS)
      return StringFormat("the key must be %d-%d characters", KEY_MIN_CHARS, KEY_MAX_CHARS);
   if(!KeyBytesPrintable(bytes, n))
      return "the key must be printable ASCII without spaces (and the file must have no BOM)";
   if(KeyLooksLikePlaceholder(bytes, n))
      return "the key is a placeholder";
   return "";
}

// Loads MQL5\Files\<file_name> into g_hmac_key; g_key_problem says why not.
bool LoadHmacKey(const string file_name)
{
   ZeroBytes(g_hmac_key);
   ArrayResize(g_hmac_key, 0);
   g_hmac_key_loaded = false;
   uchar bytes[];
   int error = 0;
   int n = ReadKeyFile(file_name, bytes, error);
   g_key_problem = KeyProblem(file_name, bytes, n, error);
   if(g_key_problem == "")
   {
      ArrayResize(g_hmac_key, n);
      for(int i = 0; i < n; i++)
         g_hmac_key[i] = bytes[i];
      g_hmac_key_loaded = true;
   }
   ZeroBytes(bytes);
   return g_hmac_key_loaded;
}

void WipeHmacKey(void)
{
   ZeroBytes(g_hmac_key);
   ArrayResize(g_hmac_key, 0);
   g_hmac_key_loaded = false;
}

#endif // QLIPV6_HMAC_MQH
