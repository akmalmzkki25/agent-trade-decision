"""Claude Code, Codex and Antigravity get one identical operator harness.

The rules file (AGENTS.md, imported by CLAUDE.md), the canonical skill and its
Claude Code copy, the config's agent enum and the CLI must agree: same agent
names, same commands, same exit codes, same trigger phrases, DEMO only.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

from app.v6.config import DEFAULT_OPERATOR_AGENTS, OPERATOR_AGENTS, V6Settings

from .operator_cli_fixtures_v6 import cli

REPO: Final[Path] = Path(__file__).resolve().parents[3]
AGENTS_MD: Final[Path] = REPO / "AGENTS.md"
CLAUDE_MD: Final[Path] = REPO / "CLAUDE.md"
CANONICAL_SKILL: Final[Path] = REPO / ".agents" / "skills" / "v6-trading" / "SKILL.md"
CLAUDE_SKILL: Final[Path] = REPO / ".claude" / "skills" / "v6-trading" / "SKILL.md"
ENV_EXAMPLE: Final[Path] = REPO / "adapter" / ".env.example"
OPERATOR_DOC: Final[Path] = REPO / "docs" / "v6-operator.md"
RUNBOOK: Final[Path] = REPO / "docs" / "v6-runbook.md"
INSTRUCTION_FILES: Final[tuple[Path, ...]] = (AGENTS_MD, CANONICAL_SKILL)
ANTIGRAVITY_RULES_MAX_CHARS: Final[int] = 12_000
CODEX_PROJECT_DOC_MAX_BYTES: Final[int] = 32 * 1024
PHRASES: Final[tuple[str, ...]] = ("Mulai trading skrg", "Sudah cukup hari ini",
                                   "status trading")
DISPLAY_NAMES: Final[dict[str, str]] = {
    "claude_code": "Claude Code", "codex": "Codex", "antigravity": "Antigravity"}
AGENT_COMMANDS: Final[tuple[str, ...]] = ("preflight", "wait", "submit")
LOOP_EXIT_CODES: Final[frozenset[int]] = frozenset({0, 1, 3, 4, 5})
BLOCKING_TIMEOUT_MS: Final[str] = "300000"
OP_COMMAND: Final[re.Pattern[str]] = re.compile(r"`OP ([a-z][a-z-]*)")
EXIT_MENTION: Final[re.Pattern[str]] = re.compile(r"\b[Ee]xit (?:codes? )?(\d)\b")
EXIT_ROW: Final[re.Pattern[str]] = re.compile(r"^\| (\d) \|", re.MULTILINE)
HEADING: Final[re.Pattern[str]] = re.compile(r"^#{2,3} (.+)$", re.MULTILINE)
AT_FILE: Final[re.Pattern[str]] = re.compile(r"@[\w./-]+\.[A-Za-z]+")


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def section(path: Path, title: str) -> str:
    """The body under the `## title` / `### title` heading, up to the next heading."""
    content = text(path)
    headings = list(HEADING.finditer(content))
    for index, heading in enumerate(headings):
        if heading.group(1).strip() == title:
            end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
            return content[heading.end():end].strip()
    raise AssertionError(f"{path.name} has no heading {title!r}")


def front_matter(path: Path) -> dict[str, str]:
    match = re.match(r"---\n(.*?)\n---\n", text(path), re.DOTALL)
    assert match is not None, path
    return dict(line.split(": ", 1) for line in match.group(1).splitlines())


def subcommands() -> dict[str, object]:
    parser = cli.build_parser()
    actions = [a for a in parser._actions if a.dest == "command"]  # noqa: SLF001
    assert len(actions) == 1
    return dict(actions[0].choices)


def cli_exit_codes() -> set[int]:
    return {value for name, value in vars(cli).items()
            if name.startswith("EXIT_") and isinstance(value, int)}


# --- files -------------------------------------------------------------------------------
def test_the_claude_code_skill_is_a_byte_identical_copy() -> None:
    assert CLAUDE_SKILL.read_bytes() == CANONICAL_SKILL.read_bytes()


def test_agents_md_fits_antigravity_and_codex() -> None:
    assert len(text(AGENTS_MD)) <= ANTIGRAVITY_RULES_MAX_CHARS
    assert len(AGENTS_MD.read_bytes()) < CODEX_PROJECT_DOC_MAX_BYTES


def test_agents_md_never_triggers_an_import() -> None:
    assert AT_FILE.search(text(AGENTS_MD)) is None


def test_claude_md_imports_agents_md_and_stays_thin() -> None:
    lines = text(CLAUDE_MD).splitlines()
    assert lines[0] == "@AGENTS.md"
    assert [line for line in lines if line.startswith("@")] == ["@AGENTS.md"]
    assert len(text(CLAUDE_MD)) < len(text(AGENTS_MD)) // 4
    assert "`claude_code`" in text(CLAUDE_MD) and "timeout: 300000" in text(CLAUDE_MD)


def test_the_skill_front_matter_is_agent_neutral() -> None:
    fields = front_matter(CANONICAL_SKILL)
    assert fields["name"] == "v6-trading"
    description = fields["description"]
    assert "DEMO only" in description
    assert all(phrase in description for phrase in PHRASES)
    assert all(name in description for name in (*DISPLAY_NAMES.values(), *OPERATOR_AGENTS))


def test_the_skill_body_starts_by_naming_the_agent() -> None:
    body = text(CANONICAL_SKILL).split("\n---\n", 1)[1]
    first_paragraph = body.strip().split("\n\n")[1]
    assert "set AGENT to your own name" in first_paragraph


# --- agent names ---------------------------------------------------------------------------
def test_the_config_the_cli_and_the_env_example_list_the_same_agents() -> None:
    assert tuple(DISPLAY_NAMES) == OPERATOR_AGENTS == tuple(cli.AGENTS)
    assert DEFAULT_OPERATOR_AGENTS == ",".join(OPERATOR_AGENTS)
    assert V6Settings().operator_agents == OPERATOR_AGENTS
    assert f"V6_OPERATOR_AGENTS={DEFAULT_OPERATOR_AGENTS}" in text(ENV_EXAMPLE).splitlines()


@pytest.mark.parametrize("command", AGENT_COMMANDS)
def test_every_agent_command_offers_exactly_the_config_agents(command: str) -> None:
    parser = subcommands()[command]
    agent = next(a for a in parser._actions if a.dest == "agent")  # type: ignore[attr-defined]  # noqa: SLF001
    assert tuple(agent.choices) == OPERATOR_AGENTS
    for name in OPERATOR_AGENTS:
        parser.parse_args(["--agent", name])  # type: ignore[attr-defined]


@pytest.mark.parametrize("path", [*INSTRUCTION_FILES, OPERATOR_DOC])
def test_every_agent_is_named_with_its_tool(path: Path) -> None:
    content = text(path)
    for agent, display in DISPLAY_NAMES.items():
        assert re.search(rf"\| {display} \| `{agent}` \|", content), (path.name, agent)


# --- one loop, one set of commands and exit codes ------------------------------------------
@pytest.mark.parametrize("path", INSTRUCTION_FILES)
def test_every_quoted_subcommand_exists(path: Path) -> None:
    named = set(OP_COMMAND.findall(text(path)))
    assert {"preflight", "session", "wait", "template", "submit", "status"} <= named
    assert named <= set(subcommands())


@pytest.mark.parametrize("path", INSTRUCTION_FILES)
def test_every_named_exit_code_exists(path: Path) -> None:
    content = text(path)
    named = {int(code) for code in EXIT_MENTION.findall(content)}
    loop_title = "The loop" if path == AGENTS_MD else '"Mulai trading skrg"'
    rows = {int(code) for code in EXIT_ROW.findall(section(path, loop_title))}
    assert rows == LOOP_EXIT_CODES
    assert named | rows <= cli_exit_codes()
    assert {cli.EXIT_OK, cli.EXIT_ERROR, cli.EXIT_TIMEOUT, cli.EXIT_NO_SESSION,
            cli.EXIT_NOT_DEMO} == LOOP_EXIT_CODES


def exit_table(path: Path) -> list[str]:
    return [line for line in text(path).splitlines() if EXIT_ROW.match(line)]


def test_the_wait_exit_table_is_the_same_in_both_files() -> None:
    assert exit_table(AGENTS_MD) == exit_table(CANONICAL_SKILL)
    assert len(exit_table(AGENTS_MD)) == len(LOOP_EXIT_CODES)


def test_the_blocking_command_rules_are_the_same_in_both_files() -> None:
    rules = section(CANONICAL_SKILL, "Blocking commands")
    assert section(AGENTS_MD, "Blocking commands").startswith(rules)
    assert "--timeout 240" in rules and "**foreground**" in rules
    tools = [line.split(" | ")[0][2:] for line in rules.splitlines()
             if line.startswith("| ") and not line.startswith(("| Agent ", "|---"))]
    assert tools == list(DISPLAY_NAMES.values())
    assert rules.count(BLOCKING_TIMEOUT_MS) == 2 and "never `run_in_background`" in rules


@pytest.mark.parametrize("path", [*INSTRUCTION_FILES, OPERATOR_DOC])
def test_the_trigger_phrases_and_the_demo_rule_appear(path: Path) -> None:
    content = text(path)
    assert all(phrase in content for phrase in PHRASES)
    assert "DEMO" in content and "session stop --reason not_demo" in content


def test_the_runbook_has_a_setup_appendix_for_each_tool() -> None:
    appendix = text(RUNBOOK).split("## Appendix: per-tool setup", 1)[1]
    for display in DISPLAY_NAMES.values():
        assert f"### {display}" in appendix
    assert "Not verified" in section(RUNBOOK, "Antigravity")
