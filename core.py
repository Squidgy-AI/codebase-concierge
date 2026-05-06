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
import bleach
from anthropic import Anthropic

import cache


# ---------- HTML sanitization (defense against prompt-injected XSS) ----------

_ALLOWED_TAGS = [
    "p", "br", "strong", "em", "code", "pre",
    "ul", "ol", "li", "h3", "h4", "a", "blockquote",
]
_ALLOWED_ATTRS = {"a": ["href", "title"]}
_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def sanitize_html(raw: str) -> str:
    """Allowlist-based sanitizer. Strips <script>, on*, style, javascript: URLs.
    Forces target=_blank rel=noopener on links."""
    if not raw:
        return ""
    cleaned = bleach.clean(
        raw,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
        strip_comments=True,
    )
    # Force target=_blank rel=noopener on every <a>. bleach already stripped
    # any pre-existing target/rel since they're not in _ALLOWED_ATTRS.
    return re.sub(
        r'<a\s+([^>]*?)href=',
        r'<a target="_blank" rel="noopener noreferrer" \1href=',
        cleaned,
        flags=re.IGNORECASE,
    )


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

class NiaUpstreamError(Exception):
    """Raised when Nia's API returns a non-2xx or the request fails entirely.
    Caught by route handlers so a transient Nia issue doesn't crash the endpoint
    with a bare 'Internal Server Error' string."""
    def __init__(self, status: int | None, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(f"Nia upstream {status}: {detail}")


def _parse_repo_entry(entry: str) -> tuple[str, str | None]:
    """Split an active-list entry like 'org/repo' or 'org/repo@branch' into
    (repo, branch_or_none). Empty branch and 'main' both normalize to None
    (Nia's default), so the search payload stays in the simpler string form."""
    repo, _, branch = entry.partition("@")
    repo = repo.strip()
    branch = branch.strip()
    if not branch or branch == "main":
        return repo, None
    return repo, branch


def get_active_repos() -> list[str]:
    """Active repos for Nia queries: admin-managed setting overrides env var.
    Entries may be 'org/repo' (default branch) or 'org/repo@branch'."""
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
    parsed = [_parse_repo_entry(e) for e in get_active_repos()]
    # If any active repo pins a non-default branch, send the whole list as
    # objects so Nia doesn't silently fall back to main for those entries.
    # Otherwise keep the simpler string form.
    if any(b for _, b in parsed):
        repos_payload = [
            {"repository": r, "branch": b} if b else {"repository": r, "branch": "main"}
            for r, b in parsed
        ]
    else:
        repos_payload = [r for r, _ in parsed]
    payload = {
        "mode": "query",
        "messages": [{"role": "user", "content": question}],
        "repositories": repos_payload,
    }
    ds = get_active_data_sources()
    if ds:
        payload["data_sources"] = ds

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{NIA_BASE}/v2/search",
                headers={"Authorization": f"Bearer {NIA_API_KEY}"},
                json=payload,
            )
    except httpx.RequestError as e:
        raise NiaUpstreamError(None, f"connection failed: {e}") from e
    if r.status_code >= 400:
        body_preview = (r.text or "")[:300]
        raise NiaUpstreamError(r.status_code, body_preview or "no body")
    return r.json()


async def nia_get_source(source_id: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{NIA_BASE}/v2/sources/{source_id}",
            headers={"Authorization": f"Bearer {NIA_API_KEY}"},
        )
        r.raise_for_status()
        return r.json()


async def nia_get_source_content(source_id: str, path: str | None = None) -> dict:
    """Pull the raw indexed content for a source.
    Documentation sources require ?path=<doc-path>; PDFs require ?page= or ?tree_node_id=."""
    params = {"path": path} if path else None
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{NIA_BASE}/v2/sources/{source_id}/content",
            headers={"Authorization": f"Bearer {NIA_API_KEY}"},
            params=params,
        )
        r.raise_for_status()
        return r.json()


