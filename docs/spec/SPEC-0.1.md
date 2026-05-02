# Product Specification — v0.1
**Working name:** nanoclawfork
**Status:** Locked for 0.1 build
**Authoritative scope document:** `docs/spec/CONTRACT-0.1.md`
**Date:** 2026-05-01

---

## 1. Overview

nanoclawfork is a lightweight, container-isolated, LLM-agnostic personal AI agent for a single developer. It takes NanoClaw's hardened container security model and pairs it with an OpenAI-compatible provider registry, so the agent can talk to DeepInfra, Ollama, Groq, Anthropic, or any other provider as equal-class peers — with no Anthropic SDK or protocol forced into the call path. The user interacts through Telegram text messages. The agent runs locally on the developer's Linux machine via `docker compose up`, persists conversation history across restarts, and is deployed as a public MIT-licensed repository from the first commit. The 0.1 release is a working, verifiable baseline: one connector, one provider, no bells.

---

## 2. User Stories

**US-1 — Daily messaging**
As a developer using Telegram daily, I want to send text messages to my agent and receive replies, so that I can interact with an LLM without leaving my preferred messaging client.

**US-2 — Provider freedom**
As a developer frustrated by LLM lock-in, I want to configure which LLM provider and model the agent uses via a YAML file, so that I can switch providers without touching code.

**US-3 — Conversation continuity**
As a developer who restarts Docker containers regularly, I want my conversation history to survive a `docker compose down && docker compose up` cycle, so that I do not lose context between sessions.

**US-4 — Access control**
As the sole user of this agent, I want only my Telegram chat ID to be able to send the agent messages, so that the agent does not respond to anyone who finds the bot token.

**US-5 — Verifiable security posture**
As a developer sharing this publicly on GitHub, I want the container hardening to be independently verifiable via `docker inspect` and a signed-off checklist, so that users who run forks can trust the security claims.

**US-6 — Future-proof renaming**
As a developer who has not yet settled on a final brand name, I want the project name to live in exactly two files, so that I can rename the project at any time without touching application code.

---

## 3. Functional Requirements

### 3.1 Messaging — Telegram Connector

- FR-M1: The system must accept inbound text messages from Telegram and return a text reply to the same chat.
- FR-M2: The system must enforce an allowlist of permitted Telegram chat IDs. Messages from any chat ID not in the allowlist must be silently ignored or rejected without producing a reply. The allowlist is configured via the `ALLOWED_TELEGRAM_USER_IDS` environment variable as a comma-separated list of integers.
- FR-M3: The Telegram bot token must be supplied exclusively via environment variable (`TELEGRAM_BOT_TOKEN` or the `AGENT_*` equivalent defined at build time). It must not be present in any committed file.
- FR-M4: The connector must be architecturally decoupled from the LLM runtime. Replacing or adding a connector must not require changes to the agent runtime or provider registry.

### 3.2 LLM Routing — Provider Registry

- FR-R1: The system must load provider configuration from `config.yaml` at startup. Provider definitions in that file must be the only mechanism by which a provider is enabled or changed.
- FR-R2: The DeepInfra provider (OpenAI-compatible API) must be the default and must be supported end-to-end in 0.1. The default model is `meta-llama/Llama-3.3-70B-Instruct`.
- FR-R3: The provider registry schema must support adding additional providers (e.g. Ollama, Groq, Anthropic) by editing `config.yaml` only, with no code changes required.
- FR-R4: Each provider definition in `config.yaml` must declare: a kind (e.g. `openai_compatible`, `anthropic`), a base URL, an API key environment variable name (nullable for local providers), and a list of allowed models.
- FR-R5: The agent runtime must be protocol-agnostic. It must not import or invoke the Anthropic SDK unless Anthropic is explicitly configured as a provider in `config.yaml`. The network call path must contain zero connections to `api.anthropic.com` when Anthropic is not configured.
- FR-R6: Each agent group defined in `config.yaml` must bind exactly one model (expressed as `provider/model-id`), a system prompt, and zero or more connectors. Different agent groups may reference different providers.

