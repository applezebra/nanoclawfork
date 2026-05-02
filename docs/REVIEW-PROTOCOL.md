# Review Protocol

This document governs every code change in this project. It is binding on
all SDLC phases and applies to every commit, every PR, every release.

If a review gate is skipped, the commit message must state the skip and the
reason. Silent skips are not permitted.

---

## Core principles

1. **Small beats clever.** This project's value proposition is auditability.
   A shorter, simpler implementation is always preferred over a longer,
   cleverer one — even if the cleverer one is theoretically more elegant.
2. **No speculative abstraction.** Don't add interfaces, plugins, or
   configuration knobs for "future flexibility." Add them when the second
   use case actually arrives.
3. **No premature DRY.** Three similar lines beat a wrong abstraction.
   Tolerate duplication until the third occurrence makes the right shape
   obvious.
4. **No silent feature creep.** A bug fix doesn't get cleanup riders.
   A feature doesn't get unrelated improvements. Each commit does one thing.
5. **Document the WHY, not the WHAT.** Comments only when removing them
   would surprise a future reader. Well-named code is its own documentation.

---

## Anti-bloat gate (runs at every review point)

The reviewer at every gate must answer YES to all of these before approving:

- [ ] **Was anything added that isn't required by the spec or contract?**
      If yes — remove it or amend the spec.
- [ ] **Is there a simpler implementation that would also pass acceptance?**
      If yes — use the simpler one.
- [ ] **Is any new abstraction (interface, base class, plugin point) earning
      its keep with at least two concrete uses?** If no — inline it.
- [ ] **Does the diff include any unused parameters, dead branches, or
      "just in case" code paths?** If yes — delete them.
- [ ] **Are LOC budgets in `CONTRACT-0.1.md` still respected after this
      change?** If no — stop and re-plan, do not merge.
- [ ] **Are there comments explaining WHAT the code does (vs WHY)?**
      If yes — delete them or rename the code.

---

## Gate sequence per change

| Stage | Tool | What it checks |
|---|---|---|
| Plan stage | `/plan-eng-review` (mandatory) | Architecture, plan-stage P1/P2 issues, scope boundary, LOC budgets per lane |
| Pre-test (per Tiny Step) | `code-reviewer` agent | Size constraints (≤300 LOC, ≤3 files), obvious bugs, anti-bloat checklist above |
| Code review (per staged diff) | `/codex-review` (mandatory) | Independent review by OpenAI Codex; concrete issues (injection, off-by-one, unsafe regex, missing validation, false positives) |
| Per landed lane | `/simplify` skill | Reviews changed code for reuse, quality, efficiency, fixes any issues found |
| At 500 LOC milestone | `code-reviewer` Gate 0 | Full structural health pass: files >400 LOC, functions >50 LOC, abstraction opportunities, naming drift |
| Post-test | `code-reviewer` agent | Architecture fit, performance, maintainability, edge cases |
| Sensitive code (auth, secrets, container, connectors) | `security-auditor` agent | Read-only deep security pass |
| Pre-commit | `git-steward` | Final commit; commit message includes `Codex-reviewed (VERDICT: ...)` line |

---

## LOC budget enforcement

Per-module LOC budgets are defined in `docs/spec/CONTRACT-0.1.md` (Size
Discipline section). They are non-negotiable for 0.1.

If a module approaches its hard cap during a lane:

1. **Stop the lane.** Do not push through.
2. **Identify the bloat source.** Is it scope creep, premature abstraction,
   or a legitimate complexity that the budget underestimated?
3. **Choose one of:**
   - **(a) Scope cut** — drop a feature from this lane to 0.2.
   - **(b) Refactor** — `/simplify` pass on the lane, target ≥20% reduction.
   - **(c) Accept and document** — only if (a) and (b) are infeasible. Must
     update the contract with a justification AND extend the cap explicitly.
   - **(d) Ask** — surface the trade-off to the user before deciding.

---

## Commit message format

Every commit message must include the verdict line from `/codex-review`:

```
&lt;Subject line — imperative mood, ≤72 chars&gt;

&lt;Body — what changed and why, not how&gt;

Codex-reviewed (VERDICT: APPROVED | APPROVED-WITH-NITS | REJECTED): &lt;1-line summary&gt;
LOC: +&lt;added&gt; -&lt;removed&gt; (module &lt;name&gt; now &lt;n&gt;/&lt;cap&gt;)
```

Example:

```
Add Telegram inbound handler with allowlist enforcement

Polling loop receives updates, filters by ALLOWED_TELEGRAM_USER_IDS
env var, drops unauthorized messages silently. Bot token never logged.

Codex-reviewed (VERDICT: APPROVED-WITH-NITS): Add explicit Optional
type hint on attachment field; otherwise clean.
LOC: +148 -0 (module connectors/telegram now 148/250)
```

---

## Skip permissions (rare)

The only valid reasons to skip a gate:

| Gate | Valid skip reasons |
|---|---|
| `/plan-eng-review` | Pure config scaffolds, <100 LOC tactical fixes (per global CLAUDE.md). Never skip for security, multi-tenancy, retrieval, LLM prompting, state machines, container hardening. |
| `/codex-review` | Pure docs (`*.md` only), <20 LOC trivial fix (typo, comment, config rename with no logic change), routine dep bump with no behavior change. Never skip for security, multi-tenancy, retrieval/LLM, citation, scheduler, new public API surface. |
| Other gates | Never skip. |

A skip must be stated explicitly in the commit message:

```
[SKIP /codex-review: docs-only change, README typo fix]
```

---

## Updating this document

This document is itself subject to review. Changes to it must:
1. Go through a PR
2. Be reviewed by `code-reviewer`
3. Reference the rationale (incident, lesson learned, or upstream best
   practice change) that prompted the change

The protocol is a living document but not a casual one.
