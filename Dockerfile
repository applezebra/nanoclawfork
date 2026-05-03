# syntax=docker/dockerfile:1
# Multi-stage build: build stage holds gcc/pip; runtime stage carries
# only the installed package + a non-root user. SECURITY.md controls
# 1 (non-root), 16 (minimal base), 17 (no build tools in runtime).

FROM python:3.12-slim AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY pyproject.toml ./
COPY agent/ ./agent/
# --prefix isolates the install tree so only /install (no apt cache, no
# build-essential) gets carried into the runtime stage.
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim AS runtime
# UID 10001 per IMPACT-ANALYSIS §L5 contract. --no-create-home + nologin
# shell + /nonexistent home dir hardens the account against shell access
# even if a future container exec attempt happens.
RUN groupadd --system --gid 10001 agent \
 && useradd --system --uid 10001 --gid 10001 \
    --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin agent
COPY --from=build /install /usr/local
# Strip pip from the runtime stage. python:3.12-slim ships with pip in
# /usr/local; SECURITY.md control 17 says no build tools in runtime, and
# pip-as-installer counts. Without this, a compromised process could
# write to site-packages despite read-only root FS being enabled (pip
# would still try, and any future relaxation of read-only would be a
# silent uplift). Discovered during L5 Step 2 acceptance testing.
RUN rm -rf /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12 \
           /usr/local/lib/python3.12/site-packages/pip \
           /usr/local/lib/python3.12/site-packages/pip-*.dist-info \
           /usr/local/lib/python3.12/ensurepip
# ensurepip ships bundled pip + setuptools wheels; without removing it,
# `python -m ensurepip --upgrade` restores pip in one command and silently
# voids control 17 (codex-review L5-Step2 P1).
COPY container/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0555 /usr/local/bin/entrypoint.sh \
 && mkdir -p /data /config \
 && chown agent:agent /data
# HOME=/tmp redirects ~/.cache writes onto the tmpfs mount (compose Step 3).
# PYTHONDONTWRITEBYTECODE=1 closes the __pycache__ footgun under read-only
# root FS — without it, every import would try to write next to .py files
# and fail (eng-review P2-6).
ENV AGENT_DATA_DIR=/data \
    HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER agent
WORKDIR /data
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
