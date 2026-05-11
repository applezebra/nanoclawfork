#!/bin/sh
# extract-allowlist.sh: one-shot init that walks every configured provider
# in config.yaml and writes the union of hostnames to a shared volume that
# the proxy mounts read-only. Runs as root inside the init service so it
# can write the named volume cleanly. Fail-closed: empty / unparseable
# config -> exit 1, init service fails, bot and proxy do not start.
set -eu

OUT_DIR=/run/shared
OUT_FILE="$OUT_DIR/egress-allowlist.env"
mkdir -p "$OUT_DIR"

HOSTS=$(python3 - <<'PY'
import sys
import yaml
from urllib.parse import urlparse

try:
    with open('/config/config.yaml', 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
except Exception as e:
    sys.exit(f"extract-allowlist: cannot parse config.yaml: {e}")

hosts = set()
for name, p in (cfg.get('providers') or {}).items():
    p = p or {}
    bu = p.get('base_url')
    if bu:
        h = urlparse(bu).hostname
        if h:
            hosts.add(h)
    # anthropic kind has a fixed host even if base_url is absent
    if p.get('kind') == 'anthropic':
        hosts.add('api.anthropic.com')

if not hosts:
    sys.exit("extract-allowlist: no provider base_url found")

print(' '.join(sorted(hosts)))
PY
)

# HOSTS is now space-separated, e.g. "api.deepinfra.com openrouter.ai"
printf 'LLM_PROVIDER_HOSTS=%s\n' "$HOSTS" > "$OUT_FILE"
chmod 644 "$OUT_FILE"

printf 'extract-allowlist: resolved LLM provider hosts: %s\n' "$HOSTS"
