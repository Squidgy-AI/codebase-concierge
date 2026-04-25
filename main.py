"""
Codebase Concierge — webhook receiver for AgentMail.
Single-file agent: email in → Nia query → Claude answer → threaded reply.
"""
import os
import re
import subprocess
import urllib.parse
import httpx
from fastapi import FastAPI, Request, HTTPException
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()


def _clean_secret(name: str) -> str:
    """Strip whitespace and zero-width chars often introduced by copy-paste.
    Anthropic/AgentMail API keys must be pure ASCII for HTTP headers."""
    raw = os.environ[name]
    cleaned = "".join(ch for ch in raw if ch.isprintable() and ord(ch) < 128).strip()
    if cleaned != raw:
        print(f"[startup] {name}: stripped {len(raw) - len(cleaned)} non-ASCII/invisible chars")
    return cleaned


ANTHROPIC_API_KEY = _clean_secret("ANTHROPIC_API_KEY")
NIA_API_KEY = _clean_secret("NIA_API_KEY")
AGENTMAIL_API_KEY = _clean_secret("AGENTMAIL_API_KEY")
AGENTMAIL_INBOX_ID = os.environ["AGENTMAIL_INBOX_ID"]
# Comma-separated "org/repo" identifiers (Nia uses string names, not UUIDs).
NIA_REPOS = [r.strip() for r in os.environ["NIA_REPOS"].split(",") if r.strip()]
# Optional: comma-separated documentation source display names.
NIA_DATA_SOURCES = [s.strip() for s in os.environ.get("NIA_DATA_SOURCES", "").split(",") if s.strip()]
# Local clones for git-blame lookups. Layout: REPOS_DIR/<repo-name>/.git (e.g. repos/hono).
REPOS_DIR = os.environ.get("REPOS_DIR", "repos")
# Only actually CC engineers whose email matches one of these domains. Demo-safety:
# without this we'd mail real OSS maintainers. Empty = never CC, body-only attribution.
AUTO_CC_DOMAINS = [d.strip().lower() for d in os.environ.get("AUTO_CC_DOMAINS", "").split(",") if d.strip()]

NIA_BASE = "https://apigcp.trynia.ai"
AGENTMAIL_BASE = "https://api.agentmail.to/v0"

app = FastAPI()
claude = Anthropic(api_key=ANTHROPIC_API_KEY)


# ---------- Nia ----------

async def nia_query(question: str) -> dict:
    """Query indexed codebases. Returns:
      {
        "content": str,            # full markdown answer
        "sources": list[str],      # e.g. ["honojs/hono/src/compose.ts", ...]
        "follow_up_questions": list[str],
        "retrieval_log_id": str,
      }

    NOTE: ~25s latency. Pre-warm before demos. Free plan = 50 queries/month.
    """
    payload = {
        "mode": "query",
        "messages": [{"role": "user", "content": question}],
        "repositories": NIA_REPOS,
    }
    if NIA_DATA_SOURCES:
        payload["data_sources"] = NIA_DATA_SOURCES

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{NIA_BASE}/v2/search",
            headers={"Authorization": f"Bearer {NIA_API_KEY}"},
            json=payload,
        )
        r.raise_for_status()
        return r.json()


def _normalize_source(source) -> str | None:
    """Nia sources come back as either strings ('honojs/hono/src/hono.ts') or dicts
    (when documentation sources are mixed in). Reduce to a single label string."""
    if isinstance(source, str):
        return source.strip() or None
    if isinstance(source, dict):
        # Try the most informative keys first.
        for key in ("path", "file", "source", "identifier", "url", "name", "title"):
            v = source.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _normalize_sources(sources: list) -> list[str]:
    out, seen = [], set()
    for s in sources or []:
        n = _normalize_source(s)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _source_to_local(source: str) -> tuple[str, str] | None:
    """Map 'honojs/hono/src/hono.ts' → (repo_dir, relative_path) if a local clone exists."""
    parts = source.split("/", 2)
    if len(parts) < 3:
        return None
    _org, repo, path = parts[0], parts[1], parts[2]
    repo_dir = os.path.join(REPOS_DIR, repo)
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        return None
    return repo_dir, path