async def nia_get_source_tree(source_id: str) -> dict:
    """Tree of paths/pages for a source. Used to resolve a default `path` for content."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{NIA_BASE}/v2/sources/{source_id}/tree",
            headers={"Authorization": f"Bearer {NIA_API_KEY}"},
        )
        r.raise_for_status()
        return r.json()


def _flatten_tree_paths(tree: dict | None) -> list[str]:
    """Walk Nia's nested tree dict and yield every leaf path string."""
    out: list[str] = []
    def _walk(node):
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)
    _walk((tree or {}).get("tree"))
    return out


async def nia_list_sources() -> list[dict]:
    """List every source we have indexed in Nia (repos + docs)."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{NIA_BASE}/v2/sources",
            headers={"Authorization": f"Bearer {NIA_API_KEY}"},
        )
        r.raise_for_status()
        return (r.json() or {}).get("items", [])


async def nia_index_repo(repo: str, branch: str | None = None) -> dict:
    """Trigger Nia indexing on a public org/repo. Returns the source row.
    If branch is None or 'main', Nia uses the repo's default branch."""
    body = {"type": "repository", "repository": repo.strip()}
    if branch and branch.strip() and branch.strip() != "main":
        body["branch"] = branch.strip()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{NIA_BASE}/v2/sources",
            headers={"Authorization": f"Bearer {NIA_API_KEY}"},
            json=body,
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

# In-process cache: repos we've already attempted to clone (success or fail) so
# we don't retry on every blame call.
_clone_attempts: set[str] = set()


