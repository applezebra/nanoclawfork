"""L1 tests — config schema + provider registry.

Step 1 tests (schema and loader) are below.
Step 2 tests (resolver) follow in the second section.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agent.config import Config, ConfigError, load_config
from agent.registry import ResolvedProvider, resolve

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXAMPLE_CONFIG = Path(__file__).parent.parent / "config.example.yaml"

_VALID_PROVIDER = {
    "kind": "openai_compatible",
    "base_url": "https://api.example.com/v1",
    "api_key_env": "EXAMPLE_API_KEY",
    "allowed_models": ["some-model"],
}

_VALID_AGENT = {
    "model": "example/some-model",
    "system_prompt": "You are helpful.",
    "connectors": [],
}


def _make_raw(provider_override: dict | None = None, agent_override: dict | None = None) -> dict:
    provider = {**_VALID_PROVIDER, **(provider_override or {})}
    agent = {**_VALID_AGENT, **(agent_override or {})}
    return {"providers": {"p": provider}, "agents": {"a": agent}}


def _load_from_dict(raw: dict) -> Config:
    """Write raw dict to a temp YAML file and call load_config (raises ConfigError on failure)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(raw, f)
        tmp = Path(f.name)
    try:
        return load_config(tmp)
    finally:
        tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Step 1 — Pydantic schema and loader
# ---------------------------------------------------------------------------


def test_load_example_config_returns_config():
    """Load config.example.yaml; assert at least one provider and one agent group."""
    config = load_config(_EXAMPLE_CONFIG)
    assert isinstance(config, Config)
    assert len(config.providers) >= 1
    assert len(config.agents) >= 1


def test_missing_required_field_raises_config_error():
    """A provider dict without 'kind' must raise ConfigError (not raw ValidationError)."""
    raw = _make_raw()
    raw["providers"]["p"].pop("kind")
    with pytest.raises(ConfigError):
        _load_from_dict(raw)


def test_model_without_slash_raises_config_error():
    """model: 'deepinfra' (no slash) must raise ConfigError from the field validator."""
    raw = _make_raw(agent_override={"model": "deepinfra"})
    with pytest.raises(ConfigError):
        _load_from_dict(raw)


def test_invalid_kind_raises_config_error():
    """kind: 'ollama' (not in Literal) must raise ConfigError."""
    raw = _make_raw(provider_override={"kind": "ollama"})
    with pytest.raises(ConfigError):
        _load_from_dict(raw)


def test_no_unsafe_yaml_load_in_source():
    """T1: grep agent/config.py for bare yaml.load (excluding safe_load)."""
    source = (Path(__file__).parent.parent / "agent" / "config.py").read_text(encoding="utf-8")
    # Must NOT find a bare yaml.load call; yaml.safe_load is the only permitted form.
    assert re.search(r"\byaml\.load\b", source) is None, (
        "agent/config.py contains a bare yaml.load call — use yaml.safe_load only"
    )


def test_typo_field_raises_config_error_naming_the_field():
    """T3: extra='forbid' — typo'd field 'kindd' must raise ConfigError that names it."""
    raw = {
        "providers": {
            "deepinfra": {
                "kindd": "openai_compatible",  # typo: 'kindd' instead of 'kind'
                "base_url": "https://api.deepinfra.com/v1/openai",
                "api_key_env": "DEEPINFRA_API_KEY",
                "allowed_models": [],
            }
        },
        "agents": {"a": _VALID_AGENT},
    }
    with pytest.raises(ConfigError) as exc_info:
        _load_from_dict(raw)
    assert "kindd" in str(exc_info.value)


def test_empty_allowed_models_stored_as_deny_all():
    """A2: empty allowed_models is stored as [] (deny-all semantics; resolver enforces in Step 2)."""
    raw = _make_raw(provider_override={"allowed_models": []})
    config = _load_from_dict(raw)
    assert config.providers["p"].allowed_models == []


def test_wildcard_allowed_models_stored():
    """A2: ['*'] is stored as-is for the resolver to interpret as allow-any."""
    raw = _make_raw(provider_override={"allowed_models": ["*"]})
    config = _load_from_dict(raw)
    assert config.providers["p"].allowed_models == ["*"]


def test_base_url_required_for_openai_compatible():
    """Cross-field rule (A1): openai_compatible without base_url raises ConfigError."""
    raw = _make_raw(provider_override={"base_url": None})
    with pytest.raises(ConfigError):
        _load_from_dict(raw)


def test_anthropic_kind_accepted_without_base_url():
    """anthropic kind does not require base_url — schema must accept it."""
    raw = _make_raw(provider_override={"kind": "anthropic", "base_url": None})
    config = _load_from_dict(raw)
    assert config.providers["p"].kind == "anthropic"


# --- Codex P1 fix: load_config must wrap missing-file and bad-YAML errors ---


def test_missing_file_raises_config_error(tmp_path):
    """A non-existent config path must raise ConfigError, not FileNotFoundError."""
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(ConfigError) as excinfo:
        load_config(missing)
    assert "not found" in str(excinfo.value).lower()
    assert str(missing) in str(excinfo.value)


