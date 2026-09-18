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

// The buy-limit v2 vector with entry moved by one tick, then with no signature:
// both must fail verification.
bool IntentTamperRejected(void)
{
   string tampered = "{\"schema_version\":\"v6.intent.2\",\"server_time_epoch\":1789565408,\"command\":\"NONE\",\"has_intent\":true,\"intent_id\":\"k7w2m4pq3xza\",\"source\":\"operator\",\"require_demo\":1,\"side\":\"buy\",\"order_type\":\"BUY_LIMIT\",\"entry\":4535.08,\"sl\":4528.07,\"tp\":4549.07,\"lots\":0.01,\"ref_price\":4535.35,\"max_drift_points\":200,\"max_spread_points\":35,\"valid_until_epoch\":1789565528,\"pending_expiry_epoch\":1789567200,\"time_barrier_s\":7200,\"magic\":250570,\"tp1\":0.0,\"tp2\":0.0,\"sl_after_tp1\":0.0,\"sl_after_tp2\":0.0,\"action_id\":\"\",\"action_ticket\":0,\"action_sl\":0.0,\"action_tp\":0.0,\"action_tp1\":0.0,\"action_tp2\":0.0,\"action_sl1\":0.0,\"action_sl2\":0.0,\"action_price\":0.0,\"action_expiry_epoch\":0,\"action_barrier_s\":0,\"action_issued_epoch\":0,\"sig\":\"4010767055eaed77f1c734cf415cf174186e1651968962802073f100fd64885d\"}";
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

// RFC 4231 HMAC-SHA256 test cases.
bool HmacVectorsOk(void)
{
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
   return ok;
}

// Signed requests: the payload is ts, method, path and the raw body.
bool RequestVectorsOk(void)
{
   bool ok = true;
   ok = RequestRowOk("poll", 1789565407, "POST", "/v6/intent/poll",
                     "{\"schema_version\":\"v6.poll.1\",\"login\":\"10000001\",\"trade_mode\":\"DEMO\",\"server\":\"MetaQuotes-Demo\",\"sent_at_epoch\":1789565407,\"balance\":92429.34,\"equity\":92429.34,\"free_margin\":92429.34,\"bid\":4535.18,\"ask\":4535.35,\"spread_points\":17,\"open_v6_positions\":0,\"pending_v6_orders\":0,\"floating_pnl_v6\":0.0,\"last_intent_id\":\"\",\"local_halt\":false}",
                     "4c012a7fcd0e92c2f027ff5e88cc82a01f3a19543f9cc6a1f24d5dad69a99b58") && ok;
   ok = RequestRowOk("execution", 1789565410, "POST", "/v6/execution",
                     "{\"schema_version\":\"v6.execution.1\",\"intent_id\":\"k7w2m4pq3xza\",\"status\":\"placed\",\"reason_code\":\"NONE\",\"ticket\":5012345702,\"retcode\":10008,\"requested_price\":4535.07,\"fill_price\":0.0,\"slippage_points\":0.0,\"spread_points\":17,\"latency_ms\":42,\"sent_at_epoch\":1789565410}",
                     "1e0e4817df4c76823576626840a5dcf457283028b08d58dad55b2949dcabf5c5") && ok;
   ok = RequestRowOk("action", 1789565411, "POST", "/v6/action",
                     "{\"schema_version\":\"v6.action.1\",\"kind\":\"APPLIED\",\"action_id\":\"m3a7q2z5k6pw\",\"command\":\"MODIFY_POSITION\",\"intent_id\":\"k7w2m4pq3xza\",\"ticket\":5012345702,\"reason_code\":\"NONE\",\"retcode\":10009,\"step\":0,\"old_sl\":4528.07,\"new_sl\":4536.0,\"price\":4541.2,\"sent_at_epoch\":1789565411}",
                     "de308b442c8a2ef66952708f77bd59538e1eb385baf5d4bf6d934dcf88e5928d") && ok;
   return ok;
}

// Poll replies through the live parser, canonical builder and verifier.
bool IntentVectorsOk(void)
{
   bool ok = true;
   ok = IntentRowOk("idle",
                    "{\"schema_version\":\"v6.intent.2\",\"server_time_epoch\":1789565408,\"command\":\"NONE\",\"has_intent\":false,\"intent_id\":\"\",\"source\":\"\",\"require_demo\":1,\"side\":\"\",\"order_type\":\"NONE\",\"entry\":0.0,\"sl\":0.0,\"tp\":0.0,\"lots\":0.0,\"ref_price\":0.0,\"max_drift_points\":0,\"max_spread_points\":0,\"valid_until_epoch\":0,\"pending_expiry_epoch\":0,\"time_barrier_s\":0,\"magic\":0,\"tp1\":0.0,\"tp2\":0.0,\"sl_after_tp1\":0.0,\"sl_after_tp2\":0.0,\"action_id\":\"\",\"action_ticket\":0,\"action_sl\":0.0,\"action_tp\":0.0,\"action_tp1\":0.0,\"action_tp2\":0.0,\"action_sl1\":0.0,\"action_sl2\":0.0,\"action_price\":0.0,\"action_expiry_epoch\":0,\"action_barrier_s\":0,\"action_issued_epoch\":0,\"sig\":\"19f799c7654df28ebfe4e35b418f6c7182332fa4caeecba1ad600582e172eb4a\"}",
                    0.01,
                    "v6.intent.2|1789565408|NONE|0|||1||NONE|0|0|0|0|0|0|0|0|0|0|0|0|0|0|0||0|0|0|0|0|0|0|0|0|0|0",
                    "19f799c7654df28ebfe4e35b418f6c7182332fa4caeecba1ad600582e172eb4a") && ok;
   ok = IntentRowOk("cancel-pending",
                    "{\"schema_version\":\"v6.intent.2\",\"server_time_epoch\":1789565408,\"command\":\"CANCEL_PENDING\",\"has_intent\":false,\"intent_id\":\"\",\"source\":\"\",\"require_demo\":1,\"side\":\"\",\"order_type\":\"NONE\",\"entry\":0.0,\"sl\":0.0,\"tp\":0.0,\"lots\":0.0,\"ref_price\":0.0,\"max_drift_points\":0,\"max_spread_points\":0,\"valid_until_epoch\":0,\"pending_expiry_epoch\":0,\"time_barrier_s\":0,\"magic\":0,\"tp1\":0.0,\"tp2\":0.0,\"sl_after_tp1\":0.0,\"sl_after_tp2\":0.0,\"action_id\":\"\",\"action_ticket\":0,\"action_sl\":0.0,\"action_tp\":0.0,\"action_tp1\":0.0,\"action_tp2\":0.0,\"action_sl1\":0.0,\"action_sl2\":0.0,\"action_price\":0.0,\"action_expiry_epoch\":0,\"action_barrier_s\":0,\"action_issued_epoch\":0,\"sig\":\"e59201f9d4188eaa6b7454073ea55fe88e9bfa9d4e599e77cb174617f223884a\"}",
                    0.01,
                    "v6.intent.2|1789565408|CANCEL_PENDING|0|||1||NONE|0|0|0|0|0|0|0|0|0|0|0|0|0|0|0||0|0|0|0|0|0|0|0|0|0|0",
                    "e59201f9d4188eaa6b7454073ea55fe88e9bfa9d4e599e77cb174617f223884a") && ok;
   ok = IntentRowOk("buy-limit",
                    "{\"schema_version\":\"v6.intent.2\",\"server_time_epoch\":1789565408,\"command\":\"NONE\",\"has_intent\":true,\"intent_id\":\"k7w2m4pq3xza\",\"source\":\"operator\",\"require_demo\":1,\"side\":\"buy\",\"order_type\":\"BUY_LIMIT\",\"entry\":4535.07,\"sl\":4528.07,\"tp\":4549.07,\"lots\":0.01,\"ref_price\":4535.35,\"max_drift_points\":200,\"max_spread_points\":35,\"valid_until_epoch\":1789565528,\"pending_expiry_epoch\":1789567200,\"time_barrier_s\":7200,\"magic\":250570,\"tp1\":0.0,\"tp2\":0.0,\"sl_after_tp1\":0.0,\"sl_after_tp2\":0.0,\"action_id\":\"\",\"action_ticket\":0,\"action_sl\":0.0,\"action_tp\":0.0,\"action_tp1\":0.0,\"action_tp2\":0.0,\"action_sl1\":0.0,\"action_sl2\":0.0,\"action_price\":0.0,\"action_expiry_epoch\":0,\"action_barrier_s\":0,\"action_issued_epoch\":0,\"sig\":\"4010767055eaed77f1c734cf415cf174186e1651968962802073f100fd64885d\"}",
                    0.01,
                    "v6.intent.2|1789565408|NONE|1|k7w2m4pq3xza|operator|1|buy|BUY_LIMIT|453507|452807|454907|1|453535|200|35|1789565528|1789567200|7200|250570|0|0|0|0||0|0|0|0|0|0|0|0|0|0|0",
                    "4010767055eaed77f1c734cf415cf174186e1651968962802073f100fd64885d") && ok;
   ok = IntentRowOk("sell-market",
                    "{\"schema_version\":\"v6.intent.2\",\"server_time_epoch\":1789565408,\"command\":\"NONE\",\"has_intent\":true,\"intent_id\":\"b5n6r7t2vw3y\",\"source\":\"operator\",\"require_demo\":1,\"side\":\"sell\",\"order_type\":\"SELL\",\"entry\":4535.18,\"sl\":4542.18,\"tp\":4521.18,\"lots\":0.01,\"ref_price\":4535.18,\"max_drift_points\":150,\"max_spread_points\":30,\"valid_until_epoch\":1789565468,\"pending_expiry_epoch\":0,\"time_barrier_s\":7200,\"magic\":250570,\"tp1\":0.0,\"tp2\":0.0,\"sl_after_tp1\":0.0,\"sl_after_tp2\":0.0,\"action_id\":\"\",\"action_ticket\":0,\"action_sl\":0.0,\"action_tp\":0.0,\"action_tp1\":0.0,\"action_tp2\":0.0,\"action_sl1\":0.0,\"action_sl2\":0.0,\"action_price\":0.0,\"action_expiry_epoch\":0,\"action_barrier_s\":0,\"action_issued_epoch\":0,\"sig\":\"e98b026458fbb294226c616c071a324067a4b890fa1c70dcb7d4e6d8295bf68f\"}",
                    0.01,
                    "v6.intent.2|1789565408|NONE|1|b5n6r7t2vw3y|operator|1|sell|SELL|453518|454218|452118|1|453518|150|30|1789565468|0|7200|250570|0|0|0|0||0|0|0|0|0|0|0|0|0|0|0",
                    "e98b026458fbb294226c616c071a324067a4b890fa1c70dcb7d4e6d8295bf68f") && ok;
   ok = IntentRowOk("buy-stop-ladder",
                    "{\"schema_version\":\"v6.intent.2\",\"server_time_epoch\":1789565408,\"command\":\"NONE\",\"has_intent\":true,\"intent_id\":\"w3k7m2q5z4pa\",\"source\":\"operator\",\"require_demo\":1,\"side\":\"buy\",\"order_type\":\"BUY_STOP\",\"entry\":4540.0,\"sl\":4533.0,\"tp\":4554.0,\"lots\":0.02,\"ref_price\":4535.35,\"max_drift_points\":140,\"max_spread_points\":35,\"valid_until_epoch\":1789565528,\"pending_expiry_epoch\":1789567200,\"time_barrier_s\":9000,\"magic\":250570,\"tp1\":4544.0,\"tp2\":4548.0,\"sl_after_tp1\":4540.5,\"sl_after_tp2\":4544.0,\"action_id\":\"\",\"action_ticket\":0,\"action_sl\":0.0,\"action_tp\":0.0,\"action_tp1\":0.0,\"action_tp2\":0.0,\"action_sl1\":0.0,\"action_sl2\":0.0,\"action_price\":0.0,\"action_expiry_epoch\":0,\"action_barrier_s\":0,\"action_issued_epoch\":0,\"sig\":\"7056841e60b9689a950f3791f6c9224b4fec6c5bd067e1731cac5dfe23e17d10\"}",
                    0.01,
                    "v6.intent.2|1789565408|NONE|1|w3k7m2q5z4pa|operator|1|buy|BUY_STOP|454000|453300|455400|2|453535|140|35|1789565528|1789567200|9000|250570|454400|454800|454050|454400||0|0|0|0|0|0|0|0|0|0|0",
                    "7056841e60b9689a950f3791f6c9224b4fec6c5bd067e1731cac5dfe23e17d10") && ok;
   ok = IntentRowOk("close-position",
                    "{\"schema_version\":\"v6.intent.2\",\"server_time_epoch\":1789565408,\"command\":\"CLOSE_POSITION\",\"has_intent\":false,\"intent_id\":\"\",\"source\":\"\",\"require_demo\":1,\"side\":\"\",\"order_type\":\"NONE\",\"entry\":0.0,\"sl\":0.0,\"tp\":0.0,\"lots\":0.0,\"ref_price\":0.0,\"max_drift_points\":0,\"max_spread_points\":0,\"valid_until_epoch\":0,\"pending_expiry_epoch\":0,\"time_barrier_s\":0,\"magic\":0,\"tp1\":0.0,\"tp2\":0.0,\"sl_after_tp1\":0.0,\"sl_after_tp2\":0.0,\"action_id\":\"c4z7k2m5q3wp\",\"action_ticket\":5012345702,\"action_sl\":0.0,\"action_tp\":0.0,\"action_tp1\":0.0,\"action_tp2\":0.0,\"action_sl1\":0.0,\"action_sl2\":0.0,\"action_price\":0.0,\"action_expiry_epoch\":0,\"action_barrier_s\":0,\"action_issued_epoch\":1789565408,\"sig\":\"018e404184b58fe9f57434048b0f8d8e98b4828c1f171f2a531686d87c5afedb\"}",
                    0.01,
                    "v6.intent.2|1789565408|CLOSE_POSITION|0|||1||NONE|0|0|0|0|0|0|0|0|0|0|0|0|0|0|0|c4z7k2m5q3wp|5012345702|0|0|0|0|0|0|0|0|0|1789565408",
                    "018e404184b58fe9f57434048b0f8d8e98b4828c1f171f2a531686d87c5afedb") && ok;
   ok = IntentRowOk("modify-position",
                    "{\"schema_version\":\"v6.intent.2\",\"server_time_epoch\":1789565408,\"command\":\"MODIFY_POSITION\",\"has_intent\":false,\"intent_id\":\"\",\"source\":\"\",\"require_demo\":1,\"side\":\"\",\"order_type\":\"NONE\",\"entry\":0.0,\"sl\":0.0,\"tp\":0.0,\"lots\":0.0,\"ref_price\":0.0,\"max_drift_points\":0,\"max_spread_points\":0,\"valid_until_epoch\":0,\"pending_expiry_epoch\":0,\"time_barrier_s\":0,\"magic\":0,\"tp1\":0.0,\"tp2\":0.0,\"sl_after_tp1\":0.0,\"sl_after_tp2\":0.0,\"action_id\":\"m3a7q2z5k6pw\",\"action_ticket\":5012345702,\"action_sl\":4536.0,\"action_tp\":4552.0,\"action_tp1\":0.0,\"action_tp2\":4548.0,\"action_sl1\":0.0,\"action_sl2\":4544.0,\"action_price\":0.0,\"action_expiry_epoch\":0,\"action_barrier_s\":10800,\"action_issued_epoch\":1789565408,\"sig\":\"fcfb555c553e802b7bcb7e61c0e728b759838cc73d54597ed1f3c453388b4e71\"}",
                    0.01,
                    "v6.intent.2|1789565408|MODIFY_POSITION|0|||1||NONE|0|0|0|0|0|0|0|0|0|0|0|0|0|0|0|m3a7q2z5k6pw|5012345702|453600|455200|0|454800|0|454400|0|0|10800|1789565408",
                    "fcfb555c553e802b7bcb7e61c0e728b759838cc73d54597ed1f3c453388b4e71") && ok;
   ok = IntentRowOk("modify-pending",
                    "{\"schema_version\":\"v6.intent.2\",\"server_time_epoch\":1789565408,\"command\":\"MODIFY_PENDING\",\"has_intent\":false,\"intent_id\":\"\",\"source\":\"\",\"require_demo\":1,\"side\":\"\",\"order_type\":\"NONE\",\"entry\":0.0,\"sl\":0.0,\"tp\":0.0,\"lots\":0.0,\"ref_price\":0.0,\"max_drift_points\":0,\"max_spread_points\":0,\"valid_until_epoch\":0,\"pending_expiry_epoch\":0,\"time_barrier_s\":0,\"magic\":0,\"tp1\":0.0,\"tp2\":0.0,\"sl_after_tp1\":0.0,\"sl_after_tp2\":0.0,\"action_id\":\"p5q2w7m3k4za\",\"action_ticket\":5012345703,\"action_sl\":4532.0,\"action_tp\":4553.0,\"action_tp1\":4543.0,\"action_tp2\":4547.0,\"action_sl1\":4539.5,\"action_sl2\":4543.0,\"action_price\":4539.0,\"action_expiry_epoch\":1789566608,\"action_barrier_s\":9000,\"action_issued_epoch\":1789565408,\"sig\":\"52a271912f49f81facd96bc0742e6ceee34034928e7bffe3e4dc73e9af4ac6b4\"}",
                    0.01,
                    "v6.intent.2|1789565408|MODIFY_PENDING|0|||1||NONE|0|0|0|0|0|0|0|0|0|0|0|0|0|0|0|p5q2w7m3k4za|5012345703|453200|455300|454300|454700|453950|454300|453900|1789566608|9000|1789565408",
                    "52a271912f49f81facd96bc0742e6ceee34034928e7bffe3e4dc73e9af4ac6b4") && ok;
   return ok;
}

// Every vector runs even after a failure; g_selftest_failed names the first.
bool HmacSelfTest(void)
{
   g_selftest_failed = "";
   bool ok = HmacVectorsOk();
   ok = RequestVectorsOk() && ok;
   ok = IntentVectorsOk() && ok;
   ok = FingerprintOk() && ok;
   ok = IntentTamperRejected() && ok;
   ok = IntegerReaderStrict() && ok;
   return ok;
}

#endif // QLIPV6_SELFTEST_MQH