def ensure_repo_cloned(org: str, repo: str, depth: int = 300) -> bool:
    """Best-effort lazy clone of a GitHub repo into REPOS_DIR.
    Returns True if the repo is available locally afterwards. Skips work if the
    clone already exists. Caches failures so we don't hammer GitHub on every miss.
    Honors GITHUB_TOKEN for private repos."""
    repo_dir = os.path.join(REPOS_DIR, repo)
    git_dir = os.path.join(repo_dir, ".git")
    if os.path.isdir(git_dir):
        return True
    key = f"{org}/{repo}"
    if key in _clone_attempts:
        return False
    _clone_attempts.add(key)
    os.makedirs(REPOS_DIR, exist_ok=True)
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        url = f"https://x-access-token:{token}@github.com/{org}/{repo}.git"
    else:
        url = f"https://github.com/{org}/{repo}.git"
    try:
        out = subprocess.run(
            ["git", "clone", f"--depth={depth}", url, repo_dir],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        print(f"[clone] {key} failed: {e}")
        return False
    if out.returncode != 0:
        print(f"[clone] {key} failed: {out.stderr.strip()[:200]}")
        return False
    print(f"[clone] {key} cloned to {repo_dir}")
    return True


def _source_to_local(source: str) -> tuple[str, str] | None:
    """Map 'honojs/hono/src/hono.ts' → (repo_dir, relative_path).
    Lazily clones the repo if we haven't seen it before — slow on first hit,
    fast forever after."""
    parts = source.split("/", 2)
    if len(parts) < 3:
        return None
    org, repo, path = parts[0], parts[1], parts[2]
    repo_dir = os.path.join(REPOS_DIR, repo)
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        if not ensure_repo_cloned(org, repo):
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


_RECENCY_RE = re.compile(r"\b(what'?s\s+new|recent|latest|shipped|changelog|release\s*notes|this\s+(week|sprint|month))\b", re.IGNORECASE)


def needs_recent_commits(question: str, mode: str) -> bool:
    """Marketing always wants commit context; other modes only if the question asks for it."""
    return mode == "marketing" or bool(_RECENCY_RE.search(question or ""))


def recent_commits(days: int = 14, max_per_repo: int = 20) -> str:
    """git log across every local repo clone. Returns a plain-text block; empty if no clones."""
    if not os.path.isdir(REPOS_DIR):
        return ""
    blocks = []
    for entry in sorted(os.listdir(REPOS_DIR)):
        repo_dir = os.path.join(REPOS_DIR, entry)
        if not os.path.isdir(os.path.join(repo_dir, ".git")):
            continue
        try:
            out = subprocess.run(
                ["git", "-C", repo_dir, "log",
                 f"--since={days} days ago",
                 f"--max-count={max_per_repo}",
                 "--no-merges",
                 "--pretty=format:%h %s (%an, %ar)"],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            continue
        if out.returncode == 0 and out.stdout.strip():
            blocks.append(f"## {entry}\n{out.stdout.strip()}")
    return "\n\n".join(blocks)


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
    "security": (
        "You are a security engineer reviewing the code Nia retrieved for risks. "
        "Tone: precise, advisory, no-FUD.\n"
        "- Output a <ul> of findings. Each <li> starts with a severity in <strong>: HIGH / MED / LOW.\n"
        "- Each finding states what (the issue), where (cite a file from the provided sources — never invent paths), and a one-line remediation.\n"
        "- Look for: missing auth/authz, unvalidated input, injection (SQL/cmd/template/XSS), unsafe deserialization, hard-coded secrets, predictable randomness, missing rate limits, insecure defaults, dependency-level red flags Nia surfaced.\n"
        "- If the retrieved code is too narrow to assess (e.g. only test files), say so plainly and suggest a broader question. Do NOT invent issues.\n"
        "- End with a one-line disclaimer: \"Advisory only — not a substitute for a full security review.\"\n\n"
        + _FORMAT_RULES
    ),
}


# Built-in mode display colors (foreground, background). Custom modes can override.
MODE_COLORS = {
    "eng": ("#0a7d3e", "#dcf5e6"),
    "sales": ("#0a3a99", "#e0eafc"),
    "marketing": ("#a8479a", "#fbe6f5"),
    "support": ("#b3530a", "#fcecdc"),
    "security": ("#990a0a", "#fbe0e0"),
}
BUILTIN_MODES = set(SYSTEM_PROMPTS.keys())

_VALID_MODE_ID = re.compile(r"^[a-z][a-z0-9_]{1,30}$")


def all_modes() -> dict[str, dict]:
    """Merge built-ins with admin-defined custom modes. Returns
    {id: {label, color: (fg,bg), prompt, builtin: bool}}."""
    out: dict[str, dict] = {}
    for mid, prompt in SYSTEM_PROMPTS.items():
        out[mid] = {
            "id": mid,
            "label": mid,
            "color": MODE_COLORS.get(mid, MODE_COLORS["eng"]),
            "prompt": prompt,
            "builtin": True,
        }
    for cm in cache.list_custom_modes():
        mid = cm.get("id")
        if not mid or mid in out:
            continue  # custom can't shadow a built-in
        fg = cm.get("color_fg") or "#444"
        bg = cm.get("color_bg") or "#eee"
        out[mid] = {
            "id": mid,
            "label": cm.get("label") or mid,
            "color": (fg, bg),
            "prompt": cm.get("prompt") or "",
            "builtin": False,
        }
    return out


def is_valid_mode_id(mid: str) -> bool:
    return bool(mid and _VALID_MODE_ID.match(mid))


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
    # Iterate every known mode (built-in + custom) — first subject tag wins.
    for mode in all_modes().keys():
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
    """Per-mode system prompt.
    - Custom mode → its inline prompt from custom_modes.
    - Built-in mode → settings override (set in /admin) OR baked-in default.
    Unknown mode → eng default.
    """
    modes = all_modes()
    if mode in modes and not modes[mode]["builtin"]:
        return modes[mode]["prompt"] or SYSTEM_PROMPTS["eng"]
    default = SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPTS["eng"])
    override = cache.get_setting(f"prompt_{mode}", "")
    return override.strip() if override and override.strip() else default


