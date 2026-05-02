# Branding & Renaming

Working name: **NanoClaw Fork** (`nanoclawfork`).

This name is intentionally placeholder. The codebase is structured so the
final brand name only lives in **two** places. Renaming is a find/replace,
not a refactor.

## Where the brand name lives

| File | Field | Why it's here |
|------|-------|---------------|
| `pyproject.toml` | `[project].name` | Python package name (PyPI publish identity) |
| `agent/__about__.py` | `__brand__`, `__slug__` | Runtime display name (logs, UA strings, banners) |

## Where the brand name does **NOT** live

- **Import paths** — internal package is `agent/`, deliberately generic. No `import nanoclawfork`.
- **Env var names** — prefixed `AGENT_*`, not the brand. Stable across renames.
- **Container service names** — `docker-compose.yaml` uses `agent` and `router`, not the brand.
- **Config keys** — `config.yaml` uses neutral keys (`providers`, `agents`, `connectors`).
- **Database file names / table names** — `agent.db`, not branded.

## To rename

1. Pick the new name (slug + display).
2. Edit `pyproject.toml` → `[project].name`
3. Edit `agent/__about__.py` → `__brand__`, `__slug__`
4. Edit `README.md` headline (cosmetic)
5. Optional: rename the git repo and the working directory.

That's it. No code changes required.

## Why this matters

NanoClaw and OpenClaw both bake their brand into module paths, env vars,
and config schemas. That's why forking either of them is painful — the
name is everywhere. We're not doing that.
