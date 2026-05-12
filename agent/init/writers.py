"""Render and write .env and config.yaml for `kayaclaw init`.

render_env and render_config return strings; write_files lays them down
atomically with a validate-then-write sequence so a Ctrl-C mid-write
cannot leave a half-overwritten real config behind.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_ENV_QUOTE_TRIGGERS = set(' "$\'\\`#')


def _quote_env_value(v: str) -> str:
    """Double-quote v if it contains shell-special chars; escape \\ and \"."""
    if any(c in v for c in _ENV_QUOTE_TRIGGERS):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return v


def render_env(
    telegram_token: str,
    chat_id: int,
    provider_api_key_env: str,
    api_key: str,
) -> str:
    """Build the .env file content. Fixed key order for idempotency."""
    lines = [
        f"TELEGRAM_BOT_TOKEN={_quote_env_value(telegram_token)}",
        f"ALLOWED_TELEGRAM_CHAT_IDS={chat_id}",
        f"{provider_api_key_env}={_quote_env_value(api_key)}",
    ]
    return "\n".join(lines) + "\n"


def build_config_dict(
    provider_key: str,
    base_url: str,
    api_key_env: str,
    model_id: str,
) -> dict[str, Any]:
    """Build the dict that becomes config.yaml. Pure; testable without YAML."""
    return {
        "providers": {
            provider_key: {
                "kind": "openai_compatible",
                "base_url": base_url,
                "api_key_env": api_key_env,
                "allowed_models": [model_id],
            }
        },
        "agents": {
            "personal-assistant": {
                "model": f"{provider_key}/{model_id}",
                "system_prompt": "You are a helpful assistant.",
                "connectors": ["telegram"],
            }
        },
    }


def render_config(
    provider_key: str,
    base_url: str,
    api_key_env: str,
    model_id: str,
) -> str:
    """Serialise the config dict to YAML. Stable, sorted output."""
    cfg = build_config_dict(provider_key, base_url, api_key_env, model_id)
    return yaml.safe_dump(cfg, sort_keys=True, default_flow_style=False)


def write_files(
    env_content: str,
    config_content: str,
    target_dir: Path | None = None,
) -> None:
    """Validate config, then write both files atomically.

    Sequence (per PLAN write-then-validate fix):
      1. Parse config_content and run Config.model_validate (no disk writes).
      2. Write .env to .env.tmp.
      3. Write config.yaml to config.yaml.tmp.
      4. Atomic rename .env.tmp -> .env.
      5. Atomic rename config.yaml.tmp -> config.yaml.

    Validation failure leaves nothing on disk. A Ctrl-C between step 2 and
    step 5 leaves only .tmp files; the real .env and config.yaml are
    untouched.
    """
    # Deferred import: agent.config pulls pydantic, which is fine here
    # because we only reach this code after the user finished input.
    from agent.config import Config

    target = target_dir or Path.cwd()
    parsed = yaml.safe_load(config_content) or {}
    Config.model_validate(parsed)

    env_tmp = target / ".env.tmp"
    cfg_tmp = target / "config.yaml.tmp"
    env_final = target / ".env"
    cfg_final = target / "config.yaml"

    env_tmp.write_text(env_content, encoding="utf-8")
    cfg_tmp.write_text(config_content, encoding="utf-8")
    env_tmp.replace(env_final)
    cfg_tmp.replace(cfg_final)
