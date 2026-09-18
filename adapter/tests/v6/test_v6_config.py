"""V6Settings: backends, operator agents, the EA key, intent timing and execute mode."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final, get_args

import pytest
from pydantic import SecretStr, ValidationError

from app.v6 import config
from app.v6.config import V6Settings, ea_key_ok, token_ok

TOKEN: Final[str] = "operator-token-" + "t" * 40
EA_KEY: Final[str] = "ea-hmac-key-" + "e" * 40
EXAMPLE_ENV: Final[Path] = Path(__file__).resolve().parents[2] / ".env.example"
ALL_AGENTS: Final[tuple[str, ...]] = ("claude_code", "codex", "antigravity")


def settings(**overrides: Any) -> V6Settings:
    return V6Settings(_env_file=None, **overrides)


def execute(**overrides: Any) -> V6Settings:
    base = {"enabled": True, "mode": "execute", "backend": "operator",
            "operator_token": TOKEN, "ea_hmac_key": EA_KEY}
    return settings(**{**base, **overrides})


def test_defaults() -> None:
    cfg = settings()

    assert (cfg.backend, cfg.mode, cfg.operator_agents) == ("rules", "shadow", ALL_AGENTS)
    assert (cfg.intent_ttl_s, cfg.operator_deadline_s, cfg.pending_expiry_s) == (120, 180, 1800)
    assert (cfg.magic, cfg.max_drift_points, cfg.ea_signing) == (250570, 200, "off")
    assert not cfg.operator_token_ok and not cfg.ea_hmac_key_ok
    assert not hasattr(cfg, "available_backends")


def _env_names() -> set[str]:
    names = set()
    for field, info in V6Settings.model_fields.items():
        names.add(info.alias or f"V6_{field.upper()}")
    return names


def test_the_example_env_file_is_valid_and_has_no_stale_v6_keys() -> None:
    cfg = V6Settings(_env_file=str(EXAMPLE_ENV))

    assert (cfg.backend, cfg.mode, cfg.enabled) == ("rules", "shadow", False)
    assert cfg.operator_agents == ALL_AGENTS
    lines = EXAMPLE_ENV.read_text(encoding="utf-8").splitlines()
    keys = {line.lstrip("# ").split("=", 1)[0] for line in lines
            if line.lstrip("# ").startswith("V6_") and "=" in line}
    assert keys <= _env_names(), keys - _env_names()
    assert {"V6_OPERATOR_AGENTS", "V6_EA_HMAC_KEY", "V6_INTENT_TTL_S"} <= keys


@pytest.mark.parametrize("backend", ["claude_code"])
def test_the_old_operator_backend_name_explains_the_rename(backend: str) -> None:
    with pytest.raises(ValidationError, match="V6_BACKEND=operator"):
        settings(backend=backend)


@pytest.mark.parametrize("backend", ["codex", "antigravity", "openai", "", "RULES"])
def test_only_rules_and_operator_are_backends(backend: str) -> None:
    with pytest.raises(ValidationError, match="backend"):
        settings(backend=backend)


@pytest.mark.parametrize(("raw", "agents"), [
    ("claude_code", ("claude_code",)), ("codex", ("codex",)),
    ("antigravity", ("antigravity",)),
    (" codex , claude_code ", ("codex", "claude_code")),
    ("antigravity,codex,claude_code", ("antigravity", "codex", "claude_code")),
])
def test_operator_agents(raw: str, agents: tuple[str, ...]) -> None:
    assert settings(V6_OPERATOR_AGENTS=raw).operator_agents == agents


@pytest.mark.parametrize("raw", [
    "", " , ", "gpt", "codex,codex", "claude_code,cursor", "gemini", "Antigravity",
    "antigravity,antigravity", "claude_code,codex,antigravity,openrouter",
])
def test_bad_operator_agents_are_refused(raw: str) -> None:
    with pytest.raises(ValidationError, match="V6_OPERATOR_AGENTS") as caught:
        settings(V6_OPERATOR_AGENTS=raw)
    assert "claude_code, codex, antigravity" in str(caught.value)


@pytest.mark.parametrize("agent", ALL_AGENTS)
def test_every_agent_is_held_to_the_demo_only_settings(agent: str) -> None:
    cfg = execute(V6_OPERATOR_AGENTS=agent)

    assert (cfg.operator_agents, cfg.allow_real_account) == ((agent,), False)
    with pytest.raises(ValidationError, match="V6_ALLOW_REAL_ACCOUNT"):
        execute(V6_OPERATOR_AGENTS=agent, allow_real_account=True)


def test_operator_agents_load_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V6_OPERATOR_AGENTS", "codex")
    monkeypatch.setenv("V6_EA_HMAC_KEY", EA_KEY)
    monkeypatch.setenv("V6_INTENT_TTL_S", "90")

    cfg = V6Settings(_env_file=None)
    assert (cfg.operator_agents, cfg.intent_ttl_s, cfg.ea_hmac_key_ok) == (("codex",), 90, True)
    assert cfg.ea_signing == "available"
    assert EA_KEY not in repr(cfg) and EA_KEY not in cfg.model_dump_json()


def test_the_operator_backend_needs_the_token_when_enabled() -> None:
    assert settings(backend="operator").backend == "operator"
    with pytest.raises(ValidationError, match="V6_OPERATOR_TOKEN"):
        settings(enabled=True, backend="operator")
    with pytest.raises(ValidationError, match="V6_OPERATOR_TOKEN"):
        settings(enabled=True, backend="operator", operator_token="change-me-" + "x" * 30)
    assert settings(enabled=True, backend="operator", operator_token=TOKEN).operator_token_ok


def test_execute_mode_happy_path() -> None:
    cfg = execute()

    assert (cfg.ea_signing, cfg.ea_hmac_key_ok, cfg.max_lots) == ("required", True, 0.03)
    assert execute(max_lots=0.005).max_lots == 0.005


@pytest.mark.parametrize(("overrides", "message"), [
    ({"backend": "rules"}, "requires V6_BACKEND=operator"),
    ({"ea_hmac_key": ""}, "requires V6_EA_HMAC_KEY"),
    ({"max_lots": 0.031}, "V6_MAX_LOTS <= 0.03"),
    ({"max_lots": 1.0}, "V6_MAX_LOTS <= 0.03"),
    ({"ea_hmac_key": "short"}, "V6_EA_HMAC_KEY must be"),
    ({"ea_hmac_key": "has a space " + "x" * 30}, "V6_EA_HMAC_KEY must be"),
    ({"ea_hmac_key": "your-key-here-" + "x" * 30}, "V6_EA_HMAC_KEY must be"),
    ({"ea_hmac_key": "k" * 257}, "V6_EA_HMAC_KEY must be"),
])
def test_execute_mode_refusals(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message) as caught:
        execute(**overrides)
    key = overrides.get("ea_hmac_key", "")
    assert not key or key not in str(caught.value)


def test_disabled_settings_do_not_check_execute_prerequisites() -> None:
    cfg = settings(mode="execute")

    assert cfg.ea_signing == "required" and not cfg.enabled


def test_a_bad_key_is_refused_even_in_shadow_when_enabled() -> None:
    with pytest.raises(ValidationError, match="V6_EA_HMAC_KEY"):
        settings(enabled=True, ea_hmac_key="short")
    assert settings(ea_hmac_key="short").ea_signing == "off"


@pytest.mark.parametrize(("overrides", "ok"), [
    ({}, True),
    ({"pending_expiry_bars": 1, "operator_deadline_s": 720, "intent_ttl_s": 120}, True),
    ({"pending_expiry_bars": 1, "operator_deadline_s": 721, "intent_ttl_s": 120}, False),
    ({"pending_expiry_bars": 1, "operator_deadline_s": 840, "intent_ttl_s": 30}, False),
])
def test_intent_timing_must_fit_the_pending_expiry(overrides: dict[str, Any], ok: bool) -> None:
    if ok:
        settings(**overrides)
        return
    with pytest.raises(ValidationError, match="pending expiry"):
        settings(**overrides)


@pytest.mark.parametrize(("field", "value"), [
    ("intent_ttl_s", 29), ("intent_ttl_s", 301), ("magic", 250569), ("magic", 250580),
    ("max_drift_points", 9), ("max_drift_points", 1001),
])
def test_new_field_ranges(field: str, value: int) -> None:
    with pytest.raises(ValidationError, match=field):
        settings(**{field: value})


@pytest.mark.parametrize(("value", "ok"), [
    (EA_KEY, True), ("k" * 32, True), ("k" * 256, True), ("k" * 31, False), ("", False),
    ("tab\tinside" + "k" * 30, False), ("é" * 40, False), ("CHANGEME" + "k" * 30, False),
])
def test_ea_key_rule(value: str, ok: bool) -> None:
    assert ea_key_ok(SecretStr(value)) is ok


@pytest.mark.parametrize(("value", "ok"), [
    (TOKEN, True), ("t" * 31, False), ("placeholder-" + "t" * 30, False), ("", False),
])
def test_token_rule(value: str, ok: bool) -> None:
    assert token_ok(SecretStr(value)) is ok


def test_other_derived_views_and_refusals() -> None:
    cfg = settings(V6_NEWS_RSS_URLS="https://a.example/rss, ,https://b.example/rss")

    assert cfg.news_rss_urls == ("https://a.example/rss", "https://b.example/rss")
    with pytest.raises(ValidationError, match="V6_MAX_SPREAD_POINTS"):
        settings(account_type="raw", max_spread_points=21)
    with pytest.raises(ValidationError, match="V6_DEMO_SERVER_PATTERN"):
        settings(demo_server_pattern="(unclosed")


def test_only_operator_credentials_remain() -> None:
    secrets = {name for name, info in V6Settings.model_fields.items()
               if info.annotation is SecretStr}

    assert secrets == {"operator_token", "ea_hmac_key"}
    assert not {name for name in V6Settings.model_fields if "llm" in name or "budget" in name}
    assert config.OPERATOR_AGENTS == ALL_AGENTS == get_args(config.OperatorAgent)
    assert tuple(config.DEFAULT_OPERATOR_AGENTS.split(",")) == ALL_AGENTS
    assert settings(SOME_OTHER_API_KEY="ignored").backend == "rules", "unknown keys are ignored"
