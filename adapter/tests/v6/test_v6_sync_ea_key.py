"""scripts/v6_sync_ea_key.py: copy V6_EA_HMAC_KEY into the MT5 data folder, never print it."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from app.v6 import wire
from app.v6.config import ea_key_ok

from .operator_cli_fixtures_v6 import EA_KEY, SYNC_SCRIPT, sync_cli

GOLDEN = Path(__file__).resolve().parent / "golden" / "hmac_vectors.json"
KEY_FILE = Path("MQL5") / "Files" / "QlipV6" / "hmac.key"


@pytest.fixture
def terminal(tmp_path: Path) -> Path:
    folder = tmp_path / "Terminal" / "D0E8209F77C8CF37AD8BF550E51FF075"
    (folder / "MQL5" / "Experts").mkdir(parents=True)
    return folder


def run(argv: list[str], environ: dict[str, str] | None = None,
        ) -> tuple[int, dict[str, Any], dict[str, Any], str]:
    out, err = io.StringIO(), io.StringIO()
    env = {"V6_EA_HMAC_KEY": EA_KEY} if environ is None else environ
    code = sync_cli.main(argv, environ=env, stdout=out, stderr=err)
    text = out.getvalue() + err.getvalue()
    return (code, json.loads(out.getvalue() or "{}"), json.loads(err.getvalue() or "{}"), text)


def args(terminal: Path, *extra: str) -> list[str]:
    return ["--terminal-data", str(terminal), "--env-file", str(terminal / "absent.env"), *extra]


def test_the_key_is_written_as_bare_ascii(terminal: Path) -> None:
    code, out, _, text = run(args(terminal))
    target = terminal / KEY_FILE
    assert code == sync_cli.EXIT_OK and target.read_bytes() == EA_KEY.encode("ascii")
    assert out == {"written": True, "unchanged": False, "path": str(target),
                   "bytes": len(EA_KEY), "fingerprint": sync_cli.fingerprint(EA_KEY),
                   "ea_input": "InpHmacKeyFile=QlipV6\\hmac.key"}
    assert EA_KEY not in text
    assert [p.name for p in target.parent.iterdir()] == ["hmac.key"]  # no temporary left


def test_an_identical_key_is_not_rewritten(terminal: Path) -> None:
    run(args(terminal))
    target = terminal / KEY_FILE
    os.utime(target, (1_000_000_000, 1_000_000_000))
    code, out, _, _ = run(args(terminal))
    assert code == 0 and (out["written"], out["unchanged"]) == (False, True)
    assert target.stat().st_mtime == 1_000_000_000


def test_a_different_key_replaces_the_file(terminal: Path) -> None:
    target = terminal / KEY_FILE
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old-key-" + b"o" * 40 + b"\r\n")
    code, out, _, _ = run(args(terminal))
    assert code == 0 and out["written"] is True
    assert target.read_bytes() == EA_KEY.encode("ascii")


def test_the_fingerprint_matches_the_wire_contract() -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert sync_cli.fingerprint(golden["test_key"]) == golden["test_key_fingerprint"]
    assert sync_cli.FINGERPRINT_LABEL.decode("ascii") == golden["fingerprint_label"]
    assert sync_cli.fingerprint(EA_KEY) == wire.key_fingerprint(SecretStr(EA_KEY))


@pytest.mark.parametrize("candidate", [
    EA_KEY, "k" * 31, "k" * 32, "k" * 256, "k" * 257, "key with spaces " + "k" * 20,
    "k" * 31 + "\t", "kéy" + "k" * 40, "change-me-" + "k" * 30, "my-PLACEHOLDER-" + "k" * 30,
    "your-key-here" + "k" * 30, "k" * 20 + "\x7f" + "k" * 20, "",
])
def test_the_key_rule_matches_the_adapter(candidate: str) -> None:
    assert (sync_cli.key_problem(candidate) is None) == ea_key_ok(SecretStr(candidate))


@pytest.mark.parametrize(("key", "detail"), [
    ("short-key", "V6_EA_HMAC_KEY must be 32-256 characters, not 9"),
    (" " + EA_KEY, "V6_EA_HMAC_KEY must be printable ASCII without whitespace"),
    ("change-me-" + "k" * 30, "V6_EA_HMAC_KEY is a placeholder"),
])
def test_unusable_keys_are_refused_without_echo(terminal: Path, key: str, detail: str) -> None:
    code, _, error, text = run(args(terminal), environ={"V6_EA_HMAC_KEY": key})
    assert (code, error) == (sync_cli.EXIT_USAGE, {"error": "usage", "detail": detail})
    assert key.strip() not in text and not (terminal / KEY_FILE).exists()


def test_the_env_file_is_the_fallback(terminal: Path) -> None:
    env_file = terminal / "adapter.env"
    env_file.write_text(f"V6_OPERATOR_TOKEN=x\nV6_EA_HMAC_KEY='{EA_KEY}'\n", encoding="utf-8")
    argv = ["--data-folder", str(terminal), "--env-file", str(env_file)]
    code, out, _, _ = run(argv, environ={})
    assert code == 0 and out["written"] is True
    assert (terminal / KEY_FILE).read_bytes() == EA_KEY.encode("ascii")
    other = "env-wins-" + "e" * 30
    run(argv, environ={"V6_EA_HMAC_KEY": other})
    assert (terminal / KEY_FILE).read_bytes() == other.encode("ascii")


def test_a_missing_key_is_a_usage_error(terminal: Path) -> None:
    code, _, error, _ = run(args(terminal), environ={})
    assert code == sync_cli.EXIT_USAGE
    assert error["detail"] == "V6_EA_HMAC_KEY is not set (environment or absent.env)"


def test_only_a_terminal_data_folder_is_accepted(tmp_path: Path) -> None:
    missing = tmp_path / "nowhere"
    code, _, error, _ = run(args(missing))
    assert code == 2 and error["detail"].startswith("terminal data folder not found")
    plain = tmp_path / "plain"
    plain.mkdir()
    code, _, error, _ = run(args(plain))
    assert code == 2 and error["detail"].startswith("not an MT5 terminal data folder")
    assert not (plain / KEY_FILE).exists()


def test_check_compares_without_writing(terminal: Path) -> None:
    code, out, _, text = run(args(terminal, "--check"))
    assert code == sync_cli.EXIT_FAILED and out["present"] is False and out["match"] is False
    assert not (terminal / KEY_FILE).exists()

    target = terminal / KEY_FILE
    target.parent.mkdir(parents=True)
    target.write_bytes(EA_KEY.encode("ascii") + b"\r\n")
    code, out, _, text = run(args(terminal, "--check"))
    assert code == 0 and out["match"] is True and out["file_fingerprint"] == out["fingerprint"]

    other = "other-key-" + "z" * 30
    target.write_bytes(other.encode("ascii"))
    code, out, _, text = run(args(terminal, "--check"))
    assert code == 1 and out["match"] is False
    assert out["file_fingerprint"] == sync_cli.fingerprint(other)
    assert EA_KEY not in text and other not in text

    for junk in ("кириллица".encode("utf-8"), EA_KEY.encode("ascii") + b" " * 2000):
        target.write_bytes(junk)
        code, out, _, _ = run(args(terminal, "--check"))
        assert code == 1 and out["file_fingerprint"] is None and out["present"] is True


def test_write_failures_are_reported(terminal: Path) -> None:
    (terminal / "MQL5" / "Files").write_text("not a folder", encoding="utf-8")
    code, _, error, text = run(args(terminal))
    assert code == sync_cli.EXIT_FAILED and error["error"] == "io"
    assert EA_KEY not in text


def test_a_failed_replace_leaves_no_temporary(terminal: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_: object) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(sync_cli.os, "replace", broken)
    code, _, error, _ = run(args(terminal))
    folder = terminal / KEY_FILE.parent
    assert code == 1 and error["error"] == "io" and list(folder.iterdir()) == []


@pytest.mark.parametrize("argv", [[], ["--check"], ["--terminal-data"]])
def test_bad_arguments(argv: list[str], capsys: pytest.CaptureFixture) -> None:
    assert sync_cli.main(argv, environ={}) == sync_cli.EXIT_USAGE
    assert "usage" in capsys.readouterr().err


def test_help_and_standard_library_only() -> None:
    assert sync_cli.main(["--help"], environ={}) == sync_cli.EXIT_OK
    completed = subprocess.run([sys.executable, "-S", "-I", str(SYNC_SCRIPT), "--help"],
                               capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, completed.stderr
    assert "--terminal-data" in completed.stdout
