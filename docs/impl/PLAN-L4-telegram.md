# Implementation Plan — L4: Telegram Connector

**Lane:** L4
**Version:** 0.1
**Status:** Ready for `/plan-eng-review`
**Depends on:** L0 (logger), L1 (Config, ResolvedProvider, registry.resolve), L2 (runtime.reply), L3 (Memory)
**Blocks:** L5 final validation (container must run a real agent), L6
**Can run in parallel with:** L5 file-drafting phase

---

## Mandatory gate before code-implementer begins

`/plan-eng-review` on this plan file is required before code-implementer begins. This is the **highest-risk lane** (IMPACT-ANALYSIS R1: "Highest LOC risk") and a security-sensitive surface (CONNECTOR-AUDIT findings are the direct source of requirements). `/plan-eng-review` is non-skippable: auth, prompt injection, and token handling are all present in this lane.

---

## 1. Lane Summary

L4 implements the Telegram polling connector: it authenticates the bot token, enforces the chat-ID allowlist before any LLM call, enforces a 5 MB attachment size cap, routes inbound text messages through memory + runtime, and sends replies. It is the only connector in 0.1 and the only lane that touches the external Telegram API.

**LOC budget:** 150 target / 250 hard cap (effective Python LOC, blank/comment-stripped). Hard stop at 200 LOC for mandatory re-evaluation before continuing.

---

## 2. WHAT — Artifacts to Produce

| Artifact | Path |
|---|---|
| Connector package init | `agent/connectors/__init__.py` |
| Telegram connector | `agent/connectors/telegram.py` |
| Tests | `tests/test_l4_telegram.py` |

---

## 3. WHY — Rationale per Artifact

- **`agent/connectors/__init__.py`** — Creates the `connectors` sub-package so `from agent.connectors.telegram import run` works. Empty file; no logic. The discovery sketch (`docs/discovery/agent/connectors/base.py`) includes a `Connector` abstract base class — that is NOT included in 0.1 (REVIEW-PROTOCOL: "No speculative abstraction; don't add interfaces for 'future flexibility'"). With only one connector, the abstract base has no second concrete use and would cost LOC with no present benefit.

- **`agent/connectors/telegram.py`** — FR-M1: accept inbound text and return a reply. FR-M2: enforce allowlist, silent drop for non-allowlisted senders. FR-M3: token from env var only. FR-M4: connector is decoupled from LLM runtime (connector calls `runtime.reply` but does not know how it works). CONNECTOR-AUDIT must-have items 1–4 (scrubbing, size cap, fail-closed auth, framing). AC-2 (Telegram round-trip) and AC-4 (zero Anthropic traffic, verified end-to-end in the real polling loop).

---

## 4. HOW — Tiny Step Breakdown

### Step 1 — Connector package and env-var validation

**Files touched (≤3):**
1. `agent/connectors/__init__.py`
2. `agent/connectors/telegram.py` (partial — startup validation only, no polling loop)
3. `tests/test_l4_telegram.py`

**LOC estimate:** ~30 effective LOC in `agent/connectors/telegram.py` for this step.

**What to write:**

`agent/connectors/__init__.py` — empty file (just the package marker).

`agent/connectors/telegram.py` — Part 1 of 3: startup validation.

Define a module-level function `_load_env() -> tuple[str, set[int]]` that:
1. Reads `TELEGRAM_BOT_TOKEN` from env. If absent or empty, raises `ConfigError` (imported from `agent.config`) with message naming the missing variable. Crash loudly — per CONTRACT resolved decisions.
2. Reads `ALLOWED_TELEGRAM_USER_IDS` from env. If absent or empty, raises `ConfigError` naming the variable and explaining that at least one chat ID is required.
3. Parses the comma-separated IDs: `{int(s.strip()) for s in value.split(",") if s.strip()}`. If any segment is not a valid integer, raises `ConfigError` with the offending segment named. Returns `(token, allowlist_set)`.

The token string is never logged. The allowlist set is logged at startup at INFO level: `"Allowlist loaded: {len(allowlist)} chat ID(s)"` — the count is logged, not the IDs themselves (the IDs are not secret but there is no reason to emit them).

**Why a module-level function rather than a class `__init__`:** The IMPACT-ANALYSIS public interface declares `async def run(config: Config, registry_resolver, memory: Memory) -> None` as the connector's entry point. A module-level `run()` function is simpler than a class: no constructor, no `__init__`, no state lifecycle to manage. The token and allowlist are local variables within `run()`, not class attributes.

**Tests to add:**

```
tests/test_l4_telegram.py  (Step 1 portion)
```