### 3.3 Memory — Per-Chat Persistence

- FR-ME1: The system must persist per-chat conversation history across container restarts. History is scoped to Telegram chat ID.
- FR-ME2: Conversation history must be stored in SQLite on a named Docker volume (`agent-data`). The database file must not live on the container's root filesystem.
- FR-ME3: The agent must include the stored conversation history for the current chat ID as context when generating each reply.
- FR-ME4: There is no automated truncation, summarization, or expiry of history in 0.1. The full stored history is passed as context on every turn.

### 3.4 Configuration — Schema and Env Var Conventions

- FR-C1: The system must ship a `config.example.yaml` that documents every supported key with inline comments. `config.yaml` (the live config) must be gitignored.
- FR-C2: All environment variables the system reads must use the `AGENT_` prefix, with the exception of provider-specific API key variables (e.g. `DEEPINFRA_API_KEY`, `TELEGRAM_BOT_TOKEN`) which use conventional provider names for compatibility.
- FR-C3: A `.env.example` file must document every required and optional environment variable. Secrets must never appear in any committed file.
- FR-C4: `config.yaml` must be mounted into the container read-only.

### 3.5 Lifecycle — Start, Stop, Restart, Observability

- FR-L1: `docker compose up` must be the complete deploy command. No pre-run scripts, no manual database initialization, no manual network creation steps.
- FR-L2: `docker compose down` followed by `docker compose up` must restore the agent to a fully operational state with history intact.
- FR-L3: The agent must emit structured logs (at minimum: startup confirmation, incoming message received, provider call made, reply sent, errors with context). Log output goes to stdout/stderr; the Docker daemon handles retention.
- FR-L4: The agent must log the active provider and model name at startup so the operator can confirm routing without inspecting network traffic.
- FR-L5: The system must not require the operator to restart the container to pick up changes to `config.yaml` model selection within an agent group. (Note: adding a new provider still requires restart — flagged as Open Question OQ-1.)

---

## 4. Non-Functional Requirements

### 4.1 Security

The authoritative security specification is `docs/discovery/container/SECURITY.md`, which defines 20 hardening controls. For 0.1 acceptance, **16 of the 20 controls must be verified and checked off** with inline verification notes in that file. The 4 controls explicitly deferred to stage 2 are:

| Control # | Control Name | Deferral Reason |
|-----------|-------------|-----------------|
| 8 | Seccomp profile (default-deny extended) | Requires custom profile authoring; stage 2 |
| 9 | AppArmor / SELinux confinement | Host-dependent; profile shipped in stage 2 |
| 19 | Image scanned for CVEs in CI | Requires GitHub Actions CI setup; stage 2 |
| 20 | Per-agent network isolation | Multiple bridge networks; stage 2 |

All 4 deferrals must be explicitly tracked in `SECURITY.md` under a "Known deferrals" section. Any relaxation of a non-deferred control must be documented with a `# RELAXED: <reason>` comment in `docker-compose.yaml` and noted in `SECURITY.md`. Silent relaxations are not permitted.

The threat model from `SECURITY.md` applies: the LLM is untrusted, the host is trusted, the provider endpoint is trusted-but-verify.

### 4.2 Performance

Performance targets for 0.1 are intentionally loose. The system is single-user, low-volume, text-only:

- NFR-P1: The agent must return a Telegram reply within the provider's normal inference latency. No additional processing latency target is imposed in 0.1.
- NFR-P2: The container must start and reach a ready state within 30 seconds on the developer's local machine under normal conditions.

### 4.3 Observability

