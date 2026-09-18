"""The operator documents stay true: examples validate, commands parse, limits hold."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

import pytest

from app.v6.config import V6Settings
from app.v6.deliberation.operator_decision import ValidatedDecision, validate_decision
from app.v6.risk.sizing import MIN_LOT_WALL, size_position
from app.v6.schemas.operator import OperatorPacket
from app.v6.types import Refusal, SizingRequest, SymbolSpec

from .operator_cli_fixtures_v6 import cli

REPO = Path(__file__).resolve().parents[3]
OPERATOR_DOC = REPO / "docs" / "v6-operator.md"
RUNBOOK = REPO / "docs" / "v6-runbook.md"
SKILL = REPO / ".agents" / "skills" / "v6-trading" / "SKILL.md"
AGENTS = REPO / "AGENTS.md"
GITIGNORE = REPO / ".gitignore"
PHRASES = ("Mulai trading skrg", "Sudah cukup hari ini", "status trading")
CODEX_PROJECT_DOC_MAX_BYTES = 32 * 1024
XAU = SymbolSpec(digits=2, point=0.01, tick_size=0.01, tick_value=1.0, tick_value_loss=1.0,
                 contract_size=100.0, volume_min=0.01, volume_step=0.01, volume_max=100.0)


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def example(name: str) -> str:
    match = re.search(rf"<!-- example:{name} -->\s*```json\n(.*?)\n```", text(OPERATOR_DOC),
                      re.DOTALL)
    assert match is not None, name
    return match.group(1)


# --- examples ---------------------------------------------------------------------------
def test_the_documented_packet_and_decision_are_valid() -> None:
    packet = OperatorPacket.model_validate_json(example("packet"))
    raw = example("decision").encode("utf-8")
    decision = validate_decision(packet, raw, V6Settings(_env_file=None),
                                 now=packet.created_at_epoch + 60)
    assert isinstance(decision, ValidatedDecision), decision
    assert (packet.account.trade_mode, packet.state) == ("DEMO", "flat")
    assert (decision.action, decision.chief.action, decision.chief.risk_tier) == (
        "ENTER", "ENTER", "standard")
    assert decision.chief.candidate_id == packet.limits.agent_entry_id
    assert decision.plan is not None and decision.plan.order_type == "LIMIT"
    template = cli.decisions.build_template(json.loads(example("packet")))
    assert template == {**packet.decision_template.model_dump(mode="json"), "agent": None}
    assert packet.decision_template.m15_bias == packet.last_bias


def test_the_documented_agent_entry_is_sized_as_the_doc_says() -> None:
    raw = json.loads(example("decision"))
    plan, news = raw["entry_plan"], raw["views"]["news_risk"]["size_multiplier"]
    stop = round(plan["entry"] - plan["sl"], 2)
    sized = size(stop, news, max_lots=plan["lots"])
    assert (plan["lots"], sized.lots) == (0.02, 0.02)
    assert sized.risk_usd == pytest.approx(2 * (stop + 0.40))
    assert sized.risk_budget_usd == pytest.approx(25.0 * news)
    assert size(stop, news, max_lots=0.03).lots == 0.02   # 0.03 would risk $23.70 > $20
    halved = size(stop, 0.5)
    assert (halved.lots, halved.labels) == (0.01, ())      # $12.50 still pays $7.90


# --- the sizing table of section 5.7 ---------------------------------------------------------
def size(stop: float, multiplier: float, max_lots: float = 0.01) -> Any:
    request = SizingRequest(
        equity=96_896.17, balance=96_896.17, free_margin=96_000.0, price=4532.35,
        stop_distance=stop, risk_pct=0.5, size_multiplier=multiplier,
        remaining_daily_loss_usd=150.0, margin_per_lot=2266.0, friction_price=0.40, spec=XAU)
    return size_position(request, equity_basis_usd=5000.0, max_lots=max_lots,
                         notional_ratio_max=10.0)


def refused(stop: float, multiplier: float) -> bool:
    result = size(stop, multiplier)
    return isinstance(result, Refusal) and result.codes == (MIN_LOT_WALL,)


@pytest.mark.parametrize(("multiplier", "floored_from"), [(1.0, None), (0.5, 12.1), (0.25, 5.85)])
def test_the_sizing_table_rows(multiplier: float, floored_from: float | None) -> None:
    widest = size(24.6, multiplier)
    assert widest.lots == 0.01
    assert refused(24.61, multiplier)
    if floored_from is None:
        assert widest.labels == ()
    else:
        assert widest.labels == ("MIN_LOT_FLOOR",)
        assert size(max(floored_from, 6.0), multiplier).lots == 0.01


@pytest.mark.parametrize("multiplier", [0.24, 0.1])
def test_below_a_quarter_nothing_sizes(multiplier: float) -> None:
    assert refused(6.0, multiplier)


# --- commands quoted in the agent instructions -----------------------------------------------
QUOTED = re.compile(r"`OP ([^`]+)`")
PLACEHOLDERS = {"<AGENT>": "antigravity", "<you>": "codex", "<name>": "codex",
                "<why>": "drill"}


def quoted_commands(path: Path) -> list[str]:
    return QUOTED.findall(text(path))


@pytest.mark.parametrize("path", [SKILL, AGENTS])
def test_every_quoted_command_parses(path: Path) -> None:
    commands = quoted_commands(path)
    assert len(commands) >= 8
    parser = cli.build_parser()
    for command in commands:
        argv = [PLACEHOLDERS.get(part, part) for part in shlex.split(command)]
        parser.parse_args(argv)


def test_the_operator_doc_commands_parse() -> None:
    parser = cli.build_parser()
    for line in ("preflight --agent codex --allow-shadow", "session start", "session status",
                 "session stop --reason sudah_cukup", "session stop --reason not_demo",
                 "wait --agent claude_code --timeout 540", "wait --timeout 240",
                 "template --force", "submit --agent codex --file -", "halt --reason drill",
                 "resume", "status",
                 "breaker-reset --scope daily --period-key 2026-09-17"):
        parser.parse_args(shlex.split(line))


# --- agent instruction files ------------------------------------------------------------------
def test_the_skill_triggers_on_the_session_phrases() -> None:
    content = text(SKILL)
    front = re.match(r"---\n(.*?)\n---\n", content, re.DOTALL)
    assert front is not None
    fields = dict(line.split(": ", 1) for line in front.group(1).splitlines())
    assert fields["name"] == "v6-trading"
    assert all(phrase in fields["description"] for phrase in PHRASES)
    assert "DEMO only" in fields["description"]
    assert "--agent <AGENT>" in content and "timeout: 300000" in content
    assert "never `run_in_background`" in content


def test_agents_md_fits_codex_and_covers_the_loop() -> None:
    content = text(AGENTS)
    assert len(AGENTS.read_bytes()) < CODEX_PROJECT_DOC_MAX_BYTES // 2
    assert all(phrase in content for phrase in PHRASES)
    for needle in ("--agent <AGENT>", "timeout_ms: 300000", "timeout: 300000", "cmd /c",
                   "DEMO", "docs/v6-operator.md", "docs/v6-runbook.md"):
        assert needle in content, needle


def test_the_docs_name_every_problem_code_and_kill_switch() -> None:
    runbook = text(RUNBOOK)
    for needle in ("V6_MODE=execute", "V6_BACKEND=operator", "V6_OPERATOR_TOKEN",
                   "V6_EA_HMAC_KEY", "v6_sync_ea_key.py", "QlipV6_HALT", "V6_HALT",
                   "AutoTrading", "breaker-reset", "1001", "4014", "SIG_MISMATCH",
                   "APP-V6-DEMO-403", "APP-V6-WARMUP", "MIN_LOT_WALL", "powercfg", "V5",
                   "network_access = true", "trust_level", "Proceed in Sandbox",
                   "Request Review", "Always Proceed", "Not verified"):
        assert needle in runbook, needle
    operator = text(OPERATOR_DOC)
    for needle in (*PHRASES, "APP-V6-OPERATOR-TIMEOUT", "DECISION_EXPIRED", "pending"):
        assert needle in operator, needle


def test_the_operator_state_folder_is_ignored() -> None:
    assert "adapter/.v6_operator/" in text(GITIGNORE).splitlines()
