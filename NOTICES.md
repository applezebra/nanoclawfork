# NOTICES

## License

This project is released under the MIT License.

```
MIT License

Copyright (c) 2026 Anson Zeall

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## Third-party Python dependencies

Top-level runtime dependencies declared in `pyproject.toml`. Versions are pinned
to a single resolved version because license terms can change across major
versions; pinning the version pins the license.

| Package | Version | License | Project |
|---|---|---|---|
| `pydantic` | 2.13.3 | MIT | [pydantic/pydantic](https://github.com/pydantic/pydantic) |
| `pydantic-ai-slim[openai]` | 1.89.0 | MIT | [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai) |
| `python-telegram-bot` | 22.7 | LGPL-3.0 (with [exception for examples and docs](https://docs.python-telegram-bot.org/en/v22.7/index.html#license)) | [python-telegram-bot/python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) |
| `PyYAML` | 6.0.3 | MIT | [yaml/pyyaml](https://github.com/yaml/pyyaml) |

### Notable transitive dependencies

- **`openai` (Python SDK)** — pulled in transitively by `pydantic-ai-slim[openai]`.
  Apache-2.0 license. Used by the runtime to talk to OpenAI-compatible providers
  (DeepInfra, etc.) via the OpenAI HTTP API. Pinned indirectly to whatever
  `pydantic-ai-slim==1.89.0` resolves at build time — see the locked image.
- **`anthropic` (Python SDK)** — **explicitly absent.** The deliberate choice
  of `pydantic-ai-slim[openai]` over the `pydantic-ai` meta-package is what
  keeps the Anthropic SDK out of the runtime container (per NFR-AG / AC-4).
  Verify in a built container with: `docker run --rm kayaclaw-agent pip show
  anthropic` — exits non-zero.

### Standard library

The following are part of the Python 3.12 standard library and are governed by
the [PSF License](https://docs.python.org/3/license.html). Listed here for
completeness because they appear in the dependency graph readers might audit:

- `sqlite3` — used by `agent/memory.py` for per-chat conversation history.
  No third-party SQLite wrapper is taken as a top-level dependency
  (per DECISIONS.md D2).
- `asyncio`, `logging`, `pathlib`, `os`, `threading`, `re`, `json`, `typing`.

---

## Design references (no code lifted)

**NanoClaw (`qwibitai/nanoclaw`)** — MIT licensed — was consulted as a design
reference during the connector audit phase (May 2026). The audit reviewed
NanoClaw's TypeScript Telegram connector for security patterns and
architectural ideas. The review notes are checked in at
[`docs/spec/CONNECTOR-AUDIT-telegram.md`](docs/spec/CONNECTOR-AUDIT-telegram.md).

**No code was lifted from NanoClaw.** The CONNECTOR-AUDIT verdict is
"DO NOT LIFT" — the language mismatch (TypeScript vs Python) makes a direct
lift impractical, and the Python connector was written fresh against
`python-telegram-bot`. Security requirements derived from the audit are
incorporated as design requirements for this project's connector lane.

The container hardening posture in `docs/discovery/container/SECURITY.md` was
informed by NanoClaw's published Docker setup, but every control listed there
was independently re-derived against the Docker Engine documentation and
verified with a runnable `docker inspect` or `docker run` command. There is no
direct copy of NanoClaw's Dockerfile, Compose file, or container scripts.

NanoClaw is copyright its respective contributors and is separately licensed.
This project contains no NanoClaw source code.

### Verifying no code was lifted

To verify independently that the `agent/` tree was written from scratch with
no upstream import history:

```bash
git log --all --full-history -- agent/
```

Every commit author should be `Anson Zeall` and every parent reachable from
the initial commit should be a fresh-repo commit, not a fork point from
qwibitai/nanoclaw.

---

## Visual assets

The project's brand artwork was generated with AI image-generation tools, then
post-processed with ImageMagick. Disclosed here in keeping with the
project's brand commitment to transparency.

- `assets/kayaclaw-logo.png` — chili-crab-and-kaya-toast mascot. Generated
  with Google Gemini Nano Banana (`gemini-3-pro-image-preview`), May 2026.
  Background knocked out to a true alpha channel via ImageMagick
  (`-fuzz 8% -transparent white`).
- `assets/kayaclaw-social-preview.jpg` — 1280x640 Singapore-beach social
  preview banner (chili crab on a beach towel with a laptop, three
  unbranded AI mascot characters, Marina Bay Sands silhouette in the
  distance). Same generation method as the logo, then center-cropped from
  1376x768 to a 2:1 social-preview spec.

The image-generation prompts that produced these assets are reproducible;
ask in `Discussions` if you want them.

---

## Reporting an issue with this attribution

If you believe a dependency is missing, mis-licensed, or that this project
contains code lifted from another source, please open an issue or contact
`security@kayaclaw.ai`. We treat attribution accuracy as a release-blocking
defect.
