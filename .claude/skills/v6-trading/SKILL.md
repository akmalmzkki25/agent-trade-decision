---
name: v6-trading
description: Qlip V6 XAUUSD operator loop on a DEMO MetaTrader 5 account, one identical procedure for Claude Code (agent claude_code), Codex (agent codex) and Antigravity (agent antigravity). Use when the user says "Mulai trading skrg" (check preflight, start the daily session, then decide every M15 packet), "Sudah cukup hari ini" (stop the session and report the day) or "status trading" (report status only). DEMO only, never for REAL or CONTEST accounts.
---

# V6 trading operator

**First, set AGENT to your own name**, and write that name wherever `<AGENT>` appears:

| You are | AGENT |
|---|---|
| Claude Code | `claude_code` |
| Codex | `codex` |
| Antigravity | `antigravity` |

Everything below is the same for the three agents, except the table on blocking
commands. The binding rules are in `docs/v6-operator.md`: the loop (section 3), timing
(section 4), the rubric (section 5), refusals (section 7) and what not to do
(section 8). Read section 5 once per session, before the first decision. Setup and
troubleshooting are in `docs/v6-runbook.md`.

**DEMO only, for every agent, always.** Exit code 5, `NOT_DEMO`, `APP-V6-DEMO-403`,
`APP-V6-SESSION-NOT-DEMO` or a `trade_mode` other than `DEMO` means: stop at once, run
`OP session stop --reason not_demo` if a session is active, and tell the user that the
operator decides for DEMO accounts only. Never look for a workaround.

## Commands

Run every command from the repository root, the folder that holds `AGENTS.md`. `OP`
stands for:

```text
.venv/Scripts/python.exe adapter/scripts/v6_operator.py
```

This form works in PowerShell and in Git Bash. Under `cmd /c`, use backslashes:
`cmd /c .venv\Scripts\python.exe adapter\scripts\v6_operator.py`, then the arguments.
The CLI reads the token itself: never print it and never open `adapter/.env`.

## Blocking commands

`OP wait --agent <AGENT> --timeout 240` blocks until a packet arrives, for at most
240 s (285 s if the adapter hangs). Run it in the **foreground** and wait until it
exits. Never run it in the background, and never run two at once. Run every other `OP`
command the same way. This is the only step that differs between the agents:

| Agent | How to run a blocking command of up to 5 minutes |
|---|---|
| Claude Code | Bash tool in the foreground with `timeout: 300000`, never `run_in_background`. The default timeout (120000 ms) would kill `wait`. |
| Codex | One-shot shell tool: pass `timeout_ms: 300000`; the default (10000 ms) kills `wait` after 10 s. Unified exec (`exec_command` with `write_stdin`): let the call yield, then poll the same session with empty `write_stdin` calls until the command exits. |
| Antigravity | Run it in the agent terminal and wait for it to finish before doing anything else. On Windows, if the terminal still shows "Running" after the command has exited, run it through `cmd /c` with backslashes in the paths (a community fix, not official). |

## "Mulai trading skrg"

1. **Preflight.** Run `OP preflight --agent <AGENT>`.
   - Exit 5: refuse (DEMO only) and stop.
   - Exit 1 or 3: report `problems` and `hints`, then stop. Do not fix settings, start
     the adapter or touch MT5 unless the user asks.
   - Exit 0: continue.
2. **Start.** Run `OP session start` and report `session_id`, `mode` and `armed` in one
   line. Trading starts at once on the DEMO account; there is no shadow period.
   - A `refusal`: report it and stop.
   - `mode` is `execute` but `armed` is false: report `arm_reason` and stop, because no
     order could be placed.
3. **Rubric.** Read `docs/v6-operator.md` section 5, once per session.
4. **Loop.** Run `OP wait --agent <AGENT> --timeout 240` as a blocking command, act on
   its exit code, and repeat until the session ends:

| `wait` exit | Meaning | Do |
|---|---|---|
| 0 | a packet arrived | decide it (see "Deciding a packet" in the skill), then run `wait` again |
| 3 | timeout, no packet yet (normal) | run `wait` again; mention `runtime_status` once if it is `HALTED`, `BREAKER` or `STALE` |
| 4 | no active session | stop the loop and report |
| 5 | the account is not DEMO | run `OP session stop --reason not_demo`, refuse, stop |
| 1 | error | run `OP status` and report; retry once after a transient failure, otherwise stop and ask the user |

### Deciding a packet

Aim to submit within 2 minutes; the hard limit is `expires_at_epoch`. A packet that
arrives while you are busy stays open until its deadline, and the next `wait` gets it.

- If the summary says `mode execute` and `armed no`, the adapter disarmed the session
  (halt, breaker, stale EA, not DEMO, restart after a crash): do not decide. Run
  `OP session status`, report `disarm_reason` and stop the loop. The user re-arms by
  saying "Mulai trading skrg" again.
- Otherwise:
  1. Read the printed summary. Read `adapter/.v6_operator/packet.json` when you need
     bars, gates, features or the allowed enums.
  2. Run `OP template`.
  3. Decide with the rubric and write the full decision to
     `adapter/.v6_operator/decision.json` with your file tool. Keep `cycle_id`,
     `packet_hash` and `schema_version`; use only ids and enum values from `allowed`;
     leave `agent` as `null` (`submit` fills it in).
  4. Run `OP submit --agent <AGENT>`.
  5. Report one line:
     - exit 0 (accepted): the cycle, `chief_action` and `result.status`, with
       `hold_reason`, or "pending" when `result` is null;
     - exit 1 with `code` `EXPIRED`: the packet is closed (expired, or withdrawn by a
       stop, halt or disarm). Do not resubmit; run `wait` (it exits 4 if the session
       ended);
     - exit 1 with another `code`: report `code` and `error`. The packet stays open, so
       fix the decision and submit again while time remains;
     - exit 4 (the packet is closed and the session has ended) or 5: act as the table
       above says for `wait`.
  6. Run `wait` again (step 4).

## "Sudah cukup hari ini"

1. Start no new `wait`. A `wait` that is still running ends with exit 4 about 30 s
   after the stop, unless the user interrupts it first.
2. Run `OP session stop --reason sudah_cukup`.
3. Report the day from its output:
   - `cycles` and `hold_reasons` (grouped);
   - `intents`, and `intent_statuses` (fills) when present;
   - `open_v6_positions`, `pending_v6_orders` and `floating_pnl_v6`.
4. Say clearly that pending V6 orders are cancelled and open positions keep running to
   SL, TP or the time barrier. Do not flatten or halt.

## "status trading"

Run `OP session status` and `OP status`, then report:
- the session: active, armed, id;
- cycles, holds and intents today;
- open positions and pending orders;
- the runtime status, the EA last-seen age and `trade_mode`.

Start nothing.

## During a session, do not

- edit any file other than `adapter/.v6_operator/decision.json`;
- browse or search the web to inform a decision (the packet is the only input);
- change settings, restart services, place orders in MT5, or run `OP resume`, `OP halt`
  or a breaker reset unless the user explicitly asks;
- spawn subagents or start a second loop: the deadline is too short;
- write long replies. If the user writes during the loop, answer briefly between two
  `wait` runs. If that made a packet miss its deadline, say that the cycle timed out as
  HOLD.

Start a new chat each trading day to keep the context small.