- Set `TELEGRAM_BOT_TOKEN=fake-token` and `ALLOWED_TELEGRAM_USER_IDS=12345,67890` — call `_load_env()` — assert returns `("fake-token", {12345, 67890})`.
- Unset `TELEGRAM_BOT_TOKEN` — call `_load_env()` — assert raises `ConfigError` whose message contains `"TELEGRAM_BOT_TOKEN"`.
- Unset `ALLOWED_TELEGRAM_USER_IDS` — call `_load_env()` — assert raises `ConfigError` whose message contains `"ALLOWED_TELEGRAM_USER_IDS"`.
- Set `ALLOWED_TELEGRAM_USER_IDS=12345,notanint,67890` — call `_load_env()` — assert raises `ConfigError` naming `"notanint"`.
- Assert that captured log output from `_load_env()` does not contain the literal fake token value (scrubber test, cross-lane).

**Acceptance check before proceeding to Step 2:**
- Step 1 tests all pass.
- `_load_env()` is importable.

---

### Step 2 — Allowlist filter and attachment size check

**Files touched (≤3):**
1. `agent/connectors/telegram.py` (extend — add `_is_allowed`, `_check_attachment_size`)
2. `tests/test_l4_telegram.py` (extend)

**LOC estimate:** ~40 effective LOC in `agent/connectors/telegram.py` for this step. Cumulative: ~70 LOC.

**What to write:**

`agent/connectors/telegram.py` — Part 2 of 3: auth and size guard.

`_is_allowed(chat_id: int, allowlist: set[int]) -> bool`:
- Returns `chat_id in allowlist`. No exceptions, no logging — the caller logs.
- Why private and pure: keeps the allowlist check testable in isolation, independent of the Telegram library. A unit test can call this function directly with synthetic IDs without mocking the Telegram library.

`_check_attachment_size(message) -> bool`:
- Takes a `python-telegram-bot` `Message` object (or duck-typed equivalent for tests).
- Returns `True` if the message has no attachments, or if all attachments are within the 5 MB cap.
- The 5 MB cap in bytes: `5 * 1024 * 1024 = 5_242_880`.
- For 0.1, "attachment" means any `Message` attribute that is not `text`: `photo`, `document`, `audio`, `video`, `voice`, `video_note`, `sticker`. If any of these are present, check the file size against the cap. If over cap, return `False`.
- Why check all non-text types even though 0.1 is text-only: in the polling loop, the `MessageHandler` will be configured with `filters.TEXT` to ignore non-text updates. However, `_check_attachment_size` is still implemented as a safety belt for any message that slips through, and the logic documents the intent clearly for a future code auditor.
- Note to code-implementer: in 0.1 the `MessageHandler` with `filters.TEXT` effectively means `_check_attachment_size` will only be called on text messages, which will always return `True`. The function is still implemented because: (a) the CONNECTOR-AUDIT requires the size cap as a design requirement, and (b) it documents the policy for future maintainers.

The message handler that ties these together (inline in the polling loop function) must call `_is_allowed` BEFORE `_check_attachment_size` and BEFORE any call to `memory` or `runtime`. The authorization check is first, full stop. CONNECTOR-AUDIT finding #3: "Auth check happens BEFORE any LLM invocation, not after."

For an unauthorized message: log at INFO via `get_logger("agent.connectors.telegram")`: `"auth-drop: chat_id={chat_id}"`. Then return (no reply, no LLM call, no memory write). Silent drop — per CONTRACT resolved decision OQ-2.

For an oversized attachment: log at INFO: `"size-drop: chat_id={chat_id}"`. Then return. Silent drop.

**Tests to add (extend `tests/test_l4_telegram.py`):**

- `_is_allowed(12345, {12345, 67890})` → `True`.
- `_is_allowed(99999, {12345, 67890})` → `False`.
- `_is_allowed(12345, set())` → `False` (empty allowlist blocks everyone).
- Mock a `Message` with no attachments — `_check_attachment_size(msg)` → `True`.
- Mock a `Message` with a `document.file_size = 4_000_000` (under cap) → `True`.
- Mock a `Message` with a `document.file_size = 6_000_000` (over cap) → `False`.
- Integration: allowlist filter unit test — assert a message handler that combines `_is_allowed` and `_check_attachment_size` calls neither `memory` nor `runtime` for an unauthorized chat ID. (Use a mock for memory and runtime; assert mock was not called.)

**Acceptance check before proceeding to Step 3:**
- Step 2 tests all pass.
- Allowlist filter has 100% branch coverage (allowed, denied).

---

### Step 3 — Polling loop and reply sender

