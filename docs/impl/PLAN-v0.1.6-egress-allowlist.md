# PLAN — v0.1.6 Egress destination allowlist + automated egress pen-test

References: SPEC-v0.1.6-egress-allowlist.md
Status: REVISED 2026-05-10 after /plan-eng-review found 5 P1 + 7 P2 + 5 P3. All P1s and material P2s applied. See "## Eng-review revisions (applied 2026-05-10)" section near the end for the full revision log.

## Summary of approach

Add a tinyproxy sidecar container that enforces a hostname allowlist derived
automatically from `config.yaml`'s `base_url`. The bot container loses its
direct external route (`agent-net` becomes `internal: true`); all egress must
flow through the proxy via `HTTPS_PROXY`. A pytest test exercises both the
allow-side and the deny-side in CI on every PR, acting as the egress pen-test.
No operator action required beyond what v0.1.5 already needs.

## Architecture

### BEFORE (v0.1.5)

```
┌──────────────────────────────────────────────────────┐
│  docker-compose stack                                 │
│                                                       │
│  ┌───────────────┐                                    │
│  │  agent        │────── agent-net (bridge) ─────────────▶  api.telegram.org
│  │  kayaclaw:0.1 │                                    │        ▶  openrouter.ai
│  └───────────────┘                                    │        ▶  evil.example.com  ← NO BLOCK
└──────────────────────────────────────────────────────┘
agent-net driver: bridge (no destination filter)
```

### AFTER (v0.1.6)

```
┌──────────────────────────────────────────────────────────────┐
│  docker-compose stack                                         │
│                                                               │
│  ┌───────────────┐   HTTPS_PROXY=   ┌─────────────────────┐  │
│  │  agent        │─── http://proxy ─▶│  proxy (tinyproxy)  │  │
│  │  kayaclaw:0.1 │    :8888          │  alpine:3.20        │  │
│  │               │                  │                     │  │
│  └───────────────┘                  │  Filter allowlist:  │  │
│         │                           │  api.telegram.org   │  │
│         │                           │  *.telegram.org     │  │
│     agent-net                       │  openrouter.ai      │  │
│  (internal: true,                   │  (derived from      │  │
│   NO external route)                │   config.yaml)      │  │
│                                     └──────────┬──────────┘  │
│                                                │              │
│                                            egress-net         │
│                                         (external access)     │
└────────────────────────────────────────────────│─────────────┘
                                                 │
                             ┌───────────────────┴────────────────────┐
                             │ ALLOWED                  DENIED         │
                             │   api.telegram.org  ✅   evil.example.com ❌ 403
                             │   *.telegram.org    ✅   1.1.1.1        ❌ 403
                             │   openrouter.ai     ✅   any other host ❌ 403
                             └────────────────────────────────────────┘
```

A request to `api.telegram.org` travels: agent process -> HTTPS_PROXY env ->
tinyproxy on port 8888 -> proxy checks allowlist -> allowed -> egress-net ->
internet -> response returns via same path.

A request to `evil.example.com` travels: agent process -> HTTPS_PROXY env ->
tinyproxy on port 8888 -> proxy checks allowlist -> DENIED -> tinyproxy
returns HTTP 403 -> connection fails in the bot process -> error logged.

The bot container itself has no external route at all (`agent-net` is
`internal: true`). Even if something bypassed the proxy env var, the kernel
would drop the packet before it left the compose namespace.

## Glossary

- **tinyproxy**: a lightweight HTTP/HTTPS forward proxy (~15k LOC C). It
  implements CONNECT tunneling (needed for HTTPS) and a hostname allowlist via
  the `Filter` + `FilterDefaultDeny Yes` directives. Chosen over squid: squid
  is configurable to the point of complexity; tinyproxy's surface area is small
  enough to audit fully. Chosen over iptables: iptables rules require NET_ADMIN
  capability, which we already cap-dropped.

- **forward proxy**: a proxy that the CLIENT is configured to use (via
  `HTTPS_PROXY` env). The client sends its request to the proxy; the proxy
  fetches it on the client's behalf. The client never opens a TCP socket to
  the destination itself. Contrast with a reverse proxy (sits in front of a
  server, transparent to the client).

- **HTTPS_PROXY / HTTP_PROXY env vars**: a convention honored by httpx,
  urllib3, python-telegram-bot, and virtually all OpenAI-compatible client
  libraries. Setting these env vars in the bot container makes ALL HTTP and
  HTTPS traffic from those libraries route through the proxy automatically.
  Zero per-client code changes required.

- **`internal: true` (Docker network flag)**: disables the external gateway on
  that network. Containers on the network can reach each other but have no
  route to the internet. The only way out is through another container that
  sits on a second network that DOES have external access.

- **egress-net**: the second Docker network added in v0.1.6. Only the proxy
  sidecar sits on it. It has external access. `agent-net` keeps `internal: true`.

