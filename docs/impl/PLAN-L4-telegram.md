# Implementation Plan — L4: Telegram Connector

**Lane:** L4
**Version:** 0.2 (post-eng-review 2026-05-03)
**Status:** Approved by `/plan-eng-review` — ready for code-implementer
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
2. Reads `ALLOWED_TELEGRAM_CHAT_IDS` from env. If absent or empty, raises `ConfigError` naming the variable and explaining that at least one chat ID is required.
3. Parses the comma-separated IDs: `{int(s.strip()) for s in value.split(",") if s.strip()}`. If any segment is not a valid integer, raises `ConfigError` with the offending segment named. **eng-review T4 (P3):** if the parsed set is empty (e.g. env was `",,, "` — present but all segments blank), raise `ConfigError` explaining that at least one chat ID must remain after parsing. Silent deny-all from a misconfigured env var is the wrong default — fail loud at startup. Returns `(token, allowlist_set)`.

**Naming note (eng-review A1):** the env var is `ALLOWED_TELEGRAM_CHAT_IDS`, NOT `ALLOWED_TELEGRAM_USER_IDS`. In Telegram DMs `chat_id == user_id`, so they're equivalent in 0.1. They diverge once the bot enters a group (`chat_id` becomes the negative supergroup ID, `user_id` stays the human's ID). Naming the var by what we actually check (`chat_id`) prevents a future security bug where someone adds group support and the allowlist silently breaks. The connector code calls `update.effective_chat.id` for this same reason — explicit and consistent with the env var name.

The token string is never logged. The allowlist set is logged at startup at INFO level: `"Allowlist loaded: {len(allowlist)} chat ID(s)"` — the count is logged, not the IDs themselves (the IDs are not secret but there is no reason to emit them).

**Why a module-level function rather than a class `__init__`:** The IMPACT-ANALYSIS public interface declares `async def run(config: Config, registry_resolver, memory: Memory) -> None` as the connector's entry point. A module-level `run()` function is simpler than a class: no constructor, no `__init__`, no state lifecycle to manage. The token and allowlist are local variables within `run()`, not class attributes.

**Tests to add:**

```
tests/test_l4_telegram.py  (Step 1 portion)
```

- Set `TELEGRAM_BOT_TOKEN=fake-token` and `ALLOWED_TELEGRAM_CHAT_IDS=12345,67890` — call `_load_env()` — assert returns `("fake-token", {12345, 67890})`.
- Unset `TELEGRAM_BOT_TOKEN` — call `_load_env()` — assert raises `ConfigError` whose message contains `"TELEGRAM_BOT_TOKEN"`.
- Unset `ALLOWED_TELEGRAM_CHAT_IDS` — call `_load_env()` — assert raises `ConfigError` whose message contains `"ALLOWED_TELEGRAM_CHAT_IDS"`.
- Set `ALLOWED_TELEGRAM_CHAT_IDS=12345,notanint,67890` — call `_load_env()` — assert raises `ConfigError` naming `"notanint"`.
- **eng-review T4 (P3):** Set `ALLOWED_TELEGRAM_CHAT_IDS=",, , ,"` (present but all segments blank) — call `_load_env()` — assert raises `ConfigError` mentioning that at least one chat ID is required. Prevents silent deny-all from a misconfigured env.
- Assert that captured log output from `_load_env()` does not contain the literal fake token value (scrubber test, cross-lane).

**Acceptance check before proceeding to Step 2:**
- Step 1 tests all pass.
- `_load_env()` is importable.

---

### Step 2 — Allowlist filter

**Files touched (≤3):**
1. `agent/connectors/telegram.py` (extend — add `_is_allowed`)
2. `tests/test_l4_telegram.py` (extend)

**LOC estimate:** ~15 effective LOC in `agent/connectors/telegram.py` for this step (eng-review A3 dropped `_check_attachment_size`). Cumulative: ~45 LOC.

**What to write:**

`agent/connectors/telegram.py` — Part 2 of 3: auth.

`_is_allowed(chat_id: int, allowlist: set[int]) -> bool`:
- Returns `chat_id in allowlist`. No exceptions, no logging — the caller logs.
- Why private and pure: keeps the allowlist check testable in isolation, independent of the Telegram library. A unit test can call this function directly with synthetic IDs without mocking the Telegram library.

