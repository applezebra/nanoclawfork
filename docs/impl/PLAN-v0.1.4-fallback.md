# PLAN — v0.1.4 provider/model fallback

References: SPEC-v0.1.4-fallback.md, Trello #40
Status: draft, awaiting /plan-eng-review

## Summary of approach

Add a thin wrapper around `agent.runtime.reply()` that takes a list of `ResolvedProvider` instances (primary + fallbacks). Iterate them in order. On each exception from `reply()`, log the failure and try the next. On exhaustion, raise a new `AllProvidersFailed` exception that the connector translates into the user-visible "having trouble" message.

The new function lives in agent/runtime.py beside `reply()`. The existing `reply()` is unchanged.

Config: add an optional top-level `fallback: list[str]` to `Config`. Resolve each entry to a `ResolvedProvider` at startup using the existing `resolve()` from agent.registry. The 0.1.3 startup-key validation already covers each provider's api_key_env presence.

The connector code change is one line: pass `[primary] + fallbacks` to the new wrapper instead of calling `reply()` directly.

## Architecture

```
config.yaml
  providers: {openrouter, groq, deepinfra}
  agents.personal-assistant.model: openrouter/llama
  fallback: [groq/llama, deepinfra/llama]   # NEW

  ↓ load_config()

Config object
  .agents["personal-assistant"].model = "openrouter/llama"
  .fallback = ["groq/llama", "deepinfra/llama"]   # NEW

  ↓ __main__.main()

resolved_primary = resolve(config, agent_spec.model)
resolved_fallbacks = [resolve(config, ref) for ref in config.fallback]   # NEW

  ↓ connector_run(config, resolve, memory)

  ↓ telegram.handle_message()

reply_text = await reply_with_fallback(   # NEW (was: reply(...))
    primary=resolved_primary,
    fallbacks=resolved_fallbacks,
    system_prompt=...,
    history=...,
    user_text=...,
    chat_id=...,
)
```

## Step-by-step implementation

### Step 1 — Config schema for `fallback`

Files: `agent/config.py`
LOC budget: ~50

- Add `fallback: list[str] = []` to the `Config` Pydantic model with a field validator that:
  - Each entry is a non-empty string of the form `<provider>/<model-id>` (the resolver will do deeper validation, this is just shape-level).
  - The list is allowed to be empty or missing.
- Add a model-level validator (post `agents` and `fallback`):
  - Length cap: `len(fallback) <= 5`. ConfigError if exceeded.
  - No duplicates within `fallback` itself. ConfigError naming the dupe.
  - For each agent that has a `model`, no entry in `fallback` may equal that agent's model. ConfigError naming the conflicting entry.
- No change to `load_config()` other than letting Pydantic populate the new field.

### Step 2 — Startup resolution + validation

Files: `agent/__main__.py`
LOC budget: ~15

- After resolving the primary, iterate `config.fallback` and call `resolve(config, ref)` for each. Collect into `resolved_fallbacks: list[ResolvedProvider]`.
- Any `ConfigError` from `resolve()` propagates out; existing `except (ConfigError, NotImplementedError)` block already turns it into CRITICAL log + exit 1 with a clear message.
- Pass `resolved_fallbacks` into the connector's run signature (Step 3).

### Step 3 — `reply_with_fallback` function

Files: `agent/runtime.py`, `tests/test_l2_runtime.py`
LOC budget: ~80 (function + 5 tests)

- New module-level exception `AllProvidersFailed(Exception)` with `attempts: list[tuple[str, str, BaseException]]` summarizing each (provider, model, error) tried.
- New async function:

