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
| 10 | Network confined to bridge (no host net) | `networks: agent-net` (driver bridge); no `network_mode: host` | ✅ `grep -F 'network_mode' docker-compose.yaml` → empty. As of v0.1.6 `agent-net` is also `internal: true`, so the bot has no direct external route at all (see control 10b). |
| 10b | Network egress destination allowlist | tinyproxy sidecar on `agent-net` + `egress-net`; bot's `HTTPS_PROXY=http://proxy:8888`; `agent-net: internal: true`; allowlist auto-derived from `config.yaml` `base_url` plus Telegram hosts | ✅ Pen-test workflow `.github/workflows/egress-test.yml` runs on every PR: asserts allowlisted hosts (Telegram + configured provider) succeed, non-allowlisted hosts return proxy 403, IP-literal bypass returns 403, and direct external traffic from the bot fails at the kernel layer (proves `internal: true`). Local re-prove: `docker compose -p kayaclaw up -d && docker exec kayaclaw-agent-1 python3 -c "import httpx; print(httpx.head('https://evil.example.com', timeout=5))"` raises a `ProxyError` whose message includes `403` (tinyproxy denial). |
| 11 | No host network | no `network_mode: host` ever | ✅ same grep as control 10 |
| 12 | No host PID/IPC namespace sharing | default namespacing only | ✅ no `pid:` or `ipc:` keys in compose |
| 13 | Resource limits (CPU, memory, pids) | top-level `cpus`, `mem_limit`, `pids_limit` in compose (NOT `deploy.resources.limits`; `deploy.*` is Swarm-only and is silently ignored by `docker compose up`) | ✅ `grep -E '^\s*(cpus\|mem_limit\|pids_limit):' docker-compose.yaml` returns three lines; `docker inspect kayaclaw-agent-1 -f '{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.PidsLimit}}'` returns `1000000000 536870912 100` |
| 14 | Secrets via env vars from `.env`, not baked in | Dockerfile contains zero secrets; compose lists env names only | ✅ Two-layer check: (a) layer-string scan: `docker history --no-trunc kayaclaw:0.1 \| grep -E 'ENV.*=(sk-\|[0-9]+:)'` → empty (catches `ENV TOKEN=value` bakes, ignores name-only references); (b) full image content scan: `docker save kayaclaw:0.1 \| tar -xO 2>/dev/null \| strings \| grep -E '(sk-[A-Za-z0-9_-]{20,}\|[0-9]{8,12}:[A-Za-z0-9_-]{30,})'` → empty. The character class `[A-Za-z0-9_-]` covers OpenAI's sk-proj-* / sk-live-* hyphenated formats AND classic sk-* AND Telegram bot tokens. |
| 15 | Config mounted read-only | `./config.yaml:/config/config.yaml:ro` | ✅ compose `volumes:` declares `:ro` suffix |
| 16 | Minimal base image | `python:3.12-slim` | ✅ `docker history kayaclaw:0.1 \| head -3` shows the slim base; distroless deferred to stage 2 |
| 17 | No build tools in runtime image | multi-stage build; runtime stage strips pip + ensurepip | ✅ `docker run --rm --entrypoint sh kayaclaw:0.1 -c "ls /usr/local/bin/pip* gcc 2>&1"` → `No such file`; `docker run --rm --entrypoint python kayaclaw:0.1 -c "import ensurepip"` → `ModuleNotFoundError` |
| 18 | No shell features in entrypoint | POSIX `sh` with `set -eu`, exec form | ✅ `head -1 container/entrypoint.sh` → `#!/bin/sh`; `sh -n container/entrypoint.sh` succeeds |
| 8 | Seccomp profile (default-deny extended) | `security_opt: [seccomp:./container/seccomp.json]` | ⏳ stage 2 (see Known deferrals) |
| 9 | AppArmor / SELinux confinement | host-dependent profile shipped | ⏳ stage 2 (see Known deferrals) |
| 19 | Dependencies, image, and Dockerfile scanned for CVEs in CI | `.github/workflows/cve-scan.yml` runs `trivy fs`, `trivy image`, and `trivy config` on every PR, every push to main, and a Mondays 03:00 UTC cron | ✅ `gh workflow view cve-scan` (or open the README badge link). CRITICAL and HIGH findings fail the build. Local re-prove: `trivy fs . --severity CRITICAL,HIGH`, `trivy image kayaclaw:0.1 --severity CRITICAL,HIGH`, `trivy config Dockerfile --severity CRITICAL,HIGH`. Active allowlist: [`.trivyignore`](../../../.trivyignore) (empty by default = no CVEs accepted; entries require justification + review-by date). |
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
| 20 | Per-agent network isolation | 0.1 has exactly one agent group (`personal-assistant`). Per-group network segmentation is meaningful only when there are multiple groups with different egress profiles. | When the design adds multi-agent support, add one bridge network per group + per-group egress allowlist. |
