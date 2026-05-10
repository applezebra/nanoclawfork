# syntax=docker/dockerfile:1
# Egress proxy sidecar for kayaclaw. Forwards bot HTTP(S) traffic to a
# small allowlist (Telegram + the configured LLM provider hosts).
# Hostname allowlist is rendered at start by generate-config.sh from
# /run/shared/egress-allowlist.env (written by the init service).
FROM alpine:3.20

# tinyproxy from the Alpine community repo. ca-certificates are needed
# for the proxy to validate upstream TLS when forwarding HTTPS CONNECT.
# netcat-openbsd is for the healthcheck — alpine's busybox `nc` does not
# support `-z` (port-probe mode), and a healthcheck that always exits
# non-zero is worse than no healthcheck at all.
RUN apk add --no-cache tinyproxy ca-certificates netcat-openbsd

# Run as a dedicated non-root user (SECURITY.md discipline mirrors the
# bot's UID 10001 pattern). tinyproxy needs write access to its config
# dir at start (rendered conf + filter file land there); /etc/tinyproxy
# is mounted as a writable tmpfs in compose, so the template MUST live
# outside that path or the tmpfs would mask the baked-in file.
RUN addgroup -S -g 10001 proxy \
 && adduser -S -u 10001 -G proxy -H -h /nonexistent -s /sbin/nologin proxy

# Template lives under /usr/local/share — outside the tmpfs target. If it
# were under /etc/tinyproxy, the compose tmpfs mount would hide it and
# generate-config.sh would fail at first sed. (codex-review v0.1.6 P1.)
COPY tinyproxy.conf.template /usr/local/share/tinyproxy/tinyproxy.conf.template
COPY generate-config.sh /usr/local/bin/generate-config.sh
RUN chmod 0555 /usr/local/bin/generate-config.sh

USER proxy

# Healthcheck: ready as soon as tinyproxy binds 8888. The bot's
# `depends_on: proxy: condition: service_healthy` waits on this.
HEALTHCHECK --interval=2s --timeout=2s --retries=5 --start-period=2s \
  CMD nc -z localhost 8888 || exit 1

ENTRYPOINT ["/usr/local/bin/generate-config.sh"]
