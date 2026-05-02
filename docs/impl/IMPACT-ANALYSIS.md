# Impact Analysis — nanoclawfork v0.1

**Authoritative inputs:** `docs/spec/CONTRACT-0.1.md`, `docs/spec/SPEC-0.1.md`, `docs/spec/CONNECTOR-AUDIT-telegram.md`, `docs/REVIEW-PROTOCOL.md`, `docs/discovery/container/SECURITY.md`
**Date:** 2026-05-01

---

## 1. Executive Summary

v0.1 decomposes into **6 vertical lanes** plus **1 horizontal cross-cutting lane**, totalling ~450 effective LOC of core Python (hard cap 800) plus ~80 LOC of container/compose plumbing. Critical path is **L0 (scaffolding) → L1 (config + provider registry) → L2 (runtime) → L3 (memory) → L4 (Telegram connector) → L5 (container hardening) → L6 (release polish)**. L1 and L3 can run in parallel after L0; L4 depends on L2's runtime contract; L5 can begin in parallel with L1 once the package skeleton exists. Each lane fits in 1–3 Tiny Steps.

---

## 2. Lane Inventory

### L0: Repo scaffolding & cross-cutting primitives (horizontal)

- **Purpose:** Establish the package skeleton, brand-decoupling files, gitignore, license, attribution stubs, and the token-scrubbing logger that every other lane consumes.
- **LOC budget:** ~40 effective LOC (no module-specific cap; counted against runtime/connector budgets where the scrubber is wired in). Hard rule: must not exceed 60 LOC for scaffolding alone.
- **Public interface / contract:**
  - Package layout: `agent/__init__.py`, `agent/__about__.py` (exports `__brand__`, `__slug__`, `__version__`)
  - `pyproject.toml` with `[project].name = "nanoclawfork"`, MIT license declaration
  - `agent/logging.py` exports a `get_logger(name) -> logging.Logger` that installs a token-scrubbing filter (scrubs `TELEGRAM_BOT_TOKEN` value and any `*_API_KEY` env values from records before emit)
  - `.env.example`, `.gitignore` (includes `data/`, `config.yaml`, `.env`)
  - `NOTICES.md` stub
- **Inputs:** Contract (brand-decoupling rules, env var conventions).
- **Outputs:** Importable `agent` package; `get_logger` available to all downstream lanes.
- **Tests it must pass:**
  - Importing `agent` and `agent.__about__` works; `__brand__ == "nanoclawfork"`.
  - Logger redacts a known token value placed in a log record.
  - `grep -r nanoclawfork agent/` returns hits in only `__about__.py` (NFR-BD2).
- **Risk flags:** Scrubber over-redaction (false positives wiping useful logs); under-redaction (missing the token in error chains). Mitigation: scrubber reads token values from env at filter-init time and substitutes literal matches only.

### L1: Config schema + Provider registry

- **Purpose:** Load `config.yaml`, validate it with Pydantic, expose a typed registry that the runtime queries to resolve `provider/model-id`.
- **LOC budget:** Config schema 60/100, Provider registry 80/150. Combined target ~140 effective LOC.
- **Public interface / contract:**
  - `agent/config.py` exposes `load_config(path: Path) -> Config` where `Config` contains `providers: dict[str, ProviderSpec]` and `agents: dict[str, AgentGroupSpec]`.
  - `ProviderSpec` fields: `kind` (literal: `openai_compatible` | `anthropic`), `base_url`, `api_key_env` (str | None), `allowed_models: list[str]`.
  - `AgentGroupSpec` fields: `model: str` (form `<provider>/<model-id>`), `system_prompt: str`, `connectors: list[str]`.
  - `agent/registry.py` exposes `resolve(config: Config, model_ref: str) -> ResolvedProvider` returning `(kind, base_url, api_key, model_id)`. Raises `ConfigError` on unknown provider or disallowed model.
  - Ships `config.example.yaml` documenting every key (DeepInfra preset, Llama 3.3 70B default).