- **FilterDefaultDeny**: a tinyproxy directive meaning "deny all hostnames not
  explicitly listed in a Filter file." The complementary directive `Filter
  /etc/tinyproxy/filter.txt` points at the allowlist file. With both set,
  tinyproxy returns HTTP 403 for any host not in the list.

- **Option A (shared volume for host handoff)**: the chosen mechanism for
  passing the derived LLM provider hostname from the bot's startup to the proxy
  sidecar. Detailed in the Topology decision below.

- **SHA-pinned action**: GitHub Actions `uses:` references pinned to a full
  commit SHA instead of a floating version tag. A floating tag can be silently
  updated upstream; a SHA is immutable. Supply-chain risk mitigation.

## Topology decision: how the proxy learns the LLM provider host

The proxy needs the LLM provider hostnames (one PER configured provider,
including fallback chain entries) before it starts filtering. Those hostnames
live in `config.yaml`'s `providers.<name>.base_url`.

**REVISED post eng-review (2026-05-10): Option B locked. Option A rejected.**

**Option A (REJECTED):** The bot's entrypoint reads `config.yaml`, extracts
hostnames, writes them to a shared volume; the proxy polls for the file.
Reasons rejected:
- Bot runs as UID 10001 with read_only filesystem; writing to a shared
  named volume hits permission issues unless the volume is pre-chowned.
- The "proxy polls for file" loop is operationally fragile: race conditions
  on first boot, ambiguous failure modes if the file never arrives.
- Mixes proxy-infrastructure concerns into the bot's entrypoint.
- The earlier version of Option A used `cfg['providers'][0]` which is
  outright incorrect (providers is a dict, not a list — see P1-1).

**Option B (LOCKED):** A separate one-shot `init` service runs first.
- Reuses the bot image (Python + PyYAML already present).
- Runs as root so it can write and chown the shared named volume cleanly.
- Walks ALL configured providers (handles v0.1.4 fallback chains).
- Writes `/run/shared/egress-allowlist.env` with `LLM_PROVIDER_HOSTS`
  (newline- or space-separated; multi-host).
- Both `bot` and `proxy` `depends_on: init: condition: service_completed_successfully`.
- The proxy then sees the file, renders `tinyproxy.conf`, starts listening.
- The bot additionally `depends_on: proxy: condition: service_healthy` so its
  first outbound call cannot precede a working proxy.

**Locked: Option B.** Cost: one extra ~10-line YAML stanza + the
`extract-allowlist.sh` script. Benefits: no race, no permission gymnastics,
no proxy poll loop, and the resolved allowlist is auditable via
`docker logs kayaclaw-init-1` independently of either bot or proxy.

## Step-by-step implementation

All 12 steps count as one logical change. Total LOC is ~280 (within the step
limit). Run `/simplify` on the staged diff before `/codex-review`.

---

### Step 1 — Create `tinyproxy/Dockerfile`

**Files:** `tinyproxy/Dockerfile` (new)
**LOC budget:** ~12

**What:** A minimal Alpine-based image that installs tinyproxy and ships two
scripts: the config generator and the tinyproxy config template.

**Why:** Building our own image (vs pulling a third-party tinyproxy image) keeps
the attack surface fully auditable. Alpine 3.20's tinyproxy package is the same
binary, but we control exactly what else is in the image.

**How:** `FROM alpine:3.20`. Install `tinyproxy` via `apk add --no-cache`.
`COPY` the two files (`generate-config.sh` and `tinyproxy.conf.template`) into
the image. `RUN chmod 0555` on the script. `ENTRYPOINT` calls
`generate-config.sh` which renders the template and then `exec`s tinyproxy.
No shell as PID 1 (same discipline as the bot image).

Pin the Alpine version: `FROM alpine:3.20` (not `latest`). Pin in cve-scan too.

---

### Step 2 — Create `tinyproxy/tinyproxy.conf.template`

**Files:** `tinyproxy/tinyproxy.conf.template` (new)
**LOC budget:** ~22

**What:** Base tinyproxy configuration with `__FILTER_FILE__` and
`__ALLOWED_HOSTS__` placeholder tokens that `generate-config.sh` replaces via
`sed` at container startup.

**Why:** Separating config structure from config values lets the shell script
do a simple search-and-replace rather than generating config from scratch.
The template is human-readable at review time.

**Key directives to include:**
```
Port 8888
Listen 0.0.0.0
Timeout 30
LogLevel Info
Filter __FILTER_FILE__
FilterDefaultDeny Yes
FilterURLs Yes
# Telegram hosts: api.telegram.org + *.telegram.org (for future media)
# LLM provider: derived from config.yaml base_url at startup (see generate-config.sh)
```

The `__FILTER_FILE__` token will be replaced with a path like
`/etc/tinyproxy/filter.txt`. That file is generated (not templated) — it is
a plain text list of hostnames, one per line, written by `generate-config.sh`.

Add a comment block near the top: "This file is generated from
tinyproxy.conf.template at container start. Edit the template, not this file."

---

### Step 3 — Create `tinyproxy/generate-config.sh`

**Files:** `tinyproxy/generate-config.sh` (new)
**LOC budget:** ~25

**REVISED** post Pass-2 review (NEW-P1): aligned with Option B. No poll loop.
Reads plural `LLM_PROVIDER_HOSTS` (space-separated) and emits one host per
line into the filter file. Under Option B the proxy starts with `depends_on:
init: condition: service_completed_successfully`, so the env file ALWAYS
exists when this script runs; missing-file is a hard error, not a wait.

**What:** A POSIX shell script that (a) reads `/run/shared/egress-allowlist.env`,
(b) validates `LLM_PROVIDER_HOSTS` is non-empty, (c) renders the template into
`/etc/tinyproxy/tinyproxy.conf` with one filter line per host (Telegram entries
+ each LLM provider host), (d) prints the resolved list to stdout, then (e)
`exec`s tinyproxy.

**Why shell (not Python):** Alpine ships with `busybox` sh and `sed`. Adding
Python adds ~50MB to the proxy image. The image stays tiny.

**Why fail-closed:** If the env file is missing or `LLM_PROVIDER_HOSTS` is
empty, fail with a clear error. Init service is supposed to have run; if it
didn't, the user sees the failure and can debug.

**Script:**
```sh
#!/bin/sh
set -eu
SHARED_ENV="/run/shared/egress-allowlist.env"
[ -f "$SHARED_ENV" ] || { printf 'generate-config: %s missing; init service did not run\n' "$SHARED_ENV" >&2; exit 1; }
# shellcheck disable=SC1090
. "$SHARED_ENV"
[ -n "${LLM_PROVIDER_HOSTS:-}" ] || { printf 'generate-config: LLM_PROVIDER_HOSTS empty in env file\n' >&2; exit 1; }

