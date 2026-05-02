# Prototype — Discovery Artifact

This directory contains scaffolding written **before** the SDLC was followed.
It is preserved for reference only. Do not extend it directly.

The real build will follow the standard sequence:

1. spec-analyzer → spec-writer → `/docs/spec/`
2. impact-analyzer → plan-writer → `/docs/impl/`
3. `/plan-eng-review` (mandatory gate)
4. code-implementer in Tiny Steps (≤300 LOC, ≤3 files per step)
5. `/codex-review` per commit
6. test-runner → code-reviewer → security-auditor → git-steward

Use the scaffold to inform the spec. Do not lift code from it without
running it through the proper review gates first.
