# Implementation Plan — L5: Container Hardening

**Lane:** L5
**Version:** 0.1
**Status:** Ready for `/plan-eng-review`
**Depends on:** L0 (package skeleton — needed for a working `pip install`); validates fully only after L4 is complete (the agent must actually run)
**Blocks:** L6 (final LOC count, README claims, AC-6/AC-7 verification)
**Can run in parallel with:** L1, L3 (Dockerfile and compose file drafts can be written while application lanes are in progress; the final acceptance pass happens after L4)

---

## Mandatory gate before code-implementer begins

`/plan-eng-review` on this plan file is required before code-implementer begins. Container hardening is explicitly listed as non-skippable in REVIEW-PROTOCOL and global CLAUDE.md ("Never skip for: security, container hardening"). This lane implements 16 of the 20 SECURITY.md controls and is the surface that makes AC-6 and AC-7 verifiable.

---

## 1. Lane Summary

L5 produces the `Dockerfile`, `entrypoint.sh`, `docker-compose.yaml`, `container/mount-policy.yaml`, and `agent/__main__.py` (CLI entrypoint). These files collectively implement the 16 required SECURITY.md controls for 0.1, with the 4 stage-2 deferrals explicitly tracked. The container must pass `docker inspect` verification of non-root UID, read-only root FS, and dropped capabilities.

**LOC budget:**
- `Dockerfile` + `entrypoint.sh`: 40 target / 60 hard cap
- `docker-compose.yaml`: 40 target / 60 hard cap
- `agent/__main__.py` (CLI entrypoint): 30 target / 50 hard cap
- `container/mount-policy.yaml`: data file, not counted against LOC cap

---

## 2. WHAT — Artifacts to Produce

| Artifact | Path |
|---|---|
| CLI entrypoint | `agent/__main__.py` |
| Multi-stage Dockerfile | `Dockerfile` |
| Hardened entrypoint script | `container/entrypoint.sh` |
| Compose definition | `docker-compose.yaml` |
| Mount allowlist | `container/mount-policy.yaml` |
| Updated security checklist | `docs/discovery/container/SECURITY.md` (inline verification notes added) |

---

## 3. WHY — Rationale per Artifact

- **`agent/__main__.py`** — CONTRACT §Size Discipline: "CLI entrypoint 30/50 LOC, just wires Config + Memory + Connector.run." The `pyproject.toml` entry point (`agent = "agent.__main__:main"`) declared in L0 resolves to this file. This is the one file that imports from all application lanes and starts the agent. It is thin by design — all logic lives in the lane modules.

- **`Dockerfile`** — SECURITY.md controls 1 (non-root), 16 (minimal base), 17 (no build tools in runtime). The multi-stage build pattern separates the build environment (with `pip`, `gcc`) from the runtime image (none of those). AC-6 requires `docker inspect` to show non-root UID.

- **`container/entrypoint.sh`** — SECURITY.md control 18 (no shell features in entrypoint, POSIX `sh` with `set -eu`, exec form). The entrypoint does only two things: sanity-check the writable data dir and the config mount, then `exec agent`. It does not source files, it does not run scripts, it does not use bash-specific features.

- **`docker-compose.yaml`** — SECURITY.md controls 2 (read-only root FS), 3 (writable scratch via named volume), 4/5 (mount allowlist — the compose is the enforcement point), 6 (cap_drop ALL), 7 (no-new-privileges), 10 (network egress via bridge), 11 (no host network), 12 (default namespacing), 13 (resource limits), 14 (secrets via env, not baked), 15 (config read-only).

- **`container/mount-policy.yaml`** — SECURITY.md control 4 (no host bind mounts except explicit allowlist) and control 5 (no Docker socket mount). This is a declarative reference document; the compose file is the actual enforcement. The policy file documents intent for reviewers and future operators.

- **`docs/discovery/container/SECURITY.md`** — SECURITY.md itself must be updated with inline verification notes for each control (AC-7: "all 16 controls required for 0.1 are marked verified with inline notes documenting how each was verified"). The 4 deferred controls must be in a "Known deferrals" section.

---

## 4. HOW — Tiny Step Breakdown

### Step 1 — CLI entrypoint (`agent/__main__.py`)

**Files touched (≤3):**
1. `agent/__main__.py`
2. `tests/test_l5_container.py` (start the test file)

**LOC estimate:** ~30 effective LOC in `agent/__main__.py`. 0 LOC in `Dockerfile` or compose yet.

**What to write:**

`agent/__main__.py` — the `main()` function that `docker compose up` ultimately calls (via the `entrypoint.sh` → `exec agent` → `agent.__main__:main` path declared in `pyproject.toml`).

