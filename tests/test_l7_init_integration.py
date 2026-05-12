"""End-to-end integration tests for agent.init.cli.main().

All external I/O is mocked one layer above the network and filesystem:
- builtins.input feeds a queue of pre-planned answers
- urllib.request.urlopen returns scripted responses (validators + polling)
- subprocess.run returns a stub CompletedProcess for docker --version
- time.sleep / time.monotonic are mocked so polling never actually waits

Each test changes the working directory to tmp_path so file writes are
isolated and the test does not collide with the repo's real .env.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
from collections import deque
from pathlib import Path
from typing import Any, Iterable
from unittest.mock import patch

import pytest

from agent.init import cli as cli_mod


# Shared response helpers


class _Resp:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _ok(payload: dict) -> _Resp:
    return _Resp(200, json.dumps(payload).encode("utf-8"))


def _http_err(status: int, payload: dict) -> urllib.error.HTTPError:
    import io
    return urllib.error.HTTPError(
        url="http://x.test",
        code=status,
        msg="err",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(json.dumps(payload).encode("utf-8")),
    )


def _getme_ok(username: str = "testbot") -> _Resp:
    return _ok({"ok": True, "result": {"id": 1, "username": username}})


def _models_ok() -> _Resp:
    return _ok({"data": [{"id": "model-a"}]})


def _msg(update_id: int, chat_id: int) -> dict:
    return {
        "update_id": update_id,
        "message": {"chat": {"id": chat_id, "type": "private"}, "text": "hi"},
    }


def _updates(updates: list[dict]) -> _Resp:
    return _ok({"ok": True, "result": updates})


def _input_queue(*values: str):
    queue = deque(values)

    def fake_input(_prompt_text: str = "") -> str:
        if not queue:
            raise AssertionError(f"input() called past queue end; prompt={_prompt_text!r}")
        return queue.popleft()

    return fake_input, queue


# Fixtures


@pytest.fixture
def chdir_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def fast_time(monkeypatch: pytest.MonkeyPatch):
    """Make time.sleep a no-op; monotonic returns the same value forever
    so the poll deadline is never reached unless a test wants it."""
    monkeypatch.setattr("agent.init.polling.time.sleep", lambda _s: None)
    monkeypatch.setattr("agent.init.polling.time.monotonic", lambda: 0.0)


@pytest.fixture
def docker_present(monkeypatch: pytest.MonkeyPatch):
    """Default: docker --version returns success."""
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("agent.init.cli.subprocess.run", fake_run)


def _script_urlopen(responses: Iterable):
    """Build a urlopen side_effect that returns each scripted response in
    order. Items can be _Resp instances OR exceptions to raise."""
    rs = deque(responses)

    def side_effect(req, timeout=None):  # noqa: ARG001
        if not rs:
            raise AssertionError("urlopen called past scripted response queue")
        item = rs.popleft()
        if isinstance(item, Exception):
            raise item
        return item

    return side_effect


# Case 1: happy path (OpenRouter)


def test_happy_path_openrouter(chdir_tmp: Path, fast_time, docker_present):
    fake_input, _q = _input_queue(
        "sk-or-test-key",     # provider key paste
        "",                   # confirm OpenRouter detection (default yes)
        "123:telegram-test",  # telegram bot token
        "",                   # accept captured chat id (default yes)
    )
    responses = [
        _models_ok(),                            # validate provider key
        _getme_ok("kayatestbot"),                # validate telegram token
        _updates([]),                            # backlog clear: empty
        _updates([_msg(100, 555555)]),           # first poll: real message
    ]
    with patch("builtins.input", side_effect=fake_input), patch("agent.init.cli.getpass.getpass", side_effect=fake_input), \
         patch("urllib.request.urlopen", side_effect=_script_urlopen(responses + [
             _updates([]),                       # backlog clear
             _updates([_msg(100, 555555)]),      # first poll
         ])):
        rc = cli_mod.main()
    assert rc == 0
    env = (chdir_tmp / ".env").read_text()
    cfg = (chdir_tmp / "config.yaml").read_text()
    assert "TELEGRAM_BOT_TOKEN=123:telegram-test" in env
    assert "ALLOWED_TELEGRAM_CHAT_IDS=555555" in env
    assert "OPENROUTER_API_KEY=sk-or-test-key" in env
    assert "openrouter:" in cfg
    assert "https://openrouter.ai/api/v1" in cfg


# Case 2: happy path (Groq via prefix detection)


def test_happy_path_groq(chdir_tmp: Path, fast_time, docker_present):
    fake_input, _q = _input_queue(
        "gsk_test_groq_key",
        "",                  # confirm Groq detection
        "456:tg-token",
        "",                  # accept chat id
    )
    with patch("builtins.input", side_effect=fake_input), patch("agent.init.cli.getpass.getpass", side_effect=fake_input), \
         patch("urllib.request.urlopen", side_effect=_script_urlopen([
             _models_ok(), _getme_ok(),
             _updates([]), _updates([_msg(1, 42)]),
         ])):
        rc = cli_mod.main()
    assert rc == 0
    assert "GROQ_API_KEY=gsk_test_groq_key" in (chdir_tmp / ".env").read_text()
    assert "groq:" in (chdir_tmp / "config.yaml").read_text()


# Case 3: happy path (DeepInfra via list selection, Enter then pick)


def test_happy_path_deepinfra_via_list(chdir_tmp: Path, fast_time, docker_present):
    fake_input, _q = _input_queue(
        "",          # press Enter to pick from list
        "2",         # DeepInfra
        "deepinfra-random-key",
        "789:tg",
        "",          # accept chat id
    )
    with patch("builtins.input", side_effect=fake_input), patch("agent.init.cli.getpass.getpass", side_effect=fake_input), \
         patch("urllib.request.urlopen", side_effect=_script_urlopen([
             _models_ok(), _getme_ok(),
             _updates([]), _updates([_msg(1, 99)]),
         ])):
        rc = cli_mod.main()
    assert rc == 0
    cfg = (chdir_tmp / "config.yaml").read_text()
    assert "deepinfra:" in cfg
    assert "DEEPINFRA_API_KEY" in (chdir_tmp / ".env").read_text()


# Case 4: happy path (Other / custom provider via list)


def test_happy_path_custom_provider(chdir_tmp: Path, fast_time, docker_present):
    fake_input, _q = _input_queue(
        "",                              # Enter
        "4",                             # Other
        "https://api.custom.test/v1",    # base_url
        "MY_PROVIDER_KEY",               # env var
        "custom-llama-13b",              # model
        "mycustomkey123",                # api key
        "11:tg",
        "",                              # accept chat id
    )
    with patch("builtins.input", side_effect=fake_input), patch("agent.init.cli.getpass.getpass", side_effect=fake_input), \
         patch("urllib.request.urlopen", side_effect=_script_urlopen([
             _models_ok(), _getme_ok(),
             _updates([]), _updates([_msg(1, 7)]),
         ])):
        rc = cli_mod.main()
    assert rc == 0
    cfg = (chdir_tmp / "config.yaml").read_text()
    assert "https://api.custom.test/v1" in cfg
    assert "MY_PROVIDER_KEY" in cfg


# Case 5: Telegram retry-then-success


def test_telegram_retry_then_success(chdir_tmp: Path, fast_time, docker_present):
    fake_input, _q = _input_queue(
        "sk-or-key", "",
        "bad1",                  # first telegram attempt (will 401)
        "bad2",                  # second attempt (will 401)
        "good:token",            # third attempt
        "",                      # accept chat id
    )
    val_responses = [
        _models_ok(),
        _http_err(401, {"ok": False, "description": "unauthorized"}),
        _http_err(401, {"ok": False, "description": "unauthorized"}),
        _getme_ok(),
        _updates([]),
        _updates([_msg(1, 8)]),
    ]
    with patch("builtins.input", side_effect=fake_input), patch("agent.init.cli.getpass.getpass", side_effect=fake_input), \
         patch("urllib.request.urlopen", side_effect=_script_urlopen(val_responses)):
        rc = cli_mod.main()
    assert rc == 0
    assert "TELEGRAM_BOT_TOKEN=good:token" in (chdir_tmp / ".env").read_text()


# Case 6: provider key retry-then-success


def test_provider_retry_then_success(chdir_tmp: Path, fast_time, docker_present):
    fake_input, _q = _input_queue(
        "",            # press Enter for list
        "1",           # OpenRouter
        "bad-key",     # attempt 1 (401)
        "good-key",    # attempt 2 (200)
        "tg:token", "",
    )
    val_responses = [
        _http_err(401, {"error": "invalid"}),
        _models_ok(),
        _getme_ok(),
        _updates([]),
        _updates([_msg(1, 5)]),
    ]
    with patch("builtins.input", side_effect=fake_input), patch("agent.init.cli.getpass.getpass", side_effect=fake_input), \
         patch("urllib.request.urlopen", side_effect=_script_urlopen(val_responses)):
        rc = cli_mod.main()
    assert rc == 0


# Case 7: three Telegram failures -> sys.exit(1) and no files


def test_three_telegram_failures_exits_no_files(chdir_tmp: Path, fast_time, docker_present):
    fake_input, _q = _input_queue(
        "sk-or-key", "",
        "bad1", "bad2", "bad3",
    )
    val_responses = [
        _models_ok(),
        _http_err(401, {"ok": False}),
        _http_err(401, {"ok": False}),
        _http_err(401, {"ok": False}),
    ]
    with patch("builtins.input", side_effect=fake_input), patch("agent.init.cli.getpass.getpass", side_effect=fake_input), \
         patch("urllib.request.urlopen", side_effect=_script_urlopen(val_responses)):
        with pytest.raises(SystemExit) as exc_info:
            cli_mod.main()
    assert exc_info.value.code == 1
    assert not (chdir_tmp / ".env").exists()
    assert not (chdir_tmp / "config.yaml").exists()


# Case 8: existing .env, user picks "keep" -> exits, nothing changed


def test_existing_env_keep_exits_clean(chdir_tmp: Path, fast_time, docker_present):
    (chdir_tmp / ".env").write_text("ORIGINAL_KEY=untouched\n")
    fake_input, _q = _input_queue("1")  # keep
    with patch("builtins.input", side_effect=fake_input):
        rc = cli_mod.main()
    assert rc == 0
    assert (chdir_tmp / ".env").read_text() == "ORIGINAL_KEY=untouched\n"
    assert not (chdir_tmp / "config.yaml").exists()


# Case 9: existing .env, overwrite branch continues + writes


def test_existing_env_overwrite_runs_full_flow(chdir_tmp: Path, fast_time, docker_present):
    (chdir_tmp / ".env").write_text("ORIGINAL_KEY=will_be_replaced\n")
    fake_input, _q = _input_queue(
        "2",                       # overwrite
        "sk-or-key", "",
        "tg:token", "",
    )
    with patch("builtins.input", side_effect=fake_input), patch("agent.init.cli.getpass.getpass", side_effect=fake_input), \
         patch("urllib.request.urlopen", side_effect=_script_urlopen([
             _models_ok(), _getme_ok(),
             _updates([]), _updates([_msg(1, 6)]),
         ])):
        rc = cli_mod.main()
    assert rc == 0
    assert "ORIGINAL_KEY" not in (chdir_tmp / ".env").read_text()
    assert "OPENROUTER_API_KEY=sk-or-key" in (chdir_tmp / ".env").read_text()


# Case 10: docker absent (subprocess raises FileNotFoundError) -> init continues


def test_docker_absent_init_still_succeeds(
    chdir_tmp: Path, fast_time, monkeypatch: pytest.MonkeyPatch, capsys
):
    monkeypatch.setattr(
        "agent.init.cli.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("docker missing")),
    )
    fake_input, _q = _input_queue("sk-or-key", "", "tg:token", "")
    with patch("builtins.input", side_effect=fake_input), patch("agent.init.cli.getpass.getpass", side_effect=fake_input), \
         patch("urllib.request.urlopen", side_effect=_script_urlopen([
             _models_ok(), _getme_ok(),
             _updates([]), _updates([_msg(1, 9)]),
         ])):
        rc = cli_mod.main()
    assert rc == 0
    assert (chdir_tmp / ".env").exists()
    captured = capsys.readouterr()
    assert "docker was not found" in captured.out.lower()


# Case 11: chat-ID timeout -> manual entry fallback


def test_chat_id_timeout_falls_back_to_manual(chdir_tmp: Path, docker_present, monkeypatch):
    # Make the deadline elapse on the very first monotonic check.
    times = iter([0.0, 9999.0, 9999.0])
    monkeypatch.setattr("agent.init.polling.time.monotonic", lambda: next(times))
    monkeypatch.setattr("agent.init.polling.time.sleep", lambda _s: None)
    fake_input, _q = _input_queue(
        "sk-or-key", "",
        "tg:token",
        "",            # accept manual-entry fallback prompt (default yes)
        "777",         # manual chat id
    )
    with patch("builtins.input", side_effect=fake_input), patch("agent.init.cli.getpass.getpass", side_effect=fake_input), \
         patch("urllib.request.urlopen", side_effect=_script_urlopen([
             _models_ok(), _getme_ok(),
             _updates([]),     # backlog clear
             _updates([]),     # one poll attempt before deadline trips
         ])):
        rc = cli_mod.main()
    assert rc == 0
    assert "ALLOWED_TELEGRAM_CHAT_IDS=777" in (chdir_tmp / ".env").read_text()


# Case 12: non-message update is skipped, second update is picked up


def test_non_message_update_skipped(chdir_tmp: Path, fast_time, docker_present):
    fake_input, _q = _input_queue("sk-or-key", "", "tg:token", "")
    edited = {
        "update_id": 10,
        "edited_message": {"chat": {"id": 11111, "type": "private"}, "text": "edit"},
    }
    real = _msg(11, 22222)
    with patch("builtins.input", side_effect=fake_input), patch("agent.init.cli.getpass.getpass", side_effect=fake_input), \
         patch("urllib.request.urlopen", side_effect=_script_urlopen([
             _models_ok(), _getme_ok(),
             _updates([]),         # backlog
             _updates([edited]),   # poll 1: edited message (skipped)
             _updates([real]),     # poll 2: real message (captured)
         ])):
        rc = cli_mod.main()
    assert rc == 0
    assert "ALLOWED_TELEGRAM_CHAT_IDS=22222" in (chdir_tmp / ".env").read_text()


# Cases 13-15: P2-5 backward-compat dispatch suite via subprocess


def test_dispatch_no_args_does_not_run_init():
    """python3 -m agent (no args) routes to bot loop, NOT init. The bot loop
    fails fast on missing config; that's the expected failure mode, and it
    proves init was NOT run (init does not look for /config/config.yaml).
    """
    venv_py = Path.home() / ".venvs" / "kayaclaw" / "bin" / "python3"
    if not venv_py.exists():
        pytest.skip("kayaclaw venv not found; subprocess dispatch test requires it")
    result = subprocess.run(
        [str(venv_py), "-m", "agent"],
        capture_output=True, timeout=15,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
    )
    combined = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert "kayaclaw init" not in combined
    # Bot loop tries to load /config/config.yaml and fails. That's the
    # signal we routed there.
    assert "Startup failed" in combined or result.returncode != 0


def test_dispatch_unknown_subcommand_exits_1():
    venv_py = Path.home() / ".venvs" / "kayaclaw" / "bin" / "python3"
    if not venv_py.exists():
        pytest.skip("kayaclaw venv not found")
    result = subprocess.run(
        [str(venv_py), "-m", "agent", "foobar"],
        capture_output=True, timeout=15,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
    )
    assert result.returncode == 1
    assert b"Unknown subcommand" in result.stderr


def test_dispatch_flag_passes_through_to_main():
    """Flag-style args (--anything) do NOT trigger the "Unknown subcommand"
    error. They fall through to main(), which will fail on missing config.
    """
    venv_py = Path.home() / ".venvs" / "kayaclaw" / "bin" / "python3"
    if not venv_py.exists():
        pytest.skip("kayaclaw venv not found")
    result = subprocess.run(
        [str(venv_py), "-m", "agent", "--anything"],
        capture_output=True, timeout=15,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
    )
    combined = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert "Unknown subcommand" not in combined