FILTER_FILE=/etc/tinyproxy/filter.txt
{
  # Telegram allowlist: anchored regex; FilterExtended Yes is set in the conf.
  # The "(^|\.)telegram\.org$" form matches api.telegram.org AND any subdomain.
  printf '(^|\\.)telegram\\.org$\n'
  for host in $LLM_PROVIDER_HOSTS; do
    # Anchor each host as a literal (escape dots) for the FilterExtended regex engine.
    escaped=$(printf '%s\n' "$host" | sed 's/\./\\./g')
    printf '^%s$\n' "$escaped"
  done
} > "$FILTER_FILE"

printf 'generate-config: resolved allowlist:\n' >&2
sed 's/^/  /' "$FILTER_FILE" >&2

# Render the conf template (sed-substitute __FILTER_FILE__ if used).
sed "s|__FILTER_FILE__|$FILTER_FILE|g" /etc/tinyproxy/tinyproxy.conf.template > /etc/tinyproxy/tinyproxy.conf

exec tinyproxy -d -c /etc/tinyproxy/tinyproxy.conf
```

**Filter file contents (rendered at runtime, anchored regex form):**
```
(^|\.)telegram\.org$
^openrouter\.ai$
^api\.deepinfra\.com$    # if a fallback chain references deepinfra
```

The Telegram wildcard line `(^|\.)telegram\.org$` matches `api.telegram.org`
exactly AND any subdomain like `cdn.telegram.org`, but does NOT match
`evil-telegram.org.attacker.com`. `FilterExtended Yes` (set in the .conf
template) enables regex evaluation. Each LLM provider host is anchored
literally with dots escaped.

---

### Step 4 — Create `container/extract-allowlist.sh` (used by the new init service)

**Files:** `container/extract-allowlist.sh` (new), `container/entrypoint.sh` (UNCHANGED)
**LOC budget:** ~25 new LOC

**REVISED** post eng-review (P1-1 + P1-4): Option A (bot writes env file at
its own startup) is REJECTED. We now use **Option B (a one-shot init service)**
that runs to completion BEFORE both the bot and the proxy start. This:

- removes the bot-vs-proxy race (no poll loop in proxy needed)
- removes the read-only-FS / UID-10001 permission problem (init runs as root,
  writes to a named volume, and chowns it for both consumers)
- isolates the config-extraction concern from the bot (the bot does not need
  to know about the proxy at all)
- makes the resolved allowlist auditable via `docker logs kayaclaw-init-1`

**What:** A small shell script that runs ONCE inside an Alpine init container.
It reads `/config/config.yaml`, walks ALL configured providers (including
those used by `fallback:`), extracts each `base_url` host, and writes the
union to a named volume that both bot and proxy mount.

**Why multi-provider extraction (P1-1 fix):** The previous draft used
`cfg['providers'][0]['base_url']` which is wrong twice over:
1. `providers` is `dict[str, ProviderSpec]` (see `agent/config.py:69`),
   not a list. `[0]` would raise `KeyError: 0`.
2. v0.1.4 introduced `fallback:` chains. A fallback provider's host MUST be
   allowlisted or fallback would silently fail (everything goes through proxy
   now, so the fallback's HTTPS request would 403).

**Script (`container/extract-allowlist.sh`, ~25 LOC):**

```sh
#!/bin/sh
# Extract every configured LLM provider hostname from config.yaml and write
# the egress allowlist to the shared volume. Runs ONCE at compose-up time
# inside the init service. Fail-closed: empty allowlist or unparseable
# YAML -> exit 1, init service fails -> bot and proxy do not start.

set -eu

OUT_DIR=/run/shared
OUT_FILE="$OUT_DIR/egress-allowlist.env"
mkdir -p "$OUT_DIR"

HOSTS=$(python3 - <<'PY'
import sys, yaml
from urllib.parse import urlparse
try:
    cfg = yaml.safe_load(open('/config/config.yaml'))
except Exception as e:
    print(f"extract-allowlist: cannot parse config.yaml: {e}", file=sys.stderr)
    sys.exit(1)
hosts = set()
for name, p in (cfg.get('providers') or {}).items():
    bu = (p or {}).get('base_url')
    if bu:
        h = urlparse(bu).hostname
        if h:
            hosts.add(h)
    if (p or {}).get('kind') == 'anthropic':
        # anthropic kind has a fixed host even with no base_url
        hosts.add('api.anthropic.com')
if not hosts:
    print("extract-allowlist: no provider base_url could be extracted; "
          "egress allowlist would be empty", file=sys.stderr)
    sys.exit(1)
print(' '.join(sorted(hosts)))
PY
)

# HOSTS is a space-separated list (e.g. "api.deepinfra.com openrouter.ai")
printf 'LLM_PROVIDER_HOSTS=%s\n' "$HOSTS" > "$OUT_FILE"
chmod 644 "$OUT_FILE"
printf 'extract-allowlist: resolved allowlist: api.telegram.org *.telegram.org %s\n' "$HOSTS"
```

**Image for the init service:** reuse the existing bot image (`kayaclaw:0.1`)
because Python + PyYAML + the script are already there. The init service
overrides the entrypoint to run `extract-allowlist.sh` and exits.

**`container/entrypoint.sh` itself:** UNCHANGED. The bot does not write the
allowlist; the init service does. This decouples bot logic from proxy
infrastructure.

---

### Step 4b — Add proxy healthcheck

**Files:** `tinyproxy/Dockerfile`
**LOC budget:** ~3 LOC (HEALTHCHECK directive)

**REVISED** post eng-review (P1-4 + P2-7): the bot must wait for the proxy
to be LISTENING before its first outbound call. `depends_on: condition:
service_started` only confirms the container started, not that tinyproxy is
serving. Add a HEALTHCHECK to the proxy Dockerfile:

```dockerfile
HEALTHCHECK --interval=2s --timeout=2s --retries=15 --start-period=2s \
  CMD nc -z localhost 8888 || exit 1