- **Inputs:** L0 logger.
- **Outputs:** Validated `Config` + `resolve()` for L2.
- **Tests it must pass:**
  - Loading `config.example.yaml` succeeds.
  - Unknown provider in `agents.*.model` raises `ConfigError` at startup.
  - Model not in `allowed_models` raises `ConfigError`.
  - Missing API key env var raises clear error naming the missing variable (FR-resolved: crash loudly).
- **Risk flags:** Speculative provider kinds (do not add `ollama`, `groq` plumbing in 0.1 — schema must accept them via config but only `openai_compatible` needs runtime resolution code). Mitigation: registry handles `openai_compatible` only; `anthropic` accepted by schema but raising `NotImplementedError` at resolve time is acceptable per anti-bloat rule.

### L2: Runtime wrapper (PydanticAI)

- **Purpose:** Wrap PydanticAI to take `(resolved_provider, system_prompt, message_history, user_text) → reply_text` with no Anthropic SDK in dependencies and with prompt-injection framing applied to user input.
- **LOC budget:** 50 target / 100 hard cap.
- **Public interface / contract:**
  - `agent/runtime.py` exposes `async def reply(provider: ResolvedProvider, system_prompt: str, history: list[Turn], user_text: str, chat_id: int) -> str`.
  - `Turn` is a simple dataclass/TypedDict: `{"role": "user"|"assistant", "content": str}`.
  - User text is wrapped in the framing envelope from CONNECTOR-AUDIT (`<user_message chat_id="…">…</user_message>` with closing-tag escape) **inside the runtime**, not in the connector.
  - No top-level import of `anthropic`. `pyproject.toml` does not list it.
- **Inputs:** L1 (`ResolvedProvider`).
- **Outputs:** `reply()` for L4 to call.
- **Tests it must pass:**
  - With a fake/mock OpenAI-compatible endpoint, `reply()` returns the assistant text.
  - User text containing a literal `</user_message>` is escaped in the framed payload.
  - `import anthropic` is not present anywhere in `agent/`.
- **Risk flags:** PydanticAI's API surface evolving; lock the pinned version. Cap breach risk if we hand-roll OpenAI HTTP calls instead of using PydanticAI's provider abstractions — must use the framework, not bypass it.

### L3: Memory (SQLite, per-chat)

- **Purpose:** Persist per-chat conversation turns to a SQLite file on the `agent-data` named volume; supply history slices to the runtime.
- **LOC budget:** 80 target / 150 hard cap.
- **Public interface / contract:**
  - `agent/memory.py` exposes:
    - `Memory(db_path: Path)` — opens/initializes SQLite (single table `turns(chat_id INTEGER, ts INTEGER, role TEXT, content TEXT)`).
    - `append(chat_id: int, role: str, content: str) -> None`
    - `history(chat_id: int, limit: int = 20) -> list[Turn]` — last 20 turns, oldest-first (resolved decision: keep last 20).
  - DB path defaults to `/data/agent.sqlite` (env: `AGENT_DATA_DIR`).
- **Inputs:** L0 logger; `Turn` shape from L2 (kept compatible — both lanes use the same dict shape; no cross-import).
- **Outputs:** `Memory` instance for the connector/main loop to call before/after `runtime.reply()`.
- **Tests it must pass:**
  - Append + history round-trip on tmpdir DB.
  - `history()` returns at most 20 turns ordered oldest-first.
  - Schema initialization is idempotent (`docker compose down && up` leaves data intact).
- **Risk flags:** Concurrency (single-writer assumption holds because connector is single-process polling); WAL mode optional but cheap. Avoid speculative migration framework — single `CREATE TABLE IF NOT EXISTS`.

### L4: Telegram connector

- **Purpose:** Poll Telegram, enforce chat-ID allowlist, enforce attachment size cap, route inbound text through memory + runtime, send replies.
- **LOC budget:** 150 target / 250 hard cap.
- **Public interface / contract:**
  - `agent/connectors/telegram.py` exposes `async def run(config: Config, registry_resolver, memory: Memory) -> None` — long-running polling loop.
  - Reads `TELEGRAM_BOT_TOKEN` and `ALLOWED_TELEGRAM_USER_IDS` (comma-separated ints) from env.
  - Auth check happens **before** any LLM/memory call. Unauthorized → silent drop (resolved OQ-2).
  - 5 MB attachment cap; oversize → silent drop in 0.1.
  - Uses `python-telegram-bot` (pinned).
