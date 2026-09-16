"""
Adapter-level secrets stay out of reprs, dumps and logs.

`INTERNAL_HMAC_KEY` authenticates every EA write (V6 included) once HMAC is on,
and `ANTHROPIC_API_KEY` is a paid credential. Both are `SecretStr`, like V6's
own secrets, so an accidental `logger.debug(settings)` or a traceback repr
cannot print them.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Callable
from typing import Final

import pytest
from pydantic import SecretStr, ValidationError

from app.security import hmac_ok
from app.settings import PLACEHOLDER_HMAC_KEY, Settings
from app.settings import settings as adapter_settings

HMAC_KEY: Final[str] = "hmac-" + "h" * 40
ANTHROPIC_KEY: Final[str] = "sk-ant-" + "a" * 40
BODY: Final[bytes] = b'{"schema_version":"v6.intent.1"}'


def _configured() -> Settings:
    return Settings(_env_file=None, hmac_required=True, internal_hmac_key=HMAC_KEY,
                    anthropic_api_key=ANTHROPIC_KEY)


def _leaks(text: str) -> list[str]:
    return [secret for secret in (HMAC_KEY, ANTHROPIC_KEY) if secret in text]


def test_secret_fields_are_secret_str() -> None:
    cfg = _configured()
    assert isinstance(cfg.internal_hmac_key, SecretStr)
    assert isinstance(cfg.anthropic_api_key, SecretStr)
    assert cfg.internal_hmac_key.get_secret_value() == HMAC_KEY
    assert cfg.anthropic_api_key.get_secret_value() == ANTHROPIC_KEY


def test_defaults_are_secret_str() -> None:
    cfg = Settings(_env_file=None)
    assert cfg.internal_hmac_key.get_secret_value() == PLACEHOLDER_HMAC_KEY
    assert cfg.anthropic_api_key.get_secret_value() == ""


def test_secrets_load_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_HMAC_KEY", HMAC_KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    cfg = Settings(_env_file=None)
    assert cfg.internal_hmac_key.get_secret_value() == HMAC_KEY
    assert cfg.anthropic_api_key.get_secret_value() == ANTHROPIC_KEY


@pytest.mark.parametrize("render", [repr, str, Settings.model_dump_json],
                         ids=["repr", "str", "json"])
def test_settings_never_render_secret_values(render: Callable[[Settings], str]) -> None:
    assert _leaks(render(_configured())) == []


def test_logging_the_settings_does_not_leak_secrets(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("app.settings")
    cfg = _configured()
    with caplog.at_level(logging.DEBUG, logger="app.settings"):
        logger.debug("settings %s", cfg)
        logger.debug("settings %r", cfg)
        logger.debug("dump %s", cfg.model_dump())
    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 3
    assert all("**********" in message for message in messages)
    assert _leaks(caplog.text) == []


@pytest.mark.parametrize("key", [PLACEHOLDER_HMAC_KEY, f"  {PLACEHOLDER_HMAC_KEY}  ", "   ",
                                 SecretStr(PLACEHOLDER_HMAC_KEY)])
def test_hmac_with_a_placeholder_key_is_still_refused(key: str | SecretStr) -> None:
    with pytest.raises(ValidationError, match="placeholder"):
        Settings(_env_file=None, hmac_required=True, internal_hmac_key=key)


def test_hmac_ok_signs_with_the_secret_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_settings, "hmac_required", True)
    monkeypatch.setattr(adapter_settings, "internal_hmac_key", SecretStr(HMAC_KEY))
    signature = hmac.new(HMAC_KEY.encode("utf-8"), BODY, hashlib.sha256).hexdigest()
    assert hmac_ok(BODY, signature) is True
    assert hmac_ok(BODY + b" ", signature) is False
    assert hmac_ok(BODY, "0" * len(signature)) is False
    assert hmac_ok(BODY, None) is False


def test_hmac_ok_is_open_while_hmac_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_settings, "hmac_required", False)
    assert hmac_ok(BODY, None) is True
