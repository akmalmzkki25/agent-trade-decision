# V6 runbook: setup, daily operation, kill switches, troubleshooting

> **Ringkasan.** Isi `adapter/.env` (V6_MODE=execute, V6_BACKEND=operator, token, kunci
> EA), sinkronkan kunci ke MT5, izinkan WebRequest ke `http://127.0.0.1:8765`, pasang
> EA V6 di chart XAUUSD, **hentikan V5 di akun yang sama**, jalankan adapter, lalu
> ketik "Mulai trading skrg" di Claude Code, Codex atau Antigravity (prosedur sama).
> Berhenti: "Sudah cukup hari ini". Darurat: tombol HALT di dashboard, file `adapter/V6_HALT`, `v6_operator.py
> halt`, GlobalVariable `QlipV6_HALT=1`, atau tombol AutoTrading. Sejak EA 6.3.0 (tahap B)
> agen juga menerima paket `m1` hampir setiap menit; `submit --quick` untuk "tidak ada
> perubahan". EA harus dipasang ulang setelah dikompilasi.

Scope: one Windows machine, MT5 on a **DEMO** account (since 2026-09-17 **Monex-Demo**,
symbol `XAUUSD.m`; earlier MetaQuotes-Demo), the adapter on `127.0.0.1:8765`, and the operator agent in Claude Code, Codex or Antigravity (one
identical procedure; per-tool setup in the appendix). The operator rules are in
[v6-operator.md](v6-operator.md); the EA contract is in
[v6-wire-contract.md](v6-wire-contract.md).

Paths on this machine:
- Repository: `D:\Data Science\Qlip\ai-agent-trading`.
- Terminal data folder (MT5 → File → Open Data Folder):
  `C:\Users\Huawei\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075`.
- Broker times: server time is UTC+3 in summer and UTC+2 in winter. MT5 log timestamps
  are terminal-local.

## 1. `adapter/.env`

`adapter/.env` is gitignored: never commit it, and never paste it into a chat. Start
from `adapter/.env.example` and set:

```dotenv
V6_ENABLED=true
V6_MODE=execute                 # off | shadow | execute (execute = DEMO trading)
V6_BACKEND=operator             # rules (shadow only) | operator (an agent in a chat)
V6_OPERATOR_AGENTS=claude_code,codex,antigravity
V6_OPERATOR_TOKEN=<32+ random characters>
V6_EA_HMAC_KEY=<32-256 printable ASCII characters, no spaces>
V6_MAX_LOTS=0.03                # the agent picks 0.01-0.03; execute refuses more
V6_ACCOUNT_TYPE=standard
V6_MAGIC=250570                 # must equal the EA input InpMagic
V6_ALLOW_REAL_ACCOUNT=false     # true is refused at startup
V6_BROKER_QUOTE_GAP_UTC=20:00-22:00   # measure it per broker (section 6)
V6_ENTRY_HOURS=all_day          # phase A: 24 h entries, only cost and safety blocks
V6_SESSION_AUTO_RENEW=true      # reopen and re-arm the session after the rollover
V6_TIME_LIMIT_MIN_MINUTES=60    # the holding-time window of an agent plan
V6_TIME_LIMIT_MAX_MINUTES=240
V6_PENDING_EXPIRY_MIN_MINUTES=15   # how long a LIMIT or STOP may rest
V6_PENDING_EXPIRY_MAX_MINUTES=60
V6_MINUTE_PACKETS=true          # phase B: an m1 packet per closed M1 bar (EA 6.3.0)
V6_M1_DEADLINE_S=50             # an m1 packet expires this long after its minute (20-55)
V6_MINUTE_STALE_S=10            # a minute snapshot older than this is skipped (3-30)
```

The timing keys may only tighten:
- `V6_OPERATOR_DEADLINE_S` (180), `V6_INTENT_TTL_S` (120) and the shortest pending
  expiry must satisfy deadline + TTL + 60 ≤ `V6_PENDING_EXPIRY_MIN_MINUTES` × 60 (and ≤
  `V6_PENDING_EXPIRY_BARS` × 900).
- The risk keys keep their defaults (`V6_RISK_PCT` 0.5, at most 1.0;
  `V6_SIZING_EQUITY_BASIS_USD` 5000; the loss breakers 3/6/10 %) unless the user
  decides otherwise (the Monex demo runs `V6_RISK_PCT=1.0` since 2026-09-17).

