# syntax=docker/dockerfile:1
# Egress proxy sidecar for kayaclaw. Forwards bot HTTP(S) traffic to a
# small allowlist (Telegram + the configured LLM provider hosts).
# Hostname allowlist is rendered at start by generate-config.sh from
# /run/shared/egress-allowlist.env (written by the init service).
FROM alpine:3.20

# netcat-openbsd: alpine's busybox `nc` lacks `-z` (port-probe mode), and
# a healthcheck that always exits non-zero is worse than no healthcheck.
RUN apk add --no-cache tinyproxy ca-certificates netcat-openbsd

# Non-root proxy user (UID 10001 mirrors the bot's pattern).
RUN addgroup -S -g 10001 proxy \
 && adduser -S -u 10001 -G proxy -H -h /nonexistent -s /sbin/nologin proxy

# Template lives under /usr/local/share, outside the /etc/tinyproxy tmpfs
# target that compose mounts (rendered conf lives there at runtime).
COPY tinyproxy.conf.template /usr/local/share/tinyproxy/tinyproxy.conf.template
COPY generate-config.sh /usr/local/bin/generate-config.sh
RUN chmod 0555 /usr/local/bin/generate-config.sh

USER proxy

# Healthcheck: ready as soon as tinyproxy binds 8888. The bot's
# `depends_on: proxy: condition: service_healthy` waits on this.
HEALTHCHECK --interval=2s --timeout=2s --retries=5 --start-period=2s \
  CMD nc -z localhost 8888 || exit 1

ENTRYPOINT ["/usr/local/bin/generate-config.sh"]
