"""
Codebase Concierge — channel-agnostic core.

`answer_codebase_question(question, thread_history)` is the single entry point.
Channels (email webhook, /skill/ask, future Slack/CLI) call this and decide
how to render the result.
"""
import os
import re
import subprocess
import httpx
from anthropic import Anthropic

import cache


def _clean_secret(name: str) -> str:
    """Strip whitespace and zero-width chars often introduced by copy-paste.
    Anthropic/Nia API keys must be pure ASCII for HTTP headers."""
    raw = os.environ[name]
    cleaned = "".join(ch for ch in raw if ch.isprintable() and ord(ch) < 128).strip()
    if cleaned != raw:
        print(f"[startup] {name}: stripped {len(raw) - len(cleaned)} non-ASCII/invisible chars")
    return cleaned


ANTHROPIC_API_KEY = _clean_secret("ANTHROPIC_API_KEY")
NIA_API_KEY = _clean_secret("NIA_API_KEY")
NIA_REPOS = [r.strip() for r in os.environ["NIA_REPOS"].split(",") if r.strip()]
NIA_DATA_SOURCES = [s.strip() for s in os.environ.get("NIA_DATA_SOURCES", "").split(",") if s.strip()]
REPOS_DIR = os.environ.get("REPOS_DIR", "repos")

NIA_BASE = "https://apigcp.trynia.ai"

claude = Anthropic(api_key=ANTHROPIC_API_KEY)


# ---------- Nia ----------

def get_active_repos() -> list[str]:
    """Active repos for Nia queries: admin-managed setting overrides env var."""
    override = (cache.get_setting("nia_repos", "") or "").strip()
    if override:
        return [r.strip() for r in override.split(",") if r.strip()]
    return list(NIA_REPOS)


def get_active_data_sources() -> list[str]:
    override = (cache.get_setting("nia_data_sources", "") or "").strip()
    if override:
        return [s.strip() for s in override.split(",") if s.strip()]
    return list(NIA_DATA_SOURCES)


async def nia_query(question: str) -> dict:
    """Query indexed codebases. Returns {content, sources, follow_up_questions, retrieval_log_id}.

    NOTE: ~25s latency. Pre-warm before demos. Free plan = 50 queries/month.
    """
    payload = {
        "mode": "query",
        "messages": [{"role": "user", "content": question}],
        "repositories": get_active_repos(),
    }
    ds = get_active_data_sources()
    if ds:
        payload["data_sources"] = ds

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{NIA_BASE}/v2/search",
            headers={"Authorization": f"Bearer {NIA_API_KEY}"},
            json=payload,
        )
        r.raise_for_status()
        return r.json()


async def nia_get_source(source_id: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{NIA_BASE}/v2/sources/{source_id}",
            headers={"Authorization": f"Bearer {NIA_API_KEY}"},
        )
        r.raise_for_status()
        return r.json()


async def nia_get_source_content(source_id: str) -> dict:
    """Pull the raw indexed content (text body + metadata) for a source."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{NIA_BASE}/v2/sources/{source_id}/content",
            headers={"Authorization": f"Bearer {NIA_API_KEY}"},
        )
        r.raise_for_status()
        return r.json()


async def nia_list_sources() -> list[dict]:
    """List every source we have indexed in Nia (repos + docs)."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{NIA_BASE}/v2/sources",
            headers={"Authorization": f"Bearer {NIA_API_KEY}"},
        )
        r.raise_for_status()
        return (r.json() or {}).get("items", [])


async def nia_index_repo(repo: str) -> dict:
    """Trigger Nia indexing on a public org/repo. Returns the source row."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{NIA_BASE}/v2/sources",
            headers={"Authorization": f"Bearer {NIA_API_KEY}"},
            json={"type": "repository", "repository": repo.strip()},
        )
        r.raise_for_status()
        return r.json()


async def nia_index_doc(url: str, display_name: str | None = None) -> dict:
    """Trigger Nia indexing on a documentation URL. Returns the source row.
    If display_name is provided, set it via the PATCH endpoint right after."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{NIA_BASE}/v2/sources",
            headers={"Authorization": f"Bearer {NIA_API_KEY}"},
            json={"type": "documentation", "url": url.strip()},
        )
        r.raise_for_status()
        src = r.json()
        if display_name:
            try:
                await client.patch(
                    f"{NIA_BASE}/v2/sources/{src['id']}",
                    headers={"Authorization": f"Bearer {NIA_API_KEY}"},
                    json={"display_name": display_name.strip()},
                )
                src["display_name"] = display_name.strip()
            except httpx.HTTPError:
                pass
        return src


# ---------- Source normalization ----------

def _normalize_source(source) -> str | None:
    """Nia sources come back as either strings ('honojs/hono/src/hono.ts') or dicts
    (when documentation sources are mixed in). Reduce to a single label string."""
    if isinstance(source, str):
        return source.strip() or None
    if isinstance(source, dict):
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