`main()` responsibilities in order:
1. Set up a logger via `get_logger("agent.main")`.
2. Load config: `config = load_config(Path("/config/config.yaml"))`. If `ConfigError` is raised, log at CRITICAL level and `sys.exit(1)`. This is the "crash loudly" behavior for misconfig.
3. Log at INFO: `"Agent starting: provider={...} model={...} connector=telegram"` (the provider and model come from `config.agents["personal-assistant"].model`). This satisfies NFR-O3 and FR-L4.
4. Verify no Anthropic connections by resolving the provider: call `resolve(config, model_ref)`. If `ConfigError`, log CRITICAL and `sys.exit(1)`.
5. Initialize memory: `memory = Memory(default_db_path())`. If the DB path is not writable (e.g. the named volume was not mounted), this will fail with a `sqlite3.OperationalError` — catch it, log CRITICAL, `sys.exit(1)`.
6. Start the Telegram connector: `asyncio.run(connector_run(config, resolve, memory))`. The `connector_run` import is `from agent.connectors.telegram import run as connector_run`.
7. On `KeyboardInterrupt` or `SystemExit`: log `"Agent stopped"` at INFO and exit cleanly.

Why `asyncio.run()` in `main()`: the Telegram connector's `run()` is an async function (it uses `python-telegram-bot`'s async polling). `asyncio.run()` is the standard way to enter the event loop from a synchronous entry point. No `asyncio.get_event_loop()`, no `loop.run_forever()` — `asyncio.run()` is the modern, correct approach.

**Tests to add:**

```
tests/test_l5_container.py  (Step 1 portion)
```

- Test that `main()` calls `sys.exit(1)` when `TELEGRAM_BOT_TOKEN` is missing (mock `connector_run` to never be called). Use `pytest.raises(SystemExit)`.
- Test that `main()` calls `sys.exit(1)` when `config.yaml` is not found (pass a non-existent path). Use `pytest.raises(SystemExit)`.
- Test that `main()` logs the active provider and model at startup before any connector call (capture log output, assert the INFO line contains "provider=" and "model=").

**Acceptance check before proceeding to Step 2:**
- Step 1 tests pass.
- `python -m agent` (or `python agent/__main__.py`) raises `SystemExit(1)` when env vars are missing (expected in test environment without a real Telegram token).

---

### Step 2 — Dockerfile and entrypoint.sh

**Files touched (≤3):**
1. `Dockerfile`
2. `container/entrypoint.sh`

**LOC estimate:** ~40 effective LOC combined (at the 40/60 target/cap).

**What to write:**

`Dockerfile` — multi-stage build. Two stages: `build` and `runtime`.

**Build stage (`FROM python:3.12-slim AS build`):**
- Install build tools via `apt-get` (e.g. `build-essential`) with `--no-install-recommends` and clean up `apt` lists in the same `RUN` layer.
- Copy only `pyproject.toml` and the `agent/` directory.
- Run `pip install --no-cache-dir --prefix=/install .` to install the package and all dependencies into `/install`. The `--prefix` flag is the multi-stage pattern: only `/install` is copied to the runtime stage, not the build tools.

**Runtime stage (`FROM python:3.12-slim AS runtime`):**
- Create the non-root user: `groupadd --system --gid 10001 agent` and `useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin agent`. UID 10001 is the chosen UID per IMPACT-ANALYSIS §L5 contract. SECURITY.md control 1.
- Copy the installed package from the build stage: `COPY --from=build /install /usr/local`. This copies only the installed Python packages — no source, no build tools. SECURITY.md control 17.
- Copy the entrypoint: `COPY container/entrypoint.sh /usr/local/bin/entrypoint.sh` and set permissions `chmod 0555`.
- Set `ENV AGENT_DATA_DIR=/data`.
- Create `/data` and `/config` directories and `chown agent:agent /data`. `/config` will be the read-only config mount point.
- Also mount a writable `/tmp` via tmpfs in compose — but create the `/tmp` directory here so it exists for tmpfs mounting. This addresses IMPACT-ANALYSIS R3: "Read-only root FS breaks Python at runtime (some libs write to `~/.cache`, `/tmp`)."
- Set `HOME=/tmp` in `ENV` — this redirects any library that writes to `~/.cache` to use `/tmp` instead, which will be a tmpfs mount.
- `USER agent`.
- `WORKDIR /data`.
- `ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]`.

**What NOT to include in the Dockerfile:**
- No `EXPOSE` (the agent has no listening port).
- No `CMD` separate from `ENTRYPOINT` (entrypoint.sh handles args).
- No `ARG` or build-time secrets.
- No `COPY .` (copy only what is needed — not `.git`, not `docs/`, not `tests/`).

