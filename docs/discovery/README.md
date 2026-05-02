# NanoClaw Fork (working title)

A lightweight, container-isolated, **LLM-agnostic** personal AI agent.

> **Status:** Scaffold. Not yet runnable end-to-end.
> **Name:** Placeholder. See [BRANDING.md](./BRANDING.md) for the rename plan.

## Thesis

Two existing projects bracket the design space:

- **OpenClaw** — multi-LLM (provider registry baked in), but heavyweight and weak container isolation.
- **NanoClaw** — strong container isolation, but locked to Anthropic via Claude Agent SDK.

This project takes NanoClaw's **security model** and pairs it with OpenClaw's **provider-agnostic core**, on top of a runtime (PydanticAI) that doesn't speak any specific provider's protocol natively.

No proxy tricks. No "pretend to be Anthropic." The agent talks OpenAI-compatible, Anthropic, Ollama, DeepInfra, Together, Groq, etc. as **equal-class first-party providers**.

## Architecture

```
┌─────────────────────────────────────────────┐
│  Connectors: Telegram / WhatsApp / HTTP     │
└─────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│  Agent Runtime: PydanticAI                  │
│  (protocol-agnostic; no Anthropic in path)  │
└─────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│  Provider Registry (config.yaml)            │
│  deepinfra / ollama / openai / anthropic /  │
│  groq / together / any OpenAI-compat        │
└─────────────────────────────────────────────┘

       Wrapped in NanoClaw-equivalent isolation:
┌─────────────────────────────────────────────┐
│  - per-agent Docker container               │
│  - explicit mount allowlist                 │
│  - dropped capabilities, non-root user      │
│  - read-only root filesystem                │
│  - no docker socket, no host network        │
│  - egress allowlist                         │
└─────────────────────────────────────────────┘
```

## Security parity with NanoClaw

This project commits to matching NanoClaw's container security posture. See [container/SECURITY.md](./container/SECURITY.md) for the full hardening checklist and rationale per control.

## Quick layout

```
nanoclawfork/
├── BRANDING.md                  # rename plan
├── README.md
├── pyproject.toml
├── config.example.yaml          # provider registry + agent groups
├── docker-compose.yaml
├── .gitignore
│
├── agent/                       # runtime (generic name — survives rename)
│   ├── __about__.py             # brand-name constants (one of two rename sites)
│   ├── runtime.py               # PydanticAI agent loop
│   ├── providers.py             # provider registry loader
│   ├── memory.py                # SQLite memory
│   └── connectors/
│       ├── base.py              # connector interface
│       └── telegram.py          # first connector (stub)
│
├── container/
│   ├── Dockerfile               # hardened, matches NanoClaw posture
│   ├── entrypoint.sh
│   ├── mount-policy.example.yaml
│   └── SECURITY.md              # security control checklist
│
└── agents/
    └── examples/
        └── personal-assistant.yaml
```

## Next steps (MVP scope)

1. Provider registry loader → can talk to DeepInfra Llama 3.3 from a CLI test
2. Telegram connector → end-to-end message in/out
3. Memory + scheduled jobs → SQLite, mounted volume
4. Container hardening → SECURITY.md checklist all green
5. Second provider (Ollama local) → prove the registry isn't a one-off
