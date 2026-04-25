"""
Codebase Concierge — channels.

Two channels share the same brain (core.answer_codebase_question):
  - POST /webhook    : AgentMail inbound mail → threaded reply
  - POST /skill/ask  : direct JSON request for OpenClaw / CLI / future channels
"""
import os
import urllib.parse
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# core handles its own ANTHROPIC_API_KEY / NIA_* env loading
import cache  # noqa: E402
import core  # noqa: E402
import dashboard  # noqa: E402
import admin  # noqa: E402
import demo  # noqa: E402


def _clean_secret(name: str) -> str:
    raw = os.environ[name]
    cleaned = "".join(ch for ch in raw if ch.isprintable() and ord(ch) < 128).strip()
    if cleaned != raw:
        print(f"[startup] {name}: stripped {len(raw) - len(cleaned)} non-ASCII/invisible chars")
    return cleaned


AGENTMAIL_API_KEY = _clean_secret("AGENTMAIL_API_KEY")
AGENTMAIL_INBOX_ID = os.environ["AGENTMAIL_INBOX_ID"]
AUTO_CC_DOMAINS = [d.strip().lower() for d in os.environ.get("AUTO_CC_DOMAINS", "").split(",") if d.strip()]

AGENTMAIL_BASE = "https://api.agentmail.to/v0"

app = FastAPI()
app.include_router(admin.router)


# ---------- AgentMail ----------

async def get_thread_messages(thread_id: str) -> list[dict]:
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


# ---------- Channel: AgentMail webhook ----------

@app.post("/webhook")
async def agentmail_webhook(request: Request):
    payload = await request.json()
    event_type = payload.get("event_type")
    if event_type != "message.received":
        return {"ok": True, "skipped": event_type}

    msg = payload.get("message") or {}
    message_id = msg["message_id"]
    thread_id = msg.get("thread_id")
    from_list = msg.get("from_") or []
    sender = from_list[0] if from_list else ""
    subject = msg.get("subject", "")
    body = msg.get("text") or msg.get("preview") or ""

    if AGENTMAIL_INBOX_ID and AGENTMAIL_INBOX_ID.lower() in sender.lower():
        return {"ok": True, "skipped": "self"}

    # Unknown-sender handling: always log; optionally ignore based on lockdown setting.
    sender_email = core._extract_email(sender)
    known = bool(sender_email and cache.lookup_user(sender_email))
    if not known:
        cache.flag_sender(sender, subject, body)
        if cache.get_setting("lockdown", "0") == "1":
            return {"ok": True, "skipped": "unknown_sender_lockdown"}

    mode = core.detect_mode(sender, subject)
    question = f"{subject}\n\n{body}".strip()
    history = await get_thread_messages(thread_id) if thread_id else []

    result = await core.answer_codebase_question(question, history, sender=sender, mode=mode)

    cc = [
        e["email"] for e in result["engineers"]
        if AUTO_CC_DOMAINS and any(e["email"].lower().endswith("@" + d) for d in AUTO_CC_DOMAINS)
    ] or None
    await reply_to_message(message_id, result["answer_html"], cc=cc)
    return {
        "ok": True,
        "replied_to": message_id,
        "mode": result.get("mode"),
        "cache_hit": result.get("cache_hit"),
        "cc": cc,
        "engineers": [e["name"] for e in result["engineers"]],
    }


# ---------- Channel: programmatic / OpenClaw skill ----------

class AskRequest(BaseModel):
    question: str
    thread_history: list[dict] | None = None
    sender: str | None = None
    mode: str | None = None  # "eng" | "sales" | "marketing" | "support"; default eng


@app.post("/skill/ask")
async def skill_ask(req: AskRequest):
    """Channel-agnostic ask endpoint — same brain as the email channel.
    Returns {answer_html, answer_md, sources, engineers, cache_hit, mode, ...}."""
    mode = req.mode or "eng"
    return await core.answer_codebase_question(
        req.question, req.thread_history, sender=req.sender, mode=mode,
    )


@app.get("/", response_class=HTMLResponse)
async def index():
    return dashboard.render()


@app.get("/api/feed", response_class=HTMLResponse)
async def feed_fragment():
    """Stats + feed HTML fragment, polled by the dashboard for live updates."""
    return dashboard.render_feed_html()


@app.get("/demo", response_class=HTMLResponse)
async def demo_page():
    return demo.render()


@app.get("/healthz")
async def healthz():
    return {"ok": True}
