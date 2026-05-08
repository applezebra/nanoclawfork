# SPEC v0.1.4 — Provider/model fallback

Status: draft, awaiting plan + eng review
Trello: #40

## Problem

If the configured provider's call fails (HTTP error, timeout, network blip, rate limit, out-of-credit), the agent today returns nothing usable to the user. The single point of failure is one bad day at one provider.

Operators need a way to declare a fallback chain so the bot keeps replying when the primary fails.

## User-visible behavior

1. Operator declares a list of fallback `<provider>/<model-id>` references in config.yaml.
2. On a normal request, the agent tries the primary model (`agents.<group>.model`) first.
3. If that call fails for any reason (any non-2xx, exception, timeout), the agent tries the next reference in the fallback list, in declared order.
4. The agent keeps trying until one succeeds. The successful reply is sent to the user as if nothing had happened.
5. Each fallback attempt logs at INFO with the failing reference, the next reference, and the error class. Successful failover logs at INFO. The bot reply text is unchanged (no "(via fallback)" banner).
6. If every reference in the chain fails, the agent sends a single user-visible message: "Having trouble reaching the LLM right now, please try again in a minute." A CRITICAL log line records the full chain of failures.

## Non-goals (explicit)

- Provider-internal fallback (e.g. OpenRouter's `models` array). That belongs to a separate generic mechanism (Trello #49) and ships in a later release.
- Same-provider retry before switching. v0.1.4 goes straight to the next provider on first failure.
- Cost-aware routing (cheapest first). Not in scope.
- Detection of specific error classes (rate-limit-only fallback, etc.). All failures trigger fallback uniformly.
- Per-agent fallback chains. v0.1.4 has one global chain in config.yaml.
- Async / concurrent attempts. v0.1.4 is sequential.

## Configuration

Single new top-level field in config.yaml: `fallback`. List of `<provider>/<model-id>` strings.

```yaml
providers:
  openrouter:
    kind: openai_compatible
    base_url: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY
    allowed_models:
      - meta-llama/llama-3.3-70b-instruct
  groq:
    kind: openai_compatible
    base_url: https://api.groq.com/openai/v1
    api_key_env: GROQ_API_KEY
    allowed_models:
      - llama-3.3-70b-versatile

agents:
  personal-assistant:
    model: openrouter/meta-llama/llama-3.3-70b-instruct
    system_prompt: "You are a helpful assistant."
    connectors:
      - telegram

# Optional. If omitted or empty, behaviour is unchanged from 0.1.3.
fallback:
  - groq/llama-3.3-70b-versatile
```

Each entry in `fallback` must:
- Be the form `<provider>/<model-id>` with both parts non-empty.
- Reference a provider that exists in `providers`.
- Reference a model the provider's `allowed_models` permits (subject to the existing wildcard rule).
- The provider's `api_key_env` must be set in the environment at startup (validated by the existing v0.1.3 startup-key check, no new code).

If any entry fails validation at startup, the agent exits with a clear ConfigError naming the offending entry. (Same pattern as the v0.1.3 startup validation.)

## Acceptance criteria

AC-1. With `fallback` empty or absent, behavior matches 0.1.3 byte-for-byte. No regression in existing tests.

AC-2. With a single fallback entry, when the primary call raises any exception, the next entry is tried. The user receives the second provider's reply. A log line at INFO records the swap.

AC-3. With multiple fallback entries, the agent tries them in declared order and stops on the first success.

AC-4. When all entries fail, the user receives the configured "having trouble" message and the agent logs a CRITICAL line summarising the full chain.

AC-5. Validation errors at startup (unknown provider in fallback, disallowed model, missing api_key_env) cause exit code 1 with a ConfigError naming the offending entry. No tool ever runs against a misconfigured fallback chain.

AC-6. The fallback list is not iterated for failures unrelated to the LLM call (e.g., Telegram delivery error, allowlist rejection). Fallback only applies to the model.request() call.

## Test plan

Unit tests (`tests/test_l2_runtime.py` extension):
- T1. Primary succeeds → no fallback consulted.
- T2. Primary raises → first fallback called, succeeds → user sees its reply.
- T3. Primary + first fallback both raise → second fallback called.
- T4. All references raise → user-visible "having trouble" message and CRITICAL log.
- T5. Logs name the failing reference, the next reference, and a one-line summary on full failure.

Unit tests (`tests/test_l1_config_registry.py` extension):
- T6. Empty/missing `fallback` field loads cleanly.
- T7. Non-list `fallback` raises ConfigError.
- T8. Fallback entry referencing unknown provider raises ConfigError.
- T9. Fallback entry referencing disallowed model raises ConfigError.

End-to-end via Telegram smoke test (existing harness):
- E1. Configure primary as a deliberately broken provider (bogus base_url) + valid fallback. Send a message. Bot replies via fallback. Logs show the failover.

## Out of scope (carry to backlog)

- Provider-internal `models` array support (Trello #49)
- Balance/credits aware routing (Trello #41)
- Per-agent fallback chains
- Fallback observability metrics

## Open questions

- Does pydantic-ai's Agent expose a clean error path so we can catch model.request failures without unwrapping internal exceptions?  (Plan-stage investigation.)
- Does fallback need to thread through tool-calling responses, or only first-turn replies?  v0.1.x is reply-only, so first-turn reply only is fine. Lock to "first-turn reply" for v0.1.4.
