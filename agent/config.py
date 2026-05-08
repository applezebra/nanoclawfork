"""Config schema and loader.

Defines Pydantic v2 models for config.yaml and a load_config() function that
parses and validates the file, raising ConfigError on any problem.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from agent.logging import get_logger

_log = get_logger("agent.config")


class ConfigError(Exception):
    """Raised when config.yaml is missing, malformed, or fails validation."""


def _format_validation_error(ve: ValidationError) -> str:
    lines = ["Invalid config:"]
    for err in ve.errors():
        loc = ".".join(str(p) for p in err["loc"])
        lines.append(f"  {loc}: {err['msg']}")
    return "\n".join(lines)


class ProviderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["openai_compatible", "anthropic"]
    base_url: str | None = None
    api_key_env: str | None = None
    # Empty list = DENY all models. ["*"] = any model allowed. Otherwise exact match required.
    allowed_models: list[str] = []

    @model_validator(mode="after")
    def _base_url_required_for_openai_compatible(self) -> "ProviderSpec":
        if self.kind == "openai_compatible" and not self.base_url:
            raise ValueError("base_url is required when kind is openai_compatible")
        return self


class AgentGroupSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    system_prompt: str
    connectors: list[str] = []

    @field_validator("model")
    @classmethod
    def _model_must_have_slash(cls, v: str) -> str:
        parts = v.split("/", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError('model must be in "<provider>/<model-id>" format')
        return v


FALLBACK_MAX_LENGTH = 5


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: dict[str, ProviderSpec]
    agents: dict[str, AgentGroupSpec]
    # Optional per-deployment fallback chain. Each entry is a "<provider>/<model-id>"
    # reference tried in declared order if the active agent's primary call fails.
    # Empty / missing = no fallback (v0.1.3 behavior).
    fallback: list[str] = []

    @field_validator("fallback")
    @classmethod
    def _fallback_entries_have_slash(cls, v: list[str]) -> list[str]:
        for entry in v:
            parts = entry.split("/", 1)
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise ValueError(
                    f'fallback entry {entry!r} must be in "<provider>/<model-id>" format'
                )
        return v

    @model_validator(mode="after")
    def _fallback_invariants(self) -> "Config":
        if len(self.fallback) > FALLBACK_MAX_LENGTH:
            raise ValueError(
                f"fallback list length {len(self.fallback)} exceeds the cap "
                f"of {FALLBACK_MAX_LENGTH}; if you need a longer chain, "
                f"reconsider whether the providers are right"
            )
        seen: set[str] = set()
        for entry in self.fallback:
            if entry in seen:
                raise ValueError(
                    f"fallback list contains duplicate entry {entry!r}; "
                    f"each provider/model reference must appear at most once"
                )
            seen.add(entry)
        agent_models = {spec.model for spec in self.agents.values()}
        cycle = seen & agent_models
        if cycle:
            offending = sorted(cycle)[0]
            raise ValueError(
                f"fallback entry {offending!r} duplicates an agent's primary model; "
                f"a fallback to the same provider/model retries the failing call "
                f"and wastes cost"
            )
        return self


def load_config(path: Path) -> Config:
    """Read, parse, and validate config.yaml. Raises ConfigError on any failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise ConfigError(f"Config file not found: {path}") from e
    except OSError as e:
        raise ConfigError(f"Could not read config file {path}: {e}") from e
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigError(f"Malformed YAML in {path}: {e}") from e
    try:
        config = Config.model_validate(raw)
    except ValidationError as ve:
        raise ConfigError(_format_validation_error(ve)) from ve
    _log.info("Config loaded: %d providers, %d agent groups", len(config.providers), len(config.agents))
    return config