```

`nc` is already in Alpine's `busybox`. Health goes ready as soon as
tinyproxy binds 8888 (post-`generate-config.sh`). Bot uses
`depends_on: proxy: condition: service_healthy` (set in Step 5).

---

### Step 5 — Update `docker-compose.yaml`

**Files:** `docker-compose.yaml` (existing)
**LOC budget:** ~40 new/changed LOC

**REVISED** post eng-review (Option A → Option B). Add init service, add
proxy service with healthcheck, add named volume for the allowlist hand-off,
make `agent-net` internal, add `egress-net`, wire `HTTPS_PROXY` into the bot.

**Service ordering:**
```
init (one-shot)  →  proxy (long-running, depends on init success)
                 →  bot   (long-running, depends on init success AND proxy healthy)
```

**Init service (new):**
```yaml
init:
  image: kayaclaw:0.1   # reuse the bot image (script baked in via Dockerfile)
  user: "0:0"           # init runs as root so it can write + chown the volume
  read_only: true
  cap_drop: [ALL]
  security_opt:
    - no-new-privileges:true
  entrypoint: ["/bin/sh", "/usr/local/bin/extract-allowlist.sh"]
  volumes:
    - ./config.yaml:/config/config.yaml:ro
    - egress-allowlist:/run/shared
  networks: [agent-net]
  restart: "no"
```

`restart: "no"` is critical: this is a one-shot. The script exits 0 on success.

**REVISED** post Pass-2 (NEW-P2): the script is COPY'd into the bot image at
`/usr/local/bin/extract-allowlist.sh` via a single line in the existing
`Dockerfile` (after the existing `COPY container/entrypoint.sh ...` line).
This eliminates the bind-mount-from-host fragility (working directory drift,
script-not-found errors) and means the init service is a clean image-only
service.

`Dockerfile` addition (one line):
```
COPY container/extract-allowlist.sh /usr/local/bin/extract-allowlist.sh
RUN chmod +x /usr/local/bin/extract-allowlist.sh
```

**Proxy service (new):**
```yaml
proxy:
  build: ./tinyproxy
  image: kayaclaw-proxy:0.1
  restart: unless-stopped
  read_only: true
  cap_drop: [ALL]
  security_opt:
    - no-new-privileges:true
  cpus: 0.25
  mem_limit: 64m
  pids_limit: 32
  networks:
    - agent-net
    - egress-net
  volumes:
    - egress-allowlist:/run/shared:ro    # read-only mount: proxy never writes
  tmpfs:
    # Move tmpfs OFF /run to avoid masking the /run/shared volume mount.
    # Mount at /etc/tinyproxy where the rendered conf + filter file live.
    - /etc/tinyproxy:mode=1777,size=4m
  depends_on:
    init:
      condition: service_completed_successfully
  logging:
    driver: json-file
    options:
      max-size: "10m"
      max-file: "3"
```

Resource limits added (P3-1). `cpus: 0.25 / mem_limit: 64m` is more than
tinyproxy ever needs but bounds the blast radius.

**Bot service updates:**
- `environment:` add `HTTPS_PROXY=http://proxy:8888` and `HTTP_PROXY=http://proxy:8888`
- `depends_on:` (new keyed form):
  ```yaml
  depends_on:
    init:
      condition: service_completed_successfully
    proxy:
      condition: service_healthy
  ```
- `networks:` no change. Bot stays on `agent-net` only.

**Networks block:**
```yaml
networks:
  agent-net:
    driver: bridge
    internal: true       # NEW: bot has no direct external route
  egress-net:
    driver: bridge       # NEW: proxy uses this for external access
```

**Top-level volumes (new):**
```yaml
volumes:
  egress-allowlist:
    driver: local
```

Named volume (NOT tmpfs). Tmpfs is per-container; cannot be shared. The init
service writes the env file into this volume; the proxy mounts the same
volume read-only. P1-3 resolved: init runs as root, writes 644-permissioned
file, proxy reads as its own user (proxy runs as non-root per P3-2).

---

### Step 6 — Create `tests/test_l6_egress.py`

**Files:** `tests/test_l6_egress.py` (new)
**LOC budget:** ~80

**What:** Three pytest tests that use `subprocess.run(["docker", "exec",
"kayaclaw-agent-1", ...])` to assert egress behavior from inside the running
bot container. Marked `@pytest.mark.docker` so they are skipped if the
`--docker` pytest flag is absent (allows local `pytest` runs without a compose
stack).

**Why:** The test IS the egress pen-test. It exercises the actual network path,
not a mock. Running it in CI after `docker compose up -d` proves the egress
controls work end-to-end on every PR.

**REVISED** post eng-review (P1-2, P1-5, P2-6). The test suite now has FIVE
cases covering both the proxy filter AND the network-isolation layer.

**Test 1 — Allowlist allows Telegram (proxy filter allows):**
```python
result = subprocess.run(
    ["docker", "exec", "kayaclaw-agent-1",
     "curl", "-fsS", "-o", "/dev/null", "--max-time", "5", "-I",
     "https://api.telegram.org"],
    capture_output=True
)
assert result.returncode == 0, f"Telegram should be reachable. stderr: {result.stderr.decode()}"
```

