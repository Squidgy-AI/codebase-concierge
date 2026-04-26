"""
/demo page — beat-by-beat runbook matching the live demo script.

Each beat has a `prewarm` flag. Only prewarmed beats are seeded into the
cache by the admin "Pre-warm for demo" button — the others stay genuinely
fresh so the audience sees real Nia/Claude latency on stage.
"""
import html
import os
import urllib.parse

import cache


_INBOX = os.environ.get("AGENTMAIL_INBOX_ID", "codebaseconcierge@agentmail.to")


def _mailto(subject: str, body: str) -> str:
    qs = urllib.parse.urlencode({"subject": subject, "body": body}, quote_via=urllib.parse.quote)
    return f"mailto:{_INBOX}?{qs}"


def _dash_link(question: str, mode: str = "eng", sender: str = "") -> str:
    params = {"question": question, "mode": mode}
    if sender:
        params["sender"] = sender
    return "/?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


def _users_by_mode() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"eng": [], "sales": [], "marketing": [], "support": [], "security": []}
    for u in cache.list_users():
        out.setdefault(u["default_mode"], []).append(u["email"])
    return out


def _build_beats() -> list[dict]:
    """The actual demo flow. Edit presenter notes here; nothing else.

    `prewarm=True` → seeded into the cache before the demo (fast).
    `prewarm=False` → runs live for the audience (real latency).
    """
    by_mode = _users_by_mode()
    eng = (by_mode.get("eng") or [""])[0]
    sales = (by_mode.get("sales") or [""])[0]
    mktg = (by_mode.get("marketing") or [""])[0]
    sec = (by_mode.get("security") or [""])[0] or eng

    return [
        {
            "id": "beat1",
            "label": "Beat 1 — The basic loop (live)",
            "question": "How does Hono handle middleware composition?",
            "mode": "eng",
            "sender": eng,
            "prewarm": False,
            "notes": [
                "Open the dashboard on screen, /admin already authed.",
                "Send this question in the chat panel. ~25s — narrate while it runs:",
                "  – \"Nia retrieves the relevant code; Claude composes the answer; AgentMail threads the reply.\"",
                "When it returns: point to the cited sources list — these are real GitHub paths.",
                "Point to the engineer attribution in the body — git blame on the cited file.",
            ],
        },
        {
            "id": "beat2",
            "label": "Beat 2 — Same brain, different voice (live)",
            "question": "Does Hono support custom error handlers and how robust are they?",
            "mode": "sales",
            "sender": sales,
            "prewarm": False,
            "notes": [
                "Click ▶ — the dashboard pre-fills with the sales sender + sales mode.",
                "While Nia runs (~25s): \"Same codebase, same brain. But the asker is sales, so the voice changes.\"",
                "When it returns: contrast — no code blocks, plain-language yes/partial-yes, customer-safe.",
                "Optional callback: \"Subject tags override sender; type `[eng]` from the same address and you'd get code.\"",
            ],
        },
        {
            "id": "beat3",
            "label": "Beat 3 — Memory across senders (cache hit ⚡)",
            "question": "How does Hono handle middleware composition?",
            "mode": "eng",
            "sender": "newperson@later.com",
            "prewarm": False,
            "notes": [
                "This re-asks Beat 1's question — but as a fresh sender.",
                "Click ▶. Returns in <1s. The reply prepends \"Previously answered for [Beat 1's sender] on [date].\"",
                "Point at the dashboard feed — the original entry's hit counter ticks up live.",
                "Pitch line: \"Memory across workflows. Every email contributes to the org's knowledge graph.\"",
            ],
        },
        {
            "id": "beat4",
            "label": "Beat 4 — Cross-repo + docs (warm)",
            "question": "How does Hono actually run on Node.js — what's the bridge between Hono's web-standards request and Node's req/res?",
            "mode": "eng",
            "sender": eng,
            "prewarm": True,
            "notes": [
                "Click ▶. Returns instantly (pre-warmed). Point to sources:",
                "  – they include `honojs/node-server/...` (the adapter repo) AND",
                "  – `Hono Docs` (the indexed documentation site).",
                "Pitch line: \"Two repos and the docs, queried in one call. Same surface for any company's source corpus.\"",
            ],
        },
        {
            "id": "beat5-optional",
            "label": "Beat 5 — Marketing-angle reuse (optional)",
            "question": "What's interesting about how Hono runs across edge runtimes that we could write about?",
            "mode": "marketing",
            "sender": mktg,
            "prewarm": False,
            "notes": [
                "Skip if running short. Otherwise: send live.",
                "When it returns: hook + bullets + suggested headline. Reuses the same brain for content ideation.",
                "This is the \"one inbox, multiple modes\" punchline — code, sales, marketing, support all share the source of truth.",
            ],
        },
        {
            "id": "beat6-security",
            "label": "Beat 6 — Security review (live, optional)",
            "question": "[security] Review Hono's middleware composition and request handling for security risks — auth gaps, input validation, or unsafe defaults a self-hosted user should know about.",
            "mode": "security",
            "sender": sec,
            "prewarm": False,
            "notes": [
                "Click ▶. Subject tag forces security mode regardless of sender.",
                "While Nia runs (~25s): \"Same brain, but the lens changes — now it's a security review of the retrieved code.\"",
                "When it returns: each finding is tagged HIGH/MED/LOW with a real cited file and a one-line remediation.",
                "Closing line: \"Advisory only — but the citations are real. The agent can't invent paths it doesn't have.\"",
            ],
        },
        {
            "id": "beat7-meta",
            "label": "Beat 7 — Add a new mode live (optional, meta)",
            "question": "(this beat is operated from /admin, not the chat box)",
            "mode": "eng",
            "sender": "",
            "prewarm": False,
            "notes": [
                "This is the \"it's not hardcoded\" beat. Open /admin in a second tab.",
                "Scroll to the Custom modes panel → expand ✨ \"Need a prompt? Generate one from a description\".",
                "Type one sentence — e.g. \"Onboarding mode for new engineers — points them at entry-point files with one-line explanations.\" Click Draft prompt.",
                "Show the audience: id, label, and the full system prompt all auto-fill in ~3s (Claude Haiku call).",
                "Click Add mode. Switch back to the dashboard tab — refresh.",
                "Show the mode pill is now in the filter bar AND the chat-mode dropdown AND the user-add dropdown. \"It propagates everywhere.\"",
                "Optional: send a question with the new mode (subject tag like [onboarding]) live. ~25s, but lands the punchline.",
                "Closing line: \"The whole demo you just watched? Anyone could rebuild any of those modes from a one-line description. The infrastructure is the product.\"",
            ],
        },
    ]


