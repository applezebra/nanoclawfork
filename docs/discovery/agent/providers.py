"""Provider registry — the heart of LLM agnosticism.

A provider is anything that speaks an HTTP API to an LLM. We treat
OpenAI-compatible, Anthropic, Ollama, etc. as equal-class. The registry
is loaded from config.yaml and exposes a uniform `get_model(...)` interface
that PydanticAI consumes.

No provider is the "default." There is no Anthropic in the call path
unless the user configures it as one provider among many.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.anthropic import AnthropicProvider


@dataclass(frozen=True)
class ProviderConfig:
    """Single provider entry from config.yaml."""

    name: str                    # registry key, e.g. "deepinfra"
    kind: str                    # "openai_compatible" | "anthropic" | "ollama"
    base_url: str | None
    api_key_env: str | None      # env var holding the secret — never the secret itself
    allowed_models: list[str]    # allowlist; refuse unknown models


class ProviderRegistry:
    """Loads providers from config.yaml and resolves model strings to Model instances."""

    def __init__(self, providers: dict[str, ProviderConfig]) -> None:
        self._providers = providers

    @classmethod
    def from_yaml(cls, path: Path) -> ProviderRegistry:
        raw = yaml.safe_load(path.read_text())
        providers: dict[str, ProviderConfig] = {}
        for name, cfg in (raw.get("providers") or {}).items():
            providers[name] = ProviderConfig(
                name=name,
                kind=cfg["kind"],
                base_url=cfg.get("base_url"),
                api_key_env=cfg.get("api_key_env"),
                allowed_models=list(cfg.get("allowed_models") or []),
            )
        return cls(providers)

    def get_model(self, ref: str) -> Model:
        """Resolve a 'provider/model' reference to a PydanticAI Model.

        Example: 'deepinfra/meta-llama/Llama-3.3-70B-Instruct'
        """
        provider_name, _, model_name = ref.partition("/")
        if not model_name:
            raise ValueError(f"Model ref must be 'provider/model', got: {ref!r}")

        provider = self._providers.get(provider_name)
        if provider is None:
            raise KeyError(f"Unknown provider: {provider_name!r}")

        if provider.allowed_models and model_name not in provider.allowed_models:
            raise PermissionError(
                f"Model {model_name!r} not in allowlist for provider {provider_name!r}. "
                f"Add it to config.yaml under providers.{provider_name}.allowed_models."
            )

        api_key = self._read_secret(provider.api_key_env) if provider.api_key_env else None

        if provider.kind == "openai_compatible":
            return OpenAIModel(
                model_name,
                provider=OpenAIProvider(base_url=provider.base_url, api_key=api_key),
            )
        if provider.kind == "anthropic":
            return AnthropicModel(
                model_name,
                provider=AnthropicProvider(api_key=api_key),
            )
        raise ValueError(f"Unknown provider kind: {provider.kind!r}")

    @staticmethod
    def _read_secret(env_var: str) -> str:
        import os
        val = os.environ.get(env_var)
        if not val:
            raise RuntimeError(f"Required secret {env_var} not set in environment")
        return val