The adapter refuses to start when:
- `execute` is set without `operator`, without a valid key, or with `V6_MAX_LOTS` above
  0.03;
- the backend is `operator` without a valid token;
- the old value `V6_BACKEND=claude_code` is used.

Generate each secret without echoing it. From `adapter/` in PowerShell, run the pair
once per secret name (`V6_OPERATOR_TOKEN`, then `V6_EA_HMAC_KEY`):

```powershell
$s = & ..\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
Add-Content -Path .env -Value "V6_OPERATOR_TOKEN=$s"; Remove-Variable s
```

Remove any older line with the same name. After a change, restart the adapter and
re-sync the key (section 2).

## 2. MT5 and the EA

1. **Sync the key.** From `adapter/`:

   ```powershell
   ..\.venv\Scripts\python.exe scripts\v6_sync_ea_key.py --terminal-data "C:\Users\Huawei\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075"
   ..\.venv\Scripts\python.exe scripts\v6_sync_ea_key.py --terminal-data "<same folder>" --check
   ```

   - The script writes `MQL5\Files\QlipV6\hmac.key` (ASCII, no newline) and prints only
     the path and a 12-character fingerprint. `--data-folder` is an alias.
   - `--check` exits 0 when the file matches.
   - Run it again whenever `V6_EA_HMAC_KEY` changes, then restart the EA (remove it from
     the chart and attach it again).
2. **Allow WebRequest.** In Tools → Options → Expert Advisors, tick "Allow algorithmic
   trading" and "Allow WebRequest for listed URL", then add `http://127.0.0.1:8765`.
   Use exactly this address; `localhost` is a different entry.
3. **Deploy after every `.mq5`/`.mqh` change.** The repository copy is not what runs.
   1. Copy `ea\QlipV6_XAUUSD.mq5` to `<data folder>\MQL5\Experts\`.
   2. Copy `ea\QlipV6\*.mqh` to `<data folder>\MQL5\Experts\QlipV6\`.
   3. Compile:

      ```powershell
      & "C:\Program Files\MetaTrader 5\MetaEditor64.exe" /compile:"<data folder>\MQL5\Experts\QlipV6_XAUUSD.mq5" /log:"$env:TEMP\qlipv6_compile.log"
      Get-Content "$env:TEMP\qlipv6_compile.log" -Encoding Unicode
      ```

   4. Require `Result: 0 errors, 0 warnings`; the exit code means nothing.
   5. Reload the EA. A command-line compile does not reload an EA that is already
      running. Remove it from the chart and attach it again (its inputs return to the
      defaults in step 4), or restart MT5, which keeps the chart's inputs.
   6. The Experts log (`MQL5\Logs`) shows `V6 EA 6.3.0 started`, and its `V6 limits:`
      line says `minute snapshots on`. EA 6.3.0 (phase B) sends one `v6.minute.1` snapshot per
      closed M1 bar to `/v6/minute` (`Cadence.mqh`), on top of phase A: LIMIT, STOP and
      market orders, the SL+ ladder (`Plan.mqh`) and signed management actions
      (`Actions.mqh`). Deploy it together with the adapter of the same commit: an older
      adapter has no `/v6/minute` route (404 on every minute) and cannot parse newer
      snapshots, and an older EA cannot parse the adapter's `v6.intent.2` answers.
4. **Attach the EA** `QlipV6_XAUUSD` to one XAUUSD chart (M15). These inputs are the
   current build's names, so check the Inputs tab:

   | Input | Value | Why |
   |---|---|---|
   | `InpAdapterBase` | `http://127.0.0.1:8765` | must be in the WebRequest list |
   | `InpExecute` | `true` | execute signed intents (the EA still requires a DEMO account) |
   | `InpMagic` | `250570` | = `V6_MAGIC` |
   | `InpMaxLots` | `0.03` | hard cap 0.03 (the agent picks 0.01-0.03) |
   | `InpMaxRiskUsd` | `50` | loss at the stop per order; twice the adapter's $25 budget |
   | `InpDailyBreakerPct` | `3` | local breaker, works when the adapter is down |
   | `InpFlattenServerTime` | `22:55` | daily flatten before rollover (server time) |
   | `InpHmacKeyFile` | `QlipV6\hmac.key` | relative to `MQL5\Files` |
   | `InpPollIntervalMs` | `2000` | poll cadence |
   | `InpMinuteSnapshots` | `true` | one minute snapshot per closed M1 bar (phase B); `false` stops the m1 packets at the source |
   | `InpMinuteTimeoutMs` | `800` | minute snapshot timeout (200-1500 ms); a minute is sent once, never retried |

   - The Experts log should show the HMAC self-test passing. If a vector fails, the EA
     refuses to execute.
   - On start the EA backfills history, and the adapter holds with `APP-V6-WARMUP` until
     `warm` is true.
