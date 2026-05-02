# Implementation Plan — L3: Memory (SQLite, Per-Chat)

**Lane:** L3
**Version:** 0.1
**Status:** Ready for `/plan-eng-review`
**Depends on:** L0 (logger, package layout)
**Blocks:** L4 (connector calls `memory.append` and `memory.history`)
**Can run in parallel with:** L1, L2 (shares `Turn` shape with L2 but does not import from it)

---

## Mandatory gate before code-implementer begins

`/plan-eng-review` on this plan file is required before code-implementer begins. While L3 is the simplest application lane, it handles durable data (the SQLite file on the named volume) and its schema initialization must be idempotent across container restarts. Plan-stage review confirms the schema and the volume interaction before any code is written.

---

## 1. Lane Summary

L3 persists per-chat conversation turns to a SQLite file on the `agent-data` named Docker volume. It exposes three operations: initialize the schema, append a turn, and retrieve the last N turns for a chat ID. The schema is initialized on `Memory` construction, making the DB self-healing across container restarts with no manual migration step.

**LOC budget:** 80 target / 150 hard cap (effective Python LOC, blank/comment-stripped).

---

## 2. WHAT — Artifacts to Produce

| Artifact | Path |
|---|---|
| Memory class | `agent/memory.py` |
| Tests | `tests/test_l3_memory.py` |

---

## 3. WHY — Rationale per Artifact

- **`agent/memory.py`** — FR-ME1: "The system must persist per-chat conversation history across container restarts." FR-ME2: "History must be stored in SQLite on a named Docker volume." FR-ME3: "The agent must include the stored conversation history as context when generating each reply." These three functional requirements together mandate a persistent, per-chat, turn-ordered store accessible by the connector before each LLM call.

  SQLite is the right choice for 0.1 because: it requires zero infrastructure (no separate DB container), it survives `docker compose down && up` on a named volume (FR-ME2), and it is auditable in a single file. The IMPACT-ANALYSIS explicitly rules out "speculative migration framework — single `CREATE TABLE IF NOT EXISTS`."

  FR-ME4: "There is no automated truncation, summarization, or expiry of history in 0.1." The `history()` method enforces the sliding window (last 20 turns) by querying `LIMIT 20 ORDER BY ... DESC`. No deletion, no summarization.

---

## 4. HOW — Tiny Step Breakdown

### Step 1 — Memory class: schema initialization and append

**Files touched (≤3):**
1. `agent/memory.py` (partial — `Memory.__init__`, `_init_schema`, `append`)
2. `tests/test_l3_memory.py`

**LOC estimate:** ~45 effective LOC in `agent/memory.py` for this step.

**What to write:**

`agent/memory.py` — Part 1 of 2: initialization and writes.

Define `Memory` as a class (not a module-level function set) so that the DB path is encapsulated and tests can pass a tmpdir path without monkeypatching env vars.

`Memory.__init__(self, db_path: Path)`:
- Accept a `Path` as the DB file location. This makes the class testable with a tmpdir.
- Call `self._init_schema()` synchronously on construction. Using the synchronous `sqlite3` standard library (not `aiosqlite`) for a single-writer, personal bot is the right choice: `aiosqlite` adds a dependency and asynchronous complexity for no practical benefit in a single-process, single-user agent. Simpler is better (REVIEW-PROTOCOL core principle 1).
- Log at INFO via `get_logger("agent.memory")`: `"Memory initialized at {db_path}"`.

`Memory._init_schema(self)`:
- Execute the single `CREATE TABLE IF NOT EXISTS` statement. The table schema (per IMPACT-ANALYSIS §L3 public interface):
  ```
  turns(chat_id INTEGER, ts INTEGER, role TEXT, content TEXT)
  ```
  - `chat_id`: the Telegram chat ID (integer, not text — Telegram chat IDs are integers).
  - `ts`: Unix timestamp in seconds (`int(time.time())`) at append time. Using an integer timestamp rather than SQLite's `CURRENT_TIMESTAMP` text makes ordering explicit and portable.
  - `role`: either `"user"` or `"assistant"`. The schema does not enforce this via a CHECK constraint — keeping the schema simple.
  - `content`: the message content, stored as TEXT with no length limit.
