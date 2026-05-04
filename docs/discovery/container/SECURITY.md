# Container Security Posture

> **This file is the container hardening control checklist** — what controls are enforced and how to verify them. It is **not** the vulnerability disclosure policy; that lives at the repo root in [`SECURITY.md`](../../../SECURITY.md). Same filename, different purpose. Auditors verifying container controls should be on this page; researchers reporting a vulnerability should be on the root one.

**Goal:** Match NanoClaw's hardening level, control-for-control. Nothing weaker.

This file is the audit checklist. Every control listed here MUST be enforced.
If a control is intentionally relaxed (e.g. for development), that exception
must be documented in this file, not implicit in code.

## Control checklist

Verification commands assume the image is built (`docker build -t kayaclaw:0.1 .`).

| # | Control | Mechanism | Verification |
|---|---------|-----------|--------------|
| 1 | Non-root user inside container | `USER agent` (UID 10001) in Dockerfile | ✅ `docker run --rm --entrypoint id kayaclaw:0.1` → `uid=10001(agent) gid=10001(agent)` |
| 2 | Read-only root filesystem | `read_only: true` in `docker-compose.yaml` | ✅ `docker inspect <container> -f '{{.HostConfig.ReadonlyRootfs}}'` → `true` |
| 3 | Writable scratch only via tmpfs/volume | named volume `agent-data` at `/data` + tmpfs at `/tmp` | ✅ compose `volumes:` + `tmpfs:` blocks; verified by `docker run --rm --read-only --tmpfs /tmp -e HOME=/tmp --entrypoint python kayaclaw:0.1 -c "from agent.connectors.telegram import _is_allowed; print('ok')"` succeeds |
| 4 | No host bind mounts except explicit allowlist | `container/mount-policy.yaml` documents intent; compose enforces | ✅ compose `volumes:` lists exactly two mounts (config.yaml:ro + agent-data) |
| 5 | No Docker socket mount | forbidden in `mount-policy.yaml`; absent from compose | ✅ `grep -F 'docker.sock' docker-compose.yaml` → empty |
| 6 | Dropped Linux capabilities | `cap_drop: [ALL]` in compose, no `cap_add` | ✅ `docker inspect <container> -f '{{.HostConfig.CapDrop}}'` → `[ALL]` |
| 7 | No new privileges | `security_opt: [no-new-privileges:true]` in compose | ✅ `docker inspect <container> -f '{{.HostConfig.SecurityOpt}}'` includes `no-new-privileges:true` |
| 10 | Network confined to bridge (no host net) | `networks: agent-net` (driver bridge); no `network_mode: host` | ✅ `grep -F 'network_mode' docker-compose.yaml` → empty. **Note:** this is NOT a destination allowlist — bridge permits all outbound IPv4 by default. True egress destination allowlisting is deferred (see Known deferrals: control 10b). Audit P2-4. |
| 11 | No host network | no `network_mode: host` ever | ✅ same grep as control 10 |
| 12 | No host PID/IPC namespace sharing | default namespacing only | ✅ no `pid:` or `ipc:` keys in compose |
| 13 | Resource limits (CPU, memory, pids) | `deploy.resources.limits` in compose | ✅ compose declares `cpus: "1.0"`, `memory: 512M`, `pids: 100` |
| 14 | Secrets via env vars from `.env`, not baked in | Dockerfile contains zero secrets; compose lists env names only | ✅ Two-layer check (audit P2-2): (a) layer-string scan: `docker history --no-trunc kayaclaw:0.1 \| grep -E 'ENV.*=(sk-\|[0-9]+:)'` → empty (catches `ENV TOKEN=value` bakes, ignores name-only references); (b) full image content scan: `docker save kayaclaw:0.1 \| tar -xO 2>/dev/null \| strings \| grep -E '(sk-[A-Za-z0-9_-]{20,}\|[0-9]{8,12}:[A-Za-z0-9_-]{30,})'` → empty. The character class `[A-Za-z0-9_-]` covers OpenAI's sk-proj-* / sk-live-* hyphenated formats AND classic sk-* AND Telegram bot tokens (codex round 3 P1). |
| 15 | Config mounted read-only | `./config.yaml:/config/config.yaml:ro` | ✅ compose `volumes:` declares `:ro` suffix |
| 16 | Minimal base image | `python:3.12-slim` | ✅ `docker history kayaclaw:0.1 \| head -3` shows the slim base; distroless deferred to stage 2 |
| 17 | No build tools in runtime image | multi-stage build; runtime stage strips pip + ensurepip | ✅ `docker run --rm --entrypoint sh kayaclaw:0.1 -c "ls /usr/local/bin/pip* gcc 2>&1"` → `No such file`; `docker run --rm --entrypoint python kayaclaw:0.1 -c "import ensurepip"` → `ModuleNotFoundError` |
| 18 | No shell features in entrypoint | POSIX `sh` with `set -eu`, exec form | ✅ `head -1 container/entrypoint.sh` → `#!/bin/sh`; `sh -n container/entrypoint.sh` succeeds |
| 8 | Seccomp profile (default-deny extended) | `security_opt: [seccomp:./container/seccomp.json]` | ⏳ stage 2 (see Known deferrals) |
| 9 | AppArmor / SELinux confinement | host-dependent profile shipped | ⏳ stage 2 (see Known deferrals) |
| 19 | Image scanned for CVEs in CI | `trivy` scan in CI workflow | ⏳ stage 2 (see Known deferrals) |
| 20 | Per-agent network isolation | each agent group → its own bridge network | ⏳ stage 2 (see Known deferrals) |

