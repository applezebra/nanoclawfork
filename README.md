# kayaclaw

A personal AI agent that runs in a hardened Docker container. Telegram in front, SQLite for memory, your choice of LLM provider via config. Made in Singapore. MIT licensed.

## What works (0.1)

1. **Telegram connector**, text-only, single chat-id allowlist (`ALLOWED_TELEGRAM_CHAT_IDS` env var).
2. **DeepInfra provider** end-to-end via OpenAI-compatible API. Default model: Llama 3.3 70B Instruct.
3. **PydanticAI runtime** with model-agnostic tool calling. Bring your own LLM: most major providers today speak a common HTTP format, and kayaclaw works with all of them (DeepInfra, OpenRouter, Groq, Together, your self-hosted vLLM). One config line, different model.
4. **SQLite memory** on a named Docker volume, per-chat history, survives container restart.
5. **Hardened container**: 16 of 20 controls in `docs/discovery/container/SECURITY.md` verified (the 4 deferred are explicitly tracked).
6. **Local Docker** deploy. `docker compose up` is the deploy command.
7. **Provider registry** loaded from `config.yaml`. DeepInfra configured. Schema supports adding more providers without code changes.
8. **Brand-decoupled code**: project name lives in exactly two source files (`pyproject.toml`, `agent/__about__.py`).
9. **Public repo** under MIT license, `NOTICES.md` attributing third-party deps.
10. **README** documents what works, what's in progress, what's explicitly out of 0.x.

> **Stability:** 0.x is pre-1.0. APIs, config schema, and storage layout may change between minor versions. Pin a tag if that matters to you.

## Status

0.1 is small enough to read in a sitting. Three deliberate choices keep it that way:

- Library leverage. PydanticAI handles the LLM protocol, python-telegram-bot handles the bot lifecycle, sqlite3 from stdlib.
- Tight scope. One connector, one trusted user, no plugin system, no admin UI.
- Anti-bloat as a discipline. Every module has a hard size cap enforced by `bash scripts/loc.sh` in CI. New features earn their place or they don't ship.

## Quick start

**Prerequisite:** Docker 20.10+ with the Compose v2 plugin. Verify with `docker compose version` (should report `Docker Compose version v2.x` or newer).

1. Clone the repo: `git clone https://github.com/kayaclaw/kayaclaw && cd kayaclaw`
2. Copy the templates: `cp .env.example .env && cp config.example.yaml config.yaml`
3. Fill in `.env`: your Telegram bot token (from [@BotFather](https://t.me/BotFather)), your allowed Telegram chat ID (message [@userinfobot](https://t.me/userinfobot) and it will reply with your chat ID), and your provider API key — DeepInfra by default ([sign up at deepinfra.com](https://deepinfra.com) if you do not have an account). Change the provider in `config.yaml` to point at a different one.
4. Start it: `docker compose up -d`
5. Verify it is running: `docker compose ps` should show `kayaclaw-agent-1` with status `Up`. Tail logs with `docker compose logs -f` and you should see `Application started` from python-telegram-bot.
6. Send a message from your allowlisted chat to the bot — it replies via the configured LLM.

## Switching providers

kayaclaw works with any provider that speaks the OpenAI-compatible HTTP format. Whatever model that provider offers, you can point your agent at it. The configs below are starting points. The same shape works for any other model the provider serves: list it in `allowed_models` and reference it in `agents.personal-assistant.model`.

### DeepInfra (default)

DeepInfra hosts open-source models on its own infrastructure. Sign up at [deepinfra.com](https://deepinfra.com), create an API key, set `DEEPINFRA_API_KEY` in your `.env`. The default config in `config.example.yaml` uses Llama 3.3 70B Instruct. Any model DeepInfra serves works the same way.

### OpenRouter

OpenRouter routes to many upstream models from one OpenAI-compatible endpoint. Sign up at [openrouter.ai](https://openrouter.ai), create an API key, set `OPENROUTER_API_KEY` in your `.env`, then pick any model in `config.yaml`.

Llama 3.3 70B Instruct:

```yaml
providers:
  openrouter:
    kind: openai_compatible
    base_url: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY
    allowed_models:
      - meta-llama/llama-3.3-70b-instruct

agents:
  personal-assistant:
    model: openrouter/meta-llama/llama-3.3-70b-instruct
    system_prompt: "You are a helpful assistant."
    connectors:
      - telegram
```

Anthropic Claude (sonnet-4.5):

```yaml
providers:
  openrouter:
    kind: openai_compatible
    base_url: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY
    allowed_models:
      - anthropic/claude-sonnet-4.5

agents:
  personal-assistant:
    model: openrouter/anthropic/claude-sonnet-4.5
    system_prompt: "You are a helpful assistant."
    connectors:
      - telegram
```

Google Gemini 2.0 Flash:

```yaml
providers:
  openrouter:
    kind: openai_compatible
    base_url: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY
    allowed_models:
      - google/gemini-2.0-flash-001

agents:
  personal-assistant:
    model: openrouter/google/gemini-2.0-flash-001
    system_prompt: "You are a helpful assistant."
    connectors:
      - telegram
```

Any other OpenRouter-served model works the same way: list it in `allowed_models` and reference it in `agents.personal-assistant.model`.

### Groq

Groq runs open-source models on its own inference hardware. Sign up at [console.groq.com](https://console.groq.com), create an API key, set `GROQ_API_KEY` in your `.env`. Llama 3.3 70B Versatile:

```yaml
providers:
  groq:
    kind: openai_compatible
    base_url: https://api.groq.com/openai/v1
    api_key_env: GROQ_API_KEY
    allowed_models:
      - llama-3.3-70b-versatile

agents:
  personal-assistant:
    model: groq/llama-3.3-70b-versatile
    system_prompt: "You are a helpful assistant."
    connectors:
      - telegram
```

Any other Groq-served model works the same way.

## Verifying the security posture

kayaclaw is independently verifiable, not just claimed:

- **Container hardening checklist:** [`docs/discovery/container/SECURITY.md`](docs/discovery/container/SECURITY.md) — 16 controls implemented, 4 deferred to stage 2. Every control has a runnable `docker inspect` or `docker run` verification command. Anyone can clone the repo and re-prove every claim.
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

kayaclaw is a clean-room re-implementation of [NanoClaw](https://github.com/qwibitai/nanoclaw)'s container security posture, written in Python. NanoClaw was consulted as a design reference. No NanoClaw source code is included. Verify the clean-room claim with `git log --all --full-history -- agent/`. Full attribution lives in [`NOTICES.md`](NOTICES.md).
