#!/bin/sh
# POSIX sh only. set -eu = fail on errors, fail on unset vars (SECURITY.md
# control 18). Two sanity checks, then exec into the Python entry point so
# PID 1 receives signals directly. No sourcing, no scripts, no bash-isms.
set -eu

DATA_DIR="${AGENT_DATA_DIR:-/data}"
CONFIG_FILE="/config/config.yaml"

if [ ! -d "$DATA_DIR" ] || [ ! -w "$DATA_DIR" ]; then
    printf 'entrypoint: AGENT_DATA_DIR (%s) is not a writable directory\n' "$DATA_DIR" >&2
    exit 1
fi

if [ ! -f "$CONFIG_FILE" ] || [ ! -r "$CONFIG_FILE" ]; then
    # -f rejects directories, FIFOs, devices (codex-review L5-Step2 P2);
    # -r confirms readability. Both: it must be a real readable regular file.
    printf 'entrypoint: %s is not a readable regular file; mount config.yaml read-only into /config/\n' "$CONFIG_FILE" >&2
    exit 1
fi

exec agent "$@"
