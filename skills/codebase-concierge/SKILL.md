---
name: codebase-concierge
description: Answer natural-language questions about your codebase via email. Routes inbound mail through Nia (codebase context) + Claude (reasoning) and replies in-thread with cited file:line references. Different modes for engineering, sales, marketing, and support contexts.
metadata: openclaw
emoji: 📨
requires:
  - nia-api-key
  - agentmail-api-key
  - anthropic-api-key
kind: service
---

# Codebase Concierge

> Your codebase is the source of truth for what your product actually does — but only engineers can read it. This skill turns it into an inbox the whole company can email.

## What it does
- Receives email → classifies role/mode → queries Nia for code context → composes cited answer → threaded reply via AgentMail
- Auto-CCs the engineer who last touched cited files (via `git blame`)
- Modes: `eng` (default), `sales`, `marketing`, `support` — each with a tuned system prompt

## Entry points (channel-agnostic)
The reasoning core lives in `core.py`. Email is just one channel; OpenClaw / CLI / Slack call the same brain.

**HTTP** — `POST /skill/ask`
```bash
curl -X POST https://codebase-concierge.onrender.com/skill/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"How does Hono handle middleware composition?"}'
```
Returns `{answer_html, answer_md, sources, engineers}`.

**Python** — `core.answer_codebase_question(question, thread_history=None) -> dict`
```python
from core import answer_codebase_question
result = await answer_codebase_question("How does X work?")
print(result["answer_md"])         # raw markdown answer
print(result["sources"])           # list of file paths / doc URLs
print(result["engineers"])         # [{name, email, date, source}, ...]
```

`thread_history` is an optional list of prior messages (each `{from_, text|preview}`); pass it for follow-up context.

## Setup
1. Get API keys: trynia.ai, agentmail.to, console.anthropic.com
2. Index your repos via the Nia plugin or CLI; capture repo IDs
3. Create an AgentMail inbox; capture inbox ID
4. Set env vars: `NIA_API_KEY`, `AGENTMAIL_API_KEY`, `ANTHROPIC_API_KEY`, `AGENTMAIL_INBOX_ID`, `NIA_REPO_IDS`
5. Deploy `main.py` (FastAPI) anywhere with a public URL; register webhook URL with AgentMail

## Modes
| Mode | Trigger | System prompt focus |
|------|---------|---------------------|
| `eng` | default | File:line citations, code-level answers |
| `sales` | sender domain or `[sales]` subject | Capability answers, no code, customer-safe phrasing |
| `marketing` | `[marketing]` subject | What shipped, feature angles, plain language |
| `support` | `[support]` subject | Bug-vs-feature triage, links to related tickets |

## Built for OpenClaw Hackathon (Eragon × Nozomio × AgentMail).