- NFR-O1: All log output goes to stdout/stderr. No file-based logging inside the container.
- NFR-O2: Errors that prevent a reply (provider call failure, memory read/write failure) must log a human-readable error message with enough context to diagnose the issue without attaching a debugger.
- NFR-O3: The active provider, model, and connector(s) must be logged at startup.

### 4.4 Deployment

- NFR-D1: The system runs exclusively on Docker (local Linux machine) for 0.1. No Kubernetes, no VPS automation, no cloud deployment target.
- NFR-D2: The deploy surface is: `docker-compose.yaml`, `.env` (gitignored), `config.yaml` (gitignored). These three files are the operator's complete interface.

### 4.5 Licensing and Attribution

- NFR-LA1: The project is released under the MIT License.
- NFR-LA2: Any code lifted from `qwibitai/nanoclaw` (also MIT) must be attributed in `NOTICES.md` with the source file(s) and the applicable MIT copyright notice.
- NFR-LA3: The public repository must be created from the first commit. There is no "private until polished" phase.

### 4.6 Brand Decoupling

- NFR-BD1: The project's brand name must appear in exactly two files: `pyproject.toml` (the `[project].name` field) and `agent/__about__.py` (the `__brand__` and `__slug__` constants).
- NFR-BD2: Internal import paths must use the generic package name `agent/`. No import path may contain the working name `nanoclawfork` or any future brand name.
- NFR-BD3: Environment variables must use the `AGENT_` prefix. Container service names in `docker-compose.yaml` must use neutral names (`agent`, `router`). Config keys in `config.yaml` must use neutral vocabulary (`providers`, `agents`, `connectors`).
- NFR-BD4: A rename (picking a final brand) must require changes to exactly those two files plus a cosmetic README headline update. No additional code changes.

---

## 5. Acceptance Criteria

These are the eight "Done =" items from CONTRACT-0.1.md, expanded to independently verifiable form:

**AC-1 — Compose startup**
Given a machine with Docker and a valid `.env` file, when the operator runs `docker compose up`, then the agent container starts, reaches a ready state, and logs a startup confirmation without error.

**AC-2 — Telegram round-trip**
Given the agent is running and the operator's Telegram chat ID is in `ALLOWED_TELEGRAM_USER_IDS`, when the operator sends a text message to the bot, then the bot returns a text reply within normal inference latency.

**AC-3 — DeepInfra routing**
Given the agent is configured with the DeepInfra provider and `meta-llama/Llama-3.3-70B-Instruct`, when a message round-trip completes (AC-2), then the DeepInfra usage dashboard shows a request for that model, OR the startup log shows the active model is `deepinfra/meta-llama/Llama-3.3-70B-Instruct`.

**AC-4 — Zero Anthropic traffic**
Given the agent is running with only DeepInfra configured as a provider, when the operator captures outbound network traffic (e.g. via `tcpdump` or network inspection), then zero connections to `api.anthropic.com` appear during the lifetime of the agent process.

**AC-5 — Memory persistence**
Given the operator has had at least one conversation with the agent, when the operator runs `docker compose down` followed by `docker compose up` and sends a message referencing a fact from the previous session, then the agent demonstrates awareness of the prior context (i.e. the history was loaded from the SQLite volume).

**AC-6 — Hardened container**
Given the agent container is running, when the operator runs `docker inspect <container>`, then the output confirms: the running user UID is not 0 (non-root), `ReadonlyRootfs` is `true`, and capabilities do not include any beyond the dropped set.

**AC-7 — Security checklist signed off**
Given the `docs/discovery/container/SECURITY.md` checklist, when a reviewer reads the file, then all 16 controls required for 0.1 are marked verified with inline notes documenting how each was verified, and the 4 deferred controls are explicitly listed in a "Known deferrals" section.

**AC-8 — README truthful**
Given the public repository README, when a reader unfamiliar with the project reads the "What works" section, then every statement in that section corresponds to a passing item in this acceptance criteria list, and the "Out of scope" section matches the "Explicitly NOT in 0.x" list in CONTRACT-0.1.md verbatim.

