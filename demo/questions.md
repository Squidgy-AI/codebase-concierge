# Demo Questions — Tuned for Hono

10 canned questions for the live demo and rehearsal. All grounded in Hono's actual codebase
(core + Bun adapter + an example app). Each lists the *expected citation surface* so the
operator can sanity-check the agent's reply during rehearsal.

**Pre-demo checklist:**
- Pre-warm the cache with Q1 and Q5 (these are the "memory moment" questions in the live demo).
- Verify each question returns a reply with at least one valid `file:line` cite before going on stage.
- If a reply hallucinates a path, the path-validator rejects the cite — flag it during rehearsal.

---

## Q1 — Middleware composition (the headline question)

**Subject:** `How does Hono handle middleware composition?`
**Body:**
> Hi — non-engineer here, trying to understand how middleware actually chains together
> in Hono. Is it onion-style like Koa, or sequential like Express? Where in the code
> does the chaining happen?

**Expected cites:** `src/compose.ts`, `src/hono-base.ts` (the `compose` import + invocation),
mention of `next()` semantics. Onion-style answer.
**Why it's first:** clean, well-known pattern, generates a satisfying file:line answer.

---

## Q2 — Routing internals

**Subject:** `Which router does Hono use by default and why are there several?`
**Body:**
> I see RegExpRouter, TrieRouter, SmartRouter, PatternRouter mentioned. What's the
> default, and what's the tradeoff between them? Trying to understand performance
> implications for our use case.

**Expected cites:** `src/router/*` directory, `src/hono.ts` (default router selection),
`SmartRouter` falling back logic. Mentions of static-route optimization.

---

## Q3 — Type inference for paths

**Subject:** `How does Hono infer types for path params like /users/:id?`
**Body:**
> The TypeScript autocomplete on `c.req.param('id')` is uncanny. How does that work?
> Is it parsing the string literal at compile time?

**Expected cites:** `src/types.ts` (ParamKeys, ExtractKey, type-level path parsing),
template literal type magic. Explanation of conditional types over the path string.

---

## Q4 — Bun adapter (cross-repo question)

**Subject:** `How does Hono run on Bun specifically — what's different from Node?`
**Body:**
> We're evaluating Bun for a service. Curious how Hono adapts to Bun's
> `Bun.serve` vs Node's http module. Is there overhead?

**Expected cites:** **`@hono/node-server`** vs **Bun adapter** repo (different repo →
proves multi-repo search works), `Bun.serve` integration, request/response Web API
fidelity. **This is the cross-repo demo question.**

---

## Q5 — Error handling (thread follow-up)

**Subject:** `Re: How does Hono handle middleware composition?`
**Body:**
> Quick follow-up — what happens when middleware throws? Is there a global error hook?

**Expected cites:** `src/hono-base.ts` `onError` handler, `app.onError(...)` API,
`HTTPException` class in `src/http-exception.ts`. **Thread-context test:** the agent
should NOT re-explain what middleware is — should answer the follow-up directly.

---

## Q6 — Request validation

**Subject:** `What's the recommended way to validate request bodies?`
**Body:**
> A new dev on the team is asking. Is there a built-in validator, or do we bring our own
> (Zod, Valibot, etc.)?

**Expected cites:** `src/validator/*`, `validator()` middleware, examples of
zValidator from `@hono/zod-validator` (separate package). Should mention the validator
is bring-your-own with hooks.

---

## Q7 — Streaming responses

**Subject:** `Does Hono support streaming responses (SSE / chunked)?`
**Body:**
> Building a feature that needs to stream model output to the browser. Does Hono have
> first-class streaming, and what does the API look like?

**Expected cites:** `src/helper/streaming/*`, `streamSSE`, `stream`, examples of
`c.body(stream)` or the streaming helper. ReadableStream-based.

---

## Q8 — Context object lifecycle

**Subject:** `What lives on the Context object and when is it created?`
**Body:**
> Trying to understand if I can stash request-scoped state on `c` safely. When does
> the Context get created vs reused?

**Expected cites:** `src/context.ts`, per-request instantiation in `hono-base.ts`,
`c.set()`/`c.get()` typed via `Variables`, mention of `c.var`.

---

## Q9 — JSX support

**Subject:** `Hono has JSX? How does that work without React?`
**Body:**
> Saw a tweet mentioning Hono can render JSX server-side. How is that wired up — does
> it pull in React, or is there a custom renderer?

**Expected cites:** `src/jsx/*`, custom `jsxDEV`/`jsx` runtime, `tsconfig` JSX factory
notes, `jsxRenderer` middleware. No React dependency.

---

## Q10 — Testing the framework itself (the "memory moment" duplicate)

**Subject:** `How does Hono handle middleware composition?`
**Body (sent from a different sender / domain):**
> Hi! New here — trying to wrap my head around how middleware works in Hono. Could
> someone explain the chaining model?

**Expected behavior:** **Cache hit.** Reply returns in <1s with phrasing like
*"Previously answered for [Q1 sender] on [date] — here's the answer:"* followed by
the same cited reply. **This is the on-stage memory moment** — pre-warm Q1 the
night before so this hits cleanly.

---

## Rehearsal order (suggested)

1. **Q1** — anchors the demo with a clean grounded answer
2. **Q5** — same thread, proves thread context
3. **Q10** — different sender, proves the memory cache (this is the wow moment)
4. **Q4** — cross-repo, proves multi-repo Nia search

Q2/Q3/Q6/Q7/Q8/Q9 are bench depth — use them for Q&A or if the live demo loses a
question to a transient API hiccup.

## If something fails live

- **Nia timeout** → fall back to a Q10-style cache hit. Have Q1 and Q5 pre-warmed.
- **AgentMail webhook drop** → show the recorded backup video (record the night before).
- **Hallucinated cite** → don't paper over it. Say *"and here's the path validator
  catching it"* — turn the failure into a credibility moment.
