# Implementation Plan — L6: Release Polish

**Lane:** L6
**Version:** 0.1
**Status:** Ready for `/plan-eng-review`
**Depends on:** ALL prior lanes complete (L0 through L5). L6 produces the final LOC count and README claims — these must reflect the real final state.
**Blocks:** Nothing (this is the final lane)
**Can run in parallel with:** Nothing (L6 starts only when all prior lanes are closed)

---

## Mandatory gate before code-implementer begins

`/plan-eng-review` on this plan file is required before code-implementer begins. While L6 produces primarily documentation, `scripts/loc.sh` is a functional script and the brand-leak grep check is a verifiable security control (NFR-BD2). The plan gate is fast but non-skippable.

---

## 1. Lane Summary

L6 makes the repository public-presentable and its claims independently verifiable. It produces the README (with "What works" that matches CONTRACT acceptance items exactly), completes `NOTICES.md` with final dependency attributions, writes `scripts/loc.sh` that counts effective Python LOC per module, and runs the brand-leak grep as a final gate. The LOC count in the README must reflect the actual measured count from `scripts/loc.sh` after all lanes are merged.

**Final action of L6 (added 2026-05-03 per Anson):** flip the repo from private to public via `gh repo edit applezebra/kayaclaw --visibility public --accept-visibility-change-consequences`. Pre-conditions, ALL must hold:
1. L0–L5 all closed and committed.
2. security-auditor agent run on the full repo, no P1 findings.
3. README "What works" verified against every CONTRACT acceptance item.
4. Brand-leak grep clean.
5. `git log --all --oneline | grep -iE "(secret|token|key|password|TODO: remove)"` returns nothing concerning.

This flip is the LAST action of L6, not the first action of L0. Original CONTRACT said "public from first commit"; overridden — see CONTRACT-0.1.md "Public posture" line.

**LOC budget:** Documentation files are exempt from the LOC rule per global CLAUDE.md ("Documentation Exemption: LOC limits do NOT apply to `.md`/`.txt` files in `/docs/`"). The `scripts/loc.sh` script is capped at 30 effective LOC.

---

## 2. WHAT — Artifacts to Produce

| Artifact | Path |
|---|---|
| LOC counting script | `scripts/loc.sh` |
| Root README | `README.md` |
| Completed attribution file | `NOTICES.md` |
| MIT license file | `LICENSE` |
| Security disclosure policy | `SECURITY.md` |
| Branch protection on `main` | applied via `gh api` (no file in repo) |

---

## 3. WHY — Rationale per Artifact

- **`scripts/loc.sh`** — CONTRACT §"Size Discipline" acceptance criterion: "A `scripts/loc.sh` (or equivalent) exists and is reproducible by any reader." This script is the audit tool that lets any reader verify the "~10× smaller than NanoClaw" and "auditable in an afternoon" claims. The script must be runnable on any machine with `bash`, `find`, and standard POSIX utilities — no Python, no special tooling required.

- **`README.md`** — AC-8: "When a reader unfamiliar with the project reads the 'What works' section, every statement corresponds to a passing acceptance criteria item." IMPACT-ANALYSIS §L6: "README 'What works' section quoting CONTRACT-0.1.md verbatim." This is the public face of the project from the first commit. The "Out of scope" section must match the "Explicitly NOT in 0.x" list in CONTRACT-0.1.md verbatim to prevent misleading readers about what the project does.

- **`NOTICES.md`** — NFR-LA2: "Any code lifted from `qwibitai/nanoclaw` must be attributed in `NOTICES.md`." The CONNECTOR-AUDIT verdict is "DO NOT LIFT" for the connector, so no NanoClaw code was lifted. However, `NOTICES.md` must still list all third-party MIT-licensed dependencies (particularly `python-telegram-bot`) and note that NanoClaw was consulted as a design reference with no code lifted. This is the audit-clean attribution posture.

- **`LICENSE`** — `pyproject.toml` already declares `license = {text = "MIT"}` but a real `LICENSE` file at the repo root is what GitHub indexes for the license badge and what most license scanners look for. Without it, the project reads as "claims MIT but doesn't actually ship the license text" — which is a real legal grey area for downstream users. Standard MIT text, copyright `2026 Anson Zeall`.