def last_author(source: str) -> dict | None:
    """Return {name, email, date, source} for the last commit touching this file."""
    local = _source_to_local(source)
    if not local:
        return None
    repo_dir, path = local
    try:
        out = subprocess.run(
            ["git", "-C", repo_dir, "log", "-1", "--format=%an|%ae|%ad", "--date=short", "--", path],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    name, email, date = out.stdout.strip().split("|", 2)
    return {"name": name, "email": email, "date": date, "source": source}


def cited_engineers(sources: list[str], max_results: int = 3) -> list[dict]:
    """Dedupe authors across cited files. Returns up to max_results unique engineers."""
    seen, result = set(), []
    for s in sources:
        a = last_author(s)
        if not a or a["email"] in seen:
            continue
        seen.add(a["email"])
        result.append(a)
        if len(result) >= max_results:
            break
    return result


def github_blob_url(source: str, branch: str = "main") -> str:
    """Convert 'honojs/hono/src/compose.ts' → GitHub blob URL.
    Heuristic: first two path segments are org/repo, rest is filepath.
    """
    parts = source.split("/", 2)
    if len(parts) < 3:
        return f"https://github.com/{source}"
    org, repo, path = parts[0], parts[1], parts[2]
    return f"https://github.com/{org}/{repo}/blob/{branch}/{path}"


# ---------- AgentMail ----------

async def get_thread_messages(thread_id: str) -> list[dict]:
    """Fetch prior messages in this thread for context."""
    inbox = urllib.parse.quote(AGENTMAIL_INBOX_ID, safe="")
    tid = urllib.parse.quote(thread_id, safe="")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{AGENTMAIL_BASE}/inboxes/{inbox}/threads/{tid}",
            headers={"Authorization": f"Bearer {AGENTMAIL_API_KEY}"},
        )
        r.raise_for_status()
        return r.json().get("messages", [])


async def reply_to_message(message_id: str, body_html: str, cc: list[str] | None = None) -> None:
    # message_id contains <> and @ — URL-encode.
    inbox = urllib.parse.quote(AGENTMAIL_INBOX_ID, safe="")
    mid = urllib.parse.quote(message_id, safe="")
    payload: dict = {"html": body_html}
    if cc:
        payload["cc"] = cc
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{AGENTMAIL_BASE}/inboxes/{inbox}/messages/{mid}/reply",
            headers={"Authorization": f"Bearer {AGENTMAIL_API_KEY}"},
            json=payload,
        )
        r.raise_for_status()


# ---------- Agent reasoning ----------

SYSTEM_PROMPT = """You are a senior engineer answering codebase questions via email.

Inputs:
- Nia's draft answer (markdown, technically deep)
- Source file paths Nia consulted
- Recent thread history (for follow-up context)

Output rules — STRICT:
- HTML fragment only. Allowed tags: <p>, <code>, <pre>, <ul>, <ol>, <li>, <a>, <strong>, <em>, <h3>. NO <html>, <body>, <br>, <div>, <span>, or inline styles.
- Compact HTML: NO blank lines between tags. NO <br> tags. NO <p>&nbsp;</p>. Email clients add their own paragraph spacing — extra breaks compound into giant gaps.
- Output the HTML on a single line if you can; if not, no more than one newline between block elements.
- Lead with the answer in 1–2 sentences. No preamble like "Great question" or "Here's how it works".
- Tone: clear, friendly, terse. Assume a smart non-engineer reader (PM/founder). For technical follow-ups in-thread, get more code-heavy.
- End with exactly one <h3>Sources</h3> followed by the provided <ul>. Do NOT invent or rephrase sources.
- After Sources, if engineers_html is provided, append it verbatim. Do not modify it.
- If Nia's draft is empty or off-topic, say so plainly in one sentence. Do not invent.
"""