# ---------- Git blame (auto-CC differentiator) ----------

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


# ---------- Citation rendering ----------

def github_blob_url(source: str, branch: str = "main") -> str:
    """'honojs/hono/src/compose.ts' → GitHub blob URL."""
    parts = source.split("/", 2)
    if len(parts) < 3:
        return f"https://github.com/{source}"
    org, repo, path = parts[0], parts[1], parts[2]
    return f"https://github.com/{org}/{repo}/blob/{branch}/{path}"


def _source_link(s: str) -> str:
    return s if s.startswith(("http://", "https://")) else github_blob_url(s)


# ---------- Mode routing ----------

# Shared formatting rules across all modes — keep email clients sane.
_FORMAT_RULES = """Output rules — STRICT:
- HTML fragment only. Allowed tags: <p>, <code>, <pre>, <ul>, <ol>, <li>, <a>, <strong>, <em>, <h3>. NO <html>, <body>, <br>, <div>, <span>, or inline styles.
- Compact HTML: NO blank lines between tags. NO <br> tags. NO <p>&nbsp;</p>. Email clients add their own paragraph spacing — extra breaks compound into giant gaps.
- Lead with the answer in 1–2 sentences. No preamble.
- End with exactly one <h3>Sources</h3> followed by the provided <ul>. Do NOT invent or rephrase sources.
- After Sources, if engineers_html is provided, append it verbatim.
- If Nia's draft is empty or off-topic, say so plainly. Do not invent."""

SYSTEM_PROMPTS = {
    "eng": (
        "You are a senior engineer answering codebase questions via email. "
        "Tone: clear, friendly, terse. Assume a smart non-engineer reader (PM/founder); "
        "use code blocks when they clarify, but explain what they show. "
        "For technical follow-ups in-thread, you can be more code-heavy.\n\n" + _FORMAT_RULES
    ),
    "sales": (
        "You are a sales engineer answering capability questions for a prospect. "
        "Tone: confident, plain-language, no jargon. Customer-safe phrasing.\n"
        "- Lead with a direct yes/no/partial-yes answer.\n"
        "- NO code blocks. NO file paths in prose. The Sources section still cites real files for the rep's reference, but the prose stays code-free.\n"
        "- If the codebase shows partial support, say exactly which parts work and which don't — never overstate.\n"
        "- If the answer is no, suggest the closest available capability.\n\n"
        + _FORMAT_RULES
    ),
    "marketing": (
        "You are writing a content brief from real codebase activity. "
        "Tone: punchy, plain-language, angle-driven.\n"
        "- Lead with a 1-line hook a non-technical reader could repost.\n"
        "- Then a short bullet list of why-it-matters points.\n"
        "- NO code blocks. Translate technical wins into user-visible benefits.\n"
        "- End with a suggested headline or tweet.\n\n"
        + _FORMAT_RULES
    ),
    "support": (
        "You are a support engineer triaging a customer report against the actual code. "
        "Tone: precise, customer-empathetic, no-blame.\n"
        "- First sentence: BUG, EXPECTED, or NEEDS-MORE-INFO — one of those three labels in <strong>.\n"
        "- Then the evidence from the code in 1–3 short bullets.\n"
        "- If BUG: suggest a workaround. If EXPECTED: explain the design intent in plain language.\n\n"
        + _FORMAT_RULES
    ),
}


# SENDER_MODES env var: comma-separated "email_or_@domain:mode" pairs.
# Example: "sw@seth.co.uk:sales,@theai.team:eng,@4142.ltd:marketing"
# Exact email beats domain. Subject tag still overrides everything.
def _parse_sender_modes(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        key, mode = entry.rsplit(":", 1)
        key, mode = key.strip().lower(), mode.strip().lower()
        if mode in SYSTEM_PROMPTS:
            out[key] = mode
    return out


SENDER_MODES = _parse_sender_modes(os.environ.get("SENDER_MODES", ""))


def _seed_users_from_env() -> None:
    """Bootstrap the users table from SENDER_MODES so admin/autocomplete sees them.
    Domain entries (@example.com) are skipped — only exact emails become user rows.
    Existing rows are not touched."""
    for key, mode in SENDER_MODES.items():
        if "@" not in key or key.startswith("@"):
            continue
        if cache.lookup_user(key) is None:
            try:
                cache.upsert_user(key, None, mode)
            except ValueError:
                pass


_seed_users_from_env()


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+")


def _extract_email(sender: str | None) -> str | None:
    if not sender:
        return None
    m = _EMAIL_RE.search(sender)
    return m.group(0).lower() if m else None


def detect_mode(sender: str | None, subject: str | None) -> str:
    """Pick a mode for this message.

    Precedence:
      1. Explicit subject tag: [sales] / [marketing] / [support] / [eng]
      2. Users table (admin-managed) — exact email match
      3. Sender map (env: SENDER_MODES) — exact email beats domain
      4. Default: eng
    """
    s = (subject or "").lower()
    for mode in ("sales", "marketing", "support", "eng"):
        if f"[{mode}]" in s:
            return mode

    email = _extract_email(sender)
    if email:
        u = cache.lookup_user(email)
        if u:
            return u["default_mode"]
        if email in SENDER_MODES:
            return SENDER_MODES[email]
        domain = email.split("@", 1)[1] if "@" in email else None
        if domain and f"@{domain}" in SENDER_MODES:
            return SENDER_MODES[f"@{domain}"]

    return "eng"


# ---------- Claude composition ----------


def get_prompt(mode: str) -> str:
    """Per-mode system prompt. Editable in /admin via the settings table —
    falls back to the baked-in default."""
    default = SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPTS["eng"])
    override = cache.get_setting(f"prompt_{mode}", "")
    return override.strip() if override and override.strip() else default


