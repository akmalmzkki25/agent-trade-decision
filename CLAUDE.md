@AGENTS.md

## Claude Code

- Your operator agent name is `claude_code`. The V6 procedure is the `v6-trading` skill in
  `.claude/skills/v6-trading/SKILL.md`, a byte-identical copy of the canonical
  `.agents/skills/v6-trading/SKILL.md`.
- Run `wait` in the foreground with the Bash tool and `timeout: 300000`, never with
  `run_in_background`, so the loop runs exactly as it does in Codex and Antigravity.