# Used by the prewarm background task.
_SCENARIOS = [
    (b["id"], b["label"], "; ".join(b["notes"]), b["question"], b["mode"], b["sender"])
    for b in _build_beats()
    if b.get("prewarm")
]


def _section(b: dict) -> str:
    mailto = _mailto(f"[{b['mode']}] {b['label']}", b["question"])
    dash = _dash_link(b["question"], b["mode"], b["sender"])
    sender_html = (
        f'from: <strong>{html.escape(b["sender"])}</strong>'
        if b["sender"] else
        '<span style="color:#c33">no sender configured</span>'
    )
    notes_html = "".join(
        f'<li>{html.escape(n)}</li>' for n in b.get("notes", [])
    )
    badge = (
        '<span class="tag warm">⚡ pre-warmed</span>'
        if b.get("prewarm") else
        '<span class="tag live">▶ live (~25s)</span>'
    )
    return f"""
    <section class="card">
      <div class="hd">
        <h2>{html.escape(b['label'])}</h2>
        {badge}
      </div>
      <div class="q"><span class="lbl">Question:</span> {html.escape(b['question'])}</div>
      <div class="meta">mode: <strong>{b['mode']}</strong> · {sender_html}</div>
      <details class="notes" open>
        <summary>Presenter notes</summary>
        <ul>{notes_html}</ul>
      </details>
      <div class="actions">
        <a class="btn" href="{dash}">▶ Open in dashboard</a>
        <a class="btn outline" href="{mailto}">✉ Send via email</a>
      </div>
    </section>
    """


def render() -> str:
    beats = _build_beats()
    sections = "\n".join(_section(b) for b in beats)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Codebase Concierge — Demo runbook</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', sans-serif;
            background: #fafafa; color: #222; margin: 0; padding: 24px; }}
    .wrap {{ max-width: 860px; margin: 0 auto; }}
    h1 {{ font-size: 24px; margin: 0 0 4px 0; }}
    .sub {{ color: #888; font-size: 13px; margin-bottom: 18px; }}
    .nav a {{ margin-right: 16px; color: #0a3a99; font-size: 13px; }}
    .card {{ background: #fff; border-radius: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.04);
             padding: 18px 22px; margin-bottom: 14px; }}
    .card .hd {{ display: flex; align-items: center; gap: 10px; margin: 0 0 12px; }}
    .card h2 {{ font-size: 16px; margin: 0; }}
    .tag {{ font-size: 11px; padding: 2px 8px; border-radius: 4px;
            text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px; }}
    .tag.warm {{ background: #fff8d6; color: #8a6300; }}
    .tag.live {{ background: #e0eafc; color: #0a3a99; }}
    .card .q {{ font-size: 14px; padding: 8px 12px; background: #f4f6fa;
                border-left: 3px solid #0a3a99; border-radius: 4px; margin: 0 0 4px; }}
    .card .q .lbl {{ font-size: 11px; color: #888; text-transform: uppercase;
                      letter-spacing: 0.5px; margin-right: 6px; }}
    .card .meta {{ font-size: 12px; color: #666; margin: 8px 0 12px; }}
    .notes {{ background: #fafafa; border: 1px solid #eee; border-radius: 6px;
              padding: 10px 14px; margin-bottom: 12px; }}
    .notes summary {{ cursor: pointer; font-size: 12px; font-weight: 600;
                       color: #555; text-transform: uppercase; letter-spacing: 0.5px; }}
    .notes ul {{ margin: 8px 0 0 18px; padding: 0; font-size: 13px; line-height: 1.55; color: #333; }}
    .notes li {{ margin-bottom: 4px; }}
    .card .actions {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .btn {{ display: inline-block; padding: 7px 14px; border-radius: 6px;
            background: #0a3a99; color: white; font-size: 13px; font-weight: 500;
            text-decoration: none; }}
    .btn:hover {{ background: #0c46b8; }}
    .btn.outline {{ background: white; color: #0a3a99; border: 1px solid #0a3a99; }}
    .btn.outline:hover {{ background: #eaf0fb; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Codebase Concierge — demo runbook</h1>
    <div class="sub">Beat-by-beat plan with presenter notes. The "Pre-warm for demo" button in /admin only seeds the warm beats — the live ones stay genuinely fresh.</div>
    <div class="nav">
      <a href="/">live log →</a>
      <a href="/admin">admin →</a>
    </div>
    {sections}
  </div>
</body>
</html>"""