**Bonus controls beyond the original 20** (added during 0.1 implementation):

| Control | Mechanism | Verification |
|---------|-----------|--------------|
| AC-4 zero Anthropic surface | `pydantic-ai-slim[openai]` instead of `pydantic-ai` meta | ✅ `docker run --rm --entrypoint python kayaclaw:0.1 -c "import anthropic"` → `ModuleNotFoundError` |
| __pycache__ writes suppressed under read-only FS | `ENV PYTHONDONTWRITEBYTECODE=1` in Dockerfile | ✅ no `.pyc` files appear under `/usr/local/lib/python3.12/site-packages/agent/` after run |
| Entrypoint rejects non-regular config files | `[ ! -f \| ! -r ]` guard | ✅ mounting a directory at `/config/config.yaml` fails fast with a clear error message |

## What this is NOT (yet)

- **Not gVisor / Kata Containers** — userspace kernel or microVM isolation. Future option for highly-untrusted workloads.
- **Not rootless Docker** — Docker daemon still runs as root on host. Recommend rootless or Podman in production.
- **Not air-gapped by default** — agents have outbound network for LLM provider calls. Egress allowlist controls *which* destinations.

## Threat model assumptions

- The LLM is **untrusted**. It may output anything, including instructions to exfiltrate data or escape the container.
- The host is **trusted**. The user controls the host and chooses what to mount.
- The provider endpoint is **trusted-but-verify**. Egress is allowlisted so a compromised LLM can't call arbitrary URLs.

## When relaxing a control

Document the exception inline in `docker-compose.yaml` with a `# RELAXED: <reason>` comment AND in this file under a "Known exceptions" section. No silent relaxations.

## Known exceptions

*(none yet)*

## Known deferrals

The following controls are explicitly out of scope for 0.1 and tracked for stage 2.
A control listed here is **NOT** silently relaxed — it is acknowledged-and-deferred,
with a clear path to enabling it.

| # | Control | Reason for deferral | Stage 2 plan |
|---|---------|---------------------|--------------|
| 8 | Seccomp profile | A meaningful default-deny seccomp profile requires runtime profiling against the actual syscall surface of pgttb 22.x + httpx + sqlite3 + the OpenAI provider stack. Shipping an unprofiled profile would either be no-op (allow-all) or break the bot. | Profile under load with `strace -c` + `falco`, generate a tight allowlist, ship as `container/seccomp.json` referenced from compose. |
| 9 | AppArmor / SELinux | Host-dependent: AppArmor on Ubuntu/Debian, SELinux on RHEL/Fedora. A profile shipped for the wrong host is worse than none. | Document host detection in the README; ship per-host profile templates. |
| 19 | CI CVE scan | No CI pipeline exists in 0.1 — the deploy surface IS the local `docker compose up`. There is no pipeline to plug a scanner into. | Add `.github/workflows/scan.yml` running `trivy image` on every PR after the public repo flip (L6). |
| 20 | Per-agent network isolation | 0.1 has exactly one agent group (`personal-assistant`). Per-group network segmentation is meaningful only when there are multiple groups with different egress profiles. | When the design adds multi-agent support, add one bridge network per group + per-group egress allowlist. |
| 10b | Network egress destination allowlist (audit P2-4) | Bridge networks have NO destination allowlist — control 10 above only confines to the bridge (no host-net). True egress allowlisting needs an HTTP proxy sidecar (tinyproxy/squid) bound to `api.telegram.org` + the configured provider host, OR `internal: true` on `agent-net` plus a forwarding sidecar. Acceptable in 0.1 because there is no eval-of-LLM-output (no tool use); becomes the FIRST control to add when tool use lands. | Add a tinyproxy sidecar with allowlist in `docker-compose.yaml`; route all bot egress through it via `HTTPS_PROXY` env. |