**Test 2 — Proxy denies unauthorized host with explicit 403 (P2-6):**
```python
result = subprocess.run(
    ["docker", "exec", "kayaclaw-agent-1",
     "curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
     "--max-time", "5", "https://evil.example.com"],
    capture_output=True
)
# tinyproxy returns 403 for filter denials (CONNECT-tunnel pre-deny).
# Assert 403 specifically, not just "any non-zero exit", so the test
# does not silently pass on DNS failures or timeouts.
assert b"403" in result.stdout, (
    f"Expected proxy 403 for evil.example.com; got stdout={result.stdout!r} "
    f"stderr={result.stderr.decode()}"
)
```

**Test 3 — Proxy denies IP-literal bypass attempt:**
```python
# A malicious code path could try to skip DNS by using an IP literal.
# Proxy must still deny because no IP is allowlisted.
result = subprocess.run(
    ["docker", "exec", "kayaclaw-agent-1",
     "curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
     "--max-time", "5", "http://1.1.1.1"],
    capture_output=True
)
assert b"403" in result.stdout, (
    f"Expected proxy 403 for 1.1.1.1; got stdout={result.stdout!r}"
)
```

**Test 4 — Network isolation: bot has no direct external route (P1-2):**
```python
# Bypass the proxy entirely (--noproxy '*'). With agent-net internal: true,
# the request must fail at the kernel layer (connection refused / no route).
# This proves the network-level guarantee, not just the proxy filter.
result = subprocess.run(
    ["docker", "exec", "kayaclaw-agent-1",
     "curl", "-sS", "--noproxy", "*", "-o", "/dev/null",
     "--max-time", "5", "http://1.1.1.1"],
    capture_output=True, timeout=10
)
assert result.returncode != 0, (
    "internal: true must block direct external traffic from the bot"
)
# stderr should contain a connection failure marker, not a proxy 403.
err = result.stderr.decode().lower()
assert any(m in err for m in ("could not", "couldn't", "no route", "refused", "timed out")), (
    f"expected connection failure marker; stderr was: {err}"
)
```

**Test 5 — Allowlist allows LLM provider (deterministic test config):**
The CI compose override sets `base_url: https://openrouter.ai/api/v1` in
`config.yaml`. Test asserts `openrouter.ai` is reachable through the proxy
the same way as Test 1.

**Note on `evil.example.com`:** P1-5 raised the concern about real-world DNS
dependency. Resolved here: with `agent-net` `internal: true`, the bot has no
external DNS access at all; the CONNECT request goes to the proxy with
`evil.example.com` as the host header, and tinyproxy denies via Filter BEFORE
any external lookup. The test does not actually depend on `evil.example.com`
existing or resolving; the proxy denies based on its allowlist. (If we want
zero external mention, a future iteration can add a controlled `sink`
service. Not required for v0.1.6.)

**Note on container name:** the default compose container name for the `agent`
service is `kayaclaw-agent-1`. Verify this matches the actual container name
after `docker compose up`. If the project name differs, pass `-p kayaclaw` to
`docker compose` in the workflow.

**Test fixture:** use a `docker_stack` pytest fixture (scope=module) that:
1. Calls `subprocess.run(["docker", "compose", "up", "-d"], check=True)`.
2. Waits for the bot healthcheck via polling.
3. Yields (tests run).
4. Always calls `subprocess.run(["docker", "compose", "down", "-v"])` in teardown.

---

### Step 7 — Create `.github/workflows/egress-test.yml`

**Files:** `.github/workflows/egress-test.yml` (new)
**LOC budget:** ~50

**What:** A GitHub Actions workflow that builds both images, starts the compose
stack, runs the egress pytest suite, captures logs for debugging, and tears down.
Triggers on every PR. Separate workflow from `cve-scan.yml` (different concern:
runtime network behavior vs static image scan).

**Why separate workflow:** one workflow per gate is the v0.1.5 pattern. Mixing
runtime and static checks in one workflow makes failure messages ambiguous.

**Triggers:**
```yaml
on:
  pull_request:    # NOT pull_request_target (fork secret safety)
  push:
    branches: [main]
```

No cron: this is a runtime test, not a DB-freshness concern.

**Concurrency:**
```yaml
concurrency:
  group: egress-test-${{ github.ref }}
  cancel-in-progress: true
```

**Permissions:** `contents: read` and `actions: write` (same as cve-scan).

**Job timeout:** `timeout-minutes: 10`

**Key steps:**
1. `actions/checkout@<SHA>` (same SHA as cve-scan.yml)
2. `docker compose build` (builds both `agent` and `proxy` images)
3. `docker compose up -d`
4. `sleep 10` (fallback wait; implement healthcheck polling if flaky)
5. `pip install pytest` (or install from `pyproject.toml` dev extras if present)
6. `pytest tests/test_l6_egress.py --docker -v`
7. `always()`: `docker compose logs proxy agent` (for debugging failures)
8. `always()`: `docker compose down -v`

**Pin ALL action SHAs** the same way cve-scan.yml does. Fetch the SHA for the
pinned version of `actions/checkout` and `actions/cache` using:
```bash
gh api repos/actions/checkout/git/refs/tags/<TAG> --jq '.object.sha'
```

**Negative-case requirement (mandatory before merge):** same discipline as
v0.1.5. Before merging, temporarily edit `generate-config.sh` so the filter
file allows everything (comment out `FilterDefaultDeny Yes` equivalent), push,
and verify the deny-side test FAILS (Test 2 returns exit 0 when it should
return non-zero). Then revert and verify all three tests pass. Record the
temporary-commit SHA in the PR description.

---

### Step 8 — Update `.github/workflows/cve-scan.yml`

**Files:** `.github/workflows/cve-scan.yml` (existing)
**LOC budget:** ~10

**What:** Add `kayaclaw-proxy:dev` to the matrix so the tinyproxy image
receives the same CVE scrutiny as the bot image.

**Why:** The proxy is new attack surface (R-2 from spec: tinyproxy image may
have unfixed CVEs). It must be scanned from day 1.

