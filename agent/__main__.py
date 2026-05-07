"""CLI entrypoint. Invoked as `python -m agent` or via the `agent` console script.

Composes Config → personal-assistant agent group → ResolvedProvider → Memory →
connector.run() and converts every startup-failure class into one CRITICAL log.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

from agent.config import ConfigError, load_config
from agent.connectors.telegram import run as connector_run
from agent.logging import get_logger, register_secret
from agent.memory import Memory, default_db_path
from agent.registry import resolve

_DEFAULT_CONFIG_PATH = Path("/config/config.yaml")


def main(config_path: Path = _DEFAULT_CONFIG_PATH) -> int:
    """Compose + run. Returns exit code. SYNC connector_run (eng-review P1-2)."""
    log = get_logger("agent.main")
    try:
        config = load_config(config_path)
        agent_spec = config.agents.get("personal-assistant")
        if agent_spec is None:
            # Wording matches agent.connectors.telegram.run() so both
            # paths emit identical text (codex-review L5-Step1 P2).
            raise ConfigError(
                f"Agent group 'personal-assistant' not found in config; "
                f"defined groups: {list(config.agents.keys())}"
            )
        # Drive secret registration from config, not env-var name patterns.
        # The L0 _collect_secrets() only catches *_API_KEY; a self-hoster
        # using OPENAI_TOKEN, GROQ_KEY, HF_TOKEN, etc. would otherwise
        # have their key NOT registered with the scrubber and could leak
        # in tracebacks (security-audit L5 P2-1).
        #
        # Validate every configured provider's api_key_env at startup,
        # before resolve() picks the active model. resolve() also checks
        # the active provider's key, but only that one. Validating ALL
        # configured providers up front catches the "user switched
        # providers and forgot to update .env" case AND the "user has
        # a fallback provider configured but its key is missing" case.
        # If you don't intend to use a provider, comment it out.
        for provider_name, provider in config.providers.items():
            if not provider.api_key_env:
                continue
            api_key = os.environ.get(provider.api_key_env, "").strip()
            if not api_key:
                raise ConfigError(
                    f"Missing API key: env var '{provider.api_key_env}' "
                    f"(referenced by provider '{provider_name}' in "
                    f"config.yaml) is not set or is empty"
                )
            register_secret(api_key)
        resolved = resolve(config, agent_spec.model)
    except (ConfigError, NotImplementedError) as e:
        log.critical("Startup failed: %s", e)
        return 1

    log.info(
        "Agent starting: provider=%s model=%s connector=telegram",
        resolved.provider_name, resolved.model_id,
    )
    try:
        memory = Memory(default_db_path())
    except sqlite3.OperationalError as e:
        log.critical("Memory init failed at %s: %s", default_db_path(), e)
        return 1

    try:
        connector_run(config, resolve, memory)
    except (KeyboardInterrupt, SystemExit):
        log.info("Agent stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