def test_malformed_yaml_raises_config_error(tmp_path):
    """A file with invalid YAML must raise ConfigError, not yaml.YAMLError."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("providers:\n  deepinfra:\n    kind: openai_compatible\n  : invalid", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_config(bad)
    assert "malformed yaml" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# Step 2 — Provider registry resolver
# ---------------------------------------------------------------------------

# Shared helper: build a config with provider 'p' using _make_raw, then load it.
def _make_config(provider_override: dict | None = None) -> Config:
    return _load_from_dict(_make_raw(provider_override=provider_override))


def test_resolve_happy_path(monkeypatch):
    """Happy path: resolve known model with API key set returns ResolvedProvider."""
    monkeypatch.setenv("DEEPINFRA_API_KEY", "test-key")
    config = load_config(_EXAMPLE_CONFIG)
    result = resolve(config, "deepinfra/meta-llama/Llama-3.3-70B-Instruct")
    assert isinstance(result, ResolvedProvider)
    assert result.provider_name == "deepinfra"
    assert result.kind == "openai_compatible"
    assert result.model_id == "meta-llama/Llama-3.3-70B-Instruct"
    assert result.api_key == "test-key"


def test_resolve_provider_name_populated(monkeypatch):
    """P2-4: provider_name field is set to the registry key, not the kind."""
    monkeypatch.setenv("DEEPINFRA_API_KEY", "test-key")
    config = load_config(_EXAMPLE_CONFIG)
    result = resolve(config, "deepinfra/meta-llama/Llama-3.3-70B-Instruct")
    assert result.provider_name == "deepinfra"


def test_resolve_unknown_provider_raises_config_error():
    """Unknown provider in model_ref raises ConfigError naming the provider."""
    config = load_config(_EXAMPLE_CONFIG)
    with pytest.raises(ConfigError) as excinfo:
        resolve(config, "nonexistent/some-model")
    err = str(excinfo.value)
    assert err  # non-empty human-readable string
    assert "nonexistent" in err


def test_resolve_disallowed_model_raises_config_error(monkeypatch):
    """Model not in allowed_models raises ConfigError naming model and allowed list."""
    monkeypatch.setenv("DEEPINFRA_API_KEY", "test-key")
    config = load_config(_EXAMPLE_CONFIG)
    with pytest.raises(ConfigError) as excinfo:
        resolve(config, "deepinfra/gpt-4o")
    err = str(excinfo.value)
    assert err  # non-empty
    assert "gpt-4o" in err
    # Should also mention what IS allowed so operator can diagnose
    assert "meta-llama/Llama-3.3-70B-Instruct" in err


def test_resolve_empty_allowed_models_denies_all(monkeypatch):
    """A2: empty allowed_models = deny all — any model_id raises ConfigError."""
    monkeypatch.setenv("EXAMPLE_API_KEY", "test-key")
    config = _make_config(provider_override={"allowed_models": []})
    with pytest.raises(ConfigError) as excinfo:
        resolve(config, "p/anything")
    assert str(excinfo.value)  # non-empty


def test_resolve_wildcard_allows_any(monkeypatch):
    """A2: allowed_models=['*'] permits any model_id — resolve succeeds."""
    monkeypatch.setenv("EXAMPLE_API_KEY", "test-key")
    config = _make_config(provider_override={"allowed_models": ["*"]})
    result = resolve(config, "p/anything")
    assert result.model_id == "anything"
    assert result.provider_name == "p"


def test_resolve_missing_api_key_raises_config_error(monkeypatch):
    """Missing API key env var raises ConfigError naming the variable."""
    monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)
    config = load_config(_EXAMPLE_CONFIG)
    with pytest.raises(ConfigError) as excinfo:
        resolve(config, "deepinfra/meta-llama/Llama-3.3-70B-Instruct")
    err = str(excinfo.value)
    assert err  # non-empty
    assert "DEEPINFRA_API_KEY" in err


def test_resolve_anthropic_raises_not_implemented():
    """D1: anthropic kind raises NotImplementedError with 'anthropic' and '0.1' in message."""
    config = _make_config(provider_override={"kind": "anthropic", "base_url": None, "api_key_env": None})
    with pytest.raises(NotImplementedError) as excinfo:
        resolve(config, "p/some-model")
    err = str(excinfo.value)
    assert "anthropic" in err.lower()
    assert "0.1" in err


def test_resolve_anthropic_with_key_still_raises_not_implemented(monkeypatch):
    """T2: NotImplementedError fires even when ANTHROPIC_API_KEY is set in env."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    config = _make_config(provider_override={"kind": "anthropic", "base_url": None, "api_key_env": "ANTHROPIC_API_KEY"})
    with pytest.raises(NotImplementedError) as excinfo:
        resolve(config, "p/some-model")
    err = str(excinfo.value)
    assert "anthropic" in err.lower()
    assert "0.1" in err


def test_resolve_config_error_messages_are_nonempty(monkeypatch):
    """ConfigError messages are non-empty human-readable strings in all failure cases."""
    config = load_config(_EXAMPLE_CONFIG)

    # Unknown provider
    with pytest.raises(ConfigError) as exc:
        resolve(config, "ghost/model")
    assert str(exc.value)

    # Disallowed model (key not needed — fails before step 4)
    with pytest.raises(ConfigError) as exc:
        resolve(config, "deepinfra/not-allowed-model")
    assert str(exc.value)

    # Missing API key
    monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)
    with pytest.raises(ConfigError) as exc:
        resolve(config, "deepinfra/meta-llama/Llama-3.3-70B-Instruct")
    assert str(exc.value)
