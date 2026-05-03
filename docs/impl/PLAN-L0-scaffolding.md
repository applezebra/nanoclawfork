# Implementation Plan — L0: Repo Scaffolding & Cross-Cutting Primitives

**Lane:** L0
**Version:** 0.1
**Status:** `/plan-eng-review` complete (2026-05-01) — 6 fixes applied (A1, A2, A3, C1, T1, T2). Ready for code-implementer.

## Revision log

- **2026-05-01 (post-eng-review):** Applied A1 (filter on non-propagating child loggers), A2 (module-import-time install), A3 (defer `[project.scripts]` to L5), C1 (exact `==` pins, version rationale in commit), T1 (scrub `exc_text` + `stack_info`, traceback test), T2 (subprocess `pip list` test for transitive anthropic).
**Depends on:** Nothing (this is the root lane)
**Blocks:** L1, L2, L3, L4, L5 (all lanes import the logger; all lanes assume the package layout)

---

## Mandatory gate before code-implementer begins

`/plan-eng-review` on this plan file is required before code-implementer touches any file in this lane. This lane is not skippable: it establishes the security-relevant token-scrubbing logger consumed by every subsequent lane.

---

## 1. Lane Summary

L0 creates the importable `agent/` package, establishes the two-file brand-decoupling rule (CONTRACT §"Brand-decoupled code"), produces the token-scrubbing logger that every other lane uses, and lays down the gitignore and attribution stubs the public-repo posture requires (CONTRACT item 9, 10; NFR-LA1 through LA3).

**LOC budget:** 40 target / 60 hard cap (effective Python LOC, blank/comment-stripped). Docs files (`.md`, `.yaml`, `.txt`) are exempt from the LOC rule per global CLAUDE.md.

---

## 2. WHAT — Artifacts to Produce

| Artifact | Path |
|---|---|
| Package init | `agent/__init__.py` |
| Brand constants | `agent/__about__.py` |
| Token-scrubbing logger | `agent/logging.py` |
| Project manifest | `pyproject.toml` |
| Env-var documentation | `.env.example` |
| Gitignore | `.gitignore` |
| Attribution stub | `NOTICES.md` |
| Test file | `tests/test_l0_scaffolding.py` |

---

## 3. WHY — Rationale per Artifact

- **`agent/__init__.py`** — Makes `agent` an importable Python package. All downstream lanes do `from agent.xxx import yyy`; without this file, those imports fail.

- **`agent/__about__.py`** — CONTRACT item 8 requires the brand name to live in exactly two files. This is one of them. Exporting `__brand__`, `__slug__`, and `__version__` as constants enables the brand-leak grep check in L6 (IMPACT-ANALYSIS §L6 acceptance: `git grep kayaclaw` hits only the two brand files and docs).

- **`agent/logging.py`** — CONNECTOR-AUDIT finding #1: "The bot token must never appear in any log line, ever." IMPACT-ANALYSIS §L0 public interface specifies a `get_logger(name)` factory that installs a scrubbing filter. Every lane is required to use `get_logger` rather than `logging.getLogger` directly (IMPACT-ANALYSIS §5 cross-cutting concern: "Token-scrubbing logger — must use `get_logger`, never `print`, never `logging.getLogger` directly").

- **`pyproject.toml`** — CONTRACT item 8 (second of the two brand files), NFR-LA1 (MIT license declaration), and the package build surface. Also declares the Python version constraint and dependency set. No Anthropic SDK is listed here — this is the first verification point for AC-4.

- **`.env.example`** — FR-C3: "A `.env.example` file must document every required and optional environment variable. Secrets must never appear in any committed file." This file is the operator's reference; the live `.env` is gitignored.

- **`.gitignore`** — IMPACT-ANALYSIS §L0 public interface specifies it must include `data/`, `config.yaml`, `.env`. This prevents accidental credential commits, enforcing FR-M3 and the thread of NFR-LA3 (public repo from first commit means the gitignore must be correct from commit zero).

- **`NOTICES.md`** — NFR-LA2: any code lifted from `qwibitai/nanoclaw` must be attributed. The CONNECTOR-AUDIT verdict is "DO NOT LIFT" for the connector, but design patterns from the discovery sketches are referenced. This file is the stub that L6 will fill with final dependency attribution.

---

## 4. HOW — Tiny Step Breakdown

### Step 1 — Package skeleton and brand constants

**Files touched (≤3):**
1. `agent/__init__.py`
2. `agent/__about__.py`
3. `pyproject.toml`

