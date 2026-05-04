# kayaclaw

Run your own personal AI agent in a hardened Docker container. It talks to you on Telegram, remembers conversations in SQLite, and swaps LLM providers via a config file — no Anthropic SDK in the runtime. Singapore-made personal AI agent inspired by [NanoClaw](https://github.com/qwibitai/nanoclaw)'s container security model, ~17× smaller (451 LOC of core Python), MIT licensed.

> **Stability:** 0.x is pre-1.0. APIs, config schema, and storage layout may change between minor versions. Pin a tag if that matters to you.

## What works (0.1)

1. **Telegram connector**, text-only, single chat-id allowlist (`ALLOWED_TELEGRAM_CHAT_IDS` env var).
2. **DeepInfra provider** end-to-end via OpenAI-compatible API. Default model: Llama 3.3 70B Instruct.
3. **PydanticAI runtime**, with no provider-vendor SDK as a hard top-level dependency. The OpenAI SDK ships transitively via `pydantic-ai-slim[openai]`; the Anthropic SDK is absent from the runtime container (verify after `docker compose build`: `docker run --rm kayaclaw-agent pip show anthropic` exits non-zero). The default `config.example.yaml` ships pointing at a non-Anthropic provider so the out-of-box install does not require an Anthropic account.
4. **SQLite memory** on a named Docker volume, per-chat history, survives container restart.
5. **Hardened container**: 16 of 20 controls in `docs/discovery/container/SECURITY.md` verified (the 4 deferred are explicitly tracked).
6. **Local Docker** deploy. `docker compose up` is the deploy command.
7. **Provider registry** loaded from `config.yaml`. DeepInfra configured. Schema supports adding more providers without code changes.
8. **Brand-decoupled code**: project name lives in exactly two source files (`pyproject.toml`, `agent/__about__.py`).
9. **Public repo** under MIT license, `NOTICES.md` attributing third-party deps.
10. **README** documents what works, what's in progress, what's explicitly out of 0.x.

## Status

**0.1 ships at 451 effective LOC of core Python** (run `bash scripts/loc.sh` to verify).

For comparison: that is roughly **17× smaller than NanoClaw at commit [`8bdc5c4`](https://github.com/qwibitai/nanoclaw/tree/8bdc5c421735) (~7,645 effective LOC)**. Our entire 0.1 fits inside what NanoClaw spends on its Telegram connector alone (~1,000 LOC). The whole agent is **auditable in an afternoon**.

The size reflects three deliberate choices:
- Library leverage (PydanticAI handles the LLM protocol, python-telegram-bot handles the bot lifecycle, sqlite3 from stdlib).
- Tight scope (one connector, one trusted user, no plugin system, no admin UI).
- Python is more compact than TypeScript for the same logic.

| Module | LOC / Hard cap |
|---|---|
| `agent/connectors/telegram.py` | 125 / 250 |
| `agent/runtime.py` | 92 / 100 |
| `agent/registry.py` | 69 / 150 |
| `agent/config.py` | 64 / 100 |
| `agent/memory.py` | 52 / 150 |
| `agent/__main__.py` | 49 / 50 |
| **Total** | **451 / 800** |

## Quick start

**Prerequisite:** Docker 20.10+ with the Compose v2 plugin. Verify with `docker compose version` (should report `Docker Compose version v2.x` or newer).

1. Clone the repo: `git clone https://github.com/kayaclaw/kayaclaw && cd kayaclaw`
2. Copy the templates: `cp .env.example .env && cp config.example.yaml config.yaml`
3. Fill in `.env`: your Telegram bot token (from [@BotFather](https://t.me/BotFather)), your allowed Telegram chat ID (from [@userinfobot](https://t.me/userinfobot)), and your provider API key — DeepInfra by default ([sign up at deepinfra.com](https://deepinfra.com) if you do not have an account). Change the provider in `config.yaml` to point at a different one.
4. Start it: `docker compose up -d`
5. Verify it is running: `docker compose ps` should show `kayaclaw-agent-1` with status `Up`. Tail logs with `docker compose logs -f` and you should see `Application started` from python-telegram-bot.
6. Send a message from your allowlisted chat to the bot — it replies via the configured LLM.

## Verifying the security posture

kayaclaw is independently verifiable, not just claimed:

- **Container hardening checklist:** [`docs/discovery/container/SECURITY.md`](docs/discovery/container/SECURITY.md) — 16 controls implemented, 4 deferred to stage 2. Every control has a runnable `docker inspect` or `docker run` verification command. Anyone can clone the repo and re-prove every claim in 5 minutes.
- **Vulnerability disclosure policy:** [`SECURITY.md`](SECURITY.md) — how to report a security issue privately via GitHub Security Advisories or `security@kayaclaw.ai`.
- **Live container inspection:** `docker inspect kayaclaw-agent-1 -f '{{.HostConfig.ReadonlyRootfs}} {{.HostConfig.CapDrop}} {{.HostConfig.SecurityOpt}}'` after `docker compose up` returns `true [ALL] [no-new-privileges:true]`.

## Where 1.x conversations start

The following are deliberately out of scope for the 0.x line. The smallness is the value proposition — adding any of these without a clear case dilutes it. Open an issue if you want to make the case for one:

- Web UI / dashboard
- Multi-user, multi-tenant, or multi-bot deployment
- WhatsApp, Slack, Discord, Gmail connectors
- Tool / function calling beyond a text reply (no file ops, web search, bash-in-container)
- Scheduled jobs / cron
- Cost-based provider routing, fallback chains, or per-message routing
- VPS deployment automation
- Streaming responses
- Vector recall / RAG
- Episodic memory summarization
- Image, audio, video input

## Contributing

Open an issue before sending a PR — describe what you want to change and why, and wait for a green light. Bug reports should include your OS, Docker version (`docker compose version`), and the relevant `docker compose logs` output.

## License

MIT. See [`LICENSE`](LICENSE) and [`NOTICES.md`](NOTICES.md) for third-party attributions.

## Provenance

kayaclaw is a clean-room re-implementation of [NanoClaw](https://github.com/qwibitai/nanoclaw)'s container security posture in Python — same hardening controls, no Anthropic lock-in, ~17× smaller. NanoClaw was consulted as a design reference; **no NanoClaw source code is included.** Verify the clean-room claim with `git log --all --full-history -- agent/` (every commit is authored by Anson Zeall; there is no upstream merge point). Full attribution and licence terms live in [`NOTICES.md`](NOTICES.md).
