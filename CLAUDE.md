# Codebase Concierge — Hackathon Build

## What this is
An email-driven agent that answers natural-language questions about a codebase.
The codebase is the source of truth for what a product *actually* does, but only
engineers can read it. This agent translates it for everyone else via email.

**Hackathon:** OpenClaw — Eragon × Nozomio × AgentMail. Solo build.
**Theme:** "Build agents that act in the world. Give your agent a brain. Give it a voice."
- Brain = Nia (codebase context)
- Voice = AgentMail (programmable inbox)

## Vision (the pitch — bigger than the MVP)
One inbox, multiple modes — same brain, different voice depending on the asker:
- **Engineering mode** (MVP — ships today): "How does X work?" → cited file:line answer
- **Sales mode** (stretch): "Does the product support SSO with Okta?" → capability answer grounded in actual code, not stale marketing copy
- **Marketing mode** (stretch): "What shipped this sprint worth a blog post?" → feature-grounded content angles from real diffs
- **Support mode** (stretch): "Customer says X is broken — is it a bug or expected?" → behavior answer from code

Mode is detected from sender domain / subject prefix / explicit `[sales]` tag.
Each mode = different system prompt + different Nia search strategy.

**Strategy: ship engineering mode rock-solid. Tease the others on the architecture slide
and in Q&A. Don't half-build sales/marketing modes — broken modes hurt more than absent ones.**

## OpenClaw alignment (the meta-pitch)
The hackathon is hosted by Eragon (enterprise OpenClaw). OpenClaw's thesis: **personal AI
agents that live in the messaging platforms you already use**. 100k+ GitHub stars in week one.
Eragon's tagline: *"see all of it, connect all of it, act on all of it."*

Our build IS this thesis applied to the codebase:
- **Lives in messaging** (email) — not yet another dashboard
- **Acts in the world** — sends real replies, CCs real engineers
- **Makes the truest, least-readable knowledge base (the code) queryable as org knowledge**
- **Skills-extensible** — our mode system (eng/sales/marketing/support) is the same pattern as OpenClaw skills

**Pitch one-liner for judges:** "OpenClaw for your codebase — same agent-in-your-messaging
thesis, applied to the one knowledge base that's truest and least readable: your code."

**Stretch (after everything else is green):** ship a `skills/codebase-concierge/SKILL.md`
in OpenClaw's format so anyone running OpenClaw can add this to their personal agent.
Stub already exists in the repo — fill it in if time. Signals ecosystem fluency to judges.

## Judging criteria (what to optimize for)
1. **Integration depth** — Nia + AgentMail must be central, not bolted on. Threading + citations prove this.
2. **Technical execution** — must actually work in live demo. Stability > features.
3. **Problem & impact** — real pain across roles: engineers (PM interrupts), sales (capability questions), marketing (what to write about), support (bug vs feature).
4. **Creativity** — multi-mode inbox + auto-CC the engineer who last touched cited code (via git blame).

## Architecture (keep it boring, one file)
```
[AgentMail webhook] → POST /webhook → FastAPI
                                         ↓
                                  fetch thread context (AgentMail)
                                         ↓
                                  query Nia (codebase search)
                                         ↓
                                  Claude composes answer w/ citations
                                         ↓
                                  reply via AgentMail (threaded)
```

One process. No DB. No queue. Thread state lives in AgentMail itself.

## Stack
- Python 3.11 + FastAPI + uvicorn
- `anthropic` SDK (Claude Sonnet 4.6 — cheap, fast, enough)
- `httpx` for Nia + AgentMail REST calls
- `python-dotenv` for local env
- ngrok for local webhook testing
- Render for demo deployment