**Files touched (≤3):**
1. `agent/connectors/telegram.py` (complete — add `run()` polling loop)
2. `tests/test_l4_telegram.py` (extend — end-to-end happy path with mocked Telegram)

**LOC estimate:** ~80 effective LOC in `agent/connectors/telegram.py` for this step. Cumulative: ~150 LOC — at target cap.

**What to write:**

`agent/connectors/telegram.py` — Part 3 of 3: the `run()` function.

`async def run(config: Config, registry_resolver, memory: Memory) -> None`:

This is the long-running polling loop. Its responsibilities in order:

**Startup sequence (inside `run()`):**
1. Call `_load_env()` to get `(token, allowlist)`. If this raises `ConfigError`, let it propagate — the CLI entrypoint handles it.
2. Resolve the provider for the configured agent group: call `registry_resolver(config, agent_group_model_ref)` to get a `ResolvedProvider`. For 0.1, the agent group is `"personal-assistant"` from config. The `run()` function reads `config.agents["personal-assistant"].model` and `config.agents["personal-assistant"].system_prompt`. If the agent group is not found, raise `ConfigError`.
3. Log at INFO: `"Connector starting: provider={kind} model={model_id} allowlist_size={len(allowlist)}"`. No token in this log line.
4. Build the `python-telegram-bot` `Application` using `ApplicationBuilder().token(token).build()`.

**Message handler (inline within `run()`, defined as a nested async function or a local `async def _handle(update, context)`):**

The handler receives a `python-telegram-bot` `Update` and `CallbackContext`. It must:
1. Extract `chat_id = update.effective_chat.id` and `text = update.effective_message.text`.
2. Call `_is_allowed(chat_id, allowlist)`. If `False`, log auth-drop and return.
3. Call `_check_attachment_size(update.effective_message)`. If `False`, log size-drop and return.
4. Call `memory.append(chat_id, "user", text)`.
5. Call `history = memory.history(chat_id)`.
6. Call `reply_text = await runtime.reply(resolved_provider, system_prompt, history, text, chat_id)`.
7. Call `memory.append(chat_id, "assistant", reply_text)`.
8. Send the reply: `await update.effective_message.reply_text(reply_text)`.
9. Log at INFO: `"reply-sent: chat_id={chat_id} reply_len={len(reply_text)}"`. Do not log reply content — it may be sensitive.

On any exception in steps 4–8: log at ERROR (the scrubbing logger catches any token in tracebacks). Do not send an error reply to the user in 0.1 (silent failure — consistent with the "small and auditable" posture; user sees no reply on error, which prompts them to check logs). Note to code-implementer: wrap the LLM call in a `try/except Exception` and log, but do NOT catch `BaseException` (that would swallow `KeyboardInterrupt` and `SystemExit`).

**Polling loop startup:**
- Register the handler: `app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), _handle))`. The `~filters.COMMAND` exclusion means `/commands` are ignored (there are no bot commands in 0.1).
- Start polling: `await app.run_polling(allowed_updates=Update.ALL_TYPES)`. This is a blocking coroutine that runs until the process receives `SIGINT` or `SIGTERM`.

**Why a module-level `run()` function rather than a class:** The IMPACT-ANALYSIS interface contract specifies `async def run(...)`. A function is simpler than a class for this case. The function's local variables (token, allowlist, resolved provider) are naturally scoped to the polling loop's lifetime. There is no need to store them on an instance.

**Why `filters.TEXT` and a single `MessageHandler`:** IMPACT-ANALYSIS risk R1 mitigation: "use the lowest-level `Application.run_polling` + a single `MessageHandler(filters.TEXT)`." Anything more (conversation handlers, command handlers, inline keyboard handlers) is scope creep. The anti-bloat rule is explicit.

**Tests to add (extend `tests/test_l4_telegram.py`):**

- End-to-end happy path: use `python-telegram-bot`'s test utilities or `pytest-httpserver` to mock the Telegram API. Set `TELEGRAM_BOT_TOKEN=fake` and `ALLOWED_TELEGRAM_USER_IDS=42`. Simulate an inbound text message from `chat_id=42`. Assert: `memory.append` was called twice (once for user, once for assistant), `runtime.reply` was called once with the correct history, the reply was sent to `chat_id=42`.
- Non-allowlisted sender: simulate inbound from `chat_id=99`. Assert `runtime.reply` was NOT called.
- Token scrubbing during error: configure the mock runtime to raise an exception. Assert the captured log output does not contain the fake `TELEGRAM_BOT_TOKEN` value.
- Token scrubbing in startup log: assert `"fake-token"` does not appear in any log line emitted during `run()` startup.