**How:** In the matrix `include:` block, add:
```yaml
- target: proxy-image
  scan-type: image
  scan-ref: ''
  image-ref: kayaclaw-proxy:dev
  needs-build: true
```

Add a corresponding build step condition: `if: matrix.needs-build` already
handles the `kayaclaw:dev` build. For `kayaclaw-proxy:dev`, the build step
needs to run `docker compose build proxy`. The cleanest approach: change the
existing `Build image` step to run `docker compose build` (builds all services)
when `matrix.target` is `proxy-image`, or unconditionally run `docker compose
build` for all `needs-build: true` entries and let compose cache the layers.

Verify the `trivyignores` input name against the pinned SHA before committing
(same reminder as v0.1.5 plan Step 1).

---

### Step 9 — Update `docs/discovery/container/SECURITY.md`

**Files:** `docs/discovery/container/SECURITY.md` (existing)
**LOC budget:** LOC-exempt (docs)

**What:** Move control #10b from the Known Deferrals table to the main control
checklist as implemented. Same transformation as v0.1.5 did for control #19.

**How:**

Remove the `10b` row from the Known Deferrals table.

Add a new row to the main checklist (after row 10):

```
| 10b | Egress destination allowlist | tinyproxy sidecar on `egress-net`; bot on `internal: true` `agent-net` with `HTTPS_PROXY=http://proxy:8888`; allowlist auto-generated from `config.yaml` `base_url` + Telegram hosts | ✅ `docker compose up` then `docker exec kayaclaw-agent-1 curl -fsS https://evil.example.com` should fail with proxy 403; `docker exec kayaclaw-agent-1 curl -fsS https://api.telegram.org` should succeed. CI: `egress-test.yml` on every PR. |
```

Update the threat model section note: change "Egress is allowlisted so a
compromised LLM can't call arbitrary URLs" from future-tense to present-tense
(it is now implemented).

---

### Step 10 — Update `README.md`

**Files:** `README.md` (existing)
**LOC budget:** LOC-exempt (docs)

**What:** Add one bullet to the existing "Verifying the security posture"
section. No badge at the top of the README (humble voice rule). No new section.

**How:** Inside `## Verifying the security posture`, add:

```markdown
- **Egress allowlist (CI):** On every PR, a test starts the container stack and
  attempts to reach an unauthorized host from inside the bot container. The test
  must fail (proxy returns 403) for the PR to merge. To reproduce locally:
  `docker compose up -d && docker exec kayaclaw-agent-1 curl https://evil.example.com`
  should exit non-zero.
```

No internal tool names. No internal test infrastructure references.

---

### Step 11 — Update `CHANGELOG.md`

**Files:** `CHANGELOG.md` (existing)
**LOC budget:** LOC-exempt (docs)

**What:** Prepend the v0.1.6 entry above the existing `[0.1.5]` entry using
the locked text from the spec.

**How:**

```markdown
## [0.1.6] - YYYY-MM-DD

### Added
- kayaclaw's container now blocks outbound network requests to anywhere except
  your Telegram bot's API and your configured LLM provider. If something tries
  to reach a third website (a compromised dependency, a malicious LLM response,
  anything), the request is denied at the network level. The allowlist is
  generated automatically from your config.yaml, so existing configurations
  work unchanged. A CI test on every PR proves the lockdown works by attempting
  to reach an unauthorized destination and asserting the failure.
```

Replace `YYYY-MM-DD` with the actual merge date. No internal infra references.

---

### Step 12 — Version bump

**Files:** `pyproject.toml`, `agent/__about__.py`
**LOC budget:** 2

**What:** Increment version from `0.1.5` to `0.1.6` in both files.

**Why:** Both files are the single source of truth for `__version__`. They must
agree.

---

## LOC summary

**REVISED** post eng-review.

| Step | File(s) | Estimated LOC |
|------|---------|---------------|
| 1 | `tinyproxy/Dockerfile` | ~15 (incl HEALTHCHECK + non-root user) |
| 2 | `tinyproxy/tinyproxy.conf.template` | ~22 |
| 3 | `tinyproxy/generate-config.sh` | ~25 (no poll loop; reads volume directly) |
| 4 | `container/extract-allowlist.sh` (NEW init script) | ~30 |
| 4b | `tinyproxy/Dockerfile` HEALTHCHECK directive | counted in 1 |
| 5 | `docker-compose.yaml` (additions: init service, proxy service, networks, volume) | ~45 |
| 6 | `tests/test_l6_egress.py` (5 cases including DNS isolation) | ~110 |
| 7 | `.github/workflows/egress-test.yml` | ~50 |
| 8 | `.github/workflows/cve-scan.yml` (add proxy image to matrix) | ~10 |
| 9-11 | `SECURITY.md`, `README.md`, `CHANGELOG.md` | LOC-exempt |
| 12 | `pyproject.toml`, `agent/__about__.py` | 2 |
| **Total** | | **~309** |

Slight overshoot vs the original 280-LOC envelope. The increase comes from:
- The new init service script + compose stanza (Option B).
- Two extra pytest cases (DNS isolation + IP-literal bypass) per P1-2.
- Tightened deny assertions per P2-6.

The 300-LOC tiny-step soft limit is a guideline. ~309 LOC across 9 files,
each file under 110 LOC, no single file dominates. Acceptable. If we hit a
hard ceiling later the test file can move to a separate follow-up PR.

## Post-merge actions (manual, one-time)

After the PR merges to main and the first `egress-test` workflow run completes
successfully, configure branch protection:

1. Go to: `https://github.com/kayaclaw/kayaclaw/settings/branches`
2. Click Edit on the `main` rule.
3. Under "Require status checks to pass before merging": toggle ON if not already.
4. In the search box, type `egress-test` and select it from the dropdown.
   (It only appears after the first successful workflow run on the feature branch.)