5. **Stop V5 on this account.**
   - The V6 loss breakers (3 % daily, 6 % weekly, 10 % monthly) read **account** equity,
     so V5 losses would trip V6, and V5 positions consume margin.
   - Remove QlipV5 from its chart, or run V6 on a second demo account.
   - V5 does not sign requests and keeps its own magic 250560-569.

## 3. Daily operation

1. **Keep Windows awake.** Before the session, disable sleep:

   ```powershell
   powercfg /change standby-timeout-ac 0
   ```

   Or use Power settings → Screen and sleep → Never. Pause Windows Update restarts and
   keep the laptop on AC power. If the machine sleeps anyway, broker-side SL/TP still
   protect a position, but the time barrier and the daily flatten need MT5 running.
2. **Start MT5.** Check the connection (bottom right), AutoTrading on, and the EA smiling.
3. **Start the adapter** from `adapter/`, with one worker and no `--reload`:

   ```powershell
   ..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --workers 1
   ```

   The dashboard is `http://127.0.0.1:8765/v6`.
4. **Start trading.** In Claude Code, Codex or Antigravity, type **"Mulai trading
   skrg"**. The agent runs `preflight`, `session start` and the loop. To do it by hand:

   ```powershell
   ..\.venv\Scripts\python.exe scripts\v6_operator.py preflight
   ..\.venv\Scripts\python.exe scripts\v6_operator.py session start
   ```

5. **Minute packets.** While the session is armed, the agent gets an m1 packet at almost
   every closed M1 bar besides the m15 packet of each M15 bar: about 1,000-1,300 a day
   while flat (the replay of 2026-09-17 counts 1,041 of 1,274 minutes; spread, session
   and news blocks stop the rest). Most minutes are answered with `submit --quick`; the
   agent reports only actions and m15 packets, and starts a new chat when its context
   gets heavy (the session continues). The dashboard's "Minute packets" card shows the
   answered share, the actions and the adapter time per minute. To count them for other
   days: `..\.venv\Scripts\python.exe scripts\v6_replay.py --from <day> --to <day> --minutes`.
6. **Check.** **"status trading"** (or `session status` and `status`) shows the session,
   cycles, holds, intents and the EA age.
7. **Stop.** **"Sudah cukup hari ini"** (or `session stop`) disarms the session, cancels
   pending V6 orders and reports the day. **Open positions keep running** to SL, TP3,
   their SL+ steps, their time limit (60-240 min) or the daily flatten. A session left
   open is closed at the rollover block and, with `V6_SESSION_AUTO_RENEW=true`, reopened
   and re-armed as soon as the block ends; `wait` keeps waiting meanwhile.
8. **Shut down.** Stop the adapter with Ctrl+C after the session. MT5 can stay up for
   open positions.

## 4. Kill switches

Any one of these stops new entries:

| Switch | How | Effect |
|---|---|---|
| Dashboard HALT | `http://127.0.0.1:8765/v6` → HALT (no token) | creates `adapter/V6_HALT`; cycles hold `APP-V6-HALTED`; the session is disarmed, an undelivered intent is cancelled, and the EA receives `CANCEL_PENDING` on every poll |
| Halt file | `New-Item adapter\V6_HALT` | same as HALT |
| CLI | `v6_operator.py halt --reason <why>` | same as HALT, audited |
| EA global variable | MT5 → Tools → Global Variables (F3) → add `QlipV6_HALT` = 1 | the EA refuses every intent (`HALTED`), even with the adapter down |
| AutoTrading | toolbar button (Ctrl+E) | MT5 refuses all automated orders |
| Stop the adapter | Ctrl+C | no new entries and no management actions; SL/TP, the EA's SL+ steps, time barrier and flatten keep working. After a crash (no clean stop) the session comes back disarmed (`UNCLEAN_RESTART`) |

