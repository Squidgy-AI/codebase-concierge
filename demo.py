"""
/demo page — quick-fire links and dashboard deep-links to demonstrate every feature.

Senders are pulled from the configured users table so the runbook always uses
real, recognized addresses. Cache-hit demo deliberately uses a *new* sender so
the audience sees the "previously answered for X" line trigger.
"""
import html
import os
import urllib.parse

import cache


_INBOX = os.environ.get("AGENTMAIL_INBOX_ID", "codebaseconcierge@agentmail.to")


def _mailto(subject: str, body: str) -> str:
    qs = urllib.parse.urlencode({"subject": subject, "body": body}, quote_via=urllib.parse.quote)
    return f"mailto:{_INBOX}?{qs}"


def _dash_link(question: str, mode: str = "eng", sender: str = "", autosend: bool = False) -> str:
    params = {"question": question, "mode": mode}
    if sender:
        params["sender"] = sender
    if autosend:
        params["autosend"] = "1"
    return "/?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


def _users_by_mode() -> dict[str, list[str]]:
    """Group configured users by their default mode."""
    out: dict[str, list[str]] = {"eng": [], "sales": [], "marketing": [], "support": []}
    for u in cache.list_users():
        out.setdefault(u["default_mode"], []).append(u["email"])
    return out


def _build_scenarios() -> list[dict]:
    """Resolve the 7 demo scenarios against the live users table.

    Returns a list of dicts with id, title, why, question, mode, sender, note.
    """
    by_mode = _users_by_mode()

    def pick(mode: str) -> str:
        return by_mode.get(mode, [""])[0] if by_mode.get(mode) else ""

    eng_sender = pick("eng")
    sales_sender = pick("sales")
    mktg_sender = pick("marketing")
    sup_sender = pick("support") or eng_sender  # falls back to eng + subject tag

    # For the cache-hit demo we want a DIFFERENT eng-default sender than scenario 1.
    # If the user only has one eng-mode email, use a not-yet-known address — the
    # webhook will still answer (lockdown off) and the audience sees the magic.
    second_eng = ""
    eng_users = by_mode.get("eng", [])
    if len(eng_users) >= 2:
        second_eng = eng_users[1]
    else:
        second_eng = "second-asker@team.example"

    return [
        {
            "id": "eng-cited",
            "title": "1. Engineering Q&A with cited file:line",
            "why": "The basic loop. Nia retrieves code; Claude composes; reply links back to real GitHub blobs. Auto-CCs the engineer who last touched the cited code.",
            "question": "How does Hono handle middleware composition?",
            "mode": "eng",
            "sender": eng_sender,
            "note": "" if eng_sender else "Add an eng user in /admin first.",
        },
        {
            "id": "sales-mode",
            "title": "2. Sales mode — capability answer, no code",
            "why": "Same brain, different voice. Sales people get a customer-safe yes/no/partial-yes — no jargon, no code blocks. Sender's default mode is sales, no subject tag needed.",
            "question": "Does Hono support custom error handlers and how robust are they?",
            "mode": "sales",
            "sender": sales_sender,
            "note": "" if sales_sender else "Add a sales user in /admin first.",
        },
        {
            "id": "marketing-mode",
            "title": "3. Marketing mode — hook + bullets + headline",
            "why": "Marketing voice. Punchy hook, why-it-matters bullets, suggested tweet/headline. Same memory, repurposed.",
            "question": "What's interesting about how Hono runs across edge runtimes that we could write about?",
            "mode": "marketing",
            "sender": mktg_sender,
            "note": "" if mktg_sender else "Add a marketing user in /admin first.",
        },
        {
            "id": "support-mode",
            "title": "4. Support mode — BUG / EXPECTED / NEEDS-MORE-INFO triage",
            "why": "Support agent voice. First word is the verdict label — code-grounded triage in seconds. Subject tag [support] forces the mode regardless of sender.",
            "question": "[support] Customer says middleware after next() is silently swallowing thrown errors.",
            "mode": "support",
            "sender": sup_sender,
            "note": "Add a support user in /admin to skip the [support] tag." if not by_mode.get("support") else "",
        },
        {
            "id": "cross-repo",
            "title": "5. Cross-repo answer (hono + node-server)",
            "why": "Two repos indexed; Nia pulls from whichever is relevant. Sources should include the node-server adapter, not just core.",
            "question": "How does Hono actually run on Node.js — what's the bridge between Hono's web-standards request and Node's req/res?",
            "mode": "eng",
            "sender": eng_sender,
            "note": "",
        },
        {
            "id": "cache-hit",
            "title": "6. Memory across senders (cache magic ⚡)",
            "why": "Run scenario 1 first, then this. Same question, different sender. First fired Nia (~25s). This one returns in <1s with 'Previously answered for [original sender]'. Dashboard shows the hit count tick up live.",
            "question": "How does Hono handle middleware composition?",
            "mode": "eng",
            "sender": second_eng,
            "note": "",
        },
        {
            "id": "unknown-sender",
            "title": "7. Unknown sender → flagged in admin",
            "why": "Email from someone not in the users table gets logged to the admin panel under 'Flagged'. Lockdown OFF: still answered. Lockdown ON: ignored.",
            "question": "Hi, can you tell me about the codebase?",
            "mode": "eng",
            "sender": "stranger@unknown-domain.example",
            "note": "",
        },
    ]


