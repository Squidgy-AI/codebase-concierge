# Backup Demo Video — Click-by-Click Plan

**Purpose:** A recorded 3-minute video to play if the live demo fails (Nia timeout, AgentMail
webhook drop, wifi flake). Record this the night before. Watch it once before going on stage.

**Target length:** 2:55 — 3:05. Hard ceiling 3:10.
**Tool:** QuickTime screen recording (macOS, no extra install) + built-in mic. OBS if you want
webcam PiP, but skip if it adds setup risk.
**Resolution:** 1920×1080. Zoom browser to 125% so cites are readable on a projector.

---

## Pre-record setup (do this once, 15 min)

1. **Two browser windows, side-by-side, each at 50% width:**
   - **Left:** AgentMail inbox UI, logged in, viewing the demo inbox.
   - **Right:** Gmail (or whatever account is sending the demo emails). Compose window ready.
2. **One terminal window** behind both, visible only for the architecture beat. Show
   `tail -f` on the FastAPI log so you can prove the cache hit on screen.
3. **Pre-warm the cache:**
   - Send Q1 ("How does Hono handle middleware composition?") from sender A.
   - Wait for the reply. Confirm it lands with valid cites.
   - This is what makes Q10 (the duplicate question) a sub-second cache hit on camera.
4. **Clear the inbox of everything except the Q1 thread** so the recording isn't cluttered.
5. **Close Slack, Discord, calendar — anything that can fire a notification mid-take.**
   Enable macOS Do Not Disturb.
6. **Open `demo/deck.md` rendered (e.g. in a markdown preview)** in a third tab on the left
   window — you'll cut to it for the architecture beat.

---

## Shot list (with timing, narration, on-screen action)

Format: `[T+mm:ss] WHAT THE CAMERA SEES — narration in italics`

### Shot 1 — Hook (0:00 – 0:20, 20s)

**On screen:** Empty AgentMail inbox, cursor still.
**Narration (read steady, do not rush):**

> *"OpenClaw's thesis is that AI agents should live in the messaging platforms you already
> use. We applied that to the one knowledge base that's truest and least readable in any
> company — the codebase. Brain, voice, and memory."*

**Action:** None. Just talk over the static inbox. The visual stillness sells the line.

---

### Shot 2 — Send Q1 live (0:20 – 0:50, 30s)

**On screen:** Switch focus to the right window (Gmail compose).
**Action:**
- 0:20 — Click the compose window. Subject already drafted: `How does Hono handle middleware composition?`
- 0:25 — Body already drafted (paste from `questions.md` Q1). Read the body aloud as you "type" the last sentence — gives it presence without burning time.
- 0:35 — Click Send.
- 0:38 — Cut focus to the left window (AgentMail inbox).
- 0:40 — Wait for the email to appear in the inbox. (In the recording, you can speed-ramp this 2× if it takes >5s — keep total shot ≤30s.)

**Narration over the wait:**
> *"PM emails the inbox like she'd email a teammate. The agent reads it, queries Nia for
> grounded context across the indexed repos, and composes a reply."*

---

### Shot 3 — Reply lands, click a cite (0:50 – 1:25, 35s)

**On screen:** AgentMail thread view. Reply visible.
**Action:**
- 0:50 — Reply appears. Pause 1 second so the viewer registers it landed.
- 0:52 — Cursor moves to the body. Hover over the first `src/compose.ts:142`-style cite.
- 0:58 — **Click the cite.** It opens the GitHub blob at that line in a new tab.
- 1:05 — Pan the cursor across the highlighted line. Hold for 2 seconds.
- 1:10 — Cut back to the AgentMail tab.

**Narration:**
> *"The reply is grounded — every claim points to a real file, real line. Click through and
> it's actually there. No hallucinated paths. We validate every cite against the index
> before sending."*

---

### Shot 4 — Thread follow-up (1:25 – 1:50, 25s)