None of these closes an open position. To close one now, close it by hand in MT5 (the
EA reports it as `MANUAL`).

**Resume.** Resume only after the cause is understood.
- `v6_operator.py resume` removes the halt file and re-arms the open execute session when
  every arming check passes. It is refused while a breaker is tripped.
- Set `QlipV6_HALT` to 0 (or delete it), and switch AutoTrading back on.
- Then run `preflight`, and `session start` again if the session was stopped or is still
  disarmed (a start re-arms the open session).

**Breakers.** A new trip sends `FLATTEN` once (the EA closes V6 positions and deletes V6
pending orders), then `CANCEL_PENDING` on every poll while tripped. The session is
disarmed, every cycle holds (`APP-V6-BREAKER`), a session start is refused, and the trip
survives restarts. To reset one, only after the loss has been reviewed:

```powershell
..\.venv\Scripts\python.exe scripts\v6_operator.py breaker-reset --scope daily --period-key 2026-09-17
```

- Keys are UTC periods: `YYYY-MM-DD` (daily), `YYYY-Www` (weekly), `YYYY-MM` (monthly).
- The reset needs an EA poll from the last `V6_EA_STALE_S` seconds, and it is refused
  while that scope is still in breach.
- `RESET_NOT_EVALUATED` means no fresh poll or no day anchor. `RESET_REFUSED_CONDITION`
  means still in breach. `RESET_NOT_TRIPPED` means nothing to reset.
- The EA's local daily breaker resets at the next server day.
- After the reset, `session start` re-arms the session.

