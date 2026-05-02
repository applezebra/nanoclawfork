# Container Security Posture

**Goal:** Match NanoClaw's hardening level, control-for-control. Nothing weaker.

This file is the audit checklist. Every control listed here MUST be enforced.
If a control is intentionally relaxed (e.g. for development), that exception
must be documented in this file, not implicit in code.

## Control checklist

| # | Control | Mechanism | Status |
|---|---------|-----------|--------|
| 1 | Non-root user inside container | `USER agent` (UID 10001) in Dockerfile | ✅ |
| 2 | Read-only root filesystem | `read_only: true` in compose | ✅ enforced via compose |
| 3 | Writable scratch only via tmpfs/volume | named volume `agent-data` mounted at `/data` | ✅ |
| 4 | No host bind mounts except explicit allowlist | `mount-policy.yaml` consulted before any compose mount | ✅ policy file |
| 5 | No Docker socket mount | forbidden in `mount-policy.yaml` | ✅ |
| 6 | Dropped Linux capabilities | `cap_drop: [ALL]` in compose, no `cap_add` | ✅ |
| 7 | No new privileges | `security_opt: [no-new-privileges:true]` in compose | ✅ |
| 8 | Seccomp profile (default-deny extended) | `security_opt: [seccomp:./container/seccomp.json]` *(stage 2)* | ⏳ stage 2 |
| 9 | AppArmor / SELinux confinement | host-dependent, profile shipped *(stage 2)* | ⏳ stage 2 |
| 10 | Network egress allowlist | `networks` segmentation + provider-only egress | ✅ compose |
| 11 | No host network | no `network_mode: host` ever | ✅ |
| 12 | No host PID/IPC namespace sharing | default namespacing only | ✅ |
| 13 | Resource limits (CPU, memory, pids) | `deploy.resources.limits` in compose | ✅ |
| 14 | Secrets via env vars from `.env`, not baked in | Dockerfile contains zero secrets | ✅ |
| 15 | Config mounted read-only | `/config/config.yaml:ro` | ✅ |
| 16 | Minimal base image | `python:3.12-slim` (consider distroless stage 2) | ✅ |
| 17 | No build tools in runtime image | multi-stage build, runtime has no `gcc`/`apt` | ✅ |
| 18 | No shell features in entrypoint | POSIX `sh` with `set -eu`, exec form | ✅ |
| 19 | Image scanned for CVEs in CI | `trivy` scan in GH Actions *(stage 2)* | ⏳ stage 2 |
| 20 | Per-agent network isolation | each agent group → its own bridge network | ⏳ stage 2 |

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