**LOC estimate:** ~20 effective LOC across the three Python/TOML files (well within the 60 hard cap; this step alone uses ~1/3 of the budget).

**What to write:**

`agent/__init__.py` — empty or a single-line docstring identifying this as the generic internal package name. No imports. No logic. The file exists only to make the directory a package. Do not put `kayaclaw` in this file — the brand belongs only in `__about__.py`.

`agent/__about__.py` — define three module-level string constants:
- `__brand__`: the working project name (e.g. `"kayaclaw"`)
- `__slug__`: a URL-safe lowercase version of the same (e.g. `"kayaclaw"`)
- `__version__`: `"0.1.0"`

These three names are the only place the brand string appears in `agent/` source. No conditional logic, no imports.

`pyproject.toml` — the standard PEP 517/518 manifest. Key fields:
- `[project].name` = the brand name (the second allowed location per NFR-BD1)
- `[project].version` = `"0.1.0"`
- `[project].license` = `{text = "MIT"}`
- `[project].requires-python` = `">=3.12"`
- `[project].dependencies` — list runtime deps only: `pydantic`, `pydantic-ai`, `python-telegram-bot`, `PyYAML`. Do NOT list `anthropic`. Do NOT list `aiosqlite` (per DECISIONS.md D2). **All deps MUST be pinned with exact `==` operator (not `~=`, not `>=`)** — eng-review fix C1. Code-implementer chooses the specific versions and **must document the choice and rationale in the commit message body** (e.g. "Pinned pydantic-ai==X.Y.Z because that's the latest stable as of {date}, no known CVEs").
- `[build-system]` — standard `hatchling` or `flit` backend (whichever is lighter — flit is simpler for this project's size).
- **`[project.scripts]` — DO NOT declare in L0** (eng-review fix A3). The `agent.__main__:main` entry point is created in L5; declaring it here would register a broken `agent` console script after `pip install -e .`. Wire `[project.scripts]` in L5 alongside `__main__.py`.

**Tests to add:**

```
tests/test_l0_scaffolding.py  (Step 1 portion)
```

- Import `agent` — assert no ImportError.
- Import `agent.__about__` — assert `__brand__` is a non-empty string, `__version__` starts with `"0.1"`.
- Assert `"kayaclaw"` does not appear in the string `agent.__init__.__file__`'s source text (proxy for the brand-leak rule; the real grep check is in L6).
- Assert `anthropic` is not in declared dependencies. Read the dist name from `agent.__about__.__slug__` (do NOT hardcode the brand string in tests, per NFR-BD): `importlib.metadata.requires(__about__.__slug__)` and scan the list.
- **Transitive-dep check (eng-review fix T2):** run `subprocess.run(["pip", "list", "--format=freeze"], capture_output=True, text=True)` and assert no line starts with `anthropic==`. This catches the case where `anthropic` arrives as a transitive dependency of `pydantic-ai` or any other top-level dep. Belt + suspenders for AC-4.

**Acceptance check before proceeding to Step 2:**
- `python -c "import agent; from agent.__about__ import __brand__; print(__brand__)"` prints the brand name without error.
- `pip show $(python -c "from agent.__about__ import __slug__; print(__slug__)")` shows the package is installed (editable install via `pip install -e .`). The slug is read from `__about__` so the rename rule holds.
- Step 1 tests all pass.

---

### Step 2 — Token-scrubbing logger

**Files touched (≤3):**
1. `agent/logging.py`
2. `tests/test_l0_scaffolding.py` (extend the existing test file)

**LOC estimate:** ~20 effective LOC in `agent/logging.py` (cumulative lane total: ~40 — on target).

**What to write:**

`agent/logging.py` — implement `get_logger(name: str) -> logging.Logger` as follows:

1. **Install the filter at module-import time** (eng-review fix A2), NOT lazily on first `get_logger()` call. Reason: third-party libraries (e.g. `python-telegram-bot`) may emit log records during their own import, before our first `get_logger()` call. Lazy install would miss those records. Module-import-time install guarantees the filter is active before any other code runs that could log.

2. **Attach the filter to BOTH the root logger AND any existing non-propagating child loggers** (eng-review fix A1). Iterate `logging.Logger.manager.loggerDict` at install time; for each `Logger` instance with `propagate == False`, also attach the filter directly. This handles libraries that disable propagation for noise control. Re-attach if new non-propagating loggers are created later — a small periodic check is acceptable, but for 0.1 a one-shot at import + a re-scan inside `get_logger()` is sufficient.

3. The filter's `filter(record)` method must scrub secrets from THREE record fields (eng-review fix T1): `record.getMessage()` AND `record.exc_text` (formatted exception text) AND `record.stack_info` (stack trace strings). Plain string replacement of each known secret value with `[REDACTED]`. No regex.

4. The set of secret values to scrub is built at filter-init time by reading the environment for: `TELEGRAM_BOT_TOKEN`, and any env var whose name ends in `_API_KEY`. Values are read once at module-import time — the filter does not re-read env on every log record.

5. `get_logger(name)` returns `logging.getLogger(name)` after ensuring the root logger's level is set to `INFO` by default (or whatever `LOG_LEVEL` env var says). It is now a thin wrapper — the install work happens at import time.

6. The logger format: `"%(asctime)s %(levelname)s %(name)s — %(message)s"` (ISO-ish, structured enough for grep, simple enough to read).

Design note on why scrubbing is at the filter layer rather than at call sites: every lane is required to use `get_logger`, and a call-site approach would require every developer to remember to scrub before logging. A single root filter (plus non-propagating child attachments per A1) is a defense-in-depth backstop — CONNECTOR-AUDIT finding #1 is non-negotiable.

Design note on why scrubbing reads env at init time: reading env on every log record is a hot-path cost for no benefit, since the token value does not change at runtime. The filter is initialized once at module import.

Design note on why module-import-time install vs lazy: see A2 above. Bot tokens typically appear in HTTP error URLs from `httpx`/`aiohttp`; those errors can fire during library import in some failure modes, before the agent's main loop runs.

**Tests to add (extend `tests/test_l0_scaffolding.py`):**

- Set a fake token in `os.environ["TELEGRAM_BOT_TOKEN"]` before importing the logger. Call `get_logger("test")`. Log a message containing the fake token value. Capture log output (use `logging.handlers.MemoryHandler` or a `StringIO` handler). Assert the captured output does not contain the fake token value and does contain `[REDACTED]`.
- Assert that a normal log message (no token substring) passes through unmodified.
- Assert that a log message containing a fake `FOO_API_KEY` value is also scrubbed (env var name ends in `_API_KEY`).
- Assert that calling `get_logger("a")` and `get_logger("b")` does not install the filter twice (check `len(logging.getLogger().filters)`).
- **Traceback scrubbing (eng-review fix T1):** raise an exception whose message contains the fake token (e.g. `raise RuntimeError(f"https://api.telegram.org/bot{token}/getMe failed")`), catch it, then `logger.error("call failed", exc_info=True)`. Capture formatted output. Assert the formatted exception text contains `[REDACTED]` and does NOT contain the fake token value.
- **Non-propagating child logger (eng-review fix A1):** create a child logger via `logging.getLogger("third_party_noise")`, set `propagate = False`, attach a `MemoryHandler` to it directly. Log a message containing the fake token via that child. Assert the captured output is scrubbed.
- **Import-time install (eng-review fix A2):** in a fresh subprocess, set `TELEGRAM_BOT_TOKEN`, then `import agent.logging` followed immediately by `import logging; logging.warning("token=<fake>")` (no `get_logger` call). Assert the warning was scrubbed — proves filter install happens at import, not lazy.

**Acceptance check before proceeding to Step 3:**
- All Step 2 tests pass.
- `from agent.logging import get_logger; log = get_logger("smoke"); log.info("hello")` works without error.

---

### Step 3 — Support files (gitignore, env.example, NOTICES stub)

**Files touched (≤3):**
1. `.gitignore`
2. `.env.example`
3. `NOTICES.md`

**LOC estimate:** 0 effective Python LOC (these are data/text files; they do not count against the lane cap).

**What to write:**

`.gitignore` — must include at minimum:
- `data/` — the SQLite volume mount point in local dev
- `config.yaml` — the live config (the example is committed; the live file is not)
- `.env` — the live secrets file
- `__pycache__/`, `*.pyc`, `*.pyo`, `.pytest_cache/`, `.ruff_cache/`
- `.venv/`, `venv/`, `dist/`, `*.egg-info/`

`.env.example` — one entry per required env var, with a comment explaining what it is. Required vars for 0.1:
- `TELEGRAM_BOT_TOKEN` — the bot token from BotFather
- `DEEPINFRA_API_KEY` — the DeepInfra API key (the example value for `api_key_env` in the default config)
- `ALLOWED_TELEGRAM_USER_IDS` — comma-separated list of permitted Telegram chat IDs
- `AGENT_DATA_DIR` — (optional) override the data directory; defaults to `/data`
- `LOG_LEVEL` — (optional) Python log level; defaults to `INFO`

All values in `.env.example` must be clearly marked as placeholders (`your-token-here`, `your-key-here`). No real values ever.

`NOTICES.md` — a stub with the MIT license header block, the project name, and a section "Third-party attributions" that is currently empty with a note that it will be populated in L6. Include a note that NanoClaw (`qwibitai/nanoclaw`) was consulted as a design reference but no code was lifted (per CONNECTOR-AUDIT verdict: "DO NOT LIFT").

**Tests to add:** None for this step (text files). The L6 plan verifies these file contents at the final gate.

**Acceptance check before closing L0:**
- `git status` with these files tracked shows they are committed to the repo.
- `.gitignore` causes `git status` to suppress `data/`, `.env`, `config.yaml` when those exist.
- `cat .env.example` shows no real secret values.

---

## 5. Lane-Level Acceptance

L0 is closed when ALL of the following are true:

| Check | Maps to |
|---|---|
| `python -m pytest tests/test_l0_scaffolding.py` passes with zero failures | Internal gate |
| `from agent.__about__ import __brand__` returns the expected string | CONTRACT item 8, NFR-BD1 |
| `grep -r "kayaclaw" agent/` returns hits ONLY in `agent/__about__.py` | NFR-BD2 |
| Logger scrubs a known fake token from captured log output | CONNECTOR-AUDIT finding #1; R4 mitigation |
| No provider-vendor SDK (anthropic, openai, etc.) is in `[project].dependencies` | Agnostic guarantee (CONTRACT thesis) |
| Logger scrubs tokens in tracebacks (`exc_text`) and stack_info, not just messages | CONNECTOR-AUDIT P2-1 (eng-review T1) |
| Logger filter installed at module-import time, not lazy on first `get_logger()` call | Eng-review A2 |
| Logger filter attached to non-propagating child loggers, not just root | Eng-review A1 |
| `[project.scripts]` is NOT declared in pyproject.toml (deferred to L5) | Eng-review A3 |
| `.gitignore` suppresses `data/`, `.env`, `config.yaml` | FR-C3, FR-M3 |
| All files listed in §2 exist in the repo | Completeness |

---

## 6. Anti-Bloat Callouts

**Do NOT add a logging configuration file (e.g. `logging.yaml` or `logging.ini`).** The filter is installed in code; config-file-driven logging setup is premature for a 450 LOC project.

**Do NOT add a `conftest.py` with complex fixtures in this step.** A simple test file with direct imports is sufficient. Fixtures are added in later lanes only if two or more tests need the same setup.

**Do NOT add a `Makefile` or `justfile` in this lane.** Development scripts belong in L6.

**Do NOT make `get_logger` configurable beyond `LOG_LEVEL`.** No log rotation, no file handlers, no JSON formatter. Stdout/stderr is the contract (NFR-O1).

**Do NOT import any L1/L2/L3/L4 modules in `agent/__init__.py`.** The package init must remain a blank stub so that importing `agent` does not trigger config loading or DB initialization.

**Do NOT add `__all__` exports to `agent/__init__.py`.** Each lane imports from its own module directly. A package-level `__all__` would couple all modules at import time.

---

## 7. Review Gates Checklist

In sequence, before closing this lane:

1. **`/plan-eng-review`** on this plan file — mandatory before code-implementer begins. Catches plan-stage P1/P2 issues. (Cannot skip: logger is a security-relevant cross-cutting component.)

2. **`code-reviewer` Pre-Test Gate** — after Step 2 is written, before running tests. Checks: size constraints (≤300 LOC, ≤3 files), obvious bugs, anti-bloat checklist.

3. **`/codex-review`** on the staged diff — before committing Step 2. Cannot skip: `agent/logging.py` establishes the security boundary for token handling across the entire project.

4. **`/simplify`** — after Step 2 lands. Review the logger for reuse, quality, and LOC efficiency. Target: confirm it is at or below 20 effective LOC.

5. **`code-reviewer` Post-Test Gate** — after all tests pass. Architecture fit, edge cases (empty env, missing token env var, multiple `get_logger` calls).

6. **`security-auditor`** — recommended (not required by REVIEW-PROTOCOL for L0, but the scrubbing filter is a security primitive; a quick read is worth the cost). Flag: "Is the filter definitely installed on the root logger and not a child logger that can be bypassed?"

7. **`git-steward`** — commit message must include `Codex-reviewed (VERDICT: ...)` and `LOC: +n -0 (module logging now n/60)`.