`container/entrypoint.sh` — POSIX sh only, `set -eu`:
- Check `${AGENT_DATA_DIR:-/data}` is writable. If not, print to stderr and exit 1.
- Check `/config/config.yaml` is readable. If not, print to stderr and exit 1.
- `exec agent "$@"` — exec form hands PID 1 to the Python process, which receives signals cleanly. SECURITY.md control 18.

**Tests to add:** None at this step (Dockerfile and shell script cannot be unit-tested in Python). The acceptance check for this step is a Docker build and `docker inspect` pass in Step 3.

**Acceptance check before proceeding to Step 3:**
- `docker build -t kayaclaw:dev .` completes without error.
- `docker image inspect kayaclaw:dev` shows no unexpected layers.
- The runtime image does not contain `gcc`, `make`, or `pip` (run `docker run --rm kayaclaw:dev which gcc` → non-zero exit).

---

### Step 3 — docker-compose.yaml, mount-policy.yaml, and SECURITY.md verification

**Files touched (≤3):**
1. `docker-compose.yaml`
2. `container/mount-policy.yaml`
3. `docs/discovery/container/SECURITY.md` (add verification notes)

**LOC estimate:** ~40 effective LOC in `docker-compose.yaml` (at the 40/60 target/cap). `mount-policy.yaml` and `SECURITY.md` updates are data/doc files, not counted.

**What to write:**

`docker-compose.yaml` — a single service definition named `agent` (NFR-BD3: "Container service names must use neutral names"). Key fields:

```yaml
services:
  agent:
    build: .
    image: kayaclaw:0.1
    restart: unless-stopped
    read_only: true           # SECURITY.md control 2
    cap_drop: [ALL]           # SECURITY.md control 6
    security_opt:
      - no-new-privileges:true  # SECURITY.md control 7
    environment:
      - TELEGRAM_BOT_TOKEN   # sourced from .env, not hardcoded — control 14
      - DEEPINFRA_API_KEY
      - ALLOWED_TELEGRAM_USER_IDS
      - AGENT_DATA_DIR=/data
      - LOG_LEVEL=INFO
      - HOME=/tmp            # redirects ~/.cache writes
    volumes:
      - agent-data:/data                       # named volume, writable — control 3
      - ./config.yaml:/config/config.yaml:ro   # config read-only — control 15
    tmpfs:
      - /tmp:mode=1777       # writable temp for Python libs — R3 mitigation
    networks:
      - agent-net
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 512M
          pids: 100           # SECURITY.md control 13
    # No network_mode: host — control 11
    # No pid: host — control 12
    # No ipc: host — control 12

networks:
  agent-net:
    driver: bridge            # SECURITY.md control 10, 11

volumes:
  agent-data:                 # named volume survives docker compose down — FR-ME2
```

Env var convention: list env var names without values in the compose file. The values come from `.env` (gitignored). This is the correct pattern for `docker compose` secret handling — SECURITY.md control 14.

`container/mount-policy.yaml` — a human-readable declarative allowlist. Not enforced programmatically (enforcement is in the compose file itself); this file documents the policy for reviewers. Include:
- `required:` section listing `./config.yaml → /config/config.yaml:ro` and `agent-data → /data:rw`.
- `tmpfs:` section listing `/tmp`.
- `forbidden:` section listing: `/var/run/docker.sock`, `/`, `/home`, `/etc`, `~/.ssh`, `~/.aws`, `~/.config`.

`docs/discovery/container/SECURITY.md` — update the existing control table with inline verification notes. For each of the 16 required controls, add a verification note in the `Status` column or in a sub-note explaining how a reviewer can verify the control (e.g. "verified via `docker inspect` — see AC-6 procedure"). Add a `## Known deferrals` section listing controls 8, 9, 19, 20 with the reason for each.

**Tests to add (extend `tests/test_l5_container.py`):**

- `docker inspect` checks (run as a pytest integration test or as a manual verification script in `scripts/`): assert `HostConfig.ReadonlyRootfs` is `true`.
- Assert `HostConfig.CapDrop` includes `"ALL"`.
- Assert `Config.User` is `"10001"` or `"agent"` (non-root).
- Assert `HostConfig.SecurityOpt` includes `"no-new-privileges:true"`.
- `docker compose down && up` round-trip: assert the SQLite file exists and contains prior turns (AC-5 integration test — manual verification in 0.1, scripted in 0.2).
- Python libs do not write to the read-only root FS: run `docker run --rm kayaclaw:dev python -c "import importlib; print('ok')"` — assert exit 0 (proves the read-only FS does not break Python import machinery when `/tmp` is a tmpfs).

**Acceptance check before closing L5:**
- `docker compose up` starts without error.
- `docker inspect <container>` confirms: non-root UID, `ReadonlyRootfs: true`, `CapDrop: [ALL]`, `SecurityOpt: [no-new-privileges:true]`.
- `docker compose down && docker compose up` starts cleanly (idempotent).
- All 16 required SECURITY.md controls have verification notes.
- 4 deferred controls listed in "Known deferrals" section.

