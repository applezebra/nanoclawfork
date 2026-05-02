# Implementation Plan — L1: Config Schema + Provider Registry

**Lane:** L1
**Version:** 0.1
**Status:** `/plan-eng-review` complete (2026-05-02) — 8 fixes applied (A1, A2, A3, C1, T1, T2, T3, P2-4). Ready for code-implementer.

## Revision log

- **2026-05-02 (post-eng-review):** Applied A1 (specify `@model_validator(mode="after")` for base_url+kind cross-field rule), A2 (flipped `allowed_models: []` semantics — empty list now means DENY all; use `["*"]` for any-model), A3 (added `model_config = ConfigDict(extra="forbid")` to all 3 models so typos fail loudly), C1 (specified ConfigError message construction via `e.errors()` iteration), T1 (replaced fragile yaml.load monkeypatch test with source-grep check), T2 (added explicit test that Anthropic raises NotImplementedError even when ANTHROPIC_API_KEY is set), T3 (added typo'd-field test for extra="forbid"), P2-4 (added `provider_name: str` field to `ResolvedProvider` — needed by L2/L4 log lines).
**Depends on:** L0 (importable `agent` package, `get_logger`)
**Blocks:** L2 (runtime needs `ResolvedProvider`), L4 (connector needs `Config`)
**Can run in parallel with:** L3, L5 (Dockerfile draft)

---

## Mandatory gate before code-implementer begins

`/plan-eng-review` on this plan file is required before code-implementer begins. This lane handles secret loading (API key from env) and a config schema that controls which providers and models the agent is allowed to call. These are security-adjacent surfaces: a plan-stage review catches issues before they become code-stage debt.

---

## 1. Lane Summary

L1 loads `config.yaml`, validates it with Pydantic v2 models, and exposes a typed `resolve()` function that the runtime uses to get a fully-resolved provider tuple for any `"provider/model-id"` reference. It also ships `config.example.yaml` documenting every key.

**LOC budget:**
- `agent/config.py` (Pydantic schema + loader): 60 target / 100 hard cap
- `agent/registry.py` (resolver): 80 target / 150 hard cap
- Combined target: ~140 effective LOC. Combined hard cap: 250 (but tracked per-module separately against CONTRACT caps).

---

## 2. WHAT — Artifacts to Produce

| Artifact | Path |
|---|---|
| Pydantic config schema + loader | `agent/config.py` |
| Provider registry resolver | `agent/registry.py` |
| Documented example config | `config.example.yaml` |
| Tests | `tests/test_l1_config_registry.py` |

---

## 3. WHY — Rationale per Artifact

- **`agent/config.py`** — FR-R1 requires loading provider configuration from `config.yaml` at startup. CONTRACT resolved decision "Misconfig at startup → crash loudly with clear error" means the loader must validate the YAML against a strict schema and raise a clear exception (not a cryptic `KeyError`) when required fields are missing. Pydantic v2 is the right tool: it is already in the dependency list (used by PydanticAI), it provides clear field-level error messages, and it adds zero new dependencies.

- **`agent/registry.py`** — FR-R3 requires that adding a new provider requires only a `config.yaml` edit, no code changes. The registry resolver enforces this by being the single place that maps provider kind to the PydanticAI model constructor. FR-R4 requires each provider definition to declare kind, base URL, API key env var, and allowed models — the registry enforces the allowed-models list at resolve time.

- **`config.example.yaml`** — FR-C1: "The system must ship a `config.example.yaml` that documents every supported key with inline comments." This is the operator's complete reference. The live `config.yaml` is gitignored; the example is the committed template.

---

## 4. HOW — Tiny Step Breakdown

### Step 1 — Pydantic config schema and loader

**Files touched (≤3):**
1. `agent/config.py`
2. `config.example.yaml`
3. `tests/test_l1_config_registry.py`

**LOC estimate:** ~60 effective LOC in `agent/config.py` (at the target cap). `config.example.yaml` is a data file, not counted.

**What to write:**

`agent/config.py` — define the following Pydantic v2 `BaseModel` classes:

`ProviderSpec` — represents one entry in `config.yaml`'s `providers:` map:
- `kind: Literal["openai_compatible", "anthropic"]` — only these two are valid in 0.1 schema. Other strings fail validation at load time.
- `base_url: str | None` — required for `openai_compatible`, optional for `anthropic` (which defaults to the Anthropic endpoint). Cross-field rule enforced via `@model_validator(mode="after")` (eng-review A1) — Pydantic v2 idiom for rules that depend on multiple fields. Field-level validators cannot see other fields; model-level validators run after all fields are individually validated.
- `api_key_env: str | None` — the name of the environment variable that holds the API key. Nullable for local providers (Ollama in the future, not 0.1). This is the variable NAME, not the value — the value is read by the registry at resolve time.
- `allowed_models: list[str]` — the models this provider is permitted to serve. **Empty list = DENY all models** (operator must explicitly list each permitted model). To allow any model from this provider without enumeration, use the literal entry `"*"` (eng-review A2: deny-by-default semantics — flipped from the previous "empty = allow all" because that was a security footgun where an operator who typed `allowed_models: []` thinking they were disabling actually enabled everything).
- `model_config = ConfigDict(extra="forbid")` — eng-review A3. Unknown fields fail validation rather than silently ignored. Catches typos like `kindd:` or `base-url:` at load time with a message naming the unknown field.

`AgentGroupSpec` — represents one entry in `config.yaml`'s `agents:` map:
- `model: str` — the form `"<provider>/<model-id>"`. The slash is mandatory; a validator splits on the first `/` and checks that both parts are non-empty.
- `system_prompt: str` — the system prompt for this agent group. No validation beyond "non-empty string."
- `connectors: list[str]` — list of connector names (e.g. `["telegram"]`). In 0.1 only `"telegram"` is valid, but the schema does not enforce this — validation would be premature coupling to the connector layer.
- `model_config = ConfigDict(extra="forbid")` — eng-review A3.

`Config` — the top-level model:
- `providers: dict[str, ProviderSpec]` — keyed by provider name (e.g. `"deepinfra"`).
- `agents: dict[str, AgentGroupSpec]` — keyed by agent group name (e.g. `"personal-assistant"`).
- `model_config = ConfigDict(extra="forbid")` — eng-review A3.

`ConfigError` — a plain `Exception` subclass. Used by both `config.py` and `registry.py` to signal configuration problems. Defined here so both modules can raise it without a circular import.

`load_config(path: Path) -> Config` — reads and parses the YAML file:
1. Read the file with `path.read_text(encoding="utf-8")`.
2. Parse with `yaml.safe_load()` — never `yaml.load()` (unsafe deserializer).
3. Pass the dict to `Config.model_validate()`.
4. Wrap Pydantic's `ValidationError` in a `ConfigError` with a human-readable message naming the offending field (eng-review C1). Concretely: iterate `validation_error.errors()` (returns list of dicts with `loc`, `msg`, `type`), format each as `f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"`, join with newlines, prefix with `"Invalid config:\n"`. The goal: an operator who has never seen Pydantic's error format can still diagnose the problem at a glance.
5. Log a startup message via `get_logger("agent.config")` at INFO level: `"Config loaded: {n} providers, {m} agent groups"` (no secrets in this log line).

`config.example.yaml` — document every key with inline YAML comments. Required content:
- `providers:` section with a `deepinfra:` entry showing `kind: openai_compatible`, `base_url: https://api.deepinfra.com/v1/openai`, `api_key_env: DEEPINFRA_API_KEY`, `allowed_models: [meta-llama/Llama-3.3-70B-Instruct]`.
- An `agents:` section with a `personal-assistant:` entry showing `model: deepinfra/meta-llama/Llama-3.3-70B-Instruct`, `system_prompt: "You are a helpful assistant."`, `connectors: [telegram]`.
- Inline comments explaining every field, including a note on the `provider/model-id` format.
- A comment block at the top stating that this file is the committed template and that the live `config.yaml` (gitignored) is derived from it.

**Tests to add:**

```
tests/test_l1_config_registry.py  (Step 1 portion)
```

- Load `config.example.yaml` via `load_config()` — assert returns a `Config` with at least one provider and one agent group.
- Pass a dict with a missing required field (e.g. no `kind` on a provider) to `Config.model_validate()` — assert raises `ConfigError` (not `ValidationError`).
- Pass `model: "deepinfra"` (no slash, no model-id) — assert raises `ConfigError` from the model-format validator.
- Pass a config with `kind: "ollama"` (not in the `Literal`) — assert raises `ConfigError`.
- **yaml.safe_load enforcement (eng-review T1):** assert via source-code grep, not behavior. Read `agent/config.py` as text and assert `re.search(r"\byaml\.load\b", source)` returns no match (allowing `yaml.safe_load`). Bulletproof against aliased imports that a behavioral monkeypatch would miss.
- **Typo'd field rejection (eng-review T3):** pass a config dict with a typo'd provider field (e.g. `{"deepinfra": {"kindd": "openai_compatible", ...}}`) — assert raises `ConfigError` whose message names the typo'd field name (`kindd`). Verifies `extra="forbid"` is in effect.
- **Empty allowed_models is DENY-all (eng-review A2):** load a config where a provider has `allowed_models: []`, attempt to resolve any model against it — assert raises `ConfigError` because no model is in the (empty) allowlist. Documents the deny-by-default semantics.

**Acceptance check before proceeding to Step 2:**
- `python -c "from agent.config import load_config; from pathlib import Path; c = load_config(Path('config.example.yaml')); print(c.providers)"` works without error.
- Step 1 tests all pass.

---

### Step 2 — Provider registry resolver

**Files touched (≤3):**
1. `agent/registry.py`
2. `tests/test_l1_config_registry.py` (extend the existing test file)

**LOC estimate:** ~80 effective LOC in `agent/registry.py` (at the target cap). Cumulative lane total: ~140 effective LOC — on target.

**What to write:**

`agent/registry.py` — implement `resolve(config: Config, model_ref: str) -> ResolvedProvider`:

`ResolvedProvider` — a frozen dataclass with fields:
- `provider_name: str` — the provider's registry key (e.g. `"deepinfra"`). **Eng-review P2-4 carry-forward**: L2/L4 log lines need this for structured logging like `provider=deepinfra model=Llama-3.3-70B`. Without it the kind alone is ambiguous (multiple providers can share `kind="openai_compatible"`).
- `kind: str` — `"openai_compatible"` or `"anthropic"` (matches `ProviderSpec.kind`)
- `base_url: str | None`
- `api_key: str | None` — the actual secret value read from env at resolve time
- `model_id: str` — just the model portion of the `provider/model-id` ref (e.g. `"meta-llama/Llama-3.3-70B-Instruct"`)

The resolver does the following, in order:

1. Split `model_ref` on the first `/` to get `(provider_name, model_id)`. If either part is empty, raise `ConfigError`.

2. Look up `provider_name` in `config.providers`. If not found, raise `ConfigError` naming the missing provider. This is the "crash loudly" contract behavior.

3. **Allowed-models check (eng-review A2 — deny by default):** check that `model_id` is in `provider.allowed_models`. The wildcard literal `"*"` in the list means "any model is allowed" (escape hatch for operators who explicitly opt into open-ended provider use). Empty list means deny all (no model passes). Anything not matching → raise `ConfigError` naming the model, the provider, and the allowed alternatives. Security control: prevents accidental routing to an unconfigured model AND prevents the empty-list footgun.

4. If `provider.api_key_env` is set, read `os.environ.get(provider.api_key_env)`. If the value is `None` or empty, raise `ConfigError` naming the missing environment variable. This is the "crash loudly" behavior for missing secrets — per CONTRACT resolved decisions: "Missing API key env var raises clear error naming the missing variable."

5. If `provider.kind == "anthropic"`, raise `NotImplementedError("Anthropic provider kind is accepted by the schema but runtime support is not implemented in 0.1. Configure an openai_compatible provider instead.")`. Per DECISIONS.md D1: registry validates eagerly at config-load time so misconfig fails fast at startup, not at first message. **Order matters (eng-review T2):** this check fires AFTER step 4 (api_key read) so the NotImplementedError is the FINAL outcome regardless of whether `ANTHROPIC_API_KEY` is set. Test must verify both states (key set, key unset) both raise NotImplementedError.

6. Log at DEBUG level: `"Resolved {provider_name}/{model_id} → kind={kind}"`. Do not log the API key value.

**Interface revision note:** The IMPACT-ANALYSIS declared the public interface as `resolve(config: Config, model_ref: str) -> ResolvedProvider`. This plan confirms that interface. The only addition is the explicit definition of `ResolvedProvider` as a frozen dataclass (not just a tuple), which makes the interface more readable and testable without adding LOC bloat.

**Tests to add (extend `tests/test_l1_config_registry.py`):**

- Happy path: load `config.example.yaml`, set `DEEPINFRA_API_KEY=test-key` in env, call `resolve(config, "deepinfra/meta-llama/Llama-3.3-70B-Instruct")` — assert returns a `ResolvedProvider` with `kind="openai_compatible"`, `model_id="meta-llama/Llama-3.3-70B-Instruct"`, `api_key="test-key"`.
- Unknown provider: call `resolve(config, "nonexistent/some-model")` — assert raises `ConfigError`.
- Disallowed model: call `resolve(config, "deepinfra/gpt-4o")` (not in `allowed_models`) — assert raises `ConfigError` whose message names the disallowed model.
- Missing API key: unset `DEEPINFRA_API_KEY` from env, call `resolve()` — assert raises `ConfigError` whose message names the env var.
- Assert the `ConfigError` message in each failure case is a non-empty human-readable string (not just the exception type).
- Anthropic kind: configure a provider with `kind: anthropic`, call `resolve()` against any model in that provider — assert raises `NotImplementedError` with a message naming "anthropic" and "0.1" (per D1).
- **Anthropic-with-key-set (eng-review T2):** same as above but ALSO set `ANTHROPIC_API_KEY=test-key` in env beforehand — assert STILL raises `NotImplementedError` (not a successful resolve). Verifies the runtime-not-implemented check fires regardless of whether the key is configured.
- **provider_name populated (eng-review P2-4):** in the happy-path test, also assert `result.provider_name == "deepinfra"`.

**Acceptance check before closing L1:**
- All Step 1 and Step 2 tests pass.
- `python -c "from agent.registry import resolve; from agent.config import load_config; from pathlib import Path; import os; os.environ['DEEPINFRA_API_KEY']='x'; c = load_config(Path('config.example.yaml')); r = resolve(c, 'deepinfra/meta-llama/Llama-3.3-70B-Instruct'); print(r.kind)"` prints `openai_compatible`.

---

## 5. Lane-Level Acceptance

L1 is closed when ALL of the following are true:

| Check | Maps to |
|---|---|
| `python -m pytest tests/test_l1_config_registry.py` passes with zero failures | Internal gate |
| `load_config(Path("config.example.yaml"))` succeeds | FR-R1, FR-C1 |
| Unknown provider in `model_ref` raises `ConfigError` with a named-variable message | CONTRACT resolved decision: crash loudly |
| Model not in `allowed_models` raises `ConfigError` | FR-R4 security enforcement |
| Missing API key env var raises `ConfigError` naming the variable | FR-C2, CONTRACT resolved decision |
| `agent/config.py` effective LOC ≤ 100 | CONTRACT §Size Discipline |
| `agent/registry.py` effective LOC ≤ 150 | CONTRACT §Size Discipline |
| `import anthropic` does not appear in `agent/config.py` or `agent/registry.py` | AC-4 checkpoint |

---

## 6. Anti-Bloat Callouts

**Do NOT implement Ollama or Groq resolution logic in `agent/registry.py`.** The schema accepts `kind: openai_compatible` for both DeepInfra and Ollama — any `openai_compatible` provider is resolved by the same code path. No Ollama-specific branch is needed. IMPACT-ANALYSIS R5: "Schema accepts `kind: ollama` but registry resolver implements `openai_compatible` only." Note: the schema's `Literal` field for `kind` includes only `openai_compatible` and `anthropic` in 0.1. If the user wants to add `kind: ollama`, that is a 0.2 schema change.

**Do NOT add config hot-reload.** CONTRACT resolved decision: restart required for any config change. Do not add a `ConfigWatcher`, `inotify`, or any periodic re-read mechanism.

**Do NOT add a provider abstraction class.** The `resolve()` function returns a plain `ResolvedProvider` dataclass. The runtime (`agent/runtime.py`) takes that dataclass and constructs the PydanticAI model. Adding a `Provider` abstract class here would be a premature abstraction with only one concrete use.

**Do NOT validate connector names in `AgentGroupSpec`.** The config schema should not know about connectors. Coupling `AgentGroupSpec.connectors` to `["telegram"]` validation would be a cross-lane dependency that makes the schema harder to test independently.

**Do NOT catch and swallow `ConfigError` inside `load_config` or `resolve`.** Let it propagate to the caller. The CLI entrypoint (L5) is responsible for catching it, logging it, and exiting non-zero. This keeps the error visible rather than hidden.

---

## 7. Review Gates Checklist

In sequence, before closing this lane:

1. **`/plan-eng-review`** on this plan file — mandatory before code-implementer begins. (Cannot skip: secret loading is security-adjacent.)

2. **`code-reviewer` Pre-Test Gate** — after Step 1 is written (before Step 2). Check size constraints, obvious bugs, anti-bloat checklist. Specifically: verify no `yaml.load()` call, verify `ConfigError` wraps `ValidationError` properly.

3. **`/codex-review`** on the Step 1 staged diff — before committing. Cannot skip: `load_config` reads user-provided YAML and reads env vars. Injection and parsing risks belong in codex review.

4. **`code-reviewer` Pre-Test Gate** — after Step 2 is written. Check resolver logic, `ResolvedProvider` shape, error message quality.

5. **`/codex-review`** on the Step 2 staged diff — before committing. Cannot skip: `resolve()` reads `os.environ`; missing-var handling is a security-relevant code path.

6. **`/simplify`** — after both steps land. Target: confirm `agent/config.py` ≤ 60 effective LOC, `agent/registry.py` ≤ 80 effective LOC.

7. **`code-reviewer` Post-Test Gate** — after all tests pass.

8. **`security-auditor`** — recommended. The loader reads arbitrary YAML from disk; the resolver reads env vars. Quick pass to confirm: no YAML deserialization risks, no secret value logged, error messages don't expose secret values.

9. **`git-steward`** — commit message must include `Codex-reviewed (VERDICT: ...)` and `LOC: +n -0 (module config now n/100, module registry now n/150)`.
