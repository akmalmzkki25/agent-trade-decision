"""scripts/v6ops/envfile.py: the scripts read adapter/.env exactly as the adapter does."""

from __future__ import annotations

import builtins
from pathlib import Path
from typing import Any, Final

import pytest

from app.v6.config import V6Settings

from .operator_cli_fixtures_v6 import EA_KEY, sync_cli

from v6ops import envfile  # noqa: E402,I001 - on sys.path once the scripts are loaded

KEY_ENV: Final[str] = "V6_EA_HMAC_KEY"
# A key python-dotenv decodes: an escape and an expansion inside double quotes.
TRICKY_LINE: Final[str] = 'V6_EA_HMAC_KEY="' + EA_KEY + '\\n${QLIP_TEST_PART}\\"end"\n'


def write_env(tmp_path: Path, text: str) -> Path:
    env_file = tmp_path / "adapter.env"
    env_file.write_text(text, encoding="utf-8")
    return env_file


def adapter_key(env_file: Path) -> str:
    return V6Settings(_env_file=str(env_file)).ea_hmac_key.get_secret_value()


def test_the_script_reads_the_key_the_adapter_reads(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QLIP_TEST_PART", "expanded")
    monkeypatch.delenv(KEY_ENV, raising=False)
    env_file = write_env(tmp_path, "# comment\nOTHER=1\n" + TRICKY_LINE)
    value = envfile.read_env_file_value(env_file, KEY_ENV)
    assert value == adapter_key(env_file) == EA_KEY + '\nexpanded"end'


@pytest.mark.parametrize(("text", "expected"), [
    ("v6_ea_hmac_key=lower\nV6_EA_HMAC_KEY=last # note\n", "last"),
    ("export V6_EA_HMAC_KEY='single $X'\n", "single $X"),
    ("V6_EA_HMAC_KEY=\n", None),
])
def test_case_comments_and_last_assignment_match_the_adapter(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str,
        expected: str | None) -> None:
    monkeypatch.delenv(KEY_ENV, raising=False)
    env_file = write_env(tmp_path, text)
    assert envfile.read_env_file_value(env_file, KEY_ENV) == expected
    assert adapter_key(env_file) == (expected or "")


def test_missing_or_unreadable_files_read_as_unset(tmp_path: Path) -> None:
    assert envfile.read_env_file_value(tmp_path / "absent.env", KEY_ENV) is None
    assert envfile.read_env_file_value(tmp_path, KEY_ENV) is None
    bad = tmp_path / "bad.env"
    bad.write_bytes(b"V6_EA_HMAC_KEY=\xff\xfe\n")
    assert envfile.read_env_file_value(bad, KEY_ENV) is None


def test_without_python_dotenv_the_simple_parser_is_used(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def no_dotenv(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "dotenv":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_dotenv)
    assert envfile.dotenv_reader() is envfile.simple_env_values
    env_file = write_env(tmp_path, 'export V6_EA_HMAC_KEY="quoted"\nX=a #c\nnoise\n')
    assert envfile.read_env_file_value(env_file, KEY_ENV) == "quoted"
    assert envfile.read_env_file_value(env_file, "x") == "a"


def test_the_sync_script_writes_the_adapter_key(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QLIP_TEST_PART", "expanded")
    monkeypatch.delenv(KEY_ENV, raising=False)
    env_file = write_env(tmp_path, TRICKY_LINE.replace("\\n", "-"))
    assert sync_cli.load_key({}, env_file) == adapter_key(env_file)


def test_the_environment_wins_over_the_file(tmp_path: Path) -> None:
    env_file = write_env(tmp_path, "V6_EA_HMAC_KEY=from-file\n")
    assert envfile.lookup(KEY_ENV, {KEY_ENV: " from-env "}, env_file) == "from-env"
    assert envfile.lookup(KEY_ENV, {}, env_file) == "from-file"
