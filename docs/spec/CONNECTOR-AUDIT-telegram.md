# Telegram Connector Audit — Findings Summary

**Audit date:** May 2026
**Audited:** `qwibitai/nanoclaw` (channels branch), Telegram connector files
**Verdict:** **DO NOT LIFT** — language mismatch (TypeScript vs our Python)
**Decision:** Write own Python connector against `python-telegram-bot`, baking in the audit's security findings as design requirements.

---

## Why we're not lifting

NanoClaw's connector is TypeScript/Node, ~410 LOC Telegram-specific + ~400 LOC chat-sdk-bridge + transitive permissions schema. Our project is Python. Direct lift not possible. Porting it would be larger than writing a fresh Python connector against `python-telegram-bot` (which already provides the polling loop, file_id resolution, retry handling — the bulk of what NanoClaw lifts from Vercel's chat-sdk).

## Security requirements derived from the audit

These are non-negotiable design requirements for our Python Telegram connector. They become acceptance criteria for the connector lane.

### Must-have for 0.1

1. **Token-scrubbing logger.**
   The bot token must never appear in any log line, ever. Wrap fetch errors and transport errors in a scrubber that replaces the token with `[REDACTED]` before logging. Applies to the whole project, not just the connector.

2. **Attachment size cap.**
   Hard 5 MB cap on inbound attachments. Larger files dropped with a clear "too large" reply (or silently dropped, depending on chat-id allowlist). Prevents OOM via crafted inbound flood.

3. **Fail-closed authorization.**
   Unrecognized chat IDs → silent drop (per resolved OQ-2). Auth check happens BEFORE any LLM invocation, not after. A failure in the auth check itself drops the message, not passes it through.

4. **Prompt-injection framing.**
   Raw inbound user text MUST be wrapped in a clearly-labeled envelope before reaching the LLM. The runtime, not the connector, owns this — but the connector must produce text in a form the runtime can wrap unambiguously (e.g. emit raw text with no premature concatenation).

5. **`data/` in `.gitignore`.**
   Already done.

### Nice-to-have, can defer to 0.2+

- **Pairing flow** (NanoClaw's one-time-code chat ownership proof). For 0.1, the simpler `ALLOWED_TELEGRAM_USER_IDS` env var allowlist is sufficient.
- **Rate limiting per chat ID.**
- **Pairing code TTL** (irrelevant if we're not implementing pairing).

## Architectural notes (P3-1 from audit)

The audit flagged that any chat connector inherently creates a prompt-injection surface. Fixing it in the connector is the wrong layer — the runtime must treat all inbound text as untrusted and frame it. This is a runtime requirement, not a connector requirement, but worth noting here so plan-writer surfaces it in the runtime lane.

Recommended framing pattern:
```
&lt;user_message chat_id="{chat_id}"&gt;
{raw text, with closing tag escaped}
&lt;/user_message&gt;
```

## Dependencies the connector will use

- `python-telegram-bot` (latest stable, version pinned in pyproject.toml)
  - MIT-licensed, mature project
  - Handles polling, file_id resolution, attachment download, retry
  - We pin exact version, run `pip-audit` in CI for CVE detection (CI is 0.2+ work, manual check at 0.1)

## LOC estimate (informs lane budget)

| Component | Estimated LOC |
|---|---|
| Connector class (Connector interface impl) | 80 |
| Inbound message handler (allowlist + size check + envelope) | 50 |
| Token scrubber + log filter | 20 |
| Outbound sender | 30 |
| **Total** | **~180** |

Within the 250 LOC hard cap. Comfortable.

## Out of scope for 0.1 (carried from contract)

- Attachments other than text (no images, audio, voice notes)
- Inline keyboards / buttons
- Group chats (single chat per user)
- Edit/delete/reaction handling

## Sources

- Full audit transcript: in conversation history
- Upstream files audited: `qwibitai/nanoclaw@channels:src/channels/telegram.ts`, `telegram-pairing.ts`, `telegram-markdown-sanitize.ts`, `chat-sdk-bridge.ts`
