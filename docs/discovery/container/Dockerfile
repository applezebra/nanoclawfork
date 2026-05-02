# Hardened container — matches NanoClaw's posture.
# See container/SECURITY.md for the full control checklist.

FROM python:3.12-slim AS base

# --- Build stage (separate so build tools never ship in runtime) ---
FROM base AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml /build/
COPY agent /build/agent
RUN pip install --no-cache-dir --prefix=/install .

# --- Runtime stage — minimal, non-root, read-only-friendly ---
FROM base AS runtime

# Non-root user. UID 10001 is far enough from system UIDs to be unambiguous.
RUN groupadd --system --gid 10001 agent \
 && useradd  --system --uid 10001 --gid 10001 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin agent

# Only the installed package + entrypoint. No source, no build tools.
COPY --from=build /install /usr/local
COPY container/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0555 /usr/local/bin/entrypoint.sh

# Writable data path — must be mounted as a tmpfs or named volume.
# Root filesystem is otherwise read-only (enforced via compose).
ENV AGENT_DATA_DIR=/data
RUN mkdir -p /data && chown agent:agent /data

USER agent
WORKDIR /data

# No shell on PID 1 — exec form, signal-clean.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