- **`SECURITY.md`** — The whole project's pitch is "secure container-isolated agent." Going public without a vulnerability disclosure policy is a credibility gap: researchers who find a hole have no documented way to report it privately, so they either drop it on Twitter or open a public issue. SECURITY.md documents: where to email (security@... — Anson decides), expected initial response time (target 72h), what's in scope (the agent code, the runtime, the framing logic), what's out of scope (downstream provider bugs, host OS issues). No PGP key required at v0.1.

- **Branch protection on `main`** — Without it, anyone with push access (currently just Anson) can force-push, delete the branch, or merge without review. Once the repo is public, branch protection is the difference between "casual hobby project" and "operates with discipline." Configured via `gh api` to require: pull request before merge, no force-push, no deletion, conversation resolution before merge. Status check requirements deferred to post-launch (no CI yet).

---

## 4. HOW — Tiny Step Breakdown

### Step 1 — LOC counting script

**Files touched (≤3):**
1. `scripts/loc.sh`
2. `scripts/` directory (created if not present — `mkdir -p scripts` is fine, no new files beyond the script)

**LOC estimate:** ≤ 30 effective LOC in `scripts/loc.sh`.

**What to write:**

`scripts/loc.sh` — a POSIX sh script (not bash-specific, for portability) that:

1. Counts effective Python LOC per module, defined as: total lines minus blank lines minus comment-only lines (lines where the first non-whitespace character is `#`).

2. Reports per-module counts against their hard caps from CONTRACT §Size Discipline:

   | Module | Hard cap |
   |---|---|
   | `agent/connectors/telegram.py` | 250 |
   | `agent/registry.py` | 150 |
   | `agent/runtime.py` | 100 |
   | `agent/memory.py` | 150 |
   | `agent/__main__.py` | 50 |
   | `agent/config.py` | 100 |

3. Reports a total against the 800 effective LOC hard cap.

4. Exits non-zero if any module exceeds its hard cap, or if the total exceeds 800.

5. Prints output in a format a human can read at a glance, e.g.:
   ```
   connectors/telegram.py:  148/250
   registry.py:              82/150
   runtime.py:               49/100
   memory.py:                77/150
   __main__.py:              28/50
   config.py:                58/100
   TOTAL:                   442/800  [PASS]
   ```

Implementation approach: use `grep -cv` (count of lines NOT matching a pattern) in combination. A simpler single-pass approach: for each file, count total lines with `wc -l`, then subtract blank lines (`grep -c '^[[:space:]]*$'`) and comment lines (`grep -c '^[[:space:]]*#'`). The difference is the effective LOC count.

Why a shell script rather than a Python script: the shell script can be run by anyone cloning the repo without needing to install the package first. It is also a smaller dependency surface — no imports, no virtual environment. The `scripts/loc.sh` is the audit tool for people evaluating the project, not for developers building it.

**Tests to add:** None (shell script). The acceptance check is: run `bash scripts/loc.sh` after all lanes are merged and verify the output matches the README "Status" section.

**Acceptance check before proceeding to Step 2:**
- `bash scripts/loc.sh` runs without error on the current state of `agent/`.
- Output shows total ≤ 800.
- Script exits 0.

---

### Step 2 — README

**Files touched (≤3):**
1. `README.md`

**LOC estimate:** 0 effective Python LOC (Markdown file, exempt from counting).

**What to write:**

`README.md` — the public face of the project. Required sections:

**Headline and positioning:** One sentence: "kayaclaw — a Singapore-made, LLM-agnostic agent built on NanoClaw's container security model, with no Anthropic lock-in." The `kayaclaw` brand name appears in the README headline — this is the one additional permitted location beyond the two code files, per spirit of NFR-BD1 (the README is not source code).

**What works (0.1):** This section must contain the same statements as CONTRACT §"Works in 0.1", numbered 1–10, phrased in past tense or present tense as capabilities, not as future goals. Each item must correspond to a verifiable AC item. The plan-writer recommends quoting the 10 items from CONTRACT §"Works in 0.1" nearly verbatim — this is the most reliable way to maintain truthfulness (AC-8).

Example entry (exact wording TBD by code-implementer matching the actual final state):
```
1. Telegram connector, text-only, single chat-ID allowlist (ALLOWED_TELEGRAM_USER_IDS).
2. DeepInfra provider end-to-end via OpenAI-compatible API. Default model: Llama 3.3 70B Instruct.
...
```