5. Save.

Note: GitHub exposes both the workflow-level name (`egress-test`) and the
job-level name. If only the job name is selectable, select the job name. Verify
in the UI before finalizing. This is the same ambiguity documented in the v0.1.5
plan; check which one appears and pick accordingly.

## Verification

The egress-test CI workflow IS the verification. The pen-test IS the CI test.
No separate manual step is needed beyond the mandatory negative-case proof below.

**Negative-case verification (MANDATORY before merge):**

A test that always passes is the same as no test. Prove the gate actually
blocks before merging:

1. On the feature branch, in a temporary commit, edit `generate-config.sh` to
   comment out the `FilterDefaultDeny Yes` line in the rendered tinyproxy config
   (making the proxy allow everything).
2. Push the temporary commit.
3. Observe in GitHub Actions: Test 2 (`evil.example.com` deny-side) MUST fail.
   The `curl` exits 0 when it should exit non-zero. The egress-test check must
   show red in the PR UI.
4. Revert the temporary commit. Push. Confirm all three tests pass and the check
   goes green.
5. Record both temporary-commit SHAs in the PR description as evidence.

**Local re-prove (after `docker compose up -d`):**
```bash
# Allow-side
docker exec kayaclaw-agent-1 curl -fsS -o /dev/null -I https://api.telegram.org
# Should exit 0

# Deny-side
docker exec kayaclaw-agent-1 curl -fsS -o /dev/null https://evil.example.com
# Should exit non-zero (proxy 403)

# Proxy env confirmation (AC-003)
docker exec kayaclaw-agent-1 env | grep -i proxy
# Should show HTTPS_PROXY=http://proxy:8888 and HTTP_PROXY=http://proxy:8888

# Audit the resolved allowlist
docker compose logs proxy | grep 'egress allowlist'
# Should show api.telegram.org, *.telegram.org, and the LLM provider host
```

## Risk register

Drawn from spec section 7.

| ID | Risk | Mitigation |
|----|------|------------|
| R-1 | Telegram uses an undocumented host at runtime; allowlist breaks production | Allowlist `*.telegram.org` wildcard from day 1. Audit outbound hosts before merge using `docker compose up -d && docker compose logs agent` to observe actual connection targets. If a CDN host appears, add it explicitly. |
| R-2 | tinyproxy Alpine image has unfixed CVEs that fail cve-scan | Pin to a known-clean Alpine 3.20 version; run `cve-scan` against the proxy image from day 1 (Step 8). If a CVE has no upstream fix, add a justified `.trivyignore` entry with a review-by date (same policy as the bot image). |
| R-3 | Auto-detected `base_url` host resolves incorrectly; wrong host allowed | `generate-config.sh` prints the resolved allowlist to stdout at startup; visible in `docker compose logs proxy`. The egress-test's Test 3 exercises the LLM provider path in CI. |
| R-4 | Egress test flaky in CI due to network jitter to `api.telegram.org` | Deny-side test (Test 2) uses a deterministic local sink (`evil.example.com` never reaches the internet; proxy blocks it before DNS). Allow-side test uses `--max-time 5` with retry tolerance. If still flaky, restrict the allow-side test to asserting the proxy returns something (even a 404 from Telegram) rather than asserting full HTTP 200. |
| R-5 | `HTTPS_PROXY` doesn't cover non-HTTP egress (raw sockets, DNS bypass) | `internal: true` on `agent-net` removes the external route entirely at the kernel level. Even a non-proxy-aware path cannot reach the internet. DNS is handled by Docker's embedded resolver, which is not external. |
| R-6 | Operator using a non-OpenAI-compatible provider that doesn't honor `HTTPS_PROXY` | Document in README. Current documented providers (OpenRouter, DeepInfra, Groq) all use httpx/urllib3 which honor `HTTPS_PROXY`. If an operator adds a custom provider with a bespoke HTTP client, they must configure that client manually; the README should note this. |

## Eng-review revisions (applied 2026-05-10)

`/plan-eng-review` (software-architect agent) found 5 P1 + 7 P2 + 5 P3 against
the original plan. Verdict was NEEDS REWORK. All P1s and material P2s/P3s
applied below. P3-4 (public copy nits) was already clean per voice rules.