- **Inputs:** L1 (Config + resolver), L2 (`runtime.reply`), L3 (`Memory`).
- **Outputs:** Running connector; emits structured logs (startup, message-received, provider-call, reply-sent, errors) via L0 logger.
- **Tests it must pass:**
  - Allowlist filter unit test: non-allowlisted chat_id → no call to runtime/memory.
  - Oversize attachment → drop, no call to runtime.
  - Token never appears in any captured log output (asserted via the L0 scrubber + a deliberate error injection test).
  - End-to-end happy path with mocked Telegram + mocked runtime.
- **Risk flags:** **Highest LOC risk.** Framework convenience features (handlers, filters, persistence) tempt scope creep. Mitigation: use the lowest-level `Application.run_polling` + a single `MessageHandler(filters.TEXT)`. **Security-auditor review required** before merge (REVIEW-PROTOCOL Gate: connectors are sensitive code).

### L5: Container hardening (Dockerfile, compose, mount policy, entrypoint)

- **Purpose:** Implement the 16 required SECURITY.md controls in shippable form.
- **LOC budget:** Dockerfile + entrypoint.sh 40/60; docker-compose.yaml 40/60. Plus a small `mount-policy.yaml` (data file, not counted).
- **Public interface / contract:**
  - `Dockerfile`: multi-stage (builder + runtime), `python:3.12-slim` runtime, non-root `agent` UID 10001, no build tools in runtime stage, no secrets baked.
  - `entrypoint.sh`: POSIX sh, `set -eu`, exec form, no shell features.
  - `docker-compose.yaml`: `read_only: true`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, named volume `agent-data:/data`, `config.yaml:/config/config.yaml:ro`, resource limits, default bridge network with egress (no `network_mode: host`).
  - `container/mount-policy.yaml`: declarative allowlist; explicitly forbids Docker socket.
  - `container/cli.py` (or `agent/__main__.py`) — CLI entrypoint, ≤30/50 LOC, just wires `Config + Memory + Connector.run`.
  - Stage-2 deferrals (controls 8, 9, 19, 20) explicitly listed in SECURITY.md "Known deferrals" section.
- **Inputs:** All other lanes (so the image actually runs the agent), but the file shapes can be drafted in parallel with L1–L4.
- **Outputs:** `docker compose up` working; verifiable via `docker inspect` for AC-6.
- **Tests it must pass:**
  - `docker inspect` confirms non-root UID, `ReadonlyRootfs: true`, no extra capabilities.
  - `docker compose down && up` round-trip preserves SQLite DB.
  - `tcpdump`/network inspection during run shows zero `api.anthropic.com` connections (AC-4).
  - All 16 required controls in SECURITY.md ticked with verification notes.
- **Risk flags:** Read-only root FS + Python tempdirs — must mount tmpfs `/tmp` if any dep writes there. Egress allowlist (control 10) is the trickiest — for 0.1 a single bridge network + DeepInfra outbound is sufficient; do NOT introduce per-agent networks (control 20 is stage 2). **Security-auditor review required.**

### L6: Release polish (README, NOTICES, LOC script)

- **Purpose:** Make the repo public-presentable and verifiable.
- **LOC budget:** Docs unbounded by the LOC rule (per global CLAUDE.md exemption); `scripts/loc.sh` ≤30 LOC.
- **Public interface / contract:**
  - `README.md` "What works" section quoting CONTRACT-0.1.md verbatim; "Out of scope" section quoting "Explicitly NOT in 0.x" verbatim; "Status" section quotes the measured LOC count from `scripts/loc.sh`.
  - `NOTICES.md` lists `python-telegram-bot` and any other lifted MIT-licensed dependencies; notes that no NanoClaw code was lifted (audit decision).
  - `scripts/loc.sh` counts effective Python LOC (blank/comment stripped) and reports per-module against caps.
