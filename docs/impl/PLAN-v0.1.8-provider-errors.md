# PLAN: v0.1.8 : provider-error clarity in init

**Status:** Draft (rev 2 : plan-eng-review applied)
**Owner:** Anson
**Author:** Hao (plan-writer)
**Spec ref:** `docs/spec/SPEC-v0.1.8-provider-errors.md`
**Date:** 2026-05-20
**Branch:** `v0.1.8-provider-errors`

## Revision log

- **rev 2 (2026-05-20):** Applied all 7 findings from `/plan-eng-review`. P1s (A early-happy-path bypass, B scrubber-misses-custom-keys, C Telegram-branch-no-snippet) folded into Step 1 + Step 3. P2s (D explicit-retry-menu, E CLI-test-surface-note, F retry-attempt counter, G 500-char snippet) folded into Step 1 + Step 3 + test plan.
- **rev 1 (2026-05-20):** Initial plan written from SPEC-v0.1.8-provider-errors.md.

## 1. What's actually changing

Three files, surgically:

```
agent/init/validators.py   ← extend ValidationResult with diagnostics; add secret scrubber
agent/init/cli.py          ← status→headline mapper; render diagnostics block; retry-after-fix loop
tests/test_l7_init_validators.py  ← extend existing tests + new tests for diagnostics + scrubber
tests/test_l7_init_cli.py         ← new (or extended) tests for retry-after-fix + headline mapping
CHANGELOG.md + pyproject.toml + README.md   ← release plumbing
```

Nothing else. No new packages. No new dependencies. No runtime changes outside the init flow.

## 2. Data shape change

Today:

```python
class ValidationResult(NamedTuple):
    ok: bool
    error: str | None
    data: dict | None
```

After:

```python
class ValidationResult(NamedTuple):
    ok: bool
    error: str | None
    data: dict | None
    status_code: int | None = None      # HTTP status when applicable; None for network/local failures
    endpoint: str | None = None         # URL that was hit
    body_snippet: str | None = None     # first 200 chars of response body, secrets scrubbed
```

Defaults keep existing callers source-compatible. The init CLI is the only consumer; everything else just sees richer `ValidationResult`.

## 3. Tiny steps

Each step ≤300 LOC, ≤3 files. Standard SDLC gates apply: tests after each step.

### Step 1: ValidationResult shape + validator wiring

**Files:** `agent/init/validators.py`, `tests/test_l7_init_validators.py`