- No index in 0.1. The table is small (single user, last 20 turns queried). An index would be premature — `CREATE TABLE IF NOT EXISTS` is the full DDL.
- Enable WAL mode: execute `PRAGMA journal_mode=WAL` after schema creation. WAL mode is cheap (one line) and makes the DB more robust to concurrent reads, even though 0.1 is single-writer. The IMPACT-ANALYSIS notes: "WAL mode optional but cheap" — the plan chooses to include it.

`Memory.append(self, chat_id: int, role: str, content: str) -> None`:
- Insert one row: `INSERT INTO turns(chat_id, ts, role, content) VALUES (?, ?, ?, ?)` with `ts = int(time.time())`.
- Use a context manager (`with sqlite3.connect(self._db_path) as conn:`) for automatic commit/rollback.
- Log at DEBUG via `get_logger("agent.memory")`: `"append: chat_id={chat_id} role={role} len={len(content)}"`. Do not log content — it may be sensitive.

**Tests to add:**

```
tests/test_l3_memory.py  (Step 1 portion)
```

- Create a `Memory(tmp_path / "agent.sqlite")` in a pytest `tmp_path` fixture. Assert no error.
- Call `memory.append(chat_id=1, role="user", content="hello")` — assert no error.
- Assert the file exists at the expected path after construction.
- Call `_init_schema` twice (by constructing two `Memory` instances pointing at the same file) — assert no error (tests idempotency of `CREATE TABLE IF NOT EXISTS`).
- Assert the WAL files (`.sqlite-wal`, `.sqlite-shm`) are created or that WAL mode is confirmed via `PRAGMA journal_mode`.

**Acceptance check before proceeding to Step 2:**
- Step 1 tests all pass.
- `Memory(Path("/tmp/test.sqlite")).append(1, "user", "test")` works without error.

---

### Step 2 — Memory class: history retrieval and default path helper

**Files touched (≤3):**
1. `agent/memory.py` (complete — add `history()`, `default_db_path()`)
2. `tests/test_l3_memory.py` (extend)

**LOC estimate:** ~35 effective LOC in `agent/memory.py` for this step. Cumulative lane total: ~80 effective LOC — on target.

**What to write:**

`agent/memory.py` — Part 2 of 2: history retrieval and path helper.

`Memory.history(self, chat_id: int, limit: int = 20) -> list[Turn]`:
- Query: `SELECT role, content FROM turns WHERE chat_id = ? ORDER BY ts DESC LIMIT ?` with `(chat_id, limit)`.
- The query orders by `ts DESC` (newest first) then the result is reversed in Python before returning, so the final list is oldest-first (chronological order). This is the ordering the runtime needs: earlier turns first, most recent turn last, so PydanticAI receives the conversation in natural reading order.
- Return a `list[Turn]` where each `Turn` is `{"role": role, "content": content}` — a plain dict matching the `TypedDict` shape defined in `agent/runtime.py`. Do not import `Turn` from `agent.runtime` — use a plain dict. Both layers agree on the shape by convention, not by import. The IMPACT-ANALYSIS is explicit: "no cross-import."
- If the chat has no history, return an empty list. Never raise for a new chat.

`default_db_path() -> Path` — a module-level helper function (not a class method) that returns the default DB path:
- Read `AGENT_DATA_DIR` env var; default to `"/data"` if unset.
- Return `Path(agent_data_dir) / "agent.sqlite"`.
- This is the path used by the connector in production. Tests use `Memory(tmp_path / "agent.sqlite")` directly.

Why a module-level function rather than a class method or a default argument: the default path depends on an env var. Using a factory function separates the "what path" decision (env-dependent, production) from the "create a Memory" decision (testable with any path). This is simpler than a class method and more obvious than a default argument that reads env at import time.

**Tests to add (extend `tests/test_l3_memory.py`):**

- Append 5 turns for `chat_id=1`, call `memory.history(1)` — assert returns 5 turns in chronological order (role and content match, oldest first).
- Append 25 turns for `chat_id=2`, call `memory.history(2, limit=20)` — assert returns exactly 20 turns (the most recent 20), in chronological order.
- Append turns for `chat_id=1` and `chat_id=2`, call `memory.history(1)` — assert only `chat_id=1`'s turns are returned.
- Call `memory.history(chat_id=999)` (no turns for this ID) — assert returns an empty list, no exception.
- Test idempotency of container restart: construct `Memory(path)`, append 3 turns, discard the object, construct a new `Memory(path)`, call `history()` — assert the 3 turns are still there (proves `CREATE TABLE IF NOT EXISTS` did not wipe data on re-init).
- Call `default_db_path()` with `AGENT_DATA_DIR` unset — assert returns `Path("/data/agent.sqlite")`.
- Call `default_db_path()` with `AGENT_DATA_DIR="/custom"` — assert returns `Path("/custom/agent.sqlite")`.