- **Inputs:** All prior lanes complete (LOC count requires final state).
- **Outputs:** Releasable repo.
- **Tests it must pass:**
  - `scripts/loc.sh` runs and reports core Python ≤ 800 with each module ≤ its cap.
  - README "What works" matches CONTRACT acceptance items 1:1.
  - `git grep nanoclawfork` returns hits only in `pyproject.toml`, `agent/__about__.py`, README headline, and docs.
- **Risk flags:** Branding leak — last-mile chance to violate NFR-BD. Run the rename grep at this gate.

---

## 3. Dependency Graph

```
                     ┌──────────────────────────┐
                     │ L0 Scaffolding + Logger  │
                     └──────────────┬───────────┘
                                    │
                ┌───────────────────┼───────────────────┐
                ▼                   ▼                   ▼
          ┌──────────┐        ┌──────────┐        ┌──────────────┐
          │ L1 Config│        │ L3 Memory│        │ L5 Container │
          │ Registry │        │ (SQLite) │        │  hardening   │
          └────┬─────┘        └────┬─────┘        │ (file shapes │
               │                   │              │  drafted in  │
               ▼                   │              │  parallel,   │
          ┌──────────┐             │              │  validated   │
          │ L2 Runtime│            │              │  after L4)   │
          │ (Pydantic │            │              └──────┬───────┘
          │   AI)     │            │                     │
          └────┬──────┘            │                     │
               │                   │                     │
               └─────────┬─────────┘                     │
                         ▼                               │
                  ┌──────────────┐                       │
                  │ L4 Telegram  │                       │
                  │  connector   │                       │
                  └──────┬───────┘                       │
                         │                               │
                         └───────────────┬───────────────┘
                                         ▼
                                  ┌─────────────┐
                                  │ L6 Release  │
                                  │   polish    │
                                  └─────────────┘
```

| Lane | Hard depends on | Can start in parallel with |
|---|---|---|
| L0 | — | — |
| L1 | L0 | L3, L5 (Dockerfile draft) |
| L2 | L0, L1 | L3 |
| L3 | L0 | L1, L2, L5 |
| L4 | L0, L1, L2, L3 | (L5 final wiring) |
| L5 | L0 (skeleton); validates after L4 | L1, L3 (file drafts) |
| L6 | All prior lanes | — |

---

## 4. Sequence Recommendation

1. **L0 — Scaffolding + token-scrubbing logger.** First because every lane imports the logger and the package layout. Smallest possible step.
2. **L1 — Config + Provider registry.** Unblocks L2 and gives us a "hello-world" surface: a CLI that loads config and prints the active provider/model is verifiable before any messaging exists.
3. **L3 — Memory.** Independent of L2; can land in parallel with L2 if a second contributor picks it up. Sequenced second because the schema is trivial and keeps the critical path warm.
4. **L2 — Runtime wrapper.** Now testable end-to-end against DeepInfra with a stubbed CLI before Telegram exists — proves AC-3 and AC-4 (zero Anthropic traffic) **early**, when remediation is cheap.
5. **L4 — Telegram connector.** Last application-layer lane. Wires L1+L2+L3. Security-auditor gate required before merge.
6. **L5 — Container hardening final pass.** Dockerfile/compose drafts can begin alongside L1 (skeleton to keep `docker compose up` green from week 1), but the verifiable pass for AC-4/AC-6/AC-7 happens once the agent actually runs. Security-auditor gate required.
7. **L6 — Release polish.** README, NOTICES, `scripts/loc.sh`, brand-leak grep. Only meaningful once measured numbers are real.

**Why this order:** It puts the highest-risk verifiable claims (zero Anthropic traffic, container hardening) on the critical path **before** the connector, so the project's headline thesis is provable from a CLI smoke test, not gated on Telegram polling working.

---

## 5. Cross-Cutting Concerns

These belong to no single lane. L0 owns the artifact; every lane inherits the requirement.