**Attachment size cap is enforced by `filters.TEXT` (eng-review A3):** The CONNECTOR-AUDIT requires a 5 MB attachment cap. The previous draft of this plan added a `_check_attachment_size` helper for this. We dropped it because in 0.1 the `MessageHandler` is registered with `filters.TEXT` (see Step 3), which makes `python-telegram-bot` discard every non-text update at the dispatch layer — before any handler code runs. A 5 MB photo never reaches our code in 0.1; the size cap is satisfied by the filter itself. Step 3's handler registration includes a code comment documenting that `filters.TEXT` IS the size-cap mechanism for 0.1. When a future lane adds non-text support, a real `_check_attachment_size` (with bytes inspection) will be added at that point — we don't need it now and dead code is anti-bloat.

The message handler (inline in the polling loop function) must call `_is_allowed` BEFORE any call to `memory` or `runtime`. The authorization check is first, full stop. CONNECTOR-AUDIT finding #3: "Auth check happens BEFORE any LLM invocation, not after." Spirit of the rule: auth-before-ANY-work, including memory writes — see eng-review T1 test below.

For an unauthorized message: log at INFO via `get_logger("agent.connectors.telegram")`: `"auth-drop: chat_id={chat_id}"`. Then return (no reply, no LLM call, **no memory write**). Silent drop — per CONTRACT resolved decision OQ-2.

**Tests to add (extend `tests/test_l4_telegram.py`):**

- `_is_allowed(12345, {12345, 67890})` → `True`.
- `_is_allowed(99999, {12345, 67890})` → `False`.
- `_is_allowed(12345, set())` → `False` (empty allowlist blocks everyone — though `_load_env` now refuses to construct an empty allowlist, this remains a defensive unit test).
- **eng-review T1 (P2):** Integration — invoke the handler logic for an unauthorized `chat_id`. Assert (a) `runtime.reply` was NOT called, AND (b) `memory.append` was NOT called. The CONNECTOR-AUDIT spirit is "auth before any work" — including memory writes, not just LLM calls. Don't store data from people we've explicitly blocked.

**Acceptance check before proceeding to Step 3:**
- Step 2 tests all pass.
- Allowlist filter has 100% branch coverage (allowed, denied).

---

### Step 3 — Polling loop and reply sender

**Files touched (≤3):**
1. `agent/connectors/telegram.py` (complete — add `run()` polling loop)
2. `tests/test_l4_telegram.py` (extend — end-to-end happy path with mocked Telegram)

**LOC estimate:** ~80 effective LOC in `agent/connectors/telegram.py` for this step. Cumulative: ~125 LOC — under target (eng-review A3 freed ~25 LOC by dropping attachment-size).

**What to write:**

`agent/connectors/telegram.py` — Part 3 of 3: the `run()` function.

`def run(config: Config, registry_resolver, memory: Memory) -> None`:

**Sync, NOT async (eng-review A4):** `python-telegram-bot` 22.x `Application.run_polling()` is a SYNCHRONOUS method that creates and owns its own event loop, registers signal handlers (SIGINT/SIGTERM) for graceful shutdown, and blocks until shutdown. The original draft of this plan said `async def run(...)` and `await app.run_polling(...)` — that's the wrong API for 22.x and would either crash at startup or deadlock the event loop. The handler INSIDE the loop is async (pgttb requires async handlers) — the inner `runtime.reply` await works correctly because pgttb runs handlers on the loop it owns. The `run()` entry point is sync. The CLI entrypoint calls `connector.run(config, resolve, memory)` directly with no `asyncio.run` wrapper.

**Signal handling (eng-review A6):** pgttb's `Application.run_polling()` registers SIGINT and SIGTERM handlers internally. Ctrl-C in the terminal or `docker stop` cleanly shuts down the bot, closes the Telegram connection, and unwinds the loop. We do NOT add our own signal handler — pgttb owns the lifecycle. If we ever drop `run_polling()` for the lower-level `Application.start()` + `Updater.start_polling()` pattern, signal handling becomes our problem; that's not in 0.1.

This is the long-running polling loop. Its responsibilities in order:

**Startup sequence (inside `run()`):**
1. Call `_load_env()` to get `(token, allowlist)`. If this raises `ConfigError`, let it propagate — the CLI entrypoint handles it.
2. Resolve the provider for the configured agent group: call `registry_resolver(config, agent_group_model_ref)` to get a `ResolvedProvider`. For 0.1, the agent group is `"personal-assistant"` from config. The `run()` function reads `config.agents["personal-assistant"].model` and `config.agents["personal-assistant"].system_prompt`. If the agent group is not found, raise `ConfigError`.
3. Log at INFO: `"Connector starting: provider={kind} model={model_id} allowlist_size={len(allowlist)}"`. No token in this log line.
4. Build the `python-telegram-bot` `Application` using `ApplicationBuilder().token(token).build()`.

