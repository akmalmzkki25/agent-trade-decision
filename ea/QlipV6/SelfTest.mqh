//+------------------------------------------------------------------+
//| QlipV6/SelfTest.mqh                                              |
//| OnInit self-test of the signing code against the public golden   |
//| vectors in adapter/tests/v6/golden/hmac_vectors.json (RFC 4231,  |
//| signed requests, canonical intents). test_ea_wire_parity.py      |
//| checks that every value below equals the golden file. If any     |
//| vector fails the EA never executes (contract section 5).         |
//| SELFTEST_KEY is public and never valid for production use.       |
//+------------------------------------------------------------------+
#ifndef QLIPV6_SELFTEST_MQH
#define QLIPV6_SELFTEST_MQH

#include "Json.mqh"
#include "Hmac.mqh"
#include "Intent.mqh"

#define SELFTEST_KEY              "qlip-v6-public-test-vector-key-0123456789"
#define SELFTEST_KEY_FINGERPRINT  "3ddbc8fce1f7"
#define SELFTEST_POINT            0.01
#define SELFTEST_INT_VALUE        7200

string g_selftest_failed = "";

// Keeps the first failing vector's name for the startup log.
bool NoteVector(const bool ok, const string name)
{
   if(!ok && g_selftest_failed == "")
      g_selftest_failed = name;
   return ok;
}

bool HmacRowOk(const string name, const string key_hex, const string data_hex, const string sig)
{
   uchar key[], data[];
   bool ok = HexToBytes(key_hex, key) && HexToBytes(data_hex, data) && SignBytes(key, data) == sig;
   return NoteVector(ok, name);
}

bool RequestRowOk(const string name, const long ts, const string method, const string path,
                  const string body, const string sig)
{
   uchar key[], body_bytes[], payload[];
   TextBytes(SELFTEST_KEY, key);
   TextBytes(body, body_bytes);
   SigningPayload(ts, method, path, body_bytes, payload);
   return NoteVector(SignBytes(key, payload) == sig, name);
}

// The reply goes through the live parser, canonical builder and verifier.
bool IntentRowOk(const string name, const string response, const double point,
                 const string canonical, const string sig)
{
   uchar key[];
   TextBytes(SELFTEST_KEY, key);
   PollReply reply;
   bool ok = ParsePollReply(response, reply)
             && IntentCanonical(reply, point) == canonical
             && SignText(key, canonical) == sig
             && reply.sig == sig
             && PollReplySignatureOk(reply, key, point);
   return NoteVector(ok, name);
}

bool FingerprintOk(void)
{
   uchar key[];
   TextBytes(SELFTEST_KEY, key);
   return NoteVector(KeyFingerprint(key) == SELFTEST_KEY_FINGERPRINT, "fingerprint");
}

// The buy-limit vector with entry moved by one tick, then with no signature:
// both must fail verification.
bool IntentTamperRejected(void)
{
   string tampered = "{\"schema_version\":\"v6.intent.1\",\"server_time_epoch\":1789565408,\"command\":\"NONE\",\"has_intent\":true,\"intent_id\":\"k7w2m4pq3xza\",\"source\":\"operator\",\"require_demo\":1,\"side\":\"buy\",\"order_type\":\"BUY_LIMIT\",\"entry\":4535.08,\"sl\":4528.07,\"tp\":4549.07,\"lots\":0.01,\"ref_price\":4535.35,\"max_drift_points\":200,\"max_spread_points\":35,\"valid_until_epoch\":1789565528,\"pending_expiry_epoch\":1789567200,\"time_barrier_s\":7200,\"magic\":250570,\"sig\":\"9aee1b5eb5d37dfc995f74c6d3262df6adf63d31a43f264b53632ef2a43e016d\"}";
   uchar key[];
   TextBytes(SELFTEST_KEY, key);
   PollReply reply;
   bool ok = ParsePollReply(tampered, reply) && !PollReplySignatureOk(reply, key, SELFTEST_POINT);
   reply.sig = "";
   ok = ok && !PollReplySignatureOk(reply, key, SELFTEST_POINT);
   return NoteVector(ok, "tampered-intent");
}