| Concern | Owner | Inherited by |
|---|---|---|
| Token-scrubbing logger | L0 (`agent/logging.py`) | All lanes — must use `get_logger`, never `print`, never `logging.getLogger` directly |
| Prompt-injection framing | L2 (runtime applies the envelope) | L4 produces raw text only, must NOT pre-concatenate or pre-format user input |
| Structured logging fields | L0 sets the format; lanes emit | L1 (config-loaded), L2 (provider-call), L3 (memory R/W errors), L4 (msg-received, reply-sent, auth-drop) |
| LOC accounting | L6 ships `scripts/loc.sh` | Every lane's commit message must include `LOC: +x -y (module … now n/cap)` per REVIEW-PROTOCOL |
| Brand decoupling | L0 fixes the two-file rule; L6 verifies | Every lane forbids the literal string `nanoclawfork` in `agent/` source |
| Anti-bloat 6-question gate | REVIEW-PROTOCOL.md | Every reviewer at every lane gate |
| `/plan-eng-review` gate | After plan-writer per lane | Every lane (mandatory; not skippable for L1/L2/L4/L5 — security/state-machine surfaces) |
| `/codex-review` gate | Before each commit | Every lane; never skippable for L4, L5 |
| `security-auditor` deep pass | REVIEW-PROTOCOL Gate | Required for L4 (connector) and L5 (container); recommended for L1 (config/secret handling) |

---

## 6. Risk Register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | **L4 (Telegram) blows past 250 LOC cap** because `python-telegram-bot` features get pulled in (handlers, conversation states, persistence). | Med | High — forces re-plan, slows release | Plan-writer hands L4 a concrete "use only `Application.run_polling` + one `MessageHandler`" constraint. `/simplify` after each Tiny Step. Hard stop at 200 LOC for re-evaluation. |
| R2 | **Anthropic SDK or `api.anthropic.com` leaks into the call path** via a transitive dep of PydanticAI or a misconfig. | Low | Critical — kills the headline thesis (AC-4) | Pin PydanticAI version; add a `pip show` check that `anthropic` is not installed; add the `tcpdump`/`ss` smoke test as part of L2's acceptance, not deferred to L5. Add a CI-equivalent local script: `pip list \| grep -i anthropic` must be empty. |
| R3 | **Read-only root FS breaks Python at runtime** (some libs write to `~/.cache`, `/tmp`). | Med | Med — discovered late means rework in L5 | Draft Dockerfile + compose with tmpfs `/tmp` and `HOME=/data` from L5 day one. Smoke-test by running a no-op agent invocation under the hardened compose before L4 starts. |
| R4 | **Token leak via uncaught exception path** (e.g. `aiohttp` error includes the URL with an embedded token, or a third-party logger bypasses the filter). | Low | High — security incident, public repo | L0 scrubber installs as a `logging.Filter` on the root logger AND wraps `python-telegram-bot`'s logger. Add a unit test that injects a known token into a forced exception and asserts redaction. Security-auditor must verify in L4 gate. |
| R5 | **Scope creep into provider abstractions** ("just add Ollama support, it's small") pushing L1 over its 150 cap or pulling untested code paths into 0.1. | Med | Med — anti-bloat violation, audit story weakens | Stretch goal stays a stretch goal. Schema accepts `kind: ollama` but registry resolver implements `openai_compatible` only. New provider implementations are 0.2 work, full stop. |

---

## 7. Out-of-Scope Confirmation

This plan contains **no** lane, interface, or task for any of the following CONTRACT "Explicitly NOT in 0.x" items:

- ❌ Web UI / dashboard — none planned
- ❌ Multi-user / multi-tenant / multi-bot — single allowlist, single agent group
- ❌ WhatsApp / Slack / Discord / Gmail — only Telegram (L4)
- ❌ Tool / function calling — runtime returns text only
- ❌ Scheduled jobs / cron — no scheduler lane
- ❌ Cost-based routing / fallback chains — registry resolver returns one provider, no fallback logic
- ❌ VPS deployment — local Docker only (L5)
- ❌ Streaming responses — `runtime.reply` returns a complete string
- ❌ Vector recall / RAG — memory is plain SQLite turn log
- ❌ Episodic summarization — sliding-window of last 20 turns only (resolved decision)
- ❌ Image / audio / video input — text-only enforced in L4 message handler

The Ollama stretch goal from CONTRACT §"Stretch" is **not** included as a lane; it remains a post-0.1 follow-up unless 0.1 lands well under budget, in which case it can be added without blocking release.