---

## 5. Lane-Level Acceptance

L5 is closed when ALL of the following are true:

| Check | Maps to |
|---|---|
| `docker compose up` starts the agent without error | AC-1, FR-L1 |
| `docker inspect` confirms non-root UID | AC-6, SECURITY.md control 1 |
| `docker inspect` confirms `ReadonlyRootfs: true` | AC-6, SECURITY.md control 2 |
| `docker inspect` confirms `CapDrop: [ALL]` | AC-6, SECURITY.md control 6 |
| `docker inspect` confirms `no-new-privileges:true` | AC-6, SECURITY.md control 7 |
| `docker compose down && up` round-trip completes | FR-L2, AC-5 |
| 16 controls in SECURITY.md marked verified with notes | AC-7 |
| 4 deferrals in SECURITY.md "Known deferrals" section | SPEC §4.1 |
| `pip show anthropic` inside the container returns non-zero (not installed) | AC-4 — verified at container level |
| `agent/__main__.py` effective LOC ≤ 50 | CONTRACT §Size Discipline |
| Dockerfile + entrypoint combined effective LOC ≤ 60 | CONTRACT §Size Discipline |
| `docker-compose.yaml` effective LOC ≤ 60 | CONTRACT §Size Discipline |
| `security-auditor` review approved | REVIEW-PROTOCOL Gate: container hardening |

---

## 6. Anti-Bloat Callouts

**Do NOT add a `network_mode: host` setting anywhere in `docker-compose.yaml`.** SECURITY.md control 11. If a test requires host networking, use `docker run` with a network flag for that one test, not a permanent compose change.

**Do NOT add per-agent networks (multiple bridge networks).** SECURITY.md control 20 is explicitly stage 2. One `agent-net` bridge network for 0.1.

**Do NOT add seccomp or AppArmor profiles.** SECURITY.md controls 8 and 9 are stage 2. Adding them in 0.1 without proper profiling would be a false control.

**Do NOT add a `healthcheck` directive in compose.** 0.1 does not define a health endpoint — adding a healthcheck would require one. Healthchecks are 0.2+ work.

**Do NOT add CI/CD pipeline files (GitHub Actions `.yml`).** SECURITY.md control 19 (CVE scanning in CI) is stage 2. The 0.1 compose is the full deploy surface.

**Do NOT copy `docs/`, `tests/`, or `.git/` into the Docker image.** The `COPY` instructions in the Dockerfile copy only `pyproject.toml` and `agent/`. Everything else stays on the host.

**Do NOT add a `COPY .dockerignore` or embed logic in entrypoint.sh beyond the two sanity checks.** The entrypoint must remain ≤ 20 lines. Any additional startup logic belongs in `agent/__main__.py`.

---

## 7. Review Gates Checklist

In sequence, before closing this lane:

1. **`/plan-eng-review`** on this plan file — mandatory, non-skippable (container hardening is explicitly listed as non-skippable in REVIEW-PROTOCOL and global CLAUDE.md).

2. **`code-reviewer` Pre-Test Gate** — after Step 1 (`agent/__main__.py`). Check: no hardcoded paths, ConfigError handled correctly, asyncio.run() usage.

3. **`/codex-review`** on the Step 1 staged diff — before committing. Cannot skip: `__main__.py` is the entry point that wires all security-relevant components together.

4. **`code-reviewer` Pre-Test Gate** — after Step 2 (Dockerfile + entrypoint). Check: multi-stage structure, non-root user, no build tools in runtime stage, exec form in entrypoint.

5. **`/codex-review`** on the Step 2 staged diff — before committing. Cannot skip: container hardening code.

6. **`code-reviewer` Pre-Test Gate** — after Step 3 (compose + mount policy + SECURITY.md updates). Check: all 16 controls present, 4 deferrals listed, no `network_mode: host`, resource limits set.

7. **`/codex-review`** on the Step 3 staged diff — before committing. Cannot skip: compose file is a security surface.

8. **`/simplify`** — after all three steps land. Confirm all three LOC budgets are within target.

9. **`code-reviewer` Post-Test Gate** — after `docker inspect` verification passes.

10. **`security-auditor`** — **required** (REVIEW-PROTOCOL: "Required for L5 (container)"). Verify: all 16 controls are implemented as claimed, tmpfs `/tmp` covers all writable paths Python needs, no secrets baked into any layer, no shell features in entrypoint.sh, `pip show anthropic` inside container returns non-zero.

11. **`git-steward`** — commit message must include `Codex-reviewed (VERDICT: ...)` and `LOC: +n -0 (module __main__ now n/50, Dockerfile+entrypoint now n/60, compose now n/60)`.
