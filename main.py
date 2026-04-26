"""
Codebase Concierge — channels.

Two channels share the same brain (core.answer_codebase_question):
  - POST /webhook    : AgentMail inbound mail → threaded reply
  - POST /skill/ask  : direct JSON request for OpenClaw / CLI / future channels
"""
import os
import secrets
import time
import urllib.parse
from collections import deque

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from svix.webhooks import Webhook, WebhookVerificationError

load_dotenv()

# core handles its own ANTHROPIC_API_KEY / NIA_* env loading
import cache  # noqa: E402
import core  # noqa: E402
import dashboard  # noqa: E402
import admin  # noqa: E402
import demo  # noqa: E402
import setup_page  # noqa: E402


def _clean_secret(name: str) -> str:
    raw = os.environ[name]
    cleaned = "".join(ch for ch in raw if ch.isprintable() and ord(ch) < 128).strip()
    if cleaned != raw:
        print(f"[startup] {name}: stripped {len(raw) - len(cleaned)} non-ASCII/invisible chars")
    return cleaned


AGENTMAIL_API_KEY = _clean_secret("AGENTMAIL_API_KEY")
AGENTMAIL_INBOX_ID = os.environ["AGENTMAIL_INBOX_ID"]
AUTO_CC_DOMAINS = [d.strip().lower() for d in os.environ.get("AUTO_CC_DOMAINS", "").split(",") if d.strip()]

WEBHOOK_SIGNING_SECRET = os.environ.get("WEBHOOK_SIGNING_SECRET", "").strip()
SKILL_API_KEY = os.environ.get("SKILL_API_KEY", "").strip()
ALLOW_INSECURE_ENDPOINTS = os.environ.get("ALLOW_INSECURE_ENDPOINTS", "").strip() == "1"

AGENTMAIL_BASE = "https://api.agentmail.to/v0"

# Comma-separated list of origins permitted to issue state-changing requests
# (e.g. "https://codebase-concierge.onrender.com,http://127.0.0.1:8765").
# If empty, the middleware falls back to "same Host header" matching, which
# covers the typical single-origin deploy.
ALLOWED_ORIGINS = {
    o.strip().rstrip("/").lower()
    for o in os.environ.get("ALLOWED_ORIGINS", "").split(",")
    if o.strip()
}

app = FastAPI()


# ---------- Security headers ----------
# Per-request CSP nonce: the dashboard/admin pages inject this into their
# inline <script nonce="..."> tags. CSP then allows ONLY scripts carrying the
# matching nonce — no 'unsafe-inline'. An injected XSS payload won't know the
# nonce (regenerated every request) so the browser refuses to execute it.
#
# Inline style="..." attributes still require 'unsafe-inline' for style-src.
# That's a much smaller risk surface — style XSS can exfiltrate data via
# background-image: url() but cannot execute code.
_IS_PROD = os.environ.get("RENDER", "").strip() != "" or os.environ.get("ENV", "").lower() == "production"