**Message handler (inline within `run()`, defined as a nested async function `async def _handle(update, context)`):**

The handler receives a `python-telegram-bot` `Update` and `CallbackContext`. **The ENTIRE handler body is wrapped in a single `try/except Exception` block (eng-review C1)** — narrow per-call try/except blocks would let a memory failure between auth and the LLM call escape into the polling loop and crash the bot. With a whole-handler wrap, ANY failure in any step → log at ERROR → return → next inbound message is processed normally. Only catch `Exception`, never `BaseException` (would swallow KeyboardInterrupt / SystemExit and break Ctrl-C shutdown).

Inside the try block, in order:
1. Extract `chat_id = update.effective_chat.id` and `text = update.effective_message.text`.
2. Call `_is_allowed(chat_id, allowlist)`. If `False`, log auth-drop (`"auth-drop: chat_id=%d"`, format string only — no token) and return. **No `memory.append`, no `runtime.reply` for unauthorized chats** (eng-review T1: this guarantee is the spirit of CONNECTOR-AUDIT finding #3).
3. Call `memory.append(chat_id, "user", text)`. (Attachment size cap is enforced by `filters.TEXT` at handler registration — see Step 2 doc — so non-text never reaches here in 0.1.)
4. Call `history = memory.history(chat_id)`.
5. Call `reply_text = await runtime.reply(resolved_provider, system_prompt, history, text, chat_id)`.
6. Call `memory.append(chat_id, "assistant", reply_text)`.
7. Send the reply: `await update.effective_message.reply_text(reply_text)`.
8. Log at INFO: `"reply-sent: chat_id=%d reply_len=%d"` with `(chat_id, len(reply_text))`. **DO NOT log `reply_text`** — eng-review T3 enforces this with a test asserting the literal reply text never appears in any captured log record.

In the `except Exception` block: log at ERROR via `get_logger("agent.connectors.telegram")` with `exc_info=True`. The L0 scrubbing logger redacts any secret values in the traceback (including the bot token if it appears in a Telegram API URL). Do NOT send an error reply to the user in 0.1 — silent failure per CONTRACT (operator checks logs).

**Polling loop startup:**
- Register the handler: `app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), _handle))`. The `~filters.COMMAND` exclusion means `/commands` are ignored (there are no bot commands in 0.1). **Code comment in this line: `# filters.TEXT IS the 5MB attachment-size cap mechanism for 0.1 — non-text is dropped before any handler runs`** (eng-review A3).
- Start polling: `app.run_polling(allowed_updates=Update.ALL_TYPES)` — SYNC call, NOT awaited (eng-review A4). Blocks until pgttb's internal SIGINT/SIGTERM handlers fire.

**Why a module-level `run()` function rather than a class:** The IMPACT-ANALYSIS interface contract originally specified `async def run(...)`. Per eng-review A4, the signature is corrected to `def run(...)` (sync) for python-telegram-bot 22.x compatibility. A function is simpler than a class for this case. The function's local variables (token, allowlist, resolved provider) are naturally scoped to the polling loop's lifetime. There is no need to store them on an instance. The handler `_handle` is a nested async function inside `run()` and closes over those locals.

**Why `filters.TEXT` and a single `MessageHandler`:** IMPACT-ANALYSIS risk R1 mitigation: "use the lowest-level `Application.run_polling` + a single `MessageHandler(filters.TEXT)`." Anything more (conversation handlers, command handlers, inline keyboard handlers) is scope creep. The anti-bloat rule is explicit.

**Tests to add (extend `tests/test_l4_telegram.py`):**

Note: tests directly invoke the inner `_handle` coroutine with synthetic `Update` mocks. We do NOT spin up the real polling loop in tests — pgttb's `run_polling` owns an event loop and exercising it under pytest is fragile and unnecessary. The handler is the unit of behavior worth testing; the loop is library plumbing.

- End-to-end happy path: build a synthetic `Update` mock with `chat_id=42` and `text="hello"`. Set `TELEGRAM_BOT_TOKEN=fake` and `ALLOWED_TELEGRAM_CHAT_IDS=42`. Mock `memory` and `runtime.reply`. Invoke `_handle(update, context)`. Assert: `memory.append` called twice (user, then assistant), `runtime.reply` called once with the correct history, `update.effective_message.reply_text` called with the model's reply.
- Non-allowlisted sender: synthetic update with `chat_id=99`. Assert `runtime.reply` was NOT called AND `memory.append` was NOT called (eng-review T1 reaffirmed at handler integration level).
- **eng-review T2 (P2):** Memory failure resilience — configure the mock `memory.append` to raise `sqlite3.OperationalError("disk I/O error")` on the FIRST call (the user-message append). Invoke `_handle`. Assert: (a) handler does NOT propagate the exception (try/except Exception catches), (b) `runtime.reply` was NOT called (we never got past the failed append), (c) the polling loop would survive — emulated by checking that a SECOND `_handle` call with a fresh mock succeeds normally.
- **eng-review T3 (P2):** Reply text NOT in logs — configure mock `runtime.reply` to return a known sentinel string `"sensitive-reply-content-XYZ-789"`. Invoke `_handle` with `caplog` capturing `agent.connectors.telegram` at DEBUG level. Assert that the sentinel string does NOT appear in any captured log record. Also assert `"reply-sent"` and the literal length (`reply_len=33`) DO appear, proving the metadata-only log line fired.
- Token scrubbing during error: configure mock `runtime.reply` to raise `RuntimeError("https://api.telegram.org/botFAKE_TOKEN_VALUE/getUpdates failed")`. Set `TELEGRAM_BOT_TOKEN=FAKE_TOKEN_VALUE` so the L0 scrubber registers it. Invoke `_handle`. Assert the captured log output does not contain `FAKE_TOKEN_VALUE`.
- Token scrubbing in startup log: invoke a portion of `run()`'s startup that emits the "Connector starting" INFO log. Assert the literal token value does not appear in any log line.

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

**Do NOT implement the "pairing flow"** (NanoClaw's one-time-code ownership proof). CONNECTOR-AUDIT explicitly defers this: "For 0.1, the simpler `ALLOWED_TELEGRAM_CHAT_IDS` env var allowlist is sufficient." The env var allowlist is simpler, testable, and in-contract.

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

10. **`security-auditor`** — **required** (REVIEW-PROTOCOL: "Required for L4 (connector)"). Checks: token scrubbing in all error paths, allowlist is enforced before LLM, framing envelope is applied in runtime (not in connector), no token in startup or error logs, `ALLOWED_TELEGRAM_CHAT_IDS` parsing cannot be bypassed.

11. **`git-steward`** — commit message must include `Codex-reviewed (VERDICT: ...)` and `LOC: +n -0 (module connectors/telegram now n/250)`.

---

## 8. Revision Log

**v0.2 (post-/plan-eng-review 2026-05-03):**

P1 (would block code-implementer if not caught at plan stage):
- A1: Renamed env var `ALLOWED_TELEGRAM_USER_IDS` → `ALLOWED_TELEGRAM_CHAT_IDS`. The code checks `chat_id` (which equals `user_id` in DMs but diverges in groups). Naming the var by what we actually check prevents a future security bug when group support is added.
- A4: Corrected polling-loop API. `python-telegram-bot` 22.x `Application.run_polling()` is SYNC, not async. Changed `async def run(...)` → `def run(...)`. The handler stays async (pgttb requires it). Original draft would have crashed at startup or deadlocked the event loop.

P2:
- A3: Dropped `_check_attachment_size` entirely (~15 LOC saved). Plan admitted the function would never run in 0.1 because `filters.TEXT` blocks non-text at handler dispatch — that satisfies the CONNECTOR-AUDIT 5MB cap requirement. Added a code comment at the handler-registration line documenting this.
- A6: Documented that pgttb owns SIGINT/SIGTERM via `run_polling()`. We don't add our own signal handlers in 0.1.
- C1: Wrap the ENTIRE handler body in a single `try/except Exception`, not just the LLM call. A memory.append failure between auth and the LLM call would otherwise crash the polling loop. With whole-handler wrap: any failure → log + return → next message processed normally.
- T1: Added explicit assertion in the unauthorized-chat-id test that `memory.append` is also NOT called (not just `runtime.reply`). Spirit of CONNECTOR-AUDIT finding #3 = no work for blocked users, including data storage.
- T2: Added test for memory.append failure → handler doesn't crash polling loop. Pairs with C1.
- T3: Added test asserting reply text NOT in any captured log record (lane acceptance demanded but no test enforced).

P3:
- T4: Added test for empty allowlist after parse (env present but blank/`,,,`). Plan code now raises ConfigError instead of silent deny-all.

LOC impact: A3 removed ~25 LOC of dead code + tests; total Step 1+2+3 cumulative target dropped from ~150 to ~125 — well under the 250 hard cap. No schedule impact; all changes are plan-doc edits.