```python
async def reply_with_fallback(
    primary: ResolvedProvider,
    fallbacks: list[ResolvedProvider],
    system_prompt: str,
    history: list[Turn],
    user_text: str,
    chat_id: int,
) -> str:
```

  - **AC-1 short-circuit**: if `fallbacks == []`, return `await reply(primary, ...)` directly. No try/except, no AllProvidersFailed wrapping. The empty-fallback path is byte-for-byte identical to v0.1.3.
  - Otherwise iterates `[primary, *fallbacks]`.
  - Wraps each call in try/except.
  - On success: returns immediately.
  - On exception: log INFO with `from-provider/model -> next-provider/model: ExceptionClassName`. NO str(exc) — secret-leak hygiene. Continue.
  - On exhaustion: raise `AllProvidersFailed(attempts=...)` where `attempts: list[tuple[provider_name, model_id, ExceptionClassName_str]]`. Log CRITICAL with the chain summary `provider1/model1: Class1; provider2/model2: Class2; ...` BEFORE raising. The original exceptions are preserved in `attempts` for debugging via `__cause__` chaining; CRITICAL log line carries class names only.

- Tests T1-T7 from spec + applied fixes, in tests/test_l2_runtime.py:
  - T1 primary succeeds → no fallback consulted (use AsyncMock to assert).
  - T2 primary raises → first fallback called, returns its reply.
  - T3 primary + first fallback raise → second fallback called.
  - T4 all raise → AllProvidersFailed raised, attempts populated correctly, CRITICAL log present.
  - T5 log content asserts (provider, model, error class on each step). Specifically: assert that str(exc) does NOT appear in the log lines.
  - T6 (NEW from P1 fix) empty fallback list + primary raises → reply()'s exception bubbles up as-is, no AllProvidersFailed, no "having trouble" message synthesis.
  - T7 (NEW) AllProvidersFailed exposes attempts list with provider, model, exception class name for each attempt.

### Step 4 — Connector wiring

Files: `agent/connectors/telegram.py`
LOC budget: ~25

- The `run()` signature gains `fallbacks: list[ResolvedProvider]` parameter.
- In the message-handling path, replace the direct `await reply(provider, ...)` with `await reply_with_fallback(provider, fallbacks, ...)`.
- On `AllProvidersFailed` exception: send the user-visible message "Having trouble reaching the LLM right now, please try again in a minute." Do not re-raise to PTB (suppress, like the existing error path does for misc exceptions).

### Step 5 — Wire `fallbacks` through `__main__.connector_run()`

Files: `agent/__main__.py`, `agent/connectors/telegram.py`
LOC budget: ~10

- `connector_run(config, resolve, memory, fallbacks)` instead of `connector_run(config, resolve, memory)`.
- In `__main__.main()`, pass the resolved fallback list.

### Step 6 — Config fixture tests

Files: `tests/test_l1_config_registry.py`
LOC budget: ~50 (4 tests)

- T-cfg-1 empty/missing `fallback` loads cleanly.
- T-cfg-2 non-list `fallback` raises ValidationError.
- T-cfg-3 fallback referencing unknown provider raises ConfigError at resolve time. (Tested in test_l5_container.py since resolve happens in __main__.)
- T-cfg-4 fallback referencing disallowed model raises ConfigError. Same.
- T-cfg-5 (NEW from P2 cycle fix) fallback list contains an entry equal to an agent's model → ConfigError naming the conflict.
- T-cfg-6 (NEW from P2 dedupe fix) fallback list with internal duplicates → ConfigError naming the dupe.
- T-cfg-7 (NEW from P2 cap fix) fallback list of length 6 → ConfigError mentioning the cap.

### Step 7 — End-to-end smoke test (manual)

Files: `config.test.yaml` (gitignored, edit only)
LOC budget: 0 (manual)

- Set primary to an obviously broken provider (bogus base_url).
- Set fallback to OpenRouter (working).
- Run docker compose -f docker-compose.test.yaml --env-file .env.test up.
- Send a Telegram message.
- Bot should reply via the fallback. Log should show the fallback INFO line.
- Tear down.

### Step 8 — Documentation

Files: `README.md`, `config.example.yaml`, `CHANGELOG.md`
LOC budget: ~30 (docs)

- README: small new section under "Switching providers" titled "Fallback chain", showing the optional `fallback:` block in config.yaml.
- config.example.yaml: a commented example `fallback:` block at the bottom.
- CHANGELOG: 0.1.4 entry "Added: optional `fallback:` config field…"