| ID | Sev | Finding | Resolution |
|----|-----|---------|------------|
| P1-1 | P1 | Hostname extraction used `cfg['providers'][0]` but providers is a `dict`, not a list. Also missed v0.1.4 fallback chain hosts. | Step 4 rewritten as `extract-allowlist.sh` that walks all providers (dict iteration), unions every `base_url` host, plus `api.anthropic.com` for anthropic-kind providers. Multi-host allowlist field. |
| P1-2 | P1 | No test asserted `internal: true` actually blocks direct external traffic; only the proxy filter was tested. | Step 6 adds Test 4 that bypasses the proxy via `--noproxy '*'` and asserts a connection-level failure (kernel-layer drop). |
| P1-3 | P1 | Volume permission/UID issue: bot runs as UID 10001 with read-only FS; couldn't write the shared volume cleanly. | Resolved by Option B switch: init service runs as root, writes the file once to a named volume, then both bot and proxy mount it (proxy read-only). |
| P1-4 | P1 | Bot-vs-proxy ordering had a race; `depends_on: service_started` only checks container start, not proxy listening. | Switched from Option A to Option B (init service). Added HEALTHCHECK to proxy Dockerfile (`nc -z localhost 8888`); bot uses `depends_on: proxy: condition: service_healthy`. |
| P1-5 | P1 | Test relied on real-world DNS to evil.example.com. | Step 6 documents that with `internal: true` the bot cannot resolve external hosts at all; the proxy denies based on the host header in the CONNECT request, NOT on DNS. Test 2 tightened to assert proxy 403 specifically (P2-6). |
| P2-1 | P2 | tinyproxy `Filter` directive: confirm it applies to CONNECT (HTTPS) requests. | Will be verified at implementation time against tinyproxy v1.11 docs. `FilterExtended Yes` ensures regex match across both HTTP URL and CONNECT host. |
| P2-2 | P2 | Confirm httpx, python-telegram-bot, pydantic-ai's OpenAIProvider all respect `HTTPS_PROXY`. | Implementer to grep for `trust_env=False` and `proxies=None` in agent/ before commit; absent today. |
| P2-3 | P2 | CVE scan integration may rebuild bot image when scanning proxy image. | `cve-scan.yml` matrix gains proxy-image entry with conditional build (`if: matrix.target == 'proxy-image'`); existing entries unchanged. |
| P2-4 | P2 | tinyproxy `LogLevel Info` may log full URLs incl. query strings. | `tinyproxy.conf.template` set to `LogLevel Notice` to suppress per-request URL logging by default. |
| P2-5 | P2 | Telegram wildcard syntax — `*.telegram.org` vs `\.telegram\.org$`. | Pick `FilterExtended Yes` regex form; comment in conf template documents the choice. |
| P2-6 | P2 | Deny test asserts only "non-zero exit"; could pass on unrelated DNS/timeout failures. | Test 2 now asserts `b"403" in result.stdout` via `curl -w '%{http_code}'`. |
| P2-7 | P2 | No bot healthcheck. | Out of scope: bot is a polling Telegram client, no HTTP server to healthcheck. The proxy healthcheck is sufficient for `depends_on: condition: service_healthy` ordering. |
| P3-1 | P3 | Resource limits on proxy. | Added `cpus: 0.25 mem_limit: 64m pids_limit: 32` to proxy service. |
| P3-2 | P3 | tinyproxy should run non-root. | `tinyproxy/Dockerfile` adds dedicated `tinyproxy` user; matches bot's UID 10001 discipline. |
| P3-3 | P3 | LOC budget reality check (~265 expected). | Updated LOC summary; total now ~309, slight overshoot accepted with rationale. |
| P3-4 | P3 | Public copy nits — already clean. | No change. |
| P3-5 | P3 | Branch-protection name doc — check both workflow-level and job-level. | SECURITY.md update mentions both names. |

### Pass 2 findings (after first revision, applied 2026-05-10)

A second `/plan-eng-review` pass on the revised plan caught 1 NEW-P1 + 3 NEW-P2 + 2 NEW-P3 introduced by the Option-A-to-Option-B switch.

| ID | Sev | Finding | Resolution |
|----|-----|---------|------------|
| NEW-P1 | P1 | Step 3 (`generate-config.sh`) still described the rejected Option-A poll loop and used singular `LLM_PROVIDER_HOST` while Step 4 emits plural `LLM_PROVIDER_HOSTS`. Single-line filter rendering would silently break multi-provider/fallback. | Step 3 rewritten to read `LLM_PROVIDER_HOSTS`, iterate, emit one anchored regex per host. Poll loop removed (init guarantees file exists). |
| NEW-P2 | P2 | `FilterExtended Yes` regex form was implied but filter-file syntax not specified. Plain `api.telegram.org` matches dots as any char (under-restrictive). | Step 3 emits anchored regex form: `(^|\.)telegram\.org$` and `^<host>$` with escaped dots. Documented inline. |
| NEW-P2 | P2 | Init service bind-mounted `extract-allowlist.sh` from host; cwd-fragile. | Script COPY'd into bot image at `/usr/local/bin/extract-allowlist.sh` in Dockerfile. Init service uses image-only path. |
| NEW-P2 | P2 | Proxy `tmpfs: /run` masks `/run/shared` named volume mount. Order-dependent. | Tmpfs moved to `/etc/tinyproxy` where rendered conf lives. `/run/shared` volume mount unaffected. |
| NEW-P3 | P3 | Proxy HEALTHCHECK retries=15 too lenient under Option B (init guarantees ordering). | Tighten to retries=5 in implementation. |
| NEW-P3 | P3 | P2-2 (httpx/PTB HTTPS_PROXY honoring) deferred to runtime grep. | Implementer to confirm at code-implementer step; documented in Step 0 verification gates. |

## Out of plan (will not change in this PR)

- seccomp profile (control 8) — deferred.
- AppArmor / SELinux (control 9) — deferred.
- Per-agent network isolation (control 20) — deferred.
- Egress logging to a SIEM — local container logs only.
- Tool-use surface for the LLM — no tool-call code in v0.1.6.
- Outbound DNS filtering — Docker embedded DNS unchanged.
- SARIF upload to GitHub Security tab — not in scope.
- No CLAUDE.md creation or modification anywhere in the repo.

## Verification gates (per SDLC)

Before this plan goes to code:
- [ ] /plan-eng-review on this file (MANDATORY before code-implementer starts)

Before commit:
- [ ] /simplify on the staged diff (workflow YAML and shell scripts are common sources of bloat)
- [ ] /codex-review on the staged diff (security + new public surface; cannot skip)

Before merge:
- [ ] PR opened; `egress-test` and updated `cve-scan` checks green
- [ ] Negative-case verification done; temporary-commit SHAs recorded in PR description
- [ ] Required-status-check name verified in branch protection UI dropdown
- [ ] End-to-end verification via test bot (runtime path is touched: proxy env vars, entrypoint changes)
- [ ] Public copy (README, CHANGELOG, SECURITY.md edits) reviewed and approved before push

After merge:
- [ ] Branch protection updated with `egress-test` required check (see Post-merge actions)
- [ ] Tag v0.1.6
- [ ] GitHub Release with the CHANGELOG body
- [ ] Daily ship post draft (pending per-piece approval)
