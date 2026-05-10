#!/bin/sh
# generate-config.sh — render tinyproxy.conf + filter file from the env
# file written by the init service, then exec tinyproxy. POSIX sh only.
#
# Under Option B the init service has already written the env file before
# this proxy container starts (compose `depends_on: init: condition:
# service_completed_successfully`). Missing file or empty value is a hard
# error: the operator must see it and fix the config.
set -eu

SHARED_ENV="/run/shared/egress-allowlist.env"
[ -f "$SHARED_ENV" ] || {
    printf 'generate-config: %s missing; init service did not run\n' "$SHARED_ENV" >&2
    exit 1
}
# shellcheck disable=SC1090
. "$SHARED_ENV"
[ -n "${LLM_PROVIDER_HOSTS:-}" ] || {
    printf 'generate-config: LLM_PROVIDER_HOSTS empty in %s\n' "$SHARED_ENV" >&2
    exit 1
}

FILTER_FILE=/etc/tinyproxy/filter.txt
{
    # Telegram allowlist: matches api.telegram.org and any subdomain
    # (anchored). Future Telegram CDN paths (file-server, media) are
    # covered without a config change.
    printf '(^|\\.)telegram\\.org$\n'
    # Each LLM provider host is anchored as a literal regex (dots escaped).
    for host in $LLM_PROVIDER_HOSTS; do
        escaped=$(printf '%s' "$host" | sed 's/\./\\./g')
        printf '^%s$\n' "$escaped"
    done
} > "$FILTER_FILE"

printf 'generate-config: resolved allowlist:\n' >&2
sed 's/^/  /' "$FILTER_FILE" >&2

# Render the conf template by substituting the filter file path. Template
# lives under /usr/local/share so the /etc/tinyproxy tmpfs mount does not
# hide it (codex-review v0.1.6 P1).
sed "s|__FILTER_FILE__|$FILTER_FILE|g" \
    /usr/local/share/tinyproxy/tinyproxy.conf.template \
    > /etc/tinyproxy/tinyproxy.conf

# Foreground (-d), explicit config path (-c). PID 1 inside the container.
exec tinyproxy -d -c /etc/tinyproxy/tinyproxy.conf