### Step 9 — Version bump

Files: `pyproject.toml`, `agent/__about__.py`
LOC budget: ~2

- 0.1.3 → 0.1.4 in both files.

## Total LOC estimate

~237 effective LOC across runtime, config, tests, and docs. Within the 300-LOC step budget if we count the whole change as one logical step. If we hit the 600-LOC test-runner trigger, we run pytest. We will run pytest at the end of step 6 anyway.

## Plan-eng-review findings (codex, applied 2026-05-08)

[P1] AC-1 violation: with empty fallback, the new flow still routes through reply_with_fallback and translates failures into the new "Having trouble" user message, which is a behavior change vs v0.1.3.
APPLIED FIX: in Step 3, reply_with_fallback short-circuits when `fallbacks == []`: it calls `await reply(primary, ...)` directly and lets the exception propagate. No AllProvidersFailed wrapping when there is nothing to fall back to. Step 4 connector code only emits the "Having trouble" message on AllProvidersFailed, which by definition only fires when fallbacks were configured AND all of them failed. Empty-fallback path is byte-for-byte identical to v0.1.3.

[P2] Degenerate cycle: operator could put the primary's `<provider>/<model-id>` in the fallback list and waste cost retrying the same broken endpoint.
APPLIED FIX: in Step 1 config validation, raise ConfigError if any fallback entry equals `agents.<group>.model` for any agent that uses it. Also raise if duplicates exist within the fallback list.

[P2] Unbounded chain length: huge fallback lists could blow up per-request cost or hit rate-limits cumulatively.
APPLIED FIX: in Step 1 config validation, cap fallback length at 5. Operator can override with `fallback_max_length: N` if they really need more, but the default cap is opinionated. ConfigError if exceeded without override.

[P2] Secret leak risk in CRITICAL log on full failure: full exception str() can leak request bodies, including api keys or user content.
APPLIED FIX: in Step 3 logging, the AllProvidersFailed CRITICAL log line only emits `provider/model: ExceptionClassName` per attempt. No `str(exc)` in the chain summary. The per-step INFO logs already do the same. Stack traces still go through the existing scrubbing logger via `exc_info=True` (the L0 secret scrubber strips known patterns).

## Risk register

R1. pydantic-ai's `agent.run()` exception model. The current `reply()` only raises bare `Exception` from inside the OpenAI SDK. We rely on this being a single catch-all. Mitigation: keep the `except Exception` broad, log `exc_info=True` so we never lose stack traces.

R2. The user-visible "having trouble" message is an English string. Acceptable for v0.1.x (English-only). Internationalisation deferred.

R3. Order matters. The fallback list is "primary, then fallbacks in order." Document this prominently in README and config.example.yaml comments.

R4. Cost. Each fallback attempt is a billable LLM call. Document this in README ("if your primary fails, you pay for the next attempt"). No automatic limit beyond the chain length.

R5. Connector signature change is a breaking internal API. No external consumers exist (telegram is the only connector). Safe.

## Out of plan (will not change)

- agent.runtime.reply() function unchanged. The new wrapper sits beside it. Easier to revert if needed.
- agent.registry.resolve() unchanged. We reuse it.
- agent.config.Config schema gains one optional field. Existing configs are forward-compatible.
- Telegram allowlist code unchanged.
- Memory layer unchanged. Fallback only affects the LLM call, not what gets persisted.

## Verification gates (per CLAUDE.md SDLC)

Before this plan goes to code:
- [ ] /plan-eng-review on this file (mandatory)
- [ ] Anson approval

Before commit:
- [ ] /codex-review on the staged diff
- [ ] All unit tests pass
- [ ] Smoke test E1 verified via the existing test harness

Before merge:
- [ ] PR opened, Anson self-merge

After merge:
- [ ] Tag v0.1.4
- [ ] GitHub Release with the CHANGELOG body
- [ ] kayaclaw-site hero badge bump
- [ ] Daily ship post drafts to GDoc
