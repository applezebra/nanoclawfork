# Security Policy

> **This file is the vulnerability disclosure policy** — how to report a security issue in kayaclaw privately. It is **not** the container hardening checklist; that lives at [`docs/discovery/container/SECURITY.md`](docs/discovery/container/SECURITY.md). Same filename, different purpose. Researchers reporting a vulnerability should be on this page; auditors verifying container controls should be on the other.

## Supported versions

Only the latest 0.1.x release is supported. kayaclaw is pre-1.0 — there is no LTS, no backports, no parallel maintained branches. Run the latest tag.

| Version | Supported |
|---|---|
| 0.1.x   | ✅ |
| < 0.1   | ❌ |

## Trust model

A vulnerability is something that violates one of these assumptions. Anything within the assumptions is by design.

- **The bot operator is trusted.** The person running `docker compose up` controls the container, the host, and the env vars. They can read the SQLite database, they can read the logs, they can rotate the keys. We do not protect the operator from themselves.
- **The Telegram chat IDs in the `ALLOWED_TELEGRAM_CHAT_IDS` allowlist are trusted.** They can converse with the agent and the agent will store and recall their messages. They cannot escape the container or read other operators' data — but they can fully drive the LLM.
- **The LLM is untrusted.** Anything the LLM emits may be a prompt-injection attempt by an upstream actor. The runtime framing in `agent/runtime.py` is the boundary. Bypassing the framing is a vulnerability; tricking the LLM into producing a rude or off-topic reply is not.
- **The host OS and Docker daemon are trusted.** The operator chooses the host. Container escape via a Docker daemon bug is upstream; container escape via a misconfiguration in our `Dockerfile` or `docker-compose.yaml` is in scope.
- **Upstream provider APIs (DeepInfra, OpenAI, etc.) are out of scope.** A leak in the provider API is the provider's problem; report it to them.

## Reporting a vulnerability

**Two channels, in order of preference:**

1. **GitHub Security Advisories (preferred).** Go to the repo's "Security" tab → "Report a vulnerability". This creates a private advisory that only the maintainer sees, supports CVE assignment, and keeps the discussion off public issues. This is the right channel 95% of the time.

2. **Email fallback** (use only if GitHub is unavailable, or if the report cannot be filed via GitHub for some reason): [`security@kayaclaw.ai`](mailto:security@kayaclaw.ai). This forwards via Cloudflare Email Routing to a monitored inbox. PGP not required at v0.1; if you need encryption, file via GitHub Security Advisories instead.

**Please do not** open a public GitHub issue, post on social media, or contact me on Telegram with vulnerability details before the dual-channel disclosure window above.

**Response targets:**
- Initial acknowledgement: within 72 hours.
- Patch timeline: depends on severity. Critical issues with a working exploit get same-week turnaround; lower-severity issues are batched into the next release.

## In scope

- The agent runtime: prompt-injection framing in `agent/runtime.py`, anything that escapes the framing into the underlying LLM API call.
- Container isolation posture per [`docs/discovery/container/SECURITY.md`](docs/discovery/container/SECURITY.md): readonly root, capability drops, namespace isolation, tmpfs hardening, non-root UID, secret env handling.
- Secrets handling: the L0 logging scrubber (`agent/logging.py`), the `register_secret` API, traceback redaction.
- The SQLite memory layer (`agent/memory.py`): per-chat isolation, SQL injection safety, file-permission handling on the named volume.
- The Telegram allowlist enforcement in `agent/connectors/telegram.py` (`_is_allowed`).
- Env var validation in `_load_env()` — anything that allows an empty/missing required var to pass through silently.
- Provider registry resolution (`agent/registry.py`) — anything that allows an unintended provider kind to be activated.

## Out of scope

- Bugs in upstream providers (DeepInfra, OpenAI, Anthropic, etc.) — report them to the provider.
- Bugs in `python-telegram-bot`, `pydantic-ai-slim`, `pydantic`, `PyYAML`, or any other listed dependency in [`NOTICES.md`](NOTICES.md) — report upstream.
- Bugs in Docker Engine, the Linux kernel, or the host OS.
- Social engineering of bot operators (e.g. tricking an operator into setting `ALLOWED_TELEGRAM_CHAT_IDS=*`).
- Prompt-injection that does NOT cross a security boundary — e.g. tricking the LLM into producing a rude reply or refusing to answer is a UX issue, not a vulnerability. Tricking the LLM into emitting raw secrets, escaping the framing, or executing tool calls outside the declared boundary IS in scope.
- Any class of issue that requires the operator to have already shared the bot token with the attacker.

## Recognition

No bug bounty at v0.1. Accepted reports are credited (with reporter consent) in the release notes for the patched version. A `SECURITY-HALL-OF-FAME.md` will be added if and when the first accepted report lands.

## Cross-references

- Container control checklist (audit-side): [`docs/discovery/container/SECURITY.md`](docs/discovery/container/SECURITY.md)
- Third-party attribution and license terms: [`NOTICES.md`](NOTICES.md)
- License: [`LICENSE`](LICENSE) (MIT)
