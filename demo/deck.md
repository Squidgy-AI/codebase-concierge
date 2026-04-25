# Codebase Concierge — Demo Deck

5 slides. 3-minute pitch + 2-minute Q&A. Built for OpenClaw (Eragon × Nozomio × AgentMail).

---

## Slide 1 — The Problem

**The codebase is the truest source of what your product does. It's also the least readable.**

- Sales asks: "Do we support SSO with Okta?" → engineer interrupted, answer is stale by next quarter
- Marketing asks: "What shipped this sprint worth a blog post?" → guess from Slack, miss the real story
- Support asks: "Customer says X is broken — bug or feature?" → triage round-trip, hours lost
- New PM asks: "How does middleware composition work in our framework?" → reads docs, docs lie

**Every non-engineer in the company is blocked on the one person who can read code.**

Speaker note: Land the universality. This is not a developer-tools pitch — it's an org-knowledge pitch.

---

## Slide 2 — The Pitch

**OpenClaw for your codebase.**

OpenClaw's thesis: AI agents should live in the messaging platforms you already use.
We applied it to the one knowledge base that's truest and least readable: the code itself.

- **Brain** — Nia indexes the repos, returns grounded snippets with file:line citations
- **Voice** — AgentMail gives the agent a real inbox, real threading, real replies
- **Memory** — SQLite Q&A cache today, Nia vaults tomorrow — the org's accumulated answers

One inbox. Anyone can email it. Replies are grounded in actual code, with clickable citations.

Speaker note: Hit "brain, voice, memory" as the three-beat. Memory is the hackathon theme.

---

## Slide 3 — Live Demo

**Three emails, three moments:**

1. **The grounded answer** — "How does Hono handle middleware composition?" → reply lands with `src/hono.ts:142` cites you can click. Real code, not marketing.

2. **The thread** — Follow-up in the same thread: "What about error handling in middleware?" → agent has full thread context, answers without re-stating the question.

3. **The memory moment** — A different sender asks the same question. Reply returns in <1 second: *"Previously answered for Sarah on Wednesday — here's what we said."* The cache hit. Quota saved. Latency killed.

4. **The differentiator** — Agent auto-CC's the engineer who last touched the cited code (via `git blame`). The right human is in the loop without anyone tagging them.

Speaker note: If the memory moment lands, the demo is won. Pre-warm the cache.

---

## Slide 4 — Architecture

```
[AgentMail webhook] ──► FastAPI ──► thread context (AgentMail)
                                         │
                                         ▼
                              ┌── Q&A cache (SQLite) ──┐
                              │   cosine sim > 0.92    │  ◄── hit: <1s reply
                              └────────┬───────────────┘
                                       │ miss
                                       ▼
                              Nia search (multi-repo)
                                       │
                                       ▼
                              Claude composes + cites
                                       │
                                       ▼
                              AgentMail reply (threaded, HTML)
                                       │
                                       └─► write to cache
```

- **~250 lines of Python**, single file, single process
- **No database** for thread state — AgentMail owns it
- **Multi-repo by design** — Hono core + Bun adapter + example app, indexed once

**Same brain, different voices via skills:**
- Engineering mode (shipped today)
- Sales / Marketing / Support modes (architecture-ready, ~20 lines per mode)
- Sender domain or `[sales]` subject tag selects the system prompt

Speaker note: Boring architecture is the feature. Stability beats cleverness on judging day.

---

## Slide 5 — Why This Wins & What's Next

**Hits all four judging criteria:**

| Criterion | How |
|---|---|
| Integration depth | Nia + AgentMail are load-bearing. Threading + citations only work if both are wired deeply. |
| Technical execution | Live demo, real code, real inbox, real replies. No hand-waving. |
| Problem & impact | Every role in the org has this pain. Engineers aren't the only audience. |
| Creativity | Multi-mode inbox + git-blame auto-CC + memory cache from day one. |

**Hits the organizers' themes:**
- ✅ **Shared memory across workflows** — SQLite cache (today), Nia vaults (next)
- ✅ **OpenClaw skill** — ships as a drop-in skill anyone can install
- ✅ **Brain + voice** — Nia + AgentMail, not bolted on

**Roadmap (60 seconds in Q&A):**
- **Nia vaults** — Karpathy-style persistent wikis. `dream` cycles surface non-obvious connections across past answers and flag contradictions when behavior drifts from prior answers. The cache becomes the org's living knowledge graph.
- **Mode marketplace** — sales, marketing, support skills. Each mode = different system prompt + different Nia search strategy. Same inbox, many voices.
- **Action mode** — agent doesn't just answer; it opens the PR. "Customer needs Y, here's the diff."

**One-liner for the close:**
> *"OpenClaw for your codebase — same agent-in-your-messaging thesis, applied to the one knowledge base that's truest and least readable: your code."*
