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

async def nia_query(question: str) -> dict:
    """Query indexed codebases. Returns {content, sources, follow_up_questions, retrieval_log_id}.

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


# ---------- Claude composition ----------

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


def compose_answer_html(
    question: str,
    nia_response: dict,
    thread_history: list[dict],
    engineers: list[dict] | None = None,
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
    html = re.sub(r"<p>\s*(&nbsp;|&#160;)?\s*</p>", "", html)
    html = re.sub(r"<br\s*/?>\s*<br\s*/?>", "<br>", html)
    html = re.sub(r">\s+<", "><", html)
    return html.strip()


# ---------- Public entry point ----------

async def answer_codebase_question(
    question: str,
    thread_history: list[dict] | None = None,
) -> dict:
    """Channel-agnostic Q&A over indexed codebases.

    Args:
        question: Natural-language question.
        thread_history: Optional list of prior messages
            (each {from_, text|preview}); used as context for follow-ups.

    Returns:
        {
          "answer_html": str,        # email-ready, compact HTML
          "answer_md": str,          # raw markdown answer (Nia's draft, less polish)
          "sources": list[str],      # normalized: GitHub paths or doc URLs
          "engineers": list[dict],   # [{name, email, date, source}], from git blame
        }
    """
    history = thread_history or []
    nia_context = await nia_query(question)
    sources = _normalize_sources(nia_context.get("sources", []))
    engineers = cited_engineers(sources)
    answer_html = compose_answer_html(question, nia_context, history, engineers)
    return {
        "answer_html": answer_html,
        "answer_md": nia_context.get("content", ""),
        "sources": sources,
        "engineers": engineers,
    }