// Integer fields are read strictly: 7200.0, "7200" and 7.2e3 are refused.
bool IntegerReaderStrict(void)
{
   long value = 0;
   bool ok = JsonGetLong("{\"n\":7200}", "n", value) && value == SELFTEST_INT_VALUE
             && !JsonGetLong("{\"n\":7200.0}", "n", value)
             && !JsonGetLong("{\"n\":\"7200\"}", "n", value)
             && !JsonGetLong("{\"n\":7.2e3}", "n", value);
   return NoteVector(ok, "integer-reader");
}

// Every vector runs even after a failure; g_selftest_failed names the first.
bool HmacSelfTest(void)
{
   g_selftest_failed = "";
   bool ok = true;
   ok = HmacRowOk("rfc4231-tc1",
                  "0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b",
                  "4869205468657265",
                  "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7") && ok;
   ok = HmacRowOk("rfc4231-tc2",
                  "4a656665",
                  "7768617420646f2079612077616e7420666f72206e6f7468696e673f",
                  "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843") && ok;
   ok = HmacRowOk("rfc4231-tc6",
                  "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                  "54657374205573696e67204c6172676572205468616e20426c6f636b2d53697a65204b6579202d2048617368204b6579204669727374",
                  "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54") && ok;
   ok = RequestRowOk("poll", 1789565407, "POST", "/v6/intent/poll",
                     "{\"schema_version\":\"v6.poll.1\",\"login\":\"10000001\",\"trade_mode\":\"DEMO\",\"server\":\"MetaQuotes-Demo\",\"sent_at_epoch\":1789565407,\"balance\":92429.34,\"equity\":92429.34,\"free_margin\":92429.34,\"bid\":4535.18,\"ask\":4535.35,\"spread_points\":17,\"open_v6_positions\":0,\"pending_v6_orders\":0,\"floating_pnl_v6\":0.0,\"last_intent_id\":\"\",\"local_halt\":false}",
                     "4c012a7fcd0e92c2f027ff5e88cc82a01f3a19543f9cc6a1f24d5dad69a99b58") && ok;
   ok = RequestRowOk("execution", 1789565410, "POST", "/v6/execution",
                     "{\"schema_version\":\"v6.execution.1\",\"intent_id\":\"k7w2m4pq3xza\",\"status\":\"placed\",\"reason_code\":\"NONE\",\"ticket\":5012345702,\"retcode\":10008,\"requested_price\":4535.07,\"fill_price\":0.0,\"slippage_points\":0.0,\"spread_points\":17,\"latency_ms\":42,\"sent_at_epoch\":1789565410}",
                     "1e0e4817df4c76823576626840a5dcf457283028b08d58dad55b2949dcabf5c5") && ok;
   ok = IntentRowOk("idle",
                    "{\"schema_version\":\"v6.intent.1\",\"server_time_epoch\":1789565408,\"command\":\"NONE\",\"has_intent\":false,\"intent_id\":\"\",\"source\":\"\",\"require_demo\":1,\"side\":\"\",\"order_type\":\"NONE\",\"entry\":0.0,\"sl\":0.0,\"tp\":0.0,\"lots\":0.0,\"ref_price\":0.0,\"max_drift_points\":0,\"max_spread_points\":0,\"valid_until_epoch\":0,\"pending_expiry_epoch\":0,\"time_barrier_s\":0,\"magic\":0,\"sig\":\"129a6abc2d97675b715acf3f39cec48f207a439212951f2ab655385c3bc73400\"}",
                    0.01, "v6.intent.1|1789565408|NONE|0|||1||NONE|0|0|0|0|0|0|0|0|0|0|0",
                    "129a6abc2d97675b715acf3f39cec48f207a439212951f2ab655385c3bc73400") && ok;
   ok = IntentRowOk("cancel-pending",
                    "{\"schema_version\":\"v6.intent.1\",\"server_time_epoch\":1789565408,\"command\":\"CANCEL_PENDING\",\"has_intent\":false,\"intent_id\":\"\",\"source\":\"\",\"require_demo\":1,\"side\":\"\",\"order_type\":\"NONE\",\"entry\":0.0,\"sl\":0.0,\"tp\":0.0,\"lots\":0.0,\"ref_price\":0.0,\"max_drift_points\":0,\"max_spread_points\":0,\"valid_until_epoch\":0,\"pending_expiry_epoch\":0,\"time_barrier_s\":0,\"magic\":0,\"sig\":\"91753aab56ba26dbf52794c8c5f49af611731e9246d1bcb5f41257c5ce2b2716\"}",
                    0.01, "v6.intent.1|1789565408|CANCEL_PENDING|0|||1||NONE|0|0|0|0|0|0|0|0|0|0|0",
                    "91753aab56ba26dbf52794c8c5f49af611731e9246d1bcb5f41257c5ce2b2716") && ok;
   ok = IntentRowOk("buy-limit",
                    "{\"schema_version\":\"v6.intent.1\",\"server_time_epoch\":1789565408,\"command\":\"NONE\",\"has_intent\":true,\"intent_id\":\"k7w2m4pq3xza\",\"source\":\"operator\",\"require_demo\":1,\"side\":\"buy\",\"order_type\":\"BUY_LIMIT\",\"entry\":4535.07,\"sl\":4528.07,\"tp\":4549.07,\"lots\":0.01,\"ref_price\":4535.35,\"max_drift_points\":200,\"max_spread_points\":35,\"valid_until_epoch\":1789565528,\"pending_expiry_epoch\":1789567200,\"time_barrier_s\":7200,\"magic\":250570,\"sig\":\"9aee1b5eb5d37dfc995f74c6d3262df6adf63d31a43f264b53632ef2a43e016d\"}",
                    0.01, "v6.intent.1|1789565408|NONE|1|k7w2m4pq3xza|operator|1|buy|BUY_LIMIT|453507|452807|454907|1|453535|200|35|1789565528|1789567200|7200|250570",
                    "9aee1b5eb5d37dfc995f74c6d3262df6adf63d31a43f264b53632ef2a43e016d") && ok;
   ok = IntentRowOk("sell-market",
                    "{\"schema_version\":\"v6.intent.1\",\"server_time_epoch\":1789565408,\"command\":\"NONE\",\"has_intent\":true,\"intent_id\":\"b5n6r7t2vw3y\",\"source\":\"operator\",\"require_demo\":1,\"side\":\"sell\",\"order_type\":\"SELL\",\"entry\":4535.18,\"sl\":4542.18,\"tp\":4521.18,\"lots\":0.01,\"ref_price\":4535.18,\"max_drift_points\":150,\"max_spread_points\":30,\"valid_until_epoch\":1789565468,\"pending_expiry_epoch\":0,\"time_barrier_s\":7200,\"magic\":250570,\"sig\":\"1f203e109dc5a53931774513144d3912f5c08fbfffc9887027cc404c72102a12\"}",
                    0.01, "v6.intent.1|1789565408|NONE|1|b5n6r7t2vw3y|operator|1|sell|SELL|453518|454218|452118|1|453518|150|30|1789565468|0|7200|250570",
                    "1f203e109dc5a53931774513144d3912f5c08fbfffc9887027cc404c72102a12") && ok;
   ok = FingerprintOk() && ok;
   ok = IntentTamperRejected() && ok;
   ok = IntegerReaderStrict() && ok;
   return ok;
}

#endif // QLIPV6_SELFTEST_MQH