**Acceptance check before closing L3:**
- All tests pass.
- The idempotency test (simulated restart) passes — this is the proxy for FR-ME1 and AC-5.
- `agent/memory.py` effective LOC ≤ 150.

---

## 5. Lane-Level Acceptance

L3 is closed when ALL of the following are true:

| Check | Maps to |
|---|---|
| `python -m pytest tests/test_l3_memory.py` passes | Internal gate |
| Append + history round-trip on tmpdir DB | FR-ME1, FR-ME3 |
| `history()` returns at most 20 turns, oldest-first | CONTRACT resolved decision: keep last 20 |
| `history()` returns empty list for unknown `chat_id` | Robustness |
| Simulated restart: second `Memory(same_path)` sees prior turns | FR-ME1, AC-5 |
| `import sqlite3` used (not `aiosqlite`) | Anti-bloat: no new dependency |
| `agent/memory.py` effective LOC ≤ 150 | CONTRACT §Size Discipline |
| No `chat_id` data appears in INFO log output (only len/role) | NFR-O1 privacy |

---

## 6. Anti-Bloat Callouts

**Do NOT use `aiosqlite`.** The discovery sketch (`docs/discovery/agent/memory.py`) uses `aiosqlite`, but that was written pre-SDLC. The standard library `sqlite3` module is sufficient for a single-writer agent, adds no dependency, and keeps the code simpler. The `reply()` function is async; the memory calls around it in L4 can be wrapped in `asyncio.to_thread()` if needed, or simply called synchronously in the async polling loop (sqlite3 calls are fast).

**Do NOT create a migration framework.** One `CREATE TABLE IF NOT EXISTS` statement. No version table, no migration runner, no `alembic`. The schema is stable for 0.1. IMPACT-ANALYSIS: "Avoid speculative migration framework."

**Do NOT add an index.** No `CREATE INDEX` in 0.1. The table is small; a full scan on `chat_id` with `LIMIT 20` is negligible. Add an index if profiling identifies it as a bottleneck in 0.2.

**Do NOT add a `close()` method or context manager protocol.** SQLite connections are opened and closed per-operation (using `with sqlite3.connect(...) as conn:`). There is no persistent connection object to manage. A `close()` method would imply a connection lifecycle that does not exist.

**Do NOT add history expiry or summarization.** FR-ME4 is explicit: no automated truncation, summarization, or expiry in 0.1. The `LIMIT 20` in `history()` is not expiry — it is a sliding read window. Old rows remain in the DB; they are just not returned.

**Do NOT import from `agent.runtime`.** The `Turn` dict shape is shared by convention. Both modules define what they mean by a turn dict independently. If they drift, L4 (the connector) catches it at integration time.

---

## 7. Review Gates Checklist

In sequence, before closing this lane:

1. **`/plan-eng-review`** on this plan file — mandatory before code-implementer begins.

2. **`code-reviewer` Pre-Test Gate** — after Step 1. Check schema DDL, WAL pragma, append correctness, log content (confirm no PII in INFO logs).

3. **`/codex-review`** on the Step 1 staged diff — before committing. Check: SQL injection risk (all queries use parameterized `?` placeholders — no f-string SQL), WAL mode correctly set, connection lifecycle correct.

4. **`code-reviewer` Pre-Test Gate** — after Step 2. Check history ordering logic (DESC then Python-reverse), default path helper, no cross-import from `agent.runtime`.

5. **`/codex-review`** on the Step 2 staged diff — before committing.

6. **`/simplify`** — after both steps land. Target: confirm ≤ 80 effective LOC. If approaching 150, identify what to cut.

7. **`code-reviewer` Post-Test Gate** — after all tests pass. Check: restart idempotency test covers the AC-5 scenario.

8. **`git-steward`** — commit message must include `Codex-reviewed (VERDICT: ...)` and `LOC: +n -0 (module memory now n/150)`.
