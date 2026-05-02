# Implementation Decisions Log

Decisions made during plan-writer phase. Authoritative for code-implementer
when plan files conflict or are silent.

| # | Date | Decision | Rationale |
|---|---|---|---|
| D1 | 2026-05-01 | `kind: anthropic` raises at **`registry.resolve()`**, not `runtime.reply()`. Registry validates eagerly at config-load. | Aligns with "crash loudly at startup" (resolved OQ-5). User sees misconfig before first message. |
| D2 | 2026-05-01 | Use **`sqlite3` stdlib**, not `aiosqlite`. | One fewer dependency. Single-user, single-process polling has no async-DB benefit. Anti-bloat aligned. |
| D3 | 2026-05-01 | Telegram connector written **fresh in Python**, not lifted from NanoClaw. | Language mismatch (NanoClaw is TS). `python-telegram-bot` provides the bulk of what NanoClaw lifts from Vercel chat-sdk. See CONNECTOR-AUDIT-telegram.md. |

## How to use this log

- Plan files reflect decisions as of their write date. If a plan disagrees with a later entry here, this log wins.
- When code-implementer hits a question this log answers, no need to re-ask the user.
- When code-implementer hits a question this log does NOT answer, surface to the user before proceeding.