- Extend `ValidationResult` with 3 optional fields (defaults `None`).
- Both validators populate the new fields on every return path (success and failure).
- Body snippet: first 200 chars of the raw response body, post-scrubber.
- Add `_scrub_secrets(text: str) -> str` (private). Pattern set: `sk-[A-Za-z0-9_\-]{16,}`, `gsk_[A-Za-z0-9]{20,}`, `Bearer\s+\S{16,}`, `[A-Z_]*TOKEN[A-Z_]*\s*=\s*\S+`, `[A-Z_]*API_KEY[A-Z_]*\s*=\s*\S+`. Replace with `[REDACTED]`.
- Existing tests get the new fields asserted where relevant. New tests:
  - `body_snippet` is populated on failure for both validators.
  - `body_snippet` is scrubbed (test with each pattern).
  - `body_snippet` is capped at 500 chars (P2-G: 200 was arbitrary; some providers return useful info past 200 : full URL chains, error stacks).
  - `status_code` and `endpoint` populated on success and on every failure branch.
  - **P1-C:** Telegram 401/404 branch (today returns `None, None, None`) must now populate `status_code`, `endpoint`, and `body_snippet` from the parsed response body before returning.
  - **P1-B:** `_scrub_secrets(text, *, also_redact: list[str] | None = None)` takes an optional list of literal strings to redact in addition to the generic regex set. Each validator passes the input key (`api_key` / `token`) via `also_redact=[input_key]` so providers that echo the request key in error bodies cannot leak it, even for custom key shapes (e.g., DeepInfra's 32-char alphanumeric, which the generic patterns do not match). The scrubber escapes the literal with `re.escape` before substituting.

**Out:** richer ValidationResult, consumer-facing data shape locked.

### Step 2: Status→headline mapper + diagnostics renderer in CLI

**Files:** `agent/init/cli.py`, `tests/test_l7_init_cli.py` (new file)

- Add two module-level dicts: `_PROVIDER_HEADLINES` and `_TELEGRAM_HEADLINES`, keyed by status code (and a `"network"` and `"other"` sentinel).
- Add `_render_diagnostics(result: ValidationResult, headlines: dict) -> None`. Prints:
  - One-line headline (mapped from status).
  - One-line "HTTP {status} from {endpoint}" technical line.
  - Indented body snippet block if present.
- Keep print I/O in cli.py only. Validators stay pure.
- Tests use `capsys` to assert the rendered text for each status.

**Out:** when validator returns failure, cli renders structured diagnostics.

### Step 3: Retry-after-fix loop + early-happy-path routing

**Files:** `agent/init/cli.py`, `tests/test_l7_init_cli.py` (new), `tests/test_l7_init_integration.py` (extended)

**Note on test surface (P2-E):** CLI logic today is only exercised via the integration test (no dedicated unit-test file). This step CREATES `tests/test_l7_init_cli.py` for focused CLI unit tests AND extends the integration test with a 404-then-retry-success scenario. Both surfaces matter: unit tests are cheap and pin the menu/headline behaviour; the integration test proves the full init flow still produces a working `.env` after a retry.

- Replace `_retry_validator`'s 3-attempt cap with an unbounded loop that:
  - First attempt: prompt for the value.
  - On failure: render diagnostics (Step 2), then present an **explicit menu** (P2-D):
    ```
    [r] Retry with the same value (you fixed the upstream issue)
    [n] Paste a new value
    [q] Quit init
    ```
    Read a single-char response. Default on Enter = `r`. Unknown char reprompts the menu. (Reason: empty-input-means-retry combined with `getpass` lets a stray space silently change behaviour. The explicit menu makes intent unambiguous.)
  - On `[r]`: re-validate the previously-stored value.
  - On `[n]`: prompt again for a new value, then validate.
  - On `[q]`: raise `KeyboardInterrupt` so the existing top-level Ctrl-C handling does the clean-exit (no partial files).
  - Ctrl-C at any sub-prompt: same as `[q]`.
- **Retry-attempt counter (P2-F):** track per-loop attempt count locally. Pass it into the diagnostics renderer so the final line can read `(attempt N)`. Counter is local-only : no telemetry, no file writes : but exists so v0.1.9 can wire it to "after 7 attempts, here's a doc link" without a second refactor.
- **P1-A : early happy path:** `_step_provider()` (cli.py:140-160) detects a known-prefix key via `detect_provider_from_key` and inline-calls `validate_provider_key` once, printing `result.error` on failure before falling through to the picker. This bypasses the new diagnostics + retry-after-fix. Fix: route this path through `_retry_validator` too, by calling `_retry_validator` with a one-shot prompter that returns the already-pasted key on the first call and then delegates to `_prompt_secret` for any subsequent `[n]` selection. The picker fallback only fires if the user explicitly chooses `[q]` at the retry menu (the user already confirmed the detected provider; "give up and pick from the list" is now a separate explicit action, not silent fall-through).
- Tests:
  - Same-value retry after fix → second call succeeds (`[r]` then validator OK).
  - New-value retry → `[n]` prompts and validator sees new input.
  - Seven failures then success on the eighth : confirms no cap.
  - `[q]` raises KeyboardInterrupt : confirms clean-exit path.
  - Ctrl-C at retry prompt → KeyboardInterrupt propagates (existing behaviour).
  - **P1-A regression:** detected-prefix-then-fail-then-retry-success : confirms the early-happy-path now respects the retry loop.
  - Diagnostics renderer shows `(attempt N)` on the Nth failure.

**Out:** users can iterate on upstream issues without restarting init, regardless of whether they took the detected-prefix path or the picker path.

### Step 4: Release plumbing

**Files:** `CHANGELOG.md`, `pyproject.toml`, `README.md`

- CHANGELOG entry under `[0.1.8] - <release date>`, voice-rule compliant (no internal-tool names, no `[P1]/[P2]` tags, no em-dashes).
- `pyproject.toml` version bump `0.1.7` → `0.1.8`.
- README: one-paragraph note in the "If init fails" or troubleshooting section pointing out that the validator now retries without restarting and shows the upstream provider's error verbatim.

**Out:** ready for tag + GitHub release.

## 4. Test strategy

- Per-step unit tests as above.
- One integration test in `tests/test_l7_init_integration.py` (if it exists) or extended: a mocked-HTTP run-through of init that hits a 404 from the provider, prints the v0.1.8 guidance, retries on Enter with success, and writes the files.
- No live network. urllib mocked at `agent.init.validators.urllib.request.urlopen` exactly as v0.1.7 tests do.

## 5. Backwards-compat sanity

- `ValidationResult` is a NamedTuple; adding optional fields with defaults preserves positional and keyword access for the existing 3 fields.
- The CLI's call sites already use `result.ok`, `result.error`, `result.data` : no rename.
- External callers of `agent.init.validators` outside the init CLI: none. The package is self-contained.

## 6. Resolved open questions from spec §10

- **Secret scrubber lives in `validators.py`** as `_scrub_secrets(text, *, also_redact)`. Inlined, no Mira dep, minimal pattern set, takes per-call literals so the user's own pasted key is always redacted (P1-B).
- **No retry cap.** Removing the 3-attempt limit matches spec §4.1.6 and §AC4-5. Explicit `[r]/[n]/[q]` menu replaces empty-input-means-retry to avoid stray-space false retries (P2-D). Attempt counter tracked locally for future v0.1.9 surface (P2-F).
- **Per-validator headline dicts** (not shared). They're 6-7 lines each, deduplication saves nothing and obscures intent.
- **Early happy path (detected-prefix) routes through `_retry_validator` too.** (P1-A) Picker fallback is now an explicit `[q]` action, not silent fall-through.
- **Body snippet capped at 500 chars** (not 200, P2-G). Still safe, more useful for providers with verbose error bodies.

## 7. Risks

| Risk | Mitigation |
|------|------------|
| Secret scrubber misses a pattern, leaks a key | Pattern set covers all formats kayaclaw has seen in v0.1.7 smokes; add patterns as new providers are added. |
| User pastes a NEW wrong key on retry, thinks it's still the old one | Diagnostics line shows `HTTP {status} from {endpoint}` : same status on second try means same issue, different status means user changed something. Self-evident. |
| Removing the 3-attempt cap encourages flailing | Spec §10 confirms decision. If real users complain, add a soft "you've retried 5 times, want to abort?" prompt in a future minor. |
| `ValidationResult` shape change breaks an external consumer | grep confirms zero external consumers in this repo; v0.1.x has no published Python API contract. |
| Body snippet shows internal API URL containing secret-like tokens | Scrubber runs before snippet is set, not before display. |

## 8. Definition of done

All 7 acceptance criteria from spec §8 met, full test suite green, manual smoke against a real OpenRouter restricted key (or its mock) confirms the v0.1.7 Wei dead-end is gone.

## 9. SDLC gates (per CLAUDE.md and kayaclaw memory rules)

1. `/plan-eng-review` on THIS plan, before any code. Apply P1 findings here.
2. After each code step: tests must pass.
3. After all code: `/simplify` on the staged diff (kayaclaw anti-bloat gate).
4. Then `/codex-review` on the staged diff (mandatory pre-commit gate).
5. Apply review findings (P1 before commit, P2/P3 in commit message or follow-up).
6. Human smoke before final ship (Mira is deferred; Wei or Anson runs it).
7. Per-piece approval for any public-facing copy (CHANGELOG entry, README addition, ship post).
