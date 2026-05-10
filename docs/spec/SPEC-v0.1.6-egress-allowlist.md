# SPEC v0.1.6 — Egress destination allowlist + automated egress pen-test

Status: Spec Brief LOCKED. All 5 open questions resolved with Anson 2026-05-09. Ready for plan-writing.
Author: hao
Date: 2026-05-09

## 1. Problem statement

The kayaclaw container's threat model treats the LLM as untrusted and assumes it may emit instructions that try to exfiltrate data or call attacker-controlled hosts. Today the agent-net bridge permits all outbound IPv4. Closing SECURITY.md control #10b (egress destination allowlist) removes the data-exfiltration vector before any tool-use surface ships in later versions.

Impact if unsolved: a prompt-injected or malicious LLM response can cause the bot process to reach arbitrary hosts. Workaround today: trust the LLM, which contradicts the documented threat model.

## 2. Goal

Lock kayaclaw container egress to a small, config-derived allowlist (Telegram + the configured LLM provider) and prove it works with an automated test that runs in CI as the verification gate.

## 3. User-visible outcome

- **Operator view:** `docker compose up` still works. config.yaml remains the single source of truth; the LLM provider host is derived from `base_url`. No new required env var. Logs surface a clear "egress denied: <host>" line if anything tries to leave the allowlist.
- **Security evaluator view:** README "Verifying the security posture" gains an egress-allowlist bullet with a one-command reproduction. SECURITY.md #10b flips from deferred to implemented. CI workflow run on every PR shows the egress test passing (deny-by-default proven, allow-listed hosts proven reachable).

## 4. Resolved decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | tinyproxy sidecar | Smallest known-good HTTP forward proxy with hostname allowlist. Avoids NET_ADMIN cap (preserves cap-drop control). squid is overkill. |
| 2 | HTTPS_PROXY env var | httpx, urllib3, OpenAI-compatible clients respect it natively. Zero per-client code. |
| 3 | Auto-generate allowlist from config.yaml at startup | Matches "config.yaml is the single source of truth." Operator already sets `base_url`; we parse the host from it. No new required env var. No migration step. |
| 4 | Sidecar container in docker-compose | One process per container. Shared `agent-net`. Bot has no external network; only the proxy does (`internal: true` on agent-net + proxy on a second network with external access). |
| 5 | pytest test in `tests/test_l6_egress.py`, run in CI | Same posture as v0.1.5 CVE scan: the gate workflow IS the verification. Uses `docker exec` against the running compose stack. |
| 6 | Failure mode = HTTP 403 from proxy | tinyproxy returns 403 for non-allowlisted destinations. Bot logs the proxy error verbatim. Today no code path actually fetches arbitrary URLs, so the gate is preventive for future tool-use surface. |
| 7 | Hostname allowlist | Readable, survives upstream IP rotation. tinyproxy's `Filter` + `FilterDefaultDeny Yes` + hostname patterns. |
| 8 | Add tinyproxy image to cve-scan.yml | New attack surface gets the same scrutiny. Pin a specific tinyproxy image tag (not `latest`). |
| 9 | README + SECURITY.md updates | One bullet in README's verification section; #10b moves to implemented. No em-dashes. No internal infra names. |
| 10 | No migration step | Auto-detection from `base_url` means existing v0.1.5 configs work as-is. CHANGELOG notes the new egress posture, not a required action. |
| 11 | New workflow `egress-test.yml` | Separate from cve-scan (different concern: runtime behavior vs static image scan). Mirrors v0.1.5's clean one-workflow-per-gate pattern. |
| 12 | Allowlist `api.telegram.org` plus Telegram media wildcard | Verify Telegram media hosts during implementation; if media uses `*.telegram.org`, allowlist that wildcard. If a separate CDN host is used, allowlist it explicitly. Captured in tinyproxy config comments. |

## 5. Acceptance criteria

- **AC-001:** Given a running stack with `base_url: https://openrouter.ai/api/v1` in config.yaml, when the bot attempts an HTTPS request to `openrouter.ai`, the request succeeds via the proxy.
- **AC-002:** Given the same stack, when any process inside the bot container attempts an HTTP/HTTPS request to `evil.example.com` (or `1.1.1.1`), the request fails with a proxy-issued 403 or equivalent connection failure, and the denial is logged.
- **AC-003:** Given the bot container, `env | grep -i proxy` shows `HTTPS_PROXY` and `HTTP_PROXY` pointing at the tinyproxy sidecar.
- **AC-004:** Given a PR to main, CI runs `egress-test.yml` and fails the PR if either the deny-side or allow-side assertion fails.
- **AC-005:** Given `docker compose up` with no extra env beyond v0.1.5, the tinyproxy allowlist is generated from `config.yaml`'s `base_url` plus the Telegram host(s) without operator action.
- **AC-006:** Given the v0.1.6 release, `cve-scan.yml` scans both `kayaclaw:dev` and the pinned tinyproxy image.

