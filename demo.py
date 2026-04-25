"""
/demo page — quick-fire links and dashboard deep-links to demonstrate every feature.
"""
import os
import urllib.parse


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


# Each scenario: (id, title, why-it-matters, question, mode, sender (opt))
_SCENARIOS = [
    (
        "eng-cited",
        "1. Engineering Q&A with cited file:line",
        "The basic loop. Nia retrieves code; Claude composes; reply links back to real GitHub blobs. Auto-CCs the engineer who last touched the cited code.",
        "How does Hono handle middleware composition?",
        "eng",
        "alice@yourco.com",
    ),
    (
        "sales-mode",
        "2. Sales mode — capability answer, no code",
        "Same brain, different voice. Sales people get a customer-safe yes/no/partial-yes — no jargon, no code blocks. Demonstrate by sending from a sender we've configured as sales, OR by tagging the subject [sales].",
        "Does Hono support custom error handlers and how robust are they?",
        "sales",
        "vp@bigco.com",
    ),
    (
        "marketing-mode",
        "3. Marketing mode — hook + bullets + headline",
        "Marketing voice. Punchy hook, why-it-matters bullets, suggested tweet/headline. Same memory, repurposed.",
        "What's interesting about how Hono runs across edge runtimes that we could write about?",
        "marketing",
        "growth@yourco.com",
    ),
    (
        "support-mode",
        "4. Support mode — BUG / EXPECTED / NEEDS-MORE-INFO triage",
        "Support agent voice. First word is the verdict label — code-grounded triage in seconds.",
        "Customer says middleware after next() is silently swallowing thrown errors.",
        "support",
        "cs@yourco.com",
    ),
    (
        "cross-repo",
        "5. Cross-repo answer (hono + node-server)",
        "Two repos indexed; Nia pulls from whichever is relevant. Sources should include the node-server adapter, not just core.",
        "How does Hono actually run on Node.js — what's the bridge between Hono's web-standards request and Node's req/res?",
        "eng",
        "alice@yourco.com",
    ),
    (
        "cache-hit",
        "6. Memory across senders (cache magic ⚡)",
        "Send the SAME question from two different senders. First fires Nia (~25s). Second returns in <1s with 'Previously answered for [original sender]'. Watch the dashboard register the hit live.",
        "How does Hono handle middleware composition?",
        "eng",
        "newperson@later.com",
    ),
    (
        "unknown-sender",
        "7. Unknown sender → flagged in admin",
        "Email from someone not in the users table gets logged to the admin panel under 'Flagged'. Toggle lockdown in /admin to make unknown senders ignored entirely.",
        "Hi, can you tell me about the codebase?",
        "eng",
        "stranger@unknown-domain.example",
    ),
]


def _section(scenario) -> str:
    sid, title, why, question, mode, sender = scenario
    mailto = _mailto(f"[{mode}] {title.split('. ',1)[-1]}", question)
    dash = _dash_link(question, mode, sender)
    return f"""
    <section class="card">
      <h2>{title}</h2>
      <p class="why">{why}</p>
      <div class="q"><span class="lbl">Question:</span> {question}</div>
      <div class="meta">mode: <strong>{mode}</strong>{' · from: <strong>' + sender + '</strong>' if sender else ''}</div>
      <div class="actions">
        <a class="btn" href="{dash}">▶ Run in dashboard</a>
        <a class="btn outline" href="{mailto}">✉ Send via email</a>
      </div>
    </section>
    """


def render() -> str:
    sections = "\n".join(_section(s) for s in _SCENARIOS)
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
    <div class="sub">Click ▶ to fire a scenario in the dashboard, ✉ to send it as a real email through AgentMail.</div>
    <div class="nav">
      <a href="/">live log →</a>
      <a href="/admin">admin →</a>
    </div>
    {sections}
  </div>
</body>
</html>"""
