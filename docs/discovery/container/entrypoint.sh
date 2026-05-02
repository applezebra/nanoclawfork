#!/bin/sh
# Minimal entrypoint. Initializes the data dir and execs the agent.
# No shell features used — keeps the attack surface small.

set -eu

# Sanity-check writable data dir (the only writable path).
if [ ! -w "${AGENT_DATA_DIR:-/data}" ]; then
    echo "FATAL: AGENT_DATA_DIR (${AGENT_DATA_DIR:-/data}) is not writable" >&2
    exit 1
fi

# Sanity-check config presence (read-only mount).
if [ ! -r "/config/config.yaml" ]; then
    echo "FATAL: /config/config.yaml not mounted read-only" >&2
    exit 1
fi

exec agent "$@"
