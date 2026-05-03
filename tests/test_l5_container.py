"""Tests for L5 Step 1: agent.__main__.main()."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent import __main__ as main_mod


def _write_config(tmp_path: Path, model: str = "deepinfra/m1") -> Path:
    """Write a minimal valid config.yaml."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"""
providers:
  deepinfra:
    kind: openai_compatible
    base_url: https://api.example/v1
    api_key_env: DEEPINFRA_API_KEY
    allowed_models:
      - m1
agents:
  personal-assistant:
    model: {model}
    system_prompt: be helpful
"""
    )
    return cfg


class TestMainConfigLoading:
    def test_missing_config_file_returns_1(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Non-existent config path → CRITICAL log + exit code 1."""
        with caplog.at_level(logging.CRITICAL, logger="agent.main"):
            rc = main_mod.main(tmp_path / "does-not-exist.yaml")
        assert rc == 1
        assert any("Startup failed" in r.getMessage() for r in caplog.records)

    def test_missing_personal_assistant_group_returns_1(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Config without personal-assistant group → CRITICAL log + exit 1."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            """
providers:
  p:
    kind: openai_compatible
    base_url: https://x
    allowed_models: ["*"]
agents:
  other-group:
    model: p/m
    system_prompt: sp
"""
        )
        with caplog.at_level(logging.CRITICAL, logger="agent.main"):
            rc = main_mod.main(cfg)
        assert rc == 1
        assert any("personal-assistant" in r.getMessage() for r in caplog.records)


class TestMainProviderResolve:
    def test_unknown_provider_returns_1(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Provider not declared in config → ConfigError from resolve → exit 1."""
        cfg = _write_config(tmp_path, model="ghost/m1")
        monkeypatch.setenv("DEEPINFRA_API_KEY", "fake")
        with caplog.at_level(logging.CRITICAL, logger="agent.main"):
            rc = main_mod.main(cfg)
        assert rc == 1
        assert any("Startup failed" in r.getMessage() for r in caplog.records)


class TestMainStartupLogging:
    def test_startup_log_contains_provider_and_model(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cfg = _write_config(tmp_path)
        monkeypatch.setenv("DEEPINFRA_API_KEY", "fake")
        # Stop before connector_run blocks
        monkeypatch.setattr(main_mod, "connector_run", MagicMock())
        # Use a temp db path so we don't try to write /data
        monkeypatch.setattr(
            main_mod, "default_db_path", lambda: tmp_path / "agent.sqlite"
        )

        with caplog.at_level(logging.INFO, logger="agent.main"):
            rc = main_mod.main(cfg)

        assert rc == 0
        msgs = " ".join(r.getMessage() for r in caplog.records)
        assert "Agent starting:" in msgs
        assert "provider=deepinfra" in msgs
        assert "model=m1" in msgs
        assert "connector=telegram" in msgs


class TestMainSyncContract:
    """eng-review P2-2: main() must invoke connector synchronously, not via asyncio.run.

    Locks in L4 A4 — pgttb 22.x's run_polling owns its own event loop and
    must NOT be wrapped. A regression that re-introduces asyncio.run would
    crash with TypeError: a coroutine was expected.
    """

    def test_connector_called_with_positional_args_no_asyncio(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _write_config(tmp_path)
        monkeypatch.setenv("DEEPINFRA_API_KEY", "fake")

        connector_mock = MagicMock()
        monkeypatch.setattr(main_mod, "connector_run", connector_mock)
        monkeypatch.setattr(
            main_mod, "default_db_path", lambda: tmp_path / "agent.sqlite"
        )

        # Hard-fail if anyone calls asyncio.run from main()
        import asyncio

        def must_not_call(*_args, **_kwargs):  # noqa: ANN001
            pytest.fail(
                "asyncio.run must not be called from main() — connector is sync. "
                "Re-introducing the wrapper would crash run_polling."
            )

        monkeypatch.setattr(asyncio, "run", must_not_call)

        rc = main_mod.main(cfg)

        assert rc == 0
        connector_mock.assert_called_once()
        args = connector_mock.call_args.args
        assert len(args) == 3, f"expected (config, resolve, memory); got {args!r}"
        # arg[0] is the loaded Config; arg[1] is the resolve callable; arg[2] is Memory
        from agent.config import Config
        from agent.memory import Memory
        from agent.registry import resolve as resolve_fn

        assert isinstance(args[0], Config)
        assert args[1] is resolve_fn
        assert isinstance(args[2], Memory)


class TestMainKeyboardInterrupt:
    def test_keyboard_interrupt_returns_0_and_logs_stopped(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cfg = _write_config(tmp_path)
        monkeypatch.setenv("DEEPINFRA_API_KEY", "fake")

        def raise_kbd(*_a, **_kw):
            raise KeyboardInterrupt

        monkeypatch.setattr(main_mod, "connector_run", raise_kbd)
        monkeypatch.setattr(
            main_mod, "default_db_path", lambda: tmp_path / "agent.sqlite"
        )

        with caplog.at_level(logging.INFO, logger="agent.main"):
            rc = main_mod.main(cfg)

        assert rc == 0
        assert any("Agent stopped" in r.getMessage() for r in caplog.records)


class TestMainMemoryInitFailure:
    def test_unwritable_db_path_returns_1(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cfg = _write_config(tmp_path)
        monkeypatch.setenv("DEEPINFRA_API_KEY", "fake")
        # Point default_db_path at a directory that doesn't exist and can't be created
        # (a path under a regular file)
        bad_parent = tmp_path / "regular_file"
        bad_parent.write_text("not a dir")
        monkeypatch.setattr(
            main_mod, "default_db_path", lambda: bad_parent / "subdir" / "agent.sqlite"
        )
        monkeypatch.setattr(main_mod, "connector_run", MagicMock())

        with caplog.at_level(logging.CRITICAL, logger="agent.main"):
            rc = main_mod.main(cfg)

        assert rc == 1
        assert any("Memory init failed" in r.getMessage() for r in caplog.records)
