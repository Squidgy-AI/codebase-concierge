"""
Codebase Concierge — webhook receiver for AgentMail.
Single-file agent: email in → Nia query → Claude answer → threaded reply.
"""
import os
import httpx
from fastapi import FastAPI, Request, HTTPException
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
NIA_API_KEY = os.environ["NIA_API_KEY"]
AGENTMAIL_API_KEY = os.environ["AGENTMAIL_API_KEY"]
AGENTMAIL_INBOX_ID = os.environ["AGENTMAIL_INBOX_ID"]
# Comma-separated "org/repo" identifiers (Nia uses string names, not UUIDs).
NIA_REPOS = [r.strip() for r in os.environ["NIA_REPOS"].split(",") if r.strip()]
# Optional: comma-separated documentation source display names.
NIA_DATA_SOURCES = [s.strip() for s in os.environ.get("NIA_DATA_SOURCES", "").split(",") if s.strip()]

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
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{AGENTMAIL_BASE}/inboxes/{AGENTMAIL_INBOX_ID}/threads/{thread_id}",
            headers={"Authorization": f"Bearer {AGENTMAIL_API_KEY}"},
        )
        r.raise_for_status()
        return r.json().get("messages", [])


async def reply_to_message(message_id: str, body_html: str, cc: list[str] | None = None) -> None:
    payload = {"html": body_html}
    if cc:
        payload["cc"] = cc
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{AGENTMAIL_BASE}/inboxes/{AGENTMAIL_INBOX_ID}/messages/{message_id}/reply",
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

Output:
- Clean HTML email body. <p>, <code>, <pre>, <ul>, <a>. No <html>/<body>.
- Lead with the answer, not preamble.
- Tone: clear, friendly, terse. Assume a smart non-engineer reader (PM/founder).
- For technical follow-ups in thread, you can be more code-heavy.
- End with a "Sources" section listing the source links provided.
- If the draft is empty or off-topic, say so plainly. Don't invent.
"""


def compose_answer(question: str, nia_response: dict, thread_history: list[dict]) -> str:
    history_text = "\n\n".join(
        f"From: {m.get('from')}\n{m.get('text', '')[:500]}"
        for m in thread_history[-4:]
    )
    sources = nia_response.get("sources", [])
    # Dedupe while preserving order
    seen, unique_sources = set(), []
    for s in sources:
        if s not in seen:
            seen.add(s)
            unique_sources.append(s)
    sources_html = "\n".join(
        f'<li><a href="{github_blob_url(s)}">{s}</a></li>' for s in unique_sources[:8]
    )
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
                f"<question>{question}</question>\n\n"
                "Write the HTML email reply. Embed the sources list at the end."
            ),
        }],
    )
    return msg.content[0].text


# ---------- Webhook ----------

@app.post("/webhook")
async def agentmail_webhook(request: Request):
    payload = await request.json()
    event_type = payload.get("event") or payload.get("type")

    # AgentMail webhook event for inbound mail — verify exact event name in their docs
    if event_type not in ("message.received", "message_received"):
        return {"ok": True, "skipped": event_type}

    msg = payload.get("message") or payload.get("data", {})
    message_id = msg["id"]
    thread_id = msg.get("thread_id")
    sender = msg.get("from", "")
    subject = msg.get("subject", "")
    body = msg.get("text") or msg.get("body", "")

    # Don't reply to our own outbound (defensive — AgentMail may filter already)
    if "@" in sender and AGENTMAIL_INBOX_ID in sender:
        return {"ok": True, "skipped": "self"}

    question = f"{subject}\n\n{body}".strip()

    history = await get_thread_messages(thread_id) if thread_id else []
    nia_context = await nia_query(question)
    answer_html = compose_answer(question, nia_context, history)

    # TODO: differentiator — auto-CC engineer via git blame on cited files
    await reply_to_message(message_id, answer_html, cc=None)
    return {"ok": True, "replied_to": message_id}


@app.get("/healthz")
async def healthz():
    return {"ok": True}