**On screen:** AgentMail thread, reply still visible.
**Action:**
- 1:25 — Switch to Gmail. The Q1 thread is already in the inbox (the agent's reply).
- 1:28 — Click Reply on the thread.
- 1:30 — Body pre-drafted (Q5): "Quick follow-up — what happens when middleware throws?"
- 1:35 — Send.
- 1:38 — Cut to AgentMail. Wait for second reply to land.
- 1:45 — Reply lands. Highlight the new cite (`onError`, `HTTPException`).

**Narration:**
> *"Same thread. The agent has the full context — it doesn't re-explain middleware, it
> answers the follow-up. AgentMail owns thread state; we don't need a database for it."*

---

### Shot 5 — The memory moment (1:50 – 2:20, 30s) ⭐

**This is the shot the demo lives or dies on. Get it right.**

**On screen:** Switch Gmail to a **different sender** (second tab, second account).
**Action:**
- 1:50 — Open the second Gmail account (sender B). Compose pre-drafted with Q10
  (same subject as Q1, slightly different body wording).
- 1:55 — Send.
- 1:57 — Cut to AgentMail inbox AND to the terminal showing the FastAPI log.
- 1:58 — Reply lands in **<1 second**. Log line shows `cache hit, cosine 0.94`.
- 2:02 — Open the new email. Highlight the prefix: *"Previously answered for [sender A] on
  [date]."*
- 2:10 — Hold the highlight for 3 full seconds. Let it land.

**Narration (this is the line — slow it down):**
> *"Different sender, same question. Reply comes back in under a second — it's a cache hit.
> The agent remembers what it told Sarah on Wednesday and tells Marcus the same thing,
> grounded in the same code. That's the memory layer. SQLite today. Nia vaults next."*

---

### Shot 6 — Auto-CC differentiator (2:20 – 2:35, 15s)

**On screen:** Back to one of the agent's replies. Scroll to the To/Cc header.
**Action:**
- 2:20 — Cursor highlights the **CC** field. There's a real engineer's email there.
- 2:25 — Hover/highlight for 3 seconds.
- 2:30 — Cut.

**Narration:**
> *"And the agent CC'd the engineer who last touched that code — pulled via git blame from
> the cited line. The right human is in the loop without anyone tagging them."*

---

### Shot 7 — Architecture + close (2:35 – 3:00, 25s)

**On screen:** Cut to the rendered `deck.md` slide 4 (architecture diagram).
**Action:**
- 2:35 — Architecture slide visible. Hold 3 seconds.
- 2:40 — Cursor traces the flow: webhook → cache → Nia → Claude → AgentMail.
- 2:50 — Cut to slide 5 (the closing slide).

**Narration:**
> *"Brain, voice, memory. Nia, AgentMail, SQLite — about 250 lines of Python. Ships as an
> OpenClaw skill. Same brain, different voices: sales asks capability questions, marketing
> asks what shipped, support asks bug or feature. OpenClaw for your codebase."*

- 3:00 — Final frame holds for 2 seconds on the one-liner. Stop recording.

---

## Editing pass (15 min, do once)

- **Trim dead air** at the start and end of each shot. Aim for tight cuts, not jumpy cuts.
- **Speed-ramp any wait >3s to 2×** (the email-arriving moments). Keep the cache-hit wait
  at real speed — it's the proof.
- **Add a 1-second fade between shots 5 and 6.** Lets the memory moment breathe.
- **No background music.** It distracts from narration and judges hate it.
- **Add captions** for every narration line — judges in a noisy room may not hear audio.
  QuickTime won't do this; if you have time, drop into iMovie or Descript. If not, skip.
- **Export 1080p, ≤100MB.** Drop on the laptop's desktop AND on a USB stick. Two copies.

---

## Failure-mode rehearsal

Before recording, run the live demo dry once. If anything below fires, fix it before
hitting record:

- [ ] Q1 reply lands in <30s with valid cites
- [ ] Q5 reply preserves thread context (doesn't re-explain middleware)
- [ ] Q10 hits the cache, returns in <2s, includes "previously answered" prefix
- [ ] CC field on at least one reply contains a real engineer email (git blame worked)
- [ ] Architecture slide renders cleanly in markdown preview
- [ ] Mic level is audible but not peaking (do a 5-second test record first)

---

## On-stage usage

- If the live demo dies, say: *"The live agent is having a moment — here's the recording
  from last night, same code, same inbox, same Nia index."* Play the video. Don't
  apologize past that one sentence; judges respect a clean fallback more than a flailing
  recovery.
- If the live demo works, **do not show the video.** Move straight to Q&A. Mentioning a
  backup video you didn't need just dilutes the live result.
