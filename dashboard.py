"""
Concierge dashboard — read-only HTML view of the SQLite Q&A log.

One-file, zero JS frameworks. Auto-refreshes every 5s via meta tag.
"""
import html
import os
import re
from datetime import datetime, timezone

import cache
import core


_MODE_COLORS = {
    "eng": ("#0a7d3e", "#dcf5e6"),
    "sales": ("#0a3a99", "#e0eafc"),
    "marketing": ("#a8479a", "#fbe6f5"),
    "support": ("#b3530a", "#fcecdc"),
}


def _strip_mode_prefix(question: str) -> tuple[str, str]:
    """Cache stores '[mode] question' — split it back for display."""
    m = re.match(r"^\[([a-z]+)\]\s*(.*)$", question, flags=re.DOTALL)
    if m and m.group(1) in _MODE_COLORS:
        return m.group(1), m.group(2)
    return "eng", question


def _ago(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        # Cache stores ISO timestamps with optional Z; sqlite default is "YYYY-MM-DD HH:MM:SS".
        if "T" in ts:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            t = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    except ValueError:
        return ts
    now = datetime.now(timezone.utc)
    secs = int((now - t).total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _badge(mode: str) -> str:
    fg, bg = _MODE_COLORS.get(mode, _MODE_COLORS["eng"])
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:4px;'
        f'font-size:11px;font-weight:600;letter-spacing:0.5px;'
        f'color:{fg};background:{bg};text-transform:uppercase">{html.escape(mode)}</span>'
    )


def _row(entry: dict) -> str:
    mode, question = _strip_mode_prefix(entry["question"])
    asked_ago = _ago(entry["created_at"])
    sources = entry["sources"][:5]
    engineers = entry["engineers"][:3]

    def _link(s):
        href = s if s.startswith(("http://", "https://")) else core.github_blob_url(s)
        return f'<a href="{html.escape(href)}" target="_blank" rel="noopener">{html.escape(s)}</a>'

    sources_html = " · ".join(_link(s) for s in sources) if sources else '<span style="color:#999">no sources</span>'
    if len(entry["sources"]) > 5:
        sources_html += f' <span style="color:#999">+{len(entry["sources"]) - 5} more</span>'

    engineers_html = ""
    if engineers:
        names = ", ".join(html.escape(e["name"]) for e in engineers)
        engineers_html = f'<div style="color:#666;font-size:12px;margin-top:4px">cc: {names}</div>'

    hits_html = ""
    if entry["hit_count"]:
        hit_who = html.escape(entry["last_hit_sender"] or "another teammate")
        hit_when = _ago(entry["last_hit_at"])
        hits_html = (
            f'<div style="margin-top:6px;padding:6px 10px;background:#fff8d6;'
            f'border-left:3px solid #f5b800;border-radius:3px;font-size:12px">'
            f'⚡ <strong>Cache hit ×{entry["hit_count"]}</strong> — last asked by '
            f'{hit_who} {hit_when}</div>'
        )

    sender = html.escape(entry["original_sender"] or "unknown")
    # Searchable text for the client-side filter (lowercased question + sender + mode)
    search_blob = html.escape(f"{question} {sender} {mode}".lower())
    answer_html = entry.get("answer_html") or ""
    return (
        f'<article class="row" data-search="{search_blob}" data-mode="{mode}">'
        f'<div class="row-head" onclick="toggleRow(this)">'
        f'  <div class="row-meta">'
        f'    {_badge(mode)}'
        f'    <span style="color:#999;font-size:12px">{sender} · {asked_ago}</span>'
        f'    <span class="row-toggle">▾</span>'
        f'  </div>'
        f'  <div class="row-q">{html.escape(question)}</div>'
        f'  <div class="row-sources">{sources_html}</div>'
        f'  {engineers_html}'
        f'  {hits_html}'
        f'</div>'
        f'<div class="row-detail">'
        f'  <div class="row-detail-inner">{answer_html}</div>'
        f'</div>'
        f'</article>'
    )


def _feature_card(icon: str, label: str, value: str, sub: str = "") -> str:
    return (
        f'<div class="feat">'
        f'  <div class="feat-icon">{icon}</div>'
        f'  <div class="feat-body">'
        f'    <div class="feat-label">{html.escape(label)}</div>'
        f'    <div class="feat-value">{value}</div>'
        f'    {f"<div class=\"feat-sub\">{html.escape(sub)}</div>" if sub else ""}'
        f'  </div>'
        f'</div>'
    )


def render_features_html() -> str:
    """Live system capability panel. Pulls real config so it's never stale."""
    inbox = os.environ.get("AGENTMAIL_INBOX_ID", "—")
    repos = [r.strip() for r in os.environ.get("NIA_REPOS", "").split(",") if r.strip()]
    docs = [d.strip() for d in os.environ.get("NIA_DATA_SOURCES", "").split(",") if d.strip()]
    cc_domains = [d.strip() for d in os.environ.get("AUTO_CC_DOMAINS", "").split(",") if d.strip()]
    users = cache.list_users()
    lockdown = cache.get_setting("lockdown", "0") == "1"
    db_path = cache.CACHE_DB_PATH
    db_exists = os.path.exists(db_path)
    db_size = os.path.getsize(db_path) if db_exists else 0

    # Voice / mode list
    modes_html = " ".join(_badge(m) for m in ("eng", "sales", "marketing", "support"))

    # Indexed sources
    src_parts = []
    for r in repos:
        src_parts.append(f'<a href="https://github.com/{html.escape(r)}" target="_blank">{html.escape(r)}</a>')
    for d in docs:
        src_parts.append(f'<span style="color:#a8479a">{html.escape(d)}</span>')
    sources_html = " · ".join(src_parts) or '<span style="color:#999">none configured</span>'

    inbox_html = (
        f'<a href="mailto:{html.escape(inbox)}">{html.escape(inbox)}</a>'
        if "@" in inbox else html.escape(inbox)
    )

    cc_html = (
        f'<span style="color:#0a7d3e">on</span> <span style="color:#888;font-size:11px">'
        f'({", ".join(html.escape(d) for d in cc_domains)})</span>'
        if cc_domains else '<span style="color:#999">off</span>'
    )

    lock_html = (
        '<span style="color:#c33">ON</span> <span style="color:#888;font-size:11px">(unknown senders ignored)</span>'
        if lockdown else
        '<span style="color:#0a7d3e">off</span> <span style="color:#888;font-size:11px">(answers everyone, flags unknown)</span>'
    )

    return (
        '<div class="features">'
        + _feature_card("🧠", "Brain (Nia)", sources_html, f"{len(repos)} repo{'s' if len(repos)!=1 else ''}, {len(docs)} doc source{'s' if len(docs)!=1 else ''} indexed")
        + _feature_card("📨", "Voice (AgentMail)", inbox_html, "threaded replies, /webhook")
        + _feature_card("🔌", "Skill API", '<code>POST /skill/ask</code>', "OpenClaw, Roam, CLI — same brain")
        + _feature_card("🎭", "Modes", modes_html, "subject tag · sender map · default eng")
        + _feature_card("👥", "Known senders", str(len(users)), f'<a href="/admin">manage in /admin</a>')
        + _feature_card("⚡", "Memory cache", f"SQLite · <code>{html.escape(db_path)}</code>", f"{db_size:,} bytes — {'persistent' if db_path.startswith(('/disk', '/var/data')) else '⚠ EPHEMERAL — set CACHE_DB_PATH'}")
        + _feature_card("🔍", "Auto-CC engineer", cc_html, "via git blame on cited code")
        + _feature_card("🛡️", "Lockdown", lock_html, "")
        + '</div>'
    )


def render_feed_html() -> str:
    """Features + stats + feed fragment, polled for live updates without page reload."""
    rows = cache.recent(limit=30)
    s = cache.stats()
    body = "\n".join(_row(r) for r in rows) or (
        '<div style="padding:48px;text-align:center;color:#999">'
        'No questions yet. Send one below or email '
        '<code>codebaseconcierge@agentmail.to</code>.</div>'
    )
    return (
        render_features_html()
        + f'<div class="stats">'
        + f'  <div class="stat"><div class="stat-num">{s["questions"]}</div><div class="stat-label">Questions answered</div></div>'
        + f'  <div class="stat"><div class="stat-num">{s["cache_hits"]}</div><div class="stat-label">Cache hits</div></div>'
        + f'  <div class="stat"><div class="stat-num">{s["flagged_senders"]}</div><div class="stat-label">Flagged senders</div></div>'
        + f'</div>'
        + f'<div class="feed">{body}</div>'
    )


def render() -> str:
    initial_feed = render_feed_html()
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Codebase Concierge — live log</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', sans-serif;
            background: #fafafa; color: #222; margin: 0; padding: 24px; }}
    .wrap {{ max-width: 820px; margin: 0 auto; }}
    h1 {{ font-size: 22px; margin: 0 0 4px 0; }}
    .sub {{ color: #888; font-size: 13px; margin-bottom: 18px; }}
    .stats {{ display: flex; gap: 24px; margin: 0 0 14px 0;
              padding: 14px 18px; background: #fff; border-radius: 8px;
              box-shadow: 0 1px 2px rgba(0,0,0,0.04); }}
    .stat {{ flex: 0 0 auto; }}
    .stat-num {{ font-size: 24px; font-weight: 600; }}
    .stat-label {{ font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; }}
    .feed {{ background: #fff; border-radius: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.04);
             overflow: hidden; }}
    a {{ color: #0a3a99; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    code {{ background: #f0f0f0; padding: 1px 5px; border-radius: 3px; font-size: 12px; }}

    .chat {{ background: #fff; border-radius: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.04);
             padding: 16px 18px; margin-bottom: 18px; }}
    .chat-row {{ display: flex; gap: 8px; margin-bottom: 10px; }}
    .chat input, .chat select, .chat textarea, .chat button {{
      font-family: inherit; font-size: 14px; padding: 8px 10px;
      border: 1px solid #ddd; border-radius: 6px; background: #fff;
    }}
    .chat textarea {{ flex: 1; resize: vertical; min-height: 38px; }}
    .chat select {{ flex: 0 0 110px; }}
    .chat input.sender {{ flex: 0 0 220px; }}
    .chat button {{ background: #0a3a99; color: white; border: none;
                    cursor: pointer; padding: 8px 16px; font-weight: 500; }}
    .chat button:hover {{ background: #0c46b8; }}
    .chat button:disabled {{ background: #aaa; cursor: not-allowed; }}
    .chat-out {{ margin-top: 12px; }}
    .chat-bubble {{ padding: 10px 14px; border-radius: 8px;
                    background: #f4f6fa; border-left: 3px solid #0a3a99;
                    margin-bottom: 8px; font-size: 14px; line-height: 1.45; }}
    .chat-bubble.cache {{ border-left-color: #f5b800; background: #fff8d6; }}
    .chat-meta {{ font-size: 11px; color: #888; margin-top: 6px; }}
    .chat-bubble h3 {{ font-size: 13px; margin: 10px 0 4px; color: #555; }}
    .chat-bubble ul {{ margin: 4px 0 4px 18px; padding: 0; }}
    .chat-bubble pre {{ background: #fff; padding: 8px; border-radius: 4px;
                        border: 1px solid #eee; overflow-x: auto; font-size: 12px; }}
    .chat-bubble code {{ background: #fff; }}

    .features {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
                  gap: 10px; margin: 0 0 16px 0; }}
    .feat {{ display: flex; gap: 10px; background: #fff; padding: 12px 14px;
             border-radius: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }}
    .feat-icon {{ font-size: 20px; line-height: 1; flex: 0 0 auto; }}
    .feat-label {{ font-size: 10px; color: #888; text-transform: uppercase;
                   letter-spacing: 0.5px; margin-bottom: 2px; }}
    .feat-value {{ font-size: 13px; font-weight: 500; line-height: 1.3; word-break: break-word; }}
    .feat-sub {{ font-size: 11px; color: #888; margin-top: 4px; }}
    .feat-value a {{ color: #0a3a99; }}

    .filterbar {{ display: flex; gap: 8px; align-items: center; margin: 0 0 10px 0;
                   padding: 10px 12px; background: #fff; border-radius: 8px;
                   box-shadow: 0 1px 2px rgba(0,0,0,0.04); flex-wrap: wrap; }}
    .filterbar input {{ flex: 1; min-width: 180px; font-family: inherit; font-size: 13px;
                        padding: 6px 10px; border: 1px solid #ddd; border-radius: 6px; }}
    .filterbar .pill {{ padding: 4px 10px; border-radius: 12px; font-size: 11px; cursor: pointer;
                        border: 1px solid #ddd; background: #fff; user-select: none; font-weight: 500; }}
    .filterbar .pill.active {{ background: #0a3a99; color: white; border-color: #0a3a99; }}

    .row {{ border-bottom: 1px solid #eee; }}
    .row.hidden {{ display: none; }}
    .row-head {{ padding: 14px 18px; cursor: pointer; }}
    .row-head:hover {{ background: #fafafa; }}
    .row-meta {{ display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }}
    .row-toggle {{ margin-left: auto; color: #999; font-size: 12px; transition: transform .15s; }}
    .row.open .row-toggle {{ transform: rotate(180deg); }}
    .row-q {{ font-size: 15px; font-weight: 500; margin-bottom: 4px; }}
    .row-sources {{ font-size: 12px; color: #555; }}
    .row-detail {{ display: none; padding: 4px 18px 16px 18px; background: #fafafa; border-top: 1px solid #eee; }}
    .row.open .row-detail {{ display: block; }}
    .row-detail-inner {{ background: #fff; padding: 14px 16px; border-radius: 6px;
                          font-size: 14px; line-height: 1.5; color: #222; }}
    .row-detail-inner h3 {{ font-size: 13px; margin: 12px 0 4px; color: #555; }}
    .row-detail-inner ul {{ margin: 4px 0 4px 18px; padding: 0; }}
    .row-detail-inner pre {{ background: #f4f6fa; padding: 10px; border-radius: 4px;
                              border: 1px solid #eee; overflow-x: auto; font-size: 12px; }}
    .row-detail-inner code {{ background: #f0f0f0; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Codebase Concierge — live log</h1>
    <div class="sub">One brain, two channels. Ask via email <code>codebaseconcierge@agentmail.to</code> or right here. <a href="/demo" style="margin-left:8px">demo</a> · <a href="/admin">admin</a></div>

    <div class="chat">
      <div class="chat-row">
        <select id="mode">
          <option value="eng">eng</option>
          <option value="sales">sales</option>
          <option value="marketing">marketing</option>
          <option value="support">support</option>
        </select>
        <input class="sender" id="sender" type="text" list="users" autocomplete="off" placeholder="from (e.g. you@team.com)">
        <datalist id="users"></datalist>
      </div>
      <div class="chat-row">
        <textarea id="question" rows="1" placeholder="Ask anything about the indexed codebase…"></textarea>
        <button id="send">Send</button>
      </div>
      <div class="chat-out" id="chat-out"></div>
    </div>

    <div class="filterbar">
      <input id="search" type="text" placeholder="Search questions, senders, sources…" autocomplete="off">
      <span class="pill mode-pill active" data-mode="all">all</span>
      <span class="pill mode-pill" data-mode="eng">eng</span>
      <span class="pill mode-pill" data-mode="sales">sales</span>
      <span class="pill mode-pill" data-mode="marketing">marketing</span>
      <span class="pill mode-pill" data-mode="support">support</span>
    </div>

    <div id="feed-mount">{initial_feed}</div>
  </div>

  <script>
    const mount = document.getElementById('feed-mount');
    const out = document.getElementById('chat-out');
    const send = document.getElementById('send');
    const q = document.getElementById('question');
    const m = document.getElementById('mode');
    const s = document.getElementById('sender');

    function toggleRow(head) {{ head.parentElement.classList.toggle('open'); }}

    function applyFilter() {{
      const term = (document.getElementById('search').value || '').trim().toLowerCase();
      const activeMode = document.querySelector('.mode-pill.active')?.dataset.mode || 'all';
      document.querySelectorAll('.row').forEach(r => {{
        const matchesText = !term || (r.dataset.search || '').includes(term);
        const matchesMode = activeMode === 'all' || r.dataset.mode === activeMode;
        r.classList.toggle('hidden', !(matchesText && matchesMode));
      }});
    }}
    document.addEventListener('input', e => {{ if (e.target.id === 'search') applyFilter(); }});
    document.addEventListener('click', e => {{
      if (e.target.classList && e.target.classList.contains('mode-pill')) {{
        document.querySelectorAll('.mode-pill').forEach(p => p.classList.remove('active'));
        e.target.classList.add('active');
        applyFilter();
      }}
    }});

    async function refreshFeed() {{
      try {{
        const r = await fetch('/api/feed');
        if (r.ok) {{
          mount.innerHTML = await r.text();
          applyFilter();
        }}
      }} catch (e) {{ /* swallow */ }}
    }}

    async function ask() {{
      const text = q.value.trim();
      if (!text) return;
      send.disabled = true;
      const t0 = performance.now();
      const echo = document.createElement('div');
      echo.className = 'chat-bubble';
      echo.style.background = '#fff';
      echo.style.borderLeftColor = '#999';
      echo.innerHTML = '<strong>You:</strong> ' + text.replace(/[<>&]/g, c => ({{'<':'&lt;','>':'&gt;','&':'&amp;'}}[c])) +
                       '<div class="chat-meta">mode: ' + m.value + (s.value ? ' · from: ' + s.value : '') + '</div>';
      out.prepend(echo);

      const loading = document.createElement('div');
      loading.className = 'chat-bubble';
      loading.textContent = 'Thinking…';
      out.prepend(loading);

      try {{
        const r = await fetch('/skill/ask', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ question: text, mode: m.value, sender: s.value || null }}),
        }});
        const data = await r.json();
        const dur = ((performance.now() - t0) / 1000).toFixed(1);
        loading.className = 'chat-bubble' + (data.cache_hit ? ' cache' : '');
        loading.innerHTML = data.answer_html +
          '<div class="chat-meta">' +
          (data.cache_hit ? '⚡ cache hit · ' : '') +
          dur + 's · mode: ' + (data.mode || m.value) +
          '</div>';
        q.value = '';
        refreshFeed();
      }} catch (e) {{
        loading.textContent = 'Error: ' + e.message;
      }} finally {{
        send.disabled = false;
      }}
    }}

    send.addEventListener('click', ask);
    q.addEventListener('keydown', e => {{
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) ask();
    }});

    async function loadUsers() {{
      try {{
        const r = await fetch('/api/users');
        if (!r.ok) return;
        const list = await r.json();
        const dl = document.getElementById('users');
        dl.innerHTML = list.map(u => {{
          const label = u.display_name ? `${{u.display_name}} <${{u.email}}>` : u.email;
          return `<option value="${{u.email}}" label="${{label}} · ${{u.default_mode}}">`;
        }}).join('');
      }} catch (e) {{ /* swallow */ }}
    }}

    // When the user picks a known address, snap the mode dropdown to their default.
    s.addEventListener('change', async () => {{
      try {{
        const r = await fetch('/api/users');
        if (!r.ok) return;
        const list = await r.json();
        const hit = list.find(u => u.email.toLowerCase() === s.value.trim().toLowerCase());
        if (hit) m.value = hit.default_mode;
      }} catch (e) {{ /* swallow */ }}
    }});

    // Pre-fill from ?question= / ?mode= / ?sender= so /demo can deep-link into a scenario.
    const params = new URLSearchParams(location.search);
    if (params.get('question')) q.value = params.get('question');
    if (params.get('mode'))     m.value = params.get('mode');
    if (params.get('sender'))   s.value = params.get('sender');

    refreshFeed();
    loadUsers().then(() => {{
      if (params.get('autosend') === '1' && q.value.trim()) ask();
    }});
    setInterval(refreshFeed, 5000);
  </script>
</body>
</html>"""