---

## 6. Out of Scope

The following are explicitly NOT included in 0.1 or any 0.x release. Scope changes require a contract revision.

- Web UI or dashboard of any kind
- Multi-user, multi-tenant, or multi-bot deployment
- WhatsApp, Slack, Discord, or Gmail connectors
- Tool or function calling beyond a plain text reply (no file operations, web search, or shell execution inside the container)
- Scheduled jobs or cron-triggered messages
- Cost-based provider routing, fallback chains, or per-message routing decisions
- VPS deployment automation
- Streaming responses
- Vector recall or RAG (retrieval-augmented generation)
- Episodic memory summarization or automated history truncation
- Image, audio, or video input

---

## 7. Open Questions

These are gaps or ambiguities that spec-writer cannot resolve from the contract. Anson must answer these before plan-writer produces the implementation plan.

**OQ-1 — Hot-reload scope**
FR-L5 states that changing the active model for an existing agent group in `config.yaml` should not require a container restart. Is this truly a 0.1 requirement, or is a restart acceptable for any config change? The contract does not specify hot-reload behavior. If hot-reload is not required, FR-L5 should be relaxed to "restart required for any config change."

**OQ-2 — Allowlist rejection behavior**
FR-M2 says messages from non-allowlisted chat IDs should be "silently ignored or rejected." Should the bot send an explicit rejection message (e.g. "Unauthorized") to non-allowlisted senders, or must it be fully silent? Silent is more secure (does not confirm the bot exists), but explicit may be more useful for debugging. The contract says "single chat-id allowlist" but does not specify the rejection behavior.

**OQ-3 — Telegram connector lift decision**
The contract notes the Telegram connector from `qwibitai/nanoclaw` is a "lift candidate — pending security-auditor approval." Has the security-auditor review been done? If yes, is the connector cleared for lift with attribution, or must it be re-derived? This determines whether plan-writer should plan a lift+audit step or a re-derive step.

**OQ-4 — Memory context window behavior**
FR-ME4 states the full stored history is passed on every turn with no truncation. For long conversations this will eventually exceed the model's context window and cause an API error. Is the 0.1 behavior "fail with a logged error and no reply" or "silently truncate from oldest"? The contract does not specify the failure mode.

**OQ-5 — Startup failure behavior**
If the agent starts but cannot reach the DeepInfra API (e.g. invalid key, network issue), should `docker compose up` exit non-zero, or should the container stay up and log an error on the first message attempt? The contract specifies AC-1 (compose starts cleanly) but does not define the provider reachability check behavior.

---

## 8. Glossary

**PydanticAI**
A Python agent framework that is protocol-agnostic: it can target OpenAI-compatible APIs, Anthropic's native API, Ollama, and others as first-class peers without treating any one provider as the default. Used as the agent runtime in this project.

**Provider**
A configured LLM backend (e.g. DeepInfra, Groq, Ollama, Anthropic). Each provider entry in `config.yaml` declares its kind, base URL, API key variable, and the list of model IDs the operator permits it to serve.

**Model**
A specific LLM served by a provider, referenced in `config.yaml` as `provider/model-id` (e.g. `deepinfra/meta-llama/Llama-3.3-70B-Instruct`). Each agent group binds exactly one model.

**Connector**
The interface between a messaging platform and the agent runtime. The Telegram connector is the only connector in 0.1. Connectors are architecturally decoupled from the runtime so additional connectors can be added without modifying the agent core.

**Agent group**
A named configuration unit in `config.yaml` that binds a model, a system prompt, and one or more connectors. In 0.1, one agent group (`personal-assistant`) is active.

**Mount allowlist**
A policy file (`mount-policy.yaml`) that declares which host paths may be bind-mounted into the container. The Docker socket is explicitly forbidden. This is one of the 20 hardening controls in `SECURITY.md` (control #4).