def compose_answer_html(
    question: str,
    nia_response: dict,
    thread_history: list[dict],
    engineers: list[dict] | None = None,
    mode: str = "eng",
) -> str:
    def _fmt(m: dict) -> str:
        sender = (m.get("from_") or [""])[0] if isinstance(m.get("from_"), list) else m.get("from_", "")
        text = (m.get("text") or m.get("preview") or "")[:500]
        return f"From: {sender}\n{text}"

    history_text = "\n\n".join(_fmt(m) for m in thread_history[-4:])
    unique_sources = _normalize_sources(nia_response.get("sources", []))
    sources_html = "\n".join(
        f'<li><a href="{_source_link(s)}">{s}</a></li>' for s in unique_sources[:8]
    )
    engineers_html = ""
    if engineers:
        items = "".join(
            f'<li>{e["name"]} — last touched <code>{e["source"]}</code> on {e["date"]}</li>'
            for e in engineers
        )
        engineers_html = f"<h3>Last touched by</h3><ul>{items}</ul>"
    nia_draft = nia_response.get("content", "")

    system_prompt = get_prompt(mode)
    msg = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=system_prompt,
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
    html = re.sub(r"<p>\s*(&nbsp;|&#160;)?\s*</p>", "", html)
    html = re.sub(r"<br\s*/?>\s*<br\s*/?>", "<br>", html)
    html = re.sub(r">\s+<", "><", html)
    return html.strip()


# ---------- Public entry point ----------

def _prepend_cache_note(html: str, original_sender: str | None, original_date: str) -> str:
    """Prepend the demo's "previously answered" line to a cached HTML answer."""
    who = original_sender or "another teammate"
    note = f'<p><em>Previously answered for {who} on {original_date}.</em></p>'
    return note + html


async def answer_codebase_question(
    question: str,
    thread_history: list[dict] | None = None,
    sender: str | None = None,
    mode: str = "eng",
) -> dict:
    """Channel-agnostic Q&A over indexed codebases.

    Args:
        question: Natural-language question.
        thread_history: Optional list of prior messages
            (each {from_, text|preview}); used as context for follow-ups.
        sender: Optional asker identifier (email/handle). Cached on first
            answer; surfaced in the "previously answered for X" note on
            subsequent hits.

    Returns:
        {
          "answer_html": str,        # email-ready, compact HTML
          "answer_md": str,          # raw markdown answer (Nia's draft, less polish)
          "sources": list[str],      # normalized: GitHub paths or doc URLs
          "engineers": list[dict],   # [{name, email, date, source}], from git blame
          "cache_hit": bool,
          "original_sender": str | None,  # only present on cache_hit
          "original_date":   str,         # only present on cache_hit
        }
    """
    # Don't cache follow-ups — thread context shifts the answer.
    # Cache by (question, mode) — same question in eng vs sales mode is a different answer.
    cacheable = not (thread_history and len(thread_history) > 0)
    cache_key = f"[{mode}] {question}"
    if cacheable:
        hit = cache.lookup(cache_key, hit_sender=sender)
        if hit:
            hit["answer_html"] = _prepend_cache_note(
                hit["answer_html"], hit["original_sender"], hit["original_date"]
            )
            hit["mode"] = mode
            return hit

    history = thread_history or []
    nia_context = await nia_query(question)
    sources = _normalize_sources(nia_context.get("sources", []))
    engineers = cited_engineers(sources)
    answer_html = compose_answer_html(question, nia_context, history, engineers, mode=mode)
    answer_md = nia_context.get("content", "")

    if cacheable:
        cache.store(cache_key, answer_html, answer_md, sources, engineers, sender)

    return {
        "answer_html": answer_html,
        "answer_md": answer_md,
        "sources": sources,
        "engineers": engineers,
        "cache_hit": False,
        "mode": mode,
    }
