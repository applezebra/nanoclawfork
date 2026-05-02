"""Provider registry resolver.

Resolves a '<provider>/<model-id>' reference against the loaded Config and
returns a ResolvedProvider dataclass ready for the runtime to use.

Raises ConfigError for any misconfiguration, NotImplementedError for provider
kinds accepted by the schema but not yet supported at runtime (anthropic in 0.1).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from agent.config import Config, ConfigError
from agent.logging import get_logger

_log = get_logger("agent.registry")


@dataclass(frozen=True)
class ResolvedProvider:
    """Fully resolved provider details for a single model reference."""

    provider_name: str   # registry key, e.g. "deepinfra" — needed by L2/L4 log lines
    kind: str            # "openai_compatible" or "anthropic"
    base_url: str | None
    api_key: str | None  # actual secret value read from env at resolve time
    model_id: str        # model portion of the ref, e.g. "meta-llama/Llama-3.3-70B-Instruct"


def resolve(config: Config, model_ref: str) -> ResolvedProvider:
    """Resolve a 'provider/model-id' reference against config.

    Steps (in order):
      1. Split model_ref on first '/' → (provider_name, model_id). Empty part → ConfigError.
      2. Look up provider_name in config.providers. Missing → ConfigError.
      3. Allowed-models check (deny-default): ["*"] passes all; [] denies all; otherwise exact match.
      4. Read api_key from env if api_key_env is set. Absent/empty → ConfigError.
      5. Anthropic kind → NotImplementedError (fires after step 4, per T2 ordering).
      6. Log at DEBUG (no api_key value).
      7. Return ResolvedProvider.
    """
    # Step 1 — split on first '/'
    parts = model_ref.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ConfigError(
            f"Invalid model_ref {model_ref!r}: expected '<provider>/<model-id>' with both parts non-empty."
        )
    provider_name, model_id = parts[0], parts[1]

    # Step 2 — provider lookup
    provider = config.providers.get(provider_name)
    if provider is None:
        raise ConfigError(
            f"Unknown provider {provider_name!r}. Defined providers: {list(config.providers.keys())}"
        )

    # Step 3 — allowed-models check (A2 deny-default)
    allowed = provider.allowed_models
    if "*" not in allowed and model_id not in allowed:
        raise ConfigError(
            f"Model {model_id!r} is not permitted for provider {provider_name!r}. "
            f"Allowed models: {allowed if allowed else '(none — empty list denies all)'}"
        )

    # Step 4 — API key resolution
    api_key: str | None = None
    if provider.api_key_env:
        api_key = os.environ.get(provider.api_key_env)
        if not api_key:
            raise ConfigError(
                f"Environment variable {provider.api_key_env!r} is not set or empty. "
                f"Set it to the API key for provider {provider_name!r}."
            )

    # Step 5 — anthropic not implemented in 0.1 (fires after step 4 per T2)
    if provider.kind == "anthropic":
        raise NotImplementedError(
            "Anthropic provider kind is accepted by the schema but runtime support is not "
            "implemented in 0.1. Configure an openai_compatible provider instead."
        )

    # Step 6 — debug log (no api_key value)
    _log.debug("Resolved %s/%s → kind=%s", provider_name, model_id, provider.kind)

    # Step 7 — return
    return ResolvedProvider(
        provider_name=provider_name,
        kind=provider.kind,
        base_url=provider.base_url,
        api_key=api_key,
        model_id=model_id,
    )
