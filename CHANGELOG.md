# Changelog

All notable changes to kayaclaw are documented in this file. Format follows Keep a Changelog (https://keepachangelog.com/en/1.1.0/). This project adheres to Semantic Versioning (https://semver.org/spec/v2.0.0.html).

## [0.1.5] - 2026-05-09

### Added
- kayaclaw's CI now blocks merges if a known high-severity security hole is found in any library, container layer, or Docker setting kayaclaw uses. The scan runs on every change and once a week against the latest CVE database, so a release tag means the build was clean against public CVEs at the time it shipped.

## [0.1.4] - 2026-05-08

### Added
- Optional `fallback:` config field. List up to 5 `<provider>/<model-id>` alternates tried in order when the primary call fails. Duplicates and primary-model cycles are rejected at config load. On full exhaustion, the bot replies with a user-visible status message and the failure summary is logged. Empty or omitted keeps prior behaviour.

## [0.1.3] - 2026-05-07

### Added
- Startup validation: if a configured provider's `api_key_env` is missing or empty in the environment, the agent now exits at boot with a clear error naming the missing variable and the provider that referenced it. Previously the agent would boot successfully and only fail at the first user message.

### Changed
- Quick start section rewritten for clarity. Three setup steps split out, fewer pasted commands, log-tailing is now a debugging path rather than a verification step.
- Default example provider switched from DeepInfra to OpenRouter. OpenRouter is itself a meta-provider (one key gets you Llama, Claude, Gemini, and more), which is a more impartial starting point. DeepInfra and Groq remain documented as alternatives.
- `pyproject.toml` description rewritten to drop legacy framing.

## [0.1.2] - 2026-05-06

### Added
- Anthropic Claude (sonnet-4.5) and Google Gemini (2.0 Flash) examples via OpenRouter in the README.

### Changed
- Reframed the "Switching providers" section to make explicit that any model the provider serves works, not just the ones shown.

## [0.1.1] - 2026-05-06

### Added
- Verified OpenRouter and Groq provider snippets in README and config.example.yaml. One config line, swap your LLM. ([#6](https://github.com/kayaclaw/kayaclaw/pull/6))

## [0.1.0]

Initial public release. See repository history.
