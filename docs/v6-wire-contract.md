# V6 wire contract — EA ↔ adapter

Status: **binding** for every V6 stage (adapter routes, runtime, EA, scripts). Code of
record: `adapter/app/v6/wire.py` (signatures), `adapter/app/v6/schemas/intent.py`
(poll, intent, execution), `adapter/app/v6/schemas/snapshot.py` (snapshot, backfill),
`adapter/app/models.py` (`BasketResultEvent`). Golden vectors:
`adapter/tests/v6/golden/hmac_vectors.json`. When this document and the code disagree,
the code plus its tests win and this document is fixed.

User decisions (2026-09-16) this contract implements:

- Decisions come from `V6_BACKEND=rules` (shadow only) or `V6_BACKEND=operator` (an
  operator agent — Claude Code, Codex or Antigravity — in a chat session). There is no
  other backend.
- **The operator backend decides for DEMO accounts only. REAL and CONTEST are refused
  at config, operator API, intent builder and EA. This is never relaxable.**
- `V6_MODE=execute` requires the operator backend, `V6_EA_HMAC_KEY` and
  `V6_MAX_LOTS <= 0.01`, and publishes intents only while a daily session is active
  **and armed**. `shadow` never publishes an intent.
- One position, no layering, limit orders preferred, SL/TP at the broker, time barrier
  8 × M15 (max 4 h), no break-even, no trailing, no partial closes.

---

## 1. Transport rules (all messages)

| Rule | Value |
|---|---|
| Base URL | `http://127.0.0.1:8765` (EA input `InpAdapterBase`, allowed in MT5 WebRequest) |
| Method / type | `POST`, `Content-Type: application/json`, UTF-8, no BOM, no trailing NUL |
| Size | ≤ `MAX_REQUEST_BYTES` (262 144); the adapter answers 413 above it |
| Browser guards | `Origin` / `Sec-Fetch-Site` that look cross-site → 403 (the EA sends neither) |
| Times | UTC epoch **seconds** as JSON integers (`TimeGMT()`), never server time |
| Number kinds | integers are written as integers, prices/money as decimals; strict parsing never coerces `17.0` ↔ `17` |
| Shape | flat where the EA parses (`v6.intent.1`); every field always present; no unknown fields (400) |
| V6 off | every `/v6/*` route answers **404** `{"detail": "V6 disabled"}` |
| Storage down | 503 `{"detail": "V6 storage unavailable"}` — retryable |
| Validation | 400 with a list of `{type, loc, msg}` (no input echo) — not retryable |
| `X-Internal-Sig` | V1–V5 only. `HMAC_REQUIRED` / `INTERNAL_HMAC_KEY` never apply to V6 routes |

Retry policy of the EA (`Http.mqh`): retry only transport failures and 5xx. A 4xx is
final. A **signed** request is re-signed for every attempt and the attempt waits until
`TimeGMT()` has moved to a new second (retry pause ≥ 1000 ms), because an identical
`(X-Qlip6-Ts, X-Qlip6-Sig)` pair inside the window is refused as a replay.

---

## 2. The V6 key