## 6. Resolved decisions (was: open questions)

- **U-1 RESOLVED (code audit):** Bot only ever talks to `api.telegram.org` and the configured LLM provider host. Connector is text-only (`filters.TEXT & (~filters.COMMAND)`) so no media-download paths. Allowlist on day one = those two.
- **U-2 RESOLVED (Anson, build our own):** Build our own ~10-LOC minimal Alpine Dockerfile shipping tinyproxy. Smaller attack surface, fully auditable, no external image dependency.
- **U-3 RESOLVED (Anson, block):** Block all non-allowlisted hosts including LLM SDK telemetry. Anything that needs telemetry can be added to the allowlist with a written justification.
- **U-4 RESOLVED (Anson, every PR):** Egress test runs on every PR. Same posture as v0.1.5 cve-scan.
- **U-5 RESOLVED (Anson, fail-closed):** Container refuses to start if `base_url` cannot be parsed for hostname. Hardened brand; clear error to operator beats silently-broken egress allowlist.

## 7. Risks

- **R-1:** Telegram uses an undocumented host the bot needs at runtime; allowlist breaks production. Mitigate: outbound audit before merge, allowlist `*.telegram.org` wildcard.
- **R-2:** tinyproxy image has unfixed CVEs that fail cve-scan.yml. Mitigate: pin a known-clean version; if needed, add an allowlist entry in `.trivyignore` with justification.
- **R-3:** Auto-detection of `base_url` host is too clever and silently allows the wrong host. Mitigate: log resolved allowlist on container start; egress test catches drift.
- **R-4:** Egress test flaky in CI (network jitter to Telegram). Mitigate: deny-side asserts against deterministic local sink; allow-side uses HEAD against `api.telegram.org` with retry.
- **R-5:** HTTPS_PROXY doesn't cover non-HTTP egress (raw sockets, DNS bypass). Mitigate: `internal: true` on agent-net means bot has no external route at all; HTTPS_PROXY is the only egress path.
- **R-6:** Operator with a non-OpenAI-compatible provider that doesn't honor HTTPS_PROXY. Mitigate: documented in README; current shipped providers all honor it.

## 8. Out of scope

- Per-agent network isolation (control #20). Deferred.
- seccomp (#8), AppArmor (#9). Deferred.
- Egress logging to a SIEM. Local container logs only.
- Tool-use surface for the LLM. Egress allowlist is preventive groundwork; no tool-call code in v0.1.6.
- Outbound DNS filtering. Docker embedded DNS unchanged.
- Host-level firewall guidance.

## 9. LOC envelope

| File | LOC |
|------|-----|
| `docker-compose.yaml` | +~25 |
| `tinyproxy/Dockerfile` (if we build our own) | +~15 |
| `scripts/generate_tinyproxy_conf.py` (or shell) | +~40 |
| `tinyproxy.conf.template` | +~20 |
| `tests/test_l6_egress.py` | +~80 |
| `.github/workflows/egress-test.yml` | +~40 |
| `.github/workflows/cve-scan.yml` edits | +~10 |
| README + SECURITY.md + CHANGELOG | LOC-exempt |
| **Total** | **~230** |

Single tiny-step.

## 10. Verification path

The gate workflow IS the verification (mirroring v0.1.5):

1. `docker compose up -d`
2. Wait for healthcheck
3. `docker exec kayaclaw-bot curl https://api.telegram.org` → expect 2xx/3xx
4. `docker exec kayaclaw-bot curl https://evil.example.com` → expect 403 from proxy
5. `docker exec kayaclaw-bot curl http://1.1.1.1` → expect failure
6. Tear down

PR blocked if any assertion fails.

## 11. Workflow gates

- `/plan-eng-review` on plan before code-implementer (mandatory).
- `/simplify` after implementation, before `/codex-review` (mandatory).
- `/codex-review` on staged diff (mandatory; security + new public surface).
- oss-readme-reviewer on README + CHANGELOG + SECURITY.md edits before Telegram approval.
- End-to-end verification against the test bot before public push.
- Per-piece Telegram approval on public copy before any commit/push.
