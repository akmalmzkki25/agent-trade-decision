# AGENTS.md: Qlip MT5 XAUUSD bots

The project rules for every coding agent. Codex reads this file natively, Antigravity
reads it as workspace rules (version 1.20.3 and later), and Claude Code loads it through
`CLAUDE.md`. Keep it under 12,000 characters (Antigravity's limit for one rules file) and
move detail into `docs/`. Never write an at sign directly before a file name here:
Claude Code and Antigravity would inline that file.

## Repository

- `ea/` holds the MetaTrader 5 Expert Advisors.
  - V1-V5 are rule-based: `QlipV1..V5_XAUUSD.mq5`, magics V1 250518, V2 250519-524,
    V3 250530-544, V4 250551-552, V5 250560-569.
  - V6 is `QlipV6_XAUUSD.mq5` plus `ea/QlipV6/*.mqh`, magic 250570-579. It sends
    snapshots, polls signed intents, executes on DEMO only and manages exits locally.
  - `ea/Scripts/QlipV6_Probe.mq5` measures the broker facts.
- `adapter/` is the FastAPI adapter on `http://127.0.0.1:8765`: Python 3.12, pydantic v2,
  SQLite, one worker.
  - `app/routes/` holds the V1-V5 plan endpoints and the V6 routes: EA data plane,
    control plane, dashboard `/v6`, operator API.
  - `app/v6/` is the V6 brain: `market/`, `setups/`, `desks/` (deterministic views),
    `risk/` (gates, sizing, exits, breakers, demo policy), `deliberation/` (protocol,
    engine), `runtime/`, `schemas/`, the ledgers and `wire.py` (HMAC contract).
  - `scripts/` holds `v6_operator.py` (operator CLI) and `v6_sync_ea_key.py` (EA key sync).
  - `tests/` is pytest; V6 tests are in `tests/v6/`.
- `knowledge/` is the evidence base V6 is built against (Indonesian, files 00-15).
  `15-implikasi-untuk-v6.md` lists the design constraints.
- `docs/` holds the V2-V5 quickstarts and, for V6:
  - `v6-operator.md`: operator rules and rubric (binding);
  - `v6-runbook.md`: setup, operations and the per-tool setup appendix;
  - `v6-wire-contract.md`: the EA contract.
- `.agents/skills/v6-trading/SKILL.md` is the operator skill (canonical copy).
  `.claude/skills/v6-trading/SKILL.md` is a byte-identical copy for Claude Code.

## Development tasks

- Run tests from `adapter/`: `..\.venv\Scripts\python.exe -m pytest -q` (V6 coverage:
  `--cov=app/v6 --cov-report=term-missing`).
- Code rules:
  - files ≤ 400 lines, functions < 50 lines, type hints everywhere;
  - frozen dataclasses or strict pydantic models;
  - `Decimal` for money and lots, UTC epoch integers;
  - parameterized SQL;
  - secrets only as `SecretStr`, compared with `hmac.compare_digest`;
  - no `print()` in app code.
- Never edit or print `adapter/.env`, and never commit secrets. Do not commit unless the
  user asks.
- After any `.mq5`/`.mqh` edit, copy the files into the terminal's `MQL5\Experts` folder
  and compile with MetaEditor, as described in `docs/v6-runbook.md` section 2.
- Edit the operator skill in `.agents/skills/`, then copy it over the Claude Code copy.
  `tests/v6/test_agent_harness_parity.py` checks that the copies match, that this file
  stays under 12,000 characters, and that agent names, commands and exit codes agree
  with the CLI.

## V6 operator: Claude Code, Codex and Antigravity

V6 decisions come from `V6_BACKEND=rules` (shadow only) or `V6_BACKEND=operator`: an
operator agent in a chat session submits one decision per M15 packet. Claude Code, Codex
and Antigravity follow one identical procedure. Only the agent name and the way a tool
runs a blocking command differ.

**Hard rule, never relaxable: the operator backend trades DEMO accounts only, for every
agent. REAL and CONTEST accounts are refused by the config, the operator API, the intent
builder and the EA.** Exit code 5, `NOT_DEMO`, `APP-V6-DEMO-403` or a `trade_mode` other
than `DEMO` means: stop, run `OP session stop --reason not_demo` if a session is active,
and tell the user. Never edit `V6_ALLOW_REAL_ACCOUNT` and never look for a workaround.

| Agent | AGENT | Skill file it loads |
|---|---|---|
| Claude Code | `claude_code` | `.claude/skills/v6-trading/SKILL.md` |
| Codex | `codex` | `.agents/skills/v6-trading/SKILL.md` |
| Antigravity | `antigravity` | `.agents/skills/v6-trading/SKILL.md` |

### Trigger phrases

- **"Mulai trading skrg"**: run preflight, start the daily session and trade at once on
  the DEMO account (no shadow period), then decide every M15 packet.
- **"Sudah cukup hari ini"**: stop the session (disarm, cancel pending V6 orders, leave
  open positions to SL, TP or the time barrier) and report the day.
- **"status trading"**: report the status only.

On any of them, read your skill file in full and follow it exactly. Sections 3 to 8 of
`docs/v6-operator.md` are binding: the loop, timing, rubric, refusals and prohibitions.

### The loop

`OP` is `.venv/Scripts/python.exe adapter/scripts/v6_operator.py`, run from the
repository root in PowerShell or Git Bash (under `cmd /c`:
`cmd /c .venv\Scripts\python.exe adapter\scripts\v6_operator.py`, then the arguments).
Write your AGENT name wherever `<AGENT>` appears. The CLI reads the token itself: never
print it and never open `adapter/.env`.

1. Run `OP preflight --agent <AGENT>`. Exit 0: continue. Exit 5: refuse and stop.
   Exit 1 or 3: report `problems` and `hints`, then stop.
2. Run `OP session start`. Stop on a `refusal`, or when `mode` is `execute` and `armed`
   is false.
3. Repeat: run `OP wait --agent <AGENT> --timeout 240` as a blocking command. A packet
   comes on every M15 bar that passes the hard gates while V6 is flat. On a packet,
   analyse the market yourself, run `OP template`, write
   `adapter/.v6_operator/decision.json` by the rubric (HOLD, a suggestion, or your own
   `entry_plan` inside the packet's `limits`, with `lots` 0.01-0.03; while a V6 order
   rests, a review packet asks `pending_action` KEEP or CANCEL), run
   `OP submit --agent <AGENT>`, then wait again.
4. On "Sudah cukup hari ini", run `OP session stop --reason sudah_cukup` and report the
   day. Open positions keep running.

| `wait` exit | Meaning | Do |
|---|---|---|
| 0 | a packet arrived | decide it (see "Deciding a packet" in the skill), then run `wait` again |
| 3 | timeout, no packet yet (normal) | run `wait` again; mention `runtime_status` once if it is `HALTED`, `BREAKER` or `STALE` |
| 4 | no active session | stop the loop and report |
| 5 | the account is not DEMO | run `OP session stop --reason not_demo`, refuse, stop |
| 1 | error | run `OP status` and report; retry once after a transient failure, otherwise stop and ask the user |

### Blocking commands

`OP wait --agent <AGENT> --timeout 240` blocks until a packet arrives, for at most
240 s (285 s if the adapter hangs). Run it in the **foreground** and wait until it
exits. Never run it in the background, and never run two at once. Run every other `OP`
command the same way. This is the only step that differs between the agents:

| Agent | How to run a blocking command of up to 5 minutes |
|---|---|
| Claude Code | Bash tool in the foreground with `timeout: 300000`, never `run_in_background`. The default timeout (120000 ms) would kill `wait`. |
| Codex | One-shot shell tool: pass `timeout_ms: 300000`; the default (10000 ms) kills `wait` after 10 s. Unified exec (`exec_command` with `write_stdin`): let the call yield, then poll the same session with empty `write_stdin` calls until the command exits. |
| Antigravity | Run it in the agent terminal and wait for it to finish before doing anything else. On Windows, if the terminal still shows "Running" after the command has exited, run it through `cmd /c` with backslashes in the paths (a community fix, not official). |

The three tools do not wake an agent alike when a background command ends (Codex has no
such event yet), so all three run the loop in the foreground.

### During a session

- Edit no file except `adapter/.v6_operator/decision.json`.
- Do not browse or search the web for trading decisions: the packet is the only input.
- Do not change settings, restart services or place orders in MT5. Do not run
  `OP resume`, `OP halt` or a breaker reset unless the user explicitly asks.
- Keep one loop: no subagents and no second `wait`. Keep replies short.

Tool setup (Claude Code permissions, Codex network and sandbox, Antigravity terminal
modes) is in the appendix of `docs/v6-runbook.md`. Start a new chat each trading day.