| Item | Contract |
|---|---|
| Name | `V6_EA_HMAC_KEY` in `adapter/.env` (pydantic `SecretStr`, alias `V6_EA_HMAC_KEY`) |
| Format | 32–256 printable ASCII characters, no whitespace (`[\x21-\x7E]`), not a placeholder (`config.ea_key_ok`) |
| Bytes | the HMAC key is the key's ASCII bytes, on both sides |
| Independence | unrelated to `INTERNAL_HMAC_KEY` / `HMAC_REQUIRED` (V5 does not sign and keeps working) |
| Required | when `V6_MODE=execute` (startup refuses otherwise). Optional in shadow |
| Generate | `python -c "import secrets; print(secrets.token_urlsafe(32))"` (43 chars) — never commit it |
| EA file | `<terminal data folder>\MQL5\Files\QlipV6\hmac.key`, EA input `InpHmacKeyFile` (default `QlipV6\hmac.key`, relative to `MQL5\Files`) |
| File format | the key's ASCII bytes, no BOM; the EA ignores trailing CR, LF, space and tab |
| Sync | `python adapter/scripts/v6_sync_ea_key.py --data-folder "<terminal data folder>"` copies the key from the environment or `adapter/.env` into that file, creates `QlipV6\`, never prints the key |
| Fingerprint | `wire.key_fingerprint(key)` = first 12 hex chars of `HMAC(key, "qlip-v6-key-fingerprint")`. The EA and the adapter may log it so an operator can compare; nothing else about the key is ever logged, printed, stored in the DB or sent |

HMAC is HMAC-SHA256 (RFC 2104) with a 64-byte block: keys longer than 64 bytes are
hashed with SHA-256 first, shorter keys are zero-padded. MQL5 has only
`CryptEncode(CRYPT_HASH_SHA256, …)`, so the EA builds HMAC itself
(`H((K ⊕ opad) ‖ H((K ⊕ ipad) ‖ m))`, ipad `0x36`, opad `0x5c`). Signatures are
**lowercase** hex, 64 characters.

---

## 3. Request signatures (EA → adapter)

Every V6 EA route (`/v6/bars/backfill`, `/v6/snapshot`, `/v6/intent/poll`,
`/v6/execution`, `/v6/action`, `/v6/basket-result`) carries two headers:

```
X-Qlip6-Ts:  <unix seconds, decimal, no sign, no leading zeros>
X-Qlip6-Sig: <lowercase hex HMAC-SHA256(key, payload)>
payload = ts + "\n" + METHOD + "\n" + PATH + "\n" + <raw body bytes>
```

- `METHOD` is upper case (`POST`); `PATH` is the request path exactly as sent, without
  scheme, host or query (`/v6/intent/poll`). The EA never sends a query string.
- The body is signed byte for byte as sent (the same `uchar[]` passed to `WebRequest`).
- `wire.request_signing_payload`, `wire.sign_request`, `wire.verify_request`.

Adapter checks (`wire.verify_request`, in order; any failure → **401**
`{"detail": "<code>"}`):

| Code | Meaning |
|---|---|
| `SIG_NO_KEY` | the adapter has no valid key (execute cannot start without one, so this is a misconfiguration) |
| `SIG_MISSING` | a header is absent |
| `SIG_BAD_TS` | `X-Qlip6-Ts` is not canonical decimal seconds |
| `SIG_BAD_FORMAT` | `X-Qlip6-Sig` is not 64 lowercase hex characters |
| `SIG_STALE` | `|adapter now − ts| > 30 s` (`wire.REQUEST_WINDOW_S`) |
| `SIG_MISMATCH` | constant-time comparison failed |
| `SIG_REPLAY` | the same `(ts, sig)` was accepted within the window |
| `SIG_REPLAY_FULL` | replay cache (4096 pairs, `wire.ReplayCache`) is full — fail closed |

**When enforced:** `V6_MODE=execute` → required and verified on every V6 EA route.
`shadow`/`off` → the headers are ignored (the EA may still send them). Only correctly
signed requests enter the replay cache. The generic body guards (JSON content type,
cross-site refusal, size cap) run before the signature check.

---

## 4. Intent signature (adapter → EA)

`PollResponse.sig` = lowercase hex `HMAC-SHA256(key, canonical)`, where `canonical` is
the following fields joined with `|` (`wire.CANONICAL_FIELDS`, `wire.intent_canonical`):

| # | Field | Text in the canonical string |
|---|---|---|
| 1 | `schema_version` | `v6.intent.2` |
| 2 | `server_time_epoch` | decimal integer |
| 3 | `command` | `NONE` / `FLATTEN` / `CANCEL_PENDING` / `CLOSE_POSITION` / `MODIFY_POSITION` / `MODIFY_PENDING` |
| 4 | `has_intent` | `1` or `0` |
| 5 | `intent_id` | 12 chars or empty |
| 6 | `source` | `rules` / `operator` or empty |
| 7 | `require_demo` | always `1` |
| 8 | `side` | `buy` / `sell` or empty |
| 9 | `order_type` | `BUY_LIMIT` / `SELL_LIMIT` / `BUY_STOP` / `SELL_STOP` / `BUY` / `SELL` / `NONE` |
| 10 | `entry_points` | `round(entry / point)` as an integer |
| 11 | `sl_points` | `round(sl / point)` |
| 12 | `tp_points` | `round(tp / point)` |
| 13 | `lots_hundredths` | `round(lots × 100)` as an integer |
| 14 | `ref_points` | `round(ref_price / point)` |
| 15 | `max_drift_points` | integer |
| 16 | `max_spread_points` | integer |
| 17 | `valid_until_epoch` | integer |
| 18 | `pending_expiry_epoch` | integer |
| 19 | `time_barrier_s` | integer |
| 20 | `magic` | integer |
| 21 | `tp1_points` | `round(tp1 / point)`; `0` without a ladder |
| 22 | `tp2_points` | `round(tp2 / point)`; `0` without a ladder |
| 23 | `sl_after_tp1_points` | `round(sl_after_tp1 / point)`; `0` for no step |
| 24 | `sl_after_tp2_points` | `round(sl_after_tp2 / point)`; `0` for no step |
| 25 | `action_id` | 12 chars or empty (management commands only) |
| 26 | `action_ticket` | integer; `0` without an action |
| 27 | `action_sl_points` | `round(action_sl / point)` |
| 28 | `action_tp_points` | `round(action_tp / point)` |
| 29 | `action_tp1_points` | `round(action_tp1 / point)` |
| 30 | `action_tp2_points` | `round(action_tp2 / point)` |
| 31 | `action_sl1_points` | `round(action_sl1 / point)` |
| 32 | `action_sl2_points` | `round(action_sl2 / point)` |
| 33 | `action_price_points` | `round(action_price / point)` (pending price) |
| 34 | `action_expiry_epoch` | integer |
| 35 | `action_barrier_s` | integer, the total holding time from the fill |
| 36 | `action_issued_epoch` | integer; the EA refuses an action older than 30 s |

- `point` is the symbol's `SYMBOL_POINT` (XAUUSD: 0.01). The adapter uses the newest
  snapshot's `symbol_spec.point` (`CarryOver.point`, default 0.01); the EA uses
  `SymbolInfoDouble(_Symbol, SYMBOL_POINT)`. A mismatch fails verification, which is
  safe.
- Prices are already on the tick grid and lots on the 0.01 grid; the adapter refuses
  to sign anything else (`wire.price_to_points`, `wire.lots_to_hundredths`). The EA
  computes `(long)MathRound(value / point)` and `(long)MathRound(lots * 100)` from the
  **parsed** numbers — no float text is ever signed.
- Zero-valued fields (idle response) contribute `0` or an empty string. Example (idle):
  `v6.intent.1|1789565408|NONE|0|||1||NONE|0|0|0|0|0|0|0|0|0|0|0`.
- Buy-limit example: `v6.intent.1|1789565408|NONE|1|k7w2m4pq3xza|operator|1|buy|BUY_LIMIT|453507|452807|454907|1|453535|200|35|1789565528|1789567200|7200|250570`.
- **Every** poll response is signed when a key is configured (the command is covered
  too). `sig` is `""` only when no key is configured (shadow without a key).
  `wire.sign_intent(key, response, point)` returns the signed copy;
  `wire.verify_intent` does what the EA does.

---

## 5. Golden vectors and the EA self-test

`adapter/tests/v6/golden/hmac_vectors.json` (schema `v6.hmac.vectors.1`):

- `hmac`: RFC 4231 test cases 1, 2 and 6 (`key_hex`, `data_hex`, `sig`; TC6 has a
  131-byte key and exercises the pre-hash path).
- `requests`: signed EA requests (`ts`, `method`, `path`, `body`,
  `signing_payload_hex`, `sig`) under `test_key`.
- `intents`: poll responses (`response`, `point`, `canonical`, `sig`) under `test_key`:
  idle, CANCEL_PENDING, a BUY_LIMIT and a SELL market intent.
- `test_key`, `fingerprint_label`, `test_key_fingerprint`.

`test_key` is public and never valid for production use. The EA embeds these vectors
in an `OnInit` self-test (HMAC on all `hmac` rows, request signatures, canonical
strings built from the parsed `response` objects and their signatures). **If any
vector fails, the EA refuses to execute** (it may stay in data-only mode and logs the
failing vector name). `tests/v6/test_wire_golden.py` recomputes every value with
`app.v6.wire`; regenerate the file only together with the EA self-test.

---

## 6. Messages

### 6.1 `POST /v6/bars/backfill` — `v6.backfill.1`

Sent at EA start, chunked (≤ 2 500 rows per request), timeout 3 000 ms.

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `"v6.backfill.1"` | |
| `symbol` | string | `^[A-Za-z0-9._#+-]{3,20}$` |
| `tf` | `M1`/`M5`/`M15`/`H1`/`D1` | |
| `sent_at_epoch` | int ≥ 0 | |
| `rows` | `[[t, o, h, l, c, tick_volume, spread_points], …]` | ≤ 3 000, closed bars only, `t` = bar **open** UTC, strictly increasing, M1/M5/M15 on grid, `h ≥ max(o,c)`, `l ≤ min(o,c)` |

Response 200 `{"accepted": <rows written>}`.

### 6.2 `POST /v6/snapshot` — `v6.snapshot.1`

One per closed M15 bar, timeout 1 500 ms, retried for `InpSnapshotRetryS`. Blocks:
`account` (login, trade_mode, server, currency, leverage, balance, equity, margin,
free_margin, margin_level), `symbol_spec` (… `calc_profit_per_price`,
`calc_loss_per_price` from `OrderCalcProfit` — sizing uses these because the server
reports `tick_value` 0.1 on XAUUSD), `quote`, `bars` (M1×12, M5×48, M15×16, H1×8, D1×3,
closed only), `ticks`, `positions` and `pending_orders` (V6 magic only, comment
`Q6:<intent_id>`), `day`, `calendar`, `probe` (first snapshot of an EA session, else
`null`), `ea_state` (`ea_version`, `execute_enabled`, `halted`, `local_breaker`
`none|daily`, `outbox_pending`, `last_intent_id`). Field list: `schemas/snapshot.py`;
EA ↔ schema sync: `tests/v6/test_golden_contract.py`.

`snapshot_id` = `Q6S-<login>-<bar_open_epoch>`. Responses: 202
`{"accepted": true, "cycle_id", "server_time_epoch"}`; a repeat is 200
`{"accepted": false, "duplicate": true, "cycle_id"}`.

### 6.3 `POST /v6/intent/poll` — `v6.poll.1` → `v6.intent.1`

Every `InpPollIntervalMs` (2 000 ms), timeout 600 ms. Request fields: `login`,
`trade_mode` (`DEMO|CONTEST|REAL`; anything not demo/contest is reported as REAL),
`server`, `sent_at_epoch`, `balance`, `equity`, `free_margin`, `bid`, `ask`,
`spread_points`, `open_v6_positions`, `pending_v6_orders`, `floating_pnl_v6`,
`last_intent_id` (last intent the EA processed, `""` if none), `local_halt`.

Response `PollResponse` (always 200, always every field):

| Field | Idle value | With an intent |
|---|---|---|
| `schema_version` | `v6.intent.2` | same |
| `server_time_epoch` | adapter clock | same |
| `command` | `NONE`/`FLATTEN`/`CANCEL_PENDING`/`CLOSE_POSITION`/`MODIFY_POSITION`/`MODIFY_PENDING` | always `NONE` |
| `has_intent` | `false` | `true` |
| `intent_id` | `""` | `^[a-z2-7]{12}$` (`schemas.intent.new_intent_id`) |
| `source` | `""` | `operator` (execute mode requires the operator backend) |
| `require_demo` | `1` | `1` — the adapter can never send anything else |
| `side` | `""` | `buy`/`sell` |
| `order_type` | `NONE` | `BUY_LIMIT`/`SELL_LIMIT` (preferred), `BUY_STOP`/`SELL_STOP` or `BUY`/`SELL`, matching `side` |
| `entry`, `sl`, `tp` | `0.0` | > 0, on the tick grid; `sl`/`tp` on their own side of `entry` |
| `lots` | `0.0` | `0 < lots ≤ 0.01` (0.01 grid) |
| `ref_price` | `0.0` | side price at decision time (ask for buy, bid for sell) |
| `max_drift_points` | `0` | 20 % of the stop distance in points, within [10, `V6_MAX_DRIFT_POINTS`] (default cap 200); a market order may get less, so a fill at `ref_price` ± drift still fits the risk budget |
| `max_spread_points` | `0` | effective spread gate (35 standard / 20 raw) |
| `valid_until_epoch` | `0` | EA must not act at or after it: `published_at + V6_INTENT_TTL_S` (limit orders: at most `pending_expiry − 60`); > `server_time_epoch` |
| `pending_expiry_epoch` | `0` | limit: decision bar close + `V6_PENDING_EXPIRY_BARS`×900, and ≥ `valid_until_epoch + 60`; market: `0` |
| `time_barrier_s` | `0` | `V6_TIME_BARRIER_BARS`×900 (default 7 200, max 14 400) |
| `magic` | `0` | `V6_MAGIC` (250570..250579; must equal the EA's `InpMagic`) |
| `tp1`, `tp2` | `0.0` | the SL+ triggers of an agent plan, advancing entry → `tp1` → `tp2` → `tp`; `0.0` when the plan has no ladder |
| `sl_after_tp1`, `sl_after_tp2` | `0.0` | the stop the EA moves to when that trigger is reached, between the stop before it and its trigger; `0.0` for no step |
| `action_id` | `""` | management commands only: `^[a-z2-7]{12}$`, applied once by the EA |
| `action_ticket` | `0` | the V6 ticket the command acts on |
| `action_sl`, `action_tp` | `0.0` | `MODIFY_POSITION` and `MODIFY_PENDING`: the full values after the change |
| `action_tp1`, `action_tp2`, `action_sl1`, `action_sl2` | `0.0` | the ladder after the change (`0.0` = no level); steps already executed are ignored by the EA |
| `action_price` | `0.0` | `MODIFY_PENDING`: the new order price |
| `action_expiry_epoch` | `0` | `MODIFY_PENDING`: the new expiry (UTC) |
| `action_barrier_s` | `0` | the total holding time from the fill (≤ 14 400) |
| `action_issued_epoch` | `0` | when the adapter issued the action; the EA refuses one older than 30 s |
| `sig` | see §4 | see §4 |

`PollResponse` enforces these rules itself (`schemas/intent.py`).

Serving rules (adapter):
- An intent is served only in `execute` mode, for an active **armed** session, for a
  DEMO poll that passes the account policy (`risk.policy`), while the intent is
  `PUBLISHED` or `DELIVERED` and `now < valid_until_epoch`. The first response that
  carries it moves it to `DELIVERED` (write first, then answer; if the write fails,
  answer idle). Re-delivery of a `DELIVERED` intent is allowed until the EA reports.
- `command`: `CANCEL_PENDING` after a session stop or any disarm, and on every poll
  while halted or a breaker is tripped; `FLATTEN` once when a drawdown breaker newly
  trips (plan §6: HALTED + FLATTEN + CANCEL_PENDING). `FLATTEN` outranks
  `CANCEL_PENDING` and is never replaced by it. A queued command is repeated until a
  poll after its first delivery shows it done (`pending_v6_orders == 0`; for `FLATTEN`
  also `open_v6_positions == 0`) or its TTL (600 s) passes
  (`runtime/commands.py`, `runtime/poll_reply.py`). A session stop never flattens.

### 6.4 `POST /v6/execution` — `v6.execution.1`

What the EA did with one intent. Timeout 1 000 ms; queued in the EA outbox
(`MQL5\Files\QlipV6\outbox.jsonl`) and resent until a 2xx or a 4xx.

| Field | Type | Notes |
|---|---|---|
| `intent_id` | `^[a-z2-7]{12}$` | |
| `status` | `placed`, `filled`, `rejected_local`, `expired`, `cancelled`, `failed`, `dry_run` | |
| `reason_code` | see §8 | `NONE` exactly for `placed`/`filled` |
| `ticket` | int ≥ 0 | order ticket (`placed`), position/deal ticket (`filled`); > 0 for both |
| `retcode` | int ≥ 0 | `MqlTradeResult.retcode` (0 when no request was sent) |
| `requested_price`, `fill_price` | ≥ 0 | `0.0` when not applicable |
| `slippage_points` | number | signed, adverse positive |
| `spread_points` | int ≥ 0 | at send time |
| `latency_ms` | int ≥ 0 | poll receipt → broker answer |
| `sent_at_epoch` | int ≥ 0 | when the report was **built**; resends keep it |

Rules: `placed`/`filled` need `reason_code=NONE` and `ticket>0`; `rejected_local`,
`failed` and `dry_run` need a reason. The adapter stores a report once per
`(intent_id, status, sent_at_epoch)` (a resend is `{"ok": true}` and changes nothing)
and moves the intent (§7). A report for an unknown `intent_id` is stored and logged,
never applied. Response 200 `{"ok": true}`.

### 6.5 `POST /v6/action` — `v6.action.1`

What the EA did with a management command (`CLOSE_POSITION`, `MODIFY_POSITION`,
`MODIFY_PENDING`), or an SL+ step it took by itself. Queued in the outbox and resent
until the adapter answers, like an execution report.

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `"v6.action.1"` | |
| `kind` | enum | `APPLIED`, `REJECTED`, `FAILED` (broker) or `PLAN_STEP` |
| `action_id` | string | the command's id; empty for `PLAN_STEP` |
| `command` | enum | the command applied; `NONE` for `PLAN_STEP` |
| `intent_id` | string | the intent of the ticket, when the EA knows it |
| `ticket` | integer | the V6 order or position the report is about |
| `reason_code` | enum | `NONE` when applied; otherwise `UNKNOWN_TICKET`, `STALE`, `SL_WIDER`, `TOO_CLOSE`, `BARRIER`, `DEMO_REQUIRED`, `HALTED`, `MARKET_CLOSED`, `BROKER_ERROR`, `BAD_ACTION` |
| `retcode` | integer | the broker return code (0 when there was no trade request) |
| `step` | integer | `1` or `2` for `PLAN_STEP`, else `0` |
| `old_sl`, `new_sl` | number | the stop before and after the step |
| `price` | number | the price that triggered the step, or the order price |
| `sent_at_epoch` | integer | when the report was built (UTC) |

The adapter records the report in `v6_actions` (or `v6_plan_steps`), stores the new
ladder on `APPLIED`, and stops repeating the command on the next poll.

### 6.6 `POST /v6/basket-result` — `basket-result-event.v1`, `version: "v6"`

Sent by the EA when a V6 position is closed (queued and resent like §6.4). Same body
as `/v1/events/basket-result` (`app/models.py:BasketResultEvent`), which V2–V5 keep
using unsigned; V6 uses its own signed route and only `version: "v6"` is accepted there
(`schemas/basket.py`: strict, no unknown fields). `/v1/events/basket-result` refuses
`version: "v6"` with 400, so V6 P&L can only arrive signed.

| Field | V6 value |
|---|---|
| `basket_id` | `<symbol>-V6B-<intent_id>` (`schemas.intent.basket_id_for`) |
| `version` | `"v6"` |
| `symbol`, `side` | position symbol, `buy`/`sell` |
| `opened_at_utc`, `closed_at_utc` | ISO 8601 **UTC**, `YYYY-MM-DDTHH:MM:SSZ` (true UTC, not server time) |
| `close_reason` | `TP`, `SL`, `TIME`, `FLATTEN`, `ROLLOVER`, `MANUAL`, `OTHER` |
| `bursts`, `positions` | `1`, `1` |
| `gross_profit` ≥ 0, `gross_loss` ≤ 0, `net_pnl` | account currency; `net_pnl` includes swap, commission and fees |
| `max_floating_dd` ≤ 0 | worst floating P&L (MAE in money) |
| `avg_slippage_points` ≥ 0, `avg_spread_points` ≥ 0 | entry and exit |
| `decision_latency_ms` | bar close → order sent |
| `equity_at_open`, `equity_at_close` | account equity |

The adapter writes `basket_results` once per `basket_id` (a resend keeps the first
row; `ledger_baskets.py`, so `metrics(version="v6")` works) and moves the intent
`FILLED → CLOSED` with `outcome_pnl=net_pnl` and `basket_id`. The result feeds the
outcome journal and the realised V6 P&L the breakers read (`runtime/realised_pnl.py`).
A result naming no known intent is stored unlinked. Response 200 `{"ok": true}`.

---

## 7. Intent lifecycle (`runtime/intent_states.py`, table `v6_intents`)

```
PUBLISHED ──poll──► DELIVERED ──placed──► REPORTED ──filled──► FILLED ──basket result──► CLOSED
   │                    │                     │
   ├► EXPIRED           ├► FILLED (market)    ├► EXPIRED   (pending expired at the broker)
   ├► CANCELLED         ├► REJECTED           ├► CANCELLED (CANCEL_PENDING, halt, session stop)
   └► REJECTED          ├► EXPIRED            └► REJECTED  (broker refused the activation)
                        └► CANCELLED
```

| Report `status` | Target |
|---|---|
| `placed` | `REPORTED` |
| `filled` | `FILLED` |
| `rejected_local`, `failed`, `dry_run` | `REJECTED` |
| `expired` | `EXPIRED` |
| `cancelled` | `CANCELLED` |

- Terminal: `CLOSED`, `EXPIRED`, `CANCELLED`, `REJECTED`. Active: the other four.
- At most **one** active intent (unique partial index): no layering, one position.
- A move to the current status is a no-op; any unlisted move raises
  `IllegalIntentTransition`. `delivered_at` is set on the first delivery,
  `reported_at` on the first EA report, `closed_at` on reaching a terminal status.
- The watchdog expires `PUBLISHED` intents after `valid_until_epoch`. The snapshot
  reconciliation (`runtime/reconcile.py`, on every snapshot and at startup) expires
  `DELIVERED`/`REPORTED` ones only after `pending_expiry_epoch` (market: `valid_until`)
  plus a grace period **and** when the newest snapshot shows neither a pending order nor
  a position with comment `Q6:<intent_id>`; a position found that way is reconciled to
  `FILLED` (ticket from the snapshot) instead. While the snapshot's
  `ea_state.outbox_pending` is above 0, these guesses (and the `FILLED` →
  `CLOSED_UNREPORTED` one) wait for the queued reports, for at most one hour
  (`OUTBOX_HOLD_MAX_S`).
- Session stop / halt / breaker / any other disarm: `PUBLISHED` → `CANCELLED` (reason
  `SESSION_STOP`, `HALTED`, `BREAKER`, or the disarm reason such as `EA_STALE` or
  `NOT_DEMO`). The EA acts on a `DELIVERED` intent inside the reply that carried it, so
  `DELIVERED` and `REPORTED` wait for the EA's reports (a fill, or `cancelled` after
  `CANCEL_PENDING`) or the reconciliation above; `FILLED` keeps running to
  SL/TP/time barrier.
- Restart: a `PUBLISHED` intent is cancelled (`RESTART`; its EA-only fields lived in
  memory); `DELIVERED` and later ones are reconciled from the reports and snapshots.
  When the previous runtime did not stop cleanly, an armed session starts disarmed
  (`UNCLEAN_RESTART`).

Arming (`runtime/arming.py`, `runtime/desk.py`): a session start or a resume arms an
execute session only when every check passes (settings policy, active execute
session, no kill switch, breakers evaluated and clear, an EA poll younger than
`V6_EA_STALE_S`, DEMO in the newest poll and snapshot, the operator account policy,
no EA local halt). The watchdog re-checks every 5 s and only ever disarms; each
disarm cancels undelivered intents, withdraws a pending operator packet and queues
`CANCEL_PENDING`. The poll route re-checks before serving an intent.

---

## 8. EA rules

### 8.1 Checks before acting on a response (in order)

| # | Check | On failure |
|---|---|---|
| 1 | execute build, key loaded, self-test passed; response `sig` verifies (§4) | ignore the whole response (intent **and** command); if `has_intent` and `intent_id` is well-formed, report `rejected_local`/`BAD_SIGNATURE` |
| 2 | `intent_id` not processed before (persisted set) | ignore silently — never report a re-delivery (`DUPLICATE` is reserved) |
| 3 | `InpExecute` | report `dry_run`/`EXECUTE_DISABLED` |
| 4 | `require_demo == 1` **and** `AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_DEMO` (compiled check, no input can disable it) | `rejected_local`/`DEMO_REQUIRED` (`require_demo != 1` → `BAD_INTENT`) |
| 5 | schema, `magic == InpMagic`, `order_type` matches `side`, prices > 0, lots on `SYMBOL_VOLUME_STEP`, expiry rules of §6.3 | `rejected_local`/`BAD_INTENT` |
| 6 | `sl > 0` | `rejected_local`/`NO_SL` |
| 7 | `TimeGMT() < valid_until_epoch` | `rejected_local`/`EXPIRED` |
| 8 | no local halt (`GlobalVariable QlipV6_HALT == 1`, AutoTrading off) | `rejected_local`/`HALTED` |
| 9 | local daily breaker (3 %) not tripped | `rejected_local`/`BREAKER` |
| 10 | no V6 position and no V6 pending order (magic) | `rejected_local`/`OCCUPIED` |
| 11 | `lots ≤ InpMaxLots` (default and hard cap 0.03) | `rejected_local`/`LOT_CAP` |
| 12 | current spread ≤ `max_spread_points` | `rejected_local`/`SPREAD` |
| 13 | `|side price − ref_price| / point ≤ max_drift_points` (a market order: `<`, since the rest is its deviation); a limit price still passive (`BUY_LIMIT entry < ask − stops_level·point`, `SELL_LIMIT entry > bid + stops_level·point`) | `rejected_local`/`DRIFT` |
| 14 | symbol trade mode full and session open | `rejected_local`/`MARKET_CLOSED` |
| 15 | loss at `sl` for `lots` (`OrderCalcProfit`) ≤ `InpMaxRiskUsd` (default 50.0: twice the adapter's $25 budget, so a 10× spec error cannot pass) | `rejected_local`/`RISK_CAP` |
| 16 | `OrderCheck` passes | `rejected_local`/`ORDER_CHECK` (with `retcode`) |
| 17 | `OrderSend` | `placed` (pending) / `filled` (market) / `failed`/`BROKER_ERROR` |

### 8.2 Order and position rules

- Limit orders: `ORDER_TIME_SPECIFIED`, expiration = `pending_expiry_epoch` converted to
  server time. Market orders: filling mode from `SYMBOL_FILLING_MODE` (IOC or FOK).
- `sl` and `tp` are always sent with the order (broker-side). Comment `Q6:<intent_id>`,
  magic `InpMagic`; a market order's deviation is the drift limit minus the drift the
  quote already used, and a fill beyond the drift limit is logged as a risk breach.
- The EA manages, without HTTP and before any poll: the time barrier (close when
  `TimeGMT() − fill time ≥ time_barrier_s`), flatten before the daily rollover, the
  local 3 % daily breaker, `FLATTEN` and `CANCEL_PENDING`. No break-even, no trailing,
  no partial close.
- `CANCEL_PENDING`: delete every V6 pending order; report `cancelled`/`COMMAND` per
  intent (id from the comment). `FLATTEN`: also close every V6 position (basket result
  `close_reason: FLATTEN`).
- `OnTradeTransaction` only sets a flag; reports and basket results go through the
  outbox. Adapter unreachable → no new entries; open positions keep SL/TP and the time
  barrier.
- `ea_state.execute_enabled`, `last_intent_id`, `outbox_pending`, `local_breaker` in the
  snapshot and `last_intent_id` in the poll describe the EA truthfully.

### 8.3 Execution reasons (`ExecutionReason`)

`NONE`, `EXPIRED`, `DRIFT`, `SPREAD`, `DEMO_REQUIRED`, `LOT_CAP`, `RISK_CAP`, `BREAKER`,
`HALTED`, `OCCUPIED`, `ORDER_CHECK`, `DUPLICATE` (reserved), `BAD_SIGNATURE`,
`BAD_INTENT`, `NO_SL`, `MARKET_CLOSED`, `BROKER_ERROR`, `EXECUTE_DISABLED`, `COMMAND`.

---

## 9. Operator contract (adapter ↔ operator agent, not the EA)

`adapter/app/v6/schemas/operator.py`:

- `OperatorPacket` (`v6.operator.packet.1`): `cycle_id`, `packet_hash`,
  `created_at_epoch`, `expires_at_epoch` (≤ bar close + `V6_OPERATOR_DEADLINE_S`),
  `bar_open_epoch`, `bar_close_epoch`, `mode`, `session_id`, `account {trade_mode:
  "DEMO", server, equity_band}`, `market`, `session`, `bars {M15 ≤12, H1 ≤8}` as
  `[t, o, h, l, c]`, `gates`, `calendar`, `candidates` (1–3, with `exit` and indicative
  `sizing`), `baseline_views`, `allowed`, `decision_template`. Built with
  `seal_packet(OperatorPacketBody)`; only DEMO accounts can be represented.
- `OperatorDecision` (`v6.operator.decision.1`, ≤ 64 KB): `cycle_id`, `packet_hash`,
  `agent` (`claude_code`/`codex`/`antigravity`, must be in `V6_OPERATOR_AGENTS`), `views
  {price_action, news_risk, liquidity, structure}`, `chief`, optional `rebuttal
  {candidate_id: maintain|withdraw}` (PA TAKE ids only). The strict parser is
  `parse_operator_decision(raw, packet, now=…)`; the queue validates with
  `deliberation.operator_decision.validate_decision`, which is lenient for the risk
  desks only: a missing or invalid news, liquidity or structure view is flagged and
  replaced by its rules view (plan §2 rule 2), while a bad Price Action view or Chief
  refuses the decision. Errors carry `DECISION_ERR_*`.
- Recorded as provider `operator`: `v6_agent_views.source = "operator"`,
  `v6_agent_views.model = <agent>`, `v6_intents.source = "operator"`,
  `v6_intents.agent = <agent>`.