## 5. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Experts log: WebRequest `-1`, error **4014** | the URL is not in the WebRequest list | add `http://127.0.0.1:8765` (section 2) |
| WebRequest status **1001**, or error 5201 / 5202 | no connection: adapter not running, wrong port, firewall | start the adapter; `status` must answer; check `InpAdapterBase` |
| `preflight`: `ADAPTER_UNREACHABLE` (exit 3) | adapter down, or the agent's sandbox blocks localhost | start the adapter; see the appendix for your tool |
| HTTP **404** `V6 disabled` | `V6_ENABLED` is not true, or another process holds the V6 lock | fix `.env`, run a single adapter with `--workers 1` |
| HTTP **401** `invalid operator token` | CLI token ≠ adapter token | fix `V6_OPERATOR_TOKEN` (the environment overrides `.env`), restart |
| HTTP **401** `SIG_MISSING` / `SIG_MISMATCH` / `SIG_STALE` / `SIG_REPLAY` on EA routes | execute mode needs signed EA requests: key missing or different, or the clock is off by more than 30 s | re-run `v6_sync_ea_key.py` and `--check`, restart the EA, sync the Windows clock |
| EA rejects intents with `BAD_SIGNATURE` | the adapter and EA keys differ, or the EA self-test failed | same as above; compare fingerprints |
| HTTP **403** `APP-V6-DEMO-403`, `preflight` `NOT_DEMO`, CLI exit 5 | the account is not DEMO | by design; use a demo account (the operator never trades REAL or CONTEST) |
| `session start` refused `APP-V6-SESSION-NOT-DEMO` | no EA poll yet (`unknown`) or not DEMO | wait for the EA to poll; check the account |
| `session start` refused `APP-V6-SESSION-MARKET-CLOSED` | weekend or rollover block | start after the reopen |
| `status`: `armed: false` in execute mode; `session.disarm_reason` is `HALTED`, `BREAKER`, `EA_STALE`, `EA_NOT_SEEN`, `NOT_DEMO`, `ACCOUNT_POLICY`, `EA_LOCAL_HALT` or `UNCLEAN_RESTART` | the adapter disarmed the session and publishes nothing; the watchdog never re-arms | fix the cause, then `session start` (re-arms the open session); after a HALT use `resume` |
| every cycle holds `APP-V6-WARMUP`; `preflight` `WARMUP` | not enough M15/M5 history yet | let the EA backfill (restart the EA once); check `bar_coverage` in `status` |
| holds `APP-V6-SIZE`, refusal `MIN_LOT_WALL` | 0.5 % of $5,000 ($25) cannot pay a 0.01 lot at this stop (wider than about $24.60) | expected for very wide stops; see v6-operator.md §5.7 |
| holds `APP-V6-OPERATOR-TIMEOUT` | no decision before the deadline | keep the agent session focused; submit within 2 minutes |
| holds `APP-V6-STALE`, `preflight` `EA_STALE` | EA silent (terminal frozen, disconnected, sleep) | check MT5, network, sleep settings |
| `preflight` `EA_SIGNING_NOT_REQUIRED` | execute mode without required signing | check `V6_MODE` and `V6_EA_HMAC_KEY`, restart |
| EA intent rejected `DRIFT` / `SPREAD` / `EXPIRED` | a slow decision or a fast market | decide faster; these are protective refusals |
| `OCCUPIED` | a V6 position or order is already open | by design: one position, no layering; the agent manages it through management packets |
| holds `APP-V6-MANAGE-REFUSED` | the adapter could not queue a management action (session not armed, arming check failed, `ACTION_INVALID`) | read `hold_detail`; re-arm with "Mulai trading skrg" if the session was disarmed |
| a management action shows `REJECTED` or `EXPIRED` (dashboard "Plan & actions") | the EA refused it against its live quote (`SL_WIDER`, `TOO_CLOSE`, `BARRIER`, `STALE`, `UNKNOWN_TICKET`, `BAD_ACTION`), or it never reached the EA within 30 s | expected protection; the next packet shows `last_action` and the agent decides again |
| `wait` answers 3 after the rollover with no session | the adapter is reopening the session (`session_renewal_due` in `status`) | keep waiting; it reopens when the block ends and the arming checks pass |
| `wait` keeps timing out | nothing passed the gates (normal), halted, breaker, outside the main window | read `runtime_status` and `last_cycle.hold_reason` in `status` |
| no m1 packets between the M15 packets | the minute worker skips every minute: see the reason in the dashboard's "Minute packets" rows or `status` → `runtime.minutes` (`NOT_ARMED`, `M15_PENDING`, `M15_CLOSE`, `ROLLOVER`, `NO_M15_CONTEXT`, `INTENT_ACTIVE`, `GATES:<codes>`, `DISABLED`, `INACTIVE`); no rows at all means no minute snapshot arrives | an EA older than 6.3.0, or not re-attached after the compile (the log shows the version); `InpMinuteSnapshots` false; `V6_MINUTE_PACKETS=false` |
| Experts log: `minute` failure lines (at most one per 15 minutes) | the EA cannot post `/v6/minute`: adapter down, or an adapter older than phase B (404) | start the adapter of the same commit; minutes are not retried, the next one replaces a lost one |
| m1 packets answer `EXPIRED` often | the agent answers after the 50 s deadline, or an m15 packet replaced the m1 packet | answer an m1 packet with `--quick` at once unless it needs an action; keep replies short |

Where to look:
- adapter console log;
- tables `v6_actions` (management actions), `v6_plan_steps` (SL+ steps) and
  `v6_minute_cycles` (every processed minute: outcome or skip reason, `tier0_ms`; kept
  3 days);