## Demo repos (multi-repo by design — real companies have many)
Index 2-4 related repos so the demo can show cross-repo answers. Suggested set:
- **Hono core** (https://github.com/honojs/hono)
- **Hono Bun adapter** or another official plugin
- **A Hono example app** showing real usage

This lets the demo answer questions that span repos: "how does middleware work?" pulls
from core; "how does Hono run on Bun?" pulls from the adapter; "how do I structure a
real app?" pulls from the example. Same inbox, multiple sources.

Index FIRST THING via Nia REST API (curl or httpx).
Capture each `repo_id` and put comma-separated in `NIA_REPO_IDS` env var.

**Verify tomorrow:** does Nia search natively across multiple repos in one call, or
do we fan out queries? Read https://docs.trynia.ai and the OpenAPI/SDK to confirm.
Both paths are wired in main.py.

## Env vars (see .env.example)
- `ANTHROPIC_API_KEY`
- `NIA_API_KEY` — get at trynia.ai
- `AGENTMAIL_API_KEY` — get from AgentMail dashboard
- `AGENTMAIL_INBOX_ID` — the inbox the webhook is attached to
- `NIA_REPO_ID` — the indexed repo's identifier (set after indexing)
- `WEBHOOK_SIGNING_SECRET` — for AgentMail signature verification (optional for demo)

## Build order (de-risked)
1. **0:00–0:30** — Sign up Nia + AgentMail, get keys. **Skip the Claude Code plugins** —
   the Nia plugin isn't in the marketplace and we don't need it. Go API-direct.
   Verify: index a small repo via Nia REST API, send 1 email through AgentMail REST API.
   **If either API auth is broken, pivot.**
2. **0:30–1:30** — End-to-end ugly path: webhook → Nia query → Claude → reply. Hardcoded prompt. No threading.
3. **1:30–2:30** — (after lunch) Add thread context, citations, path validation.
4. **2:30–4:30** — Polish: error handling, latency tuning, deploy to Render.
5. **4:30–5:30** — Add ONE differentiator (auto-CC engineer via git blame).
6. **5:30–6:00** — Demo prep: 3 canned demo emails, rehearse, record backup video.

**Stretch (only if 5:00 PM with everything green):** add sales mode.
Implementation: a `detect_mode(sender, subject)` function that returns `"eng" | "sales" | ...`,
a dict of system prompts per mode, and route in the webhook handler. ~20 lines total.
**Do not start this unless engineering mode is rock-solid AND deployed AND demo rehearsed.**

## Anti-patterns (don't do)
- Don't add a database. Thread state is in AgentMail.
- Don't multi-agent. One agent, one prompt path.
- Don't re-index live during demo. Pre-index at start.
- Don't fake the demo. Founder judges spot it instantly.
- Don't skip path validation — hallucinated `src/foo/bar.ts` cites kill credibility.

## Demo script (3 min + 2 min Q&A)
1. **20s** — Hook: "OpenClaw's thesis is that AI agents should live in the messaging platforms you already use. We applied that to the one knowledge base that's truest and least readable in any company — the codebase. Your code is the source of truth for what your product does. We built an inbox that translates it for everyone who can't read it."
2. **30s** — Show inbox. Send live email: "How does Hono handle middleware composition?"
3. **45s** — Reply arrives with cited file:line refs. Click through one — it's real.
4. **25s** — Follow-up in thread: "What about error handling in middleware?" — thread context preserved.
5. **25s** — Auto-CC differentiator: agent looped in the engineer who last touched that code via git blame.
6. **35s** — Architecture + roadmap slide: Nia (brain) + AgentMail (voice) in ~200 lines. Same brain, different voices via skills: sales asks capability questions, marketing asks "what shipped", support asks "bug or feature". One inbox per role. Ships as an OpenClaw skill — drop it into your personal agent and your codebase becomes queryable from any messaging channel.

**Q&A prep:**
- "Why not Slack?" → email is async, has threading, works across orgs (sales asking about partner's API), and AgentMail's two-way threading is uniquely suited.
- "What about hallucinations?" → path validation rejects any cite that isn't in the index. Show the validator code if asked.
- "Privacy?" → Nia indexes are scoped per customer; same auth model as GitHub Apps.
- "How is this different from Cursor/Claude Code?" → those are IDE tools for engineers. This is for everyone who *can't* open the IDE.

## API references (live links)
- Nia API: https://docs.trynia.ai/welcome
- Nia plugin (Claude Code): https://github.com/nozomio-labs/nia-plugin
- Nia main repo: https://github.com/nozomio-labs/nia
- AgentMail docs: https://docs.agentmail.to
- AgentMail full LLM-readable docs: https://docs.agentmail.to/llms-full.txt