**Acceptance check before closing L4:**
- All tests pass.
- Effective LOC in `agent/connectors/telegram.py` ≤ 250 (hard cap).
- If LOC is between 200 and 250: mandatory re-evaluation before this step is committed. Identify what to cut.
- `security-auditor` review completed and approved.

---

## 5. Lane-Level Acceptance

L4 is closed when ALL of the following are true:

| Check | Maps to |
|---|---|
| `python -m pytest tests/test_l4_telegram.py` passes | Internal gate |
| Allowlist filter: non-allowlisted chat_id → no call to runtime/memory | FR-M2, CONNECTOR-AUDIT finding #3 |
| Oversize attachment → drop, no call to runtime | CONNECTOR-AUDIT finding #2 |
| Token never appears in any captured log output | CONNECTOR-AUDIT finding #1, R4 mitigation |
| End-to-end happy path with mocked Telegram + mocked runtime | FR-M1, AC-2 |
| Auth check occurs before memory or runtime call | CONNECTOR-AUDIT finding #3 |
| `agent/connectors/telegram.py` effective LOC ≤ 250 | CONTRACT §Size Discipline |
| `security-auditor` review approved | REVIEW-PROTOCOL Gate: connectors are sensitive code |

---

## 6. Anti-Bloat Callouts

**Do NOT add a `Connector` abstract base class or `connectors/base.py`.** The discovery sketch includes this; it is not in scope. REVIEW-PROTOCOL: "No speculative abstraction. Don't add interfaces for 'future flexibility.'" There is only one connector in 0.1.

**Do NOT use `python-telegram-bot`'s `ConversationHandler`, `CommandHandler`, `InlineKeyboardHandler`, or `Persistence` features.** One `MessageHandler(filters.TEXT)`. Anything else is scope creep and LOC bloat (IMPACT-ANALYSIS R1 mitigation).

**Do NOT send an error reply to the user when the LLM call fails.** Silent failure. An error reply requires formatting, and formatting is a feature not in the contract. The operator checks logs (NFR-O2: "log a human-readable error message with enough context to diagnose").

**Do NOT log the full reply text.** Log only `reply_len`. The reply may contain information the operator would not want in logs.

**Do NOT log the full user message text.** Log only `chat_id` and `len`. Same reasoning.

**Do NOT implement the "pairing flow"** (NanoClaw's one-time-code ownership proof). CONNECTOR-AUDIT explicitly defers this: "For 0.1, the simpler `ALLOWED_TELEGRAM_USER_IDS` env var allowlist is sufficient." The env var allowlist is simpler, testable, and in-contract.

**Do NOT handle group chats, edited messages, reactions, or voice notes.** CONNECTOR-AUDIT out-of-scope: "Group chats, Edit/delete/reaction handling, Attachments other than text." The `filters.TEXT` filter naturally excludes non-text updates.

---

## 7. Review Gates Checklist

In sequence, before closing this lane:

1. **`/plan-eng-review`** on this plan file — mandatory, non-skippable (auth, token handling, prompt injection are all present).

2. **`code-reviewer` Pre-Test Gate** — after Step 1. Check env-var parsing, ConfigError messages, no token in logs.

3. **`/codex-review`** on the Step 1 staged diff — before committing. Cannot skip: auth-related code.

4. **`code-reviewer` Pre-Test Gate** — after Step 2. Check auth ordering (allowlist before LLM call), size cap constant, mock design.

5. **`/codex-review`** on the Step 2 staged diff — before committing. Cannot skip: allowlist enforcement is the primary access-control mechanism for the entire agent.

6. **`code-reviewer` Pre-Test Gate** — after Step 3. Check polling loop structure, handler registration, exception handling, log content.

7. **`/codex-review`** on the Step 3 staged diff — before committing. Cannot skip: this is the full connector with auth and token handling. Explicitly listed in global CLAUDE.md "NEVER skip for" list (security, LLM prompting code, agent code).

8. **`/simplify`** — after all three steps land. LOC target is 150; hard cap is 250. If approaching 200, identify cuts.

9. **`code-reviewer` Post-Test Gate** — after all tests pass.

10. **`security-auditor`** — **required** (REVIEW-PROTOCOL: "Required for L4 (connector)"). Checks: token scrubbing in all error paths, allowlist is enforced before LLM, framing envelope is applied in runtime (not in connector), no token in startup or error logs, `ALLOWED_TELEGRAM_USER_IDS` parsing cannot be bypassed.

11. **`git-steward`** — commit message must include `Codex-reviewed (VERDICT: ...)` and `LOC: +n -0 (module connectors/telegram now n/250)`.