**Status (LOC count):** A single line: `"0.1 ships at N effective LOC of core Python (run scripts/loc.sh to verify)."` where N is the actual output of `scripts/loc.sh`. This line is written last, after running the script on the final merged state. The CONTRACT §"Honest positioning claims" authorizes these specific phrases:
- *"~10× smaller than NanoClaw's current source size."* (NanoClaw is ~7,645 effective LOC)
- *"Our entire 0.1 fits inside what NanoClaw spends on Telegram alone."* (NanoClaw's Telegram connector is ~1,000 LOC)
- *"Auditable in an afternoon."*

These claims may appear in the README only if `scripts/loc.sh` confirms the LOC count is within the CONTRACT caps. Do not write them speculatively before running the script.

**Explicitly NOT in 0.x:** Quote the list from CONTRACT §"Explicitly NOT in 0.x" verbatim, in a section titled "Out of scope" or "Not in 0.x". Word-for-word is required — any paraphrase risks omitting an item and misleading readers (AC-8).

**Quick start:** A minimal "how to run" section:
1. Clone the repo.
2. Copy `.env.example` to `.env` and fill in values.
3. Copy `config.example.yaml` to `config.yaml` (already configured for DeepInfra).
4. `docker compose up`.

No multi-page setup doc. No prerequisites beyond Docker. The deploy surface is exactly three files (NFR-D2).

**Verifying the security posture:** A brief section pointing readers to `docs/discovery/container/SECURITY.md` for the control checklist and to `docker inspect` for the verifiable claims. This is the "independently verifiable" posture from US-5.

**Tests to add:** None (Markdown file). The acceptance check is a manual review by the code-reviewer against the AC-8 criterion.

**Acceptance check before proceeding to Step 3:**
- README "What works" contains exactly 10 items matching CONTRACT §"Works in 0.1".
- README "Out of scope" matches CONTRACT §"Explicitly NOT in 0.x" word-for-word.
- README "Status" section contains the actual LOC count from `scripts/loc.sh`.
- `git grep "kayaclaw" agent/` returns hits ONLY in `agent/__about__.py` (brand-leak check — the final gate for NFR-BD2).

---

### Step 3 — NOTICES.md completion and brand-leak grep

**Files touched (≤3):**
1. `NOTICES.md` (complete the stub created in L0)

**LOC estimate:** 0 effective Python LOC (Markdown file, exempt).

**What to write:**

`NOTICES.md` — the complete attribution file. Required content:

1. The project's own MIT license header block (project name, year, standard MIT text).

2. A section "Third-party Python dependencies" listing every package in `pyproject.toml`'s `[project.dependencies]` with its license. For each:
   - `python-telegram-bot`: MIT license, link to the project's GitHub.
   - `pydantic-ai`: MIT license.
   - `pydantic`: MIT license.
   - `PyYAML`: MIT license.
   - (Note: per DECISIONS.md D2, L3 uses `sqlite3` from stdlib — `aiosqlite` is NOT a top-level dependency. Code-implementer must run `pip freeze` and list any transitive deps that appear there with their licenses, rather than assuming a fixed list.)

3. A section "Design references (no code lifted)":
   - `qwibitai/nanoclaw` (MIT) — consulted as a design reference for container hardening patterns and connector security audit. No code was lifted. The Telegram connector was re-derived from scratch in Python after a security audit concluded the TypeScript connector could not be ported (language mismatch). Attribution is provided per the spirit of the MIT license and the CONNECTOR-AUDIT verdict.

4. A note: "To verify that no code was lifted from qwibitai/nanoclaw, run `git log --all --full-history -- agent/` and review each commit. The repo was initialized from scratch with no upstream commits."

**Brand-leak grep (part of Step 3, not a separate step):**

Before closing L6, run: `git grep "kayaclaw" agent/`

This must return hits ONLY in `agent/__about__.py`. If any other file in `agent/` contains the brand name, it is a violation of NFR-BD2 and must be fixed before the lane closes.

Also run: `git grep -i "anthropic" agent/` — this must return zero hits (or only in comments, not in import statements or function calls). This is the last verification of AC-4 before release.

**Tests to add:** None (Markdown file and shell grep). These are manual verification steps documented in the acceptance check.

**Acceptance check before proceeding to Step 4:**
- `bash scripts/loc.sh` exits 0, total ≤ 800, all modules within caps.
- `git grep "kayaclaw" agent/` hits only `agent/__about__.py`.
- `git grep "import anthropic" agent/` returns no hits.
- `README.md` "What works" has 10 items matching CONTRACT verbatim.
- `NOTICES.md` lists all top-level dependencies with licenses.
- `NOTICES.md` has the design-reference note for `qwibitai/nanoclaw`.

---

### Step 4 — OSS minimum (LICENSE + SECURITY.md)

**Files touched (≤2):** `LICENSE`, `SECURITY.md`. Both at repo root. Doc files — no LOC budget impact.

**What to write:**

`LICENSE` — verbatim MIT license text (https://opensource.org/license/mit), `Copyright (c) 2026 Anson Zeall`. No deviations from the standard text. GitHub auto-detects this and shows the "MIT License" badge on the repo header.

`SECURITY.md` — short and honest. Sections:
- **Supported versions:** `0.1.x` (only). Pre-1.0; no LTS.
- **Reporting a vulnerability:** email `<address Anson chooses — placeholder TBD until Anson confirms>`. Do NOT open public issues for security bugs. Initial response within 72 hours.
- **In scope:** the agent runtime, prompt-injection framing, container isolation posture, secrets handling, the SQLite memory layer.
- **Out of scope:** bugs in upstream providers (DeepInfra, OpenAI, Anthropic), bugs in `python-telegram-bot`, host OS issues, social engineering of bot operators.
- **No bug bounty at v0.1.** Recognition in `SECURITY-HALL-OF-FAME.md` for accepted reports.

**Acceptance check before proceeding to Step 5:**
- `LICENSE` exists, contains the literal string "MIT License" and "Copyright (c) 2026 Anson Zeall".
- `SECURITY.md` exists with all 5 sections.
- GitHub repo page shows "MIT License" badge (verify after first push).

---

### Step 5 — Branch protection on main

**Files touched (0 in repo):** This step runs `gh api` commands; produces no files. The configuration lives on GitHub, not in the repo.

**What to run** (executed manually — NOT automated, because branch protection changes are sensitive and should be deliberate):

```bash
gh api -X PUT /repos/applezebra/kayaclaw/branches/main/protection \
  -f required_pull_request_reviews.required_approving_review_count=0 \
  -f required_pull_request_reviews.dismiss_stale_reviews=true \
  -F enforce_admins=false \
  -F required_status_checks=null \
  -F restrictions=null \
  -F allow_force_pushes=false \
  -F allow_deletions=false \
  -F required_conversation_resolution=true
```

Why `required_approving_review_count=0`: Anson is the only committer at v0.1; requiring 1 review would block every PR. The protection still requires a PR (no direct push to main), no force-push, no deletion, and conversation resolution before merge — that's the meaningful safety. Once a second committer joins, bump to 1.

Why `enforce_admins=false`: Anson can override in emergencies (e.g., reverting a bad merge). With one committer, admin enforcement is just self-flagellation.

Why `required_status_checks=null`: No CI exists at v0.1. Add status checks once GitHub Actions is set up post-launch.

**Acceptance check before proceeding to Step 6:**
- `gh api /repos/applezebra/kayaclaw/branches/main/protection` returns 200 (not 404).
- Test: try `git push --force origin main` — must be rejected by the remote.

---

### Step 6 — Flip to public

**Files touched (0):** Single `gh repo edit` command. This is the LAST action of the entire 0.1 build.

**Pre-conditions** (ALL must hold — verify manually):
1. L0–L5 lanes all closed and committed.
2. L6 Steps 1–5 complete.
3. security-auditor agent has been run on the full repo and returned no P1 findings. (If P1s exist, fix them before this step. Do not flip to public with known P1s.)
4. README "What works" verified against every CONTRACT acceptance item.
5. `git grep "kayaclaw" agent/` returns hits ONLY in `agent/__about__.py`.
6. `git log --all --oneline | grep -iE "(secret|token|key|password|TODO: remove|XXX|FIXME)"` reviewed — no leaked secrets, no embarrassing TODOs.

**What to run:**

```bash
gh repo edit applezebra/kayaclaw --visibility public --accept-visibility-change-consequences
```

Then verify GitHub recognizes:
- The MIT license badge appears on the repo page.
- `SECURITY.md` is linked from the repo's "Security" tab.
- Branch protection is preserved across the visibility change (it should be, but confirm).

**No reverting.** Once public, the git history is permanently visible (GitHub caches forks and the Wayback Machine indexes within hours). Treat this step as a one-way door. The Step 6 pre-conditions exist precisely because this step is irreversible in practice.

---

## 5. Lane-Level Acceptance

L6 is closed when ALL of the following are true:

| Check | Maps to |
|---|---|
| `bash scripts/loc.sh` exits 0, reports total ≤ 800 and all modules within caps | CONTRACT §Size Discipline acceptance criterion |
| README "What works" matches CONTRACT §"Works in 0.1" items 1–10 | AC-8 |
| README "Out of scope" matches CONTRACT §"Explicitly NOT in 0.x" verbatim | AC-8 |
| README "Status" section quotes the actual measured LOC count | CONTRACT §"Acceptance criterion (added to 'Done =' list)" |
| `git grep "kayaclaw" agent/` returns hits ONLY in `agent/__about__.py` | NFR-BD2 |
| `git grep "import anthropic" agent/` returns zero hits | AC-4 final verification |
| `NOTICES.md` lists all top-level deps with licenses | NFR-LA2 |
| `NOTICES.md` notes `qwibitai/nanoclaw` as design reference with no code lifted | NFR-LA2 |
| `LICENSE` file at repo root, MIT text, copyright Anson Zeall 2026 | OSS minimum (Step 4) |
| `SECURITY.md` at repo root with all 5 sections | OSS minimum (Step 4) |
| `gh api /repos/applezebra/kayaclaw/branches/main/protection` returns 200 | OSS minimum (Step 5) |
| `git push --force origin main` rejected by remote (proves protection is live) | OSS minimum (Step 5) |
| `gh repo view applezebra/kayaclaw --json visibility` returns `PUBLIC` AFTER Steps 1–5 + security-auditor pass | Step 6 |

---

## 6. Anti-Bloat Callouts

**Do NOT add a `CHANGELOG.md`, `CONTRIBUTING.md`, or `CODE_OF_CONDUCT.md`.** These are standard open-source repo files but are not in the CONTRACT scope. They can be added in 0.2 if the project gains contributors. Adding them now is scope creep.

**Do NOT add GitHub Actions workflows.** SECURITY.md control 19 (CI CVE scanning) is stage 2. No `.github/workflows/` directory in 0.1.

**Do NOT add badges to the README** (build status, coverage, license shield). Badges require CI infrastructure that does not exist in 0.1.

**Do NOT make the README a comprehensive user guide.** The "Quick start" section is four steps. The goal is "auditable in an afternoon," not "comprehensive documentation." Brevity is a feature.

**Do NOT add a `Makefile` or `justfile`.** If development convenience scripts are wanted, they belong in `scripts/` as individual `.sh` files. A Makefile implies a build system the project does not have.

**Do NOT write the LOC count into the README before running `scripts/loc.sh` on the actual final state.** The count must be measured, not estimated. Writing an estimated count and then not updating it is an AC-8 violation.

---

## 7. Review Gates Checklist

In sequence, before closing this lane:

1. **`/plan-eng-review`** on this plan file — mandatory before code-implementer begins.

2. **`code-reviewer` Pre-Test Gate** — after Step 1 (`scripts/loc.sh`). Check: script is POSIX-compatible, caps are correct per CONTRACT, exit code logic is correct.

3. **`/codex-review`** on the Step 1 staged diff — before committing. Cannot skip: the LOC script is a verifiable contract claim. An incorrect script would invalidate the "auditable" positioning.

4. **`code-reviewer` Pre-Test Gate** — after Step 2 (README). Check: "What works" matches CONTRACT, "Out of scope" matches CONTRACT, no speculative claims, LOC count is the actual measured number.

5. `[SKIP /codex-review: docs-only change, README.md]` — README is a `.md` file, per REVIEW-PROTOCOL skip criteria. State the skip explicitly in the commit message.

6. **`code-reviewer` Pre-Test Gate** — after Step 3 (NOTICES.md). Check: all deps listed, design-reference note present, no missing attributions.

7. `[SKIP /codex-review: docs-only change, NOTICES.md]` — NOTICES is a `.md` file. State the skip explicitly.

8. **`code-reviewer` Post-Test Gate** — after brand-leak grep and Anthropic-import grep both pass. This is the final quality gate for the entire project.

9. **`git-steward`** — the final commit. Message: `"Release v0.1.0: all lanes merged, LOC verified, brand-leak clean"`. Include `LOC: total N/800` from `scripts/loc.sh` output. Include `Codex-reviewed (VERDICT: APPROVED)` for the `scripts/loc.sh` commit; mark the README and NOTICES commits with the appropriate skip line.
