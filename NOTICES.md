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

## Third-party attributions

_(This section will be populated in L6 with full runtime dependency attribution,
including licenses for pydantic, pydantic-ai, python-telegram-bot, PyYAML, and
any transitive dependencies.)_

---

## Design references

**NanoClaw (`qwibitai/nanoclaw`)** was consulted as a design reference during
the connector audit phase (May 2026). The audit reviewed NanoClaw's TypeScript
Telegram connector for security patterns and architectural ideas.

**No code was lifted from NanoClaw.** The CONNECTOR-AUDIT verdict (see
`docs/spec/CONNECTOR-AUDIT-telegram.md`) is "DO NOT LIFT" — the language
mismatch (TypeScript vs Python) makes a direct lift impractical, and the
Python connector was written fresh against `python-telegram-bot`. Security
requirements derived from the audit are incorporated as design requirements
for this project's connector lane.

NanoClaw is copyright its respective contributors and is separately licensed.
This project contains no NanoClaw source code.