def compose_answer(question: str, nia_response: dict, thread_history: list[dict], engineers: list[dict] | None = None) -> str:
    def _fmt(m: dict) -> str:
        sender = (m.get("from_") or [""])[0] if isinstance(m.get("from_"), list) else m.get("from_", "")
        text = (m.get("text") or m.get("preview") or "")[:500]
        return f"From: {sender}\n{text}"
    history_text = "\n\n".join(_fmt(m) for m in thread_history[-4:])
    unique_sources = _normalize_sources(nia_response.get("sources", []))
    def _link(s: str) -> str:
        # External URLs (docs sources) point straight to themselves; repo paths get a GitHub blob URL.
        href = s if s.startswith(("http://", "https://")) else github_blob_url(s)
        return f'<li><a href="{href}">{s}</a></li>'
    sources_html = "\n".join(_link(s) for s in unique_sources[:8])
    engineers_html = ""
    if engineers:
        items = "".join(
            f'<li>{e["name"]} — last touched <code>{e["source"]}</code> on {e["date"]}</li>'
            for e in engineers
        )
        engineers_html = f"<h3>Last touched by</h3><ul>{items}</ul>"
    nia_draft = nia_response.get("content", "")

    msg = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                f"<thread_history>\n{history_text}\n</thread_history>\n\n"
                f"<nia_draft>\n{nia_draft}\n</nia_draft>\n\n"
                f"<sources_html>\n<ul>\n{sources_html}\n</ul>\n</sources_html>\n\n"
                f"<engineers_html>\n{engineers_html}\n</engineers_html>\n\n"
                f"<question>{question}</question>\n\n"
                "Write the HTML email reply. Embed the sources list at the end, "
                "then if engineers_html is non-empty, append it verbatim after Sources."
            ),
        }],
    )
    html = msg.content[0].text
    # Strip empty paragraphs and collapse runs of whitespace between block tags —
    # email clients render <p>&nbsp;</p> and stacked newlines as huge gaps.
    html = re.sub(r"<p>\s*(&nbsp;|&#160;)?\s*</p>", "", html)
    html = re.sub(r"<br\s*/?>\s*<br\s*/?>", "<br>", html)
    html = re.sub(r">\s+<", "><", html)
    return html.strip()


# ---------- Webhook ----------

@app.post("/webhook")
async def agentmail_webhook(request: Request):
    payload = await request.json()
    event_type = payload.get("event_type")

    if event_type != "message.received":
        return {"ok": True, "skipped": event_type}

    msg = payload.get("message") or {}
    message_id = msg["message_id"]
    thread_id = msg.get("thread_id")
    # `from_` is an array of addresses (trailing underscore is intentional per AgentMail).
    from_list = msg.get("from_") or []
    sender = from_list[0] if from_list else ""
    subject = msg.get("subject", "")
    body = msg.get("text") or msg.get("preview") or ""

    # Don't reply to our own outbound (defensive — AgentMail may filter already).
    if AGENTMAIL_INBOX_ID and AGENTMAIL_INBOX_ID.lower() in sender.lower():
        return {"ok": True, "skipped": "self"}

    question = f"{subject}\n\n{body}".strip()

    history = await get_thread_messages(thread_id) if thread_id else []
    nia_context = await nia_query(question)
    engineers = cited_engineers(_normalize_sources(nia_context.get("sources", [])))
    answer_html = compose_answer(question, nia_context, history, engineers)

    # Demo-safe CC: only loop in engineers whose email matches an allow-listed domain,
    # so we never surprise OSS maintainers with mail from a stranger's hackathon project.
    cc = [e["email"] for e in engineers if AUTO_CC_DOMAINS and any(e["email"].lower().endswith("@" + d) for d in AUTO_CC_DOMAINS)] or None
    await reply_to_message(message_id, answer_html, cc=cc)
    return {"ok": True, "replied_to": message_id, "cc": cc, "engineers": [e["name"] for e in engineers]}


@app.get("/healthz")
async def healthz():
    return {"ok": True}