# Backwards-compat: a flat tuple list still consumed by admin.py's prewarm task.
_SCENARIOS = [
    (s["id"], s["title"], s["why"], s["question"], s["mode"], s["sender"])
    for s in _build_scenarios()
]


def _section(s: dict) -> str:
    mailto = _mailto(f"[{s['mode']}] {s['title'].split('. ',1)[-1]}", s["question"])
    dash = _dash_link(s["question"], s["mode"], s["sender"])
    sender_html = (
        f' · from: <strong>{html.escape(s["sender"])}</strong>'
        if s["sender"] else
        ' · <span style="color:#c33">no sender configured</span>'
    )
    note_html = (
        f'<div style="font-size:12px;color:#c33;margin-top:6px">⚠ {html.escape(s["note"])}</div>'
        if s["note"] else ""
    )
    return f"""
    <section class="card">
      <h2>{html.escape(s['title'])}</h2>
      <p class="why">{html.escape(s['why'])}</p>
      <div class="q"><span class="lbl">Question:</span> {html.escape(s['question'])}</div>
      <div class="meta">mode: <strong>{s['mode']}</strong>{sender_html}</div>
      {note_html}
      <div class="actions">
        <a class="btn" href="{dash}">▶ Run in dashboard</a>
        <a class="btn outline" href="{mailto}">✉ Send via email</a>
      </div>
    </section>
    """


def render() -> str:
    scenarios = _build_scenarios()
    sections = "\n".join(_section(s) for s in scenarios)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Codebase Concierge — Demo</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', sans-serif;
            background: #fafafa; color: #222; margin: 0; padding: 24px; }}
    .wrap {{ max-width: 820px; margin: 0 auto; }}
    h1 {{ font-size: 24px; margin: 0 0 4px 0; }}
    .sub {{ color: #888; font-size: 13px; margin-bottom: 20px; }}
    .nav a {{ margin-right: 16px; color: #0a3a99; font-size: 13px; }}
    .card {{ background: #fff; border-radius: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.04);
             padding: 16px 20px; margin-bottom: 14px; }}
    .card h2 {{ font-size: 15px; margin: 0 0 6px; }}
    .card .why {{ color: #555; font-size: 13px; margin: 0 0 10px; }}
    .card .q {{ font-size: 14px; padding: 8px 12px; background: #f4f6fa;
                border-left: 3px solid #0a3a99; border-radius: 4px; margin: 10px 0 4px; }}
    .card .q .lbl {{ font-size: 11px; color: #888; text-transform: uppercase;
                      letter-spacing: 0.5px; margin-right: 6px; }}
    .card .meta {{ font-size: 12px; color: #666; margin-bottom: 10px; }}
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
    <div class="sub">Click ▶ to fire a scenario in the dashboard, ✉ to send it as a real email through AgentMail. Senders are pulled from the configured users in <a href="/admin">/admin</a>.</div>
    <div class="nav">
      <a href="/">live log →</a>
      <a href="/admin">admin →</a>
    </div>
    {sections}
  </div>
</body>
</html>"""