def compose_answer_html(
    question: str,
    nia_response: dict,
    thread_history: list[dict],
    engineers: list[dict] | None = None,
    mode: str = "eng",
    commits_text: str = "",
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
                f"<recent_commits>\n{commits_text}\n</recent_commits>\n\n"
                f"<sources_html>\n<ul>\n{sources_html}\n</ul>\n</sources_html>\n\n"
                f"<engineers_html>\n{engineers_html}\n</engineers_html>\n\n"
                f"<question>{question}</question>\n\n"
                "Write the HTML email reply. If <recent_commits> is non-empty and the "
                "question is about recency / what's new / what shipped, ground your "
                "answer in those commits (translate them into user-visible benefits for "
                "marketing mode; cite by short hash if useful). Embed the sources list "
                "at the end, then if engineers_html is non-empty, append it verbatim."
            ),
        }],
    )
    html = msg.content[0].text
    html = re.sub(r"<p>\s*(&nbsp;|&#160;)?\s*</p>", "", html)
    html = re.sub(r"<br\s*/?>\s*<br\s*/?>", "<br>", html)
    html = re.sub(r">\s+<", "><", html)
    # Hard guarantee: model output is treated as untrusted. Strip <script>,
    # event handlers, javascript: URLs, etc. before this HTML ever reaches
    # the cache, the email reply, or the dashboard.
    return sanitize_html(html.strip())


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
    cache_writes: bool = True,
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
    commits_text = recent_commits() if needs_recent_commits(question, mode) else ""
    answer_html = compose_answer_html(
        question, nia_context, history, engineers, mode=mode, commits_text=commits_text,
    )
    answer_md = nia_context.get("content", "")

    # Only trusted channels (the AgentMail webhook) write to the shared cache.
    # /skill/ask is read-only against the cache so a leaked SKILL_API_KEY can't
    # poison answers served to email senders.
    if cacheable and cache_writes:
        cache.store(cache_key, answer_html, answer_md, sources, engineers, sender)

    return {
        "answer_html": answer_html,
        "answer_md": answer_md,
        "sources": sources,
        "engineers": engineers,
        "cache_hit": False,
        "mode": mode,
    }


# ---------- Prompt generator (admin helper) ----------

_GENERATOR_META = """You design new "modes" for a codebase Q&A agent that runs the same brain (Nia-retrieved code + Claude composition) but switches voice/lens depending on the asker.

Given the user's one-line description, return JSON ONLY (no markdown fence, no preamble) with these exact fields:
{
  "id": "<lowercase slug, 2-31 chars, [a-z0-9_], starts with a letter>",
  "label": "<Title Case display name, 1-3 words>",
  "prompt": "<system prompt body, see structure below>"
}

The "prompt" field must be structured as:
1. One sentence defining the role / lens.
2. A "Tone:" line (e.g. "Tone: precise, customer-empathetic, no jargon.")
3. 3–5 mode-specific output rules as bullet points.
4. End with the literal token ---FORMAT_RULES--- on its own line. The orchestrator will substitute the shared format block.

Constraints on the prompt body:
- No fluff, no marketing language.
- Don't promise to "do your best" or "help the user" — describe what the OUTPUT looks like.
- Under 220 words.

Constraints on id/label:
- id MUST NOT be one of: eng, sales, marketing, support, security (reserved built-ins).
- label is short and human; id is the subject-tag slug (`[id]`).

Return only the JSON object — no other text.
"""


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", (text or "").lower()).strip("_")
    s = re.sub(r"_+", "_", s)
    if not s or not s[0].isalpha():
        s = "mode_" + s
    return s[:31]


def generate_mode_metadata(description: str) -> dict:
    """Use Claude Haiku to draft {id, label, prompt} for a new mode."""
    if not description.strip():
        raise ValueError("description required")
    msg = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=900,
        system=_GENERATOR_META,
        messages=[{"role": "user", "content": description.strip()}],
    )
    raw = (msg.content[0].text or "").strip()
    # Tolerate accidental fences.
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", raw).strip()
    import json as _json
    try:
        data = _json.loads(raw)
    except _json.JSONDecodeError:
        # Last-ditch: take the whole output as the prompt body and synthesize id/label.
        data = {"id": "", "label": "", "prompt": raw}

    label = (data.get("label") or "").strip() or description.strip()[:30]
    candidate_id = (data.get("id") or "").strip().lower()
    if not is_valid_mode_id(candidate_id) or candidate_id in BUILTIN_MODES:
        candidate_id = _slugify(label)
        if candidate_id in BUILTIN_MODES or not is_valid_mode_id(candidate_id):
            candidate_id = _slugify(label) + "_mode"
    body = (data.get("prompt") or "").strip()
    if "---FORMAT_RULES---" in body:
        body = body.replace("---FORMAT_RULES---", _FORMAT_RULES.strip())
    else:
        body = body + "\n\n" + _FORMAT_RULES.strip()
    return {"id": candidate_id, "label": label, "prompt": body}


# Back-compat for any older caller.
def generate_mode_prompt(description: str) -> str:
    return generate_mode_metadata(description)["prompt"]
