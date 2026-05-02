"""Agent runtime — wraps PydanticAI with the provider registry.

PydanticAI is protocol-agnostic by design: a Model is just an object
that can be invoked. We hand it whichever Model the registry resolves
from the agent group's configured provider/model string.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic_ai import Agent

from agent.providers import ProviderRegistry


@dataclass(frozen=True)
class AgentGroupConfig:
    name: str
    model: str               # 'provider/model' ref, e.g. 'deepinfra/meta-llama/Llama-3.3-70B-Instruct'
    system_prompt: str
    tools: list[str]         # names of registered tools (resolved elsewhere)


def load_agent_group(config_path: Path, group_name: str) -> AgentGroupConfig:
    raw = yaml.safe_load(config_path.read_text())
    groups = raw.get("agents") or {}
    if group_name not in groups:
        raise KeyError(f"Agent group {group_name!r} not in config")
    g = groups[group_name]
    return AgentGroupConfig(
        name=group_name,
        model=g["model"],
        system_prompt=g.get("system_prompt", ""),
        tools=list(g.get("tools") or []),
    )


def build_agent(config_path: Path, group_name: str) -> Agent:
    """Build a runnable PydanticAI Agent from config."""
    registry = ProviderRegistry.from_yaml(config_path)
    group = load_agent_group(config_path, group_name)
    model = registry.get_model(group.model)

    return Agent(
        model=model,
        system_prompt=group.system_prompt,
        # tools wired in stage 2 from the tool registry
    )
