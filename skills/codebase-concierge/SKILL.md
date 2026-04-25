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