- `http://127.0.0.1:8765/v6` (last cycles, hold histogram, sizing floor);
- `<data folder>\MQL5\Logs\YYYYMMDD.log` (EA);
- `adapter\.v6_operator\` (last packet and decision).

## 6. Drills and broker measurements

### Phase A: plans and management

Run the drills after deploying the adapter and EA (6.2.1 or later) of the same commit,
**with the user's approval**, on the demo account, one at a time. Record for each: the time, the
command or decision, the EA log lines and the `v6_actions` / `v6_plan_steps` rows.

| # | Drill | Expected |
|---|---|---|
| 1 | an entry with TP1/TP2 close (about 0.6R and 1R) | `V6 plan <ticket>: step 1 ...`, a `v6_plan_steps` row, then step 2; the stop in the terminal moves to the plan's level |
| 2 | a position packet answered `manage` CLOSE | the position closes, `v6_actions` APPLIED, basket `close_reason` `AGENT` |
| 3 | a wider stop sent to the EA (only with a temporary debug adapter the user approves; the operator API refuses it first) | `REJECTED`/`SL_WIDER`, the stop unchanged; otherwise record that `SL_WIDER` in the EA is covered by code review only |
| 4 | a resting LIMIT modified (price and stop) | the order changes in the terminal, APPLIED, the new ladder and levels on the intent |
| 5 | a BUY STOP and a SELL STOP | placed with the expiry of `pending_expiry_min` |
| 6 | a MODIFY that extends `time_limit_min` | the position is not closed at the old limit |
| 7 | the EA removed and attached again with a planned position open | the state log lists the record; the SL+ steps keep working |
| 8 | the adapter stopped with a position open | SL+ steps, SL/TP and the time limit keep working; queued reports arrive once the adapter is back |

**Broker quote hours.** `V6_BROKER_QUOTE_GAP_UTC` must cover the daily window in which
the broker sends no quotes. Two sources show it:

- the probe's `clock.trade_sessions_server`: the broker's declared session, in server
  time;
- the M1 bars the EA backfills when it starts (1,440 bars) and a read-only query on
  `v6_bars` for the nightly hole. Before EA 6.2.1, snapshots carried only the last 12
  M1 bars of each M15 bar, so bars stored from them have meaningless 3-minute holes. `v6_bars` has no broker column: after a
  broker switch, count only bars the new broker sent.

Results so far (summer time, UTC+3 server):

| Broker | Declared session (server) | No quotes (UTC) |
|---|---|---|
| MetaQuotes-Demo, measured before the switch | | 20:00-22:00 |
| Monex-Demo, measured 2026-09-18 | 01:01-23:59, Monday to Friday | 20:59-22:01 (single-tick bars at 20:59 and 22:00) |

For both brokers, the default `20:00-22:00` covers the break, together with two other
blocks:

- the EA takes no entry from its 22:55 server flatten (19:55 UTC) to server midnight;
- the rollover block (17:00-19:00 New York) blocks entries until 23:00 UTC.

Measure again after a daylight-saving change (US time changes on 2026-11-01). Only the
user edits `adapter/.env`; the adapter is then restarted.

### Phase B: minute packets

Run these after deploying the adapter and EA 6.3.0 of the same commit, **with the
user's approval**, on the demo account. Record the time, the packet (`M1 PACKET` or
`V6 PACKET`), the answer and the `v6_minute_cycles` row.

| # | Drill | Expected |
|---|---|---|
| 1 | an armed session, V6 flat, a quiet minute | an m1 packet every minute outside the M15 closes; `submit --quick` is accepted (HOLD) and the row is `ANSWERED` |
| 2 | an ENTER from an m1 packet (views and bias `null`) | the plan is checked like an m15 plan and published; the cycle records the inherited views |
| 3 | an m1 packet still open at an M15 close | the m15 packet replaces it; a submit to the m1 packet answers `EXPIRED`; no m1 packet while the m15 packet is open |
| 4 | a position open: an m1 packet answered MANAGE CLOSE or a MODIFY of the stop | the action reaches the EA like an m15 one (`v6_actions`) |
| 5 | the rollover block, and a disarmed session | no m1 packet; rows `SKIPPED` with `ROLLOVER` or `NOT_ARMED` |
| 6 | the adapter stopped for a few minutes | the EA logs a minute failure at most once per 15 minutes; the M15 snapshots are retried as before; after the restart, m1 packets resume after the first M15 cycle |

**Phase B is done when**, over one London-New York day:
- no minute backlog builds up in the adapter: p95 `tier0_ms` in `v6_minute_cycles`
  stays under 300 ms (the dashboard's "Minute packets" card);
- at least 90 % of the m1 packets are answered in time during two busy hours (the card's
  two-hour window);
- the ledger invariants hold: no APPLIED `v6_actions` row widened a stop, every active
  intent has `sl > 0`, never two active intents, no entry while a position or order was
  open, at most 8 entries per trading day.

## Appendix: per-tool setup

The three agents run one procedure (`AGENTS.md`, `.agents/skills/v6-trading/SKILL.md`).
They differ only in how they run a blocking command (the table in `AGENTS.md`) and in
the setup below. Whatever the tool, the operator trades DEMO accounts only. The agent
must be able to:
- run `.venv/Scripts/python.exe adapter/scripts/v6_operator.py ...` from the repository
  root for up to 5 minutes per call;
- reach `http://127.0.0.1:8765` from that command;
- write `adapter/.v6_operator/decision.json`.

