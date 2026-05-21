# SPEC: v0.1.8 : provider-error clarity in init

**Status:** Draft
**Owner:** Anson
**Author:** Hao (spec-writer)
**Date:** 2026-05-20
**Carries over from:** v0.1.7 init smoke (Wei) where Azentiq's OpenRouter key returned 404 due to account guardrails and init's "Unexpected response" message stranded the tester.

## 1. Why this exists

`python -m agent init` validates the provider API key against the OpenAI-compatible `/models` endpoint before writing config. Today, anything other than 200/401/403 returns a generic message:

> Provider returned HTTP {status} from {url}. Unexpected response.

That hides the actual cause from the user. Wei's smoke caught this: his key was technically valid but the OpenRouter account had account-level guardrails returning 404 on every endpoint. The init UX gave him no actionable next step. The fix took an unrelated DM ("create a new key with no guardrails") because the validator told him nothing useful.

Real self-hosters will hit the same dead-end on: rate-limited keys (429), no-credit accounts (402), wrong base_url (404), suspended accounts (403 with body), partial outages (5xx), DNS issues (network). Each currently fails opaquely.

## 2. Scope

In-scope:
- Better error surfacing on provider key validation failure.
- Map common HTTP statuses to one-sentence actionable guidance.
- Show the response body's first 200 chars (verbatim) so the user can copy-paste the upstream error if asking for help.
- Retry-after-fix loop so the user can rerun the validator without restarting the whole init flow.
- Apply the same treatment to Telegram token validation (parallel UX problem, smaller blast radius).

Out-of-scope:
- Auto-creating provider accounts.
- Heuristic provider switching (e.g. "your DeepInfra key looks invalid, want to try OpenRouter?"). That's a v0.2.x conversation.
- Changing the validator's transport (still urllib, no httpx).
- Changes outside the `init` flow.

## 3. Personas

| Who | What they hit today | What they hit after v0.1.8 |
|-----|---------------------|----------------------------|
| First-time self-hoster with a no-credit OpenRouter key | "Unexpected response" | "Your account has no usage credit. Top it up at openrouter.ai/credits, then press Enter to retry." |
| Self-hoster with account guardrails (Azentiq case) | "Unexpected response" | "Provider returned 404 on /models : your API key works but has restricted endpoints. Issue a new key with all endpoints enabled, then press Enter to retry." |
| Self-hoster mistyped base_url | "Unexpected response" | "Provider returned 404 on /models : check the base_url is correct (commonly https://openrouter.ai/api/v1)." |
| Self-hoster on a flaky network | "Could not reach ..." | Same as today, plus retry loop. |

## 4. User scenarios

### 4.1 Provider key validation fails

1. User pastes their API key.
2. Init posts to `{base_url}/models`.
3. Validator returns a structured failure with: status code, endpoint, body snippet.
4. Init renders:
   - One-line headline mapped from status (see §5).
   - Status code + endpoint hit (so the user can verify).
   - First 200 chars of the response body, indented.
   - "Press Enter to retry after you've fixed it, or Ctrl-C to abort."
5. User fixes the underlying issue (tops up credit, re-issues key, etc).
6. User presses Enter; init re-runs validation with the same input. No re-typing.
7. On success, init moves on.

### 4.2 Telegram token validation fails

Same shape as §4.1 but for `api.telegram.org/bot<TOKEN>/getMe`. Statuses simpler (401/404 = bad token, 5xx = Telegram outage, network = local).

### 4.3 User wants to start over

Ctrl-C exits cleanly. No state written. Re-running `python -m agent init` starts from the top.

## 5. Status-to-guidance mapping

The validator returns the status; init renders a one-liner. Mapping (provider):

| HTTP | Headline |
|------|----------|
| 401 | "Provider rejected the key. Double-check it was copied correctly (no extra spaces)." |
| 402 | "Your provider account has no usage credit. Top up and retry." |
| 403 | "Provider refused this key. The account may be suspended or the key lacks permission for /models." |
| 404 | "Provider returned 404 on /models. Likely causes: wrong base_url, or the key has restricted endpoints (issue a new key with all endpoints enabled)." |
| 429 | "Provider is rate-limiting the key. Wait a minute and retry." |
| 5xx | "Provider responded with a server error. Wait a moment and retry; if it persists, check the provider's status page." |
| network | "Could not reach {base_url}. Check your network and DNS." |
| other | "Provider returned HTTP {status}. Body snippet below." |

Mapping (Telegram):

| HTTP | Headline |
|------|----------|
| 401, 404 | "Telegram rejected the token. Check it was copied correctly from BotFather." |
| 429 | "Telegram is rate-limiting this bot. Wait a minute and retry." |
| 5xx | "Telegram returned a server error. Retry in a moment." |
| network | "Could not reach api.telegram.org. Check your network." |
| other | "Telegram returned HTTP {status}. Body snippet below." |

## 6. Functional requirements

- F1. `validate_provider_key` returns enough information for the renderer to map a status: keep `ok`, `error`, and add `status_code: int | None`, `endpoint: str | None`, `body_snippet: str | None`.
- F2. `validate_telegram_token` mirrors F1 with the same shape extension.
- F3. CLI renders the headline + diagnostics block in the same style as existing init prompts (no colour libs).
- F4. CLI offers retry-after-fix without forcing user to re-enter the key (the value stays in memory until success or abort).
- F5. Body snippet is truncated to 200 chars and rendered indented (4 spaces) for easy copy-paste.
- F6. Body snippet is scrubbed of common secret patterns before rendering (Bearer tokens, sk-* keys, the user's own pasted key) : defensive against providers that echo the request back in error bodies.

## 7. Non-functional requirements

- N1. No new runtime dependencies.
- N2. Init cold-start time unchanged (validators are already lazy-loaded).
- N3. Tests added for every status branch and the secret-scrub path.
- N4. Backwards-compatible: existing `ValidationResult` consumers keep working (extend, don't break).

## 8. Acceptance criteria

- AC1. Posting Wei's original guardrails-OpenRouter key in init shows the "404 on /models : issue a new key with all endpoints enabled" guidance (verified manually against a real OpenRouter restricted key, or with a mocked 404).
- AC2. Posting an empty-credit key shows the 402 message.
- AC3. Posting a deliberately bad key shows the 401 message.
- AC4. After any failure, pressing Enter re-runs the validator on the SAME stored value. No re-prompting.
- AC5. Pressing Ctrl-C at the retry prompt exits cleanly with no partial files written.
- AC6. Body snippets containing Bearer tokens / sk- keys are redacted.
- AC7. Unit tests cover each status branch + the retry loop + the secret-scrubber.

## 9. Out of scope (deferred)

- Auto-detecting and pre-validating the base_url shape.
- Suggesting alternative providers based on the key prefix.
- Persistent error history (init has no telemetry).

## 10. Open questions for plan-writer

- Where does the secret-scrubber live? Inherit Mira's pattern or build inline? (Recommend: inline minimal function in validators.py; do NOT pull Mira in.)
- Should retry loop have a max-retry cap? (Recommend: no cap, just allow Ctrl-C; humans are at the keyboard.)
- Telegram 429 handling shares code with provider 429 : refactor to a shared mapper, or keep per-validator? (Recommend: per-validator dict, deduped only if it becomes noisy.)
