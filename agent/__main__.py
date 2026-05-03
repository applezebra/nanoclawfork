"""CLI entrypoint — wires Config + Memory + Connector.run.

Invoked as `python -m agent` or via the `agent` console script (declared in
pyproject.toml). Inside the container, entrypoint.sh calls `exec agent`.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from agent.config import ConfigError, load_config
from agent.connectors.telegram import run as connector_run
from agent.logging import get_logger
from agent.memory import Memory, default_db_path
from agent.registry import resolve

_DEFAULT_CONFIG_PATH = Path("/config/config.yaml")


def main(config_path: Path = _DEFAULT_CONFIG_PATH) -> int:
    """Compose the agent and start the polling loop. Returns exit code.

    SYNCHRONOUS connector_run (eng-review P1-2): pgttb 22.x's run_polling
    owns its event loop; asyncio.run wrapping would crash.
    """
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