The user makes these settings. An agent never edits its own settings or permissions.

### Claude Code

- Reads `CLAUDE.md` (which imports `AGENTS.md` with the line `@AGENTS.md`) and the skill
  in `.claude/skills/v6-trading/SKILL.md`.
- `wait` runs in the foreground Bash tool with `timeout: 300000`. The Bash tool allows up
  to 600,000 ms; its default of 120,000 ms would kill `wait`.
- Optional, to avoid a permission prompt on every command: the user adds an allow rule in
  `.claude/settings.local.json` (not committed) or through `/permissions`, for example

  ```json
  {"permissions": {"allow": [
    "Bash(.venv/Scripts/python.exe adapter/scripts/v6_operator.py:*)",
    "Edit(adapter/.v6_operator/decision.json)",
    "Write(adapter/.v6_operator/decision.json)"
  ]}}
  ```

  Keep the rule this narrow. Do not allow `adapter/.env`.

### Codex

- Reads `AGENTS.md` natively (project docs are capped at 32 KiB) and discovers skills in
  `$REPO_ROOT/.agents/skills` (https://learn.chatgpt.com/docs/build-skills).
- Blocking commands: the one-shot shell tool takes `timeout_ms: 300000`. With unified
  exec, empty `write_stdin` polls may wait up to `background_terminal_max_timeout`
  (default 300,000 ms). Codex has no event that wakes the agent when a background
  command ends (https://github.com/openai/codex/issues/32188), so the loop stays in the
  foreground.
- Network: under `sandbox_mode = "workspace-write"`, commands have no outbound network
  unless `~/.codex/config.toml` enables it, and the CLI must reach `127.0.0.1:8765`:

  ```toml
  [sandbox_workspace_write]
  network_access = true
  ```

  `danger-full-access` has no sandbox and needs nothing more, but it also removes the
  file-system limits. Reference: https://learn.chatgpt.com/codex/config-file/config-reference
  and https://learn.chatgpt.com/docs/sandboxing.
- Trust: mark the repository as trusted so Codex loads its project files:

  ```toml
  [projects.'d:\data science\qlip\ai-agent-trading']
  trust_level = "trusted"
  ```

  A trusted parent folder (for example `d:\data science\qlip`) also covers it.
- Windows: native Codex uses the Windows sandbox (`[windows] sandbox = "unelevated"` or
  `"elevated"`). Whether the unelevated sandbox lets a command reach 127.0.0.1 with
  `network_access = true` was not verified on this machine: if `preflight` reports
  `ADAPTER_UNREACHABLE` while `status` works from a normal PowerShell, the sandbox is
  the cause.

### Antigravity

- Reads `AGENTS.md` as workspace rules (version 1.20.3 and later; a rules file is limited
  to 12,000 characters, which `tests/v6/test_agent_harness_parity.py` enforces) and
  discovers skills in `.agents/skills/<name>/SKILL.md`
  (https://antigravity.google/docs/rules-workflows/,
  https://antigravity.google/docs/skills/).
- Terminal command auto-execution (https://antigravity.google/docs/agent-settings/):
  - **Request Review**: every command asks first, except those on the Allow list.
  - **Proceed in Sandbox**: commands run without asking inside the terminal sandbox;
    commands that must run outside it still ask.
  - **Always Proceed**: commands run without asking, except those on the Deny list.
- The terminal sandbox runs commands "without access to sensitive system paths or
  unauthorized networks". So either put the operator CLI command on the Allow list
  (with Request Review), or approve it to run outside the sandbox. Put `adapter/.env` out
  of reach where the tool allows it.
- Blocking commands: run `wait` in the agent terminal and wait until it exits. On
  Windows, if the terminal still shows "Running" after the command has exited, run it
  through `cmd /c .venv\Scripts\python.exe adapter\scripts\v6_operator.py ...` (a
  community fix, not official).
- **Not verified** (not documented by Google): whether the terminal sandbox blocks
  `127.0.0.1`, and how long one terminal command may run. Test once with
  `OP preflight --agent antigravity` and one `OP wait --agent antigravity --timeout 240`
  before relying on it.
