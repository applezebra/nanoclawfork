"""Unit tests for agent.init.writers + drift test for PROVIDER_DEFAULTS."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from agent.init.cli import PROVIDER_DEFAULTS
from agent.init.writers import (
    _quote_env_value,
    build_config_dict,
    render_config,
    render_env,
    write_files,
)


# _quote_env_value


@pytest.mark.parametrize(
    "value, expected",
    [
        ("plain-value", "plain-value"),
        ("has space", '"has space"'),
        ("has-#-hash", '"has-#-hash"'),
        ("has-$dollar", '"has-$dollar"'),
        ("has\\backslash", '"has\\\\backslash"'),
        ('has"quote', '"has\\"quote"'),
        ("has`backtick", '"has`backtick"'),
        ("has'apostrophe", '"has\'apostrophe"'),
    ],
)
def test_quote_env_value(value, expected):
    assert _quote_env_value(value) == expected


# render_env


def test_render_env_fixed_key_order():
    out = render_env("my-token", 12345, "OPENROUTER_API_KEY", "sk-or-abc")
    lines = out.strip().split("\n")
    assert lines[0].startswith("TELEGRAM_BOT_TOKEN=")
    assert lines[1].startswith("ALLOWED_TELEGRAM_CHAT_IDS=")
    assert lines[2].startswith("OPENROUTER_API_KEY=")


def test_render_env_idempotent():
    a = render_env("tok", 42, "GROQ_API_KEY", "gsk_abc")
    b = render_env("tok", 42, "GROQ_API_KEY", "gsk_abc")
    assert a == b


def test_render_env_no_blank_or_comment_lines():
    out = render_env("tok", 1, "X_API_KEY", "key")
    for line in out.splitlines():
        assert line.strip(), "no blank lines allowed"
        assert not line.lstrip().startswith("#"), "no comments allowed"


def test_render_env_quotes_token_with_special_chars():
    out = render_env("has space", 1, "K_API_KEY", "key")
    assert 'TELEGRAM_BOT_TOKEN="has space"' in out


def test_render_env_quotes_key_with_dollar():
    out = render_env("tok", 1, "K_API_KEY", "key$1")
    assert 'K_API_KEY="key$1"' in out


# build_config_dict + render_config


def test_build_config_dict_shape():
    cfg = build_config_dict("openrouter", "https://or.example/v1", "OR_KEY", "model-x")
    assert cfg["providers"]["openrouter"]["kind"] == "openai_compatible"
    assert cfg["providers"]["openrouter"]["base_url"] == "https://or.example/v1"
    assert cfg["providers"]["openrouter"]["api_key_env"] == "OR_KEY"
    assert cfg["providers"]["openrouter"]["allowed_models"] == ["model-x"]
    assert cfg["agents"]["personal-assistant"]["model"] == "openrouter/model-x"
    assert cfg["agents"]["personal-assistant"]["connectors"] == ["telegram"]


def test_render_config_round_trips_to_same_dict():
    cfg_a = build_config_dict("groq", "https://g.example/v1", "G_KEY", "m1")
    yaml_text = render_config("groq", "https://g.example/v1", "G_KEY", "m1")
    cfg_b = yaml.safe_load(yaml_text)
    assert cfg_a == cfg_b


def test_render_config_sort_keys_true_for_idempotency():
    a = render_config("groq", "https://g.example/v1", "G_KEY", "m1")
    b = render_config("groq", "https://g.example/v1", "G_KEY", "m1")
    assert a == b


def test_render_config_passes_real_config_validate():
    """The written yaml must satisfy the real Config schema."""
    from agent.config import Config

    yaml_text = render_config(
        "openrouter",
        "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY",
        "meta-llama/llama-3.3-70b-instruct",
    )
    parsed = yaml.safe_load(yaml_text)
    Config.model_validate(parsed)  # raises if invalid


# write_files (atomic + validation)


def test_write_files_happy_path(tmp_path: Path):
    env = render_env("tok", 1, "OPENROUTER_API_KEY", "sk-or-abc")
    cfg = render_config(
        "openrouter",
        "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY",
        "meta-llama/llama-3.3-70b-instruct",
    )
    write_files(env, cfg, target_dir=tmp_path)
    assert (tmp_path / ".env").read_text() == env
    assert (tmp_path / "config.yaml").read_text() == cfg
    assert not (tmp_path / ".env.tmp").exists()
    assert not (tmp_path / "config.yaml.tmp").exists()


def test_write_files_malformed_yaml_leaves_nothing(tmp_path: Path):
    """Malformed YAML (not parseable) must not produce ANY file on disk.

    Schema-level validation (provider kind, allowed_models, etc) happens
    at container boot in agent/config.py, not in write_files, because
    init must not import pydantic. write_files's only pre-write check is
    that the YAML parses.
    """
    env = render_env("tok", 1, "OPENROUTER_API_KEY", "sk-or-abc")
    bad_cfg = "providers:\n  - this is not: valid yaml\n    unclosed: ["
    with pytest.raises(yaml.YAMLError):
        write_files(env, bad_cfg, target_dir=tmp_path)
    assert not (tmp_path / ".env").exists()
    assert not (tmp_path / "config.yaml").exists()
    assert not (tmp_path / ".env.tmp").exists()
    assert not (tmp_path / "config.yaml.tmp").exists()


def test_write_files_does_not_import_pydantic(tmp_path: Path):
    """Regression for the v0.1.7-init smoke FAIL: write_files must work
    on a fresh clone whose only deps are stdlib + PyYAML. Verify the
    happy path runs even if pydantic is hidden from sys.modules.
    """
    import sys
    saved = {k: sys.modules.pop(k) for k in list(sys.modules)
             if k == "pydantic" or k.startswith("pydantic.")}
    sys.modules["pydantic"] = None  # type: ignore[assignment]
    try:
        env = render_env("tok", 1, "OPENROUTER_API_KEY", "sk-or-abc")
        cfg = render_config(
            "openrouter",
            "https://openrouter.ai/api/v1",
            "OPENROUTER_API_KEY",
            "meta-llama/llama-3.3-70b-instruct",
        )
        write_files(env, cfg, target_dir=tmp_path)
        assert (tmp_path / ".env").exists()
        assert (tmp_path / "config.yaml").exists()
    finally:
        sys.modules.pop("pydantic", None)
        sys.modules.update(saved)


def test_write_files_overwrites_existing(tmp_path: Path):
    (tmp_path / ".env").write_text("OLD_ENV\n")
    (tmp_path / "config.yaml").write_text("old: 1\n")
    env = render_env("tok", 1, "OPENROUTER_API_KEY", "sk-or-abc")
    cfg = render_config(
        "openrouter",
        "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY",
        "meta-llama/llama-3.3-70b-instruct",
    )
    write_files(env, cfg, target_dir=tmp_path)
    assert "OLD_ENV" not in (tmp_path / ".env").read_text()
    assert "old:" not in (tmp_path / "config.yaml").read_text()


# Drift test: PROVIDER_DEFAULTS vs config.example.yaml (P2-4 plan-eng-review)


def _example_path() -> Path:
    """Locate config.example.yaml relative to the test file."""
    here = Path(__file__).resolve()
    return here.parent.parent / "config.example.yaml"


def test_provider_defaults_drift_against_example():
    """Each entry in PROVIDER_DEFAULTS must have matching values somewhere
    in config.example.yaml. Catches drift when one file is updated and the
    other is not.
    """
    text = _example_path().read_text()
    for key, defaults in PROVIDER_DEFAULTS.items():
        assert defaults["base_url"] in text, (
            f"base_url for {key} not found in config.example.yaml; one file drifted"
        )
        assert defaults["api_key_env"] in text, (
            f"api_key_env for {key} not found in config.example.yaml"
        )
        assert defaults["default_model"] in text, (
            f"default_model for {key} not found in config.example.yaml"
        )