def _build_csp(nonce: str) -> str:
    return (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    nonce = secrets.token_urlsafe(16)
    request.state.csp_nonce = nonce
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", _build_csp(nonce))
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    if _IS_PROD:
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
    return response


# ---------- CSRF: same-origin enforcement on unsafe methods ----------
# Browsers send Origin on cross-origin POST/PUT/PATCH/DELETE. Page JS cannot
# forge it. We allowlist /webhook (server-to-server, no cookies, signed) and
# /skill/ask (Bearer token clients have no cookies to ride).
_CSRF_EXEMPT_PATHS = {"/webhook", "/skill/ask"}
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def csrf_origin_guard(request: Request, call_next):
    if request.method in _UNSAFE_METHODS and request.url.path not in _CSRF_EXEMPT_PATHS:
        origin = (request.headers.get("origin") or "").rstrip("/").lower()
        referer = (request.headers.get("referer") or "").rstrip("/").lower()
        host = (request.headers.get("host") or "").lower()
        accepted = set(ALLOWED_ORIGINS)
        if not accepted and host:
            accepted.update({f"http://{host}", f"https://{host}"})

        def _block(reason: str):
            return JSONResponse({"detail": reason}, status_code=403)

        if origin:
            if origin not in accepted:
                return _block("Cross-origin request blocked (Origin)")
        elif referer:
            if not any(referer.startswith(a + "/") or referer == a for a in accepted):
                return _block("Cross-origin request blocked (Referer)")
        # If both Origin and Referer are missing, allow — non-browser clients
        # (curl, the AgentMail webhook, the OpenClaw skill) legitimately omit
        # both. Browsers always send at least Referer on form submits, so this
        # gap doesn't materially weaken the CSRF defense; an attacker page
        # cannot suppress Referer to a sensitive same-origin POST without also
        # suppressing the cookies/credentials the browser would send.
    return await call_next(request)


app.include_router(admin.router)


# ---------- Rate limiting (in-memory token bucket per IP) ----------

_RATE_LIMIT_MAX = 10        # requests
_RATE_LIMIT_WINDOW = 60.0   # seconds
_rate_buckets: dict[str, deque[float]] = {}


def _rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    bucket = _rate_buckets.setdefault(ip, deque())
    while bucket and now - bucket[0] > _RATE_LIMIT_WINDOW:
        bucket.popleft()
    if len(bucket) >= _RATE_LIMIT_MAX:
        raise HTTPException(status_code=429, detail="Too many requests")
    bucket.append(now)


# ---------- Auth dependencies ----------

def _require_skill_auth(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """Accept either Bearer SKILL_API_KEY (programmatic) or admin Basic auth
    (so the dashboard chat works once the page itself is authed).

    Stashes the auth tier on request.state.skill_auth so the handler can
    decide whether the call is allowed to write to the shared cache.
    """
    # Bearer path — external programmatic clients. Read-only.
    if SKILL_API_KEY and authorization and authorization.startswith("Bearer "):
        if secrets.compare_digest(authorization, f"Bearer {SKILL_API_KEY}"):
            request.state.skill_auth = "bearer"
            return
        raise HTTPException(status_code=401, detail="Unauthorized")
    # Admin Basic-auth path — dashboard chat. Trusted; allowed to write cache.
    if authorization and authorization.startswith("Basic ") and admin.ADMIN_PASSWORD:
        import base64
        try:
            decoded = base64.b64decode(authorization.split(" ", 1)[1]).decode("utf-8", "ignore")
            _user, _, pw = decoded.partition(":")
            if secrets.compare_digest(pw, admin.ADMIN_PASSWORD):
                request.state.skill_auth = "admin"
                return
        except Exception:
            pass
        raise HTTPException(status_code=401, detail="Unauthorized")
    # No credentials provided
    if not SKILL_API_KEY and not admin.ADMIN_PASSWORD:
        if ALLOW_INSECURE_ENDPOINTS:
            request.state.skill_auth = "insecure"
            return
        raise HTTPException(
            status_code=503,
            detail="Skill endpoint disabled: set SKILL_API_KEY or ADMIN_PASSWORD.",
        )
    raise HTTPException(
        status_code=401,
        detail="Unauthorized",
        headers={"WWW-Authenticate": 'Basic realm="Concierge Admin"'},
    )


def _verify_webhook(raw: bytes, headers: dict[str, str]) -> None:
    """Svix-format verification (AgentMail webhooks are delivered via Svix).
    Raises HTTPException on failure. Validates signature AND timestamp window."""
    try:
        wh = Webhook(WEBHOOK_SIGNING_SECRET)
        wh.verify(raw, headers)
    except WebhookVerificationError as e:
        raise HTTPException(status_code=401, detail=f"Invalid webhook signature: {e}")


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

_WEBHOOK_MAX_BYTES = 1_500_000  # AgentMail caps payloads at 1 MB; we leave headroom.


@app.post("/webhook")
async def agentmail_webhook(
    request: Request,
    _rl: None = Depends(_rate_limit),
):
    # Reject oversized bodies before reading them into memory. Cheaper than
    # await request.body() on a 1 GB attacker payload.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _WEBHOOK_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")
    # Streaming guard for clients that omit/lie about Content-Length.
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _WEBHOOK_MAX_BYTES:
            raise HTTPException(status_code=413, detail="Payload too large")
        chunks.append(chunk)
    raw = b"".join(chunks)
    if WEBHOOK_SIGNING_SECRET:
        # Svix expects svix-id, svix-timestamp, svix-signature headers.
        _verify_webhook(raw, dict(request.headers))
    elif not ALLOW_INSECURE_ENDPOINTS:
        raise HTTPException(
            status_code=503,
            detail="Webhook disabled: WEBHOOK_SIGNING_SECRET not configured.",
        )
    import json
    payload = json.loads(raw)
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

    # Auto-CC requires (a) the admin toggle is ON and (b) the engineer's email
    # domain is in AUTO_CC_DOMAINS. Default OFF so we never surprise OSS maintainers.
    cc_enabled = cache.get_setting("auto_cc_enabled", "0") == "1"
    cc = (
        [e["email"] for e in result["engineers"]
         if AUTO_CC_DOMAINS and any(e["email"].lower().endswith("@" + d) for d in AUTO_CC_DOMAINS)]
        if cc_enabled else []
    ) or None
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


@app.post("/skill/ask", dependencies=[Depends(_rate_limit)])
async def skill_ask(req: AskRequest, request: Request, _auth: None = Depends(_require_skill_auth)):
    """Channel-agnostic ask endpoint — same brain as the email channel.
    Returns {answer_html, answer_md, sources, engineers, cache_hit, mode, ...}."""
    mode = req.mode or "eng"
    # Cache writes only when the caller is admin-authenticated (dashboard chat).
    # Bearer-token clients are read-only — a leaked SKILL_API_KEY can't poison
    # the cache that webhook replies pull from.
    tier = getattr(request.state, "skill_auth", "bearer")
    return await core.answer_codebase_question(
        req.question, req.thread_history, sender=req.sender, mode=mode,
        cache_writes=(tier in ("admin", "insecure")),
    )


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(admin._require_admin)])
async def index(request: Request):
    return dashboard.render(nonce=request.state.csp_nonce)


@app.get("/api/feed", response_class=HTMLResponse, dependencies=[Depends(admin._require_admin)])
async def feed_fragment():
    """Stats + feed HTML fragment, polled by the dashboard for live updates."""
    return dashboard.render_feed_html()


@app.get("/demo", response_class=HTMLResponse, dependencies=[Depends(admin._require_admin)])
async def demo_page(request: Request):
    return demo.render(nonce=request.state.csp_nonce) if "nonce" in demo.render.__code__.co_varnames else demo.render()


@app.get("/setup", response_class=HTMLResponse)
async def setup_route():
    if setup_page.is_complete():
        return HTMLResponse(
            '<meta http-equiv="refresh" content="0;url=/admin">'
            '<p>Setup already complete. <a href="/admin">go to admin →</a></p>',
            status_code=200,
        )
    return await setup_page.render()


@app.post("/setup/register_webhook")
async def setup_register_webhook():
    try:
        result = await setup_page.register_webhook()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return RedirectResponse("/setup", status_code=303)


@app.post("/setup/complete")
async def setup_finish():
    setup_page.mark_complete()
    return RedirectResponse("/admin", status_code=303)


@app.get("/healthz")
async def healthz():
    return {"ok": True}
